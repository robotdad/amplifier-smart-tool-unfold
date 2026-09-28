"""Historical fork: byte-copy fixtures, no renderer/model/media success claim."""
import copy
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from unfold import Brief, Unfold, UnfoldError
from unfold.forks import _Files
from unfold.store import digest, uid, write_json

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Fork requires POSIX directory descriptors")


def seed(tmp_path, *, resources=False, modern=True):
    tool=Unfold(tmp_path/"library",tmp_path/"absent-backend")
    project,revision,artifact=uid(),uid(),uid()
    directory=tool.store.workspace(uid())
    source=directory/"source"
    source.mkdir()
    # Deliberate formatting must survive byte-for-byte, not scene reserialization.
    (source/"scene.json").write_text('{"title":"Retained", "duration":1,\n "output":{"resolution":"720p"}, "fixture":true}\n')
    (source/"index.html").write_text("<!-- retained historical bytes: never execute -->")
    (source/"gsap.min.js").write_text("// fixture only")
    video=directory/"video.mp4"
    video.write_bytes(b"fixture MP4 bytes; not decoded or accepted media")
    pack=tool.save_pack("Shared pack",{"required":"old rules"})
    brief=Brief(title="Retained",intent="Keep explanation",identity="  Caller\n\u2603",
                identity_version=pack["current_version"],reference_id="b"*32).model_dump()
    if resources:
        media=source/"media"
        media.mkdir()
        (media/"screens").mkdir()
        image_id,font_id,video_id="1"*32,"2"*32,"3"*32
        (media/(image_id+".png")).write_bytes(b"fixture image")
        write_json(source/"resources.json",{image_id:digest(media/(image_id+".png"))})
        (source/"fonts").mkdir()
        font=Path(__file__).parent/"fixtures/fonts/IBMPlexSerif-Regular.ttf"
        shutil.copyfile(font,source/"fonts"/(font_id+".ttf"))
        write_json(source/"fonts.json",{font_id:{"suffix":".ttf","sha256":digest(font),
            "font":{"family":"IBM Plex Serif"},"rights":"redistributable","attribution":"OFL"}})
        (media/(video_id+".mp4")).write_bytes(b"retained fixture texture clip")
        (media/"screens/screen_0.png").write_bytes(b"retained fixture atlas")
        write_json(source/"screens.json",{"screen":{"asset_id":video_id,"suffix":".mp4",
            "sha256":digest(media/(video_id+".mp4")),"pages":[{"file":"media/screens/screen_0.png",
                "sha256":digest(media/"screens/screen_0.png")}]}})
        for name in ("babylon.js","scene3d_runtime.js","layer_acquire.cjs","layer_labels.js","layer_player.js"):
            (source/name).write_text("// retained fixture "+name)
    source_hash=tool.backend.source_hash(source)
    tool.store.put("project",{"id":project,"kind":"project","name":"Original","current_revision":revision,
                              "revisions":[revision],"review_grant":{"fixture":"must not transfer"}})
    record={"id":revision,"kind":"revision","project_id":project,"base_revision":None,"brief":brief,
        "identity_version":brief["identity_version"],"source":str(source.relative_to(tool.store.root)),
        "source_sha256":source_hash,"artifacts":[artifact],"feedback":"already-applied note",
        "feedback_target":None,"operation_id":uid(),"render":{"sha256":digest(video),"duration":1},
        "model_review":"prior model review","evidence":[{"fixture":"prior observed frames"}],
        "usage":{"model_calls":2,"renders":1},"limitations":["prior limitations"],
        "grant":{"must":"not become authority"}}
    if modern:
        record.update(identity_semantics="caller-plus-selected-pack-v1",
                      identity_provenance={"caller_identity":"supplied"},
                      selected_identity={"version_id":brief["identity_version"],"pack_id":pack["id"],
                                         "guidance":{"required":"old rules"}})
    else:
        record["brief"]["identity"]='{"required": "legacy substituted guidance"}'
    tool.store.put("revision",record)
    tool.store.put("artifact",{"id":artifact,"kind":"artifact","revision_id":revision,"name":"Original",
        "relative_path":str(video.relative_to(tool.store.root)),"sha256":digest(video),
        "source_sha256":source_hash,"format":"mp4","duration":1,"fps":"30/1","width":1280,
        "height":720,"frame_count":30,"ownership":"Unfold-managed","method":"original render observation",
        "audio":"silent","alpha":False})
    return tool,record,source,video


