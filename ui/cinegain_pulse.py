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


def is_cinegain_name(name):
    """True for sinks and nodes that belong to Cinegain itself (incl. the legacy name)."""
    n = (name or "").lower()
    return "cinegain" in n or "kinoton" in n


class Stream:
    """One playback stream (PulseAudio sink-input)."""

    __slots__ = ("index", "sink", "app_name", "binary", "node_name", "volume_pct", "muted", "corked")

    def __init__(self, index, sink, app_name="", binary="", node_name="", volume_pct=100,
                 muted=False, corked=False):
        self.index = str(index)
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
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True)


def _quiet(args):
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
                muted=item.get("mute", False),
                corked=item.get("corked", False),
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
                  "node_name": "", "volume_pct": 100, "muted": False, "corked": False}
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
            else:
                for key, prop in (("app_name", "application.name"),
                                  ("binary", "application.process.binary"),
                                  ("node_name", "node.name")):
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
        _quiet(["pactl", "move-sink-input", str(stream_index), sink_name])

    def set_volume_100(self, stream_index):
        _quiet(["pactl", "set-sink-input-volume", str(stream_index), "100%"])
        _quiet(["pactl", "set-sink-input-mute", str(stream_index), "0"])

    def set_sink_volume_100(self, sink_name):
        _quiet(["pactl", "set-sink-volume", sink_name, "100%"])
        _quiet(["pactl", "set-sink-mute", sink_name, "0"])


def clean_wireplumber_targets(path):
    """
    Removes sticky Cinegain targets from WirePlumber's stream-properties so that
    streams are not trapped on a sink that no longer exists. Returns True if the
    file changed.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    if "cinegain" not in content and "kinoton" not in content:
        return False
    cleaned = re.sub(r',\s*"target"\s*:\s*"(?:cinegain|kinoton)[^"]*"', "", content)
    cleaned = re.sub(r'"target"\s*:\s*"(?:cinegain|kinoton)[^"]*"\s*,\s*', "", cleaned)
    cleaned = re.sub(r'\{\s*"target"\s*:\s*"(?:cinegain|kinoton)[^"]*"\s*\}', "{}", cleaned)
    if cleaned == content:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(cleaned)
    except OSError:
        return False
    return True
