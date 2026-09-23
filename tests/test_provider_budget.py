"""Offline wire-level regressions; no live credentials or paid generation."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from unfold.agent import Gate
from unfold.intelligence import Production
from unfold.models import Grant, UnfoldError
from unfold.store import uid


def owner_at(tmp_path):
    owner = Production(dict(library=str(tmp_path), backend=str(tmp_path), operation_id=uid(),
                            brief={"duration": 20}, grant=Grant(provider="openai",
                            model="gpt-6-astra", max_model_calls=2).model_dump()))
    owner.store.put("operation", {"id": owner.request["operation_id"], "status": "running"})
    return owner


@pytest.mark.parametrize("output", [[], [dict(type="function_call", name="author_scene",
    call_id="call_fixture", id="fc_fixture", arguments='{"title":', status="incomplete")]])
def test_real_openai_provider_stops_incomplete_without_retry(tmp_path, monkeypatch, output):
    provider_module = pytest.importorskip("amplifier_module_provider_openai")
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    from amplifier_core.message_models import ChatRequest, Message

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    wire = []

    def respond(request):
        wire.append(json.loads(request.content))
        return httpx.Response(200, json=dict(id="resp_fixture", object="response", created_at=0,
            model="gpt-6-astra", status="incomplete", output=output,
            incomplete_details={"reason": "max_output_tokens"}, usage=None))

    async def run():
        owner = owner_at(tmp_path)
        async with openai.AsyncOpenAI(api_key="offline-fixture", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            # Leave the provider's streaming default ON: Gate must actually override it.
            provider = provider_module.OpenAIProvider(client=client, config={"max_retries": 0})
            gate = Gate(provider, owner)
            request = ChatRequest(messages=[Message(role="user", content="Create a scene")])
            with pytest.raises(UnfoldError) as error:
                await gate.complete(request)
            assert error.value.code == "PROVIDER_INCOMPLETE"
            with pytest.raises(UnfoldError):
                await gate.complete(request)
            assert owner.model_calls == owner.provider_attempts == 1
            assert owner.scene is None and owner.renders == 0
            assert not owner.store.list("revision") and not owner.store.list("artifact")
            events = owner.store.events()
            assert len([e for e in events if e["kind"] == "provider_attempt"]) == 1
        assert len(wire) == 1
        assert wire[0]["max_output_tokens"] == 12000
        assert not wire[0].get("stream", False)

    asyncio.run(run())


def test_guard_blocks_internal_escalation_and_repeated_attempt(tmp_path):
    pytest.importorskip("amplifier_core")
    from amplifier_core.message_models import ChatRequest, Message

    async def run(increase):
        owner = owner_at(tmp_path / str(increase))
        wire = []

        class Provider:
            async def _create_response(self, params):
                wire.append(dict(params))
                return SimpleNamespace(status="completed")

            async def complete(self, request, **kwargs):
                params = {"model": request.model, "max_output_tokens": request.max_output_tokens}
                await self._create_response(params)
                if increase:
                    params["max_output_tokens"] = 128000
                await self._create_response(params)

        gate = Gate(Provider(), owner)
        with pytest.raises(UnfoldError) as error:
            await gate.complete(ChatRequest(messages=[Message(role="user", content="fixture")]))
        assert error.value.code == "RESOURCE_LIMIT"
        assert len(wire) == 1
        assert wire[0]["max_output_tokens"] == 12000
        assert owner.model_calls == owner.provider_attempts == 1

    asyncio.run(run(False))
    asyncio.run(run(True))


def scene_data():
    return dict(title="Private fixture", duration=20, explanation="Fixture",
                elements=[dict(id="shape", kind="text", x=0, y=0, width=200, height=80,
                               text="private supplied text")],
                tweens=[dict(target="shape", at=0, duration=1, scale=0.1)])


def test_rejected_scene_keeps_source_and_repairs_with_bounded_private_diagnostics(tmp_path, monkeypatch):
    pytest.importorskip("amplifier_core")
    import os
    import stat

    owner = owner_at(tmp_path)
    monkeypatch.setattr(owner.backend, "require", lambda: None)
    owner.backend.gsap = tmp_path / "fixture.js"
    owner.backend.gsap.write_text("// test fixture")

    async def run():
        author = owner.author_tool()
        assert author.input_schema["$defs"]["Tween"]["properties"]["scale"]["anyOf"][0]["minimum"] == 0.1
        assert "author" not in owner.tool().input_schema["properties"]["action"]["enum"]
        good = scene_data()
        assert (await author.execute(good)).success
        source_hash = owner.backend.source_hash(owner.directory / "source")
        owner.rendered = {"source_sha256": source_hash}
        owner.delivered.add("valid evidence")
        invalid = scene_data()
        invalid["tweens"][0]["scale"] = 0.05
        rejected = await author.execute(invalid)
        assert not rejected.success
        assert rejected.error["code"] == "INVALID_SCENE"
        assert rejected.error["violations"][0]["loc"] == ["tweens", "0", "scale"]
        assert owner.backend.source_hash(owner.directory / "source") == source_hash
        assert owner.rendered and owner.delivered == {"valid evidence"}
        assert owner.fatal is None
        # Huge UTF-8 and escaping-heavy inputs cannot amplify retained records.
        invalid["title"] = '\u2603\n"' * 100000
        for _ in range(4):
            assert not (await author.execute(invalid)).success
        diagnostics = list(owner.directory.glob("rejected-*.json"))
        assert len(diagnostics) == 3
        for path in diagnostics:
            assert path.stat().st_size <= 65536
            if os.name != "nt":
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
        first = json.loads((owner.directory / "rejected-2.json").read_text())
        assert json.loads(first["payload"])["tweens"][0]["scale"] == 0.05
        assert not first["truncated"]
        assert "private supplied text" not in json.dumps(owner.store.events())
        assert (await author.execute(good)).success
        assert owner.rendered is None and not owner.delivered
        owner.grant.max_tool_calls = owner.calls
        assert not (await author.execute(good)).success
        assert owner.fatal.code == "RESOURCE_LIMIT"

    asyncio.run(run())


def test_real_provider_success_transmits_typed_scene_and_counts_each_call(tmp_path):
    provider_module = pytest.importorskip("amplifier_module_provider_openai")
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    from amplifier_core.message_models import ChatRequest, Message, ToolSpec

    wire = []

    def respond(request):
        wire.append(json.loads(request.content))
        return httpx.Response(200, json=dict(id="resp_fixture", object="response", created_at=0,
            model="gpt-6-astra", status="completed", output=[dict(type="message", id="msg_fixture",
                role="assistant", status="completed", content=[dict(type="output_text",
                    text="fixture", annotations=[])])], usage=dict(input_tokens=5, output_tokens=2,
                    total_tokens=7, input_tokens_details={"cached_tokens": 0},
                    output_tokens_details={"reasoning_tokens": 0})))

    async def run():
        owner = owner_at(tmp_path)
        author = owner.author_tool()
        request = ChatRequest(messages=[Message(role="user", content="fixture")], tools=[
            ToolSpec(name=author.name, description=author.description, parameters=author.input_schema)])
        async with openai.AsyncOpenAI(api_key="offline-fixture", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            gate = Gate(provider_module.OpenAIProvider(client=client, config={"max_retries": 0},
                coordinator=SimpleNamespace(get_capability=lambda key: None)), owner)
            await gate.complete(request)
            await gate.complete(request)
            with pytest.raises(UnfoldError, match="Model-call allowance"):
                await gate.complete(request)
        assert len(wire) == owner.model_calls == owner.provider_attempts == 2
        assert all(p["max_output_tokens"] == 12000 for p in wire)
        assert wire[0]["tools"][0]["strict"] is False
        schema = wire[0]["tools"][0]["parameters"]
        assert schema["properties"]["tweens"]["type"] == "array"
        assert schema["$defs"]["Tween"]["properties"]["scale"]["anyOf"][0]["minimum"] == 0.1

    asyncio.run(run())


def test_invalid_scene_then_truncation_worker_failure_is_terminal_and_idempotent(tmp_path, monkeypatch):
    pytest.importorskip("amplifier_core")
    import subprocess
    import sys

    from unfold import Brief, Unfold

    library = Unfold(tmp_path / "library")
    monkeypatch.setattr(library.backend, "require", lambda: None)
    previous = {"id": uid(), "kind": "project", "name": "Previous work",
                "current_revision": None, "revisions": []}
    library.store.put("project", previous)
    # Run the real worker and supervisor; substitute only model execution, offline.
    code = '''
import sys
from types import SimpleNamespace
from amplifier_core.message_models import ChatRequest, Message
import unfold.agent
from unfold.agent import Gate
from unfold.worker import execute_request
from pathlib import Path
async def execute(owner):
    result = await owner.author_tool().execute({"title":"rejected","duration":20,
        "explanation":"fixture", "elements":[{"id":"shape","kind":"text","x":0,"y":0,
        "width":200,"height":80}], "tweens":[{"target":"shape","at":0,"scale":0.05}]})
    assert not result.success
    class Provider:
        async def _create_response(self, params):
            return SimpleNamespace(status="incomplete")
        async def complete(self, request, **kwargs):
            return await self._create_response({"model":request.model,
                "max_output_tokens":request.max_output_tokens})
    await Gate(Provider(), owner).complete(ChatRequest(messages=[Message(role="user", content="fixture")]))
unfold.agent.execute = execute
execute_request(Path(sys.argv[-1]))
'''
    popen = subprocess.Popen
    children = []

    def offline_worker(argv, **kwargs):
        child = popen([sys.executable, "-c", code, argv[-1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", offline_worker)
    request_id = uid()
    brief = Brief(title="Offline regression", intent="Bound failure")
    grant = Grant(provider="openai", model="gpt-6-astra", allow_context=True,
                  allow_frames=True, vision=True, max_seconds=30)
    result = library.create(brief, grant, request_id=request_id)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "PROVIDER_INCOMPLETE"
    assert all(child.poll() is not None for child in children)
    assert not library.store.list("revision") and not library.store.list("artifact")
    assert library.store.get(previous["id"]) == previous
    assert list(library.store.workspace(request_id).glob("rejected-*.json"))
    assert library.create(brief, grant, request_id=request_id) == result
    assert len(children) == 1


@pytest.mark.parametrize("override", [{"max_output_tokens": 128000}, {"max_output_tokens": None},
    {"model": "different-model"}, {"stream": True}, {"background": True}])
def test_guard_rejects_out_of_scope_first_request_before_transmission(tmp_path, override):
    pytest.importorskip("amplifier_core")
    from amplifier_core.message_models import ChatRequest, Message

    async def run():
        owner = owner_at(tmp_path)

        class Provider:
            async def _create_response(self, params):
                pytest.fail("Out-of-scope request reached transport")

            async def complete(self, request, **kwargs):
                return await self._create_response({"model": request.model,
                    "max_output_tokens": request.max_output_tokens, **override})

        with pytest.raises(UnfoldError) as error:
            await Gate(Provider(), owner).complete(
                ChatRequest(messages=[Message(role="user", content="fixture")]))
        assert error.value.code == "RESOURCE_LIMIT"
        assert owner.model_calls == 1 and owner.provider_attempts == 0

    asyncio.run(run())


def test_real_provider_transport_failure_does_not_retry(tmp_path):
    provider_module = pytest.importorskip("amplifier_module_provider_openai")
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    from amplifier_core.message_models import ChatRequest, Message

    wire = []

    def respond(request):
        wire.append(json.loads(request.content))
        return httpx.Response(500, json={"error": {"message": "offline failure"}})

    async def run():
        owner = owner_at(tmp_path)
        async with openai.AsyncOpenAI(api_key="offline-fixture", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            gate = Gate(provider_module.OpenAIProvider(client=client,
                        config={"max_retries": 0}), owner)
            with pytest.raises(UnfoldError) as error:
                await gate.complete(ChatRequest(messages=[Message(role="user", content="fixture")]))
            assert error.value.code == "PROVIDER_ERROR"
            assert len(wire) == owner.provider_attempts == owner.model_calls == 1

    asyncio.run(run())


def test_malformed_production_call_returns_recoverable_error(tmp_path):
    pytest.importorskip("amplifier_core")
    owner = owner_at(tmp_path)
    result = asyncio.run(owner.tool().execute({"action": "render"}))
    assert not result.success and result.error["code"] == "INVALID_INPUT"
    assert owner.renders == 0 and owner.fatal is None
