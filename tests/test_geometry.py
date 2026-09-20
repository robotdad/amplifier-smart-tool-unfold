import os
from pathlib import Path

import pytest
from PIL import Image

from unfold.backend import Backend, geometry
from unfold.models import Element, Scene


def vector(**kwargs):
    return Element(
        id="v",
        kind="path",
        x=0,
        y=0,
        width=1280,
        height=720,
        points=[(200, 500), (300, 300)],
        color="#b59aff",
        opacity=1,
        arrow_end=True,
        **kwargs,
    )


def test_geometry_is_numeric_svg_and_rejects_invalid_vectors():
    shape = geometry(vector())
    assert "M 200.0 500.0 L 300.0 300.0" in shape
    assert 'class="head"' in shape
    for points in ([(0, 0)], [(0, 0), (0, 0)], [(0, 0), (float("nan"), 5)], [(0, 0), (1300, 20)]):
        data = vector().model_dump()
        data["points"] = points
        with pytest.raises(ValueError):
            Element.model_validate(data)
    with pytest.raises(ValueError):
        vector(closed=True)


def test_draw_progress_cannot_target_a_text_box():
    with pytest.raises(ValueError, match="Drawing progress"):
        Scene(
            title="Wrong target",
            duration=5,
            explanation="invalid",
            elements=[Element(id="label", kind="text", x=0, y=0, width=100, height=50, text="v")],
            tweens=[dict(target="label", at=0, duration=1, draw=1)],
        )


def test_camera_timing_and_circular_arc():
    arc = Element(id="arc", kind="arc", x=0, y=0, width=204, height=204,
                  start_angle=0, sweep_angle=90, stroke_width=4)
    assert 'A 100.0 100.0 0 0 1' in geometry(arc)
    with pytest.raises(ValueError):
        Element.model_validate({**arc.model_dump(), "sweep_angle": 360})
    with pytest.raises(ValueError, match="Camera moves"):
        Scene(title="Invalid camera", duration=5, explanation="Overlap",
              elements=[arc], tweens=[dict(target="arc", at=0, draw=1)],
              camera=[dict(at=0, duration=3, center_x=100, center_y=100, zoom=2),
                      dict(at=2, duration=1, center_x=200, center_y=200, zoom=1)])


def _morphable(**kwargs):
    return Element(
        id="p",
        kind="path",
        x=0,
        y=0,
        width=200,
        height=200,
        points=[(10, 10), (50, 50), (100, 100)],
        color="#b59aff",
        opacity=1,
        **kwargs,
    )


def test_points_tween_morphs_a_matching_path_and_validates():
    path = _morphable()
    Scene(
        title="Morph",
        duration=5,
        explanation="Triangle reshapes",
        elements=[path],
        tweens=[dict(target="p", at=0, duration=1, points=[(20, 20), (60, 60), (110, 110)])],
    )


def test_points_tween_rejects_a_mismatched_point_count():
    path = _morphable()
    with pytest.raises(ValueError, match="exactly as many points"):
        Scene(
            title="Mismatched",
            duration=5,
            explanation="Wrong count",
            elements=[path],
            tweens=[dict(target="p", at=0, duration=1, points=[(20, 20), (60, 60)])],
        )


def test_points_tween_rejects_a_non_path_target():
    text = Element(id="t", kind="text", x=0, y=0, width=100, height=50, text="hi")
    with pytest.raises(ValueError, match="only target a path"):
        Scene(
            title="Non-path",
            duration=5,
            explanation="Wrong kind",
            elements=[text],
            tweens=[dict(target="t", at=0, duration=1, points=[(0, 0), (10, 10), (20, 20)])],
        )


def test_points_tween_rejects_an_out_of_bounds_point():
    path = _morphable()
    with pytest.raises(ValueError, match="local coordinates inside"):
        Scene(
            title="Out of bounds",
            duration=5,
            explanation="Escapes bounds",
            elements=[path],
            tweens=[dict(target="p", at=0, duration=1, points=[(10, 10), (60, 60), (500, 500)])],
        )


def test_points_tween_rejects_an_arrowed_target():
    arrowed = _morphable(arrow_end=True)
    with pytest.raises(ValueError, match="arrowhead"):
        Scene(
            title="Arrowed morph",
            duration=5,
            explanation="Cannot morph an arrow",
            elements=[arrowed],
            tweens=[dict(target="p", at=0, duration=1, points=[(20, 20), (60, 60), (110, 110)])],
        )


