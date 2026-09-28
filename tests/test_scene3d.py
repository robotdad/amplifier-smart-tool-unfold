import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from unfold.backend import Backend, run
from unfold.models import Effect3D, Element, Link3D, Media3D, Node3D, Scene, Scene3D, Shot3D
from unfold.store import digest


def node(**overrides):
    payload = dict(id="n0", position=(0, 1, 0))
    payload.update(overrides)
    return Node3D(**payload)


def scene3d(**overrides):
    payload = dict(nodes=[node()], camera=[Shot3D(at=0)])
    payload.update(overrides)
    return Scene3D(**payload)


# --- validation -------------------------------------------------------------


def test_node_position_bounds_are_enforced():
    with pytest.raises(ValueError):
        node(position=(21, 1, 0))
    with pytest.raises(ValueError):
        node(position=(0, 13, 0))


def test_link_rejects_unknown_node_reference():
    with pytest.raises(ValueError, match="existing nodes"):
        scene3d(links=[Link3D(id="l0", from_node="n0", to_node="missing")])


def test_stream_effect_requires_a_distinct_to_node():
    with pytest.raises(ValueError, match="to_node"):
        Effect3D(id="e0", preset="stream", node="n0", start=0, end=1, count=10)
    with pytest.raises(ValueError, match="to_node"):
        Effect3D(id="e0", preset="stream", node="n0", to_node="n0", start=0, end=1, count=10)


def test_non_stream_effect_forbids_to_node():
    with pytest.raises(ValueError, match="to_node"):
        Effect3D(
            id="e0", preset="burst", node="n0", to_node="n1", start=0, end=1, count=10
        )


def test_effect_count_cap_is_enforced_per_preset():
    with pytest.raises(ValueError, match="400"):
        Effect3D(id="e0", preset="flock", node="n0", start=0, end=1, count=401)


def test_effect_total_particle_count_cannot_exceed_8000():
    effects = [
        Effect3D(id=f"e{i}", preset="field", node="n0", start=0, end=1, count=2700)
        for i in range(3)
    ]
    with pytest.raises(ValueError, match="8000"):
        scene3d(effects=effects)


def test_camera_shots_must_strictly_increase():
    with pytest.raises(ValueError, match="increasing"):
        scene3d(camera=[Shot3D(at=0), Shot3D(at=0)])


def test_first_camera_shot_must_be_at_time_zero():
    with pytest.raises(ValueError, match="time 0"):
        scene3d(camera=[Shot3D(at=1)])


def test_effect_timing_must_fit_the_scene_duration():
    effect = Effect3D(id="e0", preset="burst", node="n0", start=0, end=3, count=10)
    with pytest.raises(ValueError, match="duration"):
        Scene(title="Short", duration=2, explanation="x", scene3d=scene3d(effects=[effect]))


def test_scene3d_id_cannot_collide_with_a_2d_element_id():
    element = Element(id="n0", kind="text", x=0, y=0, width=100, height=50, text="hi")
    with pytest.raises(ValueError, match="collide"):
        Scene(
            title="Collision",
            duration=5,
            explanation="x",
            elements=[element],
            tweens=[dict(target="n0", at=0, duration=0, opacity=1)],
            scene3d=scene3d(),
        )


def test_scene3d_only_scene_needs_no_2d_elements_or_tweens():
    scene = Scene(title="Pure 3D", duration=5, explanation="x", scene3d=scene3d())
    assert scene.elements == []
    assert scene.tweens == []


def test_scene_without_scene3d_still_requires_elements_and_tweens():
    with pytest.raises(ValueError, match="at least one element"):
        Scene(title="Empty", duration=5, explanation="x")
    with pytest.raises(ValueError, match="at least one tween"):
        Scene(
            title="Empty",
            duration=5,
            explanation="x",
            elements=[Element(id="a", kind="text", x=0, y=0, width=10, height=10, text="a")],
        )


# --- author (needs a backend dir with babylonjs installed) -----------------


