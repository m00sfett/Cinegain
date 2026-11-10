"""
Routing engine: moves every playback stream to the sink its application's
profile asks for, starts profile sinks on demand and stops idle ones.

Router.tick() is called periodically by the GUI (and by `cinegain watch`).
It only issues `pactl move-sink-input` for streams that are on the wrong sink,
so steady state costs two pactl calls per tick.
"""
import time

from cinegain_pulse import PulseControl, is_cinegain_name
from cinegain_registry import AppRegistry, PROFILE_OFF, PROFILE_STANDARD, make_key
from cinegain_profiles import ProfileCatalog, SinkProcesses, sink_name

# A profile sink with no stream assigned is stopped after this many seconds.
PROFILE_IDLE_STOP_S = 45.0
# Minimum delay between two start attempts of the same profile sink.
START_RETRY_S = 5.0


class AppView:
    """Snapshot of one application for the UI."""

    __slots__ = ("key", "label", "name", "binary", "profile", "playing", "paused", "last_seen",
                 "sink", "volume_pct", "muted", "streams")

    def __init__(self, entry):
        self.key = entry["key"]
        self.label = entry["label"]
        self.name = entry["name"]
        self.binary = entry["binary"]
        self.profile = entry["profile"]
        self.last_seen = entry["last_seen"]
        self.playing = False
        self.paused = False
        self.sink = None
        self.volume_pct = 100
        self.muted = False
        self.streams = []

    def as_dict(self):
        return {s: getattr(self, s) for s in self.__slots__ if s != "streams"} | {
            "streams": [st.index for st in self.streams]
        }


class Router:
    def __init__(self, registry=None, catalog=None, sinks=None, pulse=None, clock=time.time):
        self.registry = registry or AppRegistry()
        self.catalog = catalog or ProfileCatalog()
        self.sinks = sinks or SinkProcesses(self.catalog)
        self.pulse = pulse or PulseControl()
        self.clock = clock
        self._profile_last_used = {}
        self._start_attempts = {}

    def effective_profile(self, profile):
        """Profiles whose preset vanished fall back to standard."""
        if profile in (PROFILE_OFF, PROFILE_STANDARD):
            return profile
        return profile if self.catalog.exists(profile) else PROFILE_STANDARD

    def tick(self, active=True, route=True):
        """
        Scans streams, updates the registry and (if route) enforces routing.
        active=False means Cinegain is switched off: everything goes to hardware.
        Returns a list of AppView, playing apps first, then most recently heard.
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
        wanted_profiles = set()
        for key, sts in by_key.items():
            view = views[key]
            view.streams = sts
            view.playing = any(not s.corked for s in sts)
            view.paused = not view.playing
            view.volume_pct = min(s.volume_pct for s in sts)
            view.muted = all(s.muted for s in sts)
            profile = self.effective_profile(view.profile) if active else PROFILE_OFF
            target = hw if profile == PROFILE_OFF else sink_name(profile)
            if profile not in (PROFILE_OFF, PROFILE_STANDARD):
                wanted_profiles.add(profile)
            target_id = sink_by_name.get(target)
            for s in sts:
                current = sink_ids.get(s.sink, s.sink)
                if route:
                    if profile == PROFILE_OFF:
                        # Only pull streams off Cinegain; never fight a user's own
                        # choice of hardware output (e.g. HDMI in pavucontrol).
                        if is_cinegain_name(current):
                            self.pulse.move(s.index, hw)
                            current = hw
                    elif target_id is None:
                        if profile != PROFILE_STANDARD:
                            # Profile sink not up yet: spawn it (with backoff, in case
                            # it fails to come up), route on a later tick.
                            if now - self._start_attempts.get(profile, 0.0) > START_RETRY_S:
                                self._start_attempts[profile] = now
                                self.sinks.start(profile)
                    elif s.sink != target_id:
                        self.pulse.move(s.index, target)
                        current = target
                view.sink = current

        if route:
            self._reap_idle_profiles(wanted_profiles, now, active)
        self.registry.flush()
        return sorted(views.values(), key=lambda v: (not v.playing, not v.streams, -v.last_seen, v.label.lower()))

    def _reap_idle_profiles(self, wanted, now, active):
        for pid in wanted:
            self._profile_last_used[pid] = now
        for pid in self.sinks.running():
            if pid == PROFILE_STANDARD:
                continue
            last = self._profile_last_used.setdefault(pid, now)
            if not active or (pid not in wanted and now - last > PROFILE_IDLE_STOP_S):
                self.sinks.stop(pid)
                self._profile_last_used.pop(pid, None)

    def release_all(self):
        """Moves every stream off any Cinegain sink and stops all profile sinks."""
        sink_ids = self.pulse.list_sinks()
        hw = self.pulse.hardware_sink(sink_ids)
        for st in self.pulse.list_streams():
            if st.is_own:
                continue
            if is_cinegain_name(sink_ids.get(st.sink, "")):
                self.pulse.move(st.index, hw)
        self.sinks.stop_all_profiles()
        self._profile_last_used.clear()
        self.registry.flush(force=True)

    def restore_volume(self, key):
        """Sets every live stream of an app back to 100 % and unmutes it."""
        for st in self.pulse.list_streams():
            if not st.is_own and make_key(st.app_name or st.node_name, st.binary) == key:
                self.pulse.set_volume_100(st.index)
