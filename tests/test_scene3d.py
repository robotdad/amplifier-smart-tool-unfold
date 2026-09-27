import os

import pytest

from unfold.backend import Backend, run
from unfold.models import Effect3D, Element, Link3D, Node3D, Scene, Scene3D, Shot3D
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


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs a babylonjs-equipped backend")
def test_scene3d_render_is_deterministic_across_worker_counts(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    scene = build_scene3d_scene(duration=2)
    source = tmp_path / "source"
    backend.author(scene, source)

    two_worker_output = tmp_path / "two.mp4"
    backend.render(source, two_worker_output)

    one_worker_output = tmp_path / "one.mp4"
    timeout = max(240, int(120 + 75 * scene.duration))
    backend._run_cli(
        [
            "render",
            str(source),
            "--output",
            str(one_worker_output),
            "--format",
            "mp4",
            "--fps",
            "30",
            "--workers",
            "1",
            "--quality",
            "standard",
        ],
        timeout=timeout,
    )

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
def test_scene3d_render_with_a_mid_timeline_screen_is_deterministic_across_worker_counts(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    clip = quadrant_clip(tmp_path)
    scene = screen_scene(duration=2, appear_at=0.6, label="Clip")
    source = tmp_path / "source"
    backend.author(scene, source, video_resources(clip))
    two_worker_output = tmp_path / "two.mp4"
    backend.render(source, two_worker_output)
    one_worker_output = tmp_path / "one.mp4"
    backend._run_cli(["render", str(source), "--output", str(one_worker_output), "--format", "mp4",
                      "--fps", "30", "--workers", "1", "--quality", "standard"],
                     timeout=max(240, int(120 + 75 * scene.duration)))

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
