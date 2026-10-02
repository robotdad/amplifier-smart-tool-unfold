"""Real Agent 0.20 lifecycle against a loopback Gemini protocol fixture.

No Engine/provider monkeypatches, SDK hooks, paid calls or live credentials.
Renderer doubles prove orchestration, not video quality; JPEG payloads are real.
"""
import asyncio
import base64
import json
import os
import socket
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from unfold import Grant, UnfoldError
from unfold.agent import execute, image_input
from unfold.intelligence import Production
from unfold.store import digest, uid

agent_api = pytest.importorskip("amplifier_agent")


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    replies, requests = deque(), []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"models": [{"name": "models/gemini-2.5-flash",
                "displayName": "Offline fixture", "inputTokenLimit": 1000000,
                "outputTokenLimit": 65536, "supportedGenerationMethods": ["generateContent"]}]}).encode())

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if not replies:
                status, body = 400, {"error": {"code": 400, "message": "fixture exhausted", "status": "INVALID_ARGUMENT"}}
            else:
                item = replies.popleft()
                if callable(item):
                    item = item()
                status, body = item if isinstance(item, tuple) else (200, item)
            self.send_response(status)
            streaming = "streamGenerateContent" in self.path and status == 200
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.end_headers()
            raw = json.dumps(body)
            try:
                self.wfile.write((f"data: {raw}\n\n" if streaming else raw).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass  # Cancellation deliberately closes the client before a late reply.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Isolate ambient host settings and credentials; prove no external connection.
    for key in list(os.environ):
        if key.startswith("AMPLIFIER_") or key.endswith("_API_KEY") or key.lower().endswith("_proxy"):
            monkeypatch.delenv(key, raising=False)
    config = tmp_path / "agent-config.json"
    config.write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(config))
    monkeypatch.setenv("GEMINI_API_KEY", "offline-fixture")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError(f"Non-loopback connection attempted: {address[0]}")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    try:
        yield replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def reply(text="End turn", *, name=None, args=None):
    part = {"functionCall": {"name": name, "args": args}} if name else {"text": text}
    return {"candidates": [{"content": {"role": "model", "parts": [part]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10}}


def production(action, payload=None):
    return reply(name="production", args={"action": action, "payload": json.dumps(payload or {})})


def scene_data():
    return {"title": "Offline fixture", "duration": 2, "output": {"resolution": "720p"},
            "explanation": "Integration fixture, not creative proof",
            "elements": [{"id": "word", "kind": "text", "x": 0, "y": 0,
                          "width": 200, "height": 80, "text": "Retained"}],
            "tweens": [{"target": "word", "at": 0, "duration": 1, "opacity": 1}]}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    owner = Production({"library": str(tmp_path / "library"), "backend": str(tmp_path / "backend"),
        "operation_id": uid(), "brief": {"duration": 2, "output": {"resolution": "720p"}},
        "grant": Grant(provider="gemini", model="gemini-2.5-flash", allow_context=True,
                       allow_frames=True, vision=True).model_dump()})
    owner.store.put("operation", {"id": owner.request["operation_id"], "status": "running"})
    monkeypatch.setattr(owner.backend, "require", lambda: None)
    owner.backend.gsap = tmp_path / "fixture.js"
    owner.backend.gsap.write_text("// fixture, not a real renderer")
    monkeypatch.setattr(owner.backend, "doctor", lambda: {"versions": {"renderer": "offline-double"}})

    def render(source, output):
        output.write_bytes(b"offline render double, NOT playable media")
        return {"sha256": digest(output), "source_sha256": owner.backend.source_hash(source)}

    def frames(video, times, directory):
        directory.mkdir()
        results = []
        for index, time in enumerate(times):
            path = directory / f"{index}.jpg"
            Image.new("RGB", (16, 16), (index * 30, 50, 90)).save(path)
            results.append({"path": str(path), "time": time, "sha256": digest(path)})
        return results

    monkeypatch.setattr(owner.backend, "render", render)
    monkeypatch.setattr(owner.backend, "frames", frames)
    return owner


def author_sample(replies):
    replies.extend([reply(name="author_scene", args=scene_data()), production("render"),
                    production("sample", {"times": [0, 1]}), reply("Ready for image review.")])


def terminals(owner):
    return [event["data"] for event in owner.store.events() if event["kind"] == "agent_terminal"]


def test_real_gemini_two_turn_images_review_and_submission(endpoint, owner):
    replies, requests = endpoint
    config_before = os.environ.get("AMPLIFIER_AGENT_CONFIG")
    author_sample(replies)
    replies.extend([production("submit", {"review": "Reviewed fixture JPEGs", "limitations": []}),
                    reply("Submitted validated work.")])
    result = asyncio.run(execute(owner))
    assert os.environ.get("AMPLIFIER_AGENT_CONFIG") == config_before
    assert result is owner.result
    assert len(requests) == 6 and not replies
    assert [r["state"] for r in terminals(owner)] == ["success", "success"]
    assert terminals(owner)[1]["usage"]["entries"]
    assert result["usage"] == {"tool_calls": 4, "renders": 1, "frames": 2}
    assert len(result["evidence"]) == 1
    reviewed = requests[4]["contents"][-1]
    assert reviewed["role"] == "user"
    images = [p["inlineData"] for p in reviewed["parts"] if "inlineData" in p]
    assert len(images) == 2
    for image, frame in zip(images, result["evidence"][0]["frames"], strict=True):
        assert image["mimeType"] == "image/jpeg"
        assert base64.urlsafe_b64decode(image["data"]) == (owner.store.root / frame["path"]).read_bytes()
    for request in requests[:4]:
        assert "inlineData" not in json.dumps(request)
    assert set(t["name"] for t in requests[0]["tools"][0]["functionDeclarations"]) == {"author_scene", "production"}


def test_real_success_without_submission_is_not_completion(endpoint, owner):
    endpoint[0].append(reply("I completed everything (unsupported prose)."))
    with pytest.raises(UnfoldError) as error:
        asyncio.run(execute(owner))
    assert error.value.code == "NO_SUBMISSION"
    assert owner.result is None
    assert [r["state"] for r in terminals(owner)] == ["success"]


def test_real_admission_error_is_retained_without_terminal_or_request(endpoint, owner):
    # An invalid explicit model is still reported even though ambient config is ignored.
    owner.grant.model = ""
    with pytest.raises(UnfoldError) as error:
        asyncio.run(execute(owner))
    assert error.value.code == "AGENT_FAILED"
    assert not terminals(owner) and not endpoint[1]
    errors = [e["data"] for e in owner.store.events() if e["kind"] == "agent_error"]
    assert len(errors) == 1 and errors[0]["code"] == "invalid_input"


@pytest.mark.parametrize("selection", ["default_home", "explicit_environment"])
def test_public_agent_cannot_inherit_forwarding_or_request_settings(endpoint, owner, monkeypatch, selection):
    received = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    marker = "synthetic-unfold-private-base-title"
    owner.request["base_scene"] = {"title": marker}
    config = Path.home() / ".amplifier-agent" / "config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "context_intelligence": {"destinations": {"fixture": {
            "url": f"http://127.0.0.1:{collector.server_port}", "api_key": "fixture",
            "include": ["**"],
        }}},
        "extra_request_params": {"gemini": {"temperature": 0.123}},
    }))
    if selection == "default_home":
        monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG")
    else:
        monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(config))
    before = os.environ.get("AMPLIFIER_AGENT_CONFIG")

    async def positive_control():
        # Real public API without Unfold's boundary proves the collector/config
        # actually forwards this synthetic tool result; no private hook fixtures.
        async with await agent_api.create_agent(agent_api.AgentOptions(
            provider="gemini", model=owner.grant.model, tools=[owner.tool()],
            skills=[], mcp_servers=[], approvals="allow", storage=str(owner.directory / "control"),
        )) as agent:
            async with await agent.create_session(agent_api.SessionOptions(persistence="ephemeral")) as session:
                result = await session.run(agent_api.TurnInput([agent_api.TextPart("Inspect fixture")]))
                assert result.state == "success"

    try:
        endpoint[0].extend([production("inspect"), reply()])
        asyncio.run(positive_control())
        assert marker.encode() in b"".join(received), "Positive control did not exercise forwarding"
        assert endpoint[1][0]["generationConfig"]["temperature"] == 0.123
        received.clear()
        endpoint[0].extend([production("inspect"), reply()])
        with pytest.raises(UnfoldError, match="without a validated submission"):
            asyncio.run(execute(owner))
        assert not received
        assert endpoint[1][2]["generationConfig"]["temperature"] != 0.123
        assert os.environ.get("AMPLIFIER_AGENT_CONFIG") == before
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("permission", ["allow_context", "allow_frames", "vision"])
def test_missing_disclosure_permission_never_constructs_or_calls_agent(endpoint, owner, permission):
    setattr(owner.grant, permission, False)
    with pytest.raises(UnfoldError) as error:
        asyncio.run(execute(owner))
    assert error.value.code == "DISCLOSURE_DENIED"
    assert not owner.store.events() and not endpoint[1]


