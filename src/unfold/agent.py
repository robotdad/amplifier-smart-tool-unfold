"""Isolated Amplifier Agent execution with bounded provider disclosure.

The Engine embedding follows the same protocol boundary used by sibling Outtake;
only Unfold's production capability is mounted in the creative session.
"""

import base64
import copy
import json
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from .credentials import CREDENTIALS, credential
from .models import Scene, UnfoldError
from .store import digest

PROVIDERS = {
    "openai": ("9831ad3c0221bc6dac5dc717122ef0834af0f77e", "OPENAI_API_KEY"),
    "anthropic": ("8a1f837730049b80340a3a2819f48f6563b10368", "ANTHROPIC_API_KEY"),
    "gemini": ("a7c5e0ceb11251499009d77b7993b9bbb375be48", "GEMINI_API_KEY"),
}


class Finished(BaseException):
    pass


def disclosure_size(value):
    """Count encoded image payloads separately, including retained history on retries."""
    image_bytes = 0

    def scrub(item):
        nonlocal image_bytes
        if isinstance(item, list):
            return [scrub(child) for child in item]
        if isinstance(item, dict):
            if item.get("type") == "base64" and str(item.get("media_type", "")).startswith(
                "image/"
            ):
                image_bytes += len(item.get("data", "").encode())
                return {**item, "data": "[image bytes counted separately]"}
            return {key: scrub(child) for key, child in item.items()}
        return item

    text_bytes = len(json.dumps(scrub(value), ensure_ascii=False).encode())
    return text_bytes, image_bytes


class Gate:
    def __init__(self, provider, production):
        self.inner, self.owner = provider, production
        self.attempted = False
        if production.grant.provider == "openai":
            # This seam belongs to the pinned OpenAI provider above. Its normal
            # retry configuration does not govern truncation auto-continuation.
            create = getattr(provider, "_create_response", None)
            if not callable(create):
                raise UnfoldError("PROVIDER_CONFIGURATION", "OpenAI request boundary unavailable.")

            async def bounded_response(params):
                owner = self.owner
                if owner.fatal:
                    raise owner.fatal
                limit = params.get("max_output_tokens")
                if (self.attempted or type(limit) is not int or
                        not 0 < limit <= owner.grant.max_response_tokens or
                        params.get("model") != owner.grant.model or
                        params.get("background") or params.get("stream")):
                    owner.fatal = UnfoldError(
                        "RESOURCE_LIMIT", "Provider attempted a request outside the grant.",
                        "No additional request was sent. Authorize a new operation if needed.",
                    )
                    raise owner.fatal
                self.attempted = True
                owner.provider_attempts += 1
                owner.event("provider_attempt", {
                    "number": owner.provider_attempts, "model_call": owner.model_calls,
                    "provider": "openai", "max_response_tokens": limit,
                })
                # Scene uses optional defaults and tuple schemas outside OpenAI's
                # strict subset. Expose its structure without implicit strict-mode
                # normalization; library validation remains the acceptance boundary.
                params = {**params, "tools": [
                    {**tool, "strict": False} if tool.get("type") == "function" else tool
                    for tool in params.get("tools", [])
                ]} if "tools" in params else params
                response = await create(params)
                if getattr(response, "status", None) != "completed":
                    owner.fatal = UnfoldError(
                        "PROVIDER_INCOMPLETE", "OpenAI returned an incomplete response.",
                        "No continuation or larger-token retry was sent. Simplify the request "
                        "or authorize a new operation with an appropriate allowance.",
                    )
                    owner.event("provider_incomplete", {"attempt": owner.provider_attempts})
                    raise owner.fatal
                return response

            provider._create_response = bounded_response

    def __getattr__(self, name):
        if name == "stream":
            raise AttributeError(name)
        return getattr(self.inner, name)

    async def complete(self, request, **kwargs):
        from amplifier_core.message_models import Message

        owner = self.owner
        if owner.fatal:
            raise owner.fatal
        if owner.result is not None:
            raise Finished()
        if owner.model_calls >= owner.grant.max_model_calls:
            owner.fatal = UnfoldError("RESOURCE_LIMIT", "Model-call allowance exhausted.")
            raise owner.fatal
        pending = {
            key: val for key, val in owner.observations.items() if key not in owner.delivered
        }
        content = [
            {"type": "text", "text": "Remaining allowance: " + json.dumps(owner.remaining())}
        ]
        image_bytes = 0
        for key, observation in pending.items():
            for frame in observation["frames"]:
                path = Path(frame["path"])
                if digest(path) != frame["sha256"]:
                    raise UnfoldError("STALE_EVIDENCE", "Sample changed before disclosure.")
                encoded = base64.b64encode(path.read_bytes()).decode()
                image_bytes += len(encoded)
                content += [
                    {
                        "type": "text",
                        "text": f"Evidence {key}, {observation.get('method', 'encoded video')}, at composition time {frame['time']} seconds. Sampling gaps remain unobserved.",
                    },
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
                    },
                ]
        text_bytes, historical_images = disclosure_size(request.model_dump(mode="json"))
        new_text, new_images = disclosure_size(content)
        text_bytes += new_text
        image_bytes = historical_images + new_images
        if (
            owner.text_bytes + text_bytes > owner.grant.max_text_bytes
            or owner.image_bytes + image_bytes > owner.grant.max_image_bytes
        ):
            owner.fatal = UnfoldError("RESOURCE_LIMIT", "Provider disclosure allowance exhausted.")
            raise owner.fatal
        owner.model_calls += 1
        self.attempted = False
        owner.text_bytes += text_bytes
        owner.image_bytes += image_bytes
        owner.event(
            "model_call",
            {
                "number": owner.model_calls,
                "provider": owner.grant.provider,
                "model": owner.grant.model,
                "image_count": sum(len(v["frames"]) for v in pending.values()),
                "text_bytes_total": owner.text_bytes,
                "image_bytes_total": owner.image_bytes,
            },
        )
        request = request.model_copy(
            update={
                "messages": [*request.messages, Message(role="user", content=content)],
                "model": owner.grant.model,
                "max_output_tokens": owner.grant.max_response_tokens,
                "stream": False,
                "metadata": {**(request.metadata or {}), "stream": False},
            }
        )
        if owner.grant.provider == "openai":
            kwargs = {**kwargs, "model": owner.grant.model, "background": False}
        try:
            response = await self.inner.complete(request, **kwargs)
        except Exception:
            owner.fatal = owner.fatal or UnfoldError(
                "PROVIDER_ERROR",
                "The configured provider failed the request.",
                "Check model availability and credentials. Inspect provider-attempt events; "
                "a new operation requires a new request identity.",
            )
            raise owner.fatal from None
        if owner.fatal:
            raise owner.fatal
        owner.text_bytes += len(response.model_dump_json(exclude={"metadata"}).encode())
        if owner.text_bytes > owner.grant.max_text_bytes:
            owner.fatal = UnfoldError("RESOURCE_LIMIT", "Response exceeded the text allowance.")
            raise owner.fatal
        owner.delivered.update(pending)
        return response


