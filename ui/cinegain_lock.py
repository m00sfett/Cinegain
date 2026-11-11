"""
Only one process may distribute audio streams: the GUI or `cinegain watch`.
Two routers would take turns moving the same stream between sinks (#18).

The lock is an flock on RUNTIME_DIR/router.lock, so it disappears with the
process even after a crash or kill (no stale lock file to clean up). The file
holds "<owner> <pid>" for messages.
"""
import fcntl
import os

import cinegain_log
import cinegain_paths as paths

log = cinegain_log.get("lock")


def lock_path():
    return os.path.join(paths.RUNTIME_DIR, "router.lock")


def gui_socket_path():
    """Local socket a second GUI start uses to bring the running window to the front."""
    return os.path.join(paths.RUNTIME_DIR, "gui.sock")


class RouterLock:
    def __init__(self, owner, path=None):
        self.owner = owner
        self.path = path or lock_path()
        self.fd = None

    def acquire(self):
        """True if this process now distributes the streams."""
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            log.warning("%s: routing already owned by %s", self.owner, self.holder() or "?")
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{self.owner} {os.getpid()}\n".encode())
        self.fd = fd
        log.info("%s owns routing (pid %s)", self.owner, os.getpid())
        return True

    def holder(self):
        """"<owner> <pid>" of the process holding the lock, or ""."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def holder_owner(self):
        return (self.holder().split() or [""])[0]

    def release(self):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
