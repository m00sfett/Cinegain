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
FADE_S = 3.0            # a fade-in takes at least this long ...
MAX_RISE_DB_S = 10.0    # ... and never rises faster than this (SPEC 3.3 policy, #40)
MAX_STEP_DB = 1.0       # largest raise per step, fine enough against zipper noise (#40)
FLOOR_DB = -90.0        # a fade from silence (or an unknown level) starts here
OUTPUT_PREFIX = "cinegain_output"


def to_db(v):
    return 20.0 * math.log10(max(float(v), 1e-9))


def from_db(db):
    return 10.0 ** (db / 20.0)


def rise_rate(delta_db):
    """dB/s of a fade over delta_db: spread over at least FADE_S, at most MAX_RISE_DB_S."""
    return min(MAX_RISE_DB_S, max(float(delta_db), 1e-6) / FADE_S)


class Fade:
    """
    One rising fade, linear in dB. Invariants (#40): the level never rises faster
    than rise_rate() (<= MAX_RISE_DB_S) measured from the start, no single step
    rises more than MAX_STEP_DB (a late or stalled timer only makes it slower), and
    the last step lands exactly on the target. apply(db, final) does the write.
    """

    __slots__ = ("start_db", "target_db", "t0", "rate", "applied_db", "apply", "what")

    def __init__(self, start_db, target_db, t0, apply, what=""):
        self.start_db = max(FLOOR_DB, float(start_db))
        self.target_db = float(target_db)
        self.t0 = t0
        self.rate = rise_rate(self.target_db - self.start_db)
        self.applied_db = self.start_db
        self.apply = apply
        self.what = what

    @property
    def duration(self):
        return max(0.0, self.target_db - self.start_db) / self.rate

    def next_db(self, now):
        timed = self.start_db + self.rate * max(0.0, now - self.t0)
        return min(self.target_db, timed, self.applied_db + MAX_STEP_DB)


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


# The guard a running GUI or `cinegain watch` keeps updating and stepping. Raises
# from the analysis view and the CLI go through it, so no second fade fights it.
LIVE_S = 5.0
_live = None


def live_guard():
    """The guard of the router owner in this process, if it is still being ticked."""
    g = _live
    if g is not None and g._alive is not None and time.monotonic() - g._alive <= LIVE_S:
        return g
    return None


