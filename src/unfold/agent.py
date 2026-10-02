"""Scoped creative execution using only Amplifier Agent's public Python API."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .credentials import CREDENTIALS, credential
from .models import Scene, UnfoldError


@contextmanager
def neutral_agent_config(directory):
    """Select only Unfold-owned host settings in the isolated execution child.

    This private adapter runs once per worker process, not in the caller's process.
    Keep the explicit selection for the whole Agent lifetime, then restore it even
    on failure/cancellation. HOME's default config must never enable forwarding or
    inject provider request settings into an Unfold grant.
    """
    path = Path(directory) / "config.json"
    path.write_text('{"context_intelligence":{"destinations":{}},"extra_request_params":{}}')
    previous = os.environ.get("AMPLIFIER_AGENT_CONFIG")
    os.environ["AMPLIFIER_AGENT_CONFIG"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AMPLIFIER_AGENT_CONFIG", None)
        else:
            os.environ["AMPLIFIER_AGENT_CONFIG"] = previous


def identity_asset_metadata(resources):
    """Disclose only authoring metadata, never local paths, hashes or media bytes."""
    return {i: {k: a[k] for k in ("name", "role", "font") if k in a}
            for i, a in resources.items()}


def production_prompt(owner):
    prompt = files("unfold").joinpath("resources/production.md").read_text()
    prompt += "\n" + files("unfold").joinpath("resources/scene3d_guide.md").read_text()
    prompt += "\nSCENE SCHEMA:\n" + json.dumps(Scene.model_json_schema())
    prompt += "\nINPUT DATA:\n" + json.dumps(
        {key: owner.request.get(key) for key in (
            "brief", "feedback", "base_scene", "identity_semantics", "identity_provenance",
            "selected_identity",
        )}
    )
    prompt += "\nAVAILABLE IDENTITY ASSETS (image/video asset_id; text/card font_asset_id):\n"
    prompt += json.dumps(identity_asset_metadata(owner.request.get("resources", {})))
    return prompt


def image_input(owner):
    """Read/hash the exact disclosed bytes. Images are user input, never tool output."""
    from amplifier_agent import ImagePart, TextPart

    if not (owner.grant.allow_context and owner.grant.allow_frames and owner.grant.vision):
        raise UnfoldError("DISCLOSURE_DENIED", "Context and visual disclosure require permission.")
    pending = owner.observations.keys() - owner.delivered
    parts = []
    for key in sorted(pending):
        observation = owner.observations[key]
        for frame in observation["frames"]:
            raw = Path(frame["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != frame["sha256"]:
                raise UnfoldError("STALE_EVIDENCE", "Sample changed before disclosure.")
            parts.extend([
                TextPart(f"Evidence {key}, {observation.get('method', 'encoded video')}, "
                         f"at composition time {frame['time']} seconds. "
                         "Sampling gaps remain unobserved."),
                ImagePart(media_type="image/jpeg", data=base64.b64encode(raw).decode()),
            ])
    return parts, pending


def active_error(owner, deadline):
    if owner.store.get(owner.request["operation_id"], "operation")["status"] != "running":
        return UnfoldError("CANCELLED", "Operation is no longer active.")
    if time.monotonic() >= deadline:
        return UnfoldError("RESOURCE_LIMIT", "Operation wall-clock allowance exhausted.")
    return owner.fatal


async def run_turn(owner, session, content, deadline):
    """Observe the real terminal event, including when asking the turn to cancel."""
    from amplifier_agent import TurnInput

    if error := active_error(owner, deadline):
        raise error
    turn = await session.start_turn(TurnInput(content))
    owner.event("agent_turn_started", {"session_id": turn.info.session_id,
                                      "turn_id": turn.info.turn_id})
    stop = None

    async def watch():
        nonlocal stop
        while True:
            stop = active_error(owner, deadline)
            if stop:
                await turn.cancel()
                return
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(watch())
    terminal = None

    async def observe():
        nonlocal terminal
        async for event in turn.events():
            if event.type == "tool_result":
                resolution = event.payload.resolution
                owner.event("agent_tool_result", {
                    "turn_id": event.turn_id, "call_id": resolution.call_id,
                    "outcome": resolution.outcome,
                    "error_code": resolution.error.code if resolution.error else None,
                })
            if event.type == "terminal":
                terminal = event.payload
                # Keep the actual Agent outcome, not an invented submission success.
                owner.event("agent_terminal", {
                    "session_id": event.session_id, "turn_id": event.turn_id,
                    "state": terminal.state,
                    "error": ({"code": terminal.error.code, "category": terminal.error.category,
                               "retryable": terminal.error.retryable}
                              if terminal.error else None),
                    "usage": json.loads(json.dumps(asdict(terminal.usage), default=str))
                             if terminal.usage else None,
                })

    observer = asyncio.create_task(observe())
    try:
        await asyncio.shield(observer)
    except asyncio.CancelledError:
        await turn.cancel()
        # Task cancellation must not kill the sole event consumer or hide terminal.
        await asyncio.shield(observer)
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    if terminal is None:
        raise UnfoldError("AGENT_INTERRUPTED", "Agent ended without a terminal outcome.")
    if stop or (error := active_error(owner, deadline)):
        raise stop or error
    if terminal.state != "success":
        code = {"cancelled": "CANCELLED", "rejected": "AGENT_REJECTED"}.get(
            terminal.state, "AGENT_FAILED"
        )
        detail = f" ({terminal.error.code})" if terminal.error else ""
        raise UnfoldError(code, f"Agent turn ended with {terminal.state}{detail}.",
                          "Inspect agent_terminal events. No automatic retry was made.")
    return terminal


async def execute(owner):
    """Translate admission errors without inventing a terminal or replaying a turn."""
    try:
        from amplifier_agent import AgentError
    except ImportError:
        raise UnfoldError(
            "MISSING_PREREQUISITE", "Amplifier Agent is unavailable.", "Install the smart extra."
        ) from None
    try:
        return await _execute(owner)
    except AgentError as exc:
        owner.event("agent_error", {"code": exc.code, "category": exc.category,
                                   "correlation_id": exc.correlation_id})
        raise UnfoldError("AGENT_FAILED", f"Agent API error ({exc.code}).", exc.remedy) from None


async def _execute(owner):
    from amplifier_agent import AgentOptions, SessionOptions, TextPart, create_agent

    if not (owner.grant.allow_context and owner.grant.allow_frames and owner.grant.vision):
        raise UnfoldError("DISCLOSURE_DENIED", "Context and visual disclosure require permission.")
    if not credential(owner.grant.provider)[1]:
        raise UnfoldError("PROVIDER_UNAVAILABLE", "Selected provider credential is unavailable.",
                          "Set " + " or ".join(CREDENTIALS[owner.grant.provider]) + ".")
    deadline = time.monotonic() + owner.grant.max_seconds
    if error := active_error(owner, deadline):
        raise error
    # No ambient tools, skills, MCP servers or durable Agent sessions. Only Unfold's
    # library-controlled capabilities receive approval, within the existing grant.
    with tempfile.TemporaryDirectory(prefix="unfold-agent-") as directory, neutral_agent_config(directory):
        async with await create_agent(AgentOptions(
            provider=owner.grant.provider, model=owner.grant.model,
            tools=[owner.author_tool(), owner.tool()], skills=[], mcp_servers=[],
            storage=directory, approvals="allow", tool_error_policy="continue",
        )) as agent:
            async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
                prompt = production_prompt(owner)
                while True:
                    images, pending = image_input(owner)
                    # Only this user turn receives these verified samples. A failed or
                    # rejected turn cannot turn a tool's proposed result into completion.
                    owner.delivered.update(pending)
                    try:
                        await run_turn(owner, session, [TextPart(prompt), *images], deadline)
                    except BaseException:
                        owner.delivered.difference_update(pending)
                        raise
                    if owner.result is not None:
                        return owner.result
                    if not owner.observations.keys() - owner.delivered:
                        raise UnfoldError("NO_SUBMISSION", "The agent ended without a validated submission.")
                    prompt = (
                        "Inspect the attached sampled images. Repair if needed, then render/sample "
                        "again, or submit a review and limitations for the unchanged rendered source. "
                        "After sampling, end the turn to receive new images. Local allowances: "
                        + json.dumps(owner.remaining())
                    )
