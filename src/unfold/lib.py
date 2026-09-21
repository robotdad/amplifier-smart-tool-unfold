"""Public domain operations. Adapters do not implement creative-library behavior."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .assets import Assets
from .backend import Backend
from .delivery import Delivery
from .models import Brief, Grant, UnfoldError
from .processes import stop_worker
from .review import Review
from .store import Store, digest, uid, write_json


class Unfold(Review, Assets, Delivery):
    def __init__(self, library=None, backend=None):
        self.store = Store(library or Path.home() / ".local/share/unfold")
        self.backend = Backend(backend or Path.home() / ".local/share/unfold-backend")

    def _mutation(self, operation, request_id, payload, effect):
        """Persist exact non-model mutations before dispatching their first effect.

        A completed receipt makes a lost acknowledgement safe to retry.  If a process
        dies after an effect but before the receipt can be completed, the public
        pending record deliberately refuses to guess success or repeat the effect.
        """
        if request_id is None:
            return effect()
        self.store.workspace(request_id)
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                receipt = self.store.get(request_id, db=db)
            except UnfoldError:
                receipt = None
            if receipt:
                if (
                    receipt.get("kind") != "mutation_receipt"
                    or receipt.get("operation") != operation
                    or receipt.get("fingerprint") != fingerprint
                ):
                    raise UnfoldError(
                        "REQUEST_CONFLICT",
                        "Retry identity is already bound to different mutation input.",
                    )
                if receipt["status"] == "completed":
                    return receipt["result"]
                if receipt["status"] == "rejected":
                    error = receipt["error"]
                    raise UnfoldError(error["code"], error["message"], error.get("remedy"))
                raise UnfoldError(
                    "MUTATION_INCOMPLETE",
                    "A prior mutation outcome is uncertain. Inspect its retained receipt before a new request.",
                )
            receipt = {
                "id": request_id,
                "kind": "mutation_receipt",
                "operation": operation,
                "payload": payload,
                "fingerprint": fingerprint,
                "status": "pending",
                "accepted_at": time.time(),
            }
            self.store.put("mutation_receipt", receipt, db)
            self.store.event("mutation_accepted", request_id, {"operation": operation}, db)
        try:
            result = effect()
        except UnfoldError as error:
            # A domain error can occur after an effect. Retain an explicitly
            # incomplete receipt rather than erasing its admission and risking a
            # duplicate retry. A corrected deliberate action must use a new ID.
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    current = self.store.get(request_id, "mutation_receipt", db)
                except UnfoldError:
                    current = None
                if current and current["status"] == "pending":
                    current.update(
                        status="incomplete",
                        error=error.as_dict(),
                        incomplete_at=time.time(),
                    )
                    self.store.put("mutation_receipt", current, db)
                    self.store.event(
                        "mutation_incomplete",
                        request_id,
                        {"operation": operation, "code": error.code},
                        db,
                    )
            raise
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = self.store.get(request_id, "mutation_receipt", db)
            if receipt["status"] != "pending":
                raise UnfoldError(
                    "MUTATION_INCOMPLETE",
                    "Mutation receipt changed while its effect completed; inspect retained state.",
                )
            receipt.update(status="completed", result=result, completed_at=time.time())
            self.store.put("mutation_receipt", receipt, db)
            self.store.event("mutation_completed", request_id, {"operation": operation}, db)
        return result

    def mutation_status(self, request_id):
        """Read one durable non-model mutation receipt or incomplete pending intent."""
        return self.store.get(request_id, "mutation_receipt")

    def retain_mutation_intent(self, operation, request_id, payload):
        """Durably bind one UI mutation before its first transport dispatch."""
        if not isinstance(operation, str) or not operation:
            raise UnfoldError("INVALID_INPUT", "Mutation operation is required.")
        if not isinstance(payload, dict) or "request_id" in payload:
            raise UnfoldError(
                "INVALID_INPUT", "Mutation intent needs an exact object payload without request_id."
            )
        self.store.workspace(request_id)
        intent_id = "mutation-intent-" + request_id
        fingerprint = hashlib.sha256(
            json.dumps({"operation": operation, "payload": payload}, sort_keys=True).encode()
        ).hexdigest()
        intent = {
            "id": intent_id,
            "kind": "mutation_intent",
            "request_id": request_id,
            "operation": operation,
            "payload": payload,
            "fingerprint": fingerprint,
            "status": "pending",
            "created_at": time.time(),
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.store.get(intent_id, "mutation_intent", db)
            except UnfoldError:
                existing = None
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise UnfoldError(
                        "REQUEST_CONFLICT",
                        "Retry identity is already bound to different mutation input.",
                    )
                return existing
            self.store.put("mutation_intent", intent, db)
            self.store.event("mutation_intent_retained", request_id, {"operation": operation}, db)
        return intent

    def acknowledge_mutation_intent(self, request_id):
        """Mark a completed generic mutation receipt delivered to its presenter."""
        intent_id = "mutation-intent-" + request_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            intent = self.store.get(intent_id, "mutation_intent", db)
            receipt = self.store.get(request_id, "mutation_receipt", db)
            if receipt["status"] != "completed":
                raise UnfoldError(
                    "MUTATION_INCOMPLETE", "Mutation outcome is not complete enough to acknowledge."
                )
            intent.update(status="acknowledged", acknowledged_at=time.time())
            self.store.put("mutation_intent", intent, db)
            receipt["acknowledged_at"] = intent["acknowledged_at"]
            self.store.put("mutation_receipt", receipt, db)
            return intent

    def _read_media(self, artifact_id, offset=None):
        """Hash and capture from one scoped descriptor, never a reopened path."""
        import stat

        artifact = self.store.get(artifact_id, "artifact")
        relative = Path(artifact["relative_path"])
        if relative.is_absolute() or any(part in ("..", ".") for part in relative.parts):
            raise UnfoldError("MATERIAL_CHANGED", "Retained media is outside its library.")
        mime = {
            "mp4": "video/mp4",
            "webm": "video/webm",
            "mov": "video/quicktime",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
        }.get(artifact.get("format", "mp4"))
        if mime is None:
            raise UnfoldError(
                "UNSUPPORTED", "This artifact format is not supported for media transfer."
            )
        try:
            with (
                self.store.open_relative(
                    relative, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                ) as fd,
                os.fdopen(fd, "rb", closefd=False) as stream,
            ):
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise UnfoldError("MATERIAL_CHANGED", "Retained media must be a regular file.")
                if offset is not None and (
                    type(offset) is not int or not 0 <= offset <= before.st_size
                ):
                    raise UnfoldError(
                        "INVALID_INPUT", "Offset must be within the retained artifact."
                    )
                checksum, captured, position = hashlib.sha256(), bytearray(), 0
                while block := stream.read(1024 * 1024):
                    checksum.update(block)
                    if offset is not None:
                        start, end = (
                            max(offset - position, 0),
                            min(offset + 196608 - position, len(block)),
                        )
                        if start < end:
                            captured.extend(block[start:end])
                    position += len(block)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) or checksum.hexdigest() != artifact["sha256"]:
                    raise UnfoldError(
                        "MATERIAL_CHANGED", "Retained media changed; no bytes were transferred."
                    )
            info = {
                "artifact_id": artifact_id,
                "revision_id": artifact["revision_id"],
                "mime_type": mime,
                "size": before.st_size,
                "sha256": artifact["sha256"],
                "name": re.sub(r"[^\w .-]", "_", artifact["name"]).strip(". ")
                + "."
                + artifact.get("format", "mp4"),
                "chunk_bytes": 196608,
            }
            return info, bytes(captured)
        except OSError as error:
            raise UnfoldError(
                "MATERIAL_CHANGED", "Retained media is missing, changed, or uses a symlink."
            ) from error

    def media_info(self, artifact_id):
        """Describe intact retained media without granting filesystem access."""
        return self._read_media(artifact_id)[0]

    def read_artifact_chunk(self, artifact_id, offset=0):
        """Read at most 192 KiB from one verified media snapshot; no path/URL inputs."""
        import base64

        info, data = self._read_media(artifact_id, offset)
        return {
            **info,
            "offset": offset,
            "next_offset": offset + len(data),
            "eof": offset + len(data) >= info["size"],
            "data": base64.b64encode(data).decode("ascii"),
        }

    def sample_output(self, artifact_id, times):
        import math
        from fractions import Fraction

        from .backend import run
        from .delivery import media_info
        from .timing import sample_frame

        artifact = self.artifact(artifact_id)
        if artifact["integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "Output is changed or missing.")
        info = media_info(artifact["path"])
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        fps = float(Fraction(video["avg_frame_rate"]))
        count = int(video["nb_frames"])
        duration = count / fps
        if (
            not times
            or len(times) > 12
            or any(not math.isfinite(t) or not 0 <= t < duration for t in times)
        ):
            raise UnfoldError("INVALID_INPUT", "Choose 1–12 finite times within the output.")
        directory = self.store.workspace(uid())
        frames = []
        for i, t in enumerate(times):
            frame = min(sample_frame(t, fps), count - 1)
            path = directory / f"frame-{i}.png"
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    artifact["path"],
                    "-vf",
                    f"select=eq(n\\,{frame})",
                    "-frames:v",
                    "1",
                    str(path),
                ],
                timeout=30,
            )
            frames.append({"path": str(path), "time": frame / fps,
                           "requested_time": t, "sha256": digest(path)})
        result = {
            "artifact_id": artifact_id,
            "sha256": artifact["sha256"],
            "frames": frames,
            "method": "decoded PNG samples; sampling gaps and audio not inspected",
        }
        self.store.event("output_sampled", artifact_id, result)
        return result

    def doctor(self):
        return {
            "library": str(self.store.root),
            "backend": self.backend.doctor(),
            "providers": {
                name: bool(os.getenv(env))
                for name, env in (
                    ("gemini", "GEMINI_API_KEY"),
                    ("openai", "OPENAI_API_KEY"),
                    ("anthropic", "ANTHROPIC_API_KEY"),
                )
            },
        }

    def projects(self):
        return self.store.list("project")

    def inspect(self, identity):
        record = self.store.get(identity)
        if record.get("kind") == "artifact":
            return self.artifact(identity)
        if "artifacts" in record:
            record["resolved_artifacts"] = [self.artifact(i) for i in record["artifacts"]]
            directory = self.store.root / record["source"]
            try:
                intact = self.backend.source_hash(directory) == record["source_sha256"]
            except UnfoldError as exc:
                intact = False
                record["source_error"] = exc.as_dict()
            except (OSError, ValueError):
                intact = False
            record["source_integrity"] = "intact" if intact else "changed_or_missing"
            record["source_path"] = str(directory)
        return record

    def artifact(self, identity):
        record = self.store.get(identity, "artifact")
        path = self.store.root / record["relative_path"]
        record["path"] = str(path)
        record["integrity"] = (
            "intact"
            if path.is_file() and digest(path) == record["sha256"]
            else "changed_or_missing"
        )
        record["download_name"] = (
            re.sub(r"[^\w .-]", "_", record["name"]).strip(". ") + "." + record.get("format", "mp4")
        )
        return record

    def observe(self, after=0):
        return self.store.events(after)

    def rename(self, identity, name, request_id=None):
        if request_id is not None:
            return self._mutation(
                "rename",
                request_id,
                {"identity": identity, "name": name},
                lambda: self.rename(identity, name),
            )
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise UnfoldError("INVALID_INPUT", "Use a name of 1–120 characters.")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.store.get(identity, db=db)
            if record.get("kind") not in {"project", "artifact", "asset", "pack"}:
                raise UnfoldError("UNSUPPORTED", "Rename projects, packs, assets and outputs.")
            record["name"] = name.strip()
            self.store.put(record["kind"], record, db)
            self.store.event("renamed", identity, {"name": name.strip()}, db)
        return record

    def export(self, artifact_id, directory, request_id=None):
        if request_id is not None:
            return self._mutation(
                "export",
                request_id,
                {"artifact_id": artifact_id, "directory": str(directory)},
                lambda: self.export(artifact_id, directory),
            )
        artifact = self.artifact(artifact_id)
        revision = self.inspect(artifact["revision_id"])
        if artifact["integrity"] != "intact" or revision["source_integrity"] != "intact":
            raise UnfoldError(
                "MATERIAL_CHANGED", "Retained source or output changed; export was not performed."
            )
        directory = Path(directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / artifact["download_name"]
        try:
            with path.open("xb") as dest, Path(artifact["path"]).open("rb") as source:
                shutil.copyfileobj(source, dest)
        except FileExistsError:
            raise UnfoldError(
                "OUTPUT_EXISTS",
                "Export would overwrite an existing file.",
                "Choose a different directory or rename the output.",
            ) from None
        result = {
            "artifact_id": artifact_id,
            "path": str(path),
            "sha256": digest(path),
            "ownership": "caller-owned copy",
            "source_preserved": True,
        }
        self.store.event("exported", artifact_id, result)
        return result

    def feedback(self, revision_id, text, request_id=None):
        """Record one exact revision-bound note; exact retries return its receipt."""
        revision = self.store.get(revision_id, "revision")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 5000:
            raise UnfoldError("INVALID_INPUT", "Feedback must have 1–5000 characters.")
        payload = {"revision_id": revision_id, "text": text.strip()}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        intent_id = "feedback-intent-" + request_id if request_id is not None else None
        if request_id is not None:
            self.store.workspace(request_id)
        note = {
            "id": uid(),
            "kind": "feedback",
            "revision_id": revision_id,
            "project_id": revision["project_id"],
            "text": text.strip(),
            "status": "pending",
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                try:
                    receipt = self.store.get(request_id, "feedback_receipt", db)
                except UnfoldError:
                    receipt = None
                if receipt:
                    if receipt["fingerprint"] != fingerprint:
                        raise UnfoldError(
                            "REQUEST_CONFLICT", "Retry identity has different comment input."
                        )
                    return self.store.get(receipt["feedback_id"], "feedback", db)
                try:
                    intent = self.store.get(intent_id, "feedback_intent", db)
                except UnfoldError:
                    intent = None
                if intent and intent["fingerprint"] != fingerprint:
                    raise UnfoldError(
                        "REQUEST_CONFLICT", "Retry identity has different comment input."
                    )
                if intent is None:
                    intent = {
                        "id": intent_id,
                        "kind": "feedback_intent",
                        "request_id": request_id,
                        "fingerprint": fingerprint,
                        "payload": payload,
                        "status": "pending",
                        "created": time.time(),
                    }
                    self.store.put("feedback_intent", intent, db)
            self.store.put("feedback", note, db)
            self.store.event("feedback_submitted", revision_id, note, db)
            if request_id is not None:
                self.store.put(
                    "feedback_receipt",
                    {
                        "id": request_id,
                        "kind": "feedback_receipt",
                        "fingerprint": fingerprint,
                        "feedback_id": note["id"],
                        "revision_id": revision_id,
                    },
                    db,
                )
                intent.update(status="completed", feedback_id=note["id"])
                self.store.put("feedback_intent", intent, db)
        return note

    def retain_feedback_intent(self, revision_id, text, request_id):
        """Durably bind one visible comment command before a presenter awaits transport."""
        self.store.workspace(request_id)
        self.store.get(revision_id, "revision")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 5000:
            raise UnfoldError("INVALID_INPUT", "Feedback must have 1–5000 characters.")
        payload = {"revision_id": revision_id, "text": text.strip()}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        intent_id = "feedback-intent-" + request_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = self.store.get(intent_id, db=db)
            except UnfoldError:
                current = None
            if current:
                if (
                    current.get("kind") != "feedback_intent"
                    or current.get("fingerprint") != fingerprint
                ):
                    raise UnfoldError(
                        "REQUEST_CONFLICT", "Retry identity is already bound to different feedback."
                    )
                return current
            intent = {
                "id": intent_id,
                "kind": "feedback_intent",
                "request_id": request_id,
                "fingerprint": fingerprint,
                "payload": payload,
                "status": "pending",
                "created": time.time(),
            }
            self.store.put("feedback_intent", intent, db)
            self.store.event(
                "feedback_intent_retained", request_id, {"revision_id": revision_id}, db
            )
            return intent

    def acknowledge_feedback_intent(self, request_id):
        """Record delivery of a feedback receipt without changing the retained note."""
        intent_id = "feedback-intent-" + request_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            intent = self.store.get(intent_id, "feedback_intent", db)
            if intent["status"] != "completed":
                raise UnfoldError("MUTATION_INCOMPLETE", "Feedback is not yet retained.")
            intent["acknowledged_at"] = time.time()
            self.store.put("feedback_intent", intent, db)
            return intent

    def cancel(self, operation_id, request_id=None):
        if request_id is not None:
            return self._mutation(
                "cancel",
                request_id,
                {"operation_id": operation_id},
                lambda: self.cancel(operation_id),
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            operation = self.store.get(operation_id, "operation", db)
            if operation["status"] == "running":
                operation["status"] = "cancelling"
                self.store.put("operation", operation, db)
                self.store.event("cancellation_requested", operation_id, {}, db)
        return operation

    def address_feedback(self, feedback_id, revision_id):
        """Link submitted feedback to a revision that actually applied that exact request."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            note = self.store.get(feedback_id, "feedback", db)
            revision = self.store.get(revision_id, "revision", db)
            if (
                revision["base_revision"] != note["revision_id"]
                or revision["feedback"] != note["text"]
            ):
                raise UnfoldError(
                    "FEEDBACK_MISMATCH", "Revision does not carry this feedback and base."
                )
            if note.get("result_revision") == revision_id:
                return note
            if note["status"] != "pending":
                raise UnfoldError("FEEDBACK_CONFLICT", "Feedback already has a different result.")
            note.update(status="addressed", result_revision=revision_id)
            self.store.put("feedback", note, db)
            self.store.event("feedback_addressed", feedback_id, {"revision_id": revision_id}, db)
        return note

    def create(self, brief: Brief, grant: Grant, *, request_id=None):
        return self._produce(brief, grant, request_id=request_id)

    def revise(
        self,
        revision_id,
        feedback,
        grant: Grant,
        *,
        request_id=None,
        target=None,
        identity_version=None,
    ):
        revision = self.inspect(revision_id)
        if revision.get("source_error", {}).get("code") == "MISSING_FONT":
            raise UnfoldError(**revision["source_error"])
        if revision.get("kind") != "revision" or revision["source_integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "A valid retained base revision is required.")
        if not isinstance(feedback, str) or not 1 <= len(feedback) <= 5000:
            raise UnfoldError("INVALID_INPUT", "Use feedback of 1–5000 characters.")
        return self._produce(
            Brief.model_validate(revision["brief"]).model_copy(
                update={"identity_version": identity_version}
            )
            if identity_version
            else Brief.model_validate(revision["brief"]),
            grant,
            base=revision,
            feedback=feedback,
            target=target,
            request_id=request_id,
        )

    def adopt_identity(self, revision_id, version_id, grant, request_id=None):
        revision = self.inspect(revision_id)
        if revision["source_integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "Valid retained source is required.")
        brief = Brief.model_validate(revision["brief"]).model_copy(
            update={"identity_version": version_id}
        )
        return self._produce(
            brief,
            grant,
            base=revision,
            feedback="Adopt the selected identity version; preserve the explanation, timing and unrelated choices.",
            request_id=request_id,
        )

    def render(self, revision_id, request_id=None):
        if request_id is not None:
            return self._mutation(
                "render",
                request_id,
                {"revision_id": revision_id},
                lambda: self.render(revision_id),
            )
        """Re-render a committed composition without initializing intelligence."""
        revision = self.inspect(revision_id)
        if revision.get("source_error", {}).get("code") == "MISSING_FONT":
            raise UnfoldError(**revision["source_error"])
        if revision.get("kind") != "revision" or revision["source_integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "A valid retained source is required.")
        operation_id = uid()
        directory = self.store.workspace(operation_id)
        # Never rewrite committed source when preparing renderer input.
        shutil.copytree(revision["source_path"], directory / "source")
        metadata = self.backend.render(directory / "source", directory / "video.mp4")
        artifact = self._artifact(
            revision_id,
            directory / "video.mp4",
            metadata,
            self.store.get(revision["project_id"], "project")["name"],
        )
        with self.store.connect() as db:
            self.store.put("artifact", artifact, db)
            self.store.event("rendered", revision_id, {"artifact_id": artifact["id"]}, db)
        return self.artifact(artifact["id"])

    def _artifact(self, revision_id, path, metadata, name):
        return {
            "id": uid(),
            "kind": "artifact",
            "revision_id": revision_id,
            "name": name,
            "relative_path": str(path.relative_to(self.store.root)),
            **metadata,
            "ownership": "Unfold-managed",
            "format": "mp4",
            "time_basis": "composition seconds",
        }

    def _produce(self, brief, grant, base=None, feedback="", request_id=None, target=None):
        brief = Brief.model_validate(brief)
        grant = Grant.model_validate(grant)
        if brief.identity_version:
            identity = self.store.get(brief.identity_version, "pack_version")
            if identity["prerequisites"]:
                raise UnfoldError(
                    "MISSING_DEPENDENCY",
                    "Resolve identity prerequisites by saving a new version before use.",
                )
            for asset_id, expected in identity["asset_hashes"].items():
                asset = self.asset(asset_id)
                if asset["integrity"] != "intact" or asset["sha256"] != expected:
                    raise UnfoldError("MATERIAL_CHANGED", "Identity assets changed or missing.")
            brief = brief.model_copy(update={"identity": json.dumps(identity["guidance"])})
        if not (grant.allow_context and grant.allow_frames and grant.vision):
            raise UnfoldError(
                "DISCLOSURE_REQUIRED",
                "This creative path requires context and sampled-frame disclosure to a vision model.",
                "Supply an explicit Grant permitting context and frames, with vision=True.",
            )
        self.backend.require()
        operation_id = request_id or uid()
        directory = self.store.workspace(operation_id)
        payload = {
            "brief": brief.model_dump(),
            "grant": grant.model_dump(),
            "base": base["id"] if base else None,
            "feedback": feedback,
            "feedback_target": target,
        }
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = self.store.get(operation_id, "operation", db)
            except UnfoldError as exc:
                if exc.code != "NOT_FOUND":
                    raise
            else:
                if previous["request_sha256"] != request_hash:
                    raise UnfoldError(
                        "REQUEST_CONFLICT", "Request identity was already used for different input."
                    )
                return (
                    previous  # Includes uncertain/running/failed states; never silently re-spend.
                )
            jobs = db.execute("SELECT data FROM records WHERE kind='review_job'").fetchall()
            if any(
                j.get("operation_id") == operation_id and j["status"] in ("cancelled", "cancelling")
                for j in map(lambda row: json.loads(row[0]), jobs)
            ):
                raise UnfoldError("CANCELLED", "Refinement was cancelled before execution.")
            if base:
                project = self.store.get(base["project_id"], "project", db)
                if project["current_revision"] != base["id"]:
                    raise UnfoldError(
                        "STALE_BASE",
                        "Feedback targets an earlier revision.",
                        "Use the current revision explicitly.",
                    )
            else:
                project = {
                    "id": uid(),
                    "kind": "project",
                    "name": brief.title,
                    "current_revision": None,
                    "revisions": [],
                }
                self.store.put("project", project, db)
            operation = {
                "id": operation_id,
                "kind": "operation",
                "project_id": project["id"],
                "status": "running",
                "request_sha256": request_hash,
                **payload,
            }
            self.store.put("operation", operation, db)
            self.store.event("started", operation_id, {"project_id": project["id"]}, db)
        process = None
        try:
            resources = {}
            if brief.identity_version:
                version = self.store.get(brief.identity_version, "pack_version")
                for asset_id in version["assets"]:
                    asset = self.asset(asset_id)
                    if asset["role"] in {"image", "font"}:
                        resources[asset_id] = {
                            "path": asset["path"],
                            "sha256": asset["sha256"],
                            "name": asset["name"],
                            "role": asset["role"],
                            "rights": asset["rights"],
                            "attribution": asset["attribution"],
                        }
                        if asset["role"] == "font":
                            from .fonts import inspect_font

                            resources[asset_id]["font"] = inspect_font(asset["path"])
            # Expose retained faces when continuing a scene without a selected pack.
            # Explicit identity adoption uses the new pack's selection.
            if base and base.get("identity_version") == brief.identity_version:
                for i, resource in self.backend.retained_resources(base["source_path"]).items():
                    if isinstance(resource, dict) and i not in resources:
                        resources[i] = resource
            payload["resources"] = resources
            if brief.reference_id:
                reference = self.asset(brief.reference_id)
                from .delivery import media_info

                info = media_info(reference["path"])
                if (
                    reference["role"] != "video"
                    or reference["integrity"] != "intact"
                    or info["duration"] < brief.reference_start + brief.duration
                ):
                    raise UnfoldError(
                        "INVALID_REFERENCE",
                        "Choose intact footage covering the requested composition.",
                    )
                payload["reference"] = {
                    "id": reference["id"],
                    "path": reference["path"],
                    "sha256": reference["sha256"],
                    "start": brief.reference_start,
                }

            if base:
                payload["base_scene"] = json.loads(
                    (Path(base["source_path"]) / "scene.json").read_text()
                )
            if target:
                payload["feedback"] += "\nTarget in composition seconds: " + json.dumps(target)
            write_json(
                directory / "request.json",
                {
                    **payload,
                    "library": str(self.store.root),
                    "backend": str(self.backend.root),
                    "operation_id": operation_id,
                },
            )
            with (directory / "worker.log").open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "unfold.worker", str(directory / "request.json")],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if key
                        in {
                            "PATH",
                            "HOME",
                            "USER",
                            "TMPDIR",
                            "SYSTEMROOT",
                            "VIRTUAL_ENV",
                            "PYTHONPATH",
                            {
                                "gemini": "GEMINI_API_KEY",
                                "openai": "OPENAI_API_KEY",
                                "anthropic": "ANTHROPIC_API_KEY",
                            }[grant.provider],
                        }
                    },
                )
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    running = self.store.get(operation_id, "operation", db)
                    running["worker_pid"] = process.pid
                    self.store.put("operation", running, db)
                deadline = time.monotonic() + grant.max_seconds
                while process.poll() is None:
                    if self.store.get(operation_id, "operation")["status"] == "cancelling":
                        raise UnfoldError(
                            "CANCELLED",
                            "Operation was cancelled; prior revisions remain available.",
                        )
                    if time.monotonic() > deadline:
                        raise UnfoldError(
                            "RESOURCE_LIMIT", "Operation wall-clock allowance exhausted."
                        )
                    time.sleep(0.1)
            result_path = directory / "result.json"
            if not result_path.exists():
                raise UnfoldError(
                    "WORKER_FAILED",
                    "The isolated Amplifier worker stopped without a result.",
                    "Inspect the local worker log and prerequisites; retry requires a new request identity.",
                )
            result = json.loads(result_path.read_text())
            if "error" in result:
                raise UnfoldError(**result["error"])
            source = directory / "source"
            video = directory / "video.mp4"
            if (
                self.backend.source_hash(source) != result["source_sha256"]
                or digest(video) != result["render"]["sha256"]
            ):
                raise UnfoldError("STALE_RESULT", "Source or video changed after agent inspection.")
            self.backend.probe(video)
            revision_id = uid()
            artifact = self._artifact(revision_id, video, result["render"], project["name"])
            revision = {
                "id": revision_id,
                "kind": "revision",
                "project_id": project["id"],
                "base_revision": base["id"] if base else None,
                "identity_version": brief.identity_version,
                "brief": brief.model_dump(),
                "feedback": feedback,
                "feedback_target": target,
                "source": str(source.relative_to(self.store.root)),
                "artifacts": [artifact["id"]],
                "operation_id": operation_id,
                **result,
            }
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                operation = self.store.get(operation_id, "operation", db)
                current = self.store.get(project["id"], "project", db)
                if operation["status"] != "running":
                    raise UnfoldError("CANCELLED", "Late result cannot commit after cancellation.")
                if current["current_revision"] != project["current_revision"]:
                    raise UnfoldError(
                        "STALE_BASE",
                        "Another operation committed first; result retained as uncommitted work.",
                    )
                current["current_revision"] = revision_id
                current["revisions"].append(revision_id)
                operation.update(status="completed", revision_id=revision_id)
                for kind, record in (
                    ("revision", revision),
                    ("artifact", artifact),
                    ("project", current),
                    ("operation", operation),
                ):
                    self.store.put(kind, record, db)
                self.store.event("completed", operation_id, {"revision_id": revision_id}, db)
            return operation
        except (Exception, KeyboardInterrupt) as exc:
            self._stop_worker(process)
            error = (
                exc
                if isinstance(exc, UnfoldError)
                else UnfoldError(
                    "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "EXECUTION_FAILED",
                    "Work stopped without a committed result.",
                )
            )
            operation = self.store.get(operation_id, "operation")
            operation.update(
                status="cancelled" if error.code == "CANCELLED" else "failed", error=error.as_dict()
            )
            with self.store.connect() as db:
                self.store.put("operation", operation, db)
                self.store.event(operation["status"], operation_id, error.as_dict(), db)
            return operation
        finally:
            self._stop_worker(process)

    _stop_worker = staticmethod(stop_worker)

    def dashboard(self, port=0):
        from .dashboard import Dashboard

        return Dashboard(self, port)
