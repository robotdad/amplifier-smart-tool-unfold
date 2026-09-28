"""Private bounded straight-alpha acquisition for transparent mixed compositions.

No intelligence, caller code, network fetch or reusable frame cache. The browser
reads its generated framebuffer; this owner encodes the bytes without compositing.
"""

import json
import math
import os
import queue
import struct
import subprocess
import threading
from importlib.resources import files
from pathlib import Path
from time import monotonic

from PIL import Image

from .models import UnfoldError
from .processes import stop_worker
from .store import digest, write_json
from .timing import encoded_frames

MAX_PIXELS = 512 * 1024 * 1024
MAX_BYTES = MAX_PIXELS * 4 + 32 * 1024 * 1024
MAX_METADATA = 65536
MAX_LABEL_BYTES = 8 * 1024 * 1024
LAYER_FILES = ("layer_acquire.cjs", "layer_labels.js", "layer_player.js")
LABEL_KEYS = {"text", "x", "y", "width", "height", "opacity", "color", "background",
              "border", "shadow"}


def remaining(deadline):
    seconds = deadline - monotonic()
    if seconds <= 0:
        raise UnfoldError("RESOURCE_LIMIT", "Transparent layer render deadline exhausted.")
    return seconds


def check_budget(scene):
    width, height = scene.output.dimensions
    count = encoded_frames(scene.duration)
    if width * height * count > MAX_PIXELS:
        raise UnfoldError(
            "RESOURCE_LIMIT", "Transparent 3D staging exceeds 512 mebipixels.",
            "Use a shorter composition or lower native resolution; no trimming or downscaling was applied.",
        )
    return width, height, count


def validate_labels(labels):
    """Fixed runtime-label protocol: no HTML, CSS fragments, URLs or unknown keys."""
    if not isinstance(labels, list) or len(labels) > 28:
        raise ValueError("Invalid layer label count")
    for label in labels:
        if not isinstance(label, dict) or set(label) != LABEL_KEYS:
            raise ValueError("Invalid layer label fields")
        if not isinstance(label["text"], str) or len(label["text"]) > 40:
            raise ValueError("Invalid layer label text")
        for key in ("x", "y", "width", "height", "opacity"):
            number = label[key]
            if type(number) not in (int, float) or not math.isfinite(number):
                raise ValueError("Invalid layer label number")
            if key == "opacity":
                if not 0 <= number <= 1:
                    raise ValueError("Invalid layer opacity")
            elif abs(number) > 100000 or (key in {"width", "height"} and number < 0):
                raise ValueError("Layer label geometry exceeds bound")
        for key in ("color", "background", "border", "shadow"):
            rgba = label[key]
            if (not isinstance(rgba, list) or len(rgba) != 4
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in rgba)
                    or any(not 0 <= x <= 255 for x in rgba[:3]) or not 0 <= rgba[3] <= 1):
                raise ValueError("Invalid layer color")
    return labels


def _read_exact(stream, length):
    value = bytearray()
    while len(value) < length:
        chunk = stream.read(length - len(value))
        if not chunk:
            raise ValueError("Truncated layer acquisition pipe")
        value.extend(chunk)
    return bytes(value)


