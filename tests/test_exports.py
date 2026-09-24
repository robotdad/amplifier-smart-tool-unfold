"""Provider-free export acceptance. Scripted production is not creative/media proof."""

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from unfold import Brief, Grant, Unfold, UnfoldError
from unfold.exports import output_summary, suggested_filename
from unfold.store import digest, uid

BRIEF = Brief(title="The convergence loop", intent="Offline export fixture")
GRANT = Grant(provider="openai", model="not-a-live-model",
              allow_context=True, allow_frames=True, vision=True)
POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX exact-file publication only")
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Native Windows exact-file refusal")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    tool = Unfold(tmp_path / "library", tmp_path / "absent-backend")
    calls = []
    monkeypatch.setattr(tool.backend, "require", lambda: None)
    monkeypatch.setattr(tool.backend, "source_hash", lambda path: digest(path / "scene.json"))
    monkeypatch.setattr(tool.backend, "probe", lambda path: {"width": 1920, "height": 1080})

    class ScriptedWorker:
        pid = os.getpid()

        def __init__(self, command, **kwargs):
            request = Path(command[-1])
            payload = json.loads(request.read_text())
            assert "export_to" not in payload
            assert "destination" not in json.dumps(payload)
            calls.append(payload)
            directory = request.parent
            (directory / "source").mkdir()
            (directory / "source/scene.json").write_text(json.dumps({
                "output": payload["brief"]["output"],
            }))
            (directory / "video.mp4").write_bytes(b"scripted media: not a real MP4")
            (directory / "result.json").write_text(json.dumps({
                "source_sha256": digest(directory / "source/scene.json"),
                "render": {"sha256": digest(directory / "video.mp4")},
            }))

        def poll(self):
            return 0

    monkeypatch.setattr("unfold.lib.subprocess.Popen", ScriptedWorker)
    return tool, calls


@POSIX
def test_create_exact_retry_reopen_rename_and_deleted_copy(rig, tmp_path, monkeypatch):
    tool, calls = rig
    request = uid()
    monkeypatch.chdir(tmp_path)
    result = tool.create(BRIEF, GRANT, request_id=request, export_to="Convergence é.mp4")
    assert result["status"] == result["export"]["status"] == "completed"
    assert len(calls) == 1
    assert result["outputs"][0]["suggested_filename"] == "the-convergence-loop.mp4"
    assert result["primary_artifact_id"] == result["export"]["artifact_id"]
    assert result["revision_id"] == result["export"]["revision_id"]
    assert digest(tmp_path / "Convergence é.mp4") == result["outputs"][0]["sha256"]
    assert tool.inspect(request)["export"] == result["export"]
    assert tool.mutation_status(result["export"]["receipt_id"])["result"] == result["export"]
    tool.rename(result["primary_artifact_id"], "Renamed")
    reopened = Unfold(tool.store.root, tmp_path / "no-renderer")
    monkeypatch.chdir(tool.store.root)
    assert reopened.create(BRIEF, GRANT, request_id=request, export_to="Convergence é.mp4") == result
    (tmp_path / "Convergence é.mp4").unlink()
    assert reopened.create(BRIEF, GRANT, request_id=request) == result
    assert not (tmp_path / "Convergence é.mp4").exists()
    for changes in ({"export_to": "other.mp4"},
                    {"brief": BRIEF.model_copy(update={"intent": "changed"})},
                    {"grant": GRANT.model_copy(update={"model": "changed"})}):
        with pytest.raises(UnfoldError, match="different input"):
            reopened.create(changes.get("brief", BRIEF), changes.get("grant", GRANT),
                            request_id=request, export_to=changes.get("export_to", "Convergence é.mp4"))
    assert len(calls) == 1


