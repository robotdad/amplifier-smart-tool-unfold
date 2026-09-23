"""Private process entry point. Public clients use the library, never this protocol."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .models import UnfoldError
from .processes import owned_process, process_identity, stop_worker
from .store import Store, write_json


def execute_request(path):
    """Private execution child; the durable supervisor owns its finite lifetime."""
    try:
        from .agent import execute
        from .intelligence import Production

        result = asyncio.run(execute(Production(json.loads(path.read_text()))))
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, UnfoldError)
            else UnfoldError(
                "AGENT_FAILED",
                "Embedded agent execution failed.",
                "Inspect local worker diagnostics and prerequisites.",
            )
        )
        result = {"error": error.as_dict()}
    write_json(path.parent / "result.json", result)


def main():
    path = Path(sys.argv[-1])
    request = json.loads(path.read_text())
    store = Store(request["library"])
    operation_id = request["operation_id"]
    if "--execute" in sys.argv:
        # Wait behind supervisor admission. A child spawned across launcher loss
        # cannot spend before its exact birth identity is durably recorded.
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            operation = store.get(operation_id, "operation", db)
            state, supervisor = owned_process(operation.get("worker"))
            if (
                operation["status"] != "running"
                or state != "live"
                or supervisor.pid != os.getppid()
                or operation.get("execution_worker") != process_identity()
            ):
                return
        execute_request(path)
        return

    from .lib import Unfold

    library = Unfold(request["library"], request["backend"])
    child = None
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            operation = store.get(operation_id, "operation", db)
            identity = process_identity()
            if operation["status"] != "running":
                return
            if operation.get("worker") and operation["worker"] != identity:
                return
            operation.update(worker=identity, worker_pid=os.getpid())
            store.put("operation", operation, db)
            if time.time() >= operation["deadline"]:
                raise UnfoldError("RESOURCE_LIMIT", "Operation wall-clock allowance exhausted.")
            child = subprocess.Popen(
                [sys.executable, "-m", "unfold.worker", "--execute", str(path)],
                start_new_session=True,
            )
            child._unfold_identity = process_identity(child.pid)
            operation["execution_worker"] = child._unfold_identity
            store.put("operation", operation, db)
        while child.poll() is None:
            current = store.get(operation_id, "operation")
            if current["status"] != "running":
                raise UnfoldError("CANCELLED", "Cancellation requested; stopping owned execution.")
            if time.time() >= operation["deadline"]:
                raise UnfoldError("RESOURCE_LIMIT", "Operation wall-clock allowance exhausted.")
            time.sleep(0.1)
    except (Exception, KeyboardInterrupt) as exc:
        stopped = stop_worker(child)
        error = (
            exc
            if isinstance(exc, UnfoldError)
            else UnfoldError("INTERRUPTED", "Execution supervisor stopped; no automatic retry.")
        )
        if error.code == "CANCELLED" and not stopped:
            error = UnfoldError(
                "INTERRUPTED", "Cancellation requested; execution exited before verified cleanup."
            )
        library._finish_operation(operation_id, error)
    finally:
        stop_worker(child)


if __name__ == "__main__":
    main()
