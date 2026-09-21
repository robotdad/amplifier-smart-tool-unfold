"""Duration expectations are frame counts, not renderer-reported file existence."""

import json
import os
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from unfold import Brief, Unfold, UnfoldError
from unfold.models import Scene
from unfold.store import uid


def bumper(duration):
    return dict(title="Short signature", duration=duration, explanation="Mark settles then holds",
                background="#08131f", elements=[dict(id="mark", kind="path", x=100, y=100,
                width=200, height=200, points=[(20,20),(180,20),(180,180),(20,180)],
                closed=True, color="#f0c76e", fill="#f0c76e", fill_opacity=1, opacity=1)],
                tweens=[dict(target="mark", at=0, duration=min(0.4, max(0, duration-1/30)),
                             x=20, opacity=1, ease="none")])


@pytest.mark.parametrize(("requested", "frames"), [
    (1/30, 1), (0.05, 2), (2, 60), (2.5, 75), (2.51, 75), (2.52, 76), (20, 600), (60, 1800),
])
def test_canonical_duration_is_shared_by_brief_scene_and_roundtrip(requested, frames):
    brief = Brief(title="Bumper", intent="Short signature", duration=requested)
    scene = Scene.model_validate(bumper(requested))
    assert brief.duration == scene.duration == frames / 30
    assert Brief.model_validate_json(brief.model_dump_json()).duration == frames / 30
    assert Scene.model_validate_json(scene.model_dump_json()).duration == frames / 30
    assert Brief(title="Default", intent="Still twenty seconds").duration == 20


@pytest.mark.parametrize("duration", [0, -1, 0.02, 60.001, float("nan"), float("inf")])
def test_invalid_duration_is_rejected_before_rounding(duration):
    with pytest.raises(ValueError):
        Brief(title="Invalid", intent="Invalid timing", duration=duration)
    with pytest.raises(ValueError):
        Scene.model_validate(bumper(duration))


@pytest.mark.parametrize(("at", "length"), [(2.5, 0), (2.6, 0), (2.4, 0.2), (2.49, 0)])
def test_tween_and_camera_cannot_start_at_end_or_overrun(at, length):
    data = bumper(2.5)
    data["tweens"] = [dict(target="mark", at=at, duration=length, opacity=1)]
    with pytest.raises(ValueError, match="timing"):
        Scene.model_validate(data)
    data = bumper(2.5)
    data["camera"] = [dict(at=at, duration=length, center_x=640, center_y=360, zoom=1)]
    with pytest.raises(ValueError, match="Camera moves"):
        Scene.model_validate(data)
    # Ordinary decimal arithmetic must not create a false overrun.
    data = bumper(0.3)
    data["tweens"] = [dict(target="mark", at=0.1, duration=0.2, opacity=1)]
    Scene.model_validate(data)


def seed_revision(library, scene):
    project, revision = uid(), uid()
    source = library.store.workspace(uid()) / "source"
    source_hash = library.backend.author(scene, source)
    library.store.put("project", dict(id=project, kind="project", name="Signature",
                      current_revision=revision, revisions=[revision]))
    library.store.put("revision", dict(id=revision, kind="revision", project_id=project,
                      brief=dict(title=scene.title, intent="Fixture", duration=scene.duration),
                      source=str(source.relative_to(library.store.root)), source_sha256=source_hash,
                      artifacts=[]))
    return revision


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
@pytest.mark.parametrize(("duration", "frames"), [(1/30, 1), (2, 60), (2.5, 75), (2.52, 76)])
def test_short_retained_video_overlay_delivery_and_handoff(tmp_path, duration, frames):
    library = Unfold(tmp_path / "library", os.environ["UNFOLD_TEST_BACKEND"])
    revision = seed_revision(library, Scene.model_validate(bumper(duration)))
    assert library.inspect(revision)["brief"]["duration"] == frames / 30
    artifact = library.render(revision)
    assert artifact["duration"] == frames / 30 and artifact["frame_count"] == frames
    samples = library.sample_output(artifact["id"], [0, frames / 30 - 0.00001])["frames"]
    assert samples[-1]["time"] == (frames - 1) / 30
    for sample in samples:
        pixel = Image.open(sample["path"]).convert("RGB").getpixel((200,200))
        assert pixel[0] > 200 and pixel[1] > 150 and pixel[2] < 150
    evidence = library.backend.frames(artifact["path"], [frames / 30 - 0.00001], tmp_path / "samples")
    assert evidence[0]["time"] == (frames - 1) / 30
    with pytest.raises(UnfoldError):
        library.sample_output(artifact["id"], [frames / 30])
    library.save_review_view(revision, at=(frames - 1) / 30)
    with pytest.raises(UnfoldError):
        library.save_review_view(revision, at=frames / 30 + 0.01)
    delivery = library.configure_delivery(revision)
    assert delivery["duration"] == frames / 30
    for mode in ("overlay", "video"):
        output = library.render_delivery(delivery["id"], mode=mode)
        assert output["duration"] == frames / 30 and output["frame_count"] == frames
        observed = library.backend.probe(output["path"], alpha=mode == "overlay")
        assert observed["frame_count"] == frames and observed["duration"] == frames / 30
        if mode == "overlay":
            rgba = Image.open(library.sample_output(output["id"], [0])["frames"][0]["path"]).convert("RGBA")
            assert rgba.getpixel((10,10))[3] == 0
            assert rgba.getpixel((200,200))[3] > 250
        handoff = library.export_handoff(output["id"], tmp_path / f"{mode}.zip")
        with zipfile.ZipFile(handoff["path"]) as pack:
            manifest = json.loads(pack.read("manifest.json"))
            assert manifest["timing"]["duration"] == frames / 30
            assert pack.read(manifest["output"]) == Path(output["path"]).read_bytes()


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"), reason="Needs renderer")
def test_existing_off_grid_longer_source_is_not_silently_rewritten(tmp_path):
    library = Unfold(tmp_path / "library", os.environ["UNFOLD_TEST_BACKEND"])
    old = Scene.model_validate(bumper(5.01), context={"retained_source": True})
    revision = seed_revision(library, old)
    source = Path(library.inspect(revision)["source_path"]) / "scene.json"
    before = source.read_bytes()
    output = library.render(revision)
    assert output["frame_count"] == 151
    assert source.read_bytes() == before
    assert library.inspect(revision)["brief"]["duration"] == 5.01


