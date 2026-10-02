"""Optional standard MCP adapter: schemas, retained collaboration and binary resources."""

import base64
import hashlib
import json
import os
import subprocess
import sys

import pytest
from mcp_fixtures import seed_media_library

from unfold import Brief, Grant, Unfold, UnfoldError
from unfold.store import uid


def test_media_chunk_bounds_integrity_symlinks_and_fresh_store(tmp_path):
    library, _, _, artifacts = seed_media_library(tmp_path / "library", playable=False)
    artifact = artifacts[0]
    info = library.media_info(artifact)
    fresh = Unfold(library.store.root)
    chunks = [
        fresh.read_artifact_chunk(artifact, offset)
        for offset in range(0, info["size"], info["chunk_bytes"])
    ]
    assert b"".join(base64.b64decode(c["data"]) for c in chunks) == bytes(range(256)) * 2048
    assert chunks[-1]["eof"]
    assert fresh.read_artifact_chunk(artifact, info["size"])["data"] == ""
    for offset in (-1, info["size"] + 1, True, 0.5, float("nan")):
        with pytest.raises(UnfoldError):
            fresh.read_artifact_chunk(artifact, offset)
    path = library.store.root / library.store.get(artifact)["relative_path"]
    original = path.read_bytes()
    path.write_bytes(b"changed")
    with pytest.raises(UnfoldError, match="changed"):
        fresh.read_artifact_chunk(artifact)
    external = tmp_path / "outside.mp4"
    external.write_bytes(original)
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(UnfoldError, match="symlink"):
        fresh.read_artifact_chunk(artifact)
    path.unlink()
    path.write_bytes(original)
    parent = path.parent
    moved = parent.with_name(parent.name + "-moved")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(UnfoldError, match="symlink"):
        fresh.media_info(artifact)


def test_media_replacement_after_open_cannot_redirect_transfer(tmp_path, monkeypatch):
    library, _, _, artifacts = seed_media_library(tmp_path / "library", playable=False)
    artifact = artifacts[0]
    path = library.store.root / library.store.get(artifact)["relative_path"]
    original = path.read_bytes()
    external = tmp_path / "private.mp4"
    external.write_bytes(b"not authorized media")
    fdopen = os.fdopen

    def replace_after_open(fd, *args, **kwargs):
        path.rename(path.with_suffix(".old"))
        path.symlink_to(external)
        return fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", replace_after_open)
    # The original descriptor remains authoritative; the replacement is never read.
    assert base64.b64decode(library.read_artifact_chunk(artifact)["data"]) == original[:196608]


def test_shared_view_is_durable_revision_scoped_and_optimistic(tmp_path):
    library, _, revisions, artifacts = seed_media_library(tmp_path, playable=False)
    view = library.save_review_view(
        revisions[0], at=1.5, artifact_id=artifacts[0], expected_version=0
    )
    assert Unfold(tmp_path).review_state()["views"] == [view]
    with pytest.raises(UnfoldError, match="changed"):
        library.save_review_view(revisions[1], expected_version=0)
    for args in (
        {"at": True},
        {"at": float("nan")},
        {"expected_version": True},
        {"expected_version": 1.5},
        {"artifact_id": artifacts[1]},
    ):
        with pytest.raises(UnfoldError):
            library.save_review_view(revisions[0], **args)
    assert library.store.list("operation") == []


def test_async_creation_exact_retries_cancel_and_resume_never_replay(tmp_path, monkeypatch):
    library = Unfold(tmp_path)
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.setattr(library, "_launch_review_job", lambda identity: library.store.get(identity))
    brief = Brief(title="Fixture", intent="No actual generation")
    grant = Grant(
        provider="openai", model="fixture", allow_context=True, allow_frames=True, vision=True
    )
    request = uid()
    accepted = library.submit_creation(brief, grant, request)
    assert library.submit_creation(brief, grant, request) == accepted
    with pytest.raises(UnfoldError, match="different"):
        library.submit_creation(Brief(title="Changed", intent="No work"), grant, request)
    with pytest.raises(UnfoldError, match="disclosure"):
        library.submit_creation(brief, Grant(provider="openai", model="fixture"), uid())
    assert library.cancel_job(request)["status"] == "cancelled"
    monkeypatch.setattr(library, "create", lambda *a, **kw: pytest.fail("Cancelled work replayed"))
    assert library.run_review_job(request)["status"] == "cancelled"
    interrupted = library.submit_creation(brief, grant, uid())
    interrupted["created"] = 0
    library.store.put("review_job", interrupted)
    assert (
        next(j for j in library.review_state()["jobs"] if j["id"] == interrupted["id"])["status"]
        == "interrupted"
    )
    assert library.store.list("operation") == []


