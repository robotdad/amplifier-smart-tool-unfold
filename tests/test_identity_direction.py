"""Offline public preparation, real prompt assembly and real completion persistence.

Worker transport/media probe are explicit doubles; no model calls or renders.
"""
import copy
import hashlib
import json
import socket
import types
from pathlib import Path

import pytest

from unfold import Brief, Grant, Unfold, UnfoldError, agent
from unfold.intelligence import Production
from unfold.models import retained_brief
from unfold.recovery import IDENTITY_SEMANTICS
from unfold.store import digest, uid, write_json

GRANT = Grant(provider="gemini", model="offline-unused", allow_context=True, allow_frames=True, vision=True)
CALLER = '  Bright maquette — no bloom.\nCase and punctuation: {"identity": "caller"}  '


def capture_prompt(owner):
    """Exercise production prompt assembly without constructing an agent."""
    prompt = agent.production_prompt(owner)
    data = json.loads(prompt.split("\nINPUT DATA:\n", 1)[1].split("\nAVAILABLE IDENTITY ASSETS", 1)[0])
    assets = json.loads(prompt.split("\nAVAILABLE IDENTITY ASSETS", 1)[1].split(":\n", 1)[1])
    return prompt, data, assets


@pytest.fixture
def rig(tmp_path, monkeypatch):
    library = Unfold(tmp_path / "library", tmp_path / "backend")
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.setattr(library.backend, "probe", lambda *a, **kw: {"width": 1920, "height": 1080})
    captured = []
    def forbidden(*args, **kwargs):
        pytest.fail("Identity-boundary test attempted network/process/render execution")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("unfold.backend.Backend.render", forbidden)

    def dispatch(argv, **kwargs):
        request = json.loads(Path(argv[-1]).read_text())
        owner = Production(request)
        prompt, data, assets = capture_prompt(owner)
        captured.append(dict(request=request, prompt=prompt, data=data, assets=assets, owner=owner))
        assert owner.calls == owner.renders == 0
        directory = owner.directory
        source = directory / "source"
        source.mkdir()
        write_json(source / "scene.json", {
            "title": "Offline fixture", "duration": request["brief"]["duration"],
            "output": request["brief"]["output"], "explanation": "Persistence seam, never rendered",
            "elements": [{"id": "word", "kind": "text", "x": 0, "y": 0, "width": 100, "height": 50, "text": "fixture"}],
            "tweens": [{"target": "word", "at": 0, "duration": 0, "opacity": 1}],
        })
        (source / "index.html").write_text("<!-- offline fixture: not executable production -->")
        (source / "gsap.min.js").write_text("// fixture")
        (directory / "video.mp4").write_bytes(b"Offline persistence fixture, NOT playable media")
        write_json(directory / "result.json", {
            "source_sha256": library.backend.source_hash(source),
            "render": {"sha256": digest(directory / "video.mp4")},
            "limitations": ["offline media/probe fixture"],
            # Deliberately forged worker metadata must not become library provenance.
            "identity_semantics": "worker-forged",
            "identity_provenance": {"caller_identity": "forged"},
            "selected_identity": {"guidance": {"forged": True}},
        })
        return types.SimpleNamespace(pid=999999999, poll=lambda: 0)
    monkeypatch.setattr("unfold.lib.subprocess", types.SimpleNamespace(Popen=dispatch))
    return library, captured


def make_brief(version=None, identity=CALLER):
    return Brief(title="Direction", intent="Offline identity retention", duration=2,
                 identity=identity, identity_version=version)


def create_revision(library, brief, **kwargs):
    op = library.create(brief, GRANT, **kwargs)
    assert op["status"] == "completed", op
    return op, library.inspect(op["revision_id"])