def test_worker_retains_actual_no_submission_error(endpoint, owner):
    from unfold.worker import execute_request

    endpoint[0].append(reply("Prose, not validated work."))
    path = owner.directory / "request.json"
    path.write_text(json.dumps(owner.request))
    execute_request(path)
    result = json.loads((owner.directory / "result.json").read_text())
    assert result["error"]["code"] == "NO_SUBMISSION"
    assert [r["state"] for r in terminals(owner)] == ["success"]
    assert not owner.store.list("revision")


def test_real_failure_after_valid_submit_is_not_success(endpoint, owner):
    replies, _ = endpoint
    author_sample(replies)
    replies.extend([production("submit", {"review": "Fixture review"}),
                    (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "fixture failure"}})])
    with pytest.raises(UnfoldError) as error:
        asyncio.run(execute(owner))
    assert error.value.code == "AGENT_FAILED"
    assert owner.result is not None  # staged candidate is NOT returned/committed
    assert [r["state"] for r in terminals(owner)] == ["success", "failure"]
    assert not owner.store.list("revision")
    retained = json.loads((owner.directory / "candidate.json").read_text())
    assert retained["state"] == "validated_candidate"
    assert retained["operation_id"] == owner.request["operation_id"]
    assert retained["candidate"] == owner.result
    assert retained["candidate"]["model_review"] == "Fixture review"
    assert retained["candidate"]["source_sha256"] == owner.backend.source_hash(owner.directory / "source")
    assert retained["candidate"]["render"]["sha256"] == digest(owner.directory / "video.mp4")