def _acquire(argv, directory, width, height, count, deadline):
    """Pipe reader thread keeps the owner deadline effective even on a stuck read.

    One acknowledged frame at a time; the producer cannot grow an output queue.
    The subprocess stays a descendant of the normal execution owner for cancellation.
    """
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT")
           if k in os.environ}
    env.update(HYPERFRAMES_NO_TELEMETRY="1", DO_NOT_TRACK="1")
    remaining(deadline)
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env, bufsize=0)
    packets = queue.Queue(maxsize=1)
    stopped = threading.Event()
    diagnostics = bytearray()
    raw_size = width * height * 4

    def offer(value):
        while not stopped.is_set():
            try:
                packets.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def read_packets():
        try:
            for _ in range(count + 2):  # identity, exact frame count, completion
                size = struct.unpack(">I", _read_exact(process.stdout, 4))[0]
                if not 1 <= size <= MAX_METADATA:
                    raise ValueError("Layer metadata exceeds bound")
                packet = json.loads(_read_exact(process.stdout, size))
                if not isinstance(packet, dict):
                    raise ValueError("Invalid layer packet")
                data = _read_exact(process.stdout, raw_size) if packet.get("kind") == "frame" else None
                offer((packet, data))
            # Completion also requires actual EOF: no silent trailing data.
            if process.stdout.read(1):
                raise ValueError("Unexpected layer protocol data")
        except Exception as exc:
            offer(exc)

    def drain_errors():
        while chunk := process.stderr.read(4096):
            diagnostics.extend(chunk[:max(0, 16384 - len(diagnostics))])

    reader = threading.Thread(target=read_packets, daemon=True)
    drainer = threading.Thread(target=drain_errors, daemon=True)
    reader.start()
    drainer.start()
    records, total, label_bytes = [], 0, 0

    def receive():
        try:
            value = packets.get(timeout=remaining(deadline))
        except queue.Empty:
            raise UnfoldError("RESOURCE_LIMIT", "Layer acquisition timed out waiting for a frame.") from None
        if isinstance(value, Exception):
            raise value
        return value

    try:
        identity, _ = receive()
        if (set(identity) != {"kind", "browser", "babylon", "renderer", "premultipliedAlpha"}
                or identity["kind"] != "identity" or identity["babylon"] != "9.28.0"
                or identity["premultipliedAlpha"] is not False
                or not isinstance(identity["renderer"], str) or "SwiftShader" not in identity["renderer"]
                or not isinstance(identity["browser"], str)):
            raise ValueError("Layer acquisition runtime is not the pinned software renderer")
        for index in range(count):
            packet, raw = receive()
            if (set(packet) != {"kind", "index", "width", "height", "labels"}
                    or packet["kind"] != "frame" or type(packet["index"]) is not int
                    or packet["index"] != index or packet["width"] != width or packet["height"] != height):
                raise ValueError("Invalid layer frame identity/dimensions")
            labels = validate_labels(packet["labels"])
            label_bytes += len(json.dumps(labels).encode())
            if label_bytes > MAX_LABEL_BYTES:
                raise UnfoldError("RESOURCE_LIMIT", "Transparent layer label metadata exceeds 8 MiB.")
            remaining(deadline)
            target = directory / f"frame_{index + 1:06d}.png"
            image = Image.frombytes("RGBA", (width, height), raw).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            # Exclusive owner-created outputs. No previous render's cache is consulted.
            with target.open("xb") as stream:
                image.save(stream, format="PNG", compress_level=6)
            image.close()
            total += target.stat().st_size
            if total > MAX_BYTES:
                raise UnfoldError("RESOURCE_LIMIT", "Transparent layer PNG byte allowance exceeded.")
            records.append({"sha256": digest(target), "labels": labels})
            remaining(deadline)
            process.stdin.write(b"\n")  # back-pressure acknowledgement, no page/file authority
        done, _ = receive()
        if done != {"kind": "done", "count": count}:
            raise ValueError("Incomplete layer acquisition")
        process.stdin.close()
        process.wait(timeout=remaining(deadline))
        reader.join(timeout=remaining(deadline))
        if reader.is_alive():
            raise UnfoldError("RESOURCE_LIMIT", "Layer protocol did not close before the deadline.")
        if not packets.empty():
            value = packets.get_nowait()
            if isinstance(value, Exception):
                raise value
            raise ValueError("Unexpected extra layer packet")
        if process.returncode:
            raise ValueError("Layer acquisition failed: " + diagnostics.decode(errors="replace")[-2000:])
        return {"version": 1, "width": width, "height": height, "fps": 30, "count": count,
                "frames": records, "runtime": identity, "png_bytes": total}
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise UnfoldError("LAYER_ACQUISITION_FAILED", str(exc)[:2200]) from None
    finally:
        stopped.set()
        # Freeze/stop the still-owned root AND descendants together. Terminating
        # only the root first could orphan a browser before we can identify it.
        # Successful helper completion closes its browser before sending "done".
        if process.poll() is None:
            stop_worker(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
        reader.join(timeout=2)
        drainer.join(timeout=2)


def stage_layer(backend, scene, directory, staging, deadline):
    remaining(deadline)
    width, height, count = check_budget(scene)
    staging = Path(staging)
    source = staging / "acquisition"
    resources = backend.retained_resources(directory, deadline=deadline)
    remaining(deadline)
    # Use the same generated scene/layout for labels and raw framebuffer. No
    # model-authored JS, historical HTML or readback-derived scene substitutions.
    backend.author(scene, source, resources, deadline=deadline)
    remaining(deadline)
    frames = staging / "layer-frames"
    frames.mkdir()
    helper = str(files("unfold").joinpath("resources/layer_acquire.cjs"))
    manifest = _acquire(
        ["node", helper, str(backend.root), str(source), str(width), str(height),
         str(count), str(int(remaining(deadline) * 1000))],
        frames, width, height, count, deadline,
    )
    remaining(deadline)
    final = staging / "composition"
    backend.author(scene, final, resources, _layer_frames=manifest, deadline=deadline)
    remaining(deadline)
    frames.rename(final / "layer-frames")
    write_json(final / "layer-manifest.json", manifest)
    remaining(deadline)
    return final