"""
Shared filesystem locations and small file helpers for Cinegain.

All per-user state lives below CONFIG_DIR. Runtime files (generated PipeWire
configs, PID files) live below RUNTIME_DIR. Both can be overridden through
environment variables, which the test-suite uses to stay away from real data.
"""
import json
import os
import tempfile


def _base_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, ".."))


BASE_DIR = os.environ.get("CINEGAIN_BASE_DIR") or _base_dir()
CONFIG_DIR = os.environ.get("CINEGAIN_CONFIG_DIR") or os.path.expanduser("~/.config/cinegain")
DATA_DIR = os.environ.get("CINEGAIN_DATA_DIR") or os.path.expanduser("~/.local/share/cinegain")
RUNTIME_DIR = os.environ.get("CINEGAIN_RUNTIME_DIR") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/cinegain-{os.getuid()}", "cinegain"
)
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
