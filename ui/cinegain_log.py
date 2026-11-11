"""
Debug log shared by the GUI, the CLI and the routing engine.

Everything goes to one rotating file (LOG_FILE, default
~/.local/state/cinegain/cinegain.log, 5 x 2 MB) so a problem can be traced
afterwards: which stream appeared, where it was moved and why, which filter
process was started or stopped, which parameters were pushed, what the plugin's
meters read, and every uncaught exception.

The level comes from $CINEGAIN_LOG_LEVEL or config.json ("log_level"); the
default is DEBUG. Events are logged when something *changes*, so steady state
writes almost nothing.
"""
import logging
import logging.handlers
import os
import sys
import threading

import cinegain_paths as paths

ROOT = "cinegain"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 4
FORMAT = "%(asctime)s.%(msecs)03d %(process)d %(component)s %(levelname)-5s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

# Library use without setup() (tests, imports) stays silent instead of printing to stderr.
logging.getLogger(ROOT).addHandler(logging.NullHandler())

_component = "?"
_configured = False
_lock = threading.Lock()


class _ComponentFilter(logging.Filter):
    def filter(self, record):
        record.component = _component
        return True


def _level_from_config():
    env = os.environ.get("CINEGAIN_LOG_LEVEL")
    if env:
        return env
    cfg = paths.read_json(paths.CONFIG_FILE, default={}) or {}
    return cfg.get("log_level", "DEBUG") if isinstance(cfg, dict) else "DEBUG"


def setup(component, level=None):
    """Configures file logging once per process. Never raises: logging must not break the app."""
    global _component, _configured
    with _lock:
        _component = component
        if _configured:
            return logging.getLogger(ROOT)
        root = logging.getLogger(ROOT)
        root.propagate = False
        name = str(level or _level_from_config()).upper()
        root.setLevel(getattr(logging, name, logging.DEBUG))
        try:
            os.makedirs(paths.LOG_DIR, mode=0o700, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                paths.LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
            )
        except OSError:
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter(FORMAT, DATEFMT))
        handler.addFilter(_ComponentFilter())
        root.addHandler(handler)
        _configured = True
        _install_excepthooks()
        return root


def get(name):
    """Logger for one module, e.g. get("router") -> cinegain.router."""
    return logging.getLogger(f"{ROOT}.{name}")


def _install_excepthooks():
    log = get("crash")
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = hook

    def thread_hook(args):
        log.critical("uncaught exception in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = thread_hook


def pipewire_log_path(profile_id):
    """stderr of one filter-chain process (overwritten on each start)."""
    return os.path.join(paths.LOG_DIR, f"pipewire-{profile_id}.log")


def tail(path=None, lines=200):
    """Last lines of the log (for `cinegain log`)."""
    path = path or paths.LOG_FILE
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-lines:]
    except OSError:
        return []
