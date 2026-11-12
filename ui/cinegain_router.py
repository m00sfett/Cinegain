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
import threading
import time

import cinegain_log
import cinegain_overdrive as overdrive
from cinegain_profiles import (
    PROFILE_STANDARD,
    STANDARD_SINK,
    ProfileCatalog,
    SinkProcesses,
    find_node_id,
    full_params,
    push_params,
    read_meter,
    sink_name,
)
from cinegain_pulse import PulseControl, is_cinegain_name
from cinegain_registry import AppRegistry, is_media_app, make_key, profile_id_for

# A per-program sink with no stream assigned is stopped after this many seconds.
PROFILE_IDLE_STOP_S = 600.0
# Minimum delay between two start attempts of the same sink.
START_RETRY_S = 5.0
# While audio flows, each filter's meters are written to the debug log this often.
METER_LOG_S = 10.0

log = cinegain_log.get("router")


class AppView:
    """Snapshot of one program for the UI."""

    __slots__ = ("key", "label", "name", "binary", "enabled", "own", "params", "playing",
                 "paused", "last_seen", "sink", "volume_pct", "muted", "streams", "mode", "hidden_db")

    def __init__(self, entry):
        self.key = entry["key"]
        self.label = entry["label"]
        self.name = entry["name"]
        self.binary = entry["binary"]
        self.enabled = entry["enabled"]
        self.params = entry["params"]
        self.own = entry["params"] is not None
        self.mode = entry.get("mode")  # None: follows Standard
        self.hidden_db = None  # secretly lowered stream volume (dB) that is only reported
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
    def __init__(self, registry=None, sinks=None, pulse=None, clock=time.time, guard=None):
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
        # Hidden-volume guard (cinegain_volume.VolumeGuard); None in tests and one-shot CLI calls.
        self.guard = guard
        self.saved_reset_in_background = True  # full reset with a guard: WirePlumber reload in a thread

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
            app_name = st.app_name or st.node_name
            key = make_key(app_name, st.binary)
            if st.corked and self.registry.get(key) is None and not is_media_app(app_name, st.binary):
                # #62: ignore paused non-media streams (e.g. Electron audio probes).
                continue
            key = self.registry.observe(app_name, st.binary)
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
                            if self._source_is_attenuated(current, entry):
                                self._duck(s, now)
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
                                try:
                                    self.sinks.start(profile, self._sink_params(entry["params"]),
                                                     f"Cinegain · {entry['label']}")
                                except Exception:
                                    # one broken program must not stop the tick for all others (#31)
                                    log.exception("starting own sink %s for %s failed", profile, key)
                        else:
                            log.warning("standard sink %s missing: stream #%s (%s) stays on %s",
                                        STANDARD_SINK, s.index, key, current)
                    elif s.sink != target_id:
                        why = "own setting" if profile != PROFILE_STANDARD else "follows Standard"
                        log.info("move stream #%s (%s) %s -> %s: %s", s.index, key, current, target, why)
                        if self._target_can_boost(profile, entry):
                            self._duck(s, now)
                        self.pulse.move(s.index, target)
                        current = target
                view.sink = current

        if self.guard is not None:
            auto = {k for k, v in views.items() if v.streams and is_cinegain_name(v.sink or "")}
            try:
                for key, db in self.guard.update(auto, now).items():
                    if key in views:
                        views[key].hidden_db = db
            except Exception:
                log.exception("hidden volume guard failed")
        self._log_stream_changes(streams, sink_ids)
        if route:
            self._reap_idle(wanted, now, active)
        self._watch_processes(now)
        self.registry.flush()
        return sorted(views.values(), key=lambda v: (not v.playing, not v.streams, -v.last_seen, v.label.lower()))

    @staticmethod
    def _sink_can_boost(params):
        """True if a filter sink can amplify audio above 0 dB."""
        if not params:
            return False
        p = full_params(params)
        if float(p.get("bypass", 0.0)) >= 0.5:
            return False
        if any(float(p.get(k, 0.0)) > 0.05
               for k in ("input_db", "bass_db", "dialog_db", "treble_db", "mid_db", "side_db")):
            return True
        return float(p.get("leveler", 0.0)) >= 0.5 and float(p.get("lev_max_boost_db", 0.0)) > 0.05

    def _target_can_boost(self, profile, entry):
        if self.bypass:
            return False
        params = ProfileCatalog().standard_params() if profile == PROFILE_STANDARD else entry.get("params")
        return self._sink_can_boost(params)

    def _source_is_attenuated(self, current_sink, entry):
        """True when leaving a Cinegain sink that attenuated audio below 0 dB toward 0 dB hardware."""
        if self.bypass:
            return False
        if current_sink == STANDARD_SINK:
            params = ProfileCatalog().standard_params()
        else:
            params = entry.get("params")
        if not params:
            return False
        p = full_params(params)
        return float(p.get("input_db", 0.0)) < -0.05

    def _duck(self, stream, now):
        """#40: the target sink may already be boosted; the stream fades in there instead of jumping."""
        if self.guard is None or stream.node_id is None:
            return
        try:
            self.guard.duck(stream.node_id, now)
        except Exception:
            log.exception("ducking stream #%s before the move failed", stream.index)

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
        """
        Brings every live stream of a program's mixer volume back to 100 % (#48). Same
        policy as the full reset: it fades in from the quietest channel at <= 10 dB/s
        (never a jump) and mute is left as it is. The fade runs on the guard a GUI or
        `cinegain watch` keeps stepping; without one (one-shot use) it runs here.
        """
        from cinegain_volume import VolumeGuard

        guard = self.guard or VolumeGuard(scanner=lambda: [])
        now = self.clock()
        for st in self.pulse.list_streams():
            if st.is_own or make_key(st.app_name or st.node_name, st.binary) != key:
                continue
            if st.muted:
                log.info("restore volume: stream #%s (%s) stays muted", st.index, st.display_name)
            if st.volume_min_pct < 100:
                self._fade_mixer(guard, st, now)
        if self.guard is None:
            guard.run_fades()

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

    # -- full volume reset ----------------------------------------------------
    def _compensation_owner(self, n, streams_by_key, sink_ids, profile_keys, standard):
        """
        The Cinegain setting whose gain can offset what node n gets back, or None (#29b).
        Only if the node's audio really passes that filter: never in soft off (bypass
        ignores the gain), a program's streams must all sit on the program's own sink,
        and Standard's output needs the load/save callables.
        """
        if self.bypass:
            return None
        if n.is_cinegain_output:
            profile = n.node_name[len("cinegain_output"):].lstrip("_") or PROFILE_STANDARD
            if profile == PROFILE_STANDARD:
                return PROFILE_STANDARD if standard is not None else None
            return profile_keys.get(profile)
        entry = self.registry.get(n.key)
        if not entry or entry["params"] is None or not entry["enabled"]:
            return None
        own = sink_name(profile_id_for(n.key))
        sts = streams_by_key.get(n.key) or []
        if sts and all(sink_ids.get(st.sink, st.sink) == own for st in sts):
            return n.key
        return None

    def full_volume_reset(self, standard=None, clean_file=True, only=None):
        """
        "Full volume reset": every secretly lowered playback stream back to 0 dB,
        normal stream volumes to 100 %, saved WirePlumber volumes cleaned. Nothing
        rises faster than 10 dB/s (#29): what Cinegain can offset comes back at once
        while the owning setting drops by the same amount (Q13a, min. -30 dB), and
        everything else fades in: the rest above that floor, programs that follow
        Standard, streams that do not pass their program's filter, the mixer
        volumes. Mute is left as it is (a mixer shows it). Hardware sinks stay
        untouched.
        `standard` = (load, save) callables for Standard's setting.
        `only` = one program key: reset just that program's streams (and its own
        Cinegain output), leave everything else and the saved file alone.
        Returns a report dict.
        """
        import cinegain_modes as modes
        from cinegain_volume import HIDDEN_EPS, VolumeGuard, from_db

        guard = self.guard or VolumeGuard()
        now = self.clock()
        nodes = guard.scanner()
        sink_ids = self.pulse.list_sinks()
        streams = [st for st in self.pulse.list_streams() if not st.is_own]
        streams_by_key = {}
        for st in streams:
            streams_by_key.setdefault(make_key(st.app_name or st.node_name, st.binary), []).append(st)
        profile_keys = {profile_id_for(k): k for k in self.registry.apps}
        report = {"compensated": [], "faded": [], "volumes_100": 0, "muted_left": 0,
                  "file_entries": 0, "backup": None}
        wanted = {}   # owner -> dB the most lowered of its nodes gets back
        owners = {}   # node id -> owner
        lowered = []
        only_profile = profile_id_for(only) if only else None
        only_sink = sink_name(only_profile) if only else None
        for n in nodes:
            if n.volume >= HIDDEN_EPS or (n.is_sink and not n.is_cinegain_sink):
                continue  # hardware sinks stay untouched
            if only and n.key != only and not (
                    n.is_cinegain_output and n.node_name.endswith(only_profile)) and n.node_name != only_sink:
                continue
            lowered.append(n)
            owner = self._compensation_owner(n, streams_by_key, sink_ids, profile_keys, standard)
            if owner is not None:
                owners[n.id] = owner
                wanted[owner] = max(wanted.get(owner, 0.0), -n.db)

        offset = {}   # owner -> dB actually taken off (limited by the -30 dB floor)
        for owner, db in wanted.items():
            if owner == PROFILE_STANDARD:
                state = standard[0]()
                mode, sets = state["mode"], modes.clean_sets(state["sets"])
            else:
                entry = self.registry.get(owner)
                mode, sets = entry["mode"], modes.clean_sets(entry["sets"])
            before = sets[mode]["input_db"]
            after = round(max(overdrive.MIN_DB, before - db), 1)  # slider resolution
            if after >= before:
                continue  # already at the floor: nothing to offset, everything fades
            sets[mode]["input_db"] = after
            if owner == PROFILE_STANDARD:
                standard[1](mode, sets)
            else:
                self.registry.set_setting(owner, mode, sets)
                self.push_program_params(owner)
            offset[owner] = before - after
            report["compensated"].append({"target": owner, "hidden_db": round(-db, 1),
                                          "input_db": [before, after]})
            log.info("full reset: %s input %.1f -> %.1f dB to offset %.1f dB coming back",
                     owner, before, after, db)

        for n in lowered:
            back = offset.get(owners.get(n.id), 0.0)
            start_db = min(0.0, n.db + back)
            if start_db > -0.05:
                guard.reset_now(n.id)  # fully offset by the lower Cinegain gain
                continue
            # The offset part comes back at once (the filter dropped by as much), the rest fades.
            guard.fade_hidden(n.id, from_db(start_db), 1.0, now, apply_start=back > 0)
            report["faded"].append({"target": n.key, "hidden_db": round(n.db, 1), "from_db": round(start_db, 1)})
            log.info("full reset: fading %s from %.1f dB (hidden %.1f dB, offset %.1f dB) over %.1f s",
                     n.key, start_db, n.db, back, guard.fades[n.id].duration if n.id in guard.fades else 0.0)

        for st in streams:
            if only and make_key(st.app_name or st.node_name, st.binary) != only:
                continue
            if st.muted:
                report["muted_left"] += 1  # mute stays: unmuting is a jump to full level (#29c)
            if st.volume_min_pct >= 100:
                continue
            self._fade_mixer(guard, st, now)
            report["volumes_100"] += 1

        if clean_file and not only:
            existing = set(sink_ids.values())
            if self.guard is not None and self.saved_reset_in_background:
                # GUI / watch: WirePlumber's reload waits ~1.5 s, keep the UI and the fades going
                threading.Thread(target=self._reset_saved, args=(existing,), name="wp-reset", daemon=True).start()
                report["wireplumber"] = "reloading"
            else:
                report.update(self._reset_saved(existing))
        if self.guard is None:
            guard.run_fades()  # one-shot use (CLI): run the fades here
        log.warning("full volume reset done: %s", report)
        return report

    @staticmethod
    def _reset_saved(existing_sinks):
        import cinegain_volume
        try:
            result = cinegain_volume.reset_saved_volumes(existing_sinks)
        except Exception:
            log.exception("resetting the saved WirePlumber volumes failed")
            return {"file_entries": 0, "backup": None, "wireplumber": "failed"}
        log.info("full reset: saved volumes %s", result)
        return result

    def _fade_mixer(self, guard, st, now):
        """Fades a stream's mixer volume (all channels) to 100 % from its quietest channel."""
        from cinegain_volume import mixer_db, mixer_pct

        def apply(db, final, index=st.index, node_id=st.node_id):
            pct = 100.0 if final else mixer_pct(db)
            ok = self.pulse.set_volume_pct(index, pct)
            if ok and node_id is not None:
                guard.note_mixer(node_id, (pct / 100.0) ** 3)  # own change, not "external" (#44)
            return ok

        key = ("mixer", st.node_id if st.node_id is not None else f"#{st.index}")
        fade = guard.add_fade(key, mixer_db(st.volume_min_pct), 0.0, apply, now,
                              what=f"mixer of stream #{st.index}")
        log.info("fading mixer of stream #%s (%s) from %d %% to 100 %% over %.1f s",
                 st.index, st.display_name, st.volume_min_pct, fade.duration)