def test_worker_failure_retains_candidate_but_never_commits_it(endpoint, owner, monkeypatch):
    from unfold import Unfold
    from unfold.worker import execute_request

    author_sample(endpoint[0])
    endpoint[0].extend([production("submit", {"review": "Durable review", "limitations": ["Fixture"]}),
                       (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "failure"}})])
    # Reuse the fixture's renderer doubles; Agent, worker result writing and recovery
    # are real. No substitution of Agent execution or terminal state.
    monkeypatch.setattr("unfold.intelligence.Production", lambda request: owner)
    path = owner.directory / "request.json"
    path.write_text(json.dumps(owner.request))
    execute_request(path)
    error = json.loads((owner.directory / "result.json").read_text())
    assert error["error"]["code"] == "AGENT_FAILED" and "candidate" not in error
    candidate_path = owner.directory / "candidate.json"
    retained_bytes = candidate_path.read_bytes()
    retained = json.loads(retained_bytes)
    assert retained["candidate"]["model_review"] == "Durable review"
    receipt = next(e["data"] for e in owner.store.events() if e["kind"] == "candidate_retained")
    assert receipt["sha256"] == digest(candidate_path)
    assert receipt["path"] == str(candidate_path.relative_to(owner.store.root))
    assert "Durable review" not in json.dumps(owner.store.events())
    for observation in retained["candidate"]["evidence"]:
        for frame in observation["frames"]:
            assert digest(owner.store.root / frame["path"]) == frame["sha256"]
    library = Unfold(owner.store.root, owner.request["backend"])
    with pytest.raises(UnfoldError) as rejected:
        library._complete_operation(owner.request["operation_id"])
    assert rejected.value.code == "AGENT_FAILED"
    library._finish_operation(owner.request["operation_id"], rejected.value)
    assert library.inspect(owner.request["operation_id"])["status"] == "failed"
    assert not owner.store.list("revision") and not owner.store.list("artifact")
    library._complete_operation(owner.request["operation_id"])
    assert candidate_path.read_bytes() == retained_bytes
    assert not endpoint[0] and len(endpoint[1]) == 6  # Recovery never replays.
    assert [r["state"] for r in terminals(owner)] == ["success", "failure"]