def assert_channels(record, caller, version, guidance):
    assert record["brief"]["identity"].encode() == caller.encode()
    assert record["identity_semantics"] == IDENTITY_SEMANTICS
    selected = record["selected_identity"]
    if version is None:
        assert selected is None
    else:
        assert selected["version_id"] == version and selected["guidance"] == guidance
        assert set(selected) == {"version_id", "pack_id", "guidance"}


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("guidance,caller", [
    ({"required": "PACK_RULE", "adaptable": "palette"}, CALLER),
    ({}, CALLER), ({"required": "PACK_ONLY"}, ""),
    ({"adaptable": "bloom permitted"}, "No bloom"),
    ({"required": "Bloom mandatory"}, "No bloom"),
    ({"required": "x" * 19000}, "z" * 20000),
])
def test_both_channels_through_public_request_actual_prompt_and_commit(rig, tmp_path, imported, guidance, caller):
    library, captured = rig
    origin = Unfold(tmp_path / "origin") if imported else library
    pack = origin.save_pack("Identity", guidance)
    version = pack["current_version"]
    if imported:
        exported = origin.export_pack(version, tmp_path / "identity.zip")
        version = library.import_pack(exported["path"])["version_id"]
    op, revision = create_revision(library, make_brief(version, caller))
    request = captured[-1]["request"]
    for record in (op, request, captured[-1]["data"], revision):
        assert_channels(record, caller, version, guidance)
        assert record["identity_provenance"] == {"caller_identity": "supplied"}
    assert captured[-1]["data"]["selected_identity"] == revision["selected_identity"]
    assert revision["selected_identity"]["pack_id"] == library.inspect(version)["pack_id"]
    assert retained_brief(revision["brief"]).identity == caller
    assert "If hard requirements conflict" in captured[-1]["prompt"]
    assert json.dumps(guidance) != revision["brief"]["identity"] or caller == json.dumps(guidance)
    # String growth cannot make max-length caller direction unrevisable.
    again = library.revise(revision["id"], "Preserve direction", GRANT)
    assert again["status"] == "completed", again
    assert_channels(library.inspect(again["revision_id"]), caller, version, guidance)


def test_repeated_revise_adopt_and_new_pack_resolve_once_without_stale_guidance(rig):
    library, captured = rig
    a = library.save_pack("A", {"required": "ONLY_A_RULE"})
    b = library.save_pack("B", {"required": "ONLY_B_RULE"})
    a_id, b_id = a["current_version"], b["current_version"]
    _, first = create_revision(library, make_brief(a_id))
    old = copy.deepcopy(library.store.get(first["id"]))
    current = first
    lengths = []
    for i in range(3):
        op = library.revise(current["id"], "Keep choices", GRANT)
        assert op["status"] == "completed", op
        current = library.inspect(op["revision_id"])
        assert_channels(current, CALLER, a_id, {"required": "ONLY_A_RULE"})
        lengths.append(len(json.dumps(current["selected_identity"])))
    assert len(set(lengths)) == 1
    for mode in ("adopt", "adopt", "revise"):
        op = (library.adopt_identity(current["id"], b_id, GRANT) if mode == "adopt"
              else library.revise(current["id"], "Keep choices", GRANT, identity_version=b_id))
        assert op["status"] == "completed", op
        current = library.inspect(op["revision_id"])
        assert_channels(current, CALLER, b_id, {"required": "ONLY_B_RULE"})
        assert "ONLY_A_RULE" not in json.dumps(captured[-1]["data"])
    assert library.store.get(first["id"]) == old
    assert current["identity_provenance"] == {"caller_identity": "supplied"}


def test_caller_only_adoption_keeps_json_literal_and_no_pack_is_null(rig):
    library, captured = rig
    literal = '{"required": "a caller may legitimately type JSON"}'
    _, first = create_revision(library, make_brief(identity=literal))
    assert_channels(first, literal, None, None)
    pack = library.save_pack("Identical text is not provenance", json.loads(literal))
    op = library.adopt_identity(first["id"], pack["current_version"], GRANT)
    assert op["status"] == "completed"
    assert_channels(library.inspect(op["revision_id"]), literal, pack["current_version"], json.loads(literal))


def test_selected_resources_and_model_asset_metadata_stay_scoped(rig, tmp_path):
    from PIL import Image

    library, captured = rig
    image = tmp_path / "image.png"
    Image.new("RGB", (8, 8), "red").save(image)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"identity metadata only, never decoded")
    font = Path(__file__).parent / "fixtures/fonts/IBMPlexSerif-Regular.ttf"
    assets = [library.import_asset(path, role=role, rights="redistributable")
              for path, role in ((image, "image"), (video, "video"), (font, "font"))]
    guidance = {"typography": {"body": {"font_asset_id": assets[2]["id"]}}}
    pack = library.save_pack("Mixed", guidance, [a["id"] for a in assets])
    _, first = create_revision(library, make_brief(pack["current_version"]))
    cap = captured[-1]
    assert set(cap["request"]["resources"]) == {a["id"] for a in assets}
    for a in assets:
        resource = cap["request"]["resources"][a["id"]]
        assert resource["path"] == a["path"] and resource["sha256"] == a["sha256"]
        assert "path" not in cap["assets"][a["id"]] and "sha256" not in cap["assets"][a["id"]]
    empty = library.save_pack("New no assets", {})
    op = library.adopt_identity(first["id"], empty["current_version"], GRANT)
    assert op["status"] == "completed"
    assert captured[-1]["request"]["resources"] == {}


