"""Presence preservation ONLY for the additive Scene3D.background field."""
import json
from pathlib import Path

import pytest

from unfold.backend import Backend
from unfold.intelligence import Production
from unfold.models import Scene, Scene3D, UnfoldError
from unfold.store import uid


def data(presence="absent"):
    layer = {"nodes": [{"id": "n", "position": [0, 1, 0]}], "camera": [{"at": 0}]}
    if presence != "absent":
        layer["background"] = presence
    return {"title": "Legacy", "explanation": "Presence fixture", "duration": 1,
            "scene3d": layer, "output": {"resolution": "720p"}}


@pytest.fixture
def backend(tmp_path, monkeypatch):
    b = Backend(tmp_path/"backend")
    monkeypatch.setattr(b, "require", lambda: None)
    b.gsap = tmp_path/"gsap.js"
    b.babylon = tmp_path/"babylon.js"
    b.gsap.write_text("// pinned fixture")
    b.babylon.write_text("// pinned fixture")
    return b


@pytest.mark.parametrize("presence", ["absent", None, "environment", "transparent"])
def test_raw_nested_json_and_copy_roundtrips(presence):
    scene = Scene.model_validate(data(presence))
    for _ in range(3):
        for dump in (scene.model_dump(), scene.model_dump(mode="json"), json.loads(scene.model_dump_json())):
            assert ("background" in dump["scene3d"]) == (presence != "absent")
            if presence != "absent":
                assert dump["scene3d"]["background"] == presence
            # Unrelated defaults/nulls still serialize, including node media and floor_grid.
            assert dump["scene3d"]["floor_grid"] is None
            assert dump["scene3d"]["post_overrides"] is None
            assert dump["scene3d"]["nodes"][0]["media"] is None
        scene = Scene.model_validate(scene.model_copy(deep=True).model_dump())
        assert ("background" in scene.scene3d.model_fields_set) == (presence != "absent")


def test_explicit_assignment_and_copy_override_are_not_lost():
    layer = Scene3D.model_validate(data()["scene3d"])
    assert "background" not in layer.model_dump()
    assert layer.model_copy(update={"background": None}).model_dump()["background"] is None
    assert layer.model_copy(update={"background": "transparent"}).model_dump()["background"] == "transparent"
    layer.background = None
    assert "background" in layer.model_dump()
    layer.background = "environment"
    assert layer.model_dump()["background"] == "environment"
    assert layer.model_dump(exclude={"background"}).get("background") is None


@pytest.mark.parametrize("presence", ["absent", None, "environment", "transparent"])
def test_author_canonicalization_and_reopen_preserve_presence(backend, tmp_path, presence):
    scene = Scene.model_validate(data(presence))
    backend.author(scene, tmp_path/"first")
    saved = json.loads((tmp_path/"first/scene.json").read_text())
    assert ("background" in saved["scene3d"]) == (presence != "absent")
    assert isinstance(saved["scene3d"]["nodes"][0]["position"][0], float)
    html = (tmp_path/"first/index.html").read_text()
    embedded = json.loads(html.split('id="unfold-scene3d">')[1].split("</script>")[0])
    assert embedded["scene3d"] == saved["scene3d"]
    reopened = Scene.model_validate_json((tmp_path/"first/scene.json").read_text(), context={"retained_source": True})
    backend.author(reopened, tmp_path/"second")
    for f in (tmp_path/"first").rglob("*"):
        if f.is_file():
            assert f.read_bytes() == (tmp_path/"second"/f.relative_to(tmp_path/"first")).read_bytes()


@pytest.mark.parametrize("presence", ["absent", None, "environment", "transparent"])
def test_real_production_author_inspect_and_patch_preserve_presence(backend, tmp_path, presence):
    owner = Production({
        "operation_id": uid(), "library": str(tmp_path/"library"), "backend": str(backend.root),
        "brief": {"duration": 1, "output": {"resolution": "720p"}},
        "grant": {"provider": "gemini", "model": "offline", "allow_context": True,
                  "allow_frames": True, "vision": True},
    })
    owner.backend = backend
    owner.store.put("operation", {"id": owner.request["operation_id"], "kind": "operation", "status": "running"})
    assert owner.call("author", json.dumps(data(presence)))["authored"]
    for payload in ({}, {"title": "Changed title only"}):
        owner.call("patch", json.dumps(payload))
        inspected = owner.call("inspect", "{}")["scene"]
        saved = json.loads((owner.directory/"source/scene.json").read_text())
        assert ("background" in inspected["scene3d"]) == (presence != "absent")
        assert saved["scene3d"] == json.loads(json.dumps(inspected["scene3d"]))
    assert owner.renders == 0


