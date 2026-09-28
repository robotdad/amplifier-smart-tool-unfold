"""Same-library retained revision fork. Copy bytes, never regenerate or execute.

Publication of project/revision/artifacts AND completed receipt is one transaction.
A durable pending intent precedes filesystem work; uncertainty never replays it.
"""

import copy
import hashlib
import json
import os
import re
import stat
import time
from contextlib import ExitStack
from pathlib import PurePosixPath

from .models import UnfoldError
from .store import uid, write_all

MAX_FILES = 1024
MAX_DIRS = 32
MAX_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_METADATA = 1024 * 1024
MAX_SECONDS = 120
ID = re.compile(r"^[a-f0-9]{32}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
ROOT_FILES = {
    "index.html", "gsap.min.js", "scene.json", "babylon.js", "scene3d_runtime.js",
    "layer_acquire.cjs", "layer_labels.js", "layer_player.js",
    "resources.json", "fonts.json", "screens.json",
}


def _fail(message):
    raise UnfoldError("MATERIAL_CHANGED", message)


def _relative(value):
    if (not isinstance(value, str) or "\\" in value or "\x00" in value
            or not value or value.startswith("/") or any(p in {"", ".", ".."} for p in value.split("/"))):
        _fail("Fork material needs a safe managed relative path.")
    path = PurePosixPath(value)
    if len(path.parts) < 3 or path.parts[0] != "operations" or not ID.fullmatch(path.parts[1]):
        _fail("Fork supports retained material in managed operation directories only.")
    return path


def _file_key(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _allowed_file(name):
    return (name in ROOT_FILES
            or re.fullmatch(r"fonts/[a-f0-9]{32}\.(ttf|otf)", name)
            or re.fullmatch(r"media/[a-f0-9]{32}\.(png|mp4|mov|webm|mkv|gif)", name)
            or re.fullmatch(r"media/screens/[a-z][a-z0-9_]{0,39}_[0-9]+\.png", name))


class _Files:
    """Hold no-follow ancestor descriptors and verify namespace + byte snapshots.

    Like the existing secure local store, this is not a sandbox against a hostile
    same-UID process that can replace the namespace after the final check.
    """
    def __init__(self, store):
        self.store = store
        self.stack = ExitStack()
        self.directories = {}
        self.end = time.monotonic() + MAX_SECONDS

    def check(self):
        if time.monotonic() >= self.end:
            raise UnfoldError("RESOURCE_LIMIT", "Fork copy allowance exhausted.")

    def __enter__(self):
        self.root = os.open(self.store.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.stack.callback(os.close, self.root)
        self.root_key = (os.fstat(self.root).st_dev, os.fstat(self.root).st_ino)
        self.directories[""] = self.root
        return self

    def __exit__(self, *exc):
        self.stack.close()

    def directory(self, relative, create=False):
        current, parts = "", PurePosixPath(relative).parts
        if len(parts) > 6:
            _fail("Fork directory nesting exceeds limit.")
        for part in parts:
            self.check()
            if part in {"", ".", ".."} or "/" in part or "\\" in part:
                _fail("Unsafe fork directory.")
            parent = self.directories[current]
            next_path = (current + "/" + part).lstrip("/")
            if next_path not in self.directories:
                if len(self.directories) >= MAX_DIRS:
                    raise UnfoldError("RESOURCE_LIMIT", "Fork directory count exceeds limit.")
                if create:
                    os.mkdir(part, 0o700, dir_fd=parent)  # exclusive, never merge old staging
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                self.stack.callback(os.close, descriptor)
                self.directories[next_path] = descriptor
            current = next_path
        return self.directories[current]

    def verify_namespace(self):
        self.check()
        root = os.stat(self.store.root, follow_symlinks=False)
        if not stat.S_ISDIR(root.st_mode) or (root.st_dev, root.st_ino) != self.root_key:
            _fail("Library root changed during fork.")
        for relative, descriptor in self.directories.items():
            if not relative:
                continue
            p = PurePosixPath(relative)
            parent = self.directories[str(p.parent) if str(p.parent) != "." else ""]
            actual = os.stat(p.name, dir_fd=parent, follow_symlinks=False)
            held = os.fstat(descriptor)
            if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (held.st_dev, held.st_ino):
                _fail("Managed directory changed during fork.")

    def inventory(self, source):
        self.check()
        result = {}
        total = 0

        def walk(relative, prefix=""):
            nonlocal total
            fd = self.directory(relative)
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.check()
                    names.append(entry.name)
                    if len(names) > MAX_FILES + MAX_DIRS:
                        raise UnfoldError("RESOURCE_LIMIT", "Retained directory exceeds fork entry limit.")
            for name in sorted(names):
                self.check()
                details = os.stat(name, dir_fd=fd, follow_symlinks=False)
                local = prefix + name
                if stat.S_ISDIR(details.st_mode):
                    if local not in {"media", "media/screens", "fonts"}:
                        _fail("Unsupported directory in retained source.")
                    walk(relative + "/" + name, local + "/")
                else:
                    if not _allowed_file(local):
                        _fail("Unsupported file in retained source: " + local[:160])
                    if len(result) >= MAX_FILES or total + details.st_size > MAX_BYTES:
                        raise UnfoldError("RESOURCE_LIMIT", "Retained source exceeds fork copy bounds.")
                    result[local] = self.file(relative + "/" + name, byte_limit=MAX_BYTES - total)
                    total += result[local]["size"]
                    if len(result) > MAX_FILES or total > MAX_BYTES:
                        raise UnfoldError("RESOURCE_LIMIT", "Retained source exceeds fork copy bounds.")
        walk(str(source))
        if not {"index.html", "gsap.min.js", "scene.json"} <= result.keys():
            _fail("Retained composition source is incomplete.")
        return result

    def file(self, relative, expected=None, target=None, *, byte_limit=MAX_FILE_BYTES):
        """Read/hash through held descriptors, optionally byte-copy to owned staging."""
        self.check()
        p = _relative(relative)
        parent = self.directory(str(p.parent))
        fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _fail("Fork material must be a regular file without hardlinks or symlinks.")
            cap = min(byte_limit, MAX_METADATA if p.name.endswith(".json") else MAX_FILE_BYTES)
            if before.st_size > cap:
                raise UnfoldError("RESOURCE_LIMIT", "Fork file exceeds its byte limit.")
            if expected and _file_key(before) != expected["identity"]:
                _fail("Retained file was replaced before copying.")
            dest = None
            try:
                if target is not None:
                    t = _relative(target)
                    out_parent = self.directory(str(t.parent), create=True)
                    dest = os.open(t.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600, dir_fd=out_parent)
                checksum, size = hashlib.sha256(), 0
                while True:
                    self.check()
                    block = os.read(fd, 1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > before.st_size or size > cap:
                        _fail("Retained file grew during copying.")
                    checksum.update(block)
                    if dest is not None:
                        write_all(dest, block)
                if dest is not None:
                    os.fsync(dest)
            finally:
                if dest is not None:
                    os.close(dest)
            after = os.fstat(fd)
            named = os.stat(p.name, dir_fd=parent, follow_symlinks=False)
            value = {"sha256": checksum.hexdigest(), "size": size, "identity": _file_key(after)}
            if _file_key(before) != _file_key(after) or _file_key(named) != _file_key(after):
                _fail("Retained file changed during fork.")
            if size != before.st_size or (expected and value != expected):
                _fail("Retained bytes changed during fork.")
            return value
        finally:
            os.close(fd)

    def flush(self):
        self.verify_namespace()
        for fd in reversed(list(self.directories.values())):
            os.fsync(fd)

    def read_json(self, relative, expected):
        """Read bounded manifest bytes through the same held no-follow chain."""
        self.check()
        path = _relative(relative)
        parent = self.directory(str(path.parent))
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (_file_key(before) != expected["identity"] or not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1 or before.st_size > MAX_METADATA):
                _fail("Copied manifest changed before validation.")
            chunks = bytearray()
            while True:
                self.check()
                block = os.read(fd, min(65536, MAX_METADATA + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
                if len(chunks) > MAX_METADATA:
                    raise UnfoldError("RESOURCE_LIMIT", "Manifest grew beyond its limit.")
            after = os.fstat(fd)
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (_file_key(after) != _file_key(before) or _file_key(named) != _file_key(before)
                    or hashlib.sha256(chunks).hexdigest() != expected["sha256"]):
                _fail("Copied manifest changed during validation.")
            return json.loads(chunks)
        finally:
            os.close(fd)


def _validate_source(files, directory, expected, inventory):
    """Validate manifest closure before backend source_hash reads copied files.

    This checks identities only; never author, probe media, execute or require a runtime.
    """
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        _fail("Retained revision has no valid source digest.")
    # The retained source_hash formula (backend.py) over already verified bytes.
    # Do NOT reopen a mutable pathname through Backend.source_hash: that would
    # discard our held descriptor chain between checking it and reading manifests.
    extra = ""
    referenced = set(ROOT_FILES)
    for filename in ("resources.json", "fonts.json", "screens.json"):
        if filename not in inventory:
            continue
        value = files.read_json(directory + "/" + filename, inventory[filename])
        if not isinstance(value, dict):
            _fail("Invalid source manifest.")
        if filename == "resources.json":
            for identity, checksum in sorted(value.items()):
                if (not ID.fullmatch(identity) or not isinstance(checksum, str) or not SHA.fullmatch(checksum)
                        or inventory.get(f"media/{identity}.png", {}).get("sha256") != checksum):
                    _fail("Retained image manifest does not match copied bytes.")
                extra += identity + checksum
                referenced.add(f"media/{identity}.png")
        elif filename == "fonts.json":
            for identity, face in sorted(value.items()):
                if (not ID.fullmatch(identity) or not isinstance(face, dict)
                        or face.get("suffix") not in {".ttf", ".otf"}
                        or not isinstance(face.get("sha256"), str) or not SHA.fullmatch(face["sha256"])):
                    _fail("Invalid retained font manifest.")
                if inventory.get(f"fonts/{identity}{face['suffix']}", {}).get("sha256") != face.get("sha256"):
                    _fail("Retained font is missing or changed.")
                extra += identity + face["sha256"]
                referenced.add(f"fonts/{identity}{face['suffix']}")
            extra += inventory[filename]["sha256"]
        else:
            for item in value.values():
                if not isinstance(item, dict):
                    _fail("Invalid retained video manifest.")
                identity, suffix = item.get("asset_id", ""), item.get("suffix")
                if (not isinstance(identity, str) or not ID.fullmatch(identity)
                        or suffix not in {".mp4", ".mov", ".webm", ".mkv", ".gif"}
                        or not isinstance(item.get("sha256"), str) or not SHA.fullmatch(item["sha256"])):
                    _fail("Invalid retained video manifest.")
                if inventory.get(f"media/{identity}{suffix}", {}).get("sha256") != item.get("sha256"):
                    _fail("Retained video is missing or changed.")
                referenced.add(f"media/{identity}{suffix}")
                pages = item.get("pages")
                if not isinstance(pages, list) or not pages:
                    _fail("Retained video has no frame atlas manifest.")
                for page in pages:
                    if (not isinstance(page, dict) or not isinstance(page.get("file"), str)
                            or not re.fullmatch(r"media/screens/[a-z][a-z0-9_]{0,39}_[0-9]+\.png", page["file"])
                            or not isinstance(page.get("sha256"), str) or not SHA.fullmatch(page["sha256"])
                            or inventory.get(page["file"], {}).get("sha256") != page["sha256"]):
                        _fail("Retained atlas is missing or changed.")
                    referenced.add(page["file"])
            extra += inventory[filename]["sha256"]
    if inventory.keys() - referenced:
        _fail("Unreferenced resources in retained source; fork did not copy unrelated material into a project.")
    names = ["index.html", "gsap.min.js", "scene.json"]
    names += [name for name in ("babylon.js", "scene3d_runtime.js",
                               "layer_acquire.cjs", "layer_labels.js", "layer_player.js")
              if name in inventory]
    actual = hashlib.sha256((extra + "".join(inventory[name]["sha256"] for name in names)).encode()).hexdigest()
    if actual != expected:
        _fail("Retained source digest does not match the selected revision.")


class Forks:
    def fork_revision(self, revision_id, name, *, request_id):
        """Byte-copy a retained revision into a new project; no render or model calls.

        Required request_id binds one deterministic fork. Read mutation_status after
        interruption; pending/incomplete never replays copying. Same-library only.
        """
        if (not isinstance(revision_id, str) or not ID.fullmatch(revision_id)
                or not isinstance(request_id, str) or not ID.fullmatch(request_id)
                or not isinstance(name, str) or not name.strip() or len(name) > 120):
            raise UnfoldError("INVALID_INPUT", "Use revision/request 32-hex IDs and a name of 1–120 characters.")
        payload = {"revision_id": revision_id, "name": name}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = self.store.get(request_id, db=db)
            except UnfoldError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                old = None
            if old:
                if (old.get("kind") != "mutation_receipt" or old.get("operation") != "fork_revision"
                        or old.get("fingerprint") != fingerprint):
                    raise UnfoldError("REQUEST_CONFLICT", "Request identity is bound to different work.")
                if old["status"] == "completed":
                    return old["result"]
                raise UnfoldError("MUTATION_INCOMPLETE", "Fork is pending or incomplete; no copying was replayed.",
                                  "Read mutation_status(request_id) for planned identities, cause and retained staging.")
            if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
                raise UnfoldError("UNSUPPORTED", "Revision fork requires POSIX no-follow directory descriptors.")
            revision = self.store.get(revision_id, "revision", db)
            project = self.store.get(revision["project_id"], "project", db)
            if revision_id not in project["revisions"]:
                _fail("Selected revision is not in its retained project history.")
            source = _relative(revision["source"])
            if source.name != "source" or len(source.parts) != 3:
                _fail("Unsupported retained source location.")
            if not isinstance(revision.get("brief"), dict):
                _fail("Selected revision has no retained brief.")
            identities = revision.get("artifacts")
            if not isinstance(identities, list) or not 1 <= len(identities) <= 8 or len(set(identities)) != len(identities):
                raise UnfoldError("UNSUPPORTED", "Fork requires 1–8 retained composition MP4 artifacts.")
            artifacts = [self.store.get(i, "artifact", db) for i in identities]
            for item in artifacts:
                path = _relative(item["relative_path"])
                if (item.get("revision_id") != revision_id or item.get("format", "mp4") != "mp4"
                        or path.suffix != ".mp4" or item.get("delivery_id") or item.get("delivery")
                        or item.get("alpha") or item.get("audio", "silent") != "silent"
                        or item.get("role", "composition") != "composition"
                        or item.get("source_sha256", revision["source_sha256"]) != revision["source_sha256"]
                        or not SHA.fullmatch(item.get("sha256", ""))):
                    raise UnfoldError("UNSUPPORTED", "Fork copies retained silent composition MP4s only, not delivery outputs.")
            if len(json.dumps([revision, artifacts]).encode()) > MAX_METADATA:
                raise UnfoldError("RESOURCE_LIMIT", "Fork record metadata exceeds 1 MiB.")
            planned = {"project_id": uid(), "revision_id": uid(), "artifact_ids": [uid() for _ in artifacts]}
            staging = "operations/" + uid()
            receipt = {"id": request_id, "kind": "mutation_receipt", "operation": "fork_revision",
                       "payload": payload, "fingerprint": fingerprint, "status": "pending",
                       "accepted_at": time.time(), "planned": planned, "staging_path": staging}
            self.store.put("mutation_receipt", receipt, db)
            self.store.event("mutation_accepted", request_id, {"operation": "fork_revision"}, db)
        try:
            return self._copy_fork(receipt, revision, artifacts)
        except BaseException as exc:
            # A failed/uncertain DB commit may have landed. Resolve from its receipt;
            # never report "not written" or replay the effect merely on an exception.
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self.store.get(request_id, "mutation_receipt", db)
                if current["status"] == "completed":
                    return current["result"]
                error = exc if isinstance(exc, UnfoldError) else UnfoldError(
                    "FORK_INCOMPLETE", "Fork stopped without a confirmed publication.",
                    "Inspect mutation_status; owned partial files may remain. No automatic replay.")
                current.update(status="incomplete", incomplete_at=time.time(), error=error.as_dict())
                self.store.put("mutation_receipt", current, db)
                self.store.event("mutation_incomplete", request_id, {"operation": "fork_revision"}, db)
            raise error from exc

    def _copy_fork(self, receipt, revision, artifacts):
        source = revision["source"]
        staging = receipt["staging_path"]
        target_source = staging + "/source"
        with _Files(self.store) as files:
            inventory = files.inventory(source)
            media = []
            available = MAX_BYTES - sum(v["size"] for v in inventory.values())
            for item in artifacts:
                checked = files.file(item["relative_path"], byte_limit=available)
                available -= checked["size"]
                media.append(checked)
            for item, checked in zip(artifacts, media):
                if checked["sha256"] != item["sha256"]:
                    _fail("Retained composition MP4 changed.")
            files.directory("operations")
            files.directory(staging, create=True)
            files.directory(target_source, create=True)
            for local, expected in inventory.items():
                files.file(source + "/" + local, expected, target_source + "/" + local)
            output_paths = []
            for index, (item, checked) in enumerate(zip(artifacts, media)):
                target = staging + f"/composition-{index}.mp4"
                files.file(item["relative_path"], checked, target)
                output_paths.append(target)
            copied = files.inventory(target_source)
            def projection(values):
                return {k: (v["sha256"], v["size"]) for k, v in values.items()}
            if projection(copied) != projection(inventory):
                _fail("Copied source differs from the verified snapshot.")
            files.verify_namespace()
            _validate_source(files, target_source, revision["source_sha256"], copied)
            files.check()
            # No expensive copying under the DB write lock. Publication rechecks the
            # selected records and every origin/destination byte immediately beforehand.
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if self.store.get(revision["id"], "revision", db) != revision:
                    _fail("Origin revision record changed during fork.")
                for item in artifacts:
                    if self.store.get(item["id"], "artifact", db) != item:
                        _fail("Origin artifact record changed during fork.")
                if files.inventory(source) != inventory or files.inventory(target_source) != copied:
                    _fail("Source snapshot changed before fork publication.")
                for item, checked, target in zip(artifacts, media, output_paths):
                    files.file(item["relative_path"], checked)
                    copied_media = files.file(target)
                    if (copied_media["sha256"], copied_media["size"]) != (checked["sha256"], checked["size"]):
                        _fail("Copied MP4 changed before publication.")
                files.flush()
                return self._publish_fork(receipt, revision, artifacts, output_paths, inventory, db)

    def _publish_fork(self, receipt, origin, artifacts, paths, inventory, db):
        """Single transaction owns every published identity and completion receipt."""
        current = self.store.get(receipt["id"], "mutation_receipt", db)
        if current != receipt:
            raise UnfoldError("MUTATION_INCOMPLETE", "Fork intent changed before publication.")
        planned = receipt["planned"]
        for identity in [planned["project_id"], planned["revision_id"], *planned["artifact_ids"]]:
            if db.execute("SELECT 1 FROM records WHERE id=?", (identity,)).fetchone():
                raise UnfoldError("REQUEST_CONFLICT", "Planned fork identity already exists.")
        provenance = {"method": "deterministic retained byte copy", "project_id": origin["project_id"],
                      "revision_id": origin["id"], "artifact_ids": [a["id"] for a in artifacts],
                      "source_sha256": origin["source_sha256"], "request_id": receipt["id"]}
        # Explicit allow-list: absent legacy provenance remains absent. No output
        # grant, pending feedback/draft, operation state or paid usage becomes new work.
        revision = {k: copy.deepcopy(origin[k]) for k in (
            "brief", "identity_version", "selected_identity", "identity_semantics",
            "identity_provenance", "limitations", "reference_id",
        ) if k in origin}
        inherited = {k: copy.deepcopy(origin[k]) for k in (
            "render", "model_review", "evidence", "reference_evidence", "backend", "usage",
            "feedback", "feedback_target", "operation_id", "fork_origin", "inherited_evidence",
        ) if k in origin}
        shared = {"scope": "same library; not an archive or portable import",
                  "identity_version": origin.get("identity_version", origin["brief"].get("identity_version")),
                  "reference_id": origin["brief"].get("reference_id"),
                  "note": "Selected packs/reference assets remain same-library references; they were not copied or newly validated. "
                          "Only resources retained inside the source tree and listed composition MP4s were copied."}
        revision.update(id=planned["revision_id"], kind="revision", project_id=planned["project_id"],
                        base_revision=None, source=receipt["staging_path"] + "/source",
                        source_sha256=origin["source_sha256"], artifacts=planned["artifact_ids"],
                        feedback="", feedback_target=None, fork_origin=provenance,
                        inherited_evidence={"origin_revision_id": origin["id"],
                            "disposition": "attributed origin evidence, not a new render/model review or usage charge",
                            "records": inherited}, shared_dependencies=shared)
        project = {"id": planned["project_id"], "kind": "project", "name": receipt["payload"]["name"],
                   "current_revision": revision["id"], "revisions": [revision["id"]], "fork_origin": provenance}
        outputs = []
        for old, identity, path in zip(artifacts, planned["artifact_ids"], paths):
            artifact = {k: copy.deepcopy(old[k]) for k in (
                "sha256", "source_sha256", "frame_count", "encoded_duration", "width", "height",
                "duration", "fps", "bytes", "audio", "alpha", "output", "time_basis",
                "rights", "attribution",
            ) if k in old}
            artifact.update(id=identity, kind="artifact", revision_id=revision["id"],
                            name=receipt["payload"]["name"], relative_path=path, format="mp4",
                            ownership="Unfold-managed", role="composition",
                            method="byte copy of retained composition; no new render/probe",
                            fork_origin={**provenance, "artifact_id": old["id"]},
                            inherited_metadata={"origin_artifact_id": old["id"],
                                "disposition": "render metadata inherited; copy digest verified, media not re-probed",
                                "method": old.get("method")})
            self.store.put("artifact", artifact, db)
            outputs.append({"artifact_id": identity, "origin_artifact_id": old["id"],
                            "sha256": old["sha256"], "format": "mp4"})
        self.store.put("project", project, db)
        self.store.put("revision", revision, db)
        result = {"request_id": receipt["id"], "receipt_id": receipt["id"], "status": "completed",
                  **planned, "origin": provenance, "source_sha256": origin["source_sha256"],
                  "source_files": {k: {"sha256": v["sha256"], "bytes": v["size"]} for k, v in inventory.items()},
                  "outputs": outputs, "shared_dependencies": shared,
                  "disposition": "verified byte copy; no authoring/rendering/intelligence or new review",
                  "original_project_unchanged": True}
        current.update(status="completed", completed_at=time.time(), result=result)
        self.store.put("mutation_receipt", current, db)
        self.store.event("revision_forked", revision["id"], result, db)
        self.store.event("mutation_completed", receipt["id"], {"operation": "fork_revision"}, db)
        return result