def records(tool):
    with tool.store.connect() as db:
        return {row[0]:row[1] for row in db.execute("SELECT id,data FROM records")}


def inventory(path):
    return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob("*") if p.is_file()}


def no_execution(tool,monkeypatch):
    def forbidden(*a,**kw):
        pytest.fail("Fork attempted author/render/probe/intelligence/subprocess")
    for name in ("author","render","probe","require","doctor","retained_resources"):
        monkeypatch.setattr(tool.backend,name,forbidden)
    monkeypatch.setattr(tool,"_produce",forbidden)
    monkeypatch.setattr(subprocess,"Popen",forbidden)


@pytest.mark.parametrize("modern",[True,False])
def test_exact_copy_provenance_legacy_absence_no_authority_transfer(tmp_path,monkeypatch,modern):
    tool,original,source,video=seed(tmp_path,resources=True,modern=modern)
    before=records(tool)
    old_files=inventory(source)
    no_execution(tool,monkeypatch)
    key=uid()
    result=tool.fork_revision(original["id"],"Fork \u2603",request_id=key)
    copied=tool.inspect(result["revision_id"])
    project=tool.inspect(result["project_id"])
    assert copied["source_integrity"]=="intact"
    assert copied["brief"]==original["brief"]
    assert copied["base_revision"] is None and copied["feedback"]==""
    assert project["current_revision"]==copied["id"] and project["revisions"]==[copied["id"]]
    assert project["name"]=="Fork \u2603"
    for k in ("identity_semantics","identity_provenance","selected_identity"):
        assert (k in copied)==(k in original)
        if k in original:
            assert copied[k]==original[k]
    for k in ("usage","grant","operation_id","model_review","evidence"):
        assert k not in copied
    assert "review_grant" not in project
    inherited=copied["inherited_evidence"]
    assert inherited["origin_revision_id"]==original["id"]
    assert inherited["records"]["usage"]==original["usage"]
    assert "not a new" in inherited["disposition"]
    assert "grant" not in inherited["records"]
    assert inventory(Path(copied["source_path"]))==old_files
    out=tool.artifact(result["artifact_ids"][0])
    assert Path(out["path"]).read_bytes()==video.read_bytes()
    assert out["sha256"]==digest(video) and out["revision_id"]==copied["id"]
    assert "no new render" in out["method"]
    assert os.stat(out["path"]).st_ino!=video.stat().st_ino
    for local in old_files:
        assert (Path(copied["source_path"])/local).stat().st_ino!=(source/local).stat().st_ino
    after=records(tool)
    assert all(after[i]==v for i,v in before.items())  # no old record changed
    assert tool.mutation_status(key)["result"]==result
    assert tool.fork_revision(original["id"],"Fork \u2603",request_id=key)==result
    # Copies remain independent after a throwaway origin disappears.
    shutil.rmtree(source)
    video.unlink()
    assert tool.fork_revision(original["id"],"Fork \u2603",request_id=key)==result
    assert tool.inspect(copied["id"])["source_integrity"]=="intact"
    assert tool.artifact(out["id"])["integrity"]=="intact"
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(original["id"],"Different",request_id=key)
    assert e.value.code=="REQUEST_CONFLICT"


def test_fork_uses_descriptor_inventory_not_backend_path_reopens(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path,resources=True)
    monkeypatch.setattr(tool.backend,"source_hash",lambda *a,**kw:pytest.fail("Unsafe path-based integrity reopen"))
    no_execution(tool,monkeypatch)
    result=tool.fork_revision(revision["id"],"No backend needed",request_id=uid())
    assert result["source_sha256"]==revision["source_sha256"]