def build_scene3d_scene(duration=2):
    return Scene(
        title="3D scene",
        duration=duration,
        output={"resolution": "1080p"},
        explanation="Two nodes and a flow link",
        scene3d=Scene3D(
            nodes=[
                Node3D(id="alpha", position=(-5, 2, 0), label="A</script>tag"),
                Node3D(id="beta", position=(5, 2, 0)),
            ],
            links=[Link3D(id="flow", from_node="alpha", to_node="beta")],
            camera=[Shot3D(at=0), Shot3D(at=1, duration=1, azimuth=30)],
        ),
    )


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs a babylonjs-equipped backend")
def test_author_emits_canvas_and_data_block_and_copies_runtime_scripts(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    scene = build_scene3d_scene()
    source = tmp_path / "source"
    backend.author(scene, source)
    html = (source / "index.html").read_text()
    assert '<canvas id="scene3d" width="1920" height="1080"' in html
    assert '<script type="application/json" id="unfold-scene3d">' in html
    assert '<script src="babylon.js"></script><script src="scene3d_runtime.js"></script>' in html
    # A label containing a script-closing tag must not break out of the JSON data block.
    assert "</script>tag" not in html
    assert "\\u003c/script>tag" in html
    assert (source / "babylon.js").is_file()
    assert (source / "scene3d_runtime.js").is_file()
    assert digest(source / "babylon.js") == digest(backend.babylon)


def test_non_3d_scene_output_has_no_scene3d_traces(tmp_path, monkeypatch):
    backend = Backend(tmp_path)
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.gsap.write_text("// fixture")
    scene = Scene(
        title="Plain 2D",
        duration=2,
        explanation="No 3D layer",
        elements=[Element(id="card", kind="card", x=0, y=0, width=100, height=100, opacity=1)],
        tweens=[dict(target="card", at=0, duration=0, opacity=1)],
    )
    source = tmp_path / "source"
    backend.author(scene, source)
    html = (source / "index.html").read_text()
    assert "scene3d" not in html
    assert "babylon" not in html
    assert not (source / "babylon.js").exists()
    assert not (source / "scene3d_runtime.js").exists()


# --- renderer-gated (slow: software GL, deselect with -k 'not scene3d_render') ---


def render_one_worker(backend, source, output, monkeypatch):
    """Vary only worker count; keep both renders on the production capture path."""
    launch = backend._run_cli

    def one_worker(arguments, **kwargs):
        arguments = list(arguments)
        arguments[arguments.index("--workers") + 1] = "1"
        return launch(arguments, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backend, "_run_cli", one_worker)
        return backend.render(source, output)


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs a babylonjs-equipped backend")
def test_scene3d_render_is_deterministic_across_worker_counts(tmp_path, monkeypatch):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    scene = build_scene3d_scene(duration=2)
    source = tmp_path / "source"
    backend.author(scene, source)

    two_worker_output = tmp_path / "two.mp4"
    backend.render(source, two_worker_output)

    one_worker_output = tmp_path / "one.mp4"
    render_one_worker(backend, source, one_worker_output, monkeypatch)

    def framemd5(path):
        return run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"], timeout=60)

    assert framemd5(two_worker_output) == framemd5(one_worker_output)


# --- video screens ------------------------------------------------------------

import shutil  # noqa: E402

from unfold.models import Screen3D, UnfoldError  # noqa: E402

VIDEO_ID = "ab" * 16


def screen(**overrides):
    payload = dict(id="tv", asset_id=VIDEO_ID, position=(0, 2, 0))
    payload.update(overrides)
    return Screen3D(**payload)


def test_screen_rejects_bad_asset_id_and_rate():
    for bad in ({"asset_id": "not-hex"}, {"rate": 5}, {"rate": 0.1}, {"width": 20}):
        with pytest.raises(ValueError):
            screen(**bad)


def test_screen_id_must_be_unique_across_scene3d():
    with pytest.raises(ValueError, match="unique"):
        scene3d(screens=[screen(id="n0")])


def test_camera_and_moments_may_target_a_screen():
    s = scene3d(screens=[screen()], camera=[Shot3D(at=0, target="tv")],
                moments=[dict(at=0.5, kind="focus_pull", node="tv")])
    assert s.camera[0].target == "tv" and s.moments[0].node == "tv"


def test_screen_timing_must_fit_the_scene_duration():
    with pytest.raises(ValueError, match="screen timing"):
        Scene(title="t", duration=2, explanation="e",
              scene3d=scene3d(screens=[screen(appear_at=3)]))


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Needs ffmpeg")


def synthetic_clip(directory, seconds=3):
    clip = directory / "clip.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-threads", "1", str(clip)], timeout=120)
    return clip


