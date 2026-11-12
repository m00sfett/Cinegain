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
DUCK_VOLUME = 0.03      # a stream drops to this (-30 dB) right before it changes sinks (#40)
OUTPUT_PREFIX = "cinegain_output"


def to_db(v):
    return 20.0 * math.log10(max(float(v), 1e-9))


def from_db(db):
    return 10.0 ** (db / 20.0)


def mixer_db(pct):
    """dB of a pulse mixer volume in % (cubic scale, as pavucontrol shows it)."""
    return 60.0 * math.log10(pct / 100.0) if pct > 0 else FLOOR_DB


def mixer_pct(db):
    return 100.0 * 10.0 ** (db / 60.0)


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
    __slots__ = ("id", "key", "app_name", "binary", "node_name", "volume", "is_sink", "mixer", "mute")

    def __init__(self, node_id, app_name, binary, node_name, volume, is_sink=False, mixer=1.0, mute=False):
        self.id = node_id
        self.app_name = app_name or ""
        self.binary = binary or ""
        self.node_name = node_name or ""
        self.volume = float(volume)
        self.is_sink = bool(is_sink)   # Audio/Sink (hardware or Cinegain filter input)
        self.mixer = float(mixer)      # loudest channel volume (linear), what mixers show
        self.mute = bool(mute)
        self.key = make_key(self.app_name or self.node_name, self.binary)

    @property
    def is_cinegain_sink(self):
        return self.is_sink and self.node_name.startswith("cinegain")

    @property
    def is_cinegain_output(self):
        return self.node_name.startswith(OUTPUT_PREFIX)

    @property
    def db(self):
        return to_db(self.volume)

    def __repr__(self):
        return f"NodeVolume({self.id}, {self.key!r}, {self.node_name!r}, {self.volume:.6f})"


def parse_nodes(dump, sinks=False):
    """Playback stream nodes (and with sinks=True also Audio/Sink nodes) from `pw-dump` JSON."""
    classes = ("Stream/Output/Audio", "Audio/Sink") if sinks else ("Stream/Output/Audio",)
    out = []
    for obj in dump if isinstance(dump, list) else []:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") not in classes:
            continue
        for p in (info.get("params") or {}).get("Props") or []:
            if isinstance(p, dict) and isinstance(p.get("volume"), (int, float)):
                chans = [float(v) for v in p.get("channelVolumes") or [] if isinstance(v, (int, float))]
                out.append(NodeVolume(obj.get("id"), props.get("application.name"),
                                      props.get("application.process.binary"),
                                      props.get("node.name"), p["volume"],
                                      is_sink=props.get("media.class") == "Audio/Sink",
                                      mixer=max(chans) if chans else 1.0, mute=p.get("mute")))
                break
    return out


def scan_nodes():
    """Streams and sinks, one `pw-dump` per tick (the guard splits them)."""
    try:
        return parse_nodes(json.loads(subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL)), sinks=True)
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        log.warning("pw-dump for the volume guard failed: %s", e)
        return []


# -- who changed it? (#44) -----------------------------------------------------------
# Every hidden-volume write of this process is remembered for a few seconds, so the
# guard can tell its own (and the analysis view's) changes from external ones.
OWN_WRITE_S = 3.0
LOG_DEBOUNCE_S = 2.0    # at most one log line per node and level within this time
_own_writes = {}        # node id -> [(monotonic time, linear value), ...]


def note_own_write(node_id, value):
    now = time.monotonic()
    writes = [w for w in _own_writes.get(node_id, ()) if now - w[0] <= OWN_WRITE_S][-50:]
    writes.append((now, float(value)))
    _own_writes[node_id] = writes


def is_own_write(node_id, value):
    """True if this process wrote (about) this value to the node within OWN_WRITE_S."""
    now = time.monotonic()
    db = to_db(value)
    return any(now - t <= OWN_WRITE_S and abs(to_db(v) - db) < 0.05 for t, v in _own_writes.get(node_id, ()))