def test_manifest_parent_swap_cannot_redirect_validation_read(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path,resources=True)
    outside=tmp_path/"victim"
    outside.mkdir()
    (outside/"resources.json").write_text('{"victim":"do not consume"}')
    real=_Files.read_json
    changed=False
    def swapped(self,relative,expected):
        nonlocal changed
        if not changed:
            changed=True
            source=(self.store.root/relative).parent
            source.rename(source.with_name("held"))
            source.symlink_to(outside,target_is_directory=True)
        # Reads the held original, NOT outside's malformed manifest.
        return real(self,relative,expected)
    monkeypatch.setattr(_Files,"read_json",swapped)
    with pytest.raises(UnfoldError):
        tool.fork_revision(revision["id"],"Manifest namespace race",request_id=uid())
    assert changed and len(tool.projects())==1
    assert (outside/"resources.json").read_text()=='{"victim":"do not consume"}'


def test_historical_a_to_b_fork_a_to_c_then_normal_revise_admission(tmp_path,monkeypatch):
    # Use the existing real public production/persistence fixture to prove this
    # is NOT a fork-specific stale-guard bypass or a manually rewritten head.
    from test_identity_direction import GRANT, create_revision, make_brief, rig

    tool,captured=rig.__wrapped__(tmp_path,monkeypatch)
    _,a=create_revision(tool,make_brief())
    b=tool.revise(a["id"],"Advance A to B",GRANT)
    assert b["status"]=="completed"
    original_project=tool.store.get(a["project_id"])
    old_a=tool.store.get(a["id"])
    before=records(tool)
    with pytest.raises(UnfoldError) as e:
        tool.revise(a["id"],"Still stale",GRANT)
    assert e.value.code=="STALE_BASE"
    key=uid()
    c=tool.fork_revision(a["id"],"Separate continuation",request_id=key)
    assert len(captured)==2
    assert tool.store.get(a["project_id"])==original_project
    assert tool.store.get(a["id"])==old_a
    assert all(records(tool)[i]==v for i,v in before.items())
    admission=tool.revise(c["revision_id"],"Authorized next edit",GRANT)
    assert admission["status"]=="completed",admission
    request=captured[-1]["request"]
    assert request["base"]==c["revision_id"]
    assert request["base_scene"]==json.loads((Path(a["source_path"])/"scene.json").read_text())
    assert request["brief"]==a["brief"]
    assert tool.store.get(a["project_id"])==original_project
    assert tool.inspect(c["project_id"])["current_revision"]==admission["revision_id"]
    with pytest.raises(UnfoldError) as e:
        tool.revise(a["id"],"No stale bypass",GRANT)
    assert e.value.code=="STALE_BASE"
    assert len(captured)==3


@pytest.mark.parametrize("failure",["source","artifact","atlas","font","missing","symlink","hardlink","fifo",
                                    "escape","manifest-escape","extra","unreferenced-media"])
def test_tampering_unsafe_files_and_escapes_refused_without_published_project(tmp_path,monkeypatch,failure):
    tool,revision,source,video=seed(tmp_path,resources=True)
    if failure=="source":
        (source/"index.html").write_text("changed")
    elif failure=="artifact":
        video.write_bytes(b"changed")
    elif failure=="atlas":
        (source/"media/screens/screen_0.png").write_bytes(b"changed")
    elif failure=="font":
        (source/"fonts"/("2"*32+".ttf")).write_bytes(b"changed")
    elif failure=="missing":
        (source/"gsap.min.js").unlink()
    elif failure in {"symlink","hardlink","fifo"}:
        target=source/"gsap.min.js"
        target.unlink()
        if failure=="symlink":
            target.symlink_to(video)
        elif failure=="hardlink":
            os.link(video,target)
        else:
            os.mkfifo(target)
    elif failure=="escape":
        revision["source"]="../outside"
        tool.store.put("revision",revision)
    elif failure=="manifest-escape":
        manifest=json.loads((source/"screens.json").read_text())
        manifest["screen"]["pages"][0]["file"]="../victim"
        write_json(source/"screens.json",manifest)
    elif failure=="unreferenced-media":
        (source/"media"/("f"*32+".png")).write_bytes(b"unrelated unmanifested material")
    else:
        (source/"unrelated.txt").write_text("not a supported source resource")
    before=records(tool)
    key=uid()
    no_execution(tool,monkeypatch)
    with pytest.raises(UnfoldError):
        tool.fork_revision(revision["id"],"Unsafe",request_id=key)
    assert len(tool.projects())==1
    assert all(records(tool)[i]==v for i,v in before.items())
    try:
        receipt=tool.mutation_status(key)
    except UnfoldError:  # pre-admission path/metadata refusal
        return
    assert receipt["status"]=="incomplete"
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(revision["id"],"Unsafe",request_id=key)
    assert e.value.code=="MUTATION_INCOMPLETE"


