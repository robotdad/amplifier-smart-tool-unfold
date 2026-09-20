"""Offline grant enforcement against the pinned provider's real recovery loop."""

import asyncio
import json
import os
import stat
from types import SimpleNamespace

import pytest

from unfold import Grant, UnfoldError
from unfold.agent import Gate, bound_openai
from unfold.intelligence import Production
from unfold.store import uid


def production(tmp_path):
    operation = uid()
    owner = Production({
        "library": str(tmp_path), "backend": str(tmp_path), "operation_id": operation,
        "brief": {"duration": 5},
        "grant": Grant(provider="openai", model="gpt-6-astra", allow_context=True,
                       allow_frames=True, vision=True, max_response_tokens=12000).model_dump(),
    })
    owner.store.put("operation", {"id": operation, "status": "running"})
    return owner


@pytest.mark.parametrize("output", [[], [SimpleNamespace(
    type="function_call", status="incomplete", name="production", arguments='{"action":',
    call_id="test", id="test",
)]])
def test_real_provider_incomplete_does_not_retry(tmp_path, output):
    provider_module = pytest.importorskip("amplifier_module_provider_openai")
    from amplifier_core.message_models import ChatRequest, Message, ToolSpec

    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(status="incomplete", output=output, id="test",
                               incomplete_details=SimpleNamespace(reason="max_output_tokens"))

    provider = provider_module.OpenAIProvider(
        api_key="offline-fixture", config={"max_retries": 0},
        coordinator=SimpleNamespace(get_capability=lambda name: None),
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    owner = production(tmp_path)
    bound_openai(provider, owner)
    request = ChatRequest(messages=[Message(role="user", content="Make a shape")],
                          tools=[ToolSpec(name="production", parameters=owner.tool().input_schema)])
    with pytest.raises(UnfoldError) as exc:
        asyncio.run(Gate(provider, owner).complete(request))
    assert exc.value.code == "PROVIDER_INCOMPLETE"
    assert len(calls) == 1 and calls[0]["max_output_tokens"] == 12000
    assert owner.model_calls == 1
    assert len([e for e in owner.store.events() if e["kind"] == "provider_attempt"]) == 1
    assert owner.store.list("revision") == []
    # Even a subsequent orchestrator attempt cannot spend after this fatal outcome.
    with pytest.raises(UnfoldError):
        asyncio.run(Gate(provider, owner).complete(request))
    assert len(calls) == 1


def test_boundary_blocks_raised_limit_and_hidden_second_attempt(tmp_path):
    owner = production(tmp_path)
    calls = []

    async def create(params):
        calls.append(params)
        return SimpleNamespace(status="completed")

    provider = SimpleNamespace(_create_response=create)
    bound_openai(provider, owner)
    params = {"model": owner.grant.model, "max_output_tokens": 12000}
    asyncio.run(provider._create_response(params))
    for tokens in (12000, 128000):
        with pytest.raises(UnfoldError):
            asyncio.run(provider._create_response({**params, "max_output_tokens": tokens}))
    assert len(calls) == 1


def test_rejected_author_is_bounded_private_and_repairable(tmp_path, monkeypatch):
    pytest.importorskip("amplifier_core")
    owner = production(tmp_path)
    monkeypatch.setattr(owner.backend, "require", lambda: None)
    owner.backend.gsap = tmp_path / "gsap.js"
    owner.backend.gsap.write_text("fixture")
    scene = {"title": "Test", "duration": 5, "explanation": "An animated shape",
             "elements": [{"id": "p", "kind": "dot", "x": 0, "y": 0,
                           "width": 100, "height": 100}],
             "tweens": [{"target": "p", "at": 0, "scale": 0.1}]}
    owner.call("author", scene)
    original = (owner.directory / "source/scene.json").read_bytes()
    scene["tweens"][0]["scale"] = 0.05
    tool = owner.tool()
    result = asyncio.run(tool.execute({"action": "author", "payload": scene}))
    assert not result.success and "scale" in str(result.error)
    assert (owner.directory / "source/scene.json").read_bytes() == original
    diagnostic = owner.directory / "rejected-author-1.json"
    assert json.loads(diagnostic.read_text())["payload_excerpt"] == json.dumps(scene)
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
    scene["explanation"] = "\\\"" * 100000
    for _ in range(5):
        asyncio.run(tool.execute({"action": "author", "payload": scene}))
    diagnostics = list(owner.directory.glob("rejected-author-*.json"))
    assert len(diagnostics) == 4 and all(p.stat().st_size <= 65536 for p in diagnostics)
    scene["explanation"] = "Repaired"
    scene["tweens"][0]["scale"] = 0.1
    assert asyncio.run(tool.execute({"action": "author", "payload": scene})).success
    assert owner.renders == 0 and owner.store.list("revision") == []
    schema = tool.input_schema
    assert schema["$defs"]["Tween"]["properties"]["scale"]["anyOf"][0]["minimum"] == 0.1


def test_public_creation_truncation_stops_worker_and_replay(tmp_path, monkeypatch):
    """Exercise the real supervisor/worker boundary with an offline SDK response."""
    import os
    import subprocess

    pytest.importorskip("amplifier_module_provider_openai")
    from unfold import Brief, Unfold

    library = Unfold(tmp_path / "library", tmp_path / "backend")
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    real_popen = subprocess.Popen
    children = []
    script = '''
import sys
from types import SimpleNamespace
from amplifier_core.message_models import ChatRequest, Message
from amplifier_module_provider_openai import OpenAIProvider
from unfold import agent, worker
async def execute(owner):
    async def create(**params):
        return SimpleNamespace(status="incomplete", output=[], id="fixture")
    provider = OpenAIProvider(api_key="offline", config={"max_retries": 0},
        client=SimpleNamespace(responses=SimpleNamespace(create=create)))
    agent.bound_openai(provider, owner)
    await agent.Gate(provider, owner).complete(
        ChatRequest(messages=[Message(role="user", content="Synthetic scene")]))
agent.execute = execute
sys.argv = ["worker", sys.argv[1]]
worker.main()
'''

    def launch(argv, **kwargs):
        child = real_popen([argv[0], "-c", script, argv[-1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    grant = Grant(provider="openai", model="gpt-6-astra", allow_context=True,
                  allow_frames=True, vision=True, max_seconds=30)
    brief = Brief(title="Truncation", intent="Offline worker acceptance")
    request_id = uid()
    result = library.create(brief, grant, request_id=request_id)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "PROVIDER_INCOMPLETE"
    assert len(children) == 1 and children[0].poll() is not None
    assert library.store.list("revision") == []
    assert library.store.list("artifact") == []
    attempts = [e for e in library.observe() if e["kind"] == "provider_attempt"]
    assert len(attempts) == 1
    assert library.create(brief, grant, request_id=request_id) == result
    assert len(children) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


@pytest.mark.skipif(not os.environ.get("UNFOLD_TEST_BACKEND"),
                    reason="Needs renderer and prepared Agent runtime")
def test_real_agent_repairs_renders_reviews_and_retains(tmp_path, monkeypatch):
    import os
    import subprocess
    from pathlib import Path

    pytest.importorskip("amplifier_agent_lib")
    from unfold import Brief, Unfold

    library = Unfold(tmp_path / "library", os.environ["UNFOLD_TEST_BACKEND"])
    original = subprocess.Popen
    fixture = Path(__file__).with_name("provider_worker_fixture.py")

    def launch(argv, **kwargs):
        if "unfold.worker" not in argv:
            return original(argv, **kwargs)
        kwargs["env"]["OPENAI_API_KEY"] = "offline-fixture"
        return original([argv[0], str(fixture), argv[-1]], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    result = library.create(
        Brief(title="Repair", intent="Offline real-runtime acceptance", duration=5),
        Grant(provider="openai", model="gpt-6-astra", allow_context=True,
              allow_frames=True, vision=True, max_model_calls=5, max_seconds=120),
        request_id=uid(),
    )
    assert result["status"] == "completed", result.get("error")
    revision = library.inspect(result["revision_id"])
    assert revision["source_integrity"] == "intact"
    assert len(library.store.list("revision")) == 1
    events = library.observe()
    assert len([e for e in events if e["kind"] == "author_rejected"]) == 1
    assert len([e for e in events if e["kind"] == "provider_attempt"]) == 5
    assert len([e for e in events if e["kind"] == "fixture_provider_revision"]) == 1
    assert list(library.store.root.glob("operations/*/rejected-author-1.json"))
    fresh = Unfold(library.store.root, os.environ["UNFOLD_TEST_BACKEND"])
    exported = fresh.export(revision["artifacts"][0], tmp_path / "export")
    assert Path(exported["path"]).stat().st_size > 0
