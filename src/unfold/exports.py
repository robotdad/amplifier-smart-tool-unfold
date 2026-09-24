"""Exact-file delivery: retained intent, verified staging, no paid recovery."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from .models import Brief, Grant, UnfoldError
from .store import uid, write_all


def reserved(name):
    return name.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
        *(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"),
    }


def suggested_filename(artifact):
    """NFKC, lowercase Unicode letters/numbers; separators collapse; 180 UTF-8 bytes."""
    text = unicodedata.normalize("NFKC", artifact["name"]).lower()
    stem = re.sub("-+", "-", "".join(c if c.isalnum() else "-" for c in text)).strip("-")
    stem = stem.encode()[:180].decode("utf-8", errors="ignore").rstrip("-")
    if not stem or reserved(stem):
        stem = "artifact-" + artifact["id"]
    return stem + "." + artifact.get("format", "mp4")


def output_summary(artifact):
    return {
        "artifact_id": artifact["id"], "revision_id": artifact["revision_id"],
        "role": "primary_video", "format": artifact.get("format", "mp4"),
        "sha256": artifact["sha256"], "suggested_filename": suggested_filename(artifact),
    }


def export_payload(intent, binding):
    """Check retry ownership before interpreting any export-specific fields."""
    payload = intent.get("payload")
    if (intent.get("operation") != "export-file" or not isinstance(payload, dict)
            or payload.get("binding") != binding):
        raise UnfoldError("REQUEST_CONFLICT", "Export identity has different input.")
    if (any(not isinstance(payload.get(key), str) or not payload[key]
            for key in ("destination", "path", "staging_path"))
            or not isinstance(payload.get("parent_identity"), list)
            or len(payload["parent_identity"]) != 2
            or any(type(value) is not int for value in payload["parent_identity"])):
        raise UnfoldError("REQUEST_CONFLICT", "Export identity has no complete export intent.",
                          "Use a new request ID; the retained intent was not changed.")
    return payload


def identity(value):
    return [value.st_dev, value.st_ino]


def require_publication():
    if (os.name != "posix" or os.link not in os.supports_dir_fd
            or not hasattr(os, "O_NOFOLLOW")):
        raise UnfoldError(
            "UNSUPPORTED", "Exact-file export requires POSIX descriptor-relative hard links.",
            "Windows exact-file publication is not supported; use legacy directory export "
            "with its documented weaker guarantees, or a supported POSIX filesystem.",
        )


@contextmanager
def parent_descriptor(path):
    """Walk the already-resolved absolute parent without following replacements."""
    descriptors = []
    try:
        for part in (path.anchor, *path.parts[1:]):
            descriptors.append(os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                **({"dir_fd": descriptors[-1]} if descriptors else {}),
            ))
        yield descriptors[-1]
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def check_parent(path, expected, fd):
    with parent_descriptor(path) as current:
        if identity(os.fstat(current)) != expected or identity(os.fstat(fd)) != expected:
            raise UnfoldError("DESTINATION_CHANGED", "Export parent was replaced.",
                              "Choose a new destination and request ID.")


def preflight(path, expected=None):
    require_publication()
    with parent_descriptor(path.parent) as fd:
        parent = identity(os.fstat(fd))
        if expected is not None:
            check_parent(path.parent, expected, fd)
        try:
            os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return parent
        raise UnfoldError("OUTPUT_EXISTS", "Destination already exists.",
                          "Choose a different filename; no overwrite is permitted.")


@contextmanager
def delivery_errors():
    # Store.open_relative translates escaping OS errors as source failures. Keep
    # failures in destination I/O distinct before leaving that checked-source scope.
    try:
        yield
    except OSError as exc:
        raise UnfoldError(
            "EXPORT_FAILED", "Staging or publication I/O failed.",
            "Inspect destination space, permissions and hard-link support; use a new export ID.",
        ) from exc


class Exports:
    def _operation_export(self, operation):
        export_id = hashlib.sha256(("create-export:" + operation["id"]).encode()).hexdigest()[:32]
        try:
            self.store.get("mutation-intent-" + export_id, "mutation_intent")
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
            return operation
        result = {"status": "not_started", "receipt_id": export_id,
                  "artifact_id": operation.get("primary_artifact_id"),
                  "revision_id": operation.get("revision_id")}
        try:
            receipt = self.mutation_status(export_id)
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
        else:
            result = receipt.get("result", {
                **result, "status": "incomplete", "error": {
                    "code": "MUTATION_INCOMPLETE", "message": "Copy outcome is uncertain.",
                    "remedy": "Inspect this receipt and destination before a new export-only request.",
                },
            })
        return {**operation, "export": result}

    def _operation_outputs(self, operation):
        if operation.get("status") != "completed" or operation.get("outputs"):
            return operation
        try:
            revision = self.store.get(operation["revision_id"], "revision")
            candidates = [
                self.store.get(i, "artifact") for i in revision.get("artifacts", [])
            ]
            candidates = [a for a in candidates
                          if a.get("revision_id") == revision["id"]
                          and a.get("format", "mp4") == "mp4"
                          and not a.get("delivery_id")]
            if len(candidates) != 1:
                raise UnfoldError("PRIMARY_OUTPUT_UNAVAILABLE",
                                  "Initial output is missing or ambiguous.")
            artifact = candidates[0]
            if (operation.get("primary_artifact_id")
                    and operation["primary_artifact_id"] != artifact["id"]):
                raise UnfoldError("PRIMARY_OUTPUT_UNAVAILABLE", "Primary output is inconsistent.")
            return {**operation, "primary_artifact_id": artifact["id"],
                    "outputs": [output_summary(artifact)]}
        except (UnfoldError, KeyError) as exc:
            return {**operation, "outputs": [], "output_error": {
                "code": "PRIMARY_OUTPUT_UNAVAILABLE",
                "message": str(exc), "remedy": "Inspect the exact revision; export an explicit artifact ID.",
            }}

    def _export_intent(self, request_id, destination, payload, extension):
        """Look up spelling before resolving: cwd/symlink changes cannot redirect retries."""
        raw = os.fspath(destination)
        try:
            intent = self.store.get("mutation-intent-" + request_id, "mutation_intent")
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
        else:
            prior = export_payload(intent, payload)
            if prior["destination"] != raw:
                raise UnfoldError("REQUEST_CONFLICT", "Export identity has different input.")
            return prior
        try:
            self.store.get(request_id)
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
        else:
            raise UnfoldError("REQUEST_CONFLICT", "Export identity belongs to other work.")
        require_publication()
        path = Path(raw).expanduser()
        name = path.name
        if (not name or name.endswith((".", " ")) or reserved(name)
                or any(unicodedata.category(c).startswith("C") or c in '<>:"/\\|?*'
                       for c in name)
                or len(name.encode("utf-8")) > 255
                or path.suffix.lower() != "." + extension):
            raise UnfoldError("INVALID_DESTINATION", "Use a safe filename with ." + extension,
                              "Keep an explicit extension; avoid device names and reserved characters.")
        try:
            path = path.parent.resolve(strict=True) / name
            parent = preflight(path)
        except OSError as exc:
            raise UnfoldError("INVALID_DESTINATION", "Export parent must exist and be accessible.",
                              "Create/select a writable parent directory before creation.") from exc
        retained = {
            "destination": raw, "path": str(path), "parent_identity": parent,
            "binding": payload, "staging_path": str(path.parent / (".unfold-" + request_id + ".tmp")),
        }
        return self.retain_mutation_intent("export-file", request_id, retained)["payload"]

    def export_file(self, artifact_id, destination, request_id=None):
        """Copy one exact retained artifact; never render, overwrite, or initialize a model."""
        request_id = request_id or uid()
        # Read metadata only; completed receipts survive missing/renamed retained bytes.
        try:
            prior = self.store.get("mutation-intent-" + request_id, "mutation_intent")
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
            artifact = self.store.get(artifact_id, "artifact")
            extension = artifact.get("format", "mp4")
        else:
            payload = export_payload(prior, {"artifact_id": artifact_id})
            extension = Path(payload["path"]).suffix.lstrip(".")
        intent = self._export_intent(request_id, destination, {"artifact_id": artifact_id}, extension)
        return self._run_export(artifact_id, request_id, intent)

    def _run_export(self, artifact_id, request_id, intent):
        base = {"receipt_id": request_id, "artifact_id": artifact_id}
        try:
            base["revision_id"] = self.store.get(artifact_id, "artifact")["revision_id"]
        except UnfoldError:
            pass  # A completed receipt can outlive removed material.
        try:
            return self._mutation(
                "export-file", request_id, {**intent, "artifact_id": artifact_id},
                lambda: self._copy_export(artifact_id, intent, base),
            )
        except UnfoldError as exc:
            return {**base, "status": "incomplete" if exc.code == "MUTATION_INCOMPLETE"
                    else "failed", "error": exc.as_dict()}
        except (OSError, sqlite3.Error, KeyboardInterrupt):
            return {**base, "status": "incomplete", "error": {
                "code": "MUTATION_INCOMPLETE",
                "message": "Delivery acknowledgement was interrupted or could not be retained.",
                "remedy": "Inspect the receipt/output; do not regenerate or blindly repeat copying.",
            }}

    def _copy_export(self, artifact_id, intent, base):
        path, staging = Path(intent["path"]), Path(intent["staging_path"]).name
        owned = None
        published = False
        try:
            require_publication()
            artifact = self.store.get(artifact_id, "artifact")
            base = {**base, "revision_id": artifact["revision_id"]}
            revision = self.store.get(artifact["revision_id"], "revision")
            if self.backend.source_hash(self.store.root / revision["source"]) != revision["source_sha256"]:
                raise UnfoldError("MATERIAL_CHANGED", "Retained source changed; export refused.")
            with parent_descriptor(path.parent) as parent:
                check_parent(path.parent, intent["parent_identity"], parent)
                try:
                    with self.store.open_relative(
                        artifact["relative_path"], os.O_RDONLY | os.O_NONBLOCK
                    ) as source, delivery_errors():
                        before = os.fstat(source)
                        if not stat.S_ISREG(before.st_mode):
                            raise UnfoldError("MATERIAL_CHANGED", "Output must be a regular file.")
                        dest = os.open(staging, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                       0o600, dir_fd=parent)
                        owned = identity(os.fstat(dest))
                        try:
                            checksum = hashlib.sha256()
                            while block := os.read(source, 1024 * 1024):
                                checksum.update(block)
                                write_all(dest, block)
                            after = os.fstat(source)
                            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                                    or checksum.hexdigest() != artifact["sha256"]
                                    or identity(self.store.secure_stat(artifact["relative_path"]))
                                    != identity(before)):
                                raise UnfoldError("MATERIAL_CHANGED", "Copied source changed.")
                            os.fsync(dest)
                            os.lseek(dest, 0, os.SEEK_SET)
                            checked = hashlib.sha256()
                            while block := os.read(dest, 1024 * 1024):
                                checked.update(block)
                            if checked.hexdigest() != artifact["sha256"]:
                                raise UnfoldError("MATERIAL_CHANGED", "Staged bytes differ.")
                            check_parent(path.parent, intent["parent_identity"], parent)
                            if identity(os.stat(staging, dir_fd=parent,
                                                follow_symlinks=False)) != owned:
                                raise UnfoldError("MATERIAL_CHANGED", "Staging file was replaced.")
                            # link is atomic and fails if any destination entry exists.
                            try:
                                os.link(staging, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                                        follow_symlinks=False)
                            except FileExistsError as exc:
                                raise UnfoldError("OUTPUT_EXISTS", "Destination already exists.",
                                                  "Choose a new destination and request ID.") from exc
                            published = True
                            if identity(os.stat(path.name, dir_fd=parent,
                                                follow_symlinks=False)) != owned:
                                raise UnfoldError("MATERIAL_CHANGED", "Published entry was replaced.")
                            check_parent(path.parent, intent["parent_identity"], parent)
                            os.lseek(dest, 0, os.SEEK_SET)
                            final = hashlib.sha256()
                            while block := os.read(dest, 1024 * 1024):
                                final.update(block)
                            if final.hexdigest() != artifact["sha256"]:
                                raise UnfoldError("MATERIAL_CHANGED", "Published bytes changed.")
                            os.fsync(parent)
                        finally:
                            os.close(dest)
                finally:
                    if owned is not None:
                        try:
                            if identity(os.stat(staging, dir_fd=parent,
                                                follow_symlinks=False)) == owned:
                                os.unlink(staging, dir_fd=parent)
                        except FileNotFoundError:
                            pass
            return {**base, "status": "completed", "path": str(path),
                    "sha256": artifact["sha256"], "ownership": "caller-owned copy",
                    "source_preserved": True}
        except (UnfoldError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as exc:
            error = exc if isinstance(exc, UnfoldError) else UnfoldError(
                "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else
                "OUTPUT_EXISTS" if isinstance(exc, FileExistsError) else "EXPORT_FAILED",
                "Exact-file delivery did not complete.",
                "Inspect the receipt and destination; export the retained artifact with a new "
                "request ID and destination. Do not regenerate.",
            )
            if not error.remedy:
                error = UnfoldError(error.code, error.message,
                                    "Inspect retained material and this receipt; use export-file "
                                    "with an explicit artifact, new destination and ID. Do not regenerate.")
            return {**base, "status": "incomplete" if published or isinstance(exc, KeyboardInterrupt)
                    else "failed", "error": error.as_dict()}

    def _create_export(self, brief, grant, request_id, export_to):
        request_id = request_id or uid()
        export_id = hashlib.sha256(("create-export:" + request_id).encode()).hexdigest()[:32]
        try:
            existing = self.store.get("mutation-intent-" + export_id, "mutation_intent")
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
            existing = None
        if export_to is None and existing is None:
            return self._operation_outputs(self._produce(brief, grant, request_id=request_id))
        if existing is None:
            try:
                self.store.get(request_id)
            except UnfoldError as exc:
                if exc.code != "NOT_FOUND":
                    raise
            else:
                raise UnfoldError("REQUEST_CONFLICT",
                                  "Creation had no export intent; use export-file with a new ID.")
        # Bind even the crash-before-production gap, without passing paths to intelligence.
        generation = hashlib.sha256(json.dumps({
            "brief": Brief.model_validate(Brief.model_validate(brief).model_dump()).model_dump(),
            "grant": Grant.model_validate(grant).model_dump(),
        }, sort_keys=True).encode()).hexdigest()
        binding = {
            "operation_id": request_id, "generation_sha256": generation,
        }
        if existing is not None:
            payload = export_payload(existing, binding)
            if export_to is None:
                export_to = payload["destination"]
        intent = self._export_intent(export_id, export_to, binding, "mp4")
        try:
            self.store.get(request_id)
        except UnfoldError as exc:
            if exc.code != "NOT_FOUND":
                raise
            # An intent alone is not paid admission. After a crash before _produce,
            # check the retained target again, without resolving its original alias.
            try:
                preflight(Path(intent["path"]), intent["parent_identity"])
            except OSError as exc:
                raise UnfoldError("INVALID_DESTINATION", "Retained export parent is inaccessible.",
                                  "Inspect the destination before a new creation request.") from exc
        operation = self._operation_outputs(self._produce(brief, grant, request_id=request_id))
        result = {"status": "not_started", "receipt_id": export_id,
                  "artifact_id": operation.get("primary_artifact_id"),
                  "revision_id": operation.get("revision_id")}
        if operation["status"] == "completed":
            if not operation.get("outputs"):
                result.update(status="failed", error=operation["output_error"])
            else:
                result = self._run_export(operation["primary_artifact_id"], export_id, intent)
        else:
            result["error"] = {
                "code": "GENERATION_NOT_COMPLETED",
                "message": "No export of uncommitted production.",
                "remedy": "Inspect/reconcile the operation; never automatically regenerate.",
            }
        return {**operation, "export": result}