def test_async_creation_owned_worker_records_result_once(tmp_path, monkeypatch):
    library = Unfold(tmp_path)
    monkeypatch.setattr(library.backend, "require", lambda: None)
    monkeypatch.setattr(library, "_launch_review_job", lambda identity: library.store.get(identity))
    accepted = library.submit_creation(
        Brief(title="Fixture", intent="No actual generation"),
        Grant(
            provider="openai", model="fixture", allow_context=True, allow_frames=True, vision=True
        ),
        uid(),
    )
    calls = []

    def create(brief, grant, request_id):
        calls.append(request_id)
        return {
            "status": "completed",
            "project_id": "fixture-project",
            "revision_id": "fixture-revision",
        }

    monkeypatch.setattr(library, "create", create)
    completed = library.run_review_job(accepted["id"])
    assert completed["status"] == "completed"
    assert completed["project_id"] == "fixture-project"
    assert library.run_review_job(accepted["id"]) == completed
    assert calls == [accepted["operation_id"]]


def test_mcp_sdk_schema_resources_shared_actions_and_permission(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client
    from mcp.client import advertise
    from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID

    from unfold.mcp import UI_URI, create_server

    async def run():
        library, _, revisions, artifacts = seed_media_library(tmp_path, playable=False)
        async with Client(
            create_server(library),
            extensions=[advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})],
        ) as client:
            tools = (await client.list_tools()).tools
            creation = next(t for t in tools if t.name == "unfold_submit_creation")
            transfer = next(t for t in tools if t.name == "unfold_transfer_info")
            assert not next(
                t for t in tools if t.name == "unfold_cancel_job"
            ).annotations.destructive_hint
            assert not next(
                t for t in tools if t.name == "unfold_save_draft"
            ).annotations.destructive_hint
            assert next(t for t in tools if t.name == "unfold_remove").annotations.destructive_hint
            assert next(
                t for t in tools if t.name == "unfold_media_info"
            ).annotations.read_only_hint
            assert creation.meta["ui"] == {"resourceUri": UI_URI, "visibility": ["model", "app"]}
            assert transfer.meta["ui"] == {"resourceUri": UI_URI, "visibility": ["model", "app"]}
            assert (
                creation.input_schema["$defs"]["Grant"]["properties"]["max_tool_calls"]["maximum"]
                == 60
            )
            assert "max_model_calls" not in creation.input_schema["$defs"]["Grant"]["properties"]
            assert creation.input_schema["$defs"]["Grant"]["additionalProperties"] is False
            html = (await client.read_resource(UI_URI)).contents[0]
            assert html.mime_type == APP_MIME_TYPE
            assert html.meta["ui"]["csp"]["connectDomains"] == []
            assert "<script src=" not in html.text
            denied = await client.call_tool(
                "unfold_submit_creation",
                {
                    "brief": {"title": "A", "intent": "B"},
                    "grant": {"provider": "openai", "model": "fixture"},
                    "request_id": uid(),
                },
            )
            assert denied.is_error
            assert denied.structured_content["error"]["code"] == "MODEL_ACCESS_REQUIRED"
            for name, arguments in (
                (
                    "save_draft",
                    {"revision_id": revisions[0], "text": "Retained feedback", "sequence": 1},
                ),
                (
                    "save_review_view",
                    {
                        "revision_id": revisions[0],
                        "artifact_id": artifacts[0],
                        "at": 2,
                        "expected_version": 0,
                    },
                ),
            ):
                result = await client.call_tool("unfold_" + name, arguments)
                assert not result.is_error, result.content
            assert Unfold(tmp_path).review_state()["drafts"][0]["text"] == "Retained feedback"
            resource = (await client.read_resource(f"unfold://artifact/{artifacts[0]}/0")).contents[
                0
            ]
            assert base64.b64decode(resource.blob) == bytes(range(256)) * 768
            assert library.store.list("operation") == []

    anyio.run(run)


