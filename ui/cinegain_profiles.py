"""
Parameter sets, templates and their PipeWire sinks.

"standard" is the shared parameter set (sink `cinegain_sink`, values in
state.json) that every program without its own setting follows. A program with
its own setting runs through its own sink `cinegain_app_<...>`, a separate small
PipeWire filter-chain process, so two programs can play at the same time with
different processing. Such sinks are started on demand and stopped again when
nothing has used them for a while (see Router).

Factory presets (presets.json) and the 8 user slots (user_presets.json) are
templates: loading one copies its values into the selected target.
"""
import json
import math
import os
import re
import signal
import subprocess
import time

import cinegain_log
import cinegain_paths as paths

log = cinegain_log.get("sinks")

PROFILE_STANDARD = "standard"

STANDARD_SINK = "cinegain_sink"
STANDARD_OUTPUT = "cinegain_output"
USER_SLOTS = tuple(f"user_{i}" for i in range(1, 7))  # 6 slots since 0.4.0 (8 before)


def default_slot_name(slot_id):
    """Name a user slot carries until its owner renames it; stored as is, shown translated."""
    return f"User {slot_id.rsplit('_', 1)[-1]}"


def slot_display_name(slot_id, name):
    """
    Name to show for a user slot. Only the stored default ("User 3") is translated
    into the UI language; a name the user typed is returned unchanged.
    """
    if slot_id in USER_SLOTS and name == default_slot_name(slot_id):
        try:
            from i18n import t
        except ImportError:
            return name
        return t("slot_default_name", n=slot_id.rsplit("_", 1)[-1])
    return name


# Factory default = the "Neutral (0 dB)" preset: Standard leaves audio untouched
# until the user changes it (presets.json clean_linear must stay in sync).
DEFAULT_PARAMS = {
    "bypass": 0.0,
    "input_db": 0.0,
    "highpass_hz": 10.0,
    "bass_db": 0.0,
    "dialog_db": 0.0,
    "dialog_hz": 2500.0,
    "dialog_q": 1.0,
    "treble_db": 0.0,
    "mid_db": 0.0,
    "side_db": 0.0,
    "leveler": 0.0,
    "lev_target_db": -18.0,
    "lev_max_boost_db": 0.0,
    "lev_max_cut_db": 0.0,
    "lev_speed_s": 2.0,
    "lev_gate_db": -55.0,
    "comp_thresh_db": 0.0,
    "comp_ratio": 1.0,
    "comp_knee_db": 0.0,
    "comp_attack_ms": 20.0,
    "comp_release_ms": 250.0,
    "makeup_db": 0.0,
    "lim_ceiling_db": -0.2,
    "lim_release_ms": 80.0,
}
BOOL_PARAMS = {"bypass", "leveler"}

# Control-port ranges of the LADSPA plugin (port_hints in src/cinegain.c; the plugin
# clamps to the same limits). tests/test_modes_volume.py compares both tables.
PARAM_LIMITS = {
    "bypass": (0.0, 1.0),
    "input_db": (-30.0, 40.0),
    "highpass_hz": (10.0, 400.0),
    "bass_db": (-15.0, 15.0),
    "dialog_db": (-15.0, 15.0),
    "dialog_hz": (300.0, 8000.0),
    "dialog_q": (0.2, 5.0),
    "treble_db": (-15.0, 15.0),
    "mid_db": (-15.0, 15.0),
    "side_db": (-30.0, 15.0),
    "leveler": (0.0, 1.0),
    "lev_target_db": (-45.0, -6.0),
    "lev_max_boost_db": (0.0, 36.0),
    "lev_max_cut_db": (0.0, 36.0),
    "lev_speed_s": (0.2, 60.0),
    "lev_gate_db": (-100.0, -20.0),
    "comp_thresh_db": (-70.0, 0.0),
    "comp_ratio": (1.0, 30.0),
    "comp_knee_db": (0.0, 30.0),
    "comp_attack_ms": (0.1, 500.0),
    "comp_release_ms": (5.0, 5000.0),
    "makeup_db": (-24.0, 40.0),
    "lim_ceiling_db": (-24.0, 0.0),
    "lim_release_ms": (5.0, 2000.0),
}


def _safe_id(profile_id):
    return re.sub(r"[^a-z0-9_]", "_", str(profile_id).lower())


def sink_name(profile_id):
    if profile_id == PROFILE_STANDARD:
        return STANDARD_SINK
    return f"cinegain_{_safe_id(profile_id)}"