def quadrant_clip(directory, seconds=3):
    """Red top-left, green top-right, blue bottom-left, white bottom-right: shows flips and mirrors."""
    clip = directory / "quads.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=white:size=640x360:rate=30:duration={seconds}",
         "-vf", "drawbox=x=0:y=0:w=320:h=180:color=red:t=fill,drawbox=x=320:y=0:w=320:h=180:color=lime:t=fill,"
         "drawbox=x=0:y=180:w=320:h=180:color=blue:t=fill",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-threads", "1", str(clip)], timeout=120)
    return clip


def screen_scene(duration=2, **screen_overrides):
    return Scene(
        title="Screen", duration=duration, output={"resolution": "1080p"}, explanation="A clip on a panel",
        scene3d=Scene3D(nodes=[Node3D(id="hub", position=(-4, 1, 0))], screens=[screen(**screen_overrides)],
                        camera=[Shot3D(at=0, target="tv", distance=9)]),
    )


def fixture_backend(tmp_path, monkeypatch):
    backend = Backend(tmp_path / "backend")
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.gsap.write_text("// fixture")
    backend.babylon = tmp_path / "babylon.js"
    backend.babylon.write_text("// fixture")
    return backend


def video_resources(clip):
    return {VIDEO_ID: {"role": "video", "path": str(clip), "sha256": digest(clip)}}


