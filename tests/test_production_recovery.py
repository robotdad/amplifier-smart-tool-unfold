"""Fresh scripted processes only: no providers, retained user stores, or renderer."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from unfold import Brief, Grant, Unfold, UnfoldError
from unfold.agent import provider_entry
from unfold.credentials import credential, worker_environment
from unfold.processes import owned_process, process_identity, stop_tree
from unfold.store import digest, uid

GRANT = Grant(
    provider="gemini",
    model="explicit-fixture-model",
    allow_context=True,
    allow_frames=True,
    vision=True,
    max_seconds=30,
)
BRIEF = Brief(title="Scripted recovery", intent="Offline lifecycle fixture")

# Loaded only by the fresh processes below through an isolated PYTHONPATH.
# Generation, render and probe are scripted, not claims of real media quality.
SCRIPTED = """
import asyncio, hashlib, json, subprocess, sys
from unfold.backend import Backend
from unfold.credentials import credential
from unfold.processes import process_identity
from unfold.store import digest
import unfold.agent
Backend.require = lambda self: None
Backend.source_hash = lambda self, path: digest(path / 'scene.json')
Backend.probe = lambda self, path: {}
async def execute(owner):
    path = owner.directory
    name, value = credential(owner.grant.provider)
    (path / 'credential-check.json').write_text(json.dumps({
        'source': name,
        'sha256': hashlib.sha256(value.encode()).hexdigest() if value else None,
        'provider': owner.grant.provider, 'model': owner.grant.model,
    }))
    with (path / 'dispatch').open('x') as stream:
        stream.write('one scripted dispatch')
    owner.event('scripted_dispatch', {})
    while not (path / 'release').exists():
        if (path / 'spawn-descendant').exists() and not (path / 'descendant.json').exists():
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
            (path / 'descendant.json').write_text(json.dumps(process_identity(child.pid)))
        await asyncio.sleep(0.02)
    source = path / 'source'
    source.mkdir()
    (source / 'scene.json').write_text('{}')
    (path / 'video.mp4').write_bytes(b'scripted retained media, not a real MP4')
    return {'source_sha256': digest(source / 'scene.json'),
            'render': {'sha256': digest(path / 'video.mp4')},
            'evidence': [], 'limitations': ['scripted fixture']}
unfold.agent.execute = execute
"""

LAUNCH = """
import json, os, sys
from unfold import Unfold
library = Unfold(sys.argv[1], sys.argv[2])
if sys.argv[6] == 'before_spawn':
    import unfold.lib
    unfold.lib.subprocess.Popen = lambda *a, **kw: os._exit(73)
if sys.argv[6] == 'cleanup_denied':
    import unfold.recovery
    def denied(process):
        raise PermissionError('scripted launcher cleanup denial')
    unfold.recovery.stop_tree = denied
result = library.create(json.loads(sys.argv[3]), json.loads(sys.argv[4]),
                        request_id=sys.argv[5])
