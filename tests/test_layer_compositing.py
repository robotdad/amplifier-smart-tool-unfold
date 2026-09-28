"""3D alpha layer over preserved 2D source; real-pixel checks are renderer gated."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from unfold.backend import Backend, run
from unfold.models import Scene, Scene3D


def composition():
    return dict(title="Layer boundary", duration=1, explanation="Mechanical alpha and stacking fixture",
                background="#f6f4ee",
                elements=[dict(id="foreground", kind="card", x=620, y=350, width=40, height=60,
                               fill="#10f020", border="#10f020", radius=0, opacity=1)],
                tweens=[dict(target="foreground", at=0, duration=0, opacity=1)])


def layer(**changes):
    return dict(environment="lab_white", post="clean", floor=True, floor_grid=False,
                nodes=[dict(id="front", shape="sphere", material="matte", color="#f02010",
                            position=[0, 1.5, 0], size=2, ring=False, entrance="none", idle_motion="none"),
                       dict(id="back", shape="sphere", material="matte", color="#1020f0",
                            position=[0, 1.5, 2], size=2, ring=False, entrance="none", idle_motion="none")],
                camera=[dict(at=0, target="front", azimuth=-90, elevation=15, distance=10)],
                **changes)


def test_layer_background_validation_defaults_and_preservation():
    for value in (None, "environment", "transparent"):
        scene = Scene.model_validate({**composition(), "scene3d": layer(background=value)})
        assert scene.scene3d.background == value
        assert scene.background == "#f6f4ee"
        assert Scene.model_validate_json(scene.model_dump_json()) == scene
        assert scene.model_dump(exclude={"scene3d"}) == Scene.model_validate(composition()).model_dump(exclude={"scene3d"})
    assert Scene3D.model_validate(layer()).background is None
    for value in ("#f6f4ee", "solid", "url(file:///secret)", False):
        with pytest.raises(ValueError):
            Scene3D.model_validate(layer(background=value))


@pytest.mark.parametrize("whole,background,expected", [
    ("#f6f4ee", None, False), ("#f6f4ee", "environment", False),
    ("#f6f4ee", "transparent", True), ("transparent", None, True),
    ("transparent", "environment", True), ("transparent", "transparent", True),
])
def test_backend_emits_independent_layer_alpha_without_changing_css(tmp_path, monkeypatch, whole, background, expected):
    backend = Backend(tmp_path)
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.babylon = tmp_path / "babylon.js"
    backend.gsap.write_text("// author fixture")
    backend.babylon.write_text("// author fixture")
    scene = Scene.model_validate({**composition(), "background": whole,
                                 "scene3d": layer(background=background)})
    backend.author(scene, tmp_path / "source")
    document = (tmp_path / "source/index.html").read_text()
    encoded = document.split('id="unfold-scene3d">', 1)[1].split("</script>", 1)[0]
    assert json.loads(encoded)["transparent"] is expected
    assert f"background:{whole};overflow:hidden" in document
    saved = Scene.model_validate_json((tmp_path / "source/scene.json").read_text())
    assert saved == scene
    assert document.index('<canvas id="scene3d"') < document.index('<div id="foreground"')


needs_renderer = pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs pinned renderer/browser")


@needs_renderer
def test_real_layer_alpha_post_compositing_and_2d_occlusion(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    backend.author(Scene.model_validate(composition()), tmp_path / "baseline")
    cases = {
        "opaque": layer(),
        "environment": layer(background="environment"),
        "clear-clean": layer(background="transparent"),
        "clear-cinematic": {**layer(background="transparent"), "post": "cinematic"},
        "clear-neon": {**layer(background="transparent"), "post": "neon"},
        "clear-dreamy": {**layer(background="transparent"), "post": "dreamy"},
        "clear-noir": {**layer(background="transparent"), "post": "noir"},
        "clear-space": {**layer(background="transparent"), "environment": "deep_space"},
    }
    for name, data in cases.items():
        backend.author(Scene.model_validate({**composition(), "scene3d": data}), tmp_path / name)
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "fixtures/layer_compositing.cjs"),
         str(backend.root), str(tmp_path)], capture_output=True, text=True, timeout=300,
    )
    (tmp_path / "browser.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "browser-report.json").read_text())
    assert not report["errors"]
    with Image.open(tmp_path / "baseline.png") as baseline:
        baseline = baseline.convert("RGBA")
        for name in cases:
            with Image.open(tmp_path / f"{name}.png") as rendered, Image.open(tmp_path / f"{name}-canvas.png") as canvas:
                rendered, canvas = rendered.convert("RGBA"), canvas.convert("RGBA")
                if name in ("opaque", "environment"):
                    assert rendered.getpixel((20, 20)) != baseline.getpixel((20, 20))
                    assert canvas.getpixel((20, 20))[3] == 255
                    continue
                assert canvas.getpixel((20, 20)) == (0, 0, 0, 0)
                for point in ((20, 20), (640, 20), (640, 700)):
                    assert rendered.getpixel(point) == baseline.getpixel(point) == (246, 244, 238, 255)
                alpha_histogram = canvas.getchannel("A").histogram()
                assert alpha_histogram[255] > 1000 and sum(alpha_histogram[1:255]) > 50
                # Independent straight-alpha reference including fractional post/AA edges.
                expected = Image.alpha_composite(baseline, canvas)
                # The real 2D foreground must occlude 3D, not vice versa.
                expected.paste(baseline.crop((620, 350, 660, 410)), (620, 350))
                diff = ImageChops.difference(rendered.convert("RGB"), expected.convert("RGB"))
                assert max(v[1] for v in diff.getextrema()) <= 2, (name, diff.getextrema())
                assert rendered.getpixel((640, 360)) == (16, 240, 32, 255)
                if name == "clear-clean":
                    r, g, b, a = canvas.getpixel((640, 360))
                    assert a == 255 and r > b * 2, (r, g, b, a)  # front red object covers rear blue
                record = report["cases"][name]
                assert record["clear"] == [0, 0, 0, 0] and record["environment_ready"]
                assert not record["floor"] and not record["stars"] and not record["sky"]
                assert record["context"]["alpha"] and record["context"]["premultipliedAlpha"] is False


@needs_renderer
def test_real_encoded_background_and_alpha_delivery(tmp_path):
    backend = Backend(os.environ["UNFOLD_TEST_BACKEND"])
    base = Scene.model_validate(composition())
    transparent = Scene.model_validate({**composition(), "scene3d": layer(background="transparent")})
    # No color substitution: only scene3d is added to the typed source.
    assert transparent.model_dump(exclude={"scene3d"}) == base.model_dump(exclude={"scene3d"})
    for name, scene, alpha in (("baseline", base, False), ("mixed", transparent, False),
                               ("alpha", transparent.model_copy(update={"background": "transparent"}), True)):
        source = tmp_path / name
        backend.author(scene, source)
        output = tmp_path / (name + (".mov" if alpha else ".mp4"))
        metadata = backend.render(source, output, alpha=alpha)
        assert metadata["frame_count"] == 30 and metadata["width"] == 1280 and metadata["height"] == 720
        run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"])
        run(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1", "-pix_fmt", "rgba",
             str(tmp_path / f"{name}.png")])
    with Image.open(tmp_path / "baseline.png") as base_image, Image.open(tmp_path / "mixed.png") as mixed:
        for point in ((20, 20), (640, 20), (640, 700)):
            # YUV/chroma quantization: same literal plate is not exact RGB after separate encoding.
            assert max(abs(a-b) for a, b in zip(base_image.getpixel(point), mixed.getpixel(point))) <= 3
    with Image.open(tmp_path / "alpha.png") as image:
        assert image.getpixel((20, 20))[3] == 0
        assert image.getpixel((640, 360))[3] >= 254
        assert sum(image.getchannel("A").histogram()[1:254]) > 50