@needs_ffmpeg
def test_author_decodes_the_clip_into_frame_atlases(tmp_path, monkeypatch):
    backend = fixture_backend(tmp_path, monkeypatch)
    clip = synthetic_clip(tmp_path)
    source = tmp_path / "source"
    backend.author(screen_scene(), source, video_resources(clip))
    import json

    manifest = json.loads((source / "screens.json").read_text())["tv"]
    assert manifest["tile"] == [512, 288] and manifest["cols"] == 8 and manifest["fps"] == 15
    assert 29 <= manifest["frames"] <= 31  # 2 s of scene time at 15 fps
    page = manifest["pages"][0]
    assert (page["width"], page["height"]) == (4096, -(-manifest["frames"] // 8) * 288)
    assert digest(source / page["file"]) == page["sha256"]
    assert digest(source / "media" / (VIDEO_ID + ".mp4")) == digest(clip)
    html = (source / "index.html").read_text()
    assert '"screens":{"tv":' in html and "<video" not in html


@needs_ffmpeg
def test_screen_decode_is_byte_stable_across_authors(tmp_path, monkeypatch):
    backend = fixture_backend(tmp_path, monkeypatch)
    clip = synthetic_clip(tmp_path)
    first = backend.author(screen_scene(), tmp_path / "a", video_resources(clip))
    second = backend.author(screen_scene(), tmp_path / "b", video_resources(clip))
    assert first == second
    # Re-authoring from the retained copy (the render-time check) reproduces the same source.
    third = backend.author(screen_scene(), tmp_path / "c", backend.retained_resources(tmp_path / "a"))
    assert third == first


def test_screen_without_its_video_is_refused(tmp_path, monkeypatch):
    backend = fixture_backend(tmp_path, monkeypatch)
    with pytest.raises(UnfoldError) as caught:
        backend.author(screen_scene(), tmp_path / "source", {})
    assert caught.value.code == "MISSING_DEPENDENCY"


@needs_ffmpeg
def test_tampered_screen_frames_or_video_are_detected(tmp_path, monkeypatch):
    backend = fixture_backend(tmp_path, monkeypatch)
    clip = synthetic_clip(tmp_path)
    source = tmp_path / "source"
    backend.author(screen_scene(), source, video_resources(clip))
    page = next((source / "media" / "screens").glob("*.png"))
    original = page.read_bytes()
    page.write_bytes(original + b"x")
    with pytest.raises(UnfoldError) as caught:
        backend.source_hash(source)
    assert caught.value.code == "MATERIAL_CHANGED"
    page.write_bytes(original)
    video = source / "media" / (VIDEO_ID + ".mp4")
    video.write_bytes(video.read_bytes() + b"x")
    with pytest.raises(UnfoldError) as caught:
        backend.source_hash(source)
    assert caught.value.code == "MATERIAL_CHANGED"


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs a babylonjs-equipped backend")
def test_scene3d_render_with_a_mid_timeline_screen_is_deterministic_across_worker_counts(tmp_path, monkeypatch):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    clip = quadrant_clip(tmp_path)
    scene = screen_scene(duration=2, appear_at=0.6, label="Clip")
    source = tmp_path / "source"
    backend.author(scene, source, video_resources(clip))
    two_worker_output = tmp_path / "two.mp4"
    backend.render(source, two_worker_output)
    one_worker_output = tmp_path / "one.mp4"
    render_one_worker(backend, source, one_worker_output, monkeypatch)

    def framemd5(path):
        return run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"], timeout=60)

    assert framemd5(two_worker_output) == framemd5(one_worker_output)

    # Identical output proves nothing if the screen failed in both renders: the clip must be
    # visible, upright and unmirrored in the rendered frame.
    from PIL import Image

    still = tmp_path / "still.png"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(two_worker_output), "-vf", "select='eq(n\\,45)'",
         "-frames:v", "1", "-fps_mode", "vfr", str(still)], timeout=60)
    image = Image.open(still).convert("RGB")
    found = {}
    for y in range(0, image.height, 4):
        for x in range(0, image.width, 4):
            r, g, b = image.getpixel((x, y))
            kind = ("red" if r > 150 and g < 90 and b < 90 else "blue" if b > 150 and r < 90 and g < 110
                    else "white" if min(r, g, b) > 190 else None)
            if kind:
                found.setdefault(kind, []).append((x, y))
    assert all(len(found.get(k, [])) > 500 for k in ("red", "blue", "white")), {k: len(v) for k, v in found.items()}
    centre = {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v)) for k, v in found.items()}
    assert centre["red"][1] < centre["blue"][1]   # upright
    assert centre["red"][0] < centre["white"][0]  # not mirrored


# --- video on animated nodes ----------------------------------------------------


def test_node_media_is_refused_on_shapes_whose_uvs_scramble_video():
    for shape in ("torus", "icosahedron"):
        with pytest.raises(ValueError, match="media is supported"):
            node(shape=shape, media={"asset_id": VIDEO_ID})
    assert node(shape="cube", media={"asset_id": VIDEO_ID}, spin=45).media.asset_id == VIDEO_ID


def test_node_spin_is_bounded():
    with pytest.raises(ValueError):
        node(spin=400)


def test_node_media_timing_must_fit_the_scene_duration():
    with pytest.raises(ValueError, match="node media timing"):
        Scene(title="t", duration=2, explanation="e",
              scene3d=scene3d(nodes=[node(media={"asset_id": VIDEO_ID, "play_from": 3})]))


