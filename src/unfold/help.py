"""Library-owned self-description, available without intelligence or credentials."""

from importlib.resources import files

from .models import Brief, Grant
from .operations import CAPABILITIES, signature


def skill():
    return (
        '<skill_content name="unfold">\n'
        + files("unfold").joinpath("SMART_TOOL.md").read_text()
        + "\n</skill_content>"
    )


def manifest():
    return {
        "name": "unfold",
        "version": "0.1.0.dev0",
        "smart_tool_format": 1,
        "description": "Create, review, reuse and deliver motion graphics with embedded Amplifier Agent.",
        "library": "unfold.Unfold",
        "output_settings": {
            "resolutions": {"720p": [1280, 720], "1080p": [1920, 1080]},
            "new_creation_default": "1080p",
            "legacy_missing_setting": "720p",
            "fps": 30,
            "scope": "retained per brief and scene; native rendering, no export upscale",
        },
        "capabilities": {
            "create": "model-backed",
            "revise": "model-backed",
            **{
                name: "model-backed"
                if name in {"submit-refinement", "submit-creation", "adopt-identity"}
                else "deterministic"
                for name in CAPABILITIES
            },
            **{
                name: "deterministic"
                for name in (
                    "doctor",
                    "projects",
                    "inspect",
                    "observe",
                    "rename",
                    "export",
                    "render",
                    "feedback",
                    "address-feedback",
                    "cancel",
                    "reconcile",
                    "dashboard",
                    "manifest",
                    "schemas",
                    "backend-package",
                )
            },
        },
    }


def schemas():

    from .lib import Unfold

    return {
        "Brief": Brief.model_json_schema(),
        "Grant": Grant.model_json_schema(),
        "capabilities": {
            name: str(signature(getattr(Unfold, method))) for name, method in CAPABILITIES.items()
        },
    }


def backend_package():
    import json

    return json.loads(files("unfold").joinpath("resources/backend.json").read_text())


def capability_skill(name, *, json_adapter=False, argument_reference=""):
    """Return a complete capability skill without opening a store or provider."""
    import json

    from .capability_help import CAPABILITY_HELP, COMMAND_HELP
    from .lib import Unfold

    catalog = CAPABILITY_HELP if json_adapter else COMMAND_HELP
    purpose, example, result, guidance = catalog[name]
    command = f"call {name}" if json_adapter else name
    model_backed = name in {
        "create",
        "revise",
        "submit-refinement",
        "submit-creation",
        "adopt-identity",
    }
    if json_adapter:
        arguments = f"Named JSON parameters: `{signature(getattr(Unfold, CAPABILITIES[name]))}`"
        example = f"unfold call {name} --args - <<'JSON'\n{json.dumps(example, indent=2)}\nJSON"
    else:
        arguments = f"```text\n{argument_reference.strip()}\n```"
    extra = ""
    if name in {"create", "revise"}:
        extra = """
For `create`, brief.json can contain:
```json
{"title":"Agent handoff","intent":"Animate delegation and the returned artifact","duration":20}
```
For both commands, grant.json can contain (choose your configured vision model):
```json
{"provider":"gemini","model":"YOUR_VISION_MODEL","allow_context":true,"allow_frames":true,"vision":true}
```
Read `unfold schemas` for optional fields and budget limits. These disclosure
flags require caller authorization; do not infer permission from this example.
"""
    if name == "call":
        extra = "\nAvailable capability skills:\n" + "\n".join(
            f"- `unfold call {key} --help` — {value[0]}." for key, value in CAPABILITY_HELP.items()
        )
    return f"""<skill_content name="unfold-{name}">
# unfold {command}

## When to use

{purpose}. {"Model-backed" if model_backed else "Deterministic"}.

## Arguments

{arguments}

Global `--library PATH` and `--backend PATH` go before the command. Defaults are
`~/.local/share/unfold` and `~/.local/share/unfold-backend`.
Replace uppercase IDs and example paths with retained IDs and accessible local paths.

## Example

```sh
{example}
```
{extra}
## Result

{result}

## Constraints and recovery

{guidance}

CLI results go to stdout as JSON; help is text. Recognized input/domain failures
emit a structured error on stderr and exit 1; bad CLI syntax exits 2. Returned
failed/cancelled/interrupted operations also exit 1. A job acknowledgement is not completion.
Read its durable status before using an output or requesting another attempt.

## Related guidance

Read `unfold --help` for installation, provider setup, disclosure and workflow.
Use `unfold schemas` for exact validated shapes. Python callers use the same
library operations and catch `UnfoldError` (code, message, remedy).
</skill_content>"""