print(json.dumps(result), flush=True)
"""


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for scripted process evidence")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    tool = Unfold(tmp_path / "library", tmp_path / "backend")
    monkeypatch.setattr(tool.backend, "require", lambda: None)
    monkeypatch.setattr(tool.backend, "source_hash", lambda path: digest(path / "scene.json"))
    monkeypatch.setattr(tool.backend, "probe", lambda path: {})
    fixture = tmp_path / "injection"
    fixture.mkdir()
    (fixture / "sitecustomize.py").write_text(SCRIPTED)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "SYSTEMROOT", "TMPDIR"}
    }
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(fixture), str(Path(__file__).resolve().parents[1] / "src")]
    )
    launchers = []

    def launch(mode="normal"):
        identity = uid()
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                LAUNCH,
                str(tool.store.root),
                str(tool.backend.root),
                BRIEF.model_dump_json(),
                GRANT.model_dump_json(),
                identity,
                mode,
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        launchers.append(process)
        if mode != "before_spawn":
            wait_for(lambda: (tool.store.workspace(identity) / "dispatch").exists())
        else:
            assert process.wait(timeout=10) == 73
        return process, identity

    yield tool, launch, environment
    # Clean up only this fixture's birth-verified processes, including failed tests.
    for operation in tool.store.list("operation"):
        for key in ("worker", "execution_worker"):
            state, process = owned_process(operation.get(key))
            if state == "live":
                stop_tree(process)
        # Fixture-only identity: intentionally not in the operation record. Recovery
        # must not discover it by PID guessing after the execution root disappears.
        descendant = tool.store.workspace(operation["id"]) / "descendant.json"
        if descendant.exists():
            state, process = owned_process(json.loads(descendant.read_text()))
            if state == "live":
                assert stop_tree(process)
            assert owned_process(json.loads(descendant.read_text()))[0] == "absent"
    for process in launchers:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def lose_launcher(process):
    process.kill()
    process.wait(timeout=10)


def worker_gone(tool, identity):
    return owned_process(tool.inspect(identity).get("worker"))[0] == "absent"


def test_lost_launcher_preserves_live_worker_then_commits_result_once(rig, monkeypatch):
    tool, launch, environment = rig
    launcher, identity = launch()
    lose_launcher(launcher)
    before = tool.inspect(identity)
    assert tool.reconcile(identity)["recovery"] == "worker_live"
    assert tool.inspect(identity) == before  # inspect/reconcile-live do not relabel
    assert tool.create(BRIEF, GRANT, request_id=identity) == before
    directory = tool.store.workspace(identity)
    for mode in ([], ["--execute"]):
        duplicate = subprocess.run(
            [sys.executable, "-m", "unfold.worker", *mode, str(directory / "request.json")],
            env=environment,
            capture_output=True,
            timeout=10,
        )
        assert duplicate.returncode == 0, duplicate.stderr
    assert tool.inspect(identity) == before
    (directory / "release").touch()
    wait_for(lambda: worker_gone(tool, identity))
    result_bytes = (directory / "result.json").read_bytes()
    video_hash = digest(directory / "video.mp4")

    # Independent reconcilers must not create two revisions.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(tool.reconcile, [identity, identity]))
    assert results[0] == results[1]
    result = results[0]
    assert result["status"] == "completed"
    assert len(tool.store.list("revision")) == len(tool.store.list("artifact")) == 1
    assert (directory / "result.json").read_bytes() == result_bytes
    assert digest(directory / "video.mp4") == video_hash
    monkeypatch.setattr(tool.backend, "require", lambda: pytest.fail("retry checked renderer"))
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    assert tool.reconcile(identity) == result
    assert tool.cancel(identity) == result
    assert len([e for e in tool.observe() if e["kind"] == "scripted_dispatch"]) == 1
    assert len([e for e in tool.observe() if e["kind"] == "completed"]) == 1
    with pytest.raises(UnfoldError, match="different input"):
        tool.create(BRIEF.model_copy(update={"title": "changed"}), GRANT, request_id=identity)


def test_cancel_request_then_verified_cleanup_independent_operation_unaffected(rig):
    tool, launch, _ = rig
    first, identity = launch()
    second, independent = launch()
    lose_launcher(first)
    independent_before = tool.inspect(independent)
    requested = tool.cancel(identity)
    assert requested["status"] == "cancelling"
    result = tool.reconcile(identity)
    assert result["status"] == "cancelled"
    assert worker_gone(tool, identity)
    assert owned_process(tool.inspect(identity)["execution_worker"])[0] == "absent"
    assert tool.inspect(independent) == independent_before
    assert second.poll() is None
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    (tool.store.workspace(independent) / "release").touch()
    assert second.wait(timeout=10) == 0
    assert tool.inspect(independent)["status"] == "completed"
    assert len(tool.store.list("revision")) == 1


def test_loss_before_spawn_fences_delayed_admission_no_replay(rig):
    tool, launch, environment = rig
    _, identity = launch("before_spawn")
    directory = tool.store.workspace(identity)
    assert (directory / "request.json").exists()
    result = tool.reconcile(identity)
    assert result["status"] == "interrupted"
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    # A delayed already-launched worker cannot spend after the recovery fence.
    child = subprocess.run(
        [sys.executable, "-m", "unfold.worker", str(directory / "request.json")],
        env=environment,
        capture_output=True,
        timeout=10,
    )
    assert child.returncode == 0, child.stderr
    assert not (directory / "dispatch").exists()
    assert tool.inspect(identity) == result


def test_identity_mismatch_never_kills_replacement(rig):
    tool, launch, _ = rig
    _, identity = launch("before_spawn")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        record = tool.inspect(identity)
        record.update(
            worker_pid=unrelated.pid, worker={**process_identity(unrelated.pid), "created": 0}
        )
        tool.store.put("operation", record)
        result = tool.reconcile(identity)
        assert result["status"] == "interrupted"
        assert "identity changed" in result["error"]["message"]
        assert unrelated.poll() is None
        assert tool.create(BRIEF, GRANT, request_id=identity) == result
    finally:
        unrelated.kill()
        unrelated.wait(timeout=10)


def test_legacy_pid_is_unknown_not_a_cleanup_authority(rig):
    tool, launch, _ = rig
    _, identity = launch("before_spawn")
    record = tool.inspect(identity)
    record.pop("launcher")
    record["worker_pid"] = os.getpid()
    tool.store.put("operation", record)
    tool.cancel(identity)
    assert tool.reconcile(identity)["recovery"] == "ownership_unknown"
    assert tool.inspect(identity)["status"] == "cancelling"


def test_legacy_exact_retry_preserves_outcome_across_numeric_json_defaults(rig, monkeypatch):
    tool, launch, _ = rig
    _, identity = launch("before_spawn")
    record = tool.inspect(identity)
    record.pop("input_sha256")
    record.pop("launcher")
    record["brief"]["duration"] = 20
    record.update(status="failed", error={"code": "SCRIPTED", "message": "Retained failure"})
    tool.store.put("operation", record)
    monkeypatch.setattr(tool.backend, "require", lambda: pytest.fail("retry checked renderer"))
    assert tool.create(BRIEF, GRANT, request_id=identity) == record
    assert tool.create(json.loads(BRIEF.model_dump_json()), GRANT, request_id=identity) == record
    assert tool.reconcile(identity) == record
    assert tool.inspect(identity) == record
    assert not (tool.store.workspace(identity) / "dispatch").exists()


def test_cleanup_failure_cannot_be_reported_cancelled(rig, monkeypatch):
    tool, launch, _ = rig
    launcher, identity = launch()
    lose_launcher(launcher)
    # Freeze supervisor so its own cancellation loop cannot win this assertion.
    _, supervisor = owned_process(tool.inspect(identity)["worker"])
    supervisor.suspend()
    try:
        tool.cancel(identity)
        import unfold.recovery

        def denied(process):
            raise PermissionError("scripted denial")

        monkeypatch.setattr(unfold.recovery, "stop_tree", denied)
        assert tool.reconcile(identity)["recovery"] == "cleanup_incomplete"
        assert tool.inspect(identity)["status"] == "cancelling"
    finally:
        supervisor.resume()


def test_lost_review_launcher_uses_same_operation_result(rig):
    tool, launch, _ = rig
    launcher, identity = launch()
    operation = tool.inspect(identity)
    job = {
        "id": uid(),
        "kind": "review_job",
        "status": "running",
        "mode": "create",
        "operation_id": identity,
        "owner": operation["launcher"],
        "pid": launcher.pid,
    }
    tool.store.put("review_job", job)
    lose_launcher(launcher)
    assert tool.review_state()["jobs"][0]["status"] == "running"
    (tool.store.workspace(identity) / "release").touch()
    wait_for(lambda: worker_gone(tool, identity))
    recovered = tool.review_state()["jobs"][0]
    assert recovered["status"] == "completed"
    assert recovered["result"]["revision_id"] == tool.inspect(identity)["revision_id"]


def test_lost_launcher_still_has_original_wall_clock_limit(rig):
    tool, launch, _ = rig
    launcher, identity = launch()
    lose_launcher(launcher)
    # No reconciliation/cancel call: the durable worker must enforce the grant.
    result = wait_for(
        lambda: record if (record := tool.inspect(identity))["status"] == "failed" else None,
        timeout=GRANT.max_seconds + 10,
    )
    assert result["error"]["code"] == "RESOURCE_LIMIT"
    wait_for(lambda: worker_gone(tool, identity))
    assert owned_process(result["execution_worker"])[0] == "absent"
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    assert not tool.store.list("revision")
    assert len([e for e in tool.observe() if e["kind"] == "scripted_dispatch"]) == 1


def test_lost_supervisor_stops_only_verified_execution_child(rig):
    tool, launch, _ = rig
    launcher, identity = launch()
    lose_launcher(launcher)
    operation = tool.inspect(identity)
    _, supervisor = owned_process(operation["worker"])
    supervisor.kill()
    wait_for(lambda: worker_gone(tool, identity))
    assert owned_process(operation["execution_worker"])[0] == "live"
    result = tool.reconcile(identity)
    assert result["status"] == "interrupted"
    assert owned_process(operation["execution_worker"])[0] == "absent"
    assert tool.create(BRIEF, GRANT, request_id=identity) == result


def test_lost_supervisor_with_live_launcher_stops_execution_before_terminal(rig):
    tool, launch, _ = rig
    launcher, identity = launch()
    operation = tool.inspect(identity)
    _, supervisor = owned_process(operation["worker"])
    assert launcher.poll() is None
    assert owned_process(operation["execution_worker"])[0] == "live"
    supervisor.kill()
    stdout, stderr = launcher.communicate(timeout=10)
    assert launcher.returncode == 0, stderr
    result = json.loads(stdout)
    assert owned_process(operation["execution_worker"])[0] == "absent"
    assert result["status"] == "interrupted"
    assert tool.inspect(identity) == result
    assert tool.reconcile(identity) == result
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    assert not tool.store.list("revision")
    assert len([e for e in tool.observe() if e["kind"] == "scripted_dispatch"]) == 1


def test_live_launcher_cleanup_denial_stays_recoverable_without_paid_replay(rig):
    tool, launch, _ = rig
    launcher, identity = launch("cleanup_denied")
    operation = tool.inspect(identity)
    _, supervisor = owned_process(operation["worker"])
    supervisor.kill()
    stdout, stderr = launcher.communicate(timeout=10)
    assert launcher.returncode == 0, stderr
    pending = json.loads(stdout)
    assert pending["status"] == "cancelling"
    assert pending["cleanup_status"] == "pending"
    assert pending["recovery"] == "cleanup_incomplete"
    assert owned_process(operation["execution_worker"])[0] == "live"
    assert tool.inspect(identity) == pending
    assert tool.create(BRIEF, GRANT, request_id=identity) == pending
    assert not any(e["kind"] in {"failed", "interrupted", "cancelled"} for e in tool.observe())
    result = tool.reconcile(identity)
    assert result["status"] == "interrupted"
    assert result["cleanup_status"] == "verified_observed"
    assert owned_process(operation["execution_worker"])[0] == "absent"
    assert tool.reconcile(identity) == result
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    assert len([e for e in tool.observe() if e["kind"] == "scripted_dispatch"]) == 1


def test_lost_execution_root_cannot_certify_orphan_descendant_cleanup(rig):
    tool, launch, _ = rig
    launcher, identity = launch()
    directory = tool.store.workspace(identity)
    (directory / "spawn-descendant").touch()
    wait_for(lambda: (directory / "descendant.json").exists())
    descendant = json.loads((directory / "descendant.json").read_text())
    operation = tool.inspect(identity)
    lose_launcher(launcher)
    _, supervisor = owned_process(operation["worker"])
    supervisor.suspend()
    _, execution = owned_process(operation["execution_worker"])
    execution.kill()
    wait_for(lambda: owned_process(operation["execution_worker"])[0] == "absent")
    assert owned_process(descendant)[0] == "live"
    assert tool.cancel(identity)["status"] == "cancelling"
    result = tool.reconcile(identity)
    assert result["status"] == "interrupted"
    assert result["cleanup_status"] == "unverified"
    assert "descendant" in result["error"]["message"].lower()
    assert owned_process(descendant)[0] == "live"  # no unsafe inferred ownership
    assert worker_gone(tool, identity)
    assert tool.reconcile(identity) == result
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    assert not tool.store.list("revision")


@pytest.mark.parametrize("cancel", [False, True])
def test_uncommittable_result_preserved_without_replay(rig, cancel):
    tool, launch, _ = rig
    launcher, identity = launch()
    lose_launcher(launcher)
    directory = tool.store.workspace(identity)
    (directory / "release").touch()
    wait_for(lambda: worker_gone(tool, identity))
    result_bytes = (directory / "result.json").read_bytes()
    if cancel:
        assert tool.cancel(identity)["status"] == "cancelling"
    else:
        (directory / "video.mp4").write_bytes(b"changed after generation")
    result = tool.reconcile(identity)
    assert result["status"] == ("interrupted" if cancel else "failed")
    if not cancel:
        assert result["error"]["code"] == "STALE_RESULT"
    assert result["retained_result"].endswith("/result.json")
    assert (directory / "result.json").read_bytes() == result_bytes
    assert not tool.store.list("revision")
    assert tool.create(BRIEF, GRANT, request_id=identity) == result
    assert tool.reconcile(identity) == result


def test_cli_passive_reads_reconcile_and_retry_exit_status(rig):
    tool, launch, environment = rig
    _, identity = launch("before_spawn")
    command = [sys.executable, "-m", "unfold", "--library", str(tool.store.root)]

    def cli(*args):
        result = subprocess.run(
            [*command, *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, json.loads(result.stdout)

    assert cli("inspect", identity)[1]["status"] == "running"
    assert tool.inspect(identity)["status"] == "running"
    code, recovered = cli("reconcile", identity)
    assert code == 1 and recovered["status"] == "interrupted"
    assert cli("reconcile", identity) == (code, recovered)
    # CLI consistently exits nonzero when returning a failed terminal operation,
    # even for passive inspect. The record is still emitted on stdout.
    assert cli("inspect", identity) == (1, recovered)
    events = cli("observe", "--after", "0")[1]
    assert [event["kind"] for event in events] == ["started", "interrupted"]
    assert cli("observe", "--after", str(events[-1]["cursor"])) == (0, [])


@pytest.mark.parametrize("gemini", [None, "gemini-fixture"])
def test_alias_reaches_real_isolated_execution_worker(rig, gemini):
    import hashlib

    tool, launch, environment = rig
    environment["GOOGLE_API_KEY"] = "google-fixture"
    if gemini:
        environment["GEMINI_API_KEY"] = gemini
    launcher, identity = launch()
    directory = tool.store.workspace(identity)
    check = json.loads((directory / "credential-check.json").read_text())
    assert check == {
        "source": "GEMINI_API_KEY",
        "sha256": hashlib.sha256((gemini or "google-fixture").encode()).hexdigest(),
        "provider": GRANT.provider,
        "model": GRANT.model,
    }
    (directory / "release").touch()
    assert launcher.wait(timeout=10) == 0
    assert tool.inspect(identity)["status"] == "completed"


@pytest.mark.parametrize(
    "gemini,google,selected,value",
    [
        (None, "google-fixture", "GOOGLE_API_KEY", "google-fixture"),
        ("gemini-fixture", None, "GEMINI_API_KEY", "gemini-fixture"),
        ("gemini-fixture", "google-fixture", "GEMINI_API_KEY", "gemini-fixture"),
        ("", "google-fixture", "GOOGLE_API_KEY", "google-fixture"),
        (None, None, None, None),
    ],
)
def test_credential_aliases_doctor_worker_and_provider_agree(
    tmp_path,
    monkeypatch,
    gemini,
    google,
    selected,
    value,
):
    for name, setting in (("GEMINI_API_KEY", gemini), ("GOOGLE_API_KEY", google)):
        monkeypatch.delenv(name, raising=False)
        if setting is not None:
            monkeypatch.setenv(name, setting)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-fixture")
    tool = Unfold(tmp_path / "library")
    monkeypatch.setattr(tool.backend, "doctor", lambda: {})
    doctor = tool.doctor()
    assert doctor["providers"]["gemini"] == bool(value)
    assert doctor["credential_sources"]["gemini"] == selected
    assert credential("gemini") == (selected, value)
    assert "fixture" not in json.dumps(doctor)
    environment = worker_environment("gemini")
    assert environment.get("GEMINI_API_KEY") == value
    assert "GOOGLE_API_KEY" not in environment
    assert "OPENAI_API_KEY" not in environment
    if value:
        entry = provider_entry(GRANT)
        assert entry["module"] == "provider-gemini"
        assert entry["config"]["api_key"] == value
        assert entry["config"]["default_model"] == GRANT.model
    else:
        with pytest.raises(UnfoldError, match="credential"):
            provider_entry(GRANT)
    # Gemini aliases cannot override an explicit different provider/model.
    other = GRANT.model_copy(update={"provider": "openai", "model": "chosen-openai-model"})
    assert provider_entry(other)["config"]["default_model"] == "chosen-openai-model"
    assert provider_entry(other)["config"]["api_key"] == "unrelated-openai-fixture"
    assert "GEMINI_API_KEY" not in worker_environment("openai")