class VolumeGuard:
    def __init__(self, scanner=scan_nodes, setter=set_node_volume, clock=time.time, auto=True):
        self._alive = None
        self.scanner = scanner
        self.setter = setter
        self.clock = clock
        # Automatic fade-back for routed programs and Cinegain outputs. Off: everything
        # is only reported, so lowerings can be watched in the analysis view (Q9 0.5.0).
        self.auto = auto
        self.fades = {}      # node id (hidden volume) or ("mixer", stream) -> Fade
        self._last = {}      # node id -> last seen or written hidden volume
        self.nodes = []

    def known_volume(self, node_id, current=None):
        """
        Lowest plausible current hidden volume of a node: a running fade's last
        write, the last value seen or written, and the caller's reading. The lowest
        one is safe: starting a fade too low only means a short dip, never a jump.
        """
        values = [v for v in (current, self._last.get(node_id)) if v is not None]
        fade = self.fades.get(node_id)
        if fade is not None:
            values.append(from_db(fade.applied_db))
        return min(values) if values else None

    def fade_hidden(self, node_id, start, target=1.0, now=None, apply_start=False, what=""):
        """
        Brings a node's hidden volume from `start` to `target` (both linear). Lower
        targets are set at once; higher ones fade in (see Fade). apply_start writes
        the start value first (a duck, or a raise that was compensated beforehand).
        Returns False if a write failed.
        """
        now = self.clock() if now is None else now
        target = max(0.0, min(1.0, float(target)))
        start = max(0.0, float(start))
        self.fades.pop(node_id, None)
        if target <= start * 1.0001:
            ok = self.setter(node_id, target)
            if ok:
                self._last[node_id] = target
            return ok
        if apply_start:
            if not self.setter(node_id, start):
                return False
            self._last[node_id] = start
        target_db = 0.0 if target >= 1.0 else to_db(target)

        def apply(db, final, node_id=node_id, target=target):
            value = target if final else from_db(db)
            ok = self.setter(node_id, value)
            if ok:
                self._last[node_id] = value
            return ok

        self.fades[node_id] = Fade(to_db(start), target_db, now, apply, what or f"node {node_id}")
        return True

    def add_fade(self, key, start_db, target_db, apply, now=None, what=""):
        """Generic rising fade (e.g. a stream's mixer volume); apply(db, final) writes."""
        now = self.clock() if now is None else now
        self.fades[key] = Fade(start_db, target_db, now, apply, what or str(key))
        return self.fades[key]

    def cancel(self, key):
        self.fades.pop(key, None)

    def update(self, auto_keys=(), now=None):
        """
        Scans the nodes, starts fades for auto-managed ones and returns
        {program key: hidden dB} for the ones that are only reported.
        """
        global _live
        now = self.clock() if now is None else now
        _live, self._alive = self, time.monotonic()
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
            if n.id not in self.fades:
                self._last[n.id] = n.volume
            if n.volume >= HIDDEN_EPS or n.id in self.fades:
                continue
            if self.auto and (n.is_cinegain_output or n.key in auto_keys):
                self.fade_hidden(n.id, n.volume, 1.0, now)
                log.info("fading node %s (%s) from %.1f dB back to 0 dB over %.1f s", n.id,
                         n.key if not n.is_cinegain_output else n.node_name, n.db, self.fades[n.id].duration)
            else:
                hidden[n.key] = min(hidden.get(n.key, 0.0), n.db)
        for gone in [i for i in self._last if i not in seen]:
            self._last.pop(gone, None)
            self.fades.pop(gone, None)
        return hidden

    def step(self, now=None):
        """Advances running fades (call often: every 100 ms gives 1 dB steps at 10 dB/s)."""
        now = self.clock() if now is None else now
        for key, fade in list(self.fades.items()):
            db = fade.next_db(now)
            final = db >= fade.target_db - 1e-9
            ok = fade.apply(db, final)
            fade.applied_db = db
            if final or not ok:
                if self.fades.get(key) is fade:
                    self.fades.pop(key, None)
                if final and ok:
                    log.info("%s at %.1f dB (fade done)", fade.what, fade.target_db)

    def run_fades(self, sleep=time.sleep, interval=0.05):
        """Blocking: steps until every fade is done (one-shot CLI use without a GUI timer)."""
        while self.fades:
            self.step()
            if self.fades:
                sleep(interval)

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


def set_hidden(node_id, fraction, label="", who="user", current=None, guard=None,
               clock=time.time, sleep=time.sleep):
    """
    Sets a node's hidden volume to 0..100 % (never above: boosting is Cinegain's job,
    Q19). Lowering is instant; raising fades in at <= 10 dB/s (#27). The fade runs
    on `guard` or on the guard a GUI / `cinegain watch` keeps stepping; without one
    (CLI) it runs here and blocks until done. `current` is the caller's reading of
    the node; unknown levels fade in from silence.
    """
    value = max(0.0, min(1.0, float(fraction)))
    g = guard or live_guard()
    local = g is None
    if local:
        g = VolumeGuard(scanner=lambda: [], setter=set_node_volume, clock=clock)  # looked up now: patchable
    start = g.known_volume(node_id, current)
    if start is None:
        start = from_db(FLOOR_DB)
    raising = value > start * 1.0001
    ok = g.fade_hidden(node_id, start, value, what=f"node {node_id} ({label})")
    if ok:
        log.info("hidden volume of node %s (%s) set to %.6f = %.1f dB by %s%s", node_id, label, value,
                 to_db(value), who, f", fading in from {to_db(start):.1f} dB" if raising else "")
    if local:
        g.run_fades(sleep=sleep)
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