def video_nodes_scene(duration=2):
    return Scene(
        title="Video nodes", duration=duration, output={"resolution": "1080p"}, explanation="Clips on moving objects",
        scene3d=Scene3D(
            post="clean", floor=False,
            nodes=[Node3D(id="still", shape="cube", position=(-3.2, 2.2, 0), size=2.2, spin=0, ring=False,
                          media={"asset_id": VIDEO_ID}),
                   Node3D(id="globe", shape="sphere", position=(3.2, 2.2, 0), size=2.2, spin=40, ring=False,
                          appear_at=0.6, media={"asset_id": VIDEO_ID, "loop": True})],
            camera=[Shot3D(at=0, azimuth=-90, elevation=3, distance=11)]),
    )


@needs_ffmpeg
def test_author_decodes_node_media_into_its_own_atlas(tmp_path, monkeypatch):
    backend = fixture_backend(tmp_path, monkeypatch)
    clip = synthetic_clip(tmp_path)
    source = tmp_path / "source"
    backend.author(video_nodes_scene(), source, video_resources(clip))
    import json

    manifest = json.loads((source / "screens.json").read_text())
    assert set(manifest) == {"still", "globe"}
    assert all(m["tile"] == [512, 288] and m["frames"] > 0 for m in manifest.values())
    assert manifest["globe"]["play_from"] == 0.6


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs a babylonjs-equipped backend")
def test_scene3d_render_with_video_on_moving_nodes_is_deterministic_and_visible(tmp_path, monkeypatch):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    clip = quadrant_clip(tmp_path)
    scene = video_nodes_scene(duration=2)
    source = tmp_path / "source"
    backend.author(scene, source, video_resources(clip))
    two_worker_output = tmp_path / "two.mp4"
    backend.render(source, two_worker_output)
    one_worker_output = tmp_path / "one.mp4"
    render_one_worker(backend, source, one_worker_output, monkeypatch)

    def framemd5(path):
        return run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"], timeout=60)

    assert framemd5(two_worker_output) == framemd5(one_worker_output)

    from PIL import Image

    still = tmp_path / "still.png"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(two_worker_output), "-vf", "select='eq(n\\,45)'",
         "-frames:v", "1", "-fps_mode", "vfr", str(still)], timeout=60)
    image = Image.open(still).convert("RGB")

    def colours(x0, x1):
        found = {}
        for y in range(0, image.height, 3):
            for x in range(x0, x1, 3):
                r, g, b = image.getpixel((x, y))
                kind = ("red" if r > 150 and g < 90 and b < 90 else "blue" if b > 150 and r < 90 and g < 110
                        else "white" if min(r, g, b) > 190 else None)
                if kind:
                    found.setdefault(kind, []).append((x, y))
        return {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v), len(v)) for k, v in found.items()}

    cube = colours(0, image.width // 2)
    globe = colours(image.width // 2, image.width)
    assert all(cube.get(k, (0, 0, 0))[2] > 300 for k in ("red", "blue", "white")), cube
    assert cube["red"][1] < cube["blue"][1] and cube["red"][0] < cube["white"][0]   # upright, unmirrored
    assert sum(v[2] for v in globe.values()) > 300, globe                         # the spinning sphere shows the clip


def test_media_surface_is_a_small_per_shape_set():
    ok = [("cube", "every_face", None), ("cube", "one_face", 30), ("cube", "facing_camera", None),
          ("sphere", "wrap", 20), ("capsule", "facing_camera", None), ("platform", "one_face", None),
          ("cylinder", "auto", 15)]
    for shape, surface, spin in ok:
        node(shape=shape, spin=spin, media={"asset_id": VIDEO_ID, "surface": surface})
    bad = [("cube", "wrap", None), ("sphere", "one_face", None), ("platform", "every_face", None),
           ("sphere", "every_face", None)]
    for shape, surface, spin in bad:
        with pytest.raises(ValueError, match="not available"):
            node(shape=shape, spin=spin, media={"asset_id": VIDEO_ID, "surface": surface})
    with pytest.raises(ValueError, match="facing_camera"):
        node(shape="cube", spin=20, media={"asset_id": VIDEO_ID, "surface": "facing_camera"})


# --- PR25 production and bounded-resource regressions (no provider/WebGL) ------


@pytest.mark.parametrize("with_3d", [False, True])
@pytest.mark.parametrize("alpha", [False, True])
def test_production_render_selects_software_screenshot_only_for_3d(tmp_path, monkeypatch, with_3d, alpha):
    backend = fixture_backend(tmp_path, monkeypatch)
    scene = build_scene3d_scene()
    if not with_3d:
        scene = Scene(title="2D", duration=2, explanation="Legacy capture",
                      elements=[dict(id="scene3d", kind="card", x=0, y=0, width=100, height=100)],
                      tweens=[dict(target="scene3d", at=0, duration=0, opacity=1)])
    source = tmp_path / "source"
    backend.author(scene, source)
    launches = []

    def launch(arguments, **kwargs):
        launches.append((arguments, kwargs))
        Path(arguments[arguments.index("--output") + 1]).write_bytes(b"fixture")

    monkeypatch.setattr(backend, "_run_cli", launch)
    monkeypatch.setattr(backend, "probe", lambda *args, **kwargs: {
        "width": scene.output.dimensions[0], "height": scene.output.dimensions[1],
        "frame_count": 60, "fps": "30/1", "duration": 2, "encoded_duration": 2,
    })
    backend.render(source, tmp_path / ("out.mov" if alpha else "out.mp4"), alpha=alpha)
    assert len(launches) == 1
    arguments, options = launches[0]
    assert options["force_screenshot"] is with_3d
    assert arguments.count("--no-browser-gpu") == int(with_3d)
    assert "--browser-gpu" not in arguments
    assert arguments[arguments.index("--format") + 1] == ("mov" if alpha else "mp4")


def test_legacy_2d_scene3d_id_is_valid_without_a_3d_layer(tmp_path, monkeypatch):
    backend = fixture_backend(tmp_path, monkeypatch)
    data = dict(title="Legacy", duration=1, explanation="Existing 2D composition",
                elements=[dict(id="scene3d", kind="card", x=0, y=0, width=100, height=100)],
                tweens=[dict(target="scene3d", at=0, duration=0, opacity=1)])
    scene = Scene.model_validate(data, context={"retained_source": True})
    source = tmp_path / "legacy"
    checksum = backend.author(scene, source)
    assert '<div id="scene3d"' in (source / "index.html").read_text()
    assert "<canvas" not in (source / "index.html").read_text()
    assert not (source / "babylon.js").exists()
    assert backend.author(Scene.model_validate_json((source / "scene.json").read_text()),
                          tmp_path / "again") == checksum
    with pytest.raises(ValueError, match="reserved"):
        Scene.model_validate({**data, "scene3d": scene3d()})
    for reserved in ("root", "world"):
        with pytest.raises(ValueError, match="reserved"):
            Scene.model_validate({**data, "elements": [{**data["elements"][0], "id": reserved}]})


@needs_ffmpeg
@pytest.mark.parametrize("surface", ["screen", "node"])
def test_public_request_carries_video_through_production_author(tmp_path, monkeypatch, surface):
    """Real public preparation, Production and decode; replace only worker dispatch/runtime files."""
    from unfold import Brief, Grant, Unfold
    from unfold.agent import identity_asset_metadata
    from unfold.intelligence import Production

    backend = fixture_backend(tmp_path, monkeypatch)
    library = Unfold(tmp_path / "library")
    library.backend = backend
    clip = synthetic_clip(tmp_path, seconds=0.4)
    asset = library.import_asset(clip, role="video", rights="redistributable", name="Synthetic clip")
    pack = library.save_pack("Video identity", {}, [asset["id"]])
    scene = screen_scene(duration=0.2) if surface == "screen" else video_nodes_scene(duration=1)
    if surface == "screen":
        scene.scene3d.screens[0].asset_id = asset["id"]
    else:
        for n in scene.scene3d.nodes:
            n.media.asset_id = asset["id"]
    observed = {}

    def dispatch(argv, **kwargs):
        request = json.loads(Path(argv[-1]).read_text())
        observed["request"] = request
        production = Production(request)
        production.backend = backend
        observed["production"] = production
        observed["author"] = production.call("author", scene.model_dump_json())
        # Deliberately end before model dispatch, rendering or submission.
        raise UnfoldError("OFFLINE_TEST_STOP", "Authoring integration fixture complete.")

    # Replace the library's module reference, not subprocess.Popen used by real ffmpeg.
    monkeypatch.setattr("unfold.lib.subprocess", SimpleNamespace(Popen=dispatch))
    outcome = library.create(
        Brief(title="Video", intent="Offline integration fixture", duration=scene.duration,
              identity_version=pack["current_version"]),
        Grant(provider="openai", model="unused", allow_context=True, allow_frames=True, vision=True),
    )
    assert outcome["status"] != "completed"
    assert observed["author"]["authored"]
    resource = observed["request"]["resources"][asset["id"]]
    assert {k: resource[k] for k in ("role", "sha256", "path")} == {
        "role": "video", "sha256": asset["sha256"], "path": asset["path"],
    }
    assert identity_asset_metadata({asset["id"]: {**resource, "bytes": "private"}}) == {
        asset["id"]: {"name": "Synthetic clip", "role": "video"},
    }
    production = observed["production"]
    source = production.directory / "source"
    manifests = json.loads((source / "screens.json").read_text())
    assert set(manifests) == ({"tv"} if surface == "screen" else {"still", "globe"})
    assert all(m["frames"] > 0 and m["pages"] for m in manifests.values())
    retained = backend.retained_resources(source)
    # Continue through Production with only retained video metadata, not the original file.
    Path(asset["path"]).unlink()
    production.request["resources"] = retained
    production.store.put("operation", dict(id=production.request["operation_id"], status="running"))
    assert production.call("author", scene.model_dump_json()) == observed["author"]
    retained_path = Path(retained[asset["id"]]["path"])
    retained_path.write_bytes(retained_path.read_bytes() + b"changed")
    with pytest.raises(UnfoldError) as changed:
        production.call("author", scene.model_dump_json())
    assert changed.value.code == "MATERIAL_CHANGED"


@pytest.mark.parametrize("surface", ["screen", "node"])
@pytest.mark.parametrize("loop", [False, True])
def test_required_video_span_over_60_seconds_is_refused_before_decode(tmp_path, monkeypatch, surface, loop):
    backend = fixture_backend(tmp_path, monkeypatch)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"metadata-only span fixture")
    scene = screen_scene(duration=30, rate=4, loop=loop)
    if surface == "node":
        scene.scene3d.screens = []
        scene.scene3d.camera = [Shot3D(at=0)]
        scene.scene3d.nodes[0].media = Media3D(asset_id=VIDEO_ID, rate=4, loop=loop)
    monkeypatch.setattr("unfold.backend.probe_video_duration", lambda _: 120)
    def forbidden(*args, **kwargs):
        pytest.fail("Over-budget source must be refused, not decoded with a shortened span")
    monkeypatch.setattr("unfold.backend.decode_screen_atlases", forbidden)
    with pytest.raises(UnfoldError, match="60 source seconds") as caught:
        backend.author(scene, tmp_path / "source", video_resources(clip))
    assert caught.value.code == "RESOURCE_LIMIT"


@pytest.mark.parametrize("length,options,expected", [
    (120, {"rate": 2}, 60),                         # boundary, not clamped
    (130, {"rate": 4, "play_from": 20}, 40),        # delayed accelerated playback
    (70, {"media_start": 10, "loop": True}, 60),    # full remaining loop
    (12, {"media_start": 2, "rate": 4}, 10),        # real end holds last frame
    (12, {"media_start": 2, "rate": 0.25}, 7.5),    # slowed clip
])
def test_supported_video_span_semantics_are_preserved(tmp_path, monkeypatch, length, options, expected):
    backend = fixture_backend(tmp_path, monkeypatch)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"metadata-only span fixture")
    spans = []
    monkeypatch.setattr("unfold.backend.probe_video_duration", lambda _: length)
    def decode(video, media, identity, start, span, **kwargs):
        spans.append((start, span))
        return {"pages": []}
    monkeypatch.setattr("unfold.backend.decode_screen_atlases", decode)
    backend.author(screen_scene(duration=30, **options), tmp_path / "source", video_resources(clip))
    assert spans == [(options.get("media_start", 0), expected)]


