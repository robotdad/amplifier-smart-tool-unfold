"""Offline SDK responses for the real embedded Agent and isolated worker."""

import inspect
import json
import subprocess
import sys
from pathlib import Path

from openai.types.responses import Response

from unfold import agent, worker

scene = {
    "title": "Repair fixture", "duration": 5, "explanation": "Visible path morph",
    "elements": [{"id": "wave", "kind": "path", "x": 200, "y": 200,
                  "width": 600, "height": 300, "opacity": 1,
                  "points": [[10, 150], [150, 20], [300, 280], [590, 150]]}],
    "tweens": [{"target": "wave", "at": 1, "duration": 2, "scale": 0.05,
                "points": [[100, 150], [230, 120], [360, 180], [500, 150]]}],
}
valid = json.loads(json.dumps(scene))
valid["tweens"][0]["scale"] = 1
steps = [
    ("author", scene), ("author", valid), ("render", "{}"),
    ("sample", json.dumps({"times": [0.5, 2, 4]})),
    ("submit", json.dumps({"review": "Scripted transport fixture, no model judgment."})),
]
original_bound = agent.bound_openai


def bound(provider, owner):
    directory = Path(inspect.getfile(type(provider))).parent.parent
    revision = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True
    ).strip()
    assert revision == agent.PROVIDERS["openai"][0], "Runtime ignored provider pin"
    owner.event("fixture_provider_revision", {"revision": revision})

    async def create(params):
        index = owner.model_calls - 1
        action, payload = steps[index]
        if index == 1:
            # The actual provider wire request must contain validation feedback.
            assert "scale" in json.dumps(params["input"])
            assert "greater_than_equal" in json.dumps(params["input"])
        if index == 4:
            images = [block for item in params["input"]
                      for block in item.get("content", []) if isinstance(block, dict)
                      and block.get("type") == "input_image"]
            assert len(images) == 3
        return Response.model_validate({
            "id": f"resp_{index}", "created_at": 1, "object": "response",
            "model": "gpt-6-astra", "status": "completed", "error": None,
            "incomplete_details": None, "instructions": None, "metadata": {},
            "parallel_tool_calls": False, "temperature": 1, "tool_choice": "auto",
            "tools": [], "top_p": 1,
            "output": [{"type": "function_call", "id": f"fc_{index}",
                        "call_id": f"call_{index}", "name": "production",
                        "arguments": json.dumps({"action": action, "payload": payload}),
                        "status": "completed"}],
            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
        })

    provider._create_response = create
    original_bound(provider, owner)


agent.bound_openai = bound
sys.argv = ["worker", sys.argv[1]]
worker.main()
