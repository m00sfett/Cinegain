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

import cinegain_log
from cinegain_profiles import (
    PROFILE_STANDARD,
    STANDARD_SINK,
    SinkProcesses,
    find_node_id,
    full_params,
    push_params,
    read_meter,
    sink_name,
)
from cinegain_pulse import PulseControl, is_cinegain_name
from cinegain_registry import AppRegistry, make_key, profile_id_for

# A per-program sink with no stream assigned is stopped after this many seconds.
PROFILE_IDLE_STOP_S = 45.0
# Minimum delay between two start attempts of the same sink.
START_RETRY_S = 5.0
# While audio flows, each filter's meters are written to the debug log this often.
METER_LOG_S = 10.0

log = cinegain_log.get("router")


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
        # Soft off: every Cinegain sink passes audio through untouched (bypass=1)
        # while streams stay where they are. Removing sinks makes browsers pause.
        self.bypass = False
        self._seen_streams = {}   # stream index -> (key, sink name, corked)
        self._known_pids = {}     # profile id -> pid seen on the last tick
        self._meter_state = {}    # pid -> (last seq, last log time)

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
                            why = "Cinegain off" if not active else "program switched off"
                            log.info("move stream #%s (%s) %s -> %s: %s", s.index, key, current, hw, why)
                            self.pulse.move(s.index, hw)
                            current = hw
                    elif target_id is None:
                        if profile != PROFILE_STANDARD:
                            # Own sink not up yet: spawn it (with backoff, in case it
                            # fails to come up) and route on a later tick.
                            if now - self._start_attempts.get(profile, 0.0) > START_RETRY_S:
                                self._start_attempts[profile] = now
                                log.info("own sink %s for %s not up: starting it (stream #%s waits on %s)",
                                         profile, key, s.index, current)
                                self.sinks.start(profile, self._sink_params(entry["params"]),
                                                 f"Cinegain · {entry['label']}")
                        else:
                            log.warning("standard sink %s missing: stream #%s (%s) stays on %s",
                                        STANDARD_SINK, s.index, key, current)
                    elif s.sink != target_id:
                        why = "own setting" if profile != PROFILE_STANDARD else "follows Standard"
                        log.info("move stream #%s (%s) %s -> %s: %s", s.index, key, current, target, why)
                        self.pulse.move(s.index, target)
                        current = target
                view.sink = current

        self._log_stream_changes(streams, sink_ids)
        if route:
            self._reap_idle(wanted, now, active)
        self._watch_processes(now)
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
                reason = "Cinegain off" if not active else f"no stream for {PROFILE_IDLE_STOP_S:.0f} s"
                self.sinks.stop(pid, reason)
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
        log.debug("push own setting of %s to %s (node %s)", key, pid, node_id)
        if push_params(node_id, self._sink_params(entry["params"])):
            self._node_ids[pid] = node_id
            return True
        self._node_ids.pop(pid, None)
        return False

    def release_all(self):
        """Moves every stream off any Cinegain sink and stops all per-program sinks."""
        log.info("release all streams and stop all per-program sinks")
        sink_ids = self.pulse.list_sinks()
        hw = self.pulse.hardware_sink(sink_ids)
        for st in self.pulse.list_streams():
            if st.is_own:
                continue
            if is_cinegain_name(sink_ids.get(st.sink, "")):
                log.info("move stream #%s (%s) -> %s: release", st.index, st.display_name, hw)
                self.pulse.move(st.index, hw)
        self.sinks.stop_all_profiles("release all")
        self._last_used.clear()
        self._node_ids.clear()
        self.registry.flush(force=True)

    def restore_volume(self, key):
        """Sets every live stream of a program back to 100 % and unmutes it."""
        for st in self.pulse.list_streams():
            if not st.is_own and make_key(st.app_name or st.node_name, st.binary) == key:
                self.pulse.set_volume_100(st.index)

    # -- soft off ------------------------------------------------------------
    def _sink_params(self, params):
        out = full_params(params)
        out["bypass"] = 1.0 if self.bypass else 0.0
        return out

    def set_bypass(self, on):
        """
        Soft on/off for every running Cinegain sink. Streams are not moved and no
        sink disappears, so players keep playing. Returns the profiles that failed.
        """
        self.bypass = bool(on)
        log.info("filter %s (bypass=%d on all sinks)", "OFF" if on else "ON", int(on))
        failed = []
        for pid in sorted(self.sinks.running()):
            node_id = self._node_ids.get(pid) or find_node_id(sink_name(pid))
            if push_params(node_id, {"bypass": 1.0 if on else 0.0}):
                self._node_ids[pid] = node_id
            else:
                self._node_ids.pop(pid, None)
                failed.append(pid)
        if failed:
            log.warning("bypass=%d not applied to: %s", int(on), ", ".join(failed))
        return failed

    # -- debug log -----------------------------------------------------------
    def _log_stream_changes(self, streams, sink_ids):
        current = {}
        for st in streams:
            key = make_key(st.app_name or st.node_name, st.binary)
            sink = sink_ids.get(st.sink, st.sink)
            current[st.index] = (key, sink, st.corked)
            old = self._seen_streams.get(st.index)
            if old is None:
                log.info("stream #%s appeared: %s (app=%r bin=%r node=%r) on %s%s vol=%s%%%s",
                         st.index, key, st.app_name, st.binary, st.node_name, sink,
                         " paused" if st.corked else "", st.volume_pct, " muted" if st.muted else "")
            else:
                if old[1] != sink:
                    log.info("stream #%s (%s) now on %s (was %s)", st.index, key, sink, old[1])
                if old[2] != st.corked:
                    log.debug("stream #%s (%s) %s", st.index, key, "paused" if st.corked else "playing")
        for index, (key, sink, _) in self._seen_streams.items():
            if index not in current:
                log.info("stream #%s gone: %s (was on %s)", index, key, sink)
        self._seen_streams = current

    def _watch_processes(self, now):
        """Logs filter processes that vanished on their own, and their meters while audio flows."""
        pids = {}
        for profile in self.sinks.running():
            pids[profile] = self.sinks.pid(profile)
        for profile, pid in self._known_pids.items():
            if profile not in pids:
                log.info("filter process %s pid=%s is no longer running", profile, pid)
        for profile, pid in pids.items():
            if self._known_pids.get(profile) not in (None, pid):
                log.warning("filter process %s restarted: pid %s -> %s",
                            profile, self._known_pids[profile], pid)
            self._log_meter(profile, pid, now)
        self._known_pids = pids

    def _log_meter(self, profile, pid, now):
        if not log.isEnabledFor(10) or not isinstance(pid, int):
            return
        m = read_meter(pid)
        if not m:
            return
        seq, level, leveler, gr, limit = m
        last_seq, last_t = self._meter_state.get(pid, (None, 0.0))
        if seq == last_seq:
            return  # no audio through this filter since the last look
        if now - last_t >= METER_LOG_S:
            log.debug("meter %s: in %.1f dBFS, leveler %+.1f dB, comp %.1f dB, limiter %.1f dB%s",
                      profile, level, leveler, gr, limit, " (bypass)" if self.bypass else "")
            self._meter_state[pid] = (seq, now)
        else:
            self._meter_state[pid] = (seq, last_t)