def test_actual_stdio_restart_and_optional_import(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    library, _, revisions, _ = seed_media_library(tmp_path, playable=False)

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "unfold.mcp", "--library", str(tmp_path)],
            env={"PATH": os.environ["PATH"]},
        )
        for text in ("From first connection", "From restarted connection"):
            async with Client(params) as client:
                assert (await client.call_tool("unfold_mcp_status", {})).structured_content[
                    "allow_models"
                ] is False
                result = await client.call_tool(
                    "unfold_save_draft",
                    {
                        "revision_id": revisions[0],
                        "text": text,
                        "sequence": 1 if text.startswith("From first") else 2,
                    },
                )
                assert not result.is_error

    anyio.run(run)
    assert library.review_state()["drafts"][0]["text"] == "From restarted connection"
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import unfold, sys; assert 'mcp' not in sys.modules; assert not any(k.startswith('amplifier') for k in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


def test_actual_stdio_restart_reconciles_a_public_feedback_intent_without_duplication(tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    library, _, revisions, _ = seed_media_library(tmp_path, playable=False)
    request = uid()
    payload = {
        "revision_id": revisions[-1],
        "text": "Retain this exact note across process replacement",
        "request_id": request,
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "unfold.mcp", "--library", str(tmp_path)],
        env={"PATH": os.environ["PATH"]},
    )

    async def run():
        async with Client(params) as client:
            retained = await client.call_tool("unfold_retain_feedback_intent", payload)
            assert not retained.is_error
            accepted = await client.call_tool("unfold_feedback", payload)
            assert not accepted.is_error
            # Deliberately do not acknowledge the delivered receipt before stdio
            # closes; the next presenter must recover the public exact command.
        async with Client(params) as client:
            state = await client.call_tool("unfold_review_state", {})
            intent = state.structured_content["result"]["feedback_intents"][0]
            assert intent["request_id"] == request
            assert not intent.get("acknowledged_at")
            replay = await client.call_tool("unfold_feedback", payload)
            assert not replay.is_error
            acknowledged = await client.call_tool(
                "unfold_acknowledge_feedback_intent", {"request_id": request}
            )
            assert not acknowledged.is_error

    anyio.run(run)
    assert len(library.store.list("feedback")) == 1
    assert library.review_state()["feedback_intents"][0]["acknowledged_at"]


def test_retry_authorization_never_refills_consumed_allowance(tmp_path, monkeypatch):
    library, project, revisions, _ = seed_media_library(tmp_path, playable=False)
    monkeypatch.setattr(library, "_launch_review_job", lambda identity: library.store.get(identity))
    grant = Grant(
        provider="openai", model="fixture", allow_context=True, allow_frames=True, vision=True
    )
    authorization_id = uid()
    receipt = library.authorize_review(project, grant, request_id=authorization_id)
    job = library.submit_refinement(revisions[-1], "Original feedback", uid())
    assert library.review_state()["authorities"][0]["remaining"] == 0
    # A lost authorization response may be retried after work consumed it.
    assert library.authorize_review(project, grant, request_id=authorization_id) == receipt
    assert library.review_state()["authorities"][0]["remaining"] == 0
    assert len([e for e in library.observe() if e["kind"] == "review_authorized"]) == 1
    with pytest.raises(UnfoldError, match="different authorization"):
        library.authorize_review(project, grant, refinements=2, request_id=authorization_id)
    assert library.submit_refinement(revisions[-1], "Original feedback", job["id"]) == job
    assert library.review_state()["authorities"][0]["remaining"] == 0


def test_paid_request_ids_never_overwrite_other_retained_object_kinds(tmp_path, monkeypatch):
    library, project, revisions, artifacts = seed_media_library(tmp_path, playable=False)
    operation = uid()
    library.store.put("operation", {"id": operation, "kind": "operation", "status": "completed"})
    monkeypatch.setattr(
        library.backend, "require", lambda: pytest.fail("Collision reached execution preflight")
    )
    monkeypatch.setattr(
        library, "_launch_review_job", lambda identity: pytest.fail("Collision launched work")
    )
    grant = Grant(
        provider="openai", model="fixture", allow_context=True, allow_frames=True, vision=True
    )
    for identity in (project, revisions[0], artifacts[0], operation):
        original = library.store.get(identity)
        for invoke in (
            lambda: library.submit_creation(
                Brief(title="Collision", intent="No work"), grant, identity
            ),
            lambda: library.submit_refinement(revisions[-1], "No work", identity),
            lambda: library.authorize_review(project, grant, request_id=identity),
        ):
            with pytest.raises(UnfoldError) as caught:
                invoke()
            assert caught.value.code == "REQUEST_CONFLICT"
            assert library.store.get(identity) == original
    assert not library.store.list("review_job")
    assert not library.store.list("review_authorization")
    assert not library.store.list("authority")


def test_nonmodel_effect_receipts_are_exact_and_pending_outcomes_fail_loudly(tmp_path):
    library = Unfold(tmp_path)
    request = uid()
    first = library.save_pack(
        "Retained",
        {"required": "blue"},
        request_id=request,
    )
    assert (
        library.save_pack(
            "Retained",
            {"required": "blue"},
            request_id=request,
        )
        == first
    )
    assert len(library.packs()) == 1
    with pytest.raises(UnfoldError, match="different mutation"):
        library.save_pack("Changed", {"required": "blue"}, request_id=request)
    receipt = library.mutation_status(request)
    assert receipt["status"] == "completed"
    assert receipt["result"]["id"] == first["id"]

    uncertain = uid()
    library.store.put(
        "mutation_receipt",
        {
            "id": uncertain,
            "kind": "mutation_receipt",
            "operation": "remove",
            "fingerprint": hashlib.sha256(
                json.dumps(
                    {"identity": first["id"]}, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "payload": {"identity": first["id"]},
            "status": "pending",
        },
    )
    assert library.mutation_status(uncertain)["status"] == "pending"
    # A caller holding an incomplete operation identity cannot use it to make a
    # fresh destructive request: it receives an explicit uncertain disposition.
    with pytest.raises(UnfoldError) as caught:
        library.remove(first["id"], request_id=uncertain)
    assert caught.value.code == "MUTATION_INCOMPLETE"
    assert library.store.get(first["id"], "pack")["id"] == first["id"]
