"""Model-facing production capabilities and executable submission boundaries."""

import hashlib
import json
import os
import threading
from pathlib import Path

from pydantic import ValidationError

from .backend import Backend
from .models import Grant, Scene, UnfoldError
from .store import Store, digest, uid, write_all
from .timing import FPS, encoded_frames


class Production:
    def __init__(self, request):
        self.request = request
        self.grant = Grant.model_validate(request["grant"])
        self.store = Store(request["library"])
        self.directory = self.store.workspace(request["operation_id"])
        self.backend = Backend(request["backend"])
        self.scene = None
        self.rendered = None
        self.observations = {}
        self.reference_evidence = None
        self.delivered = set()
        self.calls = self.model_calls = self.renders = self.frames = 0
        self.provider_attempts = 0
        self.rejections = 0
        self.text_bytes = self.image_bytes = 0
        self.result = self.fatal = None
        self.lock = threading.RLock()
        reference = request.get("reference")
        if reference:
            from .backend import run
            from .store import uid

            if digest(Path(reference["path"])) != reference["sha256"]:
                raise UnfoldError("MATERIAL_CHANGED", "Reference changed before inspection.")
            last = encoded_frames(request["brief"]["duration"]) - 1
            times = list(dict.fromkeys([0, (last // 2) / FPS, last / FPS]))[
                : max(0, self.grant.max_frames - 1)
            ]
            frames = []
            for index, time in enumerate(times):
                path = self.directory / f"reference-{index}.jpg"
                run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        str(reference["start"] + time),
                        "-i",
                        reference["path"],
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=1280:720:force_original_aspect_ratio=decrease",
                        "-y",
                        str(path),
                    ],
                    timeout=30,
                )
                frames.append({"path": str(path), "time": time, "sha256": digest(path)})
            if frames:
                key = uid()
                self.observations[key] = {
                    "id": key,
                    "frames": frames,
                    "method": "reference footage samples; not generated animation",
                    "reference_id": reference["id"],
                    "video_sha256": reference["sha256"],
                    "source_sha256": reference["sha256"],
                }
                self.reference_evidence = {
                    "reference_id": reference["id"],
                    "sha256": reference["sha256"],
                    "reference_start": reference["start"],
                    "composition_times": times,
                    "method": "sampled reference frames disclosed to the selected model; gaps remain unobserved",
                }
                self.event("reference_sampled", self.reference_evidence)
                self.frames = len(frames)

    def remaining(self):
        return {
            "model_calls": self.grant.max_model_calls - self.model_calls,
            "tool_calls": self.grant.max_tool_calls - self.calls,
            "renders": self.grant.max_renders - self.renders,
            "frames": self.grant.max_frames - self.frames,
        }

    def event(self, kind, data):
        self.store.event(kind, self.request["operation_id"], data)

    def rejected_authoring(self, action, payload, error):
        """Retain at most three private 64-KiB records, never raw input in events."""
        if self.rejections >= 3:
            return
        self.rejections += 1
        raw = payload.encode("utf-8")
        record = {"action": action, "call": self.calls, "error": error,
                  "payload_sha256": hashlib.sha256(raw).hexdigest(),
                  "payload_bytes": len(raw), "payload": raw[:32768].decode("utf-8", "ignore")}
        while True:
            record["truncated"] = len(record["payload"].encode("utf-8")) != len(raw)
            encoded = json.dumps(record, ensure_ascii=False).encode("utf-8")
            if len(encoded) <= 65536:
                break
            record["payload"] = record["payload"][:len(record["payload"]) // 2]
        relative = Path("operations") / self.request["operation_id"] / f"rejected-{self.calls}.json"
        with self.store.open_relative(relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL) as fd:
            write_all(fd, encoded)
            os.fsync(fd)
        self.event("authoring_rejected", {
            "call": self.calls, "diagnostic": str(relative),
            "payload_sha256": record["payload_sha256"], "truncated": record["truncated"],
        })

    def call(self, action, payload):
        with self.lock:
            if self.result is not None:
                raise UnfoldError(
                    "ALREADY_SUBMITTED", "This operation has already submitted a result."
                )
            if self.fatal:
                raise self.fatal
            if self.store.get(self.request["operation_id"], "operation")["status"] != "running":
                raise UnfoldError("CANCELLED", "Operation is no longer active.")
            self.calls += 1
            if self.calls > self.grant.max_tool_calls:
                self.fatal = UnfoldError("RESOURCE_LIMIT", "Tool allowance exhausted.")
                raise self.fatal
            self.event("production", {"action": action, "call": self.calls})
            data = json.loads(payload)
            if action == "inspect":
                return {
                    "scene": self.scene.model_dump() if self.scene else None,
                    "base_scene": self.request.get("base_scene"),
                    "remaining": self.remaining(),
                }
            if action in ("author", "patch"):
                if action == "patch":
                    import copy

                    scene_data = copy.deepcopy(
                        self.scene.model_dump() if self.scene else self.request.get("base_scene")
                    )
                    if not scene_data:
                        raise ValueError("Patch requires an authored or base composition.")
                    if set(data) - {"elements", "title", "background"}:
                        raise ValueError("Patch supports element properties, title and background.")
                    for identity, changes in data.get("elements", {}).items():
                        element = next(
                            (e for e in scene_data["elements"] if e["id"] == identity), None
                        )
                        if element is None or "id" in changes:
                            raise ValueError(
                                "Patch must identify an existing element and preserve its ID."
                            )
                        element.update(changes)
                    for key in ("title", "background"):
                        if key in data:
                            scene_data[key] = data[key]
                    data = scene_data
                scene = Scene.model_validate(data)
                if scene.duration != self.request["brief"]["duration"]:
                    raise ValueError("Preserve the requested duration exactly.")
                resources = self.request.get("resources", {})
                for asset in resources.values():
                    if digest(Path(asset["path"])) != asset["sha256"]:
                        raise UnfoldError("MATERIAL_CHANGED", "Selected identity asset changed.")
                self.backend.author(
                    scene, self.directory / "source", {i: a if a.get("role") == "font" else a["path"] for i, a in resources.items()}
                )
                self.scene, self.rendered = scene, None
                self.observations.clear()
                self.delivered.clear()
                return {
                    "authored": True,
                    "source_sha256": self.backend.source_hash(self.directory / "source"),
                }
            if action == "render":
                if not self.scene:
                    raise ValueError("Author a scene first.")
                if self.renders >= self.grant.max_renders:
                    raise ValueError(
                        "Render allowance exhausted; submit existing valid work or a limitation."
                    )
                self.renders += 1
                self.rendered = None
                self.observations.clear()
                self.delivered.clear()
                self.rendered = self.backend.render(
                    self.directory / "source", self.directory / "video.mp4"
                )
                self.event("rendered_preview", self.rendered)
                return self.rendered
            if action == "sample":
                if not self.rendered:
                    raise ValueError("Render the current scene first.")
                times = data["times"]
                if len(times) + self.frames > self.grant.max_frames:
                    raise ValueError("Frame allowance exhausted.")
                self.frames += len(times)
                evidence_id = uid()
                frames = self.backend.frames(
                    self.directory / "video.mp4", times, self.directory / ("samples-" + evidence_id)
                )
                evidence = {
                    "id": evidence_id,
                    "video_sha256": self.rendered["sha256"],
                    "source_sha256": self.rendered["source_sha256"],
                    "frames": frames,
                    "method": "decoded JPEG samples from encoded MP4",
                }
                self.observations[evidence_id] = evidence
                return {
                    "evidence_id": evidence_id,
                    "times": times,
                    "next": "Images will be delivered with the next model call; inspect before submitting.",
                }
            if action == "submit":
                if not self.rendered or not self.delivered:
                    raise ValueError(
                        "Render and sample current source, then inspect delivered images first."
                    )
                if (
                    self.backend.source_hash(self.directory / "source")
                    != self.rendered["source_sha256"]
                ):
                    raise ValueError("Source changed since rendering.")
                if digest(self.directory / "video.mp4") != self.rendered["sha256"]:
                    raise ValueError("Rendered bytes changed since inspection.")
                review = data.get("review", "")
                limitations = data.get("limitations", [])
                if not isinstance(review, str) or not 1 <= len(review) <= 5000:
                    raise ValueError("Provide a bounded review of the actual sampled images.")
                if (
                    not isinstance(limitations, list)
                    or len(limitations) > 20
                    or any(not isinstance(x, str) or len(x) > 1000 for x in limitations)
                ):
                    raise ValueError("Provide at most 20 short limitations.")
                evidence = [self.observations[i] for i in self.delivered]
                for observation in evidence:
                    for frame in observation["frames"]:
                        if digest(frame["path"]) != frame["sha256"]:
                            raise ValueError("Observation bytes changed.")
                        frame["path"] = str(Path(frame["path"]).relative_to(self.store.root))
                self.result = {
                    "source_sha256": self.rendered["source_sha256"],
                    "render": self.rendered,
                    "evidence": evidence,
                    "model_review": review,
                    "reference_evidence": self.reference_evidence,
                    "limitations": limitations
                    + [
                        "Sampled frames do not verify every intervening frame or motion smoothness.",
                        "Silent illustrative animation; not a captured execution or timing measurement.",
                        "Human review pending.",
                    ],
                    "backend": self.backend.doctor()["versions"],
                    "usage": {
                        "model_calls": self.model_calls,
                        "provider_attempts": (self.provider_attempts
                                              if self.grant.provider == "openai" else None),
                        "tool_calls": self.calls,
                        "renders": self.renders,
                        "frames": self.frames,
                        "text_bytes": self.text_bytes,
                        "image_bytes": self.image_bytes,
                    },
                }
                return {"submitted": True}
            if action == "limitation":
                self.fatal = UnfoldError(
                    "CREATIVE_LIMITATION", str(data.get("reason", "Unsupported request"))[:2000]
                )
                raise self.fatal
            raise ValueError("Unknown production action.")

    async def execute_tool(self, action, payload):
        import asyncio

        return await asyncio.to_thread(self._execute_tool, action, payload)

    def _execute_tool(self, action, payload):
        from amplifier_core import ToolResult

        with self.lock:
            try:
                value = self.call(action, payload)
                return ToolResult(success=True, output=value)
            except Exception as exc:
                if isinstance(exc, ValidationError):
                    errors = exc.errors(include_input=False, include_url=False, include_context=False)
                    error = {"code": "INVALID_SCENE", "message": "Correct the scene validation errors.",
                             "violations": [{"loc": [str(part)[:80] for part in e["loc"][:8]], "type": e["type"],
                                             "message": e["msg"][:500]} for e in errors[:20]]}
                elif isinstance(exc, UnfoldError):
                    error = exc.as_dict()
                else:
                    error = {"code": "INVALID_INPUT", "message": str(exc)[:3000]}
                if action in {"author", "patch"} and isinstance(exc, ValueError):
                    # Serialize diagnostics with other tool actions; invalid work must
                    # never replace a valid source or reset its review evidence.
                    self.rejected_authoring(action, payload, error)
                self.event("production_error", {"action": action, "error": error})
                return ToolResult(success=False, error=error)

    def author_tool(self):
        owner = self

        class Author:
            name = "author_scene"
            description = "Author a complete scene. Constraints are validated before persistence."
            input_schema = Scene.model_json_schema()

            async def execute(self, input):
                return await owner.execute_tool("author", json.dumps(input))

        return Author()

    def tool(self):
        owner = self

        class Tool:
            name = "production"
            description = (
                "Patch, render, inspect and submit a composition. Use author_scene for authoring."
            )
            input_schema = {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "inspect",
                            "patch",
                            "render",
                            "sample",
                            "submit",
                            "limitation",
                        ],
                    },
                    "payload": {
                        "type": "string",
                        "description": "JSON object for the chosen action.",
                    },
                },
                "required": ["action", "payload"],
                "additionalProperties": False,
            }

            async def execute(self, input):
                return await owner.execute_tool(input.get("action"), input.get("payload"))

        return Tool()