def provider_entry(grant):
    revision, _ = PROVIDERS[grant.provider]
    _, api_key = credential(grant.provider)
    if not api_key:
        raise UnfoldError(
            "PROVIDER_UNAVAILABLE",
            "Selected provider credential is unavailable.",
            "Set " + " or ".join(CREDENTIALS[grant.provider]) + ".",
        )
    return {
        "module": "provider-" + grant.provider,
        "source": f"git+https://github.com/microsoft/amplifier-module-provider-{grant.provider}@{revision}",
        "config": {
            "api_key": api_key,
            "default_model": grant.model,
            "max_retries": 0,
            "use_streaming": False,
        },
    }


async def execute(owner):
    try:
        from amplifier_agent_lib.engine import Engine
        from amplifier_agent_lib.protocol import PROTOCOL_VERSION, server_default_capabilities
        from amplifier_agent_lib.protocol_points.defaults_cli import (
            CliApprovalSystem,
            CliDisplaySystem,
        )
    except ImportError:
        raise UnfoldError(
            "MISSING_PREREQUISITE", "Amplifier Agent is unavailable.", "Install the smart extra."
        ) from None
    entry = provider_entry(owner.grant)

    async def turn(ctx):
        prepared = copy.copy(engine.session)
        prepared.mount_plan = copy.deepcopy(prepared.mount_plan)
        prepared.mount_plan.update(providers=[entry], tools=[], agents={}, hooks=[])
        with tempfile.TemporaryDirectory(prefix="unfold-agent-") as directory:
            session = await prepared.create_session(
                session_id=owner.request["operation_id"], session_cwd=Path(directory)
            )
            async with session:
                providers = session.coordinator.get("providers")
                if len(providers) != 1:
                    raise UnfoldError(
                        "PROVIDER_CONFIGURATION", "Expected exactly one configured provider."
                    )
                for name, provider in list(providers.items()):
                    await session.coordinator.mount("providers", Gate(provider, owner), name=name)
                tool = owner.tool()
                await session.coordinator.mount("tools", tool, name=tool.name)
                author = owner.author_tool()
                await session.coordinator.mount("tools", author, name=author.name)
                try:
                    await session.execute(ctx.prompt)
                except Finished:
                    pass
                except RuntimeError:
                    if owner.fatal:
                        raise owner.fatal
                    if owner.result is None:
                        raise
        return "Unfold retained the validated result."

    engine = Engine(
        turn_handler=turn,
        protocol_points={
            "approval": CliApprovalSystem(mode="no"),
            "display": CliDisplaySystem(stream=sys.stderr, verbosity="quiet"),
        },
    )
    try:
        await engine.boot(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": server_default_capabilities(),
                "sessionId": owner.request["operation_id"],
                "resume": False,
            }
        )
        prompt = files("unfold").joinpath("resources/production.md").read_text()
        prompt += "\nSCENE SCHEMA:\n" + json.dumps(Scene.model_json_schema())
        prompt += "\nINPUT DATA:\n" + json.dumps(
            {key: owner.request.get(key) for key in ("brief", "feedback", "base_scene")}
        )
        prompt += "\nAVAILABLE IDENTITY ASSETS (image asset_id; text/card font_asset_id):\n" + json.dumps(
            {i: {k: a[k] for k in ("name", "role", "font") if k in a}
             for i, a in owner.request.get("resources", {}).items()}
        )
        await engine.submit_turn(
            {"sessionId": owner.request["operation_id"], "turnId": "1", "prompt": prompt}
        )
        if owner.result is None:
            raise owner.fatal or UnfoldError(
                "NO_SUBMISSION", "The agent ended without a validated submission."
            )
        return owner.result
    finally:
        await engine.shutdown()
