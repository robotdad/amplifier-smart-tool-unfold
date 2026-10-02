import os
import subprocess
import sys
from pathlib import Path

import pytest

from unfold import Grant, Unfold, UnfoldError
from unfold.backend import Backend
from unfold.intelligence import Production
from unfold.models import Scene
from unfold.store import digest, uid


@pytest.fixture
def scene():
    return Scene.model_validate(
        {
            "title": "A test",
            "duration": 20,
            "elements": [
                {
                    "id": "title",
                    "kind": "text",
                    "x": 40,
                    "y": 50,
                    "width": 800,
                    "height": 100,
                    "text": "Caller → library",
                }
            ],
            "tweens": [{"target": "title", "at": 0, "duration": 1, "opacity": 1}],
            "explanation": "A caller hands an intent to the library.",
        }
    )


@pytest.fixture
def saved(tmp_path):
    tool = Unfold(tmp_path / "library", tmp_path / "backend")
    project, revision, artifact, operation = [uid() for _ in range(4)]
    directory = tool.store.workspace(operation)
    source = directory / "source"
    source.mkdir()
    for name in ("index.html", "gsap.min.js", "scene.json"):
        (source / name).write_text("retained test fixture " + name)
    video = directory / "video.mp4"
    video.write_bytes(b"known original video fixture")
    tool.store.put(
        "project",
        {
            "id": project,
            "kind": "project",
            "name": "Before",
            "current_revision": revision,
            "revisions": [revision],
        },
    )
    tool.store.put(
        "revision",
        {
            "id": revision,
            "kind": "revision",
            "project_id": project,
            "source": str(source.relative_to(tool.store.root)),
            "source_sha256": tool.backend.source_hash(source),
            "artifacts": [artifact],
        },
    )
    tool.store.put(
        "artifact",
        {
            "id": artifact,
            "kind": "artifact",
            "revision_id": revision,
            "name": "Before",
            "relative_path": str(video.relative_to(tool.store.root)),
            "sha256": digest(video),
        },
    )
    return tool, project, revision, artifact, video


def test_rename_reopen_export_preserves_content_and_identity(saved, tmp_path):
    tool, project, revision, artifact, video = saved
    before = video.read_bytes()
    source_before = tool.inspect(revision)["source_sha256"]
    tool.rename(artifact, "After")
    fresh = Unfold(tool.store.root)
    assert fresh.artifact(artifact)["download_name"] == "After.mp4"
    assert fresh.inspect(revision)["source_sha256"] == source_before
    assert fresh.projects()[0]["current_revision"] == revision
    exported = fresh.export(artifact, tmp_path / "exports")
    assert Path(exported["path"]).read_bytes() == before == video.read_bytes()
    with pytest.raises(UnfoldError, match="overwrite"):
        fresh.export(artifact, tmp_path / "exports")
    assert [e["kind"] for e in fresh.observe()] == ["renamed", "exported"]


def test_tampered_source_blocks_export_and_render_without_rewriting(saved, tmp_path):
    tool, _, revision, artifact, _ = saved
    path = Path(tool.inspect(revision)["source_path"]) / "index.html"
    path.write_text("externally changed")
    assert tool.inspect(revision)["source_integrity"] == "changed_or_missing"
    with pytest.raises(UnfoldError):
        tool.export(artifact, tmp_path / "exports")
    with pytest.raises(UnfoldError):
        tool.render(revision)
    assert path.read_text() == "externally changed"


def test_missing_output_is_not_api_deletion(saved):
    tool, _, revision, artifact, video = saved
    video.unlink()
    assert tool.artifact(artifact)["integrity"] == "changed_or_missing"
    assert not tool.observe()


def test_feedback_remains_revision_bound_and_does_not_start_work(saved):
    tool, _, revision, _, _ = saved
    feedback = tool.feedback(revision, "Explain the handoff more clearly.")
    assert feedback["revision_id"] == revision and feedback["status"] == "pending"
    assert tool.store.list("operation") == []
    assert tool.observe()[0]["kind"] == "feedback_submitted"


def test_deterministic_installed_import_does_not_load_agent(tmp_path):
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(x in k for x in ("API_KEY", "TOKEN", "SECRET"))
    }
    code = "from unfold import Unfold; from unfold.help import manifest; import sys; assert manifest()['name']=='unfold'; assert not any(m.startswith('amplifier') for m in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True
    )
    assert result.returncode == 0, result.stderr


def test_scene_rejects_code_paths_nonfinite_and_invalid_references(scene):
    for modification in (
        {"script": "fetch('https://evil')"},
        {"duration": float("nan")},
        {"tweens": [{"target": "absent", "at": 0}]},
    ):
        with pytest.raises(ValueError):
            Scene.model_validate({**scene.model_dump(), **modification})


def test_authored_text_is_escaped_not_executable(scene, tmp_path, monkeypatch):
    backend = Backend(tmp_path)
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.gsap.write_text("/* fixture resource */")
    scene.elements[0].text = "</div><script>fetch('secret')</script>"
    backend.author(scene, tmp_path / "source")
    html = (tmp_path / "source/index.html").read_text()
    assert "<script>fetch" not in html
    assert "&lt;script&gt;fetch" in html
    assert "connect-src 'none'" in html


