"""Native pixels, retained compatibility and request identity, without provider calls."""

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from unfold import Brief, Grant, OutputSettings, Unfold, UnfoldError
from unfold.backend import Backend, run
from unfold.intelligence import Production
from unfold.models import Element, Scene, retained_brief
from unfold.store import digest, uid


def detail_scene(resolution="1080p"):
    width, height = OutputSettings(resolution=resolution).dimensions
    x, y = width - 220, height - 230
    return Scene.model_validate(dict(
        title="Native pixel detail", duration=1/30, background="#000000",
        output={"resolution": resolution}, explanation="One-pixel vector stripes at native coordinates",
        elements=[dict(id=f"stripe{i}", kind="line", x=x+2*i, y=y, width=1, height=80,
                       fill="#ffffff", opacity=1) for i in range(32)],
        tweens=[dict(target="stripe0", at=0, duration=0, opacity=1)],
        camera=[dict(at=0, duration=0, center_x=width/2, center_y=height/2, zoom=1)],
    ))


def seed(library, scene, *, legacy=False, resources=None):
    project, revision = uid(), uid()
    source = library.store.workspace(uid()) / "source"
    library.backend.author(scene, source, resources)
    brief = Brief(title=scene.title, intent="Deterministic fixture", duration=scene.duration,
                  output=scene.output).model_dump()
    if legacy:
        data = json.loads((source / "scene.json").read_text())
        data.pop("output")
        (source / "scene.json").write_text(json.dumps(data))
        brief.pop("output")
    library.store.put("project", dict(id=project, kind="project", name=scene.title,
                      current_revision=revision, revisions=[revision]))
    library.store.put("revision", dict(id=revision, kind="revision", project_id=project,
                      brief=brief, source=str(source.relative_to(library.store.root)),
                      source_sha256=library.backend.source_hash(source), artifacts=[]))
    return revision


def test_defaults_and_retained_interpretation():
    assert Brief(title="New", intent="Native").output.dimensions == (1920, 1080)
    assert retained_brief(dict(title="Old", intent="Saved")).output.dimensions == (1280, 720)
    old = detail_scene("720p").model_dump()
    old.pop("output")
    assert Scene.model_validate(old).output.dimensions == (1280, 720)


@pytest.mark.parametrize("output", [None, "1080p", 1080, {"resolution": "4k"},
                                    {"width": 1920}, {"resolution": 1080},
                                    {"resolution": True}, {"resolution": "1080p", "fps": 60}])
def test_malformed_settings(output):
    with pytest.raises(ValueError):
        Brief(title="Invalid", intent="Invalid", output=output)
    with pytest.raises(ValueError):
        Scene.model_validate({**detail_scene().model_dump(), "output": output})


def test_native_bounds_and_camera_source(tmp_path, monkeypatch):
    scene = detail_scene()
    backend = Backend(tmp_path)
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.gsap.write_text("// fixture")
    backend.author(scene, tmp_path / "source")
    html = (tmp_path / "source/index.html").read_text()
    assert 'data-width="1920" data-height="1080"' in html
    assert 'width:1920px;height:1080px' in html
    assert 'left:1700.0px;top:850.0px' in html
    assert '"x": 0.0, "y": 0.0, "scale": 1.0' in html
    with pytest.raises(ValueError, match="canvas"):
        Scene.model_validate({**scene.model_dump(), "output": {"resolution": "720p"}})
    for field, changes in [("elements", {"x": 1920}), ("camera", {"center_x": 1921}),
                           ("tweens", {"x": 1921})]:
        data = scene.model_dump()
        data[field][0].update(changes)
        with pytest.raises(ValueError):
            Scene.model_validate(data)


def test_model_cannot_change_resolution_and_patch_preserves_it(tmp_path, monkeypatch):
    scene = detail_scene()
    owner = Production(dict(library=str(tmp_path / "library"), backend=str(tmp_path),
        operation_id=uid(), brief=Brief(title="Native", intent="Native", duration=1/30).model_dump(),
        grant=Grant(provider="openai", model="fixture").model_dump()))
    owner.store.put("operation", dict(id=owner.request["operation_id"], status="running"))
    monkeypatch.setattr(owner.backend, "require", lambda: None)
    owner.backend.gsap = tmp_path / "gsap.js"
    owner.backend.gsap.write_text("// fixture")
    owner.call("author", scene.model_dump_json())
    owner.call("patch", json.dumps({"background": "#ffffff"}))
    assert owner.scene.output == scene.output
    with pytest.raises(ValueError, match="output settings"):
        owner.call("author", detail_scene("720p").model_dump_json())
    assert owner.scene.output == scene.output


