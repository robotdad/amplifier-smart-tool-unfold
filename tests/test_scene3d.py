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
