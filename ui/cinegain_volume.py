"""
Guard for the hidden stream volume.

PipeWire nodes carry a "volume" property next to the per-channel volumes that
pactl, pavucontrol and the desktop mixer show. WirePlumber remembers it per
program (~/.local/state/wireplumber/stream-properties) and restores it for every
new stream. On 2026-11-11 Chrome sat at 0.0039 (-48 dB), Electron apps
("Chromium") at -84 dB and Cinegain's own Chrome output at -24 dB while every
mixer showed 100 %. Who lowers it is still unknown; every change is logged.

- Streams of programs routed through Cinegain and Cinegain's own output streams
  are faded back to 1.0 automatically (3 s, no jump, settings untouched).
- Everything else is only reported (badge, banner) until the user presses
  "Full volume reset".
"""
import json
import math
import os
import re
import shutil
import subprocess
import time

import cinegain_log
import cinegain_paths as paths
from cinegain_registry import make_key

log = cinegain_log.get("volume")

HIDDEN_EPS = 0.999      # below this a node counts as secretly lowered
FADE_S = 3.0            # automatic reset fades in over this time
OUTPUT_PREFIX = "cinegain_output"


def to_db(v):
    return 20.0 * math.log10(max(float(v), 1e-9))


class NodeVolume:
    __slots__ = ("id", "key", "app_name", "binary", "node_name", "volume")

    def __init__(self, node_id, app_name, binary, node_name, volume):
        self.id = node_id
        self.app_name = app_name or ""
        self.binary = binary or ""
        self.node_name = node_name or ""
        self.volume = float(volume)
        self.key = make_key(self.app_name or self.node_name, self.binary)

    @property
    def is_cinegain_output(self):
        return self.node_name.startswith(OUTPUT_PREFIX)

    @property
    def db(self):
        return to_db(self.volume)

    def __repr__(self):
        return f"NodeVolume({self.id}, {self.key!r}, {self.node_name!r}, {self.volume:.6f})"


def parse_nodes(dump):
    """Playback stream nodes with their hidden volume from `pw-dump` JSON."""
    out = []
    for obj in dump if isinstance(dump, list) else []:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        for p in (info.get("params") or {}).get("Props") or []:
            if isinstance(p, dict) and isinstance(p.get("volume"), (int, float)):
                out.append(NodeVolume(obj.get("id"), props.get("application.name"),
                                      props.get("application.process.binary"),
                                      props.get("node.name"), p["volume"]))
                break
    return out


def scan_nodes():
    try:
        return parse_nodes(json.loads(subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL)))
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        log.warning("pw-dump for the volume guard failed: %s", e)
        return []


def set_node_volume(node_id, value):
    try:
        res = subprocess.run(["pw-cli", "s", str(node_id), "Props", json.dumps({"volume": float(value)})],
                             capture_output=True, text=True)
    except OSError as e:
        log.error("pw-cli could not run: %s", e)
        return False
    if res.returncode != 0:
        log.warning("setting hidden volume of node %s failed: %s", node_id, (res.stderr or "").strip())
        return False
    return True


