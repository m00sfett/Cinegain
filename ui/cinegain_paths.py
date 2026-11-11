"""
Shared filesystem locations and small file helpers for Cinegain.

All per-user state lives below CONFIG_DIR. Runtime files (generated PipeWire
configs, PID files) live below RUNTIME_DIR. Both can be overridden through
environment variables, which the test-suite uses to stay away from real data.
"""
import json
import os
import stat
import tempfile


def _base_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, ".."))


BASE_DIR = os.environ.get("CINEGAIN_BASE_DIR") or _base_dir()
CONFIG_DIR = os.environ.get("CINEGAIN_CONFIG_DIR") or os.path.expanduser("~/.config/cinegain")
DATA_DIR = os.environ.get("CINEGAIN_DATA_DIR") or os.path.expanduser("~/.local/share/cinegain")


def _is_private_dir(path, uid):
    """A real directory (no symlink) owned by uid that nobody else can enter."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == uid and not st.st_mode & 0o077


def fallback_runtime_base(uid=None, tmp=None):
    """
    Runtime base when $XDG_RUNTIME_DIR is missing (#42): /tmp/cinegain-<uid>, created
    0700. An existing one is only used if it is a real directory owned by this user
    (an old 0755 one of ours is tightened to 0700). Anything else (symlink, other
    owner) could be a trap, so an unpredictable private directory is used instead.
    """
    uid = os.getuid() if uid is None else uid
    tmp = tmp or tempfile.gettempdir()
    base = os.path.join(tmp, f"cinegain-{uid}")
    try:
        os.mkdir(base, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return tempfile.mkdtemp(prefix=f"cinegain-{uid}-", dir=tmp)
    try:
        st = os.lstat(base)
        if stat.S_ISDIR(st.st_mode) and st.st_uid == uid and st.st_mode & 0o077:
            os.chmod(base, 0o700)  # lstat said: real directory of ours, not a link
    except OSError:
        pass
    if _is_private_dir(base, uid):
        return base
    return tempfile.mkdtemp(prefix=f"cinegain-{uid}-", dir=tmp)


def _runtime_dir():
    if os.environ.get("CINEGAIN_RUNTIME_DIR"):
        return os.environ["CINEGAIN_RUNTIME_DIR"]
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(xdg if xdg else fallback_runtime_base(), "cinegain")


RUNTIME_DIR = _runtime_dir()
LOG_DIR = os.environ.get("CINEGAIN_LOG_DIR") or os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "cinegain"
)
WIREPLUMBER_STREAM_PROPS = os.environ.get("CINEGAIN_WP_STREAM_PROPS") or os.path.expanduser(
    "~/.local/state/wireplumber/stream-properties"
)

STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
USER_PRESETS_FILE = os.path.join(CONFIG_DIR, "user_presets.json")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
APPS_FILE = os.path.join(CONFIG_DIR, "apps.json")
APPS_V1_BACKUP = os.path.join(CONFIG_DIR, "apps.v1.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

PRESETS_PATH = os.path.join(BASE_DIR, "presets", "presets.json")
CONF_TEMPLATE_PATH = os.path.join(BASE_DIR, "config", "cinegain-standalone.conf")

# Files that make up the complete user state (used by reset and backup).
LOG_FILE = os.path.join(LOG_DIR, "cinegain.log")

USER_STATE_FILES = (STATE_FILE, USER_PRESETS_FILE, CONFIG_FILE, APPS_FILE, APPS_V1_BACKUP)


def read_json(path, default=None):
    """Returns parsed JSON from path, or default when missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_atomic(path, data):
    """Writes JSON through a temp file and rename, so readers never see half a file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path, text, mode=None):
    """
    Replaces a text file without a moment in which readers (e.g. WirePlumber) see
    half of it (#42): temp file in the same directory, same permission bits as the
    old file (or `mode`), fsync, rename, fsync of the directory.
    """
    directory = os.path.dirname(path) or "."
    if mode is None:
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), mode)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def private_dir(path):
    """Creates path (and parents) and makes the leaf 0700: backups hold user state (#42)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def plugin_so_path():
    """Locates the compiled LADSPA plugin."""
    candidates = [
        os.path.join(BASE_DIR, "src", "cinegain.so"),
        os.path.join(BASE_DIR, "lib", "cinegain.so"),
        os.path.expanduser("~/.local/lib/ladspa/cinegain.so"),
        "/usr/local/lib/ladspa/cinegain.so",
        "/usr/lib/ladspa/cinegain.so",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "cinegain.so"


def config_get(key, default=None):
    """One value of config.json (language, view level, sidebar width, ...)."""
    cfg = read_json(CONFIG_FILE, default={}) or {}
    return cfg.get(key, default) if isinstance(cfg, dict) else default


def config_set(**values):
    """Merges values into config.json; other keys stay untouched."""
    cfg = read_json(CONFIG_FILE, default={}) or {}
    cfg = cfg if isinstance(cfg, dict) else {}
    cfg.update(values)
    write_json_atomic(CONFIG_FILE, cfg)
