"""
Persistent registry of every program that has ever played audio.

Each program has
- an on/off switch ("enabled"): routed through Cinegain or straight to the speakers,
- either its own parameter set ("params") or None, which means it follows the
  shared "Standard" settings (the main sliders, state.json).

Programs are identified by application name *and* process binary: Electron apps
such as the ChatGPT desktop client report `application.name = "Chromium"` and
would otherwise collapse into one entry.

File format (~/.config/cinegain/apps.json), version 3:
    {"version": 3, "apps": {"<key>": {"name", "binary", "label", "enabled",
                                      "params", "mode", "sets",
                                      "first_seen", "last_seen"}}}

"mode" is "fixed" or "dynamic", "sets" holds one value set per mode and
"params" the effective parameters of the active mode (what the filter gets).
All three are null for a program that follows Standard. Version 2 files (0.3.x)
get mode and sets on load (see cinegain_modes.migrate).

Version 1 files (0.2.0, a profile reference per program) are migrated on load:
the programs are kept, their old profile choice is dropped (user decision for
0.3.0), and the v1 file is backed up next to it as apps.v1.json.
"""
import hashlib
import os
import re
import shutil
import time

import cinegain_log
import cinegain_modes as modes
import cinegain_paths as paths

log = cinegain_log.get("registry")

SCHEMA_VERSION = 3

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


# Linux reports "<binary> (deleted)" for a process whose executable was replaced
# on disk, e.g. Chrome after a package update while it keeps running.
DELETED_SUFFIX = re.compile(r"\s*\(deleted\)$", re.IGNORECASE)


def clean_binary(binary):
    """Process binary without path and without the "(deleted)" marker of an updated executable."""
    return DELETED_SUFFIX.sub("", os.path.basename((binary or "").strip()))


def make_key(app_name, binary):
    name = (app_name or "").strip().lower()
    return f"{clean_binary(binary).lower()}::{name}"


def profile_id_for(key):
    """Stable, filesystem- and node-name-safe id for a program's own filter sink."""
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:24] or "app"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
    return f"app_{slug}_{digest}"


def default_label(app_name, binary):
    name = (app_name or "").strip()
    binary = clean_binary(binary)
    if binary and name.lower() in GENERIC_APP_NAMES and binary.lower() != name.lower():
        return binary
    return name or binary or "?"


def is_media_app(app_name, binary):
    # An Electron app reporting "Chromium" is judged by its binary alone, so the
    # ChatGPT client is not mistaken for a browser.
    if default_label(app_name, binary) != (app_name or "").strip():
        hay = clean_binary(binary).lower()
    else:
        hay = f"{app_name} {clean_binary(binary)}".lower()
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
                mode, sets = None, None
                if params is not None:
                    mode, sets = modes.migrate(params, entry.get("mode"), entry.get("sets"))
                    params = modes.effective(mode, sets)
                self.apps[key] = {
                    "name": name,
                    "binary": binary,
                    "label": str(entry.get("label") or default_label(name, binary)),
                    "enabled": enabled,
                    "params": params,
                    "mode": mode,
                    "sets": sets,
                    "first_seen": float(entry.get("first_seen") or 0.0),
                    "last_seen": float(entry.get("last_seen") or 0.0),
                }
        self._mtime = self._file_mtime()
        self._dirty = False
        if apps and version < 2:
            self._migrate_from_v1()
        elif apps and version < SCHEMA_VERSION:
            log.info("apps.json v%s -> v%s: programs got a mode (fixed/dynamic)", version, SCHEMA_VERSION)
            self.save()
        if self._merge_deleted_binaries():
            self.save()

    def _merge_deleted_binaries(self):
        """
        Folds entries created for "<binary> (deleted)" (0.3.4 and older) into the
        program they belong to. The more recently used entry wins for the switch
        and the own setting, so changes made after the update are kept.
        """
        changed = False
        for key in [k for k in self.apps if DELETED_SUFFIX.search(k.split("::", 1)[0])]:
            entry = self.apps.pop(key)
            name = entry["name"]
            target = make_key(name, entry["binary"])
            entry["binary"] = clean_binary(entry["binary"])
            if DELETED_SUFFIX.search(entry["label"]):
                entry["label"] = default_label(name, entry["binary"])
            base = self.apps.get(target)
            if base is None:
                self.apps[target] = entry
            else:
                newer = entry if entry["last_seen"] > base["last_seen"] else base
                merged = dict(base)
                merged["enabled"] = newer["enabled"]
                own = newer if newer["params"] is not None else (base if base["params"] else entry)
                merged["params"], merged["mode"], merged["sets"] = own["params"], own["mode"], own["sets"]
                seen = [x for x in (base["first_seen"], entry["first_seen"]) if x]
                merged["first_seen"] = min(seen) if seen else 0.0
                merged["last_seen"] = max(base["last_seen"], entry["last_seen"])
                self.apps[target] = merged
            log.info("merged program %r into %r (executable was replaced by an update)", key, target)
            changed = True
        return changed

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
                "binary": clean_binary(binary),
                "label": default_label(app_name, binary),
                "enabled": is_media_app(app_name, binary),
                "params": None,
                "mode": None,
                "sets": None,
                "first_seen": now,
                "last_seen": now,
            }
            log.info("new program %r (label %r, %s)", key, self.apps[key]["label"],
                     "routed through Cinegain" if self.apps[key]["enabled"] else "not routed")
            self.save()
        else:
            entry["last_seen"] = now
            self._dirty = True
        return key

    def set_enabled(self, key, enabled):
        if key in self.apps and self.apps[key]["enabled"] != bool(enabled):
            self.apps[key]["enabled"] = bool(enabled)
            log.info("program %r switched %s", key, "on" if enabled else "off")
            self.save()

    def set_params(self, key, params, save=True):
        """
        Merges values into the program's active mode (None: follow Standard again).
        A program without its own setting gets one; its mode follows the values
        (leveler or compressor in use -> dynamic, otherwise fixed).
        """
        if key not in self.apps:
            return
        entry = self.apps[key]
        cleaned = _clean_params(params)
        if cleaned is None:
            self.set_setting(key, None, None, save=save)
            return
        mode = entry["mode"] or (modes.MODE_DYNAMIC if modes.is_dynamic_params(cleaned) else modes.MODE_FIXED)
        sets = entry["sets"] or {modes.MODE_FIXED: modes.neutral_fixed(),
                                 modes.MODE_DYNAMIC: modes.neutral_dynamic()}
        self.set_setting(key, mode, modes.apply_values(mode, sets, cleaned), save=save)

    def set_setting(self, key, mode, sets, save=True):
        """Own setting of a program: its mode and both value sets (None: follow Standard)."""
        if key not in self.apps:
            return
        entry = self.apps[key]
        before = entry["params"]
        if mode is None:
            entry["params"], entry["mode"], entry["sets"] = None, None, None
        else:
            sets = modes.clamp_sets(sets)
            entry["params"], entry["mode"], entry["sets"] = modes.effective(mode, sets), mode, sets
        if save:
            self.save()
        else:
            self._dirty = True
        if (before is None) != (entry["params"] is None):
            log.info("program %r now %s", key,
                     "follows Standard" if self.apps[key]["params"] is None else "has its own setting")

    def rename(self, key, label):
        label = (label or "").strip()
        if key in self.apps:
            entry = self.apps[key]
            entry["label"] = label or default_label(entry["name"], entry["binary"])
            self.save()

    def forget(self, key):
        if self.apps.pop(key, None) is not None:
            log.info("forgot program %r", key)
            self.save()
