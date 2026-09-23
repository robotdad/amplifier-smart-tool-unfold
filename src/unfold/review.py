"""Library-owned feedback authority, durable drafts and asynchronous refinement."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import psutil

from .models import Brief, Grant, UnfoldError
from .processes import is_alive, owned_process, process_identity
from .store import uid


class Review:
    def submit_creation(self, brief, grant, request_id):
        """Accept one bounded creation and launch owned work; exact retries never relaunch."""
        brief, grant = Brief.model_validate(brief), Grant.model_validate(grant)
        self.store.workspace(request_id)
        payload = {"brief": brief.model_dump(), "grant": grant.model_dump()}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = self.store.get(request_id, db=db)
            except UnfoldError:
                old = None
            if old:
                if (
                    old.get("kind") != "review_job"
                    or old.get("mode") != "create"
                    or old.get("fingerprint") != fingerprint
                ):
                    raise UnfoldError(
                        "REQUEST_CONFLICT", "Retry identity has different creation input."
                    )
                return old
            if not (grant.allow_context and grant.allow_frames and grant.vision):
                raise UnfoldError(
                    "DISCLOSURE_REQUIRED",
                    "Creation requires context and sampled-frame disclosure to a vision model.",
                )
            self.backend.require()
            job = {
                "id": request_id,
                "kind": "review_job",
                "mode": "create",
                **payload,
                "fingerprint": fingerprint,
                "project_id": None,
                "revision_id": None,
                "operation_id": uid(),
                "status": "queued",
                "created": time.time(),
            }
            self.store.put("review_job", job, db)
            self.store.event(
                "creation_accepted", request_id, {"operation_id": job["operation_id"]}, db
            )
        return self._launch_review_job(request_id)

    def cancel_job(self, job_id, request_id=None):
        """Request cancellation of owned creation or refinement; inspect review_state for cleanup."""
        return self.cancel_refinement(job_id, request_id)

    def save_review_view(
        self,
        revision_id,
        at=0,
        playing=False,
        artifact_id=None,
        expected_version=None,
        theme=None,
        request_id=None,
    ):
        """Persist the shared review position; changing it never selects creative work or grants execution."""
        if request_id is not None:
            return self._mutation(
                "save_review_view",
                request_id,
                {
                    "revision_id": revision_id,
                    "at": at,
                    "playing": playing,
                    "artifact_id": artifact_id,
                    "expected_version": expected_version,
                    "theme": theme,
                },
                lambda: self.save_review_view(
                    revision_id, at, playing, artifact_id, expected_version, theme
                ),
            )
        import math

        revision = self.store.get(revision_id, "revision")
        if artifact_id is not None and artifact_id not in revision.get("artifacts", []):
            raise UnfoldError("INVALID_INPUT", "The media must belong to the viewed revision.")
        duration = revision.get("brief", {}).get("duration", 60)
        if (
            type(at) not in (int, float)
            or not math.isfinite(at)
            or not 0 <= at <= duration
            or type(playing) is not bool
        ):
            raise UnfoldError(
                "INVALID_INPUT", "Choose a finite playback position within the revision."
            )
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 0
        ):
            raise UnfoldError(
                "INVALID_INPUT", "Expected view version must be a nonnegative integer."
            )
        if theme is not None and theme not in ("system", "light", "dark"):
            raise UnfoldError("INVALID_INPUT", "Theme must be system, light, or dark.")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = self.store.get("shared-review-view", "review_view", db)
            except UnfoldError:
                old = {"version": 0}
            if expected_version is not None and expected_version != old["version"]:
                raise UnfoldError(
                    "VIEW_CONFLICT", "The shared review position changed. Read review-state."
                )
            record = {
                "id": "shared-review-view",
                "kind": "review_view",
                "revision_id": revision_id,
                "project_id": revision["project_id"],
                "at": at,
                "playing": playing,
                "artifact_id": artifact_id,
                "theme": theme,
                "version": old["version"] + 1,
                "updated_at": time.time(),
            }
            self.store.put("review_view", record, db)
        return record

    def authorize_review(self, project_id, grant, refinements=1, request_id=None):
        """Set a bounded allowance; optional retry identity never restores consumed authority."""
        self.store.get(project_id, "project")
        grant = Grant.model_validate(grant)
        if type(refinements) is not int or not 1 <= refinements <= 10:
            raise UnfoldError("INVALID_INPUT", "Authorize 1–10 bounded refinements.")
        payload = {
            "project_id": project_id,
            "grant": grant.model_dump(),
            "refinements": refinements,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if request_id is not None:
            self.store.workspace(request_id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                try:
                    old = self.store.get(request_id, db=db)
                except UnfoldError:
                    old = None
                if old:
                    if (
                        old.get("kind") != "review_authorization"
                        or old.get("fingerprint") != fingerprint
                    ):
                        raise UnfoldError(
                            "REQUEST_CONFLICT",
                            "Retry identity has different authorization input or is already occupied.",
                        )
                    return old
            record = {
                "id": project_id + "-authority",
                "kind": "authority",
                "project_id": project_id,
                "grant": grant.model_dump(),
                "remaining": refinements,
            }
            self.store.put("authority", record, db)
            self.store.event("review_authorized", project_id, {"remaining": refinements}, db)
            if request_id is None:
                return record
            receipt = {
                "id": request_id,
                "kind": "review_authorization",
                **payload,
                "fingerprint": fingerprint,
                "authority_id": record["id"],
                "created": time.time(),
            }
            self.store.put("review_authorization", receipt, db)
            return receipt

    def save_draft(
        self,
        revision_id,
        text,
        at=0,
        end=None,
        sequence=0,
        request_id=None,
        resolve_conflict_id=None,
    ):
        if request_id is not None:
            return self._mutation(
                "save_draft",
                request_id,
                {
                    "revision_id": revision_id,
                    "text": text,
                    "at": at,
                    "end": end,
                    "sequence": sequence,
                    "resolve_conflict_id": resolve_conflict_id,
                },
                lambda: self.save_draft(
                    revision_id, text, at, end, sequence, resolve_conflict_id=resolve_conflict_id
                ),
            )
        revision = self.store.get(revision_id, "revision")
        duration = revision.get("brief", {}).get("duration", 60)
        if not isinstance(text, str) or len(text) > 5000 or not 0 <= at <= duration:
            raise UnfoldError("INVALID_INPUT", "Invalid draft or time.")
        if end is not None and not at <= end <= duration:
            raise UnfoldError("INVALID_INPUT", "Interval must fit the revision.")
        record = {
            "id": revision_id + "-draft",
            "kind": "draft",
            "revision_id": revision_id,
            "text": text,
            "at": at,
            "end": end,
            "sequence": sequence,
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = self.store.get(record["id"], "draft", db)
                if old["sequence"] >= sequence:
                    return old
                if old.get("submitted_job") and all(
                    old.get(k) == record.get(k) for k in ("text", "at", "end")
                ):
                    record["submitted_job"] = old["submitted_job"]
            except UnfoldError:
                pass
            self.store.put("draft", record, db)
            if resolve_conflict_id:
                conflict = self.store.get(resolve_conflict_id, "draft_conflict", db)
                if conflict["revision_id"] != revision_id:
                    raise UnfoldError("REQUEST_CONFLICT", "Draft conflict belongs to another revision.")
                conflict.update(status="resolved", resolved_at=time.time(), resolution=record)
                self.store.put("draft_conflict", conflict, db)
        return record

    def record_draft_conflict(self, revision_id, local, remote, conflict_id):
        """Retain local unsaved text separately when a newer shared draft wins."""
        self.store.workspace(conflict_id)
        revision = self.store.get(revision_id, "revision")
        if (
            not isinstance(local, dict)
            or local.get("revision_id") != revision_id
            or not isinstance(local.get("text"), str)
            or len(local["text"]) > 5000
        ):
            raise UnfoldError("INVALID_INPUT", "A conflict must retain one exact draft snapshot.")
        duration = revision.get("brief", {}).get("duration", 60)
        if not 0 <= local.get("at", -1) <= duration or (
            local.get("end") is not None and not local["at"] <= local["end"] <= duration
        ):
            raise UnfoldError("INVALID_INPUT", "Conflict draft time must fit the viewed revision.")
        payload = {
            "id": conflict_id,
            "kind": "draft_conflict",
            "revision_id": revision_id,
            "local": {
                key: local[key] for key in ("revision_id", "text", "at", "end", "sequence")
            },
            "remote": {
                key: remote.get(key) for key in ("revision_id", "text", "at", "end", "sequence")
            },
            "status": "open",
            "created_at": time.time(),
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                prior = self.store.get(conflict_id, "draft_conflict", db)
            except UnfoldError:
                prior = None
            if prior:
                if prior["local"] != payload["local"] or prior["remote"] != payload["remote"]:
                    raise UnfoldError(
                        "REQUEST_CONFLICT", "Conflict identity is already bound to different drafts."
                    )
                return prior
            self.store.put("draft_conflict", payload, db)
            self.store.event(
                "draft_conflict_retained",
                revision_id,
                {"conflict_id": conflict_id, "remote_sequence": payload["remote"]["sequence"]},
                db,
            )
        return payload

    def retain_refinement_intent(
        self, revision_id, text, request_id, at=0, end=None, identity_version=None
    ):
        """Retain an exact Apply command before its presenter awaits another call."""
        self.store.workspace(request_id)
        rev = self.store.get(revision_id, "revision")
        if not isinstance(text, str) or not text.strip() or len(text) > 5000:
            raise UnfoldError("INVALID_INPUT", "Write feedback before applying it.")
        duration = rev.get("brief", {}).get("duration", 60)
        if not 0 <= at <= duration or (end is not None and not at <= end <= duration):
            raise UnfoldError("INVALID_INPUT", "Feedback time must fit the viewed revision.")
        payload = {
            "revision_id": revision_id,
            "text": text,
            "at": at,
            "end": end,
            "identity_version": identity_version,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        intent_id = "refinement-intent-" + request_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.store.get(intent_id, "refinement_intent", db)
            except UnfoldError:
                existing = None
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise UnfoldError(
                        "REQUEST_CONFLICT", "Retry identity is already bound to different feedback."
                    )
                return existing
            intent = {
                "id": intent_id,
                "kind": "refinement_intent",
                "request_id": request_id,
                "fingerprint": fingerprint,
                "payload": payload,
                "status": "pending",
                "created": time.time(),
            }
            self.store.put("refinement_intent", intent, db)
            self.store.event(
                "refinement_intent_retained", request_id, {"revision_id": revision_id}, db
            )
            return intent

    def acknowledge_refinement_intent(self, request_id):
        """Record delivery of an accepted Apply receipt; never launches work."""
        intent_id = "refinement-intent-" + request_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            intent = self.store.get(intent_id, "refinement_intent", db)
            if intent["status"] != "accepted":
                raise UnfoldError("MUTATION_INCOMPLETE", "Apply is not yet accepted.")
            intent["acknowledged_at"] = time.time()
            self.store.put("refinement_intent", intent, db)
            return intent

    def submit_refinement(
        self, revision_id, text, request_id, at=0, end=None, identity_version=None
    ):
        """Accept once, consume one existing project grant, then launch owned work."""
        self.store.workspace(request_id)  # validates caller retry identity
        rev = self.store.get(revision_id, "revision")
        if not isinstance(text, str) or not text.strip() or len(text) > 5000:
            raise UnfoldError("INVALID_INPUT", "Write feedback before applying it.")
        duration = rev.get("brief", {}).get("duration", 60)
        if not 0 <= at <= duration or (end is not None and not at <= end <= duration):
            raise UnfoldError("INVALID_INPUT", "Feedback time must fit the viewed revision.")
        payload = {
            "revision_id": revision_id,
            "text": text,
            "at": at,
            "end": end,
            "identity_version": identity_version,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        intent_id = "refinement-intent-" + request_id
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = self.store.get(request_id, db=db)
            except UnfoldError:
                old = None
            if old:
                if old.get("kind") != "review_job" or old.get("fingerprint") != fingerprint:
                    raise UnfoldError("REQUEST_CONFLICT", "Retry identity has different feedback.")
                return old
            try:
                intent = self.store.get(intent_id, "refinement_intent", db)
            except UnfoldError:
                intent = None
            if intent and intent["fingerprint"] != fingerprint:
                raise UnfoldError("REQUEST_CONFLICT", "Retry identity has different feedback.")
            project = self.store.get(rev["project_id"], "project", db)
            if project["current_revision"] != revision_id:
                raise UnfoldError(
                    "STALE_BASE",
                    "A newer revision exists. Open it and adapt your feedback; your draft stays on this version.",
                )
            try:
                authority = self.store.get(project["id"] + "-authority", "authority", db)
            except UnfoldError:
                raise UnfoldError(
                    "AUTHORITY_REQUIRED", "Caller must authorize dashboard refinement first."
                ) from None
            if authority["remaining"] < 1:
                raise UnfoldError(
                    "AUTHORITY_EXHAUSTED", "Ask the caller for another refinement allowance."
                )
            busy = db.execute("SELECT data FROM records WHERE kind='review_job'").fetchall()
            if any(
                (j := json.loads(row[0]))["project_id"] == project["id"]
                and j["status"] in ("queued", "running", "cancelling")
                for row in busy
            ):
                raise UnfoldError("BUSY", "This project already has refinement work in progress.")
            note = {
                "id": uid(),
                "kind": "feedback",
                "project_id": project["id"],
                **payload,
                "status": "pending",
                "job_id": request_id,
            }
            job = {
                "id": request_id,
                "kind": "review_job",
                **payload,
                "fingerprint": fingerprint,
                "project_id": project["id"],
                "feedback_id": note["id"],
                "operation_id": uid(),
                "status": "queued",
                "grant": authority["grant"],
                "created": time.time(),
            }
            authority["remaining"] -= 1
            self.store.put("authority", authority, db)
            self.store.put("feedback", note, db)
            self.store.put("review_job", job, db)
            if intent:
                intent.update(status="accepted", job_id=request_id)
                self.store.put("refinement_intent", intent, db)
            try:
                draft = self.store.get(revision_id + "-draft", "draft", db)
                if all(draft.get(k) == payload.get(k) for k in ("text", "at", "end")):
                    draft["submitted_job"] = request_id
                    self.store.put("draft", draft, db)
            except UnfoldError:
                pass
            self.store.event(
                "refinement_accepted", request_id, {"feedback_id": note["id"], **payload}, db
            )
        return self._launch_review_job(request_id)

    def _launch_review_job(self, request_id):
        job = self.store.get(request_id, "review_job")
        try:
            logpath = self.store.workspace(request_id) / "review.log"
            with logpath.open("w") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "unfold.review_worker",
                        str(self.store.root),
                        str(self.backend.root),
                        request_id,
                    ],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            threading.Thread(target=child.wait, daemon=True).start()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                job = self.store.get(request_id, "review_job", db)
                job["pid"] = child.pid
                # A fast child may already have exited; its own admission also
                # records this identity before running any operation.
                try:
                    job["owner"] = process_identity(child.pid)
                except psutil.NoSuchProcess:
                    pass
                self.store.put("review_job", job, db)
        except OSError:
            job.update(
                status="failed",
                error="Could not start owned work. This request will not be replayed; explicit authorization is needed for a new attempt.",
            )
            self.store.put("review_job", job)
        return job

    def run_review_job(self, job_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self.store.get(job_id, "review_job", db)
            if job["status"] != "queued":
                return job
            job.update(status="running", pid=os.getpid(), owner=process_identity())
            self.store.put("review_job", job, db)
        try:
            if job.get("mode") == "create":
                result = self.create(
                    Brief.model_validate(job["brief"]),
                    Grant.model_validate(job["grant"]),
                    request_id=job["operation_id"],
                )
            else:
                result = self.revise(
                    job["revision_id"],
                    job["text"],
                    Grant.model_validate(job["grant"]),
                    request_id=job["operation_id"],
                    target={"at": job["at"], "end": job["end"]},
                    identity_version=job.get("identity_version"),
                )
            if result["status"] == "completed" and job.get("feedback_id"):
                self.address_feedback(job["feedback_id"], result["revision_id"])
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self.store.get(job_id, "review_job", db)
                current.update(status=result["status"], result=result)
                if job.get("mode") == "create" and result.get("project_id"):
                    current["project_id"] = result["project_id"]
                self.store.put("review_job", current, db)
                self.store.event(
                    ("creation_" if job.get("mode") == "create" else "refinement_")
                    + result["status"],
                    job_id,
                    result,
                    db,
                )
                return current
        except Exception as exc:
            job.update(
                status="cancelled"
                if isinstance(exc, UnfoldError) and exc.code == "CANCELLED"
                else "failed",
                error=str(exc),
            )
            self.store.put("review_job", job)
            self.store.event(
                "creation_failed" if job.get("mode") == "create" else "refinement_failed",
                job_id,
                {"error": str(exc)},
            )
            return job

    def cancel_refinement(self, job_id, request_id=None):
        if request_id is not None:
            return self._mutation(
                "cancel_refinement",
                request_id,
                {"job_id": job_id},
                lambda: self.cancel_refinement(job_id),
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self.store.get(job_id, "review_job", db)
            if job["status"] == "queued":
                job["status"] = "cancelled"
            elif job["status"] == "running":
                job["status"] = "cancelling"
            self.store.put("review_job", job, db)
            self.store.event("refinement_cancel_requested", job_id, {}, db)
        try:
            self.cancel(job["operation_id"])
        except UnfoldError:
            pass
        return job

    def review_state(self):
        jobs = self.store.list("review_job")
        for job in jobs:
            if job["status"] not in ("queued", "running", "cancelling"):
                continue
            alive = False
            if job.get("owner"):
                alive = owned_process(job["owner"])[0] in {"live", "unknown"}
            elif job.get("pid"):
                alive = is_alive(job["pid"])
            elif time.time() - job["created"] < 10:
                alive = True
            if alive:
                continue
            try:
                operation = self.store.get(job["operation_id"], "operation")
            except UnfoldError:
                operation = None
            if operation:
                operation = self.reconcile(operation["id"])
                if operation["status"] in ("running", "cancelling"):
                    # A lost review launcher does not imply a lost creative worker.
                    # Reconciliation cannot replay, kill unverified PIDs, or relabel
                    # a still-live worker as terminal.
                    continue
                if operation["status"] == "completed" and job.get("feedback_id"):
                    self.address_feedback(job["feedback_id"], operation["revision_id"])
                job.update(status=operation["status"], result=operation)
                if job.get("mode") == "create":
                    job["project_id"] = operation["project_id"]
            else:
                job.update(
                    status="interrupted",
                    error="Worker stopped. No automatic retry or additional spending.",
                )
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self.store.get(job["id"], "review_job", db)
                if current["status"] in ("queued", "running", "cancelling"):
                    self.store.put("review_job", job, db)
                    self.store.event(
                        "refinement_recovered", job["id"], {"status": job["status"]}, db
                    )
        return {
            "projects": self.projects(),
            "revisions": [self.inspect(r) for p in self.projects() for r in p["revisions"]],
            "events": self.observe(),
            "jobs": self.store.list("review_job"),
            "drafts": self.store.list("draft"),
            "draft_conflicts": self.store.list("draft_conflict"),
            "views": self.store.list("review_view"),
            "feedback_intents": self.store.list("feedback_intent"),
            "refinement_intents": self.store.list("refinement_intent"),
            "mutation_receipts": self.store.list("mutation_receipt"),
            "mutation_intents": self.store.list("mutation_intent"),
            "authorities": self.store.list("authority"),
            "assets": self.assets(),
            "packs": self.packs(),
            "versions": self.store.list("pack_version"),
            "deliveries": self.store.list("delivery"),
            "outputs": [self.artifact(a["id"]) for a in self.store.list("artifact")],
        }