class VolumeGuard:
    def __init__(self, scanner=scan_nodes, setter=set_node_volume, clock=time.time, auto=True):
        self.scanner = scanner
        self.setter = setter
        self.clock = clock
        # Automatic fade-back for routed programs and Cinegain outputs. Off: everything
        # is only reported, so lowerings can be watched in the analysis view (Q9 0.5.0).
        self.auto = auto
        self.fades = {}      # node id -> (start dB, start time)
        self._last = {}      # node id -> last seen volume (for change logging)
        self.nodes = []

    def update(self, auto_keys=(), now=None):
        """
        Scans the nodes, starts fades for auto-managed ones and returns
        {program key: hidden dB} for the ones that are only reported.
        """
        now = self.clock() if now is None else now
        self.nodes = self.scanner()
        hidden = {}
        seen = set()
        for n in self.nodes:
            seen.add(n.id)
            last = self._last.get(n.id)
            if last is None or abs(last - n.volume) > 1e-4:
                if n.volume < HIDDEN_EPS and n.id not in self.fades:
                    log.warning("hidden volume of node %s (%s, %s) is %.6f = %.1f dB%s", n.id, n.key,
                                n.node_name, n.volume, n.db,
                                "" if last is None else f" (was {to_db(last):.1f} dB)")
            self._last[n.id] = n.volume
            if n.volume >= HIDDEN_EPS or n.id in self.fades:
                continue
            if self.auto and (n.is_cinegain_output or n.key in auto_keys):
                self.fades[n.id] = (n.db, now)
                log.info("fading node %s (%s) from %.1f dB back to 0 dB over %.0f s", n.id,
                         n.key if not n.is_cinegain_output else n.node_name, n.db, FADE_S)
            else:
                hidden[n.key] = min(hidden.get(n.key, 0.0), n.db)
        for gone in [i for i in self._last if i not in seen]:
            self._last.pop(gone, None)
            self.fades.pop(gone, None)
        return hidden

    def step(self, now=None):
        """Advances running fades (call often, e.g. every 100 ms). Linear in dB, so it sounds even."""
        now = self.clock() if now is None else now
        for node_id, (start_db, t0) in list(self.fades.items()):
            x = (now - t0) / FADE_S
            if x >= 1.0:
                self.setter(node_id, 1.0)
                self.fades.pop(node_id, None)
                self._last[node_id] = 1.0
                log.info("node %s back at 0 dB", node_id)
            else:
                v = 10 ** (start_db * (1.0 - max(0.0, x)) / 20.0)
                self.setter(node_id, v)
                self._last[node_id] = v

    def reset_now(self, node_id):
        self.fades.pop(node_id, None)
        if self.setter(node_id, 1.0):
            self._last[node_id] = 1.0
            return True
        return False


def clean_stream_properties(path=None, backup_dir=None, stamp=None):
    """
    Resets saved playback volumes in WirePlumber's stream-properties (volume and
    channel volumes to 1.0, mute off). Device routes and sinks stay untouched.
    Returns (number of changed entries, backup path or None).
    Note: WirePlumber keeps its own copy in memory; entries of streams that are not
    playing can come back until those programs play again (then the guard fixes them).
    """
    path = path or paths.WIREPLUMBER_STREAM_PROPS
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines(keepends=True)
    except OSError:
        return 0, None
    changed = 0
    out = []
    for line in lines:
        if line.startswith("Output/Audio:"):
            new = re.sub(r'"volume":\s*[0-9.eE+-]+', '"volume":1.000000', line)
            new = re.sub(r'"channelVolumes":\s*\[[^\]]*\]',
                         lambda m: '"channelVolumes":[' + ", ".join(
                             "1.000000" for _ in m.group(0).split(":", 1)[1].strip("[] ").split(",")) + "]",
                         new)
            new = re.sub(r'"mute":\s*true', '"mute":false', new)
            if new != line:
                changed += 1
            line = new
        out.append(line)
    if not changed:
        return 0, None
    backup = None
    if backup_dir is not False:
        backup_dir = paths.private_dir(backup_dir or paths.BACKUP_DIR)
        backup = os.path.join(backup_dir, f"stream-properties-{stamp or time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    paths.write_text_atomic(path, "".join(out))
    log.info("cleaned %d saved stream volumes in %s (backup %s)", changed, path, backup)
    return changed, backup


# -- analysis view (0.5.0) ---------------------------------------------------------
KIND_STREAM = "stream"              # playback stream of a program
KIND_OUTPUT = "cinegain_output"     # Cinegain's own stream into the hardware
KIND_SINK = "cinegain_sink"         # Cinegain filter input
KIND_HARDWARE = "hardware"          # sound card output, shown read-only
EDITABLE_KINDS = (KIND_STREAM, KIND_OUTPUT, KIND_SINK)


def _unescape(name):
    return name.replace("\\s", " ")


def read_stored(path=None):
    """Saved playback volumes of WirePlumber: {application.name: {"volume", "mute"}}."""
    path = path or paths.WIREPLUMBER_STREAM_PROPS
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"Output/Audio:application\.name:(.*?)=(\{.*\})\s*$", line)
        if not m:
            continue
        try:
            data = json.loads(m.group(2))
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("volume"), (int, float)):
            out[_unescape(m.group(1))] = {"volume": float(data["volume"]), "mute": bool(data.get("mute"))}
    return out


def _props_entry(info):
    for p in (info.get("params") or {}).get("Props") or []:
        if isinstance(p, dict) and isinstance(p.get("volume"), (int, float)):
            return p
    return {}