def output_name(profile_id):
    if profile_id == PROFILE_STANDARD:
        return STANDARD_OUTPUT
    return f"cinegain_output_{_safe_id(profile_id)}"


def clamp_param(key, value):
    """Value inside the plugin's port range (unknown keys pass unchanged)."""
    low, high = PARAM_LIMITS.get(key, (None, None))
    if low is None:
        return value
    return max(low, min(high, value))


def normalize_params(raw):
    """Short-key floats inside the plugin's port ranges; NaN/Inf and non-numbers are dropped (#37)."""
    out = {}
    for k, v in (raw or {}).items():
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        key = str(k).replace("cinegain:", "")
        out[key] = clamp_param(key, value)
    return out


def non_finite_keys(raw):
    """Keys of a raw parameter dict whose number is NaN or infinite (the CLI refuses those)."""
    bad = []
    for k, v in (raw or {}).items():
        try:
            if not math.isfinite(float(v)):
                bad.append(str(k))
        except (TypeError, ValueError):
            continue
    return bad


class ProfileCatalog:
    """Knows which profiles exist and which parameters they carry."""

    def __init__(self, presets_path=None, user_presets_path=None, state_path=None):
        self.presets_path = presets_path or paths.PRESETS_PATH
        self.user_presets_path = user_presets_path or paths.USER_PRESETS_FILE
        self.state_path = state_path or paths.STATE_FILE

    def factory(self):
        raw = paths.read_json(self.presets_path, default={}) or {}
        return {
            pid: {"name": p.get("name", pid), "params": normalize_params(p.get("params"))}
            for pid, p in raw.items()
            if isinstance(p, dict)
        }

    def user_slots(self):
        raw = paths.read_json(self.user_presets_path, default={}) or {}
        slots = {}
        for sid in USER_SLOTS:
            entry = raw.get(sid) if isinstance(raw, dict) else None
            entry = entry if isinstance(entry, dict) else {}
            params = normalize_params(entry.get("params")) if isinstance(entry.get("params"), dict) else {}
            mode = entry.get("mode")
            if params and mode not in ("fixed", "dynamic"):
                # 0.3.x slots carry no mode: judge by their values
                p = full_params(params)
                mode = "dynamic" if (p["leveler"] > 0.5 or p["lev_max_boost_db"] > 0.05
                                     or p["comp_ratio"] > 1.01) else "fixed"
            slots[sid] = {"name": entry.get("name") or default_slot_name(sid), "params": params or None,
                          "mode": mode if params else None}
        return slots

    def standard_params(self):
        """Effective parameters of Standard (its active mode)."""
        import cinegain_modes as modes
        return modes.load_standard(self.state_path)["params"]

    def is_template(self, template_id):
        if template_id in USER_SLOTS:
            return self.user_slots()[template_id]["params"] is not None
        return template_id in self.factory()

    def params(self, profile_id):
        """Complete parameter set for "standard" or a template (defaults filled in), or None."""
        if profile_id == PROFILE_STANDARD:
            return self.standard_params()
        if profile_id in USER_SLOTS:
            p = self.user_slots()[profile_id]["params"]
        else:
            p = self.factory().get(profile_id, {}).get("params")
        if not p:
            return None
        full = dict(DEFAULT_PARAMS)
        full.update(p)
        full["bypass"] = 0.0
        return full

    def template_setting(self, template_id):
        """(mode, values) a template loads: user slots remember theirs, factory presets by kind."""
        import cinegain_modes as modes
        params = self.params(template_id)
        if params is None:
            return None, None
        if template_id in USER_SLOTS:
            mode = self.user_slots()[template_id]["mode"]
        else:
            mode = modes.preset_mode(template_id)
        import cinegain_overdrive as overdrive
        return mode, overdrive.clamp_params(modes.mode_values(mode, params))

    def name(self, template_id):
        if template_id == PROFILE_STANDARD:
            return "Standard"
        if template_id in USER_SLOTS:
            return self.user_slots()[template_id]["name"]
        return self.factory().get(template_id, {}).get("name", template_id)

    def templates(self):
        """Ordered list of (template_id, name): factory presets, then filled user slots."""
        items = [(pid, p["name"]) for pid, p in self.factory().items()]
        items += [(sid, s["name"]) for sid, s in self.user_slots().items() if s["params"]]
        return items


def full_params(params):
    """Fills missing keys with defaults so a filter always gets a complete set."""
    full = dict(DEFAULT_PARAMS)
    full.update(normalize_params(params))
    return full


