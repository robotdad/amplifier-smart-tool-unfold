"""Deterministic reconciliation of retained production; never dispatch intelligence."""

import json
import time

import psutil

from .models import UnfoldError
from .processes import owned_process, stop_tree
from .store import digest, uid

ACTIVE = {"running", "cancelling"}


class Recovery:
    def reconcile(self, operation_id):
        """Settle one operation from owned process evidence and retained results.

        Reads (inspect/observe) remain passive. An unknown legacy owner is not
        permission to kill or declare cleanup. Late worker admission is fenced by
        the same transaction used here.
        """
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            operation = self.store.get(operation_id, "operation", db)
            if operation["status"] not in ACTIVE:
                return operation
            if operation.get("cleanup_status") == "pending":
                return self._stop_operation(
                    operation_id, UnfoldError(**operation["cleanup_error"]), db=db
                )
            launcher, _ = owned_process(operation.get("launcher"))
            worker, process = owned_process(operation.get("worker"))
            cancelling = operation["status"] == "cancelling"
            expired = time.time() >= operation.get("deadline", float("inf"))
            if launcher == "live" and worker not in {"absent", "mismatch"} and not cancelling:
                return {**operation, "recovery": "launcher_live"}
            if worker == "live" and not cancelling and not expired:
                return {**operation, "recovery": "worker_live"}
            if worker == "unknown":
                # New admissions have a launcher's birth identity. A child cannot
                # spend before registering under this lock; fencing a dead launcher
                # with no admitted worker therefore closes the spawn/record gap.
                if operation.get("launcher") and launcher in {"absent", "mismatch"}:
                    if not operation.get("worker") and not operation.get("worker_pid"):
                        return self._finish_operation(
                            operation_id,
                            UnfoldError(
                                "INTERRUPTED",
                                "Launcher stopped before worker admission.",
                                "No replay. Inspect this operation before a new request.",
                            ),
                            db=db,
                        )
                return {
                    **operation,
                    "recovery": "ownership_unknown",
                    "recovery_remedy": "Cannot prove worker ownership. No process was signalled "
                    "and no terminal cleanup is claimed. Do not replay this request.",
                }
            if worker == "live":
                error = UnfoldError(
                    "CANCELLED" if cancelling else "RESOURCE_LIMIT",
                    "Stopping owned execution.",
                    "Retained results remain available; no generation was replayed.",
                )
                return self._stop_operation(operation_id, error, db=db)
            if launcher == "unknown":
                return {**operation, "recovery": "ownership_unknown"}
            execution = operation.get("execution_worker")
            if execution:
                execution_state, execution_process = owned_process(execution)
                if execution_state == "unknown":
                    return {**operation, "recovery": "ownership_unknown"}
                if execution_state == "live":
                    return self._stop_operation(
                        operation_id,
                        UnfoldError(
                            "CANCELLED" if cancelling else "INTERRUPTED",
                            "Lost supervisor; owned execution child stopped. No replay.",
                        ),
                        db=db,
                    )
            result_path = self.store.workspace(operation_id) / "result.json"
            if not cancelling and worker == "absent" and result_path.is_file():
                try:
                    return self._commit_result(operation_id, db)
                except (UnfoldError, OSError, ValueError, KeyError, TypeError) as exc:
                    error = (
                        exc
                        if isinstance(exc, UnfoldError)
                        else UnfoldError(
                            "INVALID_RESULT", "Retained result could not be validated."
                        )
                    )
                    return self._finish_operation(operation_id, error, db=db)
            # Never kill a replacement process, or certify vanished descendants.
            return self._finish_operation(
                operation_id,
                UnfoldError(
                    "INTERRUPTED",
                    "Worker identity changed."
                    if worker == "mismatch"
                    else "Worker stopped without a committable result.",
                    "No replay or cleanup of unverified processes. External calls may have "
                    "completed; inspect retained files and events before authorizing new work.",
                ),
                db=db,
            )

    def _stop_operation(self, operation_id, error, *, db=None):
        """Stop both recorded roots before publishing any terminal outcome.

        A live supervisor is not evidence that its execution subtree still exists.
        Freeze it first, then independently check/stop the execution root. Missing
        roots cannot establish cleanup of descendants that may have been reparented.
        Failed signalling stays active and fenced for a later reconciliation.
        """
        if db is None:
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self._stop_operation(operation_id, error, db=connection)
        operation = self.store.get(operation_id, "operation", db)
        if operation["status"] not in ACTIVE:
            return operation
        if operation.get("cleanup_error"):
            error = UnfoldError(**operation["cleanup_error"])
        supervisor_state, supervisor = owned_process(operation.get("worker"))
        frozen = False
        verified = True
        failure = None
        try:
            if supervisor_state == "live":
                supervisor.suspend()
                frozen = True
            elif supervisor_state == "unknown" and operation.get("worker_pid"):
                failure = "Supervisor ownership is unknown."
            execution = operation.get("execution_worker")
            if execution:
                state, process = owned_process(execution)
                if state == "live":
                    verified = stop_tree(process)
                elif state == "unknown":
                    failure = "Execution ownership is unknown."
                else:
                    verified = False
            elif supervisor_state in {"absent", "mismatch"}:
                verified = False
            if supervisor_state == "live":
                if execution:
                    # This supervisor spawns only the recorded execution child.
                    # Its subtree was checked independently above; do not mistake
                    # that now-dead child for a newly missing cleanup root.
                    supervisor.kill()
                    psutil.wait_procs([supervisor], timeout=3)
                else:
                    verified = stop_tree(supervisor) and verified
            # Do not turn failed cleanup of a known-live root into an immutable
            # terminal record. Reconciliation must still be able to stop it.
            for key in ("worker", "execution_worker"):
                if operation.get(key) and owned_process(operation[key])[0] in {"live", "unknown"}:
                    failure = "Owned process cleanup is incomplete."
        except (OSError, RuntimeError, psutil.Error) as exc:
            failure = type(exc).__name__ + ": owned process cleanup is incomplete."
        finally:
            if frozen:
                try:
                    supervisor.resume()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        if failure:
            operation.update(
                status="cancelling",
                cleanup_status="pending",
                cleanup_error=error.as_dict(),
                recovery="cleanup_incomplete",
                recovery_remedy=failure + " Retry reconcile, not generation.",
            )
            self.store.put("operation", operation, db)
            self.store.event("cleanup_incomplete", operation_id, {"message": failure}, db)
            return operation
        operation["cleanup_status"] = "verified_observed" if verified else "unverified"
        for key in ("cleanup_error", "recovery", "recovery_remedy"):
            operation.pop(key, None)
        self.store.put("operation", operation, db)
        if not verified and error.code in {"CANCELLED", "WORKER_FAILED", "INTERRUPTED"}:
            error = UnfoldError(
                "INTERRUPTED",
                "An execution root disappeared; cleanup of reparented descendants is unverified.",
                "No PID guessing or automatic replay. Only verified owned roots were signalled; "
                "unrecorded descendants may remain.",
            )
        elif error.code == "WORKER_FAILED":
            error = UnfoldError(
                "INTERRUPTED",
                "Supervisor stopped; birth-verified execution child and observed descendants stopped.",
                "Retained evidence is preserved. No generation was replayed.",
            )
        return self._finish_operation(operation_id, error, db=db)

    def _finish_operation(self, operation_id, error, *, db=None):
        if db is None:
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self._finish_operation(operation_id, error, db=connection)
        operation = self.store.get(operation_id, "operation", db)
        if operation["status"] not in ACTIVE:
            return operation
        status = {"CANCELLED": "cancelled", "INTERRUPTED": "interrupted"}.get(error.code, "failed")
        operation.update(status=status, error=error.as_dict())
        result = self.store.workspace(operation_id) / "result.json"
        if result.is_file():
            operation["retained_result"] = str(result.relative_to(self.store.root))
        self.store.put("operation", operation, db)
        self.store.event(status, operation_id, error.as_dict(), db)
        return operation

    def _complete_operation(self, operation_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            operation = self.store.get(operation_id, "operation", db)
            if operation["status"] in ACTIVE and operation.get("execution_worker"):
                state, _ = owned_process(operation["execution_worker"])
                if state in {"live", "unknown"}:
                    raise UnfoldError(
                        "WORKER_FAILED", "Supervisor exited before execution cleanup was verified."
                    )
            return self._commit_result(operation_id, db)

    def _commit_result(self, operation_id, db):
        operation = self.store.get(operation_id, "operation", db)
        if operation["status"] not in ACTIVE:
            return operation
        if operation["status"] != "running":
            raise UnfoldError("CANCELLED", "Late result cannot commit after cancellation.")
        directory = self.store.workspace(operation_id)
        result_path = directory / "result.json"
        if not result_path.is_file():
            raise UnfoldError(
                "WORKER_FAILED",
                "The isolated worker stopped without a result.",
                "Inspect retained diagnostics. Exact retry never relaunches generation.",
            )
        result = json.loads(result_path.read_text())
        if "error" in result:
            raise UnfoldError(**result["error"])
        source, video = directory / "source", directory / "video.mp4"
        if (
            self.backend.source_hash(source) != result["source_sha256"]
            or digest(video) != result["render"]["sha256"]
        ):
            raise UnfoldError("STALE_RESULT", "Source or video changed after agent inspection.")
        self.backend.probe(video)
        project = self.store.get(operation["project_id"], "project", db)
        if project["current_revision"] != operation["base"]:
            raise UnfoldError(
                "STALE_BASE", "Another operation committed first; result retained uncommitted."
            )
        revision_id = uid()
        artifact = self._artifact(revision_id, video, result["render"], project["name"])
        revision = {
            **result,
            "id": revision_id,
            "kind": "revision",
            "project_id": project["id"],
            "base_revision": operation["base"],
            "identity_version": operation["brief"]["identity_version"],
            "brief": operation["brief"],
            "feedback": operation["feedback"],
            "feedback_target": operation["feedback_target"],
            "source": str(source.relative_to(self.store.root)),
            "artifacts": [artifact["id"]],
            "operation_id": operation_id,
        }
        project["current_revision"] = revision_id
        project["revisions"].append(revision_id)
        operation.update(status="completed", revision_id=revision_id)
        for kind, record in (
            ("revision", revision),
            ("artifact", artifact),
            ("project", project),
            ("operation", operation),
        ):
            self.store.put(kind, record, db)
        self.store.event("completed", operation_id, {"revision_id": revision_id}, db)
        return operation