@pytest.mark.parametrize("which",["origin-file","origin-parent","copy-file","origin-record"])
def test_races_detected_before_atomic_publication(tmp_path,monkeypatch,which):
    tool,revision,source,video=seed(tmp_path)
    real=_Files.file
    injected=False
    def raced(self,relative,expected=None,target=None,**kwargs):
        nonlocal injected
        result=real(self,relative,expected,target,**kwargs)
        if target is not None and target.endswith("index.html") and not injected:
            injected=True
            if which=="origin-file":
                (source/"index.html").write_text("changed after verified copy")
            elif which=="origin-parent":
                saved=source.with_name("source-original")
                source.rename(saved)
                source.mkdir()
                for p in saved.iterdir():
                    shutil.copyfile(p,source/p.name)
            elif which=="copy-file":
                (self.store.root/target).write_text("bad copy")
            else:
                changed=copy.deepcopy(revision)
                changed["brief"]["intent"]="concurrent mutation"
                tool.store.put("revision",changed)
        return result
    monkeypatch.setattr(_Files,"file",raced)
    key=uid()
    with pytest.raises(UnfoldError):
        tool.fork_revision(revision["id"],"Raced",request_id=key)
    assert injected and len(tool.projects())==1
    assert tool.mutation_status(key)["status"]=="incomplete"