class ChangeLog:
    """
    Debounced change logging per subject (node and level): the first change after a
    quiet phase is logged at once, further ones within LOG_DEBOUNCE_S are merged into
    one line with the last logged and the latest value (#44). Both directions count.
    """

    def __init__(self, debounce=LOG_DEBOUNCE_S):
        self.debounce = debounce
        self.state = {}  # subject -> [logged value, time of the last line, pending emit or None]

    def known(self, subject):
        return subject in self.state

    def sync(self, subject, value):
        """Takes over a value without a line (first sight, or a change Cinegain logged itself)."""
        st = self.state.setdefault(subject, [value, float("-inf"), None])
        st[0], st[2] = value, None

    def observe(self, subject, value, now, emit):
        st = self.state.get(subject)
        if st is None:
            self.sync(subject, value)
            return
        if value == st[0]:
            st[2] = None
        elif now - st[1] >= self.debounce:
            emit(st[0], value)
            st[0], st[1], st[2] = value, now, None
        else:
            st[2] = (value, emit)

    def flush(self, now):
        for st in self.state.values():
            if st[2] is not None and now - st[1] >= self.debounce:
                value, emit = st[2]
                emit(st[0], value)
                st[0], st[1], st[2] = value, now, None

    def forget(self, keep):
        for subject in [s for s in self.state if s[0] not in keep]:
            self.state.pop(subject, None)


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
    note_own_write(node_id, value)
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
        self.nodes = []      # playback streams of the last scan
        self.sinks = []      # sinks of the last scan (hardware and Cinegain)
        self.changes = ChangeLog()

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
                self._wrote(node_id, target)
            return ok
        if apply_start:
            if not self.setter(node_id, start):
                return False
            self._wrote(node_id, start)
        target_db = 0.0 if target >= 1.0 else to_db(target)

        def apply(db, final, node_id=node_id, target=target):
            value = target if final else from_db(db)
            ok = self.setter(node_id, value)
            if ok:
                self._wrote(node_id, value)
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

    def refresh(self, now=None):
        """One scan that only learns the current levels (no logging, no fades)."""
        self._refreshed_at = self.clock() if now is None else now
        for n in self.scanner():
            if n.id not in self.fades:
                self._last[n.id] = n.volume

    def duck(self, node_id, now=None):
        """
        #40: right before a stream moves to another sink (whose boost may already be
        up) its hidden volume drops to DUCK_VOLUME and fades back to where it was, or
        to where a running fade was heading. Quieter streams are left alone.
        """
        now = self.clock() if now is None else now
        level = self.known_volume(node_id)
        if level is None and getattr(self, "_refreshed_at", None) != now:
            self.refresh(now)
            level = self.known_volume(node_id)
        if level is None or level <= DUCK_VOLUME:
            return False
        running = self.fades.get(node_id)
        target = from_db(running.target_db) if running is not None else level
        ok = self.fade_hidden(node_id, DUCK_VOLUME, target, now, apply_start=True,
                              what=f"node {node_id} (after a sink change)")
        if ok:
            log.info("ducked node %s to %.1f dB before a sink change, fading back to %.1f dB",
                     node_id, to_db(DUCK_VOLUME), to_db(target))
        return ok

    def update(self, auto_keys=(), now=None):
        """
        Scans the nodes, starts fades for auto-managed ones and returns
        {program key: hidden dB} for the ones that are only reported.
        """
        global _live
        now = self.clock() if now is None else now
        _live, self._alive = self, time.monotonic()
        scanned = self.scanner()
        self.nodes = [n for n in scanned if not n.is_sink]
        self.sinks = [n for n in scanned if n.is_sink]
        hidden = {}
        seen = set()
        try:
            self._log_changes(scanned, now)
        except Exception:
            log.exception("volume change log failed")
        for n in self.nodes:
            seen.add(n.id)
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
                    if isinstance(key, tuple) and key[0] == "mixer" and isinstance(key[1], int):
                        self.note_mixer(key[1], from_db(fade.target_db))

    def run_fades(self, sleep=time.sleep, interval=0.05):
        """Blocking: steps until every fade is done (one-shot CLI use without a GUI timer)."""
        while self.fades:
            self.step()
            if self.fades:
                sleep(interval)

    def reset_now(self, node_id):
        self.fades.pop(node_id, None)
        if self.setter(node_id, 1.0):
            self._wrote(node_id, 1.0)
            return True
        return False

    def _wrote(self, node_id, value):
        self._last[node_id] = value
        note_own_write(node_id, value)
        self.changes.sync((node_id, "hidden"), round(to_db(value), 1))

    def note_mixer(self, node_id, linear):
        """A mixer volume Cinegain set itself (logged where it was done)."""
        self.changes.sync((node_id, "mixer"), round(to_db(linear), 1))

    # -- change log (#44) ------------------------------------------------------
    def _log_changes(self, scanned, now):
        """
        Logs every change of the hidden volume, the mixer volume and mute of streams
        and sinks, both directions, at most once per LOG_DEBOUNCE_S and node/level.
        Changes Cinegain made are logged where they are made; what is left here is
        external (WirePlumber, a mixer, the program itself).
        """
        for n in scanned:
            what = f"{n.id} ({n.key}, {n.node_name})"
            hidden = (n.id, "hidden")
            if n.id in self.fades:
                pass  # Cinegain is fading it; the fade logs start and end
            elif not self.changes.known(hidden):
                self.changes.sync(hidden, round(n.db, 1))
                if n.volume < HIDDEN_EPS:
                    log.warning("hidden volume of node %s is %.6f = %.1f dB", what, n.volume, n.db)
            elif is_own_write(n.id, n.volume):
                self.changes.sync(hidden, round(n.db, 1))
            else:
                self.changes.observe(hidden, round(n.db, 1), now, lambda was, db, what=what, v=n.volume: (
                    log.warning if db < was else log.info)(
                    "hidden volume of node %s is %.6f = %.1f dB (was %.1f dB) [external]", what, v, db, was))
            mixer = (n.id, "mixer")
            mdb = round(to_db(n.mixer), 1)
            if ("mixer", n.id) in self.fades:
                pass
            elif not self.changes.known(mixer):
                self.changes.sync(mixer, mdb)
                if n.is_sink and n.mixer < HIDDEN_EPS:
                    log.info("mixer volume of node %s is %.1f dB = %.0f %%", what, mdb, mixer_pct(mdb))
            else:
                self.changes.observe(mixer, mdb, now, lambda was, db, what=what: log.info(
                    "mixer volume of node %s is %.1f dB = %.0f %% (was %.1f dB) [external]",
                    what, db, mixer_pct(db), was))
            mute = (n.id, "mute")
            if not self.changes.known(mute):
                self.changes.sync(mute, n.mute)
                if n.is_sink and n.mute:
                    log.info("mute of node %s is on", what)
            else:
                self.changes.observe(mute, n.mute, now, lambda was, on, what=what: log.info(
                    "mute of node %s is %s (was %s) [external]", what, "on" if on else "off", "on" if was else "off"))
        self.changes.flush(now)
        self.changes.forget({n.id for n in scanned})