@needs_ffmpeg
@pytest.mark.parametrize("size", ["32x512", "512x32", "64x512", "320x182"])
def test_atlas_geometry_is_bounded_without_cropping_or_stretching(tmp_path, size):
    from PIL import Image

    from unfold.backend import SCREEN_ATLAS_MAX, decode_screen_atlases

    clip = tmp_path / "aspect.mp4"
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15:duration=0.2",
         "-c:v", "libx264", "-threads", "1", str(clip)])
    if size == "32x512":
        with pytest.raises(UnfoldError, match="4096 pixels high") as caught:
            decode_screen_atlases(clip, tmp_path / "media", "tv", 0, 0.2)
        assert caught.value.code == "RESOURCE_LIMIT"
        assert not list((tmp_path / "media/screens").glob("*.png"))
        return
    manifest = decode_screen_atlases(clip, tmp_path / "media", "tv", 0, 0.2)
    w, h = map(int, size.split("x"))
    assert manifest["tile"] == [512, round(h * 512 / w / 2) * 2]
    for page in manifest["pages"]:
        assert 0 < page["width"] <= SCREEN_ATLAS_MAX
        assert 0 < page["height"] <= SCREEN_ATLAS_MAX
        with Image.open(tmp_path / page["file"]) as image:
            assert image.size == (page["width"], page["height"])
    assert manifest["frames"] == 3