def test_destination_parent_replacement_never_redirects_copy_outside_library(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    outside=tmp_path/"outside"
    outside.mkdir()
    (outside/"sentinel").write_text("keep")
    real=_Files.file
    swapped=False
    def replace_parent(self,relative,expected=None,target=None,**kwargs):
        nonlocal swapped
        result=real(self,relative,expected,target,**kwargs)
        if target is not None and target.endswith("gsap.min.js") and not swapped:
            swapped=True
            parent=(self.store.root/target).parent
            parent.rename(parent.with_name("held-source"))
            parent.symlink_to(outside,target_is_directory=True)
        return result
    monkeypatch.setattr(_Files,"file",replace_parent)
    with pytest.raises(UnfoldError):
        tool.fork_revision(revision["id"],"Parent race",request_id=uid())
    assert swapped and inventory(outside)=={"sentinel":b"keep"}
    assert len(tool.projects())==1


def test_inplace_write_during_copy_invalidates_held_source(tmp_path,monkeypatch):
    import unfold.forks as forks

    tool,revision,source,_=seed(tmp_path)
    write=forks.write_all
    changed=False
    def change_source(fd,data,offset=None):
        nonlocal changed
        result=write(fd,data,offset)
        if not changed:
            changed=True
            original=source/"gsap.min.js"
            original.write_bytes(b"!"*original.stat().st_size)  # same length, not just size defense
        return result
    monkeypatch.setattr(forks,"write_all",change_source)
    with pytest.raises(UnfoldError):
        tool.fork_revision(revision["id"],"Inplace race",request_id=uid())
    assert changed and len(tool.projects())==1


def test_copy_failure_and_commit_failure_leave_named_incomplete_no_replay(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    key=uid()
    real=tool.store.put
    def fail(kind,data,db=None):
        # Artifact/project were written in this same transaction first: rollback
        # must remove those too, with only the old durable intent left.
        if kind=="revision" and data["id"]!=revision["id"]:
            raise OSError("publication interrupted")
        return real(kind,data,db)
    monkeypatch.setattr(tool.store,"put",fail)
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(revision["id"],"Interrupted",request_id=key)
    assert e.value.code=="FORK_INCOMPLETE"
    receipt=tool.mutation_status(key)
    assert receipt["status"]=="incomplete" and receipt["planned"]["revision_id"]
    assert (tool.store.root/receipt["staging_path"]).is_dir()
    assert len(tool.projects())==len(tool.store.list("revision"))==len(tool.store.list("artifact"))==1
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(revision["id"],"Interrupted",request_id=key)
    assert e.value.code=="MUTATION_INCOMPLETE"


def test_lost_ack_after_atomic_commit_returns_landed_result(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    real=tool._copy_fork
    calls=[]
    def lost(*a):
        calls.append(True)
        real(*a)
        raise OSError("transport-like acknowledgement loss AFTER commit")
    monkeypatch.setattr(tool,"_copy_fork",lost)
    key=uid()
    result=tool.fork_revision(revision["id"],"Recovered",request_id=key)
    assert result["status"]=="completed"
    assert tool.fork_revision(revision["id"],"Recovered",request_id=key)==result
    assert calls==[True] and len(tool.projects())==2


def test_concurrent_equal_request_cannot_duplicate(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    second=Unfold(tool.store.root)
    entered,release=threading.Event(),threading.Event()
    real=tool._copy_fork
    def paused(*a):
        entered.set()
        assert release.wait(5)
        return real(*a)
    monkeypatch.setattr(tool,"_copy_fork",paused)
    key=uid()
    with ThreadPoolExecutor(max_workers=2) as workers:
        first=workers.submit(tool.fork_revision,revision["id"],"Concurrent",request_id=key)
        assert entered.wait(5)
        try:
            with pytest.raises(UnfoldError) as e:
                second.fork_revision(revision["id"],"Concurrent",request_id=key)
            assert e.value.code=="MUTATION_INCOMPLETE"
        finally:
            release.set()
        result=first.result(timeout=10)
    assert second.fork_revision(revision["id"],"Concurrent",request_id=key)==result
    assert len(tool.projects())==2 and len(tool.store.list("revision"))==2


@pytest.mark.parametrize("change",[{"format":"mov"},{"delivery_id":"delivery"},{"audio":"narration"},{"role":"overlay"}])
def test_unsupported_delivery_artifact_refused_before_admission(tmp_path,change):
    tool,revision,_,_=seed(tmp_path)
    artifact=tool.store.get(revision["artifacts"][0])
    artifact.update(change)
    tool.store.put("artifact",artifact)
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(revision["id"],"Unsupported",request_id=uid())
    assert e.value.code=="UNSUPPORTED"
    assert not tool.store.list("mutation_receipt") and len(tool.projects())==1


def test_wrong_kind_and_invalid_arguments(tmp_path):
    tool,revision,_,_=seed(tmp_path)
    for kwargs in (
        dict(revision_id=revision["project_id"],name="Wrong kind",request_id=uid()),
        dict(revision_id=revision["id"],name=" ",request_id=uid()),
        dict(revision_id=revision["id"],name="OK",request_id="../escape"),
    ):
        with pytest.raises(UnfoldError):
            tool.fork_revision(**kwargs)
    assert not tool.store.list("mutation_receipt")


def test_bounds_and_pending_receipt_survive_restart(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    monkeypatch.setattr("unfold.forks.MAX_BYTES",1)
    key=uid()
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(revision["id"],"Over limit",request_id=key)
    assert e.value.code=="RESOURCE_LIMIT"
    reopened=Unfold(tool.store.root)
    assert reopened.mutation_status(key)["status"]=="incomplete"
    with pytest.raises(UnfoldError) as e:
        reopened.fork_revision(revision["id"],"Over limit",request_id=key)
    assert e.value.code=="MUTATION_INCOMPLETE"


@pytest.mark.parametrize("bound,value",[("MAX_FILES",2),("MAX_FILE_BYTES",1),("MAX_METADATA",1),
                                      ("MAX_DIRS",2),("MAX_SECONDS",0)])
def test_each_closed_copy_bound_refuses_without_publication(tmp_path,monkeypatch,bound,value):
    tool,revision,_,_=seed(tmp_path)
    monkeypatch.setattr("unfold.forks."+bound,value)
    with pytest.raises(UnfoldError) as caught:
        tool.fork_revision(revision["id"],"Bounded",request_id=uid())
    assert caught.value.code=="RESOURCE_LIMIT"
    assert len(tool.projects())==1


def test_combined_source_and_artifact_budget_before_stage_creation(tmp_path,monkeypatch):
    tool,revision,source,video=seed(tmp_path)
    source_size=sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
    monkeypatch.setattr("unfold.forks.MAX_BYTES",source_size+video.stat().st_size-1)
    key=uid()
    with pytest.raises(UnfoldError) as caught:
        tool.fork_revision(revision["id"],"Combined bound",request_id=key)
    assert caught.value.code=="RESOURCE_LIMIT"
    receipt=tool.mutation_status(key)
    assert not (tool.store.root/receipt["staging_path"]).exists()
    assert len(tool.projects())==1


def test_multiple_artifacts_and_shared_dependencies_are_explicit(tmp_path):
    tool,revision,source,video=seed(tmp_path)
    second=copy.deepcopy(tool.store.get(revision["artifacts"][0]))
    second["id"]=uid()
    second_path=video.with_name("again.mp4")
    second_path.write_bytes(video.read_bytes()+b"second retained render")
    second.update(relative_path=str(second_path.relative_to(tool.store.root)),sha256=digest(second_path))
    tool.store.put("artifact",second)
    revision["artifacts"].append(second["id"])
    tool.store.put("revision",revision)
    result=tool.fork_revision(revision["id"],"Two outputs",request_id=uid())
    assert len(result["artifact_ids"])==2
    assert [tool.artifact(i)["sha256"] for i in result["artifact_ids"]]==[digest(video),digest(second_path)]
    shared=result["shared_dependencies"]
    assert shared["identity_version"]==revision["identity_version"]
    assert shared["reference_id"]==revision["brief"]["reference_id"]
    assert "not copied or newly validated" in shared["note"]


def test_failed_file_copy_retains_named_partial_without_record_publication(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    def short_write(*args,**kwargs):
        raise OSError("simulated disk full during file copy")
    monkeypatch.setattr("unfold.forks.write_all",short_write)
    key=uid()
    with pytest.raises(UnfoldError) as caught:
        tool.fork_revision(revision["id"],"Partial files",request_id=key)
    assert caught.value.code=="FORK_INCOMPLETE"
    receipt=tool.mutation_status(key)
    assert receipt["status"]=="incomplete"
    assert (tool.store.root/receipt["staging_path"]).is_dir()
    assert len(tool.projects())==len(tool.store.list("revision"))==len(tool.store.list("artifact"))==1
    monkeypatch.setattr(tool,"_copy_fork",lambda *a:pytest.fail("Replayed uncertain copy"))
    with pytest.raises(UnfoldError) as e:
        tool.fork_revision(revision["id"],"Partial files",request_id=key)
    assert e.value.code=="MUTATION_INCOMPLETE"


def test_pending_receipt_after_owner_loss_never_replays(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    key=uid()
    class OwnerLost(BaseException):
        pass
    real=tool.store.put
    def refuse_incomplete(kind,data,db=None):
        if kind=="mutation_receipt" and data.get("status")=="incomplete":
            raise OwnerLost()
        return real(kind,data,db)
    monkeypatch.setattr(tool,"_copy_fork",lambda *a:(_ for _ in ()).throw(OwnerLost()))
    monkeypatch.setattr(tool.store,"put",refuse_incomplete)
    with pytest.raises(OwnerLost):
        tool.fork_revision(revision["id"],"Lost owner",request_id=key)
    # Durable first admission stands even when failure recording is interrupted.
    reopened=Unfold(tool.store.root)
    assert reopened.mutation_status(key)["status"]=="pending"
    with pytest.raises(UnfoldError) as e:
        reopened.fork_revision(revision["id"],"Lost owner",request_id=key)
    assert e.value.code=="MUTATION_INCOMPLETE" and len(reopened.projects())==1


def test_fork_does_not_transfer_drafts_feedback_or_review_grants(tmp_path):
    tool,revision,_,_=seed(tmp_path)
    tool.save_draft(revision["id"],"Unsubmitted change",sequence=1)
    tool.feedback(revision["id"],"Pending note",request_id=uid())
    before=records(tool)
    result=tool.fork_revision(revision["id"],"No inherited authority",request_id=uid())
    after=records(tool)
    assert all(after[k]==v for k,v in before.items())
    new_ids=set(after)-set(before)
    new=[json.loads(after[i]) for i in new_ids]
    assert {r["kind"] for r in new}=={"project","revision","artifact","mutation_receipt"}
    project=tool.inspect(result["project_id"])
    assert "review_grant" not in project


def test_receipt_completion_failure_rolls_back_all_published_records(tmp_path,monkeypatch):
    tool,revision,_,_=seed(tmp_path)
    real=tool.store.put
    def failed_receipt(kind,data,db=None):
        if kind=="mutation_receipt" and data.get("status")=="completed":
            raise OSError("completion receipt write failed")
        return real(kind,data,db)
    monkeypatch.setattr(tool.store,"put",failed_receipt)
    key=uid()
    with pytest.raises(UnfoldError):
        tool.fork_revision(revision["id"],"Atomic receipt",request_id=key)
    assert len(tool.projects())==len(tool.store.list("revision"))==len(tool.store.list("artifact"))==1
    assert tool.mutation_status(key)["status"]=="incomplete"


def test_public_cli_help_manifest_and_call(tmp_path):
    tool,revision,_,_=seed(tmp_path)
    command=[sys.executable,"-m","unfold","--library",str(tool.store.root)]
    help_result=subprocess.run([*command,"call","fork-revision","--help"],capture_output=True,text=True,check=True)
    assert "request_id" in help_result.stdout and "MUTATION_INCOMPLETE" in help_result.stdout
    from unfold.help import manifest, schemas

    assert manifest()["capabilities"]["fork-revision"]=="deterministic"
    assert "*, request_id" in schemas()["capabilities"]["fork-revision"]
    key=uid()
    args={"revision_id":revision["id"],"name":"CLI fork","request_id":key}
    result=subprocess.run([*command,"call","fork-revision","--args","-"],input=json.dumps(args),
                          capture_output=True,text=True,check=True)
    payload=json.loads(result.stdout)
    assert tool.mutation_status(key)["status"]=="completed"
    assert payload  # CLI envelope remains owned by the existing adapter


def test_official_mcp_fork_schema_and_same_library_operation(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client

    from unfold.mcp import create_server

    tool,revision,_,_=seed(tmp_path)
    async def run():
        async with Client(create_server(tool,allow_models=False)) as client:
            advertised=next(t for t in (await client.list_tools()).tools if t.name=="unfold_fork_revision")
            assert set(advertised.input_schema["required"])=={"revision_id","name","request_id"}
            assert advertised.input_schema["properties"]["request_id"]["pattern"]=="^[a-f0-9]{32}$"
            assert not advertised.annotations.read_only_hint
            assert not advertised.annotations.destructive_hint
            assert advertised.annotations.idempotent_hint
            args={"revision_id":revision["id"],"name":"MCP fork","request_id":uid()}
            one=await client.call_tool("unfold_fork_revision",args)
            assert not one.is_error,one
            two=await client.call_tool("unfold_fork_revision",args)
            assert one.structured_content==two.structured_content
            assert len(tool.projects())==2 and not tool.store.list("operation")
    anyio.run(run)