def _spa_string(text):
    """Text safe inside a double-quoted PipeWire (SPA JSON) string: no control characters."""
    text = "".join(ch for ch in str(text) if ch >= " " and ch != "\x7f")
    return text.replace("\\", "\\\\").replace('"', '\\"')


def build_runtime_conf(template, plugin_so, profile_id, params, description):
    """Fills the filter-chain template with names and parameters of one profile."""
    text = re.sub(r'plugin\s*=\s*"[^"]+"', lambda m: f'plugin = "{plugin_so}"', template)
    text = text.replace('"cinegain_sink"', f'"{sink_name(profile_id)}"')
    text = text.replace('"cinegain_output"', f'"{output_name(profile_id)}"')
    # The label comes from the outside (application.name, rename): the replacement
    # must be literal (no re escapes) and a valid SPA string (#31).
    desc = _spa_string(description)
    text = re.sub(r'node\.description\s*=\s*"[^"]*"', lambda m: f'node.description = "{desc}"', text)
    text = re.sub(r'media\.name\s*=\s*"[^"]*"', lambda m: f'media.name       = "{desc}"', text)
    if profile_id != PROFILE_STANDARD:
        # Profile sinks must never win the default-sink election.
        text = re.sub(r"priority\.session\s*=\s*\d+", "priority.session   = 0", text)
        text = re.sub(r"priority\.driver\s*=\s*\d+", "priority.driver    = 0", text)
    for key, value in (params or {}).items():
        val = (1.0 if float(value) > 0.5 else 0.0) if key in BOOL_PARAMS else float(value)
        text = re.sub(
            r'("' + re.escape(key) + r'"\s*=\s*)-?[0-9.]+',
            lambda m, v=val: f"{m.group(1)}{v}",
            text,
        )
    return text


def _read_argv(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def is_cinegain_pipewire(pid):
    """
    True only for a `pipewire -c <conf>` process whose config belongs to Cinegain.
    Matching on argv (not on the whole command line text) keeps shells or editors
    that merely mention these words safe from being killed.
    """
    argv = _read_argv(pid)
    if not argv or os.path.basename(argv[0]) != "pipewire":
        return False
    try:
        conf = argv[argv.index("-c") + 1]
    except (ValueError, IndexError):
        return False
    name = conf.lower()
    return "cinegain" in name or "kinoton" in name


def cinegain_pipewire_pids():
    """All Cinegain filter-chain processes of the current user."""
    uid = os.getuid()
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid:
                continue
        except OSError:
            continue
        if is_cinegain_pipewire(int(entry)):
            pids.append(int(entry))
    return pids


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    # Exclude zombies and recycled PIDs that are not our pipewire.
    return is_cinegain_pipewire(pid)


class SinkProcesses:
    """Starts and stops one PipeWire filter-chain process per profile."""

    def __init__(self, runtime_dir=None, template_path=None, popen=subprocess.Popen):
        self.runtime_dir = runtime_dir or paths.RUNTIME_DIR
        self.template_path = template_path or paths.CONF_TEMPLATE_PATH
        self.popen = popen

    def _pid_file(self, profile_id):
        return os.path.join(self.runtime_dir, f"{_safe_id(profile_id)}.pid")

    def _conf_file(self, profile_id):
        return os.path.join(self.runtime_dir, f"runtime-{_safe_id(profile_id)}.conf")

    def pid(self, profile_id):
        try:
            with open(self._pid_file(profile_id)) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return None
        return pid if _pid_alive(pid) else None

    def running(self):
        """Profile ids with a live process (from PID files)."""
        result = set()
        try:
            names = os.listdir(self.runtime_dir)
        except OSError:
            return result
        for fn in names:
            if fn.endswith(".pid"):
                pid_id = fn[:-4]
                if self.pid(pid_id):
                    result.add(pid_id)
        return result

    def start(self, profile_id, params, description="Cinegain"):
        """Spawns the sink process (non-blocking). Returns the PID or None."""
        existing = self.pid(profile_id)
        if existing:
            return existing
        if not params:
            return None
        with open(self.template_path, "r", encoding="utf-8") as f:
            template = f.read()
        conf = build_runtime_conf(template, paths.plugin_so_path(), profile_id, params, description)
        os.makedirs(self.runtime_dir, mode=0o700, exist_ok=True)
        conf_path = self._conf_file(profile_id)
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(conf)
        err = subprocess.DEVNULL
        try:
            os.makedirs(paths.LOG_DIR, mode=0o700, exist_ok=True)
            err = open(cinegain_log.pipewire_log_path(profile_id), "w", encoding="utf-8")
        except OSError:
            pass
        try:
            proc = self.popen(
                ["/usr/bin/pipewire", "-c", conf_path],
                stdout=subprocess.DEVNULL,
                stderr=err,
                start_new_session=True,
            )
        except OSError as e:
            log.error("start %s failed: %s", profile_id, e)
            return None
        finally:
            if err is not subprocess.DEVNULL:
                err.close()
        with open(self._pid_file(profile_id), "w") as f:
            f.write(str(proc.pid))
        log.info("started sink %s pid=%s (%s) params=%s", profile_id, proc.pid, description,
                 _param_summary(params))
        return proc.pid

    def stop(self, profile_id, reason=""):
        pid = self.pid(profile_id)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                log.warning("stop %s pid=%s failed: %s", profile_id, pid, e)
            log.info("stopped sink %s pid=%s%s", profile_id, pid, f" ({reason})" if reason else "")
        for path in (self._pid_file(profile_id), self._conf_file(profile_id)):
            try:
                os.remove(path)
            except OSError:
                pass
        return pid is not None

    def stop_all_profiles(self, reason="stop all"):
        """Stops every per-program sink (everything except the standard one)."""
        stopped = []
        for pid_id in self.running():
            if pid_id != PROFILE_STANDARD:
                self.stop(pid_id, reason)
                stopped.append(pid_id)
        return stopped


def find_node_id(node_name, *, strict=False):
    """PipeWire object id of a node, via pw-dump."""
    try:
        data = json.loads(subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL))
        if not isinstance(data, list) or not all(isinstance(obj, dict) for obj in data):
            raise ValueError("Invalid pw-dump object list")
    except (subprocess.CalledProcessError, ValueError, OSError):
        if strict:
            raise
        return None
    for obj in data:
        if obj.get("type") == "PipeWire:Interface:Node":
            if obj.get("info", {}).get("props", {}).get("node.name") == node_name:
                return obj["id"]
    return None