@POSIX
@pytest.mark.parametrize("name", [
    "missing", "wrong.mov", "CON.mp4", "NUL.mp4", "COM¹.mp4", "bad?.mp4",
    "bad\n.mp4", "trail.mp4 ", "a" * 256 + ".mp4", "missing-parent/result.mp4",
])
def test_bad_destination_precedes_production(rig, tmp_path, name):
    tool, calls = rig
    with pytest.raises(UnfoldError):
        tool.create(BRIEF, GRANT, export_to=tmp_path / name)
    assert calls == []
    assert tool.projects() == []


@POSIX
@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
def test_existing_destination_never_overwritten(rig, tmp_path, kind):
    tool, calls = rig
    path = tmp_path / "chosen.mp4"
    if kind == "file":
        path.write_bytes(b"original")
    elif kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(tmp_path / ("chosen.mp4" if kind == "symlink" else "absent"))
    with pytest.raises(UnfoldError, match="already exists"):
        tool.create(BRIEF, GRANT, export_to=path)
    assert not calls
    if kind == "file":
        assert path.read_bytes() == b"original"


@POSIX
def test_crash_before_copy_resumes_without_generation(rig, tmp_path, monkeypatch):
    tool, calls = rig
    original = tool._run_export
    monkeypatch.setattr(tool, "_run_export", lambda *a: (_ for _ in ()).throw(SystemExit(73)))
    request, path = uid(), tmp_path / "result.mp4"
    with pytest.raises(SystemExit):
        tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    assert tool.inspect(request)["export"]["status"] == "not_started"
    monkeypatch.setattr(tool, "_run_export", original)
    result = tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    assert result["export"]["status"] == "completed"
    assert len(calls) == 1


@POSIX
@pytest.mark.parametrize("failure", ["disk-full", "permission", "interrupt", "race", "after-publish"])
def test_post_generation_failures_preserve_media_and_never_regenerate(rig, tmp_path, monkeypatch, failure):
    import unfold.exports as exports

    tool, calls = rig
    path, request = tmp_path / "result.mp4", uid()
    original_write, original_link = exports.write_all, os.link

    def fail_write(fd, data):
        original_write(fd, data[:3])
        if failure == "interrupt":
            raise KeyboardInterrupt
        raise OSError(errno.ENOSPC if failure == "disk-full" else errno.EACCES, "injected")

    def fail_link(*args, **kwargs):
        if failure == "race":
            path.write_bytes(b"competitor")
        original_link(*args, **kwargs)
        if failure == "after-publish":
            raise SystemExit(73)

    with monkeypatch.context() as patch:
        if failure in {"disk-full", "permission", "interrupt"}:
            patch.setattr(exports, "write_all", fail_write)
        else:
            # Keep supports_dir_fd capability check valid for the injected wrapper.
            patch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {fail_link})
            patch.setattr(os, "link", fail_link)
        if failure == "after-publish":
            with pytest.raises(SystemExit):
                tool.create(BRIEF, GRANT, request_id=request, export_to=path)
            result = tool.create(BRIEF, GRANT, request_id=request, export_to=path)
            assert result["export"]["error"]["code"] == "MUTATION_INCOMPLETE"
        else:
            result = tool.create(BRIEF, GRANT, request_id=request, export_to=path)
        assert result["status"] == "completed"
        assert result["export"]["status"] in {"failed", "incomplete"}
        assert tool.create(BRIEF, GRANT, request_id=request, export_to=path) == result
    assert len(calls) == 1
    assert not list(tmp_path.glob(".unfold-*.tmp"))
    if failure == "race":
        assert path.read_bytes() == b"competitor"
    recovered = tool.export_file(result["primary_artifact_id"], tmp_path / "recovery.mp4")
    assert recovered["status"] == "completed"
    assert len(calls) == 1


