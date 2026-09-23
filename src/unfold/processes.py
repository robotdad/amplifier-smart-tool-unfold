"""Inspect and stop owned workers without platform-specific liveness signals."""

import os
import socket

import psutil


def process_identity(pid=None):
    """Persist birth identity, not just a reusable PID (trusted local store)."""
    process = psutil.Process(os.getpid() if pid is None else pid)
    return {
        "pid": process.pid,
        "created": process.create_time(),
        "boot": psutil.boot_time(),
        "host": socket.gethostname(),
    }


def owned_process(identity):
    """Return (live/absent/mismatch/unknown, verified psutil handle or None)."""
    if not identity or not all(k in identity for k in ("pid", "created", "boot", "host")):
        return "unknown", None
    if identity["host"] != socket.gethostname():
        return "unknown", None
    if identity["boot"] != psutil.boot_time():
        return "mismatch", None
    try:
        process = psutil.Process(identity["pid"])
        if process.create_time() != identity["created"]:
            return "mismatch", None
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return "absent", None
        return "live", process
    except psutil.NoSuchProcess:
        return "absent", None
    except psutil.AccessDenied:
        return "unknown", None


def is_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def stop_tree(process):
    """Stop a verified worker and its descendants, guarding against PID reuse."""
    if process.pid == os.getpid():
        raise ValueError("Cannot stop the calling process")
    try:
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        # Never signal a numeric process group: its leader may have exited and its
        # PID may have been reused. psutil's mutating methods check birth identity.
        frozen = []
        complete = True

        def freeze(parent):
            nonlocal complete
            if parent.status() == psutil.STATUS_ZOMBIE:
                raise psutil.NoSuchProcess(parent.pid)
            parent.suspend()
            frozen.append(parent)
            for child in parent.children():
                try:
                    freeze(child)
                except psutil.NoSuchProcess:
                    complete = False

        try:
            freeze(process)
            for child in reversed(frozen):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        except Exception:
            for child in reversed(frozen):
                try:
                    child.resume()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            raise
        _, alive = psutil.wait_procs(frozen, timeout=3)
        alive = [p for p in alive if is_alive(p.pid) and p.is_running()]
        if alive:
            raise TimeoutError("Owned worker processes did not stop")
        return complete
    except (psutil.NoSuchProcess, ProcessLookupError):
        # A vanished root is not proof that its descendants stopped.
        return False


def stop_worker(process):
    if process is None:
        return True
    if process.poll() is not None:
        return False
    stopped = False
    try:
        identity = getattr(process, "_unfold_identity", None)
        if identity:
            state, owned = owned_process(identity)
            if state == "live":
                stopped = stop_tree(owned)
            elif state == "unknown":
                raise RuntimeError("Cannot verify worker ownership; cleanup is incomplete")
        else:
            # A still-unreaped Popen child cannot have its PID reused.
            stopped = stop_tree(psutil.Process(process.pid))
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass
    process.wait(timeout=10)
    return stopped


def stop_recorded_worker(pid, request_path, identity=None):
    """Legacy PID/path alone is insufficient authority to signal any process."""
    state, process = owned_process(identity)
    if state == "live" and process.pid == pid:
        argv = process.cmdline()
        if len(argv) >= 4 and argv[1:4] == ["-m", "unfold.worker", str(request_path)]:
            return stop_tree(process)
    return False
