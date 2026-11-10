"""
Profiles and their PipeWire sinks.

A profile is a full parameter set. "standard" is the live profile controlled by
the main sliders (sink `cinegain_sink`, parameters in state.json). Every factory
preset and every filled user slot can also be used as a profile; an application
assigned to such a profile is routed to its own sink `cinegain_<profile>`, which
runs as a separate small PipeWire filter-chain process. That way two programs
can play at the same time with different processing.

Profile sinks are started on demand and stopped again when nothing has used them
for a while (see Router).
"""
import json
import os
import re
import signal
import subprocess
import time

import cinegain_paths as paths
from cinegain_registry import PROFILE_OFF, PROFILE_STANDARD

STANDARD_SINK = "cinegain_sink"
STANDARD_OUTPUT = "cinegain_output"
USER_SLOTS = tuple(f"user_{i}" for i in range(1, 9))

DEFAULT_PARAMS = {
    "bypass": 0.0,
    "input_db": 8.0,
    "highpass_hz": 40.0,
    "bass_db": -3.0,
    "dialog_db": 6.5,
    "dialog_hz": 2500.0,
    "dialog_q": 1.0,
    "treble_db": 1.5,
    "mid_db": 2.0,
    "side_db": -3.0,
    "leveler": 1.0,
    "lev_target_db": -16.0,
    "lev_max_boost_db": 15.0,
    "lev_max_cut_db": 6.0,
    "lev_speed_s": 1.5,
    "lev_gate_db": -55.0,
    "comp_thresh_db": -12.0,
    "comp_ratio": 3.5,
    "comp_knee_db": 6.0,
    "comp_attack_ms": 10.0,
    "comp_release_ms": 180.0,
    "makeup_db": 2.0,
    "lim_ceiling_db": -0.5,
    "lim_release_ms": 70.0,
}
BOOL_PARAMS = {"bypass", "leveler"}


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


def normalize_params(raw):
    out = {}
    for k, v in (raw or {}).items():
        try:
            out[str(k).replace("cinegain:", "")] = float(v)
        except (TypeError, ValueError):
            continue
    return out


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
        for i, sid in enumerate(USER_SLOTS, start=1):
            entry = raw.get(sid) if isinstance(raw, dict) else None
            entry = entry if isinstance(entry, dict) else {}
            params = normalize_params(entry.get("params")) if isinstance(entry.get("params"), dict) else {}
            slots[sid] = {"name": entry.get("name") or f"User {i}", "params": params or None}
        return slots

    def standard_params(self):
        state = paths.read_json(self.state_path, default={}) or {}
        params = dict(DEFAULT_PARAMS)
        params.update(normalize_params(state.get("params")))
        return params

    def exists(self, profile_id):
        if profile_id in (PROFILE_OFF, PROFILE_STANDARD):
            return True
        if profile_id in USER_SLOTS:
            return self.user_slots()[profile_id]["params"] is not None
        return profile_id in self.factory()

    def params(self, profile_id):
        """Complete parameter set for a profile (defaults filled in), or None."""
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

    def name(self, profile_id):
        if profile_id == PROFILE_STANDARD:
            return "Standard"
        if profile_id in USER_SLOTS:
            return self.user_slots()[profile_id]["name"]
        return self.factory().get(profile_id, {}).get("name", profile_id)

    def choices(self):
        """Ordered list of (profile_id, name) for selectors; only usable profiles."""
        items = [(PROFILE_OFF, "Off"), (PROFILE_STANDARD, "Standard")]
        items += [(pid, p["name"]) for pid, p in self.factory().items()]
        items += [(sid, s["name"]) for sid, s in self.user_slots().items() if s["params"]]
        return items


def build_runtime_conf(template, plugin_so, profile_id, params, description):
    """Fills the filter-chain template with names and parameters of one profile."""
    text = re.sub(r'plugin\s*=\s*"[^"]+"', f'plugin = "{plugin_so}"', template)
    text = text.replace('"cinegain_sink"', f'"{sink_name(profile_id)}"')
    text = text.replace('"cinegain_output"', f'"{output_name(profile_id)}"')
    desc = description.replace('"', "'")
    text = re.sub(r'node\.description\s*=\s*"[^"]*"', f'node.description = "{desc}"', text)
    text = re.sub(r'media\.name\s*=\s*"[^"]*"', f'media.name       = "{desc}"', text)
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

    def __init__(self, catalog=None, runtime_dir=None, template_path=None, popen=subprocess.Popen):
        self.catalog = catalog or ProfileCatalog()
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

    def start(self, profile_id, params=None, description=None):
        """Spawns the sink process (non-blocking). Returns the PID or None."""
        existing = self.pid(profile_id)
        if existing:
            return existing
        if params is None:
            params = self.catalog.params(profile_id)
        if params is None:
            return None
        with open(self.template_path, "r", encoding="utf-8") as f:
            template = f.read()
        if description is None:
            name = self.catalog.name(profile_id)
            description = "Cinegain" if profile_id == PROFILE_STANDARD else f"Cinegain · {name}"
        conf = build_runtime_conf(template, paths.plugin_so_path(), profile_id, params, description)
        os.makedirs(self.runtime_dir, mode=0o700, exist_ok=True)
        conf_path = self._conf_file(profile_id)
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(conf)
        proc = self.popen(
            ["/usr/bin/pipewire", "-c", conf_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        with open(self._pid_file(profile_id), "w") as f:
            f.write(str(proc.pid))
        return proc.pid

    def stop(self, profile_id):
        pid = self.pid(profile_id)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for path in (self._pid_file(profile_id), self._conf_file(profile_id)):
            try:
                os.remove(path)
            except OSError:
                pass
        return pid is not None

    def stop_all_profiles(self):
        """Stops every profile sink except the standard one."""
        stopped = []
        for pid_id in self.running():
            if pid_id != PROFILE_STANDARD:
                self.stop(pid_id)
                stopped.append(pid_id)
        return stopped


def find_node_id(node_name):
    """PipeWire object id of a node, via pw-dump."""
    try:
        data = json.loads(subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL))
    except (subprocess.CalledProcessError, ValueError, OSError):
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
    res = subprocess.run(
        ["pw-cli", "s", str(node_id), "Props", json.dumps({"params": flat})],
        capture_output=True,
        text=True,
    )
    return res.returncode == 0


def wait_for_node(node_name, timeout=3.0, step=0.15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        node_id = find_node_id(node_name)
        if node_id:
            return node_id
        time.sleep(step)
    return None