@needs_ffmpeg
def test_pixel_budget_is_checked_before_full_decode_and_shared_by_all_surfaces(tmp_path, monkeypatch):
    from unfold.backend import decode_screen_atlases

    clip = synthetic_clip(tmp_path, seconds=0.2)
    # One row includes padding to eight columns: exactly 4096 * 288 pixels.
    row_pixels = 4096 * 288
    calls = []
    real_run = run
    def track(argv, **kwargs):
        calls.append(argv)
        return real_run(argv, **kwargs)
    monkeypatch.setattr("unfold.backend.run", track)
    with pytest.raises(UnfoldError) as caught:
        decode_screen_atlases(clip, tmp_path / "direct/media", "tv", 0, 0.2,
                             pixel_budget=row_pixels - 1)
    assert caught.value.code == "RESOURCE_LIMIT"
    assert len(calls) == 1 and calls[0][-1].endswith("probe.png")
    assert not list((tmp_path / "direct/media/screens").glob("*.png"))
    backend = fixture_backend(tmp_path, monkeypatch)
    monkeypatch.setattr("unfold.backend.SCREEN_MAX_PIXELS", row_pixels)
    scene = screen_scene(duration=0.2)
    scene.scene3d.nodes[0].media = Media3D(asset_id=VIDEO_ID)
    with pytest.raises(UnfoldError) as caught:
        backend.author(scene, tmp_path / "combined", video_resources(clip))
    assert caught.value.code == "RESOURCE_LIMIT"
    assert not (tmp_path / "combined/screens.json").exists()
    assert not (tmp_path / "combined/scene.json").exists()


@pytest.mark.skipif(not shutil.which("node"), reason="Needs node for runtime unit probes")
def test_runtime_hidden_sprite_and_projection_are_history_independent():
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "fixtures/scene3d_state.cjs"),
         str(Path(__file__).parents[1] / "src/unfold/resources/scene3d_runtime.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sprite and camera state regressions passed" in result.stdout
