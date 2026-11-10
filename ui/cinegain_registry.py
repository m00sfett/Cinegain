"""
Persistent registry of every application that has ever played audio.

Each application keeps its routing choice ("profile") across pauses, GUI
restarts and reboots. Applications are identified by application name *and*
process binary: Electron apps such as the ChatGPT desktop client report
`application.name = "Chromium"` and would otherwise collapse into one entry.

File format (~/.config/cinegain/apps.json):
    {"version": 1, "apps": {"<key>": {"name", "binary", "label", "profile",
                                      "first_seen", "last_seen"}}}
"""
import os
import time

import cinegain_paths as paths

PROFILE_OFF = "off"
PROFILE_STANDARD = "standard"

# Applications that are routed through Cinegain the first time they show up.
DEFAULT_MEDIA_APPS = (
    "google chrome", "chrome", "chromium", "firefox", "librewolf", "brave", "vivaldi",
    "opera", "vlc", "mpv", "celluloid", "totem", "kodi", "spotify", "jellyfin", "plex",
    "freetube", "haruna", "smplayer", "stremio",
)

# Generic runtime names that say nothing about the actual app; the binary is
# the better label for these (e.g. "Chromium" + binary "ChatGPT" -> "ChatGPT").
GENERIC_APP_NAMES = ("chromium", "electron", "chrome", "webkit", "qtwebengine", "python3", "java")

# Last-seen timestamps are flushed to disk at most this often.
LAST_SEEN_FLUSH_S = 60.0


def make_key(app_name, binary):
    name = (app_name or "").strip().lower()
    binary = os.path.basename((binary or "").strip()).lower()
    return f"{binary}::{name}"


def default_label(app_name, binary):
    name = (app_name or "").strip()
    binary = os.path.basename((binary or "").strip())
    if binary and name.lower() in GENERIC_APP_NAMES and binary.lower() != name.lower():
        return binary
    return name or binary or "?"


def is_media_app(app_name, binary):
    # An Electron app reporting "Chromium" is judged by its binary alone, so the
    # ChatGPT client is not mistaken for a browser.
    if default_label(app_name, binary) != (app_name or "").strip():
        hay = os.path.basename(binary or "").lower()
    else:
        hay = f"{app_name} {os.path.basename(binary or '')}".lower()
    return any(m in hay for m in DEFAULT_MEDIA_APPS)


class AppRegistry:
    def __init__(self, path=None, clock=time.time):
        self.path = path or paths.APPS_FILE
        self.clock = clock
        self.apps = {}
        self._mtime = None
        self._dirty = False
        self._last_flush = 0.0
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self):
        data = paths.read_json(self.path, default={}) or {}
        apps = data.get("apps") if isinstance(data, dict) else None
        self.apps = {}
        if isinstance(apps, dict):
            for key, entry in apps.items():
                if not isinstance(entry, dict):
                    continue
                self.apps[key] = {
                    "name": str(entry.get("name", "")),
                    "binary": str(entry.get("binary", "")),
                    "label": str(entry.get("label") or default_label(entry.get("name"), entry.get("binary"))),
                    "profile": str(entry.get("profile") or PROFILE_OFF),
                    "first_seen": float(entry.get("first_seen") or 0.0),
                    "last_seen": float(entry.get("last_seen") or 0.0),
                }
        self._mtime = self._file_mtime()
        self._dirty = False

    def _file_mtime(self):
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return None

    def reload_if_changed(self):
        """Picks up edits made by another process (CLI vs. GUI)."""
        mtime = self._file_mtime()
        if mtime != self._mtime:
            pending = {k: v["last_seen"] for k, v in self.apps.items()}
            self.load()
            for key, seen in pending.items():
                if key in self.apps and seen > self.apps[key]["last_seen"]:
                    self.apps[key]["last_seen"] = seen
            return True
        return False

    def save(self):
        paths.write_json_atomic(self.path, {"version": 1, "apps": self.apps})
        self._mtime = self._file_mtime()
        self._dirty = False
        self._last_flush = self.clock()

    def flush(self, force=False):
        """Writes pending last-seen updates, throttled unless forced."""
        if self._dirty and (force or self.clock() - self._last_flush >= LAST_SEEN_FLUSH_S):
            self.save()

    # -- queries -------------------------------------------------------------
    def get(self, key):
        return self.apps.get(key)

    def entries(self):
        return [dict(v, key=k) for k, v in self.apps.items()]

    def profiles_in_use(self):
        return {e["profile"] for e in self.apps.values()}

    # -- mutations -----------------------------------------------------------
    def observe(self, app_name, binary):
        """Registers a playing application; returns its key. New apps are saved at once."""
        key = make_key(app_name, binary)
        now = self.clock()
        entry = self.apps.get(key)
        if entry is None:
            self.apps[key] = {
                "name": app_name or "",
                "binary": os.path.basename(binary or ""),
                "label": default_label(app_name, binary),
                "profile": PROFILE_STANDARD if is_media_app(app_name, binary) else PROFILE_OFF,
                "first_seen": now,
                "last_seen": now,
            }
            self.save()
        else:
            entry["last_seen"] = now
            self._dirty = True
        return key

    def set_profile(self, key, profile):
        if key in self.apps and self.apps[key]["profile"] != profile:
            self.apps[key]["profile"] = profile
            self.save()

    def rename(self, key, label):
        label = (label or "").strip()
        if key in self.apps:
            entry = self.apps[key]
            entry["label"] = label or default_label(entry["name"], entry["binary"])
            self.save()

    def forget(self, key):
        if self.apps.pop(key, None) is not None:
            self.save()

    def replace_profile(self, old, new):
        """Moves every app using profile `old` to `new` (e.g. after a preset slot was cleared)."""
        changed = False
        for entry in self.apps.values():
            if entry["profile"] == old:
                entry["profile"] = new
                changed = True
        if changed:
            self.save()
        return changed
