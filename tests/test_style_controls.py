"""Opt-in stylistic subtraction; no provider calls or historical-source migration."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from unfold.backend import Backend, run
from unfold.models import Node3D, PostOverrides3D, Scene, Scene3D, Screen3D
from unfold.store import digest

VIDEO = "ab" * 16


@pytest.mark.parametrize("color", ["#123abc", "#FF0088", "#000000", "#ffffff"])
def test_node_color_is_bounded_hex_independent_of_role(color):
    node = Node3D(id="object", position=(0, 1, 0), role="tool", color=color)
    assert node.color == color and node.role == "tool"


@pytest.mark.parametrize("color", ["red", "#123", "#12345678", "#gg0000", "url(file:///secret)", "</script>"])
def test_node_color_rejects_invalid_or_executable_values(color):
    with pytest.raises(ValueError):
        Node3D(id="object", position=(0, 1, 0), color=color)


@pytest.mark.parametrize("field,maximum", [("bloom_weight", 1), ("glow_intensity", 2)])
def test_post_override_range_and_selectivity(field, maximum):
    for value in (0, maximum, maximum / 2):
        post = PostOverrides3D(**{field: value})
        assert getattr(post, field) == value
        assert sum(v is not None for v in post.model_dump().values()) == 1
    for value in (-0.01, maximum + 0.01, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            PostOverrides3D(**{field: value})
    with pytest.raises(ValueError):
        PostOverrides3D(shader="arbitrary code")


def test_optional_style_controls_preserve_null_defaults_and_validate_names():
    node = Node3D(id="n", position=(0, 1, 0))
    screen = Screen3D(id="s", position=(0, 1, 0), asset_id=VIDEO)
    scene = Scene3D(nodes=[node], camera=[{"at": 0}])
    assert node.entrance is node.idle_motion is node.color is screen.entrance is None
    assert scene.post_overrides is scene.floor_grid is None
    assert scene.post == "cinematic" and scene.floor and node.ring
    for field, value in (("entrance", "fade"), ("idle_motion", "jitter")):
        with pytest.raises(ValueError):
            Node3D(id="n", position=(0, 1, 0), **{field: value})
    with pytest.raises(ValueError):
        Screen3D(id="s", position=(0, 1, 0), asset_id=VIDEO, entrance="pop")


def test_style_controls_roundtrip_without_mutating_other_choices():
    scene = Scene(title="Still", duration=2, explanation="No imposed movement",
                  scene3d={"nodes": [{"id": "n", "position": [0, 1, 0], "entrance": "none",
                                     "idle_motion": "none", "color": "#d83a20"}],
                           "camera": [{"at": 0}], "floor_grid": False,
                           "post_overrides": {"bloom_weight": 0}})
    again = Scene.model_validate_json(scene.model_dump_json())
    assert again == scene
    assert again.scene3d.post_overrides.glow_intensity is None
    assert again.scene3d.nodes[0].spin is None
    assert again.scene3d.post == "cinematic"


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs pinned Babylon/browser backend")
def test_style_controls_in_real_babylon(tmp_path):
    from PIL import Image, ImageChops

    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    clip = tmp_path / "quadrants.mp4"
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=white:size=320x180:rate=30:duration=2",
         "-vf", "drawbox=x=0:y=0:w=160:h=90:color=red:t=fill,"
         "drawbox=x=160:y=0:w=160:h=90:color=lime:t=fill,"
         "drawbox=x=0:y=90:w=160:h=90:color=blue:t=fill",
         "-c:v", "libx264", "-threads", "1", str(clip)])
    resources = {VIDEO: {"role": "video", "path": str(clip), "sha256": digest(clip)}}
    simple = dict(
        title="Style boundary", duration=2, explanation="Mechanical controls, not creative acceptance",
        scene3d=dict(environment="lab_white", post="clean",
                     nodes=[dict(id="still", shape="cube", material="matte", position=[-2, 1.5, 0])],
                     screens=[dict(id="screen", asset_id=VIDEO, position=[0, 4, 0], width=4, frame="none")],
                     camera=[dict(at=0, azimuth=-90, elevation=15, distance=14)]))
    for name, overrides in (
        ("legacy", {}),
        ("explicit-legacy", {"floor_grid": True,
                             "post_overrides": {"bloom_weight": 0.35, "glow_intensity": 0.75}}),
        ("bloom-off", {"post_overrides": {"bloom_weight": 0}}),
        ("glow-off", {"post_overrides": {"glow_intensity": 0}}),
        ("adjusted", {"post_overrides": {"bloom_weight": 0.2, "glow_intensity": 0.4}}),
        ("no-floor", {"floor": False, "floor_grid": False}),
        ("space", {"environment": "deep_space", "floor_grid": False}),
        ("cinematic-off", {"post": "cinematic",
                           "post_overrides": {"bloom_weight": 0, "glow_intensity": 0}}),
    ):
        scene = Scene.model_validate({**simple, "scene3d": {**simple["scene3d"], **overrides}})
        if name == "explicit-legacy":
            scene.scene3d.nodes[0].entrance = "pop"
            scene.scene3d.nodes[0].idle_motion = "bob"
            scene.scene3d.screens[0].entrance = "scale"
        backend.author(scene, tmp_path / name, resources)
    # Test absent fields, not only Pydantic's normalized nulls.
    source = tmp_path / "legacy"
    omitted = tmp_path / "omitted"
    shutil.copytree(source, omitted)
    html = (omitted / "index.html").read_text()
    for key in ("entrance", "idle_motion", "color", "floor_grid", "post_overrides"):
        html = html.replace(f'"{key}":null,', "").replace(f',"{key}":null', "")
    (omitted / "index.html").write_text(html)
    plain = Scene.model_validate({**simple, "scene3d": {
        **simple["scene3d"], "floor_grid": False,
        "post_overrides": {"bloom_weight": 0, "glow_intensity": 0},
        "nodes": [
            dict(id="still", shape="cube", material="matte", role="tool", color="#d83a20",
                 position=[-2, 1.5, 0], entrance="none", idle_motion="none"),
            dict(id="spin", shape="cube", material="gold", color="#2040e0", ring=False,
                 position=[2, 1.5, 0], entrance="none", idle_motion="none", spin=45),
            dict(id="late", shape="sphere", ring=False, position=[0, 1, 0],
                 appear_at=1, entrance="none", idle_motion="none"),
        ],
        "screens": [
            dict(id="screen", asset_id=VIDEO, position=[0, 4, 0], width=4, frame="none", entrance="none"),
            dict(id="late_screen", asset_id=VIDEO, position=[0, 4, 1], width=4, frame="none",
                 appear_at=1, entrance="none"),
        ],
    }})
    backend.author(plain, tmp_path / "plain", resources)
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "fixtures/style_controls.cjs"),
         str(backend.root), str(tmp_path)], capture_output=True, text=True, timeout=240,
    )
    (tmp_path / "browser.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "browser-report.json").read_text())
    assert report["babylon"] == "9.28.0"
    assert not report["errors"]
    assert report["checks"] >= 20
    # Real decoded quadrant pixels, not merely a full-size but blank screen transform.
    with Image.open(tmp_path / "plain-frame0.png") as image:
        pixels = image.convert("RGB").crop((0, 0, 1280, 360))
        counts = {"red": 0, "green": 0, "blue": 0}
        for y in range(0, pixels.height, 2):
            for x in range(0, pixels.width, 2):
                r, g, b = pixels.getpixel((x, y))
                if r > 150 and g < 90 and b < 90:
                    counts["red"] += 1
                if g > 150 and r < 90 and b < 90:
                    counts["green"] += 1
                if b > 150 and r < 90 and g < 110:
                    counts["blue"] += 1
        assert min(counts.values()) > 500, counts
    # Removing a pass affects actual pixels, not just the pipeline's boolean.
    with Image.open(tmp_path / "legacy-frame1.2.png") as original:
        for name in ("bloom-off", "glow-off"):
            with Image.open(tmp_path / f"{name}-frame1.2.png") as changed:
                assert ImageChops.difference(original.convert("RGB"), changed.convert("RGB")).getbbox()