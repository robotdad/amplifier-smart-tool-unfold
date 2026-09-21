"""Deterministic retained video for protocol/browser tests; never a creative-quality claim."""

import subprocess
from pathlib import Path

from unfold import Unfold
from unfold.store import digest, uid


def seed_media_library(root, *, playable=True, duration=5):
    """Return (library, project, revisions, artifacts); FFmpeg only, no model/backend."""
    library = Unfold(root)
    project, revisions, artifacts = uid(), [uid(), uid()], [uid(), uid()]
    directory = library.store.workspace(uid())
    source = directory / "source"
    source.mkdir()
    for name in ("index.html", "gsap.min.js", "scene.json"):
        (source / name).write_text("Transport fixture only: " + name)
    video = directory / "fixture.mp4"
    if playable:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=320x180:rate=30:duration={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(video),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    else:
        video.write_bytes(bytes(range(256)) * 2048)
    library.store.put(
        "project",
        {
            "id": project,
            "kind": "project",
            "name": "Motion review fixture",
            "current_revision": revisions[-1],
            "revisions": revisions,
        },
    )
    for number, (revision, artifact) in enumerate(zip(revisions, artifacts, strict=True), start=1):
        library.store.put(
            "revision",
            {
                "id": revision,
                "kind": "revision",
                "project_id": project,
                "brief": {
                    "title": "Transport fixture",
                    "intent": "Verify retained media playback",
                    "duration": duration,
                },
                "source": str(source.relative_to(library.store.root)),
                "source_sha256": library.backend.source_hash(source),
                "artifacts": [artifact],
                "checks": {
                    "fixture": "FFmpeg test source; no generated animation or creative assessment"
                },
            },
        )
        library.store.put(
            "artifact",
            {
                "id": artifact,
                "kind": "artifact",
                "revision_id": revision,
                "name": f"Transport fixture {number}",
                "format": "mp4",
                "relative_path": str(video.relative_to(library.store.root)),
                "sha256": digest(video),
            },
        )
    return library, project, revisions, artifacts


if __name__ == "__main__":
    import json
    import sys

    library, project, revisions, artifacts = seed_media_library(Path(sys.argv[1]))
    print(
        json.dumps(
            {
                "library": str(library.store.root),
                "project": project,
                "revisions": revisions,
                "artifacts": artifacts,
            }
        )
    )