def test_overlapping_points_tweens_on_the_same_target_are_rejected():
    path = _morphable()
    with pytest.raises(ValueError, match="must not overlap"):
        Scene(
            title="Overlap",
            duration=5,
            explanation="Two morphs collide",
            elements=[path],
            tweens=[
                dict(target="p", at=0, duration=2, points=[(20, 20), (60, 60), (110, 110)]),
                dict(target="p", at=1, duration=2, points=[(30, 30), (70, 70), (120, 120)]),
            ],
        )


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
def test_encoded_camera_centers_world_and_preserves_circle(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    scene = Scene(title="Camera test", duration=5, explanation="Zoom then pull back",
                  elements=[Element(id="ring", kind="arc", x=200, y=200,
                                    width=204, height=204, stroke_width=4,
                                    start_angle=0, sweep_angle=270,
                                    color="#f0c76e", opacity=1)],
                  tweens=[dict(target="ring", at=0, opacity=1)],
                  camera=[dict(at=0, duration=0, center_x=302, center_y=302, zoom=2),
                          dict(at=1, duration=1, center_x=640, center_y=360, zoom=1)])
    source = tmp_path / "source"
    backend.author(scene, source)
    backend.render(source, tmp_path / "camera.mp4")
    frames = backend.frames(tmp_path / "camera.mp4", [0.5, 3], tmp_path / "frames")
    close, wide = [Image.open(f["path"]).convert("RGB") for f in frames]
    def gold(image, x, y):
        return max(abs(a-b) for a,b in zip(image.getpixel((x,y)), (240,199,110))) < 40
    assert gold(close, 840, 360)  # rightmost radius at 2x
    assert gold(close, 640, 560)  # same vertical radius: no ellipse
    assert gold(wide, 402, 302)   # original world coordinates after pullback
    assert gold(wide, 302, 402)


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
def test_partial_arc_reveals_only_the_completed_stroke(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    scene = Scene(title="Progressive arc", duration=5, explanation="Half at t=2",
                  stroke_animation="svg",
                  elements=[Element(id="arc", kind="arc", x=200, y=200, width=404,
                                    height=404, stroke_width=4, start_angle=0,
                                    sweep_angle=180, color="#f0c76e", opacity=1, draw=0,
                                    glow_tip=True)],
                  tweens=[dict(target="arc", at=1, duration=2, draw=1, ease="none")])
    source = tmp_path / "source"
    backend.author(scene, source)
    backend.render(source, tmp_path / "video.mp4")
    frame = backend.frames(tmp_path / "video.mp4", [2], tmp_path / "frames")[0]
    image = Image.open(frame["path"]).convert("RGB")
    # At halfway, the 45-degree point is drawn; 135 degrees is still background.
    assert sum(image.getpixel((543, 543))) > 250
    assert sum(image.getpixel((261, 543))) < 100
    # The bright tip is attached to the exact 90-degree endpoint at halfway.
    assert min(image.getpixel((402, 602))) > 200


@pytest.mark.skipif(
    not os.environ.get("UNFOLD_TEST_BACKEND"),
    reason="Requires explicit pinned renderer installation",
)
def test_encoded_vector_draw_and_rigid_translation(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    u = vector().model_copy(
        update={"id": "u", "points": [(200, 500), (500, 400)], "color": "#f0c76e", "draw": 0}
    )
    scene = Scene(
        title="Geometric renderer test",
        duration=5,
        explanation="Vector v translates by (300,-100), u draws.",
        elements=[
            u,
            vector(),
            Element(
                id="point",
                kind="circle",
                x=800,
                y=250,
                width=60,
                height=60,
                opacity=1,
                fill="#f0c76e",
                color="#f0c76e",
                fill_opacity=1,
            ),
        ],
        tweens=[
            dict(target="u", at=0, duration=1, draw=1, ease="none"),
            dict(target="v", at=1, duration=1, x=300, y=-100, ease="none"),
        ],
    )
    source = tmp_path / "source"
    backend.author(scene, source)
    metadata = backend.render(source, tmp_path / "video.mp4")
    frames = backend.frames(tmp_path / "video.mp4", [0.1, 2.5], tmp_path / "frames")
    before, after = [Image.open(Path(f["path"])).convert("RGB") for f in frames]

    def near(pixel, expected):
        return max(abs(a - b) for a, b in zip(pixel, expected)) < 35

    assert near(before.getpixel((250, 400)), (181, 154, 255))
    assert near(after.getpixel((550, 300)), (181, 154, 255))
    assert near(after.getpixel((250, 400)), (8, 19, 31))
    assert near(before.getpixel((350, 450)), (8, 19, 31))
    assert near(after.getpixel((350, 450)), (240, 199, 110))
    assert near(after.getpixel((830, 280)), (240, 199, 110))
    assert metadata["duration"] == 5


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
def test_points_tween_morphs_a_path_outline_in_place(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    shape = Element(
        id="morph",
        kind="path",
        x=100,
        y=100,
        width=200,
        height=200,
        points=[(0, 0), (80, 0), (80, 80), (0, 80)],
        closed=True,
        color="#f0c76e",
        fill="#f0c76e",
        fill_opacity=1,
        opacity=1,
    )
    scene = Scene(
        title="Points morph",
        duration=5,
        explanation="A square morphs into a square in the opposite corner",
        elements=[shape],
        tweens=[
            # Deliberately out of order: return must start at the prior destination.
            dict(target="morph", at=3, duration=1, ease="none",
                 points=[(0, 0), (80, 0), (80, 80), (0, 80)]),
            dict(
                target="morph",
                at=1,
                duration=2,
                ease="none",
                points=[(120, 120), (200, 120), (200, 200), (120, 200)],
            )
        ],
    )
    source = tmp_path / "source"
    backend.author(scene, source)
    backend.render(source, tmp_path / "morph.mp4")
    frames = backend.frames(tmp_path / "morph.mp4", [0.5, 2, 3, 3.5, 4.5], tmp_path / "frames")
    before, midpoint, after, returning, restored = [Image.open(f["path"]).convert("RGB") for f in frames]

    def lit(image, x, y):
        return sum(image.getpixel((x, y))) > 250

    # Local (40,40): inside the FROM square only.
    assert lit(before, 140, 140)
    assert not lit(midpoint, 140, 140)
    assert not lit(after, 140, 140)
    # Local (100,100): inside only the interpolated midpoint quad (60-140, 60-140).
    assert not lit(before, 200, 200)
    assert lit(midpoint, 200, 200)
    assert not lit(after, 200, 200)
    # Local (160,160): inside the TO square only.
    assert not lit(before, 260, 260)
    assert not lit(midpoint, 260, 260)
    assert lit(after, 260, 260)

    assert lit(returning, 200, 200)
    assert not lit(returning, 140, 140)
    assert lit(restored, 140, 140)
    assert not lit(restored, 260, 260)
