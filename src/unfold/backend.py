"""Code-owned HTML, HyperFrames invocation and decoded-media observations.

Models supply validated scene data, never executable HTML/JS, paths or shell commands.
"""

import html
import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from .fonts import inspect_font, retained_fonts, validate_face
from .models import Scene, UnfoldError
from .store import digest, write_json
from .timing import FPS, encoded_frames, sample_frame


def geometry(element):
    """Emit only known SVG primitives; caller/model SVG and script are never accepted."""
    e = element
    attributes = (
        f'class="trace" pathLength="1" stroke="{e.color}" '
        f'stroke-width="{e.stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round" stroke-dasharray="1" stroke-dashoffset="{1 - e.draw}" '
        f'fill="{e.fill}" fill-opacity="{e.fill_opacity}"'
    )
    head = ""
    if e.kind == "arc":
        radius = max(0, (min(e.width, e.height) - e.stroke_width) / 2)
        start = math.radians(e.start_angle)
        end = math.radians(e.start_angle + e.sweep_angle)
        ax, ay = e.width / 2 + radius * math.cos(start), e.height / 2 + radius * math.sin(start)
        bx, by = e.width / 2 + radius * math.cos(end), e.height / 2 + radius * math.sin(end)
        shape = (
            f'<path d="M {ax} {ay} A {radius} {radius} 0 '
            f'{int(e.sweep_angle > 180)} 1 {bx} {by}" {attributes}/>'
        )
        if e.glow_tip:
            head = (
                f'<circle class="follower" cx="{ax}" cy="{ay}" r="4" '
                f'fill="#ffffff" stroke="{e.color}" stroke-width="3" '
                f'style="filter:drop-shadow(0 0 5px {e.color})"/>'
            )
    elif e.kind == "circle":
        radius = max(0, (min(e.width, e.height) - e.stroke_width) / 2)
        shape = f'<circle cx="{e.width / 2}" cy="{e.height / 2}" r="{radius}" {attributes}/>'
    else:
        points = e.points
        if e.orbit:
            cx, cy = e.orbit.center
            points = [(cx + e.orbit.radius * math.cos(math.radians(a)),
                       cy + e.orbit.radius * math.sin(math.radians(a)))
                      for a in e.orbit.angles]
            head = "".join(
                f'<circle class="orbit-marker" cx="{x}" cy="{y}" '
                f'r="{e.orbit.marker_radius}" fill="{e.color}"/>' for x, y in points
            )
        path = "M " + " L ".join(f"{x} {y}" for x, y in points)
        shape = f'<path d="{path}{" Z" if e.closed else ""}" {attributes}/>'
        if e.arrow_end:
            (ax, ay), (bx, by) = e.points[-2:]
            length = math.hypot(bx - ax, by - ay)
            dx, dy = (bx - ax) / length, (by - ay) / length
            size = max(12, e.stroke_width * 3)
            left = (bx - dx * size - dy * size / 2, by - dy * size + dx * size / 2)
            right = (bx - dx * size + dy * size / 2, by - dy * size - dx * size / 2)
            head = (
                f'<polygon class="head" points="{bx},{by} {left[0]},{left[1]} {right[0]},{right[1]}" '
                f'fill="{e.color}" opacity="{1 if e.draw == 1 else 0}"/>'
            )
    return (
        f'<div id="{e.id}" class="element" style="left:{e.x}px;top:{e.y}px;'
        f'width:{e.width}px;height:{e.height}px;opacity:{e.opacity};pointer-events:none">'
        f'<svg width="{e.width}" height="{e.height}" viewBox="0 0 {e.width} {e.height}" '
        f'xmlns="http://www.w3.org/2000/svg">{shape}{head}</svg></div>'
    )