@pytest.mark.parametrize("hashed", [False, True])
def test_old_direct_retry_keeps_original_identity_and_rejects_changed_settings(tmp_path, hashed):
    library = Unfold(tmp_path, tmp_path / "absent-backend")
    brief = Brief(title="Old request", intent="Do not replay")
    grant = Grant(provider="openai", model="fixture")
    legacy = Brief.model_validate(brief.model_dump()).model_dump(exclude={"output"})
    payload = dict(brief=legacy, grant=grant.model_dump(), base=None, feedback="", feedback_target=None)
    old = dict(id=uid(), kind="operation", status="interrupted", **payload)
    if hashed:
        old["input_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    library.store.put("operation", old)
    for candidate in [legacy, Brief.model_validate(legacy)]:
        assert library.create(candidate, grant, request_id=old["id"]) == old
    for resolution in ["720p", "1080p"]:
        with pytest.raises(UnfoldError, match="different input"):
            library.create({**legacy, "output": {"resolution": resolution}}, grant, request_id=old["id"])
    assert library.store.get(old["id"]) == old


def test_old_async_retry_and_new_creation_settings(tmp_path, monkeypatch):
    library = Unfold(tmp_path)
    brief = Brief(title="Old job", intent="Do not replay")
    grant = Grant(provider="openai", model="fixture", allow_context=True, allow_frames=True, vision=True)
    payload = dict(brief=brief.model_dump(exclude={"output"}), grant=grant.model_dump())
    old = dict(id=uid(), kind="review_job", mode="create", status="interrupted", **payload,
               fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    library.store.put("review_job", old)
    assert library.submit_creation(payload["brief"], grant, old["id"]) == old
    with pytest.raises(UnfoldError, match="different creation input"):
        library.submit_creation(brief.model_dump(), grant, old["id"])
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.setattr(library, "_launch_review_job", lambda identity: library.store.get(identity))
    job = library.submit_creation(brief, grant, uid())
    assert job["brief"]["output"] == {"resolution": "1080p"}
    assert library.submit_creation(brief, grant, job["id"]) == job
    with pytest.raises(UnfoldError):
        library.submit_creation({**job["brief"], "output": {"resolution": "720p"}}, grant, job["id"])


def test_legacy_revision_preserves_resolution(tmp_path, monkeypatch):
    library = Unfold(tmp_path)
    monkeypatch.setattr(library.backend, "require", lambda: None)
    library.backend.gsap = tmp_path / "gsap.js"
    library.backend.gsap.write_text("// fixture")
    revision = seed(library, detail_scene("720p"), legacy=True)
    monkeypatch.setattr(library, "_produce", lambda brief, *args, **kwargs: brief)
    assert library.revise(revision, "Keep size", Grant(provider="openai", model="fixture")).output.resolution == "720p"
    assert "output" not in library.inspect(revision)["brief"]


def test_queued_legacy_creation_keeps_720p(tmp_path, monkeypatch):
    library = Unfold(tmp_path)
    job = dict(id=uid(), kind="review_job", mode="create", status="queued", operation_id=uid(),
               brief=Brief(title="Queued", intent="Saved").model_dump(exclude={"output"}),
               grant=Grant(provider="openai", model="fixture").model_dump())
    library.store.put("review_job", job)
    captured = []

    def create(brief, *args, **kwargs):
        captured.append(brief)
        return {"status": "completed"}

    monkeypatch.setattr(library, "create", create)
    assert library.run_review_job(job["id"])["status"] == "completed"
    assert captured[0].output.resolution == "720p"


@pytest.mark.parametrize("resolution", ["720p", "1080p"])
def test_cli_and_library_share_output_settings(tmp_path, monkeypatch, capsys, resolution):
    import sys

    from unfold import cli
    from unfold.help import manifest, schemas

    assert schemas()["Brief"]["$defs"]["OutputSettings"]["properties"]["resolution"]["enum"] == ["720p", "1080p"]
    assert manifest()["output_settings"]["new_creation_default"] == "1080p"
    brief = Brief(title="CLI native", intent="Pixels", output={"resolution": resolution})
    path = tmp_path / "brief.json"
    path.write_text(brief.model_dump_json())
    grant = tmp_path / "grant.json"
    grant.write_text(Grant(provider="openai", model="fixture").model_dump_json())
    monkeypatch.setattr(Unfold, "create", lambda self, brief, *args, **kwargs: brief.model_dump())
    monkeypatch.setattr(sys, "argv", ["unfold", "--library", str(tmp_path / "library"),
                        "create", "--brief", str(path), "--grant", str(grant)])
    cli.main()
    assert json.loads(capsys.readouterr().out)["output"] == brief.output.model_dump()


def test_renderer_rejects_wrong_native_dimensions(tmp_path, monkeypatch):
    backend = Backend(tmp_path)
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.gsap.write_text("// fixture")
    source = tmp_path / "source"
    backend.author(detail_scene(), source)
    monkeypatch.setattr(backend, "_run_cli", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend, "probe", lambda *args, **kwargs: {
        "width": 1280, "height": 720, "frame_count": 1, "fps": "30/1",
        "duration": 1/30, "encoded_duration": 1/30})
    with pytest.raises(UnfoldError, match="dimensions"):
        backend.render(source, tmp_path / "wrong.mp4")


@pytest.mark.parametrize("wrong", ["source", "media", None])
def test_commit_rechecks_requested_source_and_measured_dimensions(tmp_path, monkeypatch, wrong):
    library = Unfold(tmp_path)
    monkeypatch.setattr(library.backend, "require", lambda: None)
    library.backend.gsap = tmp_path / "gsap.js"
    library.backend.gsap.write_text("// fixture")
    operation, project = uid(), uid()
    directory = library.store.workspace(operation)
    scene = detail_scene("720p" if wrong == "source" else "1080p")
    source_hash = library.backend.author(scene, directory / "source")
    (directory / "video.mp4").write_bytes(b"scripted media; probe is mocked")
    library.store.put("project", dict(id=project, kind="project", name="Commit check",
                                      current_revision=None, revisions=[]))
    library.store.put("operation", dict(id=operation, kind="operation", status="running",
        project_id=project, base=None, feedback="", feedback_target=None,
        brief=Brief(title="Commit", intent="Native").model_dump()))
    (directory / "result.json").write_text(json.dumps(dict(
        source_sha256=source_hash,
        render={"sha256": digest(directory / "video.mp4"), "width": 99, "height": 99})))
    width, height = (1280, 720) if wrong == "media" else (1920, 1080)
    monkeypatch.setattr(library.backend, "probe", lambda *args: {"width": width, "height": height})
    if wrong:
        with pytest.raises(UnfoldError, match="dimensions"):
            library._complete_operation(operation)
        assert not library.store.list("revision")
    else:
        result = library._complete_operation(operation)
        revision = library.inspect(result["revision_id"])
        artifact = library.artifact(revision["artifacts"][0])
        assert (artifact["width"], artifact["height"]) == (1920, 1080)


def test_mcp_typed_creation_and_legacy_retry_preserve_settings(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from mcp import Client

    from unfold.mcp import create_server

    library = Unfold(tmp_path)
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.setattr(library, "_launch_review_job", lambda identity: library.store.get(identity))
    grant = Grant(provider="openai", model="fixture", allow_context=True, allow_frames=True, vision=True)
    legacy = Brief(title="Legacy MCP", intent="No replay").model_dump(exclude={"output"})
    payload = dict(brief=legacy, grant=grant.model_dump())
    old = dict(id=uid(), kind="review_job", mode="create", status="interrupted", **payload,
               fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    library.store.put("review_job", old)

    async def check():
        async with Client(create_server(library, allow_models=True)) as client:
            retry = await client.call_tool("unfold_submit_creation", {**payload, "request_id": old["id"]})
            # Interrupted retained work stays an error outcome, not a failed retry.
            assert retry.is_error
            assert retry.structured_content["result"]["id"] == old["id"]
            assert retry.structured_content["result"]["status"] == "interrupted"
            for resolution in ["720p", "1080p"]:
                result = await client.call_tool("unfold_submit_creation", {
                    "brief": {**legacy, "output": {"resolution": resolution}},
                    "grant": grant.model_dump(), "request_id": uid()})
                assert not result.is_error
                assert result.structured_content["result"]["brief"]["output"] == {"resolution": resolution}

    asyncio.run(check())


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
@pytest.mark.parametrize("resolution", ["720p", "1080p"])
def test_native_detail_rerender_alpha_composite_and_handoff(tmp_path, resolution):
    library = Unfold(tmp_path / "library", os.environ["UNFOLD_TEST_BACKEND"])
    scene = detail_scene(resolution)
    width, height = scene.output.dimensions
    font = library.import_asset(Path(__file__).parent / "fixtures/fonts/IBMPlexSerif-Regular.ttf",
                                role="font", rights="redistributable", attribution="IBM Plex OFL")
    scene.elements.append(Element(id="native_text", kind="text", x=width-520, y=height-420,
                                  width=500, height=90, text="Native detail", font_size=48,
                                  font_asset_id=font["id"], opacity=1))
    revision = seed(library, scene, legacy=resolution == "720p", resources={font["id"]: font})
    source = Path(library.inspect(revision)["source_path"])
    source_before = {p.name: digest(p) for p in source.iterdir() if p.is_file()}
    artifact = library.render(revision)
    again = library.render(revision)
    assert artifact["sha256"] == again["sha256"]
    assert (artifact["width"], artifact["height"]) == (width, height)
    assert artifact["output"] == {"resolution": resolution}
    assert {p.name: digest(p) for p in source.iterdir() if p.is_file()} == source_before
    frame = Image.open(library.sample_output(artifact["id"], [0])["frames"][0]["path"]).convert("RGB")
    assert frame.size == (width, height)
    ink = frame.crop((width-520, height-420, width-20, height-330)).convert("L")
    assert ink.getextrema()[1] > 200
    x, y = width-220, height-220
    row = [frame.getpixel((x+i, y))[0] for i in range(64)]
    assert min(row[::2]) > 200 and max(row[1::2]) < 45
    if resolution == "1080p":
        # A 720p raster cannot retain these alternating native single-pixel details.
        upscaled = frame.resize((1280, 720), Image.Resampling.LANCZOS).resize(frame.size, Image.Resampling.LANCZOS)
        blurred = [upscaled.getpixel((x+i, y))[0] for i in range(8, 56)]
        assert max(blurred) - min(blurred) < 100
    reference = tmp_path / "green.mp4"
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x00ff00:s=640x360:r=30",
         "-frames:v", "1", "-c:v", "libx264", str(reference)])
    ref = library.import_asset(reference, role="video")
    delivery = library.configure_delivery(revision, reference_id=ref["id"])
    assert delivery["dimensions"] == [width, height]
    for mode in ("overlay", "video"):
        output = library.render_delivery(delivery["id"], mode=mode)
        assert (output["width"], output["height"]) == (width, height)
        run(["ffmpeg", "-v", "error", "-i", output["path"], "-f", "null", "-"])
        image = Image.open(library.sample_output(output["id"], [0])["frames"][0]["path"]).convert("RGBA")
        assert image.size == (width, height)
        if mode == "overlay":
            assert image.getpixel((10, 10))[3] == 0
            assert image.getpixel((x, y))[3] > 250
            assert image.getpixel((x+1, y))[3] < 5
        else:
            assert image.getpixel((10, 10))[1] > 245
            assert min(image.getpixel((x, y))[:3]) > 170
        handoff = library.export_handoff(output["id"], tmp_path / f"{mode}.zip")
        with zipfile.ZipFile(handoff["path"]) as pack:
            manifest = json.loads(pack.read("manifest.json"))
            assert manifest["timing"]["dimensions"] == [width, height]
            assert manifest["fonts"][0]["sha256"] == font["sha256"]
            assert hashlib.sha256(pack.read(manifest["output"])).hexdigest() == output["sha256"]