def _is_cinegain_sink_entry(line):
    key = line.split("=", 1)[0].lower()
    return key.startswith("audio/sink:") and ("cinegain" in key or "kinoton" in key)


def clean_stream_properties(path=None, backup_dir=None, stamp=None, existing_sinks=None):
    """
    Resets saved playback volumes in WirePlumber's stream-properties (volume and
    channel volumes to 1.0, mute off): every Output/Audio entry and the Audio/Sink
    entries of Cinegain's own sinks, whose saved lowering came back with every
    restart of a program sink (#26). With existing_sinks (sink names), saved
    targets on Cinegain sinks that no longer exist are dropped too. Hardware
    sinks and inputs stay untouched.
    Returns (number of changed entries, backup path or None).
    Note: the running WirePlumber keeps its own copy in memory and writes it back;
    reset_saved_volumes() makes it load the cleaned file.
    """
    from cinegain_pulse import strip_cinegain_targets

    path = path or paths.WIREPLUMBER_STREAM_PROPS
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines(keepends=True)
    except OSError:
        return 0, None
    changed = 0
    out = []
    for line in lines:
        new = line
        if line.startswith("Output/Audio:") or _is_cinegain_sink_entry(line):
            new = re.sub(r'"volume":\s*[0-9.eE+-]+', '"volume":1.000000', new)
            new = re.sub(r'"channelVolumes":\s*\[[^\]]*\]',
                         lambda m: '"channelVolumes":[' + ", ".join(
                             "1.000000" for _ in m.group(0).split(":", 1)[1].strip("[] ").split(",")) + "]",
                         new)
            new = re.sub(r'"mute":\s*true', '"mute":false', new)
        if existing_sinks is not None:
            new = strip_cinegain_targets(new, existing_sinks)
        if new != line:
            changed += 1
        out.append(new)
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