def push_params(node_id, params):
    """Applies parameters live to a running filter node."""
    if not node_id or not params:
        return False
    flat = []
    for k, v in params.items():
        key = k.replace("cinegain:", "")
        val = (1.0 if float(v) > 0.5 else 0.0) if key in BOOL_PARAMS else float(v)
        flat.extend([f"cinegain:{key}", val])
    try:
        res = subprocess.run(
            ["pw-cli", "s", str(node_id), "Props", json.dumps({"params": flat})],
            capture_output=True,
            text=True,
        )
    except OSError as e:
        log.error("pw-cli could not run: %s", e)
        return False
    if res.returncode != 0:
        log.warning("push to node %s failed (rc=%s): %s", node_id, res.returncode,
                    (res.stderr or res.stdout or "").strip())
        return False
    log.debug("pushed to node %s: %s", node_id, _param_summary(params))
    return True


def _param_summary(params):
    """Compact one-line form of a parameter set for the log."""
    if not params:
        return "{}"
    return " ".join(f"{k.replace('cinegain:', '')}={float(v):g}" for k, v in sorted(params.items()))


def wait_for_node(node_name, timeout=3.0, step=0.15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        node_id = find_node_id(node_name)
        if node_id:
            return node_id
        time.sleep(step)
    return None


METER_FORMAT = "<IIffff"
METER_MAGIC = 0x434E4D31


def read_meter(pid, runtime_dir=None):
    """
    Reads the plugin's meter file for a filter process.
    Returns (seq, level_db, leveler_db, gr_db, limit_db) or None when unavailable.
    """
    import struct
    if not pid:
        return None
    path = os.path.join(runtime_dir or paths.RUNTIME_DIR, f"meter-{pid}.bin")
    try:
        with open(path, "rb") as f:
            raw = f.read(struct.calcsize(METER_FORMAT))
        magic, seq, level, leveler, gr, limit = struct.unpack(METER_FORMAT, raw)
    except (OSError, struct.error):
        return None
    if magic != METER_MAGIC:
        return None
    return seq, level, leveler, gr, limit


def read_ramp(pid, runtime_dir=None):
    """dB a filter still has to glide up (0.5.0 meter field), or None when unknown."""
    import struct
    if not pid:
        return None
    path = os.path.join(runtime_dir or paths.RUNTIME_DIR, f"meter-{pid}.bin")
    try:
        with open(path, "rb") as f:
            raw = f.read(28)
        magic, _seq, _a, _b, _c, _d, ramp = struct.unpack("<IIfffff", raw)
    except (OSError, struct.error):
        return None  # an older plugin writes only 24 bytes
    return ramp if magic == METER_MAGIC else None
