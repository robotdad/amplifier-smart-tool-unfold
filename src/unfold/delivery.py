"""Deterministic media preparation, true-alpha rendering, timing and delivery."""

import json
import math
from fractions import Fraction
from pathlib import Path

from .backend import run
from .models import Scene, UnfoldError
from .store import digest, portable_archive, uid
from .timing import FPS, encoded_frames


def media_info(path):
    data = json.loads(
        run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    )
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "streams": [
            {
                k: s[k]
                for k in (
                    "codec_type",
                    "codec_name",
                    "width",
                    "height",
                    "avg_frame_rate",
                    "pix_fmt",
                    "sample_rate",
                    "channels",
                    "duration",
                    "nb_frames",
                )
                if k in s
            }
            for s in data.get("streams", [])
        ],
    }


class Delivery:
    def configure_delivery(
        self,
        revision_id,
        reference_id=None,
        reference_start=0,
        audio=None,
        cues=None,
        request_id=None,
    ):
        if request_id is not None:
            return self._mutation(
                "configure_delivery",
                request_id,
                {
                    "revision_id": revision_id,
                    "reference_id": reference_id,
                    "reference_start": reference_start,
                    "audio": audio or [],
                    "cues": cues or [],
                },
                lambda: self.configure_delivery(
                    revision_id, reference_id, reference_start, audio, cues
                ),
            )
        rev = self.store.get(revision_id, "revision")
        duration = rev["brief"]["duration"]
        if not math.isfinite(reference_start) or reference_start < 0:
            raise UnfoldError("INVALID_INPUT", "Reference start must be nonnegative seconds.")
        dependencies, hashes = [], {}
        if reference_id:
            ref = self.asset(reference_id)
            if ref["role"] != "video" or ref["integrity"] != "intact":
                raise UnfoldError("INVALID_REFERENCE", "An intact video asset is required.")
            info = media_info(ref["path"])
            if info["duration"] + 0.05 < reference_start + duration:
                raise UnfoldError(
                    "INVALID_TIMING", "Reference segment is shorter than the composition."
                )
            dependencies.append(reference_id)
            hashes[reference_id] = ref["sha256"]
        tracks = []
        for track in audio or []:
            a = self.asset(track["asset_id"])
            start, offset, gain = (
                float(track.get("start", 0)),
                float(track.get("offset", 0)),
                float(track.get("gain", 1)),
            )
            if a["role"] != "audio" or a["integrity"] != "intact":
                raise UnfoldError("INVALID_AUDIO", "An intact audio asset is required.")
            info = media_info(a["path"])
            length = float(track.get("duration", min(duration - start, info["duration"] - offset)))
            if not all(math.isfinite(x) for x in (start, offset, gain, length)) or not (
                0 <= start < duration
                and offset >= 0
                and 0 <= gain <= 2
                and 0 < length <= duration - start + 0.001
                and offset + length <= info["duration"] + 0.001
            ):
                raise UnfoldError(
                    "INVALID_TIMING",
                    "Audio trim and placement must fit the source and composition; gain 0–2.",
                )
            tracks.append(
                {
                    "asset_id": a["id"],
                    "start": start,
                    "offset": offset,
                    "duration": length,
                    "gain": gain,
                }
            )
            dependencies.append(a["id"])
            hashes[a["id"]] = a["sha256"]
        if len(tracks) > 8:
            raise UnfoldError("INVALID_INPUT", "At most eight audio tracks are supported.")
        for cue in cues or []:
            if not isinstance(cue.get("text"), str) or not 0 <= cue.get("at", -1) <= duration:
                raise UnfoldError("INVALID_TIMING", "Each cue needs text and a composition time.")
        record = {
            "id": uid(),
            "kind": "delivery",
            "revision_id": revision_id,
            "reference_id": reference_id,
            "reference_start": reference_start,
            "audio": tracks,
            "cues": cues or [],
            "dependencies": dependencies,
            "hashes": hashes,
            "duration": duration,
            "dimensions": [1280, 720],
            "fps": 30,
            "time_basis": "composition seconds",
            "placement": "full canvas; reference scaled to fit with letterboxing",
            "reference_audio": "excluded",
            "graphics": "illustrative",
            "reference": "supplied recording; not authenticated",
        }
        self.store.put("delivery", record)
        self.store.event("delivery_configured", revision_id, {"delivery_id": record["id"]})
        return record

    def render_delivery(self, delivery_id, mode="video", request_id=None):
        if request_id is not None:
            return self._mutation(
                "render_delivery",
                request_id,
                {"delivery_id": delivery_id, "mode": mode},
                lambda: self.render_delivery(delivery_id, mode),
            )
        if mode not in ("video", "overlay"):
            raise UnfoldError("INVALID_INPUT", "Choose video or overlay.")
        d = self.store.get(delivery_id, "delivery")
        rev = self.inspect(d["revision_id"])
        if rev["source_integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "Composition source changed.")
        assets = {i: self.asset(i) for i in d["dependencies"]}
        if any(
            a["integrity"] != "intact" or a["sha256"] != d["hashes"][i] for i, a in assets.items()
        ):
            raise UnfoldError(
                "MATERIAL_CHANGED", "Delivery input changed. Configure a new delivery deliberately."
            )
        directory = self.store.workspace(uid())
        scene = Scene.model_validate_json(
            (Path(rev["source_path"]) / "scene.json").read_text(), context={"retained_source": True}
        )
        # The explicit overlay profile removes only the canvas background, never colored elements.
        scene.background = "transparent"
        source = directory / "source"
        resources = self.backend.retained_resources(rev["source_path"])
        self.backend.author(scene, source, resources)
        overlay = directory / "overlay.mov"
        self.backend.render(source, overlay, alpha=True)
        output = overlay
        if mode == "video":
            output = directory / "video.mp4"
            args = ["ffmpeg", "-v", "error", "-i", str(overlay)]
            if d["reference_id"]:
                args += ["-ss", str(d["reference_start"]), "-i", assets[d["reference_id"]]["path"]]
            else:
                args += [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x{Scene.model_validate_json((Path(rev['source_path']) / 'scene.json').read_text()).background.lstrip('#') if json.loads((Path(rev['source_path']) / 'scene.json').read_text())['background'] != 'transparent' else '101832'}:s=1280x720:r=30",
                ]
            filters = [
                "[1:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS[bg]",
                "[bg][0:v]overlay=0:0:shortest=1,format=yuv420p[v]",
            ]
            for index, t in enumerate(d["audio"]):
                args += ["-i", assets[t["asset_id"]]["path"]]
                filters.append(
                    f"[{index + 2}:a]atrim=start={t['offset']}:duration={t['duration']},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={t['gain']},adelay={round(t['start'] * 1000)}:all=1[a{index}]"
                )
            if d["audio"]:
                filters.append(
                    "".join(f"[a{i}]" for i in range(len(d["audio"])))
                    + f"amix=inputs={len(d['audio'])}:normalize=0,alimiter=limit=0.95:latency=1,apad[a]"
                )
            args += ["-filter_complex", ";".join(filters), "-map", "[v]"]
            if d["audio"]:
                args += ["-map", "[a]", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
            args += [
                "-t",
                str(d["duration"]),
                "-r",
                "30",
                "-frames:v",
                str(encoded_frames(d["duration"])),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output),
            ]
            run(args, timeout=240)
        info = media_info(output)
        videos = [s for s in info["streams"] if s["codec_type"] == "video"]
        expected_frames = encoded_frames(d["duration"])
        if (len(videos) != 1 or int(videos[0].get("nb_frames", 0)) != expected_frames or
                Fraction(videos[0]["avg_frame_rate"]) != FPS or
                abs(float(videos[0]["duration"]) - expected_frames / FPS) > 0.0001 or
                abs(info["duration"] - expected_frames / FPS) > 0.1):
            raise UnfoldError("INVALID_RENDER", "Delivery duration did not match.")
        artifact = self._artifact(
            rev["id"],
            output,
            {
                "sha256": digest(output),
                "duration": expected_frames / FPS,
                "frame_count": expected_frames,
                "streams": info["streams"],
                "alpha": mode == "overlay",
                "width": 1280,
                "height": 720,
                "fps": "30/1",
            },
            self.store.get(rev["project_id"], "project")["name"],
        )
        artifact.update(
            format="mov" if mode == "overlay" else "mp4",
            delivery_id=delivery_id,
            profile=mode,
            audio="silent" if mode == "overlay" or not d["audio"] else "mixed stereo",
            included_reference=bool(mode == "video" and d["reference_id"]),
        )
        self.store.put("artifact", artifact)
        self.store.event(
            "delivery_rendered", delivery_id, {"artifact_id": artifact["id"], "profile": mode}
        )
        return self.artifact(artifact["id"])

    def export_handoff(self, artifact_id, destination, request_id=None):
        if request_id is not None:
            return self._mutation(
                "export_handoff",
                request_id,
                {"artifact_id": artifact_id, "destination": str(destination)},
                lambda: self.export_handoff(artifact_id, destination),
            )
        a = self.artifact(artifact_id)
        if a["integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "Output changed or missing.")
        d = self.store.get(a["delivery_id"], "delivery")
        destination = Path(destination).expanduser().resolve()
        manifest = {
            "format": "unfold.delivery",
            "version": 1,
            "revision_id": a["revision_id"],
            "artifact_id": artifact_id,
            "profile": a["profile"],
            "alpha": a["alpha"],
            "output": "media/output." + a["format"],
            "sha256": a["sha256"],
            "timing": d,
            "audio": [],
            "reference_included": a["included_reference"],
        }
        from .fonts import retained_fonts

        revision = self.inspect(a["revision_id"])
        if revision["source_integrity"] != "intact":
            raise UnfoldError("MATERIAL_CHANGED", "Retained delivery dependencies changed.")
        fonts = retained_fonts(revision["source_path"])
        manifest["fonts"], manifest["omissions"] = [], []
        # Only requested contribution and separate audio; never include reference footage in overlay packs.
        try:
            with portable_archive(destination) as z:
                z.write(a["path"], manifest["output"])
                for t in d["audio"]:
                    asset = self.asset(t["asset_id"])
                    if (
                        asset["integrity"] != "intact"
                        or asset["sha256"] != d["hashes"][asset["id"]]
                    ):
                        raise UnfoldError(
                            "MATERIAL_CHANGED", "Audio changed since delivery configuration."
                        )
                    name = "audio/" + asset["id"] + asset["suffix"]
                    if name not in z.namelist():
                        z.write(asset["path"], name)
                    manifest["audio"].append({**t, "file": name, "sha256": asset["sha256"]})
                for identity, face in fonts.items():
                    entry = {"id": identity, **face}
                    try:
                        current = self.store.get(identity, "asset")
                    except UnfoldError:
                        current = None
                    if current and current["rights"] != "redistributable":
                        entry.update(rights=current["rights"], attribution=current["attribution"])
                    if entry["rights"] != "redistributable":
                        manifest["omissions"].append({**entry, "reason": entry["rights"] + " redistribution rights"})
                        continue
                    entry["file"] = "fonts/" + identity + face["suffix"]
                    z.write(Path(revision["source_path"]) / entry["file"], entry["file"])
                    manifest["fonts"].append(entry)
                z.writestr("manifest.json", json.dumps(manifest, indent=2))
        except FileExistsError:
            raise UnfoldError("OUTPUT_EXISTS", "Choose a new destination.") from None
        self.store.event("handoff_exported", artifact_id, {"path": str(destination)})
        return {"path": str(destination), "manifest": manifest, "sha256": digest(destination)}
