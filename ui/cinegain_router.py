"""
Routing engine: moves every playback stream to the sink its program asks for,
starts per-program sinks on demand and stops idle ones.

- switch off (or Cinegain off)  -> straight to the hardware sink
- switch on, no own setting     -> cinegain_sink (Standard)
- switch on, own setting        -> the program's own sink cinegain_app_<...>

Router.tick() is called periodically by the GUI (and by `cinegain watch`).
It only issues `pactl move-sink-input` for streams that are on the wrong sink,
so steady state costs two pactl calls per tick.
"""
import time

from cinegain_profiles import (
    PROFILE_STANDARD,
    SinkProcesses,
    find_node_id,
    full_params,
    push_params,
    sink_name,
)
from cinegain_pulse import PulseControl, is_cinegain_name
from cinegain_registry import AppRegistry, make_key, profile_id_for

# A per-program sink with no stream assigned is stopped after this many seconds.
PROFILE_IDLE_STOP_S = 45.0
# Minimum delay between two start attempts of the same sink.
START_RETRY_S = 5.0


class AppView:
    """Snapshot of one program for the UI."""

    __slots__ = ("key", "label", "name", "binary", "enabled", "own", "params", "playing",
                 "paused", "last_seen", "sink", "volume_pct", "muted", "streams")

    def __init__(self, entry):
        self.key = entry["key"]
        self.label = entry["label"]
        self.name = entry["name"]
        self.binary = entry["binary"]
        self.enabled = entry["enabled"]
        self.params = entry["params"]
        self.own = entry["params"] is not None
        self.last_seen = entry["last_seen"]
        self.playing = False
        self.paused = False
        self.sink = None
        self.volume_pct = 100
        self.muted = False
        self.streams = []

    @property
    def routed(self):
        return bool(self.streams) and is_cinegain_name(self.sink or "")


class Router:
    def __init__(self, registry=None, sinks=None, pulse=None, clock=time.time):
        self.registry = registry or AppRegistry()
        self.sinks = sinks or SinkProcesses()
        self.pulse = pulse or PulseControl()
        self.clock = clock
        self._last_used = {}
        self._start_attempts = {}
        self._node_ids = {}

    @staticmethod
    def target_profile(key, entry, active=True):
        """None = hardware, "standard", or the program's own profile id."""
        if not active or not entry["enabled"]:
            return None
        if entry["params"]:
            return profile_id_for(key)
        return PROFILE_STANDARD

    def tick(self, active=True, route=True):
        """
        Scans streams, updates the registry and (if route) enforces routing.
        Returns AppView objects: playing first, then most recently heard.
        """
        now = self.clock()
        self.registry.reload_if_changed()
        streams = [s for s in self.pulse.list_streams() if not s.is_own]
        sink_ids = self.pulse.list_sinks()
        hw = self.pulse.hardware_sink(sink_ids)
        sink_by_name = {name: sid for sid, name in sink_ids.items()}

        by_key = {}
        for st in streams:
            key = self.registry.observe(st.app_name or st.node_name, st.binary)
            by_key.setdefault(key, []).append(st)

        views = {e["key"]: AppView(e) for e in self.registry.entries()}
        wanted = set()
        for key, sts in by_key.items():
            view = views[key]
            view.streams = sts
            view.playing = any(not s.corked for s in sts)
            view.paused = not view.playing
            view.volume_pct = min(s.volume_pct for s in sts)
            view.muted = all(s.muted for s in sts)
            entry = self.registry.get(key)
            profile = self.target_profile(key, entry, active)
            if profile not in (None, PROFILE_STANDARD):
                wanted.add(profile)
            target = hw if profile is None else sink_name(profile)
            target_id = sink_by_name.get(target)
            for s in sts:
                current = sink_ids.get(s.sink, s.sink)
                if route:
                    if profile is None:
                        # Only pull streams off Cinegain; never fight a user's own
                        # choice of hardware output (e.g. HDMI in pavucontrol).
                        if is_cinegain_name(current):
                            self.pulse.move(s.index, hw)
                            current = hw
                    elif target_id is None:
                        if profile != PROFILE_STANDARD:
                            # Own sink not up yet: spawn it (with backoff, in case it
                            # fails to come up) and route on a later tick.
                            if now - self._start_attempts.get(profile, 0.0) > START_RETRY_S:
                                self._start_attempts[profile] = now
                                self.sinks.start(profile, full_params(entry["params"]),
                                                 f"Cinegain · {entry['label']}")
                    elif s.sink != target_id:
                        self.pulse.move(s.index, target)
                        current = target
                view.sink = current

        if route:
            self._reap_idle(wanted, now, active)
        self.registry.flush()
        return sorted(views.values(), key=lambda v: (not v.playing, not v.streams, -v.last_seen, v.label.lower()))

    def _reap_idle(self, wanted, now, active):
        for pid in wanted:
            self._last_used[pid] = now
        for pid in self.sinks.running():
            if pid == PROFILE_STANDARD:
                continue
            last = self._last_used.setdefault(pid, now)
            if not active or (pid not in wanted and now - last > PROFILE_IDLE_STOP_S):
                self.sinks.stop(pid)
                self._last_used.pop(pid, None)
                self._node_ids.pop(pid, None)

    def push_program_params(self, key):
        """Applies a program's own parameters live to its running sink, if any."""
        entry = self.registry.get(key)
        if not entry or not entry["params"]:
            return False
        pid = profile_id_for(key)
        if pid not in self.sinks.running():
            return False
        node_id = self._node_ids.get(pid) or find_node_id(sink_name(pid))
        if push_params(node_id, full_params(entry["params"])):
            self._node_ids[pid] = node_id
            return True
        self._node_ids.pop(pid, None)
        return False

    def release_all(self):
        """Moves every stream off any Cinegain sink and stops all per-program sinks."""
        sink_ids = self.pulse.list_sinks()
        hw = self.pulse.hardware_sink(sink_ids)
        for st in self.pulse.list_streams():
            if st.is_own:
                continue
            if is_cinegain_name(sink_ids.get(st.sink, "")):
                self.pulse.move(st.index, hw)
        self.sinks.stop_all_profiles()
        self._last_used.clear()
        self._node_ids.clear()
        self.registry.flush(force=True)

    def restore_volume(self, key):
        """Sets every live stream of a program back to 100 % and unmutes it."""
        for st in self.pulse.list_streams():
            if not st.is_own and make_key(st.app_name or st.node_name, st.binary) == key:
                self.pulse.set_volume_100(st.index)