def inspect_nodes(dump, stored=None):
    """
    Every playback node the analysis view lists, as dicts:
    id, kind, label, key, node_name, hidden (linear), normal (linear, loudest channel),
    mute, editable, stored (WirePlumber's saved volume or None).
    """
    stored = stored or {}
    rows = []
    for obj in dump if isinstance(dump, list) else []:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        cls = props.get("media.class")
        name = props.get("node.name") or ""
        if cls == "Stream/Output/Audio":
            kind = KIND_OUTPUT if name.startswith(OUTPUT_PREFIX) else KIND_STREAM
        elif cls == "Audio/Sink":
            kind = KIND_SINK if name.startswith("cinegain_") else KIND_HARDWARE
        else:
            continue
        p = _props_entry(info)
        if not p:
            continue
        app = props.get("application.name") or ""
        label = app or props.get("node.description") or props.get("media.name") or name
        chans = [float(v) for v in p.get("channelVolumes") or [] if isinstance(v, (int, float))]
        saved = stored.get(app) if kind == KIND_STREAM and app else None
        rows.append({
            "id": obj.get("id"), "kind": kind, "label": label, "node_name": name,
            "key": make_key(app or name, props.get("application.process.binary")),
            "hidden": float(p.get("volume", 1.0)), "normal": max(chans) if chans else 1.0,
            "mute": bool(p.get("mute")), "editable": kind in EDITABLE_KINDS,
            "stored": saved["volume"] if saved else None,
        })
    order = {KIND_STREAM: 0, KIND_OUTPUT: 1, KIND_SINK: 2, KIND_HARDWARE: 3}
    rows.sort(key=lambda r: (order[r["kind"]], r["label"].lower(), r["id"] or 0))
    return rows


def scan_analysis():
    try:
        dump = json.loads(subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL))
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        log.warning("pw-dump for the analysis view failed: %s", e)
        return []
    return inspect_nodes(dump, read_stored())


def set_hidden(node_id, fraction, label="", who="user"):
    """Sets a node's hidden volume to 0..100 % (never above: boosting is Cinegain's job, Q19)."""
    value = max(0.0, min(1.0, float(fraction)))
    ok = set_node_volume(node_id, value)
    if ok:
        log.info("hidden volume of node %s (%s) set to %.6f = %.1f dB by %s",
                 node_id, label, value, to_db(value), who)
    return ok


_HIST_PATTERNS = (
    ("lowered", re.compile(r"hidden volume of node (\d+) \(([^,]*), ([^)]*)\) is [0-9.]+ = (-?[0-9.]+|-inf) dB(?: \(was (-?[0-9.]+) dB\))?")),
    ("auto", re.compile(r"fading node (\d+) \(([^)]*)\) from (-?[0-9.]+) dB")),
    ("set", re.compile(r"hidden volume of node (\d+) \(([^)]*)\) set to [0-9.]+ = (-?[0-9.]+|-inf) dB by (\w+)")),
    ("full_reset", re.compile(r"full volume reset done")),
)
_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[.,]\d+ \d+ \S+ \S+\s+(\S+): (.*)$")


def history(log_path=None, days=7, limit=500, key=None, now=None):
    """
    Hidden-volume events from the debug log (and its rotated files), newest first:
    dicts with time (epoch), kind (lowered/auto/set/full_reset), key, db, was_db, who.
    """
    base = log_path or paths.LOG_FILE
    files = [f"{base}.{i}" for i in range(4, 0, -1)] + [base]
    cutoff = (now or time.time()) - days * 86400
    events = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            m = _LOG_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            try:
                ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if ts < cutoff or m.group(2) not in ("cinegain.volume", "cinegain.router"):
                continue
            msg = m.group(3)
            for kind, rx in _HIST_PATTERNS:
                hit = rx.match(msg)  # anchored: names inside a message never count (#42)
                if not hit:
                    continue
                ev = {"time": ts, "kind": kind, "key": "", "db": None, "was_db": None, "who": ""}
                if kind == "lowered":
                    ev.update(key=hit.group(2), db=_f(hit.group(4)), was_db=_f(hit.group(5)))
                elif kind == "auto":
                    ev.update(key=hit.group(2), db=0.0, was_db=_f(hit.group(3)), who="auto")
                elif kind == "set":
                    ev.update(key=hit.group(2), db=_f(hit.group(3)), who=hit.group(4))
                else:
                    ev.update(who="user")
                if key is None or ev["key"] == key or kind == "full_reset":
                    events.append(ev)
                break
    events.sort(key=lambda e: e["time"], reverse=True)
    return events[:limit]


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None
