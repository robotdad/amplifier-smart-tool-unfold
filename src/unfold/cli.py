"""Argument and I/O adapter over the public library."""

import argparse
import json
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from .help import backend_package, capability_skill, manifest, schemas, skill
from .lib import Unfold
from .models import Brief, Grant, UnfoldError


class SkillHelp(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if self.const is None:
            print(skill())
        elif self.const == "call" and getattr(namespace, "capability", None):
            from .operations import CAPABILITIES

            name = namespace.capability
            if name not in CAPABILITIES:
                parser.error(f"Unknown capability: {name}")
            print(capability_skill(name, json_adapter=True))
        else:
            print(capability_skill(self.const, argument_reference=parser.format_help()))
        parser.exit()


class SkillParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self.add_argument("-h", action="help", help="Show the short argument reference.")
        self.add_argument(
            "--help",
            action=SkillHelp,
            nargs=0,
            const=None if " " not in self.prog else self.prog.split()[-1],
            help="Read the capability usage skill.",
        )


def main():
    parser = SkillParser(
        prog="unfold", description="Unfold motion graphics. --help prints the full skill."
    )
    parser.add_argument(
        "--library", help="Retained library directory (default ~/.local/share/unfold)"
    )
    parser.add_argument(
        "--backend", help="Pinned npm backend directory (default ~/.local/share/unfold-backend)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "schemas", "backend-package", "doctor", "projects"):
        commands.add_parser(name, help="Deterministic: " + name)
    for name in ("inspect", "render", "cancel", "reconcile"):
        commands.add_parser(name).add_argument("id")
    p = commands.add_parser("observe")
    p.add_argument("--after", type=int, default=0)
    p = commands.add_parser("rename")
    p.add_argument("id")
    p.add_argument("name")
    p = commands.add_parser("export")
    p.add_argument("id")
    p.add_argument("directory")
    p = commands.add_parser("feedback")
    p.add_argument("id")
    p.add_argument("text")
    p = commands.add_parser("address-feedback")
    p.add_argument("id", help="Submitted feedback ID")
    p.add_argument("revision", help="Resulting revision with the same feedback and base")
    for name in ("create", "revise"):
        p = commands.add_parser(name, help="Model-backed; requires explicit grant JSON.")
        p.add_argument("--grant", required=True, help="JSON file matching Grant schema")
        p.add_argument(
            "--request-id",
            help="32 lowercase hexadecimal characters; identical retries never re-spend",
        )
        if name == "create":
            p.add_argument("--brief", required=True, help="JSON file matching Brief schema")
        else:
            p.add_argument("id", help="Base revision ID")
            p.add_argument("--feedback", required=True)
    p = commands.add_parser("call", help="Invoke a public library capability with JSON arguments.")
    p.add_argument("capability")
    p.add_argument("--args", required=True, help="JSON argument file, or - for stdin")
    p = commands.add_parser("dashboard", help="Open a loopback review service; Ctrl-C stops it.")
    p.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command in {"manifest", "schemas", "backend-package"}:
            result = {"manifest": manifest, "schemas": schemas, "backend-package": backend_package}[
                args.command
            ]()
        else:
            library = Unfold(args.library, args.backend)
            command = args.command
            if command == "call":
                from .operations import invoke

                payload = sys.stdin.read() if args.args == "-" else Path(args.args).read_text()
                result = invoke(library, args.capability, json.loads(payload))
            elif command == "dashboard":
                with library.dashboard(args.port) as viewer:
                    print(json.dumps({"url": viewer.url}), flush=True)
                    while True:
                        time.sleep(1)
            elif command == "create":
                result = library.create(
                    Brief.model_validate_json(Path(args.brief).read_text()),
                    Grant.model_validate_json(Path(args.grant).read_text()),
                    request_id=args.request_id,
                )
            elif command == "revise":
                result = library.revise(
                    args.id,
                    args.feedback,
                    Grant.model_validate_json(Path(args.grant).read_text()),
                    request_id=args.request_id,
                )
            elif command in {"inspect", "render", "cancel", "reconcile"}:
                result = getattr(library, command)(args.id)
            elif command == "rename":
                result = library.rename(args.id, args.name)
            elif command == "export":
                result = library.export(args.id, args.directory)
            elif command == "feedback":
                result = library.feedback(args.id, args.text)
            elif command == "address-feedback":
                result = library.address_feedback(args.id, args.revision)
            elif command == "observe":
                result = library.observe(args.after)
            else:
                result = getattr(library, command)()
        print(json.dumps(result, indent=2))
        if isinstance(result, dict) and result.get("status") in {"failed", "cancelled", "interrupted"}:
            sys.exit(1)
    except KeyboardInterrupt:
        pass
    except (UnfoldError, ValidationError, OSError, ValueError, TypeError) as exc:
        error = (
            exc.as_dict()
            if isinstance(exc, UnfoldError)
            else {"code": "INVALID_INPUT", "message": str(exc)}
        )
        print(json.dumps({"error": error}), file=sys.stderr)
        sys.exit(1)