def legacy_base(library, version, identity):
    """Fixture only: seed an unmarked historical-format base, never real old records."""
    op, revision = create_revision(library, make_brief(version, identity))
    stored = library.store.get(revision["id"])
    for key in ("identity_semantics", "identity_provenance", "selected_identity"):
        stored.pop(key)
    stored["brief"]["identity"] = identity
    library.store.put("revision", stored)
    return stored


@pytest.mark.parametrize("adopt", [False, True])
@pytest.mark.parametrize("legacy_text", ['{"required": "OLD_A_RULE"}', "ambiguous imported prose"])
def test_new_legacy_continuation_never_relabels_old_pack_text_as_caller(rig, adopt, legacy_text):
    library, captured = rig
    a = library.save_pack("A", {"required": "OLD_A_RULE"})["current_version"]
    b = library.save_pack("B", {"required": "NEW_B_RULE"})["current_version"]
    old = legacy_base(library, a, legacy_text)
    old_before = copy.deepcopy(old)
    request_id = uid()
    op = (library.adopt_identity(old["id"], b, GRANT, request_id=request_id) if adopt
          else library.revise(old["id"], "Preserve current content", GRANT, request_id=request_id))
    assert op["status"] == "completed", op
    version, guidance = (b, {"required": "NEW_B_RULE"}) if adopt else (a, {"required": "OLD_A_RULE"})
    current = library.inspect(op["revision_id"])
    assert_channels(current, "", version, guidance)
    provenance = current["identity_provenance"]
    assert provenance["caller_identity"] == "unavailable"
    assert provenance["source_revision_id"] == old["id"]
    assert "unknown" in provenance["limitation"]
    if adopt:
        assert "OLD_A_RULE" not in json.dumps(captured[-1]["data"])
    count = len(captured)
    # Exact retry still compares the original legacy admission, before normalization.
    retry = (library.adopt_identity(old["id"], b, GRANT, request_id=request_id) if adopt
             else library.revise(old["id"], "Preserve current content", GRANT, request_id=request_id))
    assert retry == op and len(captured) == count
    follow = library.revise(current["id"], "Continue", GRANT)
    assert follow["status"] == "completed"
    assert library.inspect(follow["revision_id"])["identity_provenance"] == provenance
    assert library.store.get(old["id"]) == old_before


def test_legacy_caller_only_is_not_discarded_and_unknown_markers_fail(rig):
    library, captured = rig
    old = legacy_base(library, None, CALLER)
    version = library.save_pack("New", {})["current_version"]
    op = library.adopt_identity(old["id"], version, GRANT)
    assert op["status"] == "completed"
    revision = library.store.get(op["revision_id"])
    assert revision["brief"]["identity"] == CALLER
    assert revision["identity_provenance"]["caller_identity"] == "legacy_caller_only"
    count = len(captured)
    for marker, provenance in (("future-version", {"caller_identity": "supplied"}),
                               (None, {"caller_identity": "supplied"}),
                               (IDENTITY_SEMANTICS, None),
                               (IDENTITY_SEMANTICS, {"caller_identity": "unavailable"})):
        damaged = {**revision, "identity_semantics": marker, "identity_provenance": provenance}
        library.store.put("revision", damaged)
        with pytest.raises(UnfoldError) as caught:
            library.revise(revision["id"], "Continue", GRANT)
        assert caught.value.code == "IDENTITY_PROVENANCE_UNKNOWN"
    assert len(captured) == count