@pytest.mark.parametrize("payload", [
    {}, {"times": None}, {"times": "0"}, {"times": []}, {"times": [True]},
    {"times": ["0"]}, {"times": [2]}, {"times": [float("nan")]},
])
def test_real_malformed_sample_is_failed_and_correctable(endpoint, owner, payload):
    replies, _ = endpoint
    replies.extend([reply(name="author_scene", args=scene_data()), production("render"),
                    production("sample", payload), production("sample", {"times": [0]}),
                    reply("Images next"), production("submit", {"review": "Corrected sample"}), reply()])
    result = asyncio.run(execute(owner))
    assert result["model_review"] == "Corrected sample"
    assert owner.frames == 1 and owner.renders == 1
    resolutions = [e["data"] for e in owner.store.events() if e["kind"] == "agent_tool_result"]
    assert [r["outcome"] for r in resolutions] == ["completed", "completed", "failed", "completed", "completed"]
    assert resolutions[2]["error_code"] == "tool_failed"
    assert [r["state"] for r in terminals(owner)] == ["success", "success"]


def test_real_uncertain_backend_exception_remains_unknown(endpoint, owner, monkeypatch):
    def uncertain(*args):
        raise RuntimeError("Backend effect outcome cannot be determined")

    monkeypatch.setattr(owner.backend, "frames", uncertain)
    endpoint[0].extend([reply(name="author_scene", args=scene_data()), production("render"),
                       production("sample", {"times": [0]}), production("sample", {"times": [0]})])
    with pytest.raises(UnfoldError):
        asyncio.run(execute(owner))
    resolutions = [e["data"] for e in owner.store.events() if e["kind"] == "agent_tool_result"]
    assert resolutions[2]["outcome"] == "unknown"
    assert resolutions[2]["error_code"] == "tool_completion_unknown"
    assert terminals(owner)[0]["state"] == "failure"
    assert owner.result is None and not (owner.directory / "candidate.json").exists()


def test_real_recoverable_validation_and_early_submit(endpoint, owner):
    replies, requests = endpoint
    invalid = scene_data()
    invalid["duration"] = 3  # Schema-valid; violates this operation's requested duration.
    replies.append(reply(name="author_scene", args=invalid))
    author_sample(replies)
    # The early submit is in the authoring turn, before images can be seen.
    end = replies.pop()
    replies.extend([production("submit", {"review": "Invented pixels"}), end,
                    production("submit", {"review": "Now received pixels"}), reply()])
    result = asyncio.run(execute(owner))
    assert result["model_review"] == "Now received pixels"
    assert "Images are waiting" in json.dumps(requests[5])
    assert len(list(owner.directory.glob("rejected-*.json"))) == 1
    assert [r["state"] for r in terminals(owner)] == ["success", "success"]
    resolutions = [e["data"]["outcome"] for e in owner.store.events()
                   if e["kind"] == "agent_tool_result"]
    assert resolutions.count("failed") == 2  # Domain mismatch and premature submit.


def test_real_schema_rejection_preserves_agent_failure(endpoint, owner):
    invalid = scene_data()
    invalid["tweens"][0]["scale"] = 0.01
    endpoint[0].append(reply(name="author_scene", args=invalid))
    with pytest.raises(UnfoldError) as error:
        asyncio.run(execute(owner))
    assert error.value.code == "AGENT_FAILED"
    assert terminals(owner)[0]["state"] == "failure"
    assert terminals(owner)[0]["error"]["code"] == "invalid_input"
    assert owner.calls == 0 and owner.result is None