def test_production_requires_current_delivered_observations(tmp_path, scene, monkeypatch):
    operation = uid()
    request = {
        "library": str(tmp_path),
        "backend": str(tmp_path),
        "operation_id": operation,
        "brief": {"duration": 20},
        "grant": Grant(
            provider="gemini", model="test", allow_context=True, allow_frames=True, vision=True
        ).model_dump(),
    }
    owner = Production(request)
    owner.store.put("operation", {"id": operation, "status": "running"})
    with pytest.raises(ValueError, match="Render and sample"):
        owner.call("submit", '{"review":"looks good"}')
    monkeypatch.setattr(owner.backend, "require", lambda: None)
    owner.backend.gsap = tmp_path / "gsap.js"
    owner.backend.gsap.write_text("fixture")
    owner.call("author", scene.model_dump_json())
    owner.rendered = {"source_sha256": owner.backend.source_hash(owner.directory / "source")}
    owner.delivered.add("old-observation")
    owner.call("author", scene.model_dump_json())
    assert owner.rendered is None and not owner.delivered
    with pytest.raises(ValueError, match="Render and sample"):
        owner.call("submit", '{"review":"looks good"}')


def test_reused_request_never_reexecutes(saved, monkeypatch):
    from unfold import Brief

    tool, _, _, _, _ = saved
    monkeypatch.setattr(tool.backend, "require", lambda: None)
    brief = Brief(title="Retry", intent="Test")
    grant = Grant(
        provider="gemini", model="test", allow_context=True, allow_frames=True, vision=True
    )
    # A stopped worker produces a failure. The acknowledged failure must still be idempotent.
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("fixture"))
    )
    identity = uid()
    first = tool.create(brief, grant, request_id=identity)
    assert first["status"] == "failed"
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("Retry started a new worker")
    )
    assert tool.create(brief, grant, request_id=identity) == first
    with pytest.raises(UnfoldError, match="different input"):
        tool.create(Brief(title="Other", intent="different"), grant, request_id=identity)


def test_cancel_reports_request_then_preserves_completed(saved):
    tool, _, _, _, _ = saved
    operation = {"id": uid(), "status": "running"}
    tool.store.put("operation", operation)
    assert tool.cancel(operation["id"])["status"] == "cancelling"
    operation["status"] = "completed"
    tool.store.put("operation", operation)
    assert tool.cancel(operation["id"])["status"] == "completed"


@pytest.mark.parametrize("field", [
    "max_model_calls", "max_text_bytes", "max_image_bytes", "max_response_tokens",
])
def test_removed_inference_controls_are_actionable_errors(field):
    from unfold import Grant

    with pytest.raises(ValueError, match="Unsupported inference controls.*Remove these fields"):
        Grant.model_validate({"provider": "gemini", "model": "fixture", field: 1000})
    assert field not in Grant.model_json_schema()["properties"]


def test_feedback_cannot_claim_unrelated_revision(saved):
    tool, project, revision, _, _ = saved
    note = tool.feedback(revision, "Clarify the boundary")
    unrelated = {
        "id": uid(),
        "kind": "revision",
        "project_id": project,
        "base_revision": revision,
        "feedback": "different request",
    }
    tool.store.put("revision", unrelated)
    with pytest.raises(UnfoldError, match="does not carry"):
        tool.address_feedback(note["id"], unrelated["id"])
    unrelated["feedback"] = note["text"]
    tool.store.put("revision", unrelated)
    result = tool.address_feedback(note["id"], unrelated["id"])
    assert result["status"] == "addressed"
    assert tool.address_feedback(note["id"], unrelated["id"]) == result


def test_cancel_stops_worker_before_terminal_status(tmp_path, monkeypatch):
    import threading
    import time

    from unfold import Brief

    tool = Unfold(tmp_path)
    monkeypatch.setattr(tool.backend, "require", lambda: None)
    popen = subprocess.Popen
    children = []

    def slow_worker(argv, **kwargs):
        child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", slow_worker)
    operation = uid()
    result = []
    grant = Grant(
        provider="gemini", model="unused", allow_context=True, allow_frames=True, vision=True
    )
    thread = threading.Thread(
        target=lambda: result.append(
            tool.create(
                Brief(title="Cancel", intent="Cancel before creative execution"),
                grant,
                request_id=operation,
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not children and time.monotonic() < deadline:
        time.sleep(0.01)
    assert children
    tool.cancel(operation)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result[0]["status"] == "cancelled"
    assert children[0].poll() is not None
    assert tool.store.list("revision") == []


def test_renderer_rejects_injected_source_without_replacing_it(scene, tmp_path, monkeypatch):
    backend = Backend(tmp_path)
    monkeypatch.setattr(backend, "require", lambda: None)
    backend.gsap = tmp_path / "gsap.js"
    backend.gsap.write_text("/* fixture */")
    source = tmp_path / "source"
    backend.author(scene, source)
    path = source / "index.html"
    tampered = path.read_text() + '<script>fetch("https://untrusted.example")</script>'
    path.write_text(tampered)
    with pytest.raises(UnfoldError, match="no code was executed"):
        backend.render(source, tmp_path / "video.mp4")
    assert path.read_text() == tampered
    assert not (tmp_path / "video.mp4").exists()