@pytest.mark.parametrize("tamper", ["index.html", "scene3d_runtime.js", "babylon.js", "scene.json",
                                  "layer_player.js"])
def test_source_changed_guard_not_weakened(backend, tmp_path, tamper, monkeypatch):
    scene = Scene.model_validate(data("transparent" if tamper == "layer_player.js" else "absent"))
    source = tmp_path/"source"
    backend.author(scene, source)
    if tamper == "scene.json":
        d = json.loads((source/tamper).read_text())
        d["scene3d"]["background"] = None  # semantically same, still a changed byte representation
        (source/tamper).write_text(json.dumps(d))
    else:
        with (source/tamper).open("a") as f:
            f.write("\n// altered")
    # Even a recomputed aggregate doesn't bypass regeneration of executable bytes.
    assert backend.source_hash(source)
    monkeypatch.setattr(backend, "_run_cli", lambda *a, **kw: pytest.fail("Tampered source executed"))
    with pytest.raises(UnfoldError) as caught:
        backend.render(source, tmp_path/"never.mp4")
    assert caught.value.code == "SOURCE_CHANGED"


def test_pre_field_html_oracle_omits_only_background(backend, tmp_path):
    # Construct the older serialized vocabulary explicitly; expected embedded
    # bytes retain every other default, rather than exclude_none/exclude_unset.
    scene = Scene.model_validate(data(None))
    expected = scene.model_dump()
    del expected["scene3d"]["background"]
    old = Scene.model_validate(expected)
    backend.author(old, tmp_path/"source")
    canonical = Scene.model_validate(scene.model_dump(), context={"retained_source": True}).model_dump(mode="json")
    del canonical["scene3d"]["background"]
    assert json.loads((tmp_path/"source/scene.json").read_text()) == canonical
    embedded = json.dumps({"width": 1280, "height": 720, "duration": 1.0, "fps": 30,
                          "transparent": False, "scene3d": canonical["scene3d"], "screens": {}},
                         separators=(",", ":")).replace("<", "\\u003c")
    assert f'id="unfold-scene3d">{embedded}</script>' in (tmp_path/"source/index.html").read_text()


def test_public_render_admission_keeps_original_source_and_history(backend, tmp_path, monkeypatch):
    from unfold import Unfold
    from unfold.store import digest

    tool = Unfold(tmp_path/"library")
    tool.backend = backend
    source = tool.store.workspace(uid())/"source"
    backend.author(Scene.model_validate(data()), source)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    project, revision, artifact = uid(), uid(), uid()
    video = source.parent/"video.mp4"
    video.write_bytes(b"fixture, not playable media")
    tool.store.put("project", {"id": project, "kind": "project", "name": "Original",
                              "current_revision": revision, "revisions": [revision]})
    tool.store.put("revision", {"id": revision, "kind": "revision", "project_id": project,
        "source": str(source.relative_to(tool.store.root)), "source_sha256": backend.source_hash(source),
        "artifacts": [artifact]})
    tool.store.put("artifact", {"id": artifact, "kind": "artifact", "revision_id": revision,
        "relative_path": str(video.relative_to(tool.store.root)), "name": "Retained", "sha256": digest(video)})
    before = tool.store.get(project)
    class CaptureReached(Exception):
        pass
    def capture(args, **kw):
        assert args[0] == "render"
        assert Path(args[1]) != source
        assert (Path(args[1])/"index.html").read_bytes() == original["index.html"]
        raise CaptureReached()
    monkeypatch.setattr(backend, "_run_cli", capture)
    with pytest.raises(CaptureReached):
        tool.render(revision)
    assert tool.store.get(project) == before
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original
    assert tool.inspect(revision)["source_integrity"] == "intact"