def run(argv, timeout=180, reject_font_errors=False):
    # Renderer processes have no provider credentials, user configuration or telemetry.
    env = {
        key: os.environ[key]
        for key in ("PATH", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT")
        if key in os.environ
    }
    env.update(HYPERFRAMES_NO_TELEMETRY="1", DO_NOT_TRACK="1")
    try:
        result = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnfoldError(
            "BACKEND_FAILED", type(exc).__name__, "Check doctor and backend setup."
        ) from None
    if reject_font_errors and any(message in result.stdout + result.stderr for message in (
        "Loading the font", "Failed to decode downloaded font", "OTS parsing error",
        "Required font failed to load", "Font localization failed", "sub_timeline_readiness_timeout",
    )):
        raise UnfoldError("FONT_LOAD_FAILED", "Renderer reported a font-loading failure; output was not accepted.")
    if result.returncode:
        raise UnfoldError("BACKEND_FAILED", result.stderr[-3000:] or result.stdout[-3000:])
    return result.stdout


# Video screens: a clip is decoded once, at author time, into PNG frame atlases. The runtime
# shows the tile for scene time t, so playback never depends on a media clock.
SCREEN_ATLAS_FPS = 15
SCREEN_TILE_WIDTH = 512
SCREEN_ATLAS_MAX = 4096
SCREEN_MAX_SPAN = 60.0


def probe_video_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
               str(path)], timeout=30)
    try:
        value = float(out.strip())
    except ValueError:
        raise UnfoldError("INVALID_ASSET", "Screen video duration could not be read.") from None
    if not math.isfinite(value) or value <= 0:
        raise UnfoldError("INVALID_ASSET", "Screen video has no playable duration.")
    return value


