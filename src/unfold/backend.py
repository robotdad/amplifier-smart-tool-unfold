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


class Backend:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.cli = self.root / "node_modules/hyperframes/bin/hyperframes.mjs"
        self.gsap = self.root / "node_modules/gsap/dist/gsap.min.js"

    def _run_cli(self, arguments, timeout=240, reject_font_errors=False):
        options = {"reject_font_errors": True} if reject_font_errors else {}
        return run(["node", str(self.cli), *arguments], timeout=timeout, **options)

    def doctor(self):
        versions = {}
        for name, required in (("hyperframes", "0.8.33"), ("gsap", "3.14.2")):
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
        document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self';{font_policy} connect-src 'none'; object-src 'none'; frame-src 'none'">
<title>{html.escape(scene.title)}</title><script src="gsap.min.js"></script><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden}}
body{{font-family:system-ui,sans-serif}}#root{{position:relative;width:100%;height:100%;background:{scene.background};overflow:hidden}}
.element{{position:absolute;line-height:1.22;transform-origin:center center;font-weight:550}}
.label{{font-size:14px;line-height:1.2;letter-spacing:1.5px;margin-bottom:10px;font-weight:600}}
.line,.dot{{pointer-events:none}}{font_css}
</style></head><body><div id="root" data-composition-id="unfold" data-start="0" data-width="{width}" data-height="{height}" data-duration="{scene.duration}">
{body}</div><script>
{font_gate}window.__timelines=window.__timelines||{{}};const tl=gsap.timeline({{paused:true}});
{"".join(lines)}
window.__timelines.unfold=tl;{font_gate_end}
</script></body></html>'''
        (directory / "index.html").write_text(document)
        shutil.copyfile(self.gsap, directory / "gsap.min.js")
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
        return hashlib.sha256(
            (
                extra
                + "".join(
                    digest(Path(directory) / name)
                    for name in ("index.html", "gsap.min.js", "scene.json")
                )
            ).encode()
        ).hexdigest()

    def retained_resources(self, directory):
        directory = Path(directory)
        manifest = directory / "resources.json"
        resources = {i: directory / "media" / (i + ".png")
                     for i in json.loads(manifest.read_text())} if manifest.exists() else {}
        resources.update({i: {**face, "path": str(directory / "fonts" / (i + face["suffix"]))}
                          for i, face in retained_fonts(directory).items()})
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
            if any(
                digest(Path(temporary) / name) != digest(Path(directory) / name)
                for name in ("index.html", "gsap.min.js")
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
            timeout=240,
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
