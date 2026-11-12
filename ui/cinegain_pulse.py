"""
Thin wrapper around `pactl` for listing sinks and playback streams and for
moving streams between sinks.

`pactl -f json` (libpulse >= 16) is used when available; older systems fall back
to parsing the human-readable output. Everything else in Cinegain talks to the
sound server only through PulseControl, so tests can swap in a fake.
"""
import json
import re
import subprocess

import cinegain_log
import cinegain_paths as paths
import cinegain_pwdump

log = cinegain_log.get("pulse")


def is_cinegain_name(name):
    """True for sinks and nodes that belong to Cinegain itself (incl. the legacy name)."""
    n = (name or "").lower()
    return "cinegain" in n or "kinoton" in n


class Stream:
    """One playback stream (PulseAudio sink-input)."""

    __slots__ = ("index", "sink", "app_name", "binary", "node_name", "volume_pct", "muted", "corked",
                 "node_id", "volume_min_pct")

    def __init__(self, index, sink, app_name="", binary="", node_name="", volume_pct=100,
                 muted=False, corked=False, node_id=None, volume_min_pct=None):
        self.index = str(index)
        # quietest channel: a fade to 100 % starts there, so no channel jumps up (#29)
        self.volume_min_pct = int(volume_pct if volume_min_pct is None else volume_min_pct)
        # PipeWire node id ("object.id"); the pulse index is the object serial, not the id.
        self.node_id = int(node_id) if str(node_id or "").isdigit() else None
        self.sink = str(sink)
        self.app_name = app_name or ""
        self.binary = binary or ""
        self.node_name = node_name or ""
        self.volume_pct = int(volume_pct)
        self.muted = bool(muted)
        self.corked = bool(corked)

    @property
    def is_own(self):
        """Cinegain's own output streams must never be routed."""
        return is_cinegain_name(self.node_name) or is_cinegain_name(self.app_name)

    @property
    def display_name(self):
        return self.app_name or self.node_name or self.binary or f"Stream #{self.index}"

    def __repr__(self):
        return f"Stream({self.index}, sink={self.sink}, app={self.app_name!r}, bin={self.binary!r})"