def decode_screen_atlases(video, media_dir, screen_id, start, span):
    from PIL import Image

    out_dir = Path(media_dir) / "screens"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unfold-screen-") as temporary:
        # Single-threaded software decode with fixed filters keeps the frames byte-stable.
        run(["ffmpeg", "-v", "error", "-nostdin", "-threads", "1", "-ss", f"{start:.6f}",
             "-t", f"{span:.6f}", "-i", str(video), "-an", "-threads", "1",
             "-vf", f"fps={SCREEN_ATLAS_FPS},scale={SCREEN_TILE_WIDTH}:-2:flags=lanczos",
             "-pix_fmt", "rgb24", "-fps_mode", "cfr", "-f", "image2",
             str(Path(temporary) / "f%05d.png")], timeout=600)
        frames = sorted(Path(temporary).glob("f*.png"))
        if not frames:
            raise UnfoldError("INVALID_ASSET", "Screen video produced no frames.")
        with Image.open(frames[0]) as first:
            tile_w, tile_h = first.size
        cols = max(1, SCREEN_ATLAS_MAX // tile_w)
        rows = max(1, SCREEN_ATLAS_MAX // tile_h)
        per_page = cols * rows
        pages = []
        for offset in range(0, len(frames), per_page):
            chunk = frames[offset:offset + per_page]
            used_rows = math.ceil(len(chunk) / cols)
            atlas = Image.new("RGB", (cols * tile_w, used_rows * tile_h))
            for index, frame in enumerate(chunk):
                with Image.open(frame) as image:
                    atlas.paste(image.convert("RGB"), ((index % cols) * tile_w, (index // cols) * tile_h))
            name = f"{screen_id}_{len(pages)}.png"
            atlas.save(out_dir / name)
            pages.append({"file": f"media/screens/{name}", "frames": len(chunk),
                          "width": atlas.width, "height": atlas.height,
                          "sha256": digest(out_dir / name)})
    return {"tile": [tile_w, tile_h], "cols": cols, "fps": SCREEN_ATLAS_FPS,
            "frames": len(frames), "aspect": tile_h / tile_w, "pages": pages}



def retained_screens(directory):
    path = Path(directory) / "screens.json"
    if not path.exists():
        return {}
    screens = json.loads(path.read_text())
    for manifest in screens.values():
        identity = manifest.get("asset_id", "")
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise UnfoldError("INVALID_ASSET", "Invalid screen video identity.")
        if manifest.get("suffix") not in {".mp4", ".mov", ".webm", ".mkv", ".gif"}:
            raise UnfoldError("INVALID_ASSET", "Invalid screen video type.")
        for page in manifest.get("pages", []):
            name = page.get("file", "")
            if not name.startswith("media/screens/") or ".." in name or "/" in name[len("media/screens/"):]:
                raise UnfoldError("INVALID_ASSET", "Invalid screen frame path.")
    return screens


class Backend:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.cli = self.root / "node_modules/hyperframes/bin/hyperframes.mjs"
        self.gsap = self.root / "node_modules/gsap/dist/gsap.min.js"
        self.babylon = self.root / "node_modules/babylonjs/babylon.js"

    def _run_cli(self, arguments, timeout=240, reject_font_errors=False):
        options = {"reject_font_errors": True} if reject_font_errors else {}
        return run(["node", str(self.cli), *arguments], timeout=timeout, **options)

    def doctor(self):
        versions = {}
        for name, required in (
            ("hyperframes", "0.8.33"),
            ("gsap", "3.14.2"),
            ("babylonjs", "9.28.0"),
        ):
            try:
                actual = json.loads(
                    (self.root / "node_modules" / name / "package.json").read_text()
                )["version"]
            except (OSError, ValueError, KeyError):
                actual = None
            versions[name] = {"required": required, "actual": actual, "ready": actual == required}
        commands = {name: shutil.which(name) for name in ("node", "ffmpeg", "ffprobe")}
        return {
            "ready": all(v["ready"] for v in versions.values()) and all(commands.values()),
            "versions": versions,
            "commands": commands,
            "root": str(self.root),
        }

    def require(self):
        if not self.doctor()["ready"]:
            raise UnfoldError(
                "MISSING_PREREQUISITE",
                "Pinned renderer prerequisites are unavailable.",
                "Install the packaged backend.json with npm in the configured backend directory; install ffmpeg.",
            )

    def author(self, scene: Scene, directory, resources=None):
        self.require()
        # Canonical numeric types make authored bytes stable across JSON round trips.
        scene = Scene.model_validate(scene.model_dump(), context={"retained_source": True})
        directory = Path(directory)
        directory.mkdir(exist_ok=True)
        resources = resources or {}
        resource_manifest = {}
        font_manifest = {}
        font_css = ""
        # Validate every requested face before copying dependencies or writing source.
        used_fonts = {e.font_asset_id for e in scene.elements if e.font_asset_id}
        for identity in sorted(used_fonts):
            asset = resources.get(identity)
            if not isinstance(asset, dict) or asset.get("role") != "font":
                raise UnfoldError("MISSING_FONT", "Selected font is not available in this identity.")
            path = Path(asset["path"])
            if not path.is_file() or digest(path) != asset["sha256"]:
                raise UnfoldError("MISSING_FONT", "Selected font is missing or changed.")
            metadata = inspect_font(path)
            for e in scene.elements:
                if e.font_asset_id == identity:
                    validate_face(metadata, e.font_weight, e.font_style)
                    inspect_font(path, text=e.text + e.label)
            font_manifest[identity] = {
                "role": "font", "name": asset.get("name", metadata["family"]),
                "font": metadata, "suffix": path.suffix.lower(), "sha256": asset["sha256"],
                "rights": asset.get("rights", "unknown"), "attribution": asset.get("attribution", ""),
            }
        for identity, face in font_manifest.items():
            (directory / "fonts").mkdir(exist_ok=True)
            dest = directory / "fonts" / (identity + face["suffix"])
            shutil.copyfile(resources[identity]["path"], dest)
            if digest(dest) != face["sha256"]:
                raise UnfoldError("MATERIAL_CHANGED", "Font changed during retention.")
            metadata = face["font"]
            font_css += (f'@font-face{{font-family:unfold_{identity};src:url("fonts/{identity}{face["suffix"]}") '
                         f'format("{metadata["format"]}");font-weight:{metadata["weight"]};'
                         f'font-style:{metadata["style"]};font-display:block;}}')
        if font_manifest:
            write_json(directory / "fonts.json", font_manifest)
        elif (directory / "fonts.json").exists():
            (directory / "fonts.json").unlink()
        screens_manifest = {}
        # Every video source: screens, and nodes that show a video on their surface. Each is decoded
        # into its own atlases, keyed by its scene3d id, and covered by the source hash.
        from types import SimpleNamespace

        video_sources = []
        if scene.scene3d is not None:
            video_sources += list(scene.scene3d.screens)
            video_sources += [
                SimpleNamespace(id=n.id, appear_at=n.appear_at, **n.media.model_dump())
                for n in scene.scene3d.nodes if n.media is not None
            ]
        if video_sources:
            media = directory / "media"
            media.mkdir(exist_ok=True)
            if (media / "screens").exists():
                shutil.rmtree(media / "screens")
            for screen in video_sources:
                asset = resources.get(screen.asset_id)
                if not isinstance(asset, dict) or asset.get("role") != "video":
                    raise UnfoldError("MISSING_DEPENDENCY", "Screen video is not in the selected identity.")
                source = Path(asset["path"])
                if not source.is_file() or digest(source) != asset["sha256"]:
                    raise UnfoldError("MATERIAL_CHANGED", "Screen video is missing or changed.")
                suffix = source.suffix.lower()
                if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".gif"}:
                    raise UnfoldError("INVALID_ASSET", "Screen video must be mp4, mov, webm, mkv or gif.")
                retained = media / (screen.asset_id + suffix)
                if not retained.exists() or digest(retained) != asset["sha256"]:
                    shutil.copyfile(source, retained)
                if digest(retained) != asset["sha256"]:
                    raise UnfoldError("MATERIAL_CHANGED", "Screen video changed during retention.")
                length = probe_video_duration(retained)
                if screen.media_start >= length:
                    raise UnfoldError("INVALID_ASSET", "Screen media_start is beyond the end of the video.")
                play_from = screen.appear_at if screen.play_from is None else screen.play_from
                remaining = length - screen.media_start
                needed = remaining if screen.loop else max(0.0, scene.duration - play_from) * screen.rate
                span = max(1 / SCREEN_ATLAS_FPS, min(remaining, needed, SCREEN_MAX_SPAN))
                manifest = decode_screen_atlases(retained, media, screen.id, screen.media_start, span)
                manifest.update(asset_id=screen.asset_id, suffix=suffix, sha256=asset["sha256"],
                                play_from=play_from)
                screens_manifest[screen.id] = manifest
            write_json(directory / "screens.json", screens_manifest)
        elif (directory / "screens.json").exists():
            (directory / "screens.json").unlink()
        resources = {i: a for i, a in resources.items() if not isinstance(a, dict)}
        if resources:
            from PIL import Image

            (directory / "media").mkdir(exist_ok=True)
            for identity, path in resources.items():
                if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
                    raise UnfoldError("INVALID_ASSET", "Invalid resource identity.")
                dest = directory / "media" / (identity + ".png")
                # Decode/re-encode caller images. No SVG/HTML or arbitrary script enters the renderer.
                with Image.open(path) as image:
                    if image.width * image.height > 16000000:
                        raise UnfoldError("INVALID_ASSET", "Image exceeds 16 megapixels.")
                    image.convert("RGBA").save(dest)
                resource_manifest[identity] = digest(dest)
            write_json(directory / "resources.json", resource_manifest)
        elements = []
        for e in scene.elements:
            if e.kind == "image":
                if e.asset_id not in resource_manifest:
                    raise UnfoldError(
                        "MISSING_DEPENDENCY", "Image is not in the selected identity."
                    )
                elements.append(
                    f'<img id="{e.id}" class="element" src="media/{e.asset_id}.png" style="left:{e.x}px;top:{e.y}px;width:{e.width}px;height:{e.height}px;opacity:{e.opacity};object-fit:contain">'
                )
                continue
            if e.kind in {"path", "circle", "arc"}:
                elements.append(geometry(e))
                continue
            padding = "18px" if e.kind == "card" else "0"
            background = e.fill if e.kind != "text" else "transparent"
            border = f"1px solid {e.border}" if e.kind == "card" else "none"
            text = html.escape(e.text).replace("\n", "<br>")
            label = f'<div class="label">{html.escape(e.label)}</div>' if e.label else ""
            typography = ""
            weight, style = e.font_weight, e.font_style
            if e.font_asset_id:
                face = font_manifest[e.font_asset_id]["font"]
                weight, style = face["weight"], face["style"]
                typography += f"font-family:unfold_{e.font_asset_id};font-synthesis:none;"
                if label:
                    label = label.replace('class="label"', 'class="label" style="font-weight:inherit"')
            if weight is not None:
                typography += f"font-weight:{weight};"
            if style is not None:
                typography += f"font-style:{style};"
            if e.letter_spacing != 0:
                typography += f"letter-spacing:{e.letter_spacing}px;"
            if e.line_height != 1.22:
                typography += f"line-height:{e.line_height};"
            elements.append(
                f'<div id="{e.id}" class="element {e.kind}" style="left:{e.x}px;top:{e.y}px;'
                f"width:{e.width}px;height:{e.height}px;color:{e.color};background:{background};"
                f"font-size:{e.font_size}px;opacity:{e.opacity};border:{border};"
                f'border-radius:{e.radius}px;padding:{padding}{";" + typography if typography else ""}">{label}{text}</div>'
            )
        lines = []
        for e in scene.elements:
            if e.orbit is None:
                continue
            orbit = e.orbit
            angles = {f"a{i}": a for i, a in enumerate(orbit.angles)}
            lines.append(
                f"const orbit_{e.id}={json.dumps(angles)};"
                f"function update_{e.id}(){{"
                f"const e=document.getElementById({json.dumps(e.id)});"
                f"const p=Array.from({{length:{len(orbit.angles)}}},(_,i)=>{{"
                f"const a=orbit_{e.id}['a'+i]*Math.PI/180;"
                f"return [{orbit.center[0]}+{orbit.radius}*Math.cos(a),"
                f"{orbit.center[1]}+{orbit.radius}*Math.sin(a)];}});"
                "e.querySelector('.trace').setAttribute('d','M '+p.map(v=>v.join(' ')).join(' L ')+' Z');"
                "e.querySelectorAll('.orbit-marker').forEach((m,i)=>{"
                "m.setAttribute('cx',p[i][0]);m.setAttribute('cy',p[i][1]);});}"
            )
        morph_targets = {t.target for t in scene.tweens if t.points is not None}
        for e in scene.elements:
            if e.id not in morph_targets:
                continue
            coords = {f"{axis}{i}": value for i, point in enumerate(e.points)
                      for axis, value in zip(("x", "y"), point)}
            closing = json.dumps(" Z" if e.closed else "")
            lines.append(
                f"const morph_{e.id}={json.dumps(coords)};"
                f"function morph_update_{e.id}(){{"
                f"const p=Array.from({{length:{len(e.points)}}},(_,i)=>"
                f"[morph_{e.id}['x'+i],morph_{e.id}['y'+i]]);"
                f"document.querySelector('#{e.id} .trace').setAttribute('d',"
                f"'M '+p.map(v=>v.join(' ')).join(' L ')+{closing});}}"
            )
        for tween in sorted(scene.tweens, key=lambda t: t.at):
            props = tween.model_dump(exclude_none=True, exclude={"target", "at", "draw", "orbit_angles", "marker_opacity", "points"})
            if tween.orbit_angles is not None:
                orbit_props = {f"a{i}": a for i, a in enumerate(tween.orbit_angles)}
                orbit_props.update(duration=tween.duration, ease=tween.ease)
                encoded = json.dumps(orbit_props)[:-1] + f',"onUpdate":update_{tween.target}' + "}"
                lines.append(f'tl.to(orbit_{tween.target},{encoded},{tween.at});')
            if tween.marker_opacity is not None:
                markers = dict(opacity=tween.marker_opacity, duration=tween.duration, ease=tween.ease)
                lines.append(f'tl.to("#{tween.target} .orbit-marker",{json.dumps(markers)},{tween.at});')
            if (tween.draw is None and tween.orbit_angles is None and
                    tween.marker_opacity is None and tween.points is None) or set(props) - {"duration", "ease"}:
                lines.append(f'tl.to("#{tween.target}",{json.dumps(props)},{tween.at});')
            if tween.points is not None:
                coords = {f"{axis}{i}": value for i, point in enumerate(tween.points)
                          for axis, value in zip(("x", "y"), point)}
                coords.update(duration=tween.duration, ease=tween.ease)
                encoded = (json.dumps(coords)[:-1]
                           + f',"onUpdate":morph_update_{tween.target}' + "}")
                lines.append(f'tl.to(morph_{tween.target},{encoded},{tween.at});')
            if tween.draw is not None:
                trace = {
                    "strokeDashoffset": 1 - tween.draw,
                    "duration": tween.duration,
                    "ease": tween.ease,
                }
                if scene.stroke_animation == "svg":
                    trace["attr"] = {"stroke-dashoffset": trace.pop("strokeDashoffset")}
                encoded_trace = json.dumps(trace)
                element = next(e for e in scene.elements if e.id == tween.target)
                if element.glow_tip:
                    radius = (min(element.width, element.height) - element.stroke_width) / 2
                    callback = (
                        "()=>{const e=document.getElementById(" + json.dumps(element.id) + ");"
                        'const p=1-Number(e.querySelector(".trace").getAttribute("stroke-dashoffset"));'
                        f"const a=({element.start_angle}+p*{element.sweep_angle})*Math.PI/180;"
                        'const f=e.querySelector(".follower");'
                        f'f.setAttribute("cx",{element.width / 2}+{radius}*Math.cos(a));'
                        f'f.setAttribute("cy",{element.height / 2}+{radius}*Math.sin(a));'
                        "}"
                    )
                    encoded_trace = encoded_trace[:-1] + ',"onUpdate":' + callback + "}"
                lines.append(f'tl.to("#{tween.target} .trace",{encoded_trace},{tween.at});')
                if next(e for e in scene.elements if e.id == tween.target).arrow_end:
                    lines.append(
                        f'tl.set("#{tween.target} .head",{{opacity:{1 if tween.draw == 1 else 0}}},{tween.at + tween.duration if tween.draw == 1 else tween.at});'
                    )
        body = "".join(elements)
        width, height = scene.output.dimensions
        if scene.camera:
            body = (
                f'<div id="world" style="position:absolute;width:{width}px;height:{height}px;transform-origin:0 0">'
                + body
                + "</div>"
            )
            for move in scene.camera:
                props = {
                    "x": width // 2 - move.center_x * move.zoom,
                    "y": height // 2 - move.center_y * move.zoom,
                    "scale": move.zoom,
                    "duration": move.duration,
                    "ease": move.ease,
                }
                lines.append(f'tl.to("#world",{json.dumps(props)},{move.at});')
        if scene.scene3d is not None:
            # First child of #root, before the (optional) #world wrapper / 2D elements.
            scene3d_data = json.dumps(
                {
                    "width": width,
                    "height": height,
                    "duration": scene.duration,
                    "fps": 30,
                    "transparent": scene.background == "transparent",
                    "scene3d": scene.scene3d.model_dump(),
                    "screens": screens_manifest,
                },
                separators=(",", ":"),
            ).replace("<", "\\u003c")
            body = (
                f'<canvas id="scene3d" width="{width}" height="{height}" '
                'style="position:absolute;left:0;top:0;width:100%;height:100%"></canvas>'
                f'<script type="application/json" id="unfold-scene3d">{scene3d_data}</script>'
            ) + body
        # The pinned renderer awaits document.fonts.ready before capture. Register
        # the populated timeline only after all required faces have loaded as well.
        font_gate = ""
        font_gate_end = ""
        if font_manifest:
            loads = [f'document.fonts.load({json.dumps(str(f["font"]["style"]) + " " + str(f["font"]["weight"]) + " 28px unfold_" + i)})'
                     for i, f in font_manifest.items()]
            font_gate = "Promise.all([" + ",".join(loads) + "]).then(faces=>{if(faces.some(f=>!f.length))throw new Error('Required font failed to load');"
            font_gate_end = "}).catch(error=>{document.documentElement.dataset.fontError=String(error);throw error;});"
        font_policy = " font-src 'self' data:;" if font_manifest else ""
        scene3d_scripts = (
            '<script src="babylon.js"></script><script src="scene3d_runtime.js"></script>'
            if scene.scene3d is not None
            else ""
        )
        img_policy = "'self' data: blob:" if scene.scene3d is not None else "'self'"
        # A scene3d-only scene may have no 2D tweens; an inert clock tween keeps the paused
        # timeline as long as the composition so the renderer seeks the full duration.
        timeline_registration = (
            f"tl.to({{}},{{duration:{scene.duration}}},0);"
            "(window.__unfoldScene3DReady||Promise.resolve())"
            ".then(()=>{window.__timelines.unfold=tl;});"
            if scene.scene3d is not None
            else "window.__timelines.unfold=tl;"
        )
        document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; img-src {img_policy};{font_policy} connect-src 'none'; object-src 'none'; frame-src 'none'">
<title>{html.escape(scene.title)}</title><script src="gsap.min.js"></script>{scene3d_scripts}<style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden}}
body{{font-family:system-ui,sans-serif}}#root{{position:relative;width:100%;height:100%;background:{scene.background};overflow:hidden}}
.element{{position:absolute;line-height:1.22;transform-origin:center center;font-weight:550}}
.label{{font-size:14px;line-height:1.2;letter-spacing:1.5px;margin-bottom:10px;font-weight:600}}
.line,.dot{{pointer-events:none}}{font_css}
</style></head><body><div id="root" data-composition-id="unfold" data-start="0" data-width="{width}" data-height="{height}" data-duration="{scene.duration}">
{body}</div><script>
{font_gate}window.__timelines=window.__timelines||{{}};const tl=gsap.timeline({{paused:true}});
{"".join(lines)}
{timeline_registration}{font_gate_end}
</script></body></html>'''
        (directory / "index.html").write_text(document)
        shutil.copyfile(self.gsap, directory / "gsap.min.js")
        if scene.scene3d is not None:
            from importlib.resources import files

            shutil.copyfile(self.babylon, directory / "babylon.js")
            shutil.copyfile(
                str(files("unfold").joinpath("resources/scene3d_runtime.js")),
                directory / "scene3d_runtime.js",
            )
        write_json(directory / "scene.json", scene.model_dump())
        return self.source_hash(directory)

    def source_hash(self, directory):
        import hashlib

        resource_path = Path(directory) / "resources.json"
        extra = ""
        if resource_path.exists():
            resources = json.loads(resource_path.read_text())
            for identity, expected in sorted(resources.items()):
                if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
                    raise UnfoldError("INVALID_ASSET", "Invalid resource identity.")
                actual = digest(Path(directory) / "media" / (identity + ".png"))
                if actual != expected:
                    raise UnfoldError("MATERIAL_CHANGED", "Retained image changed.")
                extra += identity + actual
        for identity, face in sorted(retained_fonts(directory).items()):
            extra += identity + face["sha256"]
        if (Path(directory) / "fonts.json").exists():
            extra += digest(Path(directory) / "fonts.json")
        for manifest in retained_screens(directory).values():
            video = Path(directory) / "media" / (manifest["asset_id"] + manifest["suffix"])
            if digest(video) != manifest["sha256"]:
                raise UnfoldError("MATERIAL_CHANGED", "Retained screen video changed.")
            for page in manifest["pages"]:
                if digest(Path(directory) / page["file"]) != page["sha256"]:
                    raise UnfoldError("MATERIAL_CHANGED", "Retained screen frames changed.")
        if (Path(directory) / "screens.json").exists():
            extra += digest(Path(directory) / "screens.json")
        names = ["index.html", "gsap.min.js", "scene.json"]
        # Present only when scene3d was authored; absent for non-3D scenes (unchanged hash).
        for name in ("babylon.js", "scene3d_runtime.js"):
            if (Path(directory) / name).exists():
                names.append(name)
        return hashlib.sha256(
            (extra + "".join(digest(Path(directory) / name) for name in names)).encode()
        ).hexdigest()

    def retained_resources(self, directory):
        directory = Path(directory)
        manifest = directory / "resources.json"
        resources = {i: directory / "media" / (i + ".png")
                     for i in json.loads(manifest.read_text())} if manifest.exists() else {}
        resources.update({i: {**face, "path": str(directory / "fonts" / (i + face["suffix"]))}
                          for i, face in retained_fonts(directory).items()})
        resources.update({m["asset_id"]: {"role": "video", "sha256": m["sha256"],
                                          "path": str(directory / "media" / (m["asset_id"] + m["suffix"]))}
                          for m in retained_screens(directory).values()})
        return resources

    def render(self, directory, output, alpha=False):
        self.require()
        # Regenerate executable bytes from validated scene data before any browser execution.
        scene = Scene.model_validate_json(
            (Path(directory) / "scene.json").read_text(), context={"retained_source": True}
        )
        expected = self.source_hash(directory)
        with tempfile.TemporaryDirectory(
            prefix="unfold-validate-", dir=Path(directory).parent
        ) as temporary:
            resources = self.retained_resources(directory)
            self.author(scene, temporary, resources)
            compare_names = ["index.html", "gsap.min.js"]
            if scene.scene3d is not None:
                compare_names += ["babylon.js", "scene3d_runtime.js"]
            if any(
                digest(Path(temporary) / name) != digest(Path(directory) / name)
                for name in compare_names
            ):
                raise UnfoldError(
                    "SOURCE_CHANGED",
                    "Generated source was externally modified; no code was executed or replaced.",
                )
        fonts = retained_fonts(directory)
        if fonts:
            from importlib.resources import files

            try:
                run(["node", str(files("unfold").joinpath("resources/check_fonts.cjs")), str(self.root),
                     *[str(Path(directory) / "fonts" / (i + f["suffix"])) for i, f in fonts.items()]],
                    timeout=30)
            except UnfoldError as exc:
                raise UnfoldError("FONT_LOAD_FAILED", "Required font failed browser validation: " + str(exc)) from None
        # Software GL is ~1.5 s/frame with 2 workers; scale the budget with scene3d duration.
        timeout = (
            240 if scene.scene3d is None else max(240, int(120 + 75 * scene.duration))
        )
        self._run_cli(
            [
                "render",
                str(directory),
                "--output",
                str(output),
                "--format",
                "mov" if alpha else "mp4",
                "--fps",
                "30",
                "--workers",
                "2",
                "--quality",
                "standard",
            ],
            timeout=timeout,
            reject_font_errors=bool(fonts),
        )
        meta = self.probe(output, alpha=alpha)
        if (
            (meta["width"], meta["height"]) != scene.output.dimensions
            or meta["frame_count"] != encoded_frames(scene.duration)
            or abs(meta["encoded_duration"] - meta["duration"]) > 0.0001
            or Fraction(meta["fps"]) != FPS
        ):
            raise UnfoldError(
                "INVALID_RENDER", "Rendered dimensions or duration do not match source."
            )
        return {
            "source_sha256": expected,
            "sha256": digest(output),
            **meta,
            "method": "ffprobe of encoded video stream",
            "audio": "silent",
            "alpha": alpha,
            "output": scene.output.model_dump(),
        }

    def probe(self, path, alpha=False):
        data = json.loads(
            run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        videos = [s for s in data["streams"] if s["codec_type"] == "video"]
        if len(videos) != 1 or videos[0]["codec_name"] != ("prores" if alpha else "h264"):
            raise UnfoldError("INVALID_RENDER", "Expected one H.264 video stream.")
        if alpha and "a" not in videos[0].get("pix_fmt", ""):
            raise UnfoldError("INVALID_RENDER", "Expected an alpha channel.")
        if any(s["codec_type"] == "audio" for s in data["streams"]):
            raise UnfoldError("INVALID_RENDER", "This profile promises silent output.")
        frames = int(videos[0]["nb_frames"])
        rate = Fraction(videos[0]["avg_frame_rate"])
        return {
            "frame_count": frames,
            "encoded_duration": float(videos[0]["duration"]),
            "width": videos[0]["width"],
            "height": videos[0]["height"],
            "duration": frames / float(rate),
            "fps": videos[0]["avg_frame_rate"],
            "bytes": Path(path).stat().st_size,
        }

    def frames(self, video, times, directory):
        meta = self.probe(video)
        if not times or len(times) > 12 or any(not 0 <= t < meta["duration"] for t in times):
            raise UnfoldError("INVALID_INPUT", "Sample 1–12 times within the encoded duration.")
        directory = Path(directory)
        directory.mkdir(exist_ok=True)
        result = []
        for i, time in enumerate(times):
            frame = min(sample_frame(time, float(Fraction(meta["fps"]))), meta["frame_count"] - 1)
            path = directory / f"frame-{i}.jpg"
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(video),
                    "-vf",
                    f"select=eq(n\\,{frame})",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    str(path),
                ],
                timeout=30,
            )
            result.append({"time": frame / float(Fraction(meta["fps"])), "requested_time": time,
                           "path": str(path), "sha256": digest(path)})
        return result