@POSIX
def test_crash_during_copy_keeps_identified_staging(rig, tmp_path, monkeypatch):
    tool, calls = rig
    request, path = uid(), tmp_path / "result.mp4"
    # Simulate abrupt process loss at the durable receipt boundary. No finally runs.
    def crash(artifact_id, intent, base):
        Path(intent["staging_path"]).write_bytes(b"partial")
        raise SystemExit(73)
    with monkeypatch.context() as patch:
        patch.setattr(tool, "_copy_export", crash)
        with pytest.raises(SystemExit):
            tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    result = tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    assert result["export"]["status"] == "incomplete"
    receipt = tool.mutation_status(result["export"]["receipt_id"])
    assert Path(receipt["payload"]["staging_path"]).read_bytes() == b"partial"
    assert not path.exists() and len(calls) == 1


@POSIX
@pytest.mark.parametrize("state", ["cancelled", "failed", "interrupted", "running"])
def test_uncommitted_production_never_copies(rig, tmp_path, monkeypatch, state):
    tool, calls = rig
    monkeypatch.setattr(tool, "_produce", lambda *a, **kw: {
        "id": kw["request_id"], "status": state, "kind": "operation",
    })
    result = tool.create(BRIEF, GRANT, export_to=tmp_path / "result.mp4")
    assert result["export"]["status"] == "not_started"
    assert not (tmp_path / "result.mp4").exists()
    assert not calls