@pytest.mark.parametrize("method", ["operation", "task"])
def test_real_cancel_has_cancelled_terminal_and_no_commit(endpoint, owner, method):
    replies, _ = endpoint
    config_before = os.environ.get("AMPLIFIER_AGENT_CONFIG")
    entered, release = threading.Event(), threading.Event()

    def delayed():
        entered.set()
        release.wait(10)
        return reply("Too late")

    replies.append(delayed)

    async def run():
        task = asyncio.create_task(execute(owner))
        assert await asyncio.to_thread(entered.wait, 10)
        if method == "operation":
            owner.store.put("operation", {"id": owner.request["operation_id"], "status": "cancelling"})
        else:
            task.cancel()
        try:
            with pytest.raises(UnfoldError if method == "operation" else asyncio.CancelledError) as error:
                await asyncio.wait_for(task, 10)
            if method == "operation":
                assert error.value.code == "CANCELLED"
        finally:
            release.set()

    asyncio.run(run())
    assert os.environ.get("AMPLIFIER_AGENT_CONFIG") == config_before
    assert [r["state"] for r in terminals(owner)] == ["cancelled"]
    assert owner.result is None and not owner.store.list("revision")


def test_real_repair_requires_new_image_turn(endpoint, owner):
    replies, requests = endpoint
    author_sample(replies)
    replies.extend([production("patch", {"elements": {"word": {"text": "Revised"}}}),
                    production("render"), production("sample", {"times": [1]}),
                    reply("Review repaired version next."),
                    production("submit", {"review": "Reviewed repaired version"}), reply()])
    result = asyncio.run(execute(owner))
    assert len(requests) == 10 and not replies
    assert [r["state"] for r in terminals(owner)] == ["success"] * 3
    assert result["usage"] == {"tool_calls": 7, "renders": 2, "frames": 3}
    assert len(result["evidence"]) == 1 and len(result["evidence"][0]["frames"]) == 1
    assert result["evidence"][0]["source_sha256"] == result["source_sha256"]
    assert owner.scene.elements[0].text == "Revised"


def test_real_tool_limit_retains_terminal_and_stops_effects(endpoint, owner):
    replies, _ = endpoint
    owner.grant.max_tool_calls = 1
    replies.extend([production("inspect"), production("inspect"), reply()])
    with pytest.raises(UnfoldError) as error:
        asyncio.run(execute(owner))
    assert error.value.code == "RESOURCE_LIMIT"
    assert owner.calls == 2 and owner.result is None
    assert len(terminals(owner)) == 1  # Never replace the observed terminal with success.


def test_real_deadline_cancels_public_turn(endpoint, owner):
    import time

    from unfold.agent import run_turn

    release = threading.Event()

    def delayed():
        release.wait(10)
        return reply("Too late")

    endpoint[0].append(delayed)

    async def run():
        async with await agent_api.create_agent(agent_api.AgentOptions(
            provider="gemini", model=owner.grant.model, tools=[], skills=[], mcp_servers=[],
            storage=str(owner.directory / "agent"), approvals="deny",
        )) as agent:
            async with await agent.create_session(agent_api.SessionOptions(persistence="ephemeral")) as session:
                with pytest.raises(UnfoldError) as error:
                    await run_turn(owner, session, [agent_api.TextPart("Fixture")], time.monotonic() + 0.3)
                assert error.value.code == "RESOURCE_LIMIT"
    try:
        asyncio.run(run())
    finally:
        release.set()
    assert [r["state"] for r in terminals(owner)] == ["cancelled"]
    assert owner.result is None


def test_render_and_frame_limits_remain_local(owner):
    owner.call("author", json.dumps(scene_data()))
    owner.grant.max_renders = 1
    owner.call("render", "{}")
    with pytest.raises(ValueError, match="Render allowance"):
        owner.call("render", "{}")
    owner.grant.max_frames = 1
    with pytest.raises(ValueError, match="Frame allowance"):
        owner.call("sample", '{"times":[0,1]}')
    assert owner.renders == 1 and owner.frames == 0


