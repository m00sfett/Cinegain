"""
Persistent registry of every program that has ever played audio.

Each program has
- an on/off switch ("enabled"): routed through Cinegain or straight to the speakers,
- either its own parameter set ("params") or None, which means it follows the
  shared "Standard" settings (the main sliders, state.json).

Programs are identified by application name *and* process binary: Electron apps
such as the ChatGPT desktop client report `application.name = "Chromium"` and
would otherwise collapse into one entry.

File format (~/.config/cinegain/apps.json), version 2:
    {"version": 2, "apps": {"<key>": {"name", "binary", "label", "enabled",
                                      "params", "first_seen", "last_seen"}}}

Version 1 files (0.2.0, a profile reference per program) are migrated on load:
the programs are kept, their old profile choice is dropped (user decision for
0.3.0), and the v1 file is backed up next to it as apps.v1.json.
"""
import hashlib
import os
import re
import shutil
import time

import cinegain_paths as paths

SCHEMA_VERSION = 2

# Programs that are routed through Cinegain the first time they show up.
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


def profile_id_for(key):
    """Stable, filesystem- and node-name-safe id for a program's own filter sink."""
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:24] or "app"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
    return f"app_{slug}_{digest}"


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


def _clean_params(raw):
    if not isinstance(raw, dict) or not raw:
        return None
    out = {}
    for k, v in raw.items():
        try:
            out[str(k).replace("cinegain:", "")] = float(v)
        except (TypeError, ValueError):
            continue
    return out or None


class AppRegistry:
    def __init__(self, path=None, clock=time.time):
        self.path = path or paths.APPS_FILE
        self.clock = clock
        self.apps = {}
        self._mtime = None
        self._dirty = False
        self._last_flush = 0.0
        self.migrated_from = None
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self):
        data = paths.read_json(self.path, default={}) or {}
        apps = data.get("apps") if isinstance(data, dict) else None
        version = data.get("version", 1) if isinstance(data, dict) else 1
        self.apps = {}
        if isinstance(apps, dict):
            for key, entry in apps.items():
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", ""))
                binary = str(entry.get("binary", ""))
                if version >= 2:
                    enabled = bool(entry.get("enabled", False))
                    params = _clean_params(entry.get("params"))
                else:
                    # 0.2.0 profile choices are dropped; programs start fresh.
                    enabled = is_media_app(name, binary)
                    params = None
                self.apps[key] = {
                    "name": name,
                    "binary": binary,
                    "label": str(entry.get("label") or default_label(name, binary)),
                    "enabled": enabled,
                    "params": params,
                    "first_seen": float(entry.get("first_seen") or 0.0),
                    "last_seen": float(entry.get("last_seen") or 0.0),
                }
        self._mtime = self._file_mtime()
        self._dirty = False
        if apps and version < SCHEMA_VERSION:
            self._migrate_from_v1()

    def _migrate_from_v1(self):
        backup = os.path.join(os.path.dirname(self.path), os.path.basename(paths.APPS_V1_BACKUP))
        if not os.path.exists(backup):
            shutil.copy2(self.path, backup)
        self.migrated_from = backup
        self.save()

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
        paths.write_json_atomic(self.path, {"version": SCHEMA_VERSION, "apps": self.apps})
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

    # -- mutations -----------------------------------------------------------
    def observe(self, app_name, binary):
        """Registers a playing program; returns its key. New programs are saved at once."""
        key = make_key(app_name, binary)
        now = self.clock()
        entry = self.apps.get(key)
        if entry is None:
            self.apps[key] = {
                "name": app_name or "",
                "binary": os.path.basename(binary or ""),
                "label": default_label(app_name, binary),
                "enabled": is_media_app(app_name, binary),
                "params": None,
                "first_seen": now,
                "last_seen": now,
            }
            self.save()
        else:
            entry["last_seen"] = now
            self._dirty = True
        return key

    def set_enabled(self, key, enabled):
        if key in self.apps and self.apps[key]["enabled"] != bool(enabled):
            self.apps[key]["enabled"] = bool(enabled)
            self.save()

    def set_params(self, key, params, save=True):
        """Gives a program its own parameters (None: follow Standard again)."""
        if key not in self.apps:
            return
        self.apps[key]["params"] = _clean_params(params)
        if save:
            self.save()
        else:
            self._dirty = True

    def rename(self, key, label):
        label = (label or "").strip()
        if key in self.apps:
            entry = self.apps[key]
            entry["label"] = label or default_label(entry["name"], entry["binary"])
            self.save()

    def forget(self, key):
        if self.apps.pop(key, None) is not None:
            self.save()