def test_cli_and_installed_schema_accept_fractional_briefs(tmp_path, monkeypatch, capsys):
    import sys

    from unfold import Grant, cli
    from unfold.help import schemas

    assert schemas()["Brief"]["properties"]["duration"]["minimum"] == 1/30
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps(dict(title="Bumper", intent="Signature", duration=2.52)))
    grant = tmp_path / "grant.json"
    grant.write_text(Grant(provider="openai", model="fixture").model_dump_json())
    monkeypatch.setattr(Unfold, "create", lambda self, brief, grant, request_id: brief.model_dump())
    monkeypatch.setattr(sys, "argv", ["unfold", "--library", str(tmp_path / "library"),
                        "create", "--brief", str(brief), "--grant", str(grant)])
    cli.main()
    assert json.loads(capsys.readouterr().out)["duration"] == 76/30


def test_mcp_creation_stores_canonical_duration_without_launching(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from mcp import Client

    from unfold import Grant
    from unfold.mcp import create_server

    library = Unfold(tmp_path)
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.setattr(library, "_launch_review_job", lambda identity: library.store.get(identity))

    async def run():
        async with Client(create_server(library, allow_models=True)) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools.tools if t.name == "unfold_submit_creation")
            assert tool.input_schema["$defs"]["Brief"]["properties"]["duration"]["minimum"] == 1/30
            result = await client.call_tool("unfold_submit_creation", {
                "brief": dict(title="Signature", intent="Short bumper", duration=2.52),
                "grant": Grant(provider="openai", model="fixture", allow_context=True,
                               allow_frames=True, vision=True).model_dump(),
                "request_id": uid()})
            assert not result.is_error
            assert result.structured_content["result"]["brief"]["duration"] == 76/30
            assert library.store.list("review_job")[0]["brief"]["duration"] == 76/30

    asyncio.run(run())


def test_one_frame_reference_sampling_stays_in_range(tmp_path, monkeypatch):
    from unfold import Grant
    from unfold.intelligence import Production
    from unfold.store import digest

    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"fixture only")
    times = []

    def decode(argv, **kwargs):
        times.append(float(argv[argv.index("-ss") + 1]))
        Path(argv[-1]).write_bytes(b"scripted frame")

    monkeypatch.setattr("unfold.backend.run", decode)
    owner = Production(dict(library=str(tmp_path / "library"), backend=str(tmp_path),
        operation_id=uid(), brief={"duration": 1/30},
        grant=Grant(provider="openai", model="fixture").model_dump(),
        reference=dict(path=str(reference), sha256=digest(reference), start=0, id=uid())))
    assert times == [0]
    assert owner.reference_evidence["composition_times"] == [0]


def test_authoring_and_revision_preserve_the_canonical_short_duration(tmp_path, monkeypatch):
    from unfold import Grant
    from unfold.intelligence import Production

    brief = Brief(title="Bumper", intent="Short signature", duration=2.52)
    scene = Scene.model_validate(bumper(2.52))
    owner = Production(dict(library=str(tmp_path / "library"), backend=str(tmp_path),
        operation_id=uid(), brief=brief.model_dump(),
        grant=Grant(provider="openai", model="fixture").model_dump()))
    owner.store.put("operation", dict(id=owner.request["operation_id"], status="running"))
    monkeypatch.setattr(owner.backend, "require", lambda: None)
    owner.backend.gsap = tmp_path / "gsap.js"
    owner.backend.gsap.write_text("// fixture")
    owner.call("author", scene.model_dump_json())
    owner.call("patch", json.dumps({"background": "#ffffff"}))
    retained = json.loads((owner.directory / "source" / "scene.json").read_text())
    assert retained["duration"] == brief.duration == 76/30
    assert retained["tweens"] == scene.model_dump()["tweens"]
    wrong = bumper(2)
    with pytest.raises(ValueError, match="Preserve the requested duration"):
        owner.call("author", json.dumps(wrong))
    assert owner.scene.duration == 76/30
    # A library revision carries the same brief to its creative operation boundary.
    library = Unfold(owner.store.root, tmp_path)
    library.backend = owner.backend
    revision = seed_revision(library, owner.scene)
    monkeypatch.setattr(library, "_produce", lambda brief, *args, **kwargs: brief)
    assert library.revise(revision, "Keep timing, change color", owner.grant).duration == 76/30