def test_modern_retry_and_conflicts_do_not_recheck_missing_prerequisites(rig, monkeypatch):
    library, captured = rig
    version = library.save_pack("A", {"required": "A"})["current_version"]
    other = library.save_pack("B", {})["current_version"]
    brief = make_brief(version)
    request_id = uid()
    op, _ = create_revision(library, brief, request_id=request_id)
    stored = library.store.get(version)
    stored["prerequisites"] = [{"reason": "later missing dependency"}]
    library.store.put("pack_version", stored)
    monkeypatch.setattr(library.backend, "require", lambda: pytest.fail("Retry rechecked backend"))
    assert library.create(brief, GRANT, request_id=request_id) == op
    for changed in (brief.model_copy(update={"identity": "changed"}),
                    brief.model_copy(update={"identity_version": other})):
        with pytest.raises(UnfoldError) as caught:
            library.create(changed, GRANT, request_id=request_id)
        assert caught.value.code == "REQUEST_CONFLICT"
    assert len(captured) == 1


@pytest.mark.parametrize("input_hash", [True, False])
def test_historical_create_retry_is_unchanged_and_does_not_dispatch(rig, monkeypatch, input_hash):
    library, captured = rig
    version = library.save_pack("A", {"required": "OLD"})["current_version"]
    brief = make_brief(version)
    op, _ = create_revision(library, brief)
    old = copy.deepcopy(op)
    old["id"] = uid()
    for key in ("identity_semantics", "identity_provenance", "selected_identity"):
        old.pop(key)
    old["brief"]["identity"] = '{"required": "OLD"}'
    if not input_hash:
        old.pop("input_sha256")
    library.store.put("operation", old)
    monkeypatch.setattr(library.backend, "require", lambda: pytest.fail("Legacy retry executed"))
    assert library.create(brief, GRANT, request_id=old["id"]) == old
    if not input_hash:
        assert library.create(brief.model_copy(update={"identity": "unverifiable legacy text"}),
                              GRANT, request_id=old["id"]) == old
    else:
        with pytest.raises(UnfoldError) as caught:
            library.create(brief.model_copy(update={"identity": "changed"}), GRANT, request_id=old["id"])
        assert caught.value.code == "REQUEST_CONFLICT"
    assert library.store.get(old["id"]) == old and len(captured) == 1


def test_guards_before_dispatch_and_stale_base_are_preserved(rig, tmp_path):
    library, captured = rig
    blocked = library.save_pack("Unresolved", {}, prerequisites=[{"reason": "missing"}])["current_version"]
    asset_path = tmp_path / "video.mp4"
    asset_path.write_bytes(b"identity metadata only")
    asset = library.import_asset(asset_path, role="video", mode="reference")
    changed = library.save_pack("Changed", {}, [asset["id"]])["current_version"]
    asset_path.write_bytes(b"changed")
    for version, grant, code in ((blocked, GRANT, "MISSING_DEPENDENCY"),
                                 (changed, GRANT, "MATERIAL_CHANGED"),
                                 (None, GRANT.model_copy(update={"allow_context": False}), "DISCLOSURE_REQUIRED")):
        with pytest.raises(UnfoldError) as caught:
            library.create(make_brief(version), grant)
        assert caught.value.code == code
    assert not captured
    _, first = create_revision(library, make_brief())
    library.revise(first["id"], "Continue", GRANT)
    with pytest.raises(UnfoldError) as caught:
        library.revise(first["id"], "Stale request", GRANT)
    assert caught.value.code == "STALE_BASE"
    assert len(captured) == 2


def test_full_prompt_preserves_large_authorized_identity(rig):
    library, captured = rig
    version = library.save_pack("Large pack", {"required": "x" * 19000})["current_version"]
    create_revision(library, make_brief(version, "y" * 20000))
    cap = captured[-1]
    assert cap["owner"].calls == 0
    assert "x" * 19000 in cap["prompt"]
    assert "selected_identity" in cap["prompt"] and "y" * 20000 in cap["prompt"]


def test_identity_metadata_cannot_be_caller_brief_input():
    with pytest.raises(ValueError):
        Brief.model_validate({**make_brief().model_dump(), "selected_identity": {"guidance": {}}})


def test_input_fingerprint_remains_caller_payload_only(rig):
    library, _ = rig
    version = library.save_pack("A", {})["current_version"]
    brief = Brief.model_validate(make_brief(version).model_dump())
    op, _ = create_revision(library, brief)
    payload = {"brief": brief.model_dump(), "grant": GRANT.model_dump(), "base": None,
               "feedback": "", "feedback_target": None}
    assert op["input_sha256"] == hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    assert op["request_sha256"] != op["input_sha256"]