# -- WirePlumber's copy in memory (#26) -------------------------------------------
# WirePlumber (0.5, state-stream.lua) loads stream-properties once and writes its
# in-memory table back on the next save, so a cleaned file alone does not hold.
# Live nodes are no problem: a Props change via pw-cli makes WirePlumber save the
# new value itself (`wpctl set-volume` would only touch the mixer channelVolumes).
# For programs that are not playing there is no API, but switching the settings
# node.stream.restore-props/-target off drops the state object, and switching them
# on again creates a new one that loads the file. Runtime settings only (no --save),
# no restart, no rebuilt links.
WP_SETTINGS = ("node.stream.restore-target", "node.stream.restore-props")
WP_SAVE_GRACE_S = 1.5   # WirePlumber saves 1 s after a change; a pending save must land first


def _wp_get(key, runner):
    try:
        res = runner(["wpctl", "settings", key], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^Value:\s*(true|false)\s*$", res.stdout or "", re.MULTILINE)
    return None if res.returncode != 0 or not m else m.group(1) == "true"


def _wp_set(key, value, runner):
    try:
        res = runner(["wpctl", "settings", key, "true" if value else "false"],
                     capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def reload_wireplumber_state(clean, runner=subprocess.run, sleep=time.sleep):
    """
    Runs clean() while WirePlumber's stream state is switched off, so the running
    instance loads the cleaned file instead of writing its old copy back.
    Returns (clean()'s result, True if the settings were switched and restored).
    Every setting that was switched off is switched back on, whatever happens.
    """
    values = {key: _wp_get(key, runner) for key in WP_SETTINGS}
    if None in values.values():
        log.warning("wpctl settings not readable (%s): stream-properties cleaned without reload", values)
        return clean(), False
    if not any(values.values()):
        return clean(), True  # stream state is off: WirePlumber holds no copy
    switched = []
    ok = True
    try:
        for key in WP_SETTINGS:
            if values[key]:
                if not _wp_set(key, False, runner):
                    ok = False
                    break
                switched.append(key)
        if ok:
            sleep(WP_SAVE_GRACE_S)
        result = clean()
    finally:
        for key in reversed(switched):
            if not _wp_set(key, True, runner):
                ok = False
        restored = all(_wp_get(key, runner) is True for key in switched)
        if not restored:
            log.error("WirePlumber settings %s could not be switched back on", switched)
    ok = ok and restored
    log.info("WirePlumber stream state %s", "reloaded from the cleaned file" if ok else "NOT reloaded")
    return result, ok


def reset_saved_volumes(existing_sinks=None, runner=subprocess.run, sleep=time.sleep):
    """
    Full reset of the saved values: cleans stream-properties (with backup) and makes
    the running WirePlumber load it. Only for the real file: tests point
    CINEGAIN_WP_STREAM_PROPS elsewhere and never reach the live WirePlumber.
    Returns {"file_entries", "backup", "wireplumber": reloaded|failed|skipped}.
    """
    path = paths.WIREPLUMBER_STREAM_PROPS

    def clean():
        return clean_stream_properties(path, existing_sinks=existing_sinks)

    if os.path.abspath(path) != os.path.abspath(paths.DEFAULT_WP_STREAM_PROPS):
        (n, backup), state = clean(), "skipped"
    else:
        (n, backup), ok = reload_wireplumber_state(clean, runner, sleep)
        state = "reloaded" if ok else "failed"
    return {"file_entries": n, "backup": backup, "wireplumber": state}


def restart_wireplumber(runner=subprocess.run):
    """Last resort, only on explicit request: rebuilds all audio links (playback may pause)."""
    log.warning("restarting WirePlumber on request")
    try:
        res = runner(["systemctl", "--user", "restart", "wireplumber"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log.error("restarting WirePlumber failed: %s", e)
        return False
    if res.returncode != 0:
        log.error("restarting WirePlumber failed: %s", (res.stderr or "").strip())
    return res.returncode == 0


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


_DB = r"(-?[0-9.]+|-inf)"
_HIST_PATTERNS = (
    ("lowered", re.compile(r"hidden volume of node (\d+) \(([^,]*), ([^)]*)\) is [0-9.]+ = " + _DB
                           + r" dB(?: \(was " + _DB + r" dB\))?( \[external\])?")),
    ("mixer", re.compile(r"mixer volume of node (\d+) \(([^,]*), ([^)]*)\) is " + _DB
                         + r" dB = [0-9]+ %(?: \(was " + _DB + r" dB\))?( \[external\])?")),
    ("mute", re.compile(r"mute of node (\d+) \(([^,]*), ([^)]*)\) is (on|off)(?: \(was (?:on|off)\))?"
                        r"( \[external\])?")),
    ("auto", re.compile(r"fading node (\d+) \(([^)]*)\) from (-?[0-9.]+) dB")),
    ("set", re.compile(r"hidden volume of node (\d+) \(([^)]*)\) set to [0-9.]+ = (-?[0-9.]+|-inf) dB by (\w+)")),
    ("full_reset", re.compile(r"full volume reset done")),
)
_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[.,]\d+ \d+ \S+ \S+\s+(\S+): (.*)$")


def wireplumber_events(since, runner=subprocess.run):
    """
    "link failed" errors of WirePlumber from the user journal since `since` (epoch),
    as timeline events (#44). One cheap journalctl call (the unit logs a few lines a
    day); empty if the journal is not readable.
    """
    try:
        res = runner(["journalctl", "--user", "-u", "wireplumber", "--since", f"@{int(since)}",
                      "-o", "json", "--no-pager", "-q"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    events = []
    for line in (res.stdout or "").splitlines():
        try:
            obj = json.loads(line)
            msg = obj.get("MESSAGE")
            ts = int(obj.get("__REALTIME_TIMESTAMP")) / 1e6
        except (ValueError, TypeError):
            continue
        if isinstance(msg, str) and "link failed" in msg:
            events.append({"time": ts, "kind": "link_failed", "key": "", "db": None, "was_db": None,
                           "who": "wireplumber"})
    return events


def history(log_path=None, days=7, limit=500, key=None, now=None, journal=None):
    """
    Volume events from the debug log (and its rotated files), newest first: dicts
    with time (epoch), kind (lowered/raised/mixer/muted/unmuted/auto/set/full_reset,
    plus link_failed from WirePlumber's journal), key, db, was_db, who (auto, user,
    cli, external, wireplumber or "" for old lines). The journal is read for the
    real log only (journal=None) or when asked.
    """
    base = log_path or paths.LOG_FILE
    files = [f"{base}.{i}" for i in range(4, 0, -1)] + [base]
    cutoff = (now or time.time()) - days * 86400
    events = []
    if journal if journal is not None else log_path is None:
        events.extend(wireplumber_events(cutoff))
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
                    ev.update(key=hit.group(2), db=_f(hit.group(4)), was_db=_f(hit.group(5)),
                              who="external" if hit.group(6) else "")
                    if ev["was_db"] is not None and ev["db"] is not None and ev["db"] > ev["was_db"]:
                        ev["kind"] = "raised"
                elif kind == "mixer":
                    ev.update(key=hit.group(2), db=_f(hit.group(4)), was_db=_f(hit.group(5)),
                              who="external" if hit.group(6) else "")
                elif kind == "mute":
                    ev.update(kind="muted" if hit.group(4) == "on" else "unmuted", key=hit.group(2),
                              who="external" if hit.group(5) else "")
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