def test_actual_public_rejected_turn_is_not_submission(endpoint, owner):
    from unfold.agent import run_turn

    endpoint[0].append(production("inspect"))

    async def run():
        import time

        async with await agent_api.create_agent(agent_api.AgentOptions(
            provider="gemini", model=owner.grant.model, tools=[owner.tool()],
            skills=[], mcp_servers=[], storage=str(owner.directory / "agent"),
            approvals="deny",
        )) as agent:
            async with await agent.create_session(agent_api.SessionOptions(persistence="ephemeral")) as session:
                with pytest.raises(UnfoldError) as error:
                    await run_turn(owner, session, [agent_api.TextPart("Fixture")], time.monotonic() + 10)
                assert error.value.code == "AGENT_REJECTED"
    asyncio.run(run())
    assert [r["state"] for r in terminals(owner)] == ["rejected"]
    assert owner.calls == 0 and owner.result is None


def test_stale_images_and_disclosure_permissions(owner):
    owner.call("author", json.dumps(scene_data()))
    owner.call("render", "{}")
    owner.call("sample", '{"times":[0]}')
    frame = next(iter(owner.observations.values()))["frames"][0]
    Path(frame["path"]).write_bytes(b"tampered")
    with pytest.raises(UnfoldError) as error:
        image_input(owner)
    assert error.value.code == "STALE_EVIDENCE" and not owner.delivered
    owner.grant.allow_frames = False
    with pytest.raises(UnfoldError) as error:
        image_input(owner)
    assert error.value.code == "DISCLOSURE_DENIED"


def test_public_tools_preserve_valid_source_and_private_diagnostics(owner):
    async def run():
        author = owner.author_tool()
        context = agent_api.ToolContext("fixture-call")
        assert json.loads(await author.handler(scene_data(), context))["authored"]
        source_hash = owner.backend.source_hash(owner.directory / "source")
        invalid = scene_data()
        invalid["tweens"][0]["scale"] = 0.01
        with pytest.raises(agent_api.ToolFailed, match="INVALID_SCENE"):
            await author.handler(invalid, context)
        assert owner.backend.source_hash(owner.directory / "source") == source_hash
        invalid["title"] = '\u2603\n"' * 100000
        for _ in range(4):
            with pytest.raises(agent_api.ToolFailed):
                await author.handler(invalid, context)
        records = list(owner.directory.glob("rejected-*.json"))
        assert len(records) == 3 and all(p.stat().st_size <= 65536 for p in records)
        assert "Retained" not in json.dumps(owner.store.events())
        with pytest.raises(agent_api.ToolFailed, match="INVALID_INPUT"):
            await owner.tool().handler({"action": "render"}, context)
    asyncio.run(run())


@pytest.mark.parametrize("action,payload", [
    ("patch", {"elements": None}), ("patch", {"elements": []}),
    ("patch", {"elements": {"word": None}}), ("patch", {"elements": {"word": []}}),
    ("submit", {}), ("submit", {"review": []}),
    ("submit", {"review": "fixture", "limitations": "not a list"}),
    ("limitation", {}), ("limitation", {"reason": 3}),
])
def test_malformed_action_shapes_are_known_failures_without_effects(owner, action, payload):
    async def run():
        with pytest.raises(agent_api.ToolFailed):
            await owner.tool().handler(
                {"action": action, "payload": json.dumps(payload)}, agent_api.ToolContext("fixture")
            )
    asyncio.run(run())
    assert owner.frames == owner.renders == 0
    assert owner.scene is owner.result is owner.fatal is None
    assert not (owner.directory / "candidate.json").exists()


def test_candidate_write_is_confined_and_does_not_overwrite(owner, tmp_path):
    owner.call("author", json.dumps(scene_data()))
    owner.call("render", "{}")
    owner.call("sample", '{"times":[0]}')
    owner.delivered.update(owner.observations)
    other = tmp_path / "unrelated.json"
    other.write_text("untouched")
    candidate_path = owner.directory / "candidate.json"
    candidate_path.symlink_to(other)
    with pytest.raises(UnfoldError):
        owner.call("submit", '{"review":"fixture"}')
    assert other.read_text() == "untouched"
    assert owner.result is None
    # A failed retention attempt does not corrupt live evidence paths either.
    assert all(Path(f["path"]).is_absolute() for e in owner.observations.values() for f in e["frames"])