def _run(args):
    try:
        return subprocess.check_output(args, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        log.warning("%s failed (rc=%s): %s", " ".join(args), e.returncode, (e.stderr or "").strip())
        raise
    except OSError as e:
        log.error("%s could not run: %s", args[0], e)
        raise


def _quiet(args):
    try:
        res = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        log.error("%s could not run: %s", args[0], e)
        return False
    if res.returncode != 0:
        log.warning("%s failed (rc=%s): %s", " ".join(args), res.returncode, (res.stderr or "").strip())
        return False
    log.debug("%s ok", " ".join(args))
    return True


def parse_streams_json(text):
    streams = []
    for item in json.loads(text or "[]"):
        props = item.get("properties") or {}
        vols = []
        for ch in (item.get("volume") or {}).values():
            m = re.match(r"(\d+)%", str(ch.get("value_percent", "")))
            if m:
                vols.append(int(m.group(1)))
        streams.append(
            Stream(
                index=item.get("index"),
                sink=item.get("sink"),
                app_name=props.get("application.name", ""),
                binary=props.get("application.process.binary", ""),
                node_name=props.get("node.name", ""),
                volume_pct=max(vols) if vols else 100,
                volume_min_pct=min(vols) if vols else 100,
                muted=item.get("mute", False),
                corked=item.get("corked", False),
                node_id=props.get("object.id"),
            )
        )
    return streams


def parse_streams_text(text):
    streams = []
    for block in (text or "").split("Sink Input #")[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        fields = {"index": lines[0].strip(), "sink": "", "app_name": "", "binary": "",
                  "node_name": "", "volume_pct": 100, "muted": False, "corked": False, "node_id": None,
                  "volume_min_pct": None}
        for raw in lines[1:]:
            s = raw.strip()
            if s.startswith("Sink:"):
                fields["sink"] = s.split(":", 1)[1].strip()
            elif s.startswith("Mute:"):
                fields["muted"] = s.split(":", 1)[1].strip().lower() in ("yes", "ja")
            elif s.startswith("Corked:"):
                fields["corked"] = s.split(":", 1)[1].strip().lower() in ("yes", "ja")
            elif s.startswith("Volume:"):
                pcts = [int(p) for p in re.findall(r"(\d+)%", s)]
                if pcts:
                    fields["volume_pct"] = max(pcts)
                    fields["volume_min_pct"] = min(pcts)
            else:
                for key, prop in (("app_name", "application.name"),
                                  ("binary", "application.process.binary"),
                                  ("node_name", "node.name"),
                                  ("node_id", "object.id")):
                    m = re.match(re.escape(prop) + r'\s*=\s*"(.*)"$', s)
                    if m:
                        fields[key] = m.group(1)
        streams.append(Stream(**fields))
    return streams


def parse_sinks_short(text):
    """Returns {sink_id: sink_name} from `pactl list sinks short`."""
    sinks = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks[parts[0].strip()] = parts[1].strip()
    return sinks


class PulseControl:
    """Real backend talking to pipewire-pulse via pactl."""

    def __init__(self):
        self._json_ok = None

    def list_streams(self):
        if self._json_ok is not False:
            try:
                streams = parse_streams_json(_run(["pactl", "-f", "json", "list", "sink-inputs"]))
                self._json_ok = True
                return streams
            except (subprocess.CalledProcessError, ValueError, OSError):
                self._json_ok = False
        try:
            return parse_streams_text(_run(["pactl", "list", "sink-inputs"]))
        except (subprocess.CalledProcessError, OSError):
            return []

    def list_sinks(self):
        try:
            return parse_sinks_short(_run(["pactl", "list", "sinks", "short"]))
        except (subprocess.CalledProcessError, OSError):
            return {}

    def default_sink(self):
        # Inside a GUI tick the default sink comes from the tick's pw-dump metadata, no extra
        # process; elsewhere (and if the metadata has none) pactl answers (#38).
        scope_dump = cinegain_pwdump.scoped_snapshot()
        name = cinegain_pwdump.default_sink(scope_dump) if scope_dump is not None else ""
        if name:
            return name
        try:
            return _run(["pactl", "get-default-sink"]).strip()
        except (subprocess.CalledProcessError, OSError):
            return ""

    def hardware_sink(self, sinks=None):
        """Name of the sink audio should go to when it bypasses Cinegain."""
        sinks = self.list_sinks() if sinks is None else sinks
        default = self.default_sink()
        names = [n for n in sinks.values() if not is_cinegain_name(n)]
        if default and not is_cinegain_name(default) and default in names:
            return default
        if names:
            return names[0]
        return "@DEFAULT_SINK@"

    def move(self, stream_index, sink_name):
        return _quiet(["pactl", "move-sink-input", str(stream_index), sink_name])

    def set_volume_pct(self, stream_index, pct):
        """Mixer volume of a stream, all channels, in % (fractions allowed: raw volume units)."""
        raw = int(round(65536 * max(0.0, float(pct)) / 100.0))
        return _quiet(["pactl", "set-sink-input-volume", str(stream_index), str(raw)])

    def set_volume_100(self, stream_index):
        _quiet(["pactl", "set-sink-input-volume", str(stream_index), "100%"])
        _quiet(["pactl", "set-sink-input-mute", str(stream_index), "0"])

    def set_sink_volume_100(self, sink_name):
        _quiet(["pactl", "set-sink-volume", sink_name, "100%"])
        _quiet(["pactl", "set-sink-mute", sink_name, "0"])


_TARGET = r'"target"\s*:\s*"(?P<name>(?:cinegain|kinoton)[^"]*)"'
_TARGET_PATTERNS = (
    (re.compile(r',\s*' + _TARGET), ""),
    (re.compile(_TARGET + r'\s*,\s*'), ""),
    (re.compile(r'\{\s*' + _TARGET + r'\s*\}'), "{}"),
)


STANDARD_SINK_MEDIA_NAME = "Cinegain (Filmton Dialog & Dynamik)"


def sink_media_name(line):
    """Returns the unescaped media.name if line is an Audio/Sink or Output/Audio entry for Cinegain/Kinoton."""
    key = line.split("=", 1)[0]
    for prefix in ("Audio/Sink:media.name:", "Output/Audio:media.name:"):
        if key.startswith(prefix):
            name = key[len(prefix):].replace("\\s", " ")
            low = name.lower()
            if "cinegain" in low or "kinoton" in low:
                return name
    return None


def is_dead_cinegain_sink_entry(line, live_media_names):
    """True when line stores properties for a Cinegain/Kinoton sink not in live_media_names."""
    if live_media_names is None:
        return False
    name = sink_media_name(line)
    return name is not None and name not in live_media_names


def live_sink_media_names(existing_sinks):
    """Maps active Cinegain sink node names to their WirePlumber media.name values."""
    if existing_sinks is None:
        return None
    names = set()
    if "cinegain_sink" in existing_sinks:
        names.add(STANDARD_SINK_MEDIA_NAME)
    try:
        import cinegain_pwdump
        for obj in cinegain_pwdump.load():
            props = (obj.get("info") or {}).get("props") or {}
            if props.get("node.name") in existing_sinks:
                mname = props.get("media.name")
                if mname:
                    names.add(mname)
    except Exception:
        pass
    return names


def strip_cinegain_targets(text, keep=()):
    """Drops saved `"target":"cinegain..."` entries, except targets named in keep."""
    for rx, repl in _TARGET_PATTERNS:
        text = rx.sub(lambda m, r=repl: m.group(0) if m.group("name") in keep else r, text)
    return text


def clean_wireplumber_targets(path, keep=(), live_media_names=None):
    """
    Removes sticky Cinegain targets and dead Cinegain sink entries from WirePlumber's
    stream-properties so that streams are not trapped on a sink that no longer exists.
    `keep` = sink names that still exist and may stay. Returns True if the file changed.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    low = content.lower()
    if "cinegain" not in low and "kinoton" not in low:
        return False
    if live_media_names is None:
        live_media_names = {STANDARD_SINK_MEDIA_NAME} if "cinegain_sink" in keep else set()
    lines = content.splitlines(keepends=True)
    kept_lines = [
        strip_cinegain_targets(line, keep)
        for line in lines
        if not is_dead_cinegain_sink_entry(line, live_media_names)
    ]
    cleaned = "".join(kept_lines)
    if cleaned == content:
        return False
    try:
        paths.write_text_atomic(path, cleaned)  # WirePlumber may read it any moment (#42)
    except OSError:
        return False
    return True