def test_help_explains_both_inputs_and_legacy_provenance():
    from unfold.capability_help import CAPABILITY_HELP, COMMAND_HELP

    for entry in (COMMAND_HELP["create"], COMMAND_HELP["revise"], CAPABILITY_HELP["adopt-identity"]):
        assert "selected_identity" in entry[-1] and "caller_identity=unavailable" in entry[-1]


def test_failed_operation_retry_preserves_both_channels_without_replay(rig, monkeypatch):
    library, _ = rig
    version = library.save_pack("A", {"required": "PACK"})["current_version"]
    seen = []
    def stop(argv, **kwargs):
        seen.append(json.loads(Path(argv[-1]).read_text()))
        raise UnfoldError("OFFLINE_STOP", "Intentional pre-provider stop")
    monkeypatch.setattr("unfold.lib.subprocess", types.SimpleNamespace(Popen=stop))
    request_id = uid()
    op = library.create(make_brief(version), GRANT, request_id=request_id)
    assert op["status"] == "failed" and op["error"]["code"] == "OFFLINE_STOP"
    assert_channels(op, CALLER, version, {"required": "PACK"})
    assert_channels(seen[0], CALLER, version, {"required": "PACK"})
    assert library.create(make_brief(version), GRANT, request_id=request_id) == op
    assert len(seen) == 1


def test_marked_retry_without_input_hash_still_conflicts_on_caller_change(rig):
    library, _ = rig
    version = library.save_pack("A", {})["current_version"]
    op, _ = create_revision(library, make_brief(version))
    op.pop("input_sha256")
    library.store.put("operation", op)
    assert library.create(make_brief(version), GRANT, request_id=op["id"]) == op
    with pytest.raises(UnfoldError) as caught:
        library.create(make_brief(version, "changed"), GRANT, request_id=op["id"])
    assert caught.value.code == "REQUEST_CONFLICT"


def test_old_completion_cannot_receive_forged_modern_provenance_from_worker(rig):
    library, _ = rig
    version = library.save_pack("A", {"required": "OLD"})["current_version"]
    op, revision = create_revision(library, make_brief(version))
    # Local historical recovery fixture: preserve old operation representation, then
    # re-run the actual commit seam using the already-written forged result fixture.
    for key in ("identity_semantics", "identity_provenance", "selected_identity"):
        op.pop(key)
    op["brief"]["identity"] = '{"required": "OLD"}'
    op["status"] = "running"
    library.store.put("operation", op)
    project = library.store.get(op["project_id"])
    project["current_revision"] = None
    library.store.put("project", project)
    done = library._complete_operation(op["id"])
    new = library.store.get(done["revision_id"])
    for key in ("identity_semantics", "identity_provenance", "selected_identity"):
        assert key not in new
    assert new["brief"] == op["brief"]


@pytest.mark.parametrize("input_hash", [True, False])
@pytest.mark.parametrize("adopt", [True, False])
def test_historical_derived_retry_compares_before_legacy_normalization(rig, monkeypatch, input_hash, adopt):
    library, captured = rig
    a = library.save_pack("A", {"required": "A"})["current_version"]
    b = library.save_pack("B", {"required": "B"})["current_version"]
    base = legacy_base(library, a, '{"required": "A"}')
    feedback = ("Adopt the selected identity version; preserve the explanation, timing and unrelated choices."
                if adopt else "Continue")
    supplied = Brief.model_validate(base["brief"]).model_copy(update={"identity_version": b if adopt else a})
    payload = {"brief": Brief.model_validate(supplied.model_dump()).model_dump(),
               "grant": GRANT.model_dump(), "base": base["id"], "feedback": feedback, "feedback_target": None}
    old = dict(id=uid(), kind="operation", status="failed",
               brief={**payload["brief"], "identity": '{"required": "B"}' if adopt else '{"required": "A"}'},
               grant=GRANT.model_dump(), base=base["id"], feedback=feedback, feedback_target=None)
    if input_hash:
        old["input_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    library.store.put("operation", old)
    monkeypatch.setattr(library.backend, "require", lambda: pytest.fail("Retry executed"))
    result = (library.adopt_identity(base["id"], b, GRANT, request_id=old["id"]) if adopt
              else library.revise(base["id"], feedback, GRANT, request_id=old["id"]))
    assert result == old and library.store.get(old["id"]) == old
    assert len(captured) == 1  # fixture base creation only