@POSIX
def test_parent_symlink_anchored_and_replacement_rejected(rig, tmp_path, monkeypatch):
    tool, _ = rig
    target, other, alias = (tmp_path / n for n in ("target", "other", "alias"))
    target.mkdir()
    other.mkdir()
    alias.symlink_to(target, target_is_directory=True)
    original = tool._produce

    def change_alias(*args, **kwargs):
        alias.unlink()
        alias.symlink_to(other, target_is_directory=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(tool, "_produce", change_alias)
    result = tool.create(BRIEF, GRANT, export_to=alias / "result.mp4")
    assert result["export"]["status"] == "completed"
    assert (target / "result.mp4").exists() and not (other / "result.mp4").exists()

    def replace_parent(*args, **kwargs):
        target.rename(tmp_path / "moved")
        target.symlink_to(other, target_is_directory=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(tool, "_produce", replace_parent)
    result = tool.create(BRIEF, GRANT, export_to=target / "new.mp4")
    assert result["export"]["status"] == "failed"
    assert not (other / "new.mp4").exists()


@POSIX
@pytest.mark.parametrize("replacement", ["symlink", "changed", "during-copy", "staging"])
def test_source_and_staging_replacement_refused(rig, tmp_path, monkeypatch, replacement):
    import unfold.exports as exports

    tool, _ = rig
    operation = tool.create(BRIEF, GRANT)
    artifact = tool.artifact(operation["primary_artifact_id"])
    source = Path(artifact["path"])
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"must survive")
    if replacement == "symlink":
        source.unlink()
        source.symlink_to(unrelated)
    elif replacement == "changed":
        source.write_bytes(b"changed")
    else:
        original = exports.write_all

        def swap(fd, data):
            original(fd, data)
            if replacement == "during-copy":
                source.rename(source.with_suffix(".saved"))
                source.write_bytes(data)
            else:
                staging = next(tmp_path.glob(".unfold-*.tmp"))
                staging.rename(staging.with_suffix(".saved"))
                staging.symlink_to(unrelated)
        monkeypatch.setattr(exports, "write_all", swap)
    result = tool.export_file(artifact["id"], tmp_path / "result.mp4")
    assert result["status"] == "failed"
    assert not (tmp_path / "result.mp4").exists()
    assert unrelated.read_bytes() == b"must survive"
    if replacement == "staging":
        assert next(tmp_path.glob(".unfold-*.tmp")).is_symlink()


def test_suggestions_and_legacy_primary_resolution(rig):
    tool, _ = rig
    result = tool.create(BRIEF, GRANT)
    artifact = tool.store.get(result["primary_artifact_id"])
    for name, expected in [("The convergence loop", "the-convergence-loop.mp4"),
                           ("cafe\u0301 数学", "café-数学.mp4"),
                           ("...", "artifact-" + artifact["id"] + ".mp4"),
                           ("CON", "artifact-" + artifact["id"] + ".mp4")]:
        assert suggested_filename({**artifact, "name": name}) == expected
    assert len(suggested_filename({**artifact, "name": "é" * 500}).encode()) <= 184
    old = tool.store.get(result["id"])
    del old["outputs"], old["primary_artifact_id"]
    tool.store.put("operation", old)
    assert tool.inspect(old["id"])["primary_artifact_id"] == artifact["id"]
    later = {**artifact, "id": uid()}
    tool.store.put("artifact", later)
    assert tool.inspect(old["id"])["primary_artifact_id"] == artifact["id"]
    project = tool.store.get(old["project_id"])
    project["current_revision"] = uid()
    tool.store.put("project", project)
    assert tool.inspect(old["id"])["primary_artifact_id"] == artifact["id"]
    revision = tool.store.get(result["revision_id"])
    delivery = {**artifact, "id": uid(), "delivery_id": uid(), "profile": "video"}
    tool.store.put("artifact", delivery)
    revision["artifacts"].append(delivery["id"])
    tool.store.put("revision", revision)
    assert tool.inspect(old["id"])["primary_artifact_id"] == artifact["id"]
    revision["artifacts"].append(later["id"])
    tool.store.put("revision", revision)
    assert tool.inspect(old["id"])["output_error"]["code"] == "PRIMARY_OUTPUT_UNAVAILABLE"
    revision["artifacts"] = []
    tool.store.put("revision", revision)
    assert tool.inspect(old["id"])["outputs"] == []


@POSIX
def test_intent_binds_generation_before_admission_and_reserved_child_id(rig, tmp_path, monkeypatch):
    import hashlib

    tool, calls = rig
    request, path = uid(), tmp_path / "result.mp4"
    with monkeypatch.context() as patch:
        patch.setattr(tool, "_produce", lambda *a, **kw: (_ for _ in ()).throw(SystemExit(73)))
        with pytest.raises(SystemExit):
            tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    with pytest.raises(UnfoldError, match="different input"):
        tool.create(BRIEF.model_copy(update={"title": "different"}), GRANT,
                    request_id=request, export_to=path)
    assert not calls
    path.write_bytes(b"appeared after intent admission")
    with pytest.raises(UnfoldError, match="already exists"):
        tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    assert not calls
    path.unlink()
    result = tool.create(BRIEF, GRANT, request_id=request, export_to=path)
    assert result["export"]["status"] == "completed" and len(calls) == 1
    occupied = uid()
    child = hashlib.sha256(("create-export:" + occupied).encode()).hexdigest()[:32]
    tool.store.put("other", {"id": child, "kind": "other"})
    with pytest.raises(UnfoldError, match="other work"):
        tool.create(BRIEF, GRANT, request_id=occupied, export_to=tmp_path / "new.mp4")
    assert len(calls) == 1


def test_legacy_directory_export_and_retry_remain_portable(rig, tmp_path):
    tool, calls = rig
    operation = tool.create(BRIEF, GRANT)
    artifact = operation["primary_artifact_id"]
    request = uid()
    legacy = tool.export(artifact, tmp_path / "directory.mp4", request)
    assert Path(legacy["path"]).name == "The convergence loop.mp4"
    assert (tmp_path / "directory.mp4").is_dir()
    assert digest(Path(legacy["path"])) == operation["outputs"][0]["sha256"]
    assert tool.export(artifact, tmp_path / "directory.mp4", request) == legacy
    with pytest.raises(UnfoldError) as error:
        tool.export(artifact, tmp_path / "directory.mp4")
    assert error.value.code == "OUTPUT_EXISTS"
    assert digest(Path(legacy["path"])) == operation["outputs"][0]["sha256"]
    with pytest.raises(UnfoldError, match="no export intent"):
        tool.create(BRIEF, GRANT, request_id=operation["id"], export_to=tmp_path / "new.mp4")
    assert len(calls) == 1


@POSIX
def test_export_only_retry(rig, tmp_path, monkeypatch):
    tool, calls = rig
    operation = tool.create(BRIEF, GRANT)
    artifact = operation["primary_artifact_id"]
    request = uid()
    result = tool.export_file(artifact, tmp_path / "exact.mp4", request)
    Path(result["path"]).unlink()
    tool.rename(artifact, "changed")
    monkeypatch.setattr(tool.backend, "source_hash", lambda *a: pytest.fail("retry revalidated"))
    assert tool.export_file(artifact, tmp_path / "exact.mp4", request) == result
    with pytest.raises(UnfoldError):
        tool.export_file(artifact, tmp_path / "other.mp4", request)
    assert len(calls) == 1


@pytest.fixture(params=[
    "missing-primitives",
    pytest.param("native-windows", marks=WINDOWS),
])
def unsupported_publication(request, monkeypatch):
    # The native Windows case must exercise the real platform, not a mocked os.name
    # or capability set. The other case checks capability loss on every host.
    if request.param == "missing-primitives":
        monkeypatch.setattr(os, "supports_dir_fd", set())


def test_unsupported_publication_fails_before_spending(rig, tmp_path, unsupported_publication):
    tool, calls = rig
    path = tmp_path / "result.mp4"
    with pytest.raises(UnfoldError, match="POSIX") as error:
        tool.create(BRIEF, GRANT, export_to=path)
    assert error.value.code == "UNSUPPORTED"
    assert "legacy directory export" in error.value.remedy
    assert not calls
    assert not tool.projects()
    assert not tool.store.list("mutation_intent")
    assert not path.exists()
    assert not list(tmp_path.glob(".unfold-*.tmp"))


def test_unsupported_export_only_preserves_retained_media(
    rig, tmp_path, unsupported_publication
):
    tool, calls = rig
    operation = tool.create(BRIEF, GRANT)
    artifact = operation["primary_artifact_id"]
    source = Path(tool.artifact(artifact)["path"])
    before = source.read_bytes()
    path = tmp_path / "result.mp4"
    with pytest.raises(UnfoldError) as error:
        tool.export_file(artifact, path)
    assert error.value.code == "UNSUPPORTED"
    assert "legacy directory export" in error.value.remedy
    assert source.read_bytes() == before
    assert not path.exists()
    assert not tool.store.list("mutation_intent")
    assert not list(tmp_path.glob(".unfold-*.tmp"))
    assert len(calls) == 1


@pytest.mark.parametrize("command_name", ["create", "export-file", "call"])
def test_unsupported_cli_reports_refusal(
    rig, tmp_path, monkeypatch, capsys, unsupported_publication, command_name
):
    from unfold import cli

    tool, calls = rig
    monkeypatch.setattr(cli, "Unfold", lambda *a: tool)
    destination = str(tmp_path / "result.mp4")
    if command_name == "create":
        brief, grant = tmp_path / "brief.json", tmp_path / "grant.json"
        brief.write_text(BRIEF.model_dump_json())
        grant.write_text(GRANT.model_dump_json())
        command = ["create", "--brief", str(brief), "--grant", str(grant),
                   "--export-to", destination]
        expected_calls = 0
    else:
        operation = tool.create(BRIEF, GRANT)
        artifact = operation["primary_artifact_id"]
        expected_calls = 1
        if command_name == "call":
            arguments = tmp_path / "args.json"
            arguments.write_text(json.dumps({"artifact_id": artifact, "destination": destination}))
            command = ["call", "export-file", "--args", str(arguments)]
        else:
            command = ["export-file", artifact, destination]
    monkeypatch.setattr(sys, "argv", ["unfold", *command])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 1
    captured = capsys.readouterr()
    assert not captured.out  # Refusal before admission, not a completed partial result.
    error = json.loads(captured.err)["error"]
    assert error["code"] == "UNSUPPORTED"
    assert "legacy directory export" in error["remedy"]
    assert not Path(destination).exists()
    assert not tool.store.list("mutation_intent")
    assert len(calls) == expected_calls


def test_cli_create_metadata_without_export_is_portable(rig, tmp_path, monkeypatch, capsys):
    from unfold import cli

    tool, calls = rig
    monkeypatch.setattr(cli, "Unfold", lambda *a: tool)
    brief, grant = tmp_path / "brief.json", tmp_path / "grant.json"
    brief.write_text(BRIEF.model_dump_json())
    grant.write_text(GRANT.model_dump_json())
    command = ["create", "--brief", str(brief), "--grant", str(grant)]
    monkeypatch.setattr(sys, "argv", ["unfold", *command])
    cli.main()
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    assert result["status"] == "completed"
    assert result["outputs"][0]["artifact_id"] == result["primary_artifact_id"]
    assert result["outputs"][0]["revision_id"] == result["revision_id"]
    assert result["outputs"] == tool.inspect(result["id"])["outputs"]
    assert "export" not in result
    assert not tool.store.list("mutation_intent")
    assert len(calls) == 1


@POSIX
def test_cli_partial_outcome_and_json_adapter(rig, tmp_path, monkeypatch, capsys):
    from unfold import cli

    tool, _ = rig
    monkeypatch.setattr(cli, "Unfold", lambda *a: tool)
    brief, grant = tmp_path / "brief.json", tmp_path / "grant.json"
    brief.write_text(BRIEF.model_dump_json())
    grant.write_text(GRANT.model_dump_json())
    original = tool._produce
    path = tmp_path / "result.mp4"

    def collision(*a, **kw):
        result = original(*a, **kw)
        path.write_bytes(b"competitor")
        return result
    monkeypatch.setattr(tool, "_produce", collision)
    monkeypatch.setattr(sys, "argv", ["unfold", "create", "--brief", str(brief),
                                    "--grant", str(grant), "--export-to", str(path)])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 1
    capture = capsys.readouterr()
    result = json.loads(capture.out)
    assert result["status"] == "completed"
    assert result["export"]["status"] == "failed"
    assert json.loads(capture.err)["error"]
    assert path.read_bytes() == b"competitor"


@POSIX
@pytest.mark.parametrize("json_adapter", [False, True])
def test_export_cli_failure_has_structured_stdout_and_nonzero_exit(
    rig, tmp_path, monkeypatch, capsys, json_adapter
):
    from unfold import cli

    tool, _ = rig
    result = tool.create(BRIEF, GRANT)
    artifact = result["primary_artifact_id"]
    Path(tool.artifact(artifact)["path"]).write_bytes(b"corrupt")
    monkeypatch.setattr(cli, "Unfold", lambda *a: tool)
    destination = str(tmp_path / "result.mp4")
    if json_adapter:
        arguments = tmp_path / "args.json"
        arguments.write_text(json.dumps({"artifact_id": artifact, "destination": destination}))
        command = ["call", "export-file", "--args", str(arguments)]
    else:
        command = ["export-file", artifact, destination]
    monkeypatch.setattr(sys, "argv", ["unfold", *command])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "failed"
    assert json.loads(captured.err)["error"]["code"] == "MATERIAL_CHANGED"


@POSIX
def test_copy_real_retained_mp4_without_renderer(tmp_path):
    """FFmpeg fixture proves byte preservation, not generation or creative quality."""
    import shutil

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    tool = Unfold(tmp_path / "library", tmp_path / "no-backend")
    directory = tool.store.workspace(uid())
    source = directory / "source"
    source.mkdir()
    (source / "scene.json").write_text("{}")
    (source / "index.html").write_text("<!-- retained fixture -->")
    (source / "gsap.min.js").write_text("// retained fixture")
    # Use the real hash machinery on a minimal retained source.
    video = directory / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=navy:s=1280x720:r=30", "-frames:v", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=30)
    revision = uid()
    artifact = tool._artifact(revision, video, {"sha256": digest(video)}, "Retained media")
    tool.store.put("artifact", artifact)
    tool.store.put("revision", {"id": revision, "kind": "revision",
                               "source": str(source.relative_to(tool.store.root)),
                               "source_sha256": tool.backend.source_hash(source), "artifacts": [artifact["id"]]})
    result = tool.export_file(artifact["id"], tmp_path / "Retained media.mp4")
    assert result["status"] == "completed"
    assert video.read_bytes() == Path(result["path"]).read_bytes()
    subprocess.run(["ffmpeg", "-v", "error", "-i", result["path"], "-f", "null", "-"],
                   check=True, timeout=30)
    assert output_summary(artifact)["sha256"] == digest(Path(result["path"]))


def test_async_metadata_without_autoexport(rig, monkeypatch):
    tool, calls = rig
    monkeypatch.setattr(tool, "_launch_review_job", lambda identity: tool.store.get(identity))
    job = tool.submit_creation(BRIEF, GRANT, uid())
    assert job["status"] == "queued"
    assert not job.get("outputs") and "export" not in job
    result = tool.run_review_job(job["id"])
    assert result["status"] == "completed"
    assert result["result"]["outputs"] == tool.inspect(result["operation_id"])["outputs"]
    assert result["result"]["primary_artifact_id"]
    assert "export" not in result["result"]
    assert not tool.store.list("mutation_intent")
    assert len(calls) == 1


@POSIX
@pytest.mark.parametrize("point", ["mid-copy", "publication", "acknowledgement"])
def test_real_process_loss_never_repeats_uncertain_copy(tmp_path, point):
    from mcp_fixtures import seed_media_library

    tool, _, _, artifacts = seed_media_library(tmp_path / "library", playable=False)
    request, path = uid(), tmp_path / "result.mp4"
    code = """
import os, sys
from unfold import Unfold
import unfold.exports as exports
tool = Unfold(sys.argv[1], '/unused-renderer')
point = sys.argv[5]
if point == 'mid-copy':
    original = exports.write_all
    def interrupted(fd, data):
        original(fd, data[:3])
        os._exit(73)
    exports.write_all = interrupted
if point == 'publication':
    original = os.link
    def interrupted(*a, **kw):
        original(*a, **kw)
        os._exit(73)
    os.link = interrupted
    os.supports_dir_fd = os.supports_dir_fd | {interrupted}
result = tool.export_file(sys.argv[2], sys.argv[3], sys.argv[4])
assert result['status'] == 'completed', result
assert not any(name.startswith('amplifier') for name in sys.modules)
os._exit(73)
"""
    completed = subprocess.run([sys.executable, "-c", code, str(tool.store.root),
                                artifacts[0], str(path), request, point],
                               capture_output=True, timeout=20,
                               env={k: v for k, v in os.environ.items()
                                    if k in {"PATH", "HOME", "SYSTEMROOT"}})
    assert completed.returncode == 73, completed.stderr
    result = Unfold(tool.store.root, tmp_path / "no-backend").export_file(
        artifacts[0], path, request)
    if point == "acknowledgement":
        assert result["status"] == "completed"
        assert not list(tmp_path.glob(".unfold-*.tmp"))
        path.unlink()
        assert tool.export_file(artifacts[0], path, request) == result
        assert not path.exists()
    else:
        assert result["status"] == "incomplete"
        assert result["error"]["code"] == "MUTATION_INCOMPLETE"
        receipt = tool.mutation_status(request)
        staging = Path(receipt["payload"]["staging_path"])
        assert staging.exists()
        assert path.exists() == (point == "publication")
        before = staging.read_bytes()
        assert tool.export_file(artifacts[0], path, request) == result
        assert staging.read_bytes() == before
        recovered = tool.export_file(artifacts[0], tmp_path / "new.mp4", uid())
        assert recovered["status"] == "completed"


@POSIX
def test_nested_mcp_export_results_never_leak_server_paths(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client
    from mcp_fixtures import seed_media_library

    from unfold.mcp import create_server

    async def run():
        tool, _, _, artifacts = seed_media_library(tmp_path / "private-library", playable=False)
        request = uid()
        result = tool.export_file(artifacts[0], tmp_path / "private-result.mp4", request)
        async with Client(create_server(tool)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert "unfold_export_file" not in names
            for identity in (request, "mutation-intent-" + request):
                response = await client.call_tool("unfold_inspect", {"identity": identity})
                assert not response.is_error
                encoded = json.dumps(response.structured_content)
                for private in (str(tmp_path), "staging_path", "parent_identity", "destination"):
                    assert private not in encoded
            response = await client.call_tool("unfold_mutation_status", {"request_id": request})
            projected = response.structured_content["result"]["result"]
            assert projected["artifact_id"] == result["artifact_id"]
            assert projected["sha256"] == result["sha256"]
            assert "path" not in projected
    anyio.run(run)


@pytest.mark.parametrize("target", ["export", "create-explicit", "create-omitted"])
@pytest.mark.parametrize("surface", ["library", "cli"])
@pytest.mark.parametrize("retained", ["rename", "wrong-binding", "missing-fields"])
def test_foreign_export_intent_conflicts_before_fields_or_effects(
    tmp_path, monkeypatch, capsys, target, surface, retained
):
    import hashlib

    from mcp_fixtures import seed_media_library

    from unfold import cli

    tool, _, _, artifacts = seed_media_library(tmp_path / "library", playable=False)
    artifact, request = artifacts[0], uid()
    receipt = (request if target == "export" else
               hashlib.sha256(("create-export:" + request).encode()).hexdigest()[:32])
    binding = {"artifact_id": artifact} if target == "export" else {
        "operation_id": request,
        "generation_sha256": hashlib.sha256(json.dumps({
            "brief": Brief.model_validate(BRIEF.model_dump()).model_dump(),
            "grant": GRANT.model_dump(),
        }, sort_keys=True).encode()).hexdigest(),
    }
    if retained == "rename":
        tool.retain_mutation_intent("rename", receipt, {"identity": artifact, "name": "New name"})
    else:
        tool.retain_mutation_intent("export-file", receipt, {
            "binding": {"artifact_id": uid()} if retained == "wrong-binding" else binding,
        })
    destination = tmp_path / "result.mp4"
    brief, grant = tmp_path / "brief.json", tmp_path / "grant.json"
    brief.write_text(BRIEF.model_dump_json())
    grant.write_text(GRANT.model_dump_json())

    def forbidden(*args, **kwargs):
        pytest.fail("Conflicting intent reached preflight, mutation or production")

    monkeypatch.setattr(Unfold, "_produce", forbidden)
    monkeypatch.setattr(Unfold, "_mutation", forbidden)
    monkeypatch.setattr("unfold.exports.preflight", forbidden)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    events = tool.observe()
    if surface == "library":
        with pytest.raises(UnfoldError) as error:
            if target == "export":
                tool.export_file(artifact, destination, request)
            else:
                tool.create(BRIEF, GRANT, request_id=request,
                            export_to=destination if target == "create-explicit" else None)
        assert error.value.code == "REQUEST_CONFLICT"
    else:
        command = (["export-file", artifact, str(destination)] if target == "export" else
                   ["create", "--brief", str(brief), "--grant", str(grant)])
        if target == "create-explicit":
            command += ["--export-to", str(destination)]
        monkeypatch.setattr(sys, "argv", ["unfold", "--library", str(tool.store.root),
                                        *command, "--request-id", request])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert json.loads(captured.err)["error"]["code"] == "REQUEST_CONFLICT"
        assert "Traceback" not in captured.err
    assert tool.observe() == events
    assert {str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*") if path.is_file()} == before
    assert not destination.exists()