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
    def __init__(self, scanner=scan_nodes, setter=set_node_volume, clock=time.time):
        self.scanner = scanner
        self.setter = setter
        self.clock = clock
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
            if n.is_cinegain_output or n.key in auto_keys:
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
        backup_dir = backup_dir or paths.BACKUP_DIR
        os.makedirs(backup_dir, exist_ok=True)
        backup = os.path.join(backup_dir, f"stream-properties-{stamp or time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    log.info("cleaned %d saved stream volumes in %s (backup %s)", changed, path, backup)
    return changed, backup
