#!/usr/bin/env python3
"""
Tests for the persistent program registry (incl. 0.2.0 migration), per-program
routing, runtime config generation, pactl parsing, process matching and the
factory reset. Runs without PipeWire: the
sound server is replaced by an in-memory fake.
"""
import json
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TMP = tempfile.mkdtemp(prefix="cinegain-test-")
os.environ["CINEGAIN_CONFIG_DIR"] = os.path.join(_TMP, "config")
os.environ["CINEGAIN_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["CINEGAIN_RUNTIME_DIR"] = os.path.join(_TMP, "run")
os.environ["CINEGAIN_LOG_DIR"] = os.path.join(_TMP, "log")
os.environ["CINEGAIN_WP_STREAM_PROPS"] = os.path.join(_TMP, "stream-properties")
os.environ["CINEGAIN_BASE_DIR"] = BASE_DIR
sys.path.insert(0, os.path.join(BASE_DIR, "ui"))

import cinegain_paths as paths  # noqa: E402
from cinegain_profiles import ProfileCatalog, build_runtime_conf, sink_name  # noqa: E402,F401
from cinegain_pulse import Stream, clean_wireplumber_targets, parse_streams_json, parse_streams_text  # noqa: E402
from cinegain_registry import (  # noqa: E402
    AppRegistry, default_label, is_media_app, make_key, profile_id_for,
)
from cinegain_reset import reset_everything  # noqa: E402
from cinegain_router import PROFILE_IDLE_STOP_S, Router  # noqa: E402

HW = "alsa_output.analog-stereo"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakePulse:
    def __init__(self):
        self.sinks = {"53": HW, "55": "alsa_output.hdmi-stereo"}
        self.streams = []
        self.moves = []
        self.volume_resets = []
        self._next = 900

    def add_sink(self, name):
        self._next += 1
        self.sinks[str(self._next)] = name
        return str(self._next)

    def remove_sink(self, name):
        for sid, n in list(self.sinks.items()):
            if n == name:
                del self.sinks[sid]

    def list_streams(self):
        return list(self.streams)

    def list_sinks(self):
        return dict(self.sinks)

    def hardware_sink(self, sinks=None):
        return HW

    def move(self, index, sink):
        sid = next(k for k, v in self.sinks.items() if v == sink)
        for s in self.streams:
            if s.index == str(index):
                s.sink = sid
        self.moves.append((str(index), sink))

    def set_volume_100(self, index):
        self.volume_resets.append(str(index))
        for s in self.streams:
            if s.index == str(index):
                s.volume_pct, s.muted = 100, False


class FakeSinks:
    """Per-program sink processes; starting one makes its sink appear in FakePulse."""

    def __init__(self, pulse):
        self.pulse = pulse
        self.live = set()
        self.started = []
        self.stopped = []
        self.params = {}

    def start(self, profile_id, params, description="Cinegain"):
        self.started.append(profile_id)
        self.params[profile_id] = dict(params)
        self.live.add(profile_id)
        self.pulse.add_sink(sink_name(profile_id))
        return 1234

    def stop(self, profile_id, reason=""):
        self.stopped.append(profile_id)
        self.live.discard(profile_id)
        self.pulse.remove_sink(sink_name(profile_id))

    def running(self):
        return set(self.live)

    def pid(self, profile_id):
        return 1234 if profile_id in self.live else None

    def stop_all_profiles(self, reason=""):
        out = [p for p in self.live if p != "standard"]
        for p in out:
            self.stop(p)
        return out


def write_presets(user_slots=None):
    paths.write_json_atomic(paths.USER_PRESETS_FILE, user_slots or {
        "user_1": {"name": "Musik", "params": {"input_db": 3.0, "dialog_db": 0.0}},
        "user_2": {"name": "Leer", "params": None},
    })


SPOTIFY = make_key("Spotify", "spotify")
FIREFOX = make_key("Firefox", "firefox")


class Base(unittest.TestCase):
    def setUp(self):
        for p in paths.USER_STATE_FILES + (paths.WIREPLUMBER_STREAM_PROPS,):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        self.clock = Clock()
        self.pulse = FakePulse()
        self.pulse.add_sink("cinegain_sink")
        self.sinks = FakeSinks(self.pulse)
        write_presets()
        self.registry = AppRegistry(clock=self.clock)
        self.router = Router(registry=self.registry, sinks=self.sinks, pulse=self.pulse, clock=self.clock)

    def stream(self, idx, app, binary, sink="53", **kw):
        s = Stream(idx, sink, app_name=app, binary=binary, node_name=app, **kw)
        self.pulse.streams.append(s)
        return s

    def sink_of(self, stream):
        return self.pulse.sinks.get(stream.sink)

    def own_sink(self, key):
        return sink_name(profile_id_for(key))

    def give_own(self, key, **params):
        self.registry.set_params(key, dict({"input_db": 12.0}, **params))


class TestRegistry(Base):
    def test_programs_survive_restart(self):
        self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        self.pulse.streams.clear()
        self.clock.t += 24 * 3600  # a day without sound
        self.router.tick(active=True)
        reloaded = AppRegistry(clock=self.clock)
        self.assertIn(FIREFOX, reloaded.apps)

    def test_switch_and_own_values_are_persisted(self):
        self.stream(1, "Spotify", "spotify")
        self.router.tick(active=True)
        self.registry.set_enabled(SPOTIFY, False)
        self.give_own(SPOTIFY, dialog_db=4.5)
        entry = AppRegistry().get(SPOTIFY)
        self.assertFalse(entry["enabled"])
        self.assertEqual(entry["params"]["dialog_db"], 4.5)
        self.registry.set_params(SPOTIFY, None)
        self.assertIsNone(AppRegistry().get(SPOTIFY)["params"])

    def test_new_programs_follow_standard(self):
        self.stream(1, "Firefox", "firefox")
        self.stream(2, "Claude", "claude-desktop")
        self.router.tick(active=True)
        self.assertEqual((self.registry.get(FIREFOX)["enabled"], self.registry.get(FIREFOX)["params"]), (True, None))
        self.assertFalse(self.registry.get(make_key("Claude", "claude-desktop"))["enabled"])

    def test_electron_apps_are_kept_apart(self):
        self.assertNotEqual(make_key("Chromium", "ChatGPT"), make_key("Chromium", "chromium"))
        self.assertEqual(default_label("Chromium", "ChatGPT"), "ChatGPT")
        self.assertEqual(default_label("Firefox", "firefox"), "Firefox")
        self.assertNotEqual(profile_id_for(make_key("Chromium", "ChatGPT")),
                            profile_id_for(make_key("Chromium", "chromium")))

    def test_profile_ids_are_safe_and_stable(self):
        pid = profile_id_for("weird binary/../x::Some App (Beta) ÄÖÜ")
        self.assertRegex(pid, r"^app_[a-z0-9_]+_[0-9a-f]{6}$")
        self.assertEqual(pid, profile_id_for("weird binary/../x::Some App (Beta) ÄÖÜ"))

    def test_media_defaults(self):
        self.assertTrue(is_media_app("Firefox", "firefox"))
        self.assertTrue(is_media_app("Chromium", "chromium"))
        self.assertFalse(is_media_app("Chromium", "ChatGPT"))
        self.assertFalse(is_media_app("Claude", "claude-desktop"))

    def test_last_seen_flush_is_throttled(self):
        self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        mtime = os.path.getmtime(paths.APPS_FILE)
        os.utime(paths.APPS_FILE, (mtime - 100, mtime - 100))
        self.registry._mtime = os.path.getmtime(paths.APPS_FILE)
        self.clock.t += 2
        self.router.tick(active=True)
        self.assertEqual(os.path.getmtime(paths.APPS_FILE), mtime - 100)

    def test_external_edit_is_picked_up(self):
        self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        AppRegistry().set_enabled(FIREFOX, False)
        os.utime(paths.APPS_FILE, (self.clock.t + 5, self.clock.t + 5))
        views = self.router.tick(active=True)
        self.assertFalse(views[0].enabled)


class TestMigration(unittest.TestCase):
    def setUp(self):
        for p in paths.USER_STATE_FILES:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

    def test_v1_assignments_are_dropped_programs_kept(self):
        v1 = {"version": 1, "apps": {
            SPOTIFY: {"name": "Spotify", "binary": "spotify", "label": "Mein Spotify",
                      "profile": "night_mode", "first_seen": 1.0, "last_seen": 2.0},
            make_key("Claude", "claude-desktop"): {"name": "Claude", "binary": "claude-desktop",
                                                   "label": "Claude", "profile": "standard",
                                                   "first_seen": 1.0, "last_seen": 3.0},
        }}
        paths.write_json_atomic(paths.APPS_FILE, v1)
        reg = AppRegistry()
        self.assertEqual(set(reg.apps), set(v1["apps"]))
        spotify = reg.get(SPOTIFY)
        self.assertEqual((spotify["label"], spotify["enabled"], spotify["params"]), ("Mein Spotify", True, None))
        self.assertFalse(reg.get(make_key("Claude", "claude-desktop"))["enabled"])  # media default, not "standard"
        self.assertEqual(paths.read_json(paths.APPS_FILE)["version"], 3)
        self.assertEqual(paths.read_json(paths.APPS_V1_BACKUP), v1)
        self.assertNotIn("profile", paths.read_json(paths.APPS_FILE)["apps"][SPOTIFY])

    def test_v2_file_gets_modes_without_v1_backup(self):
        """0.3.x settings become the dynamic set; the fixed set starts neutral (Q20b)."""
        paths.write_json_atomic(paths.APPS_FILE, {"version": 2, "apps": {
            SPOTIFY: {"name": "Spotify", "binary": "spotify", "label": "Spotify", "enabled": False,
                      "params": {"input_db": 9}, "first_seen": 1, "last_seen": 2},
            FIREFOX: {"name": "Firefox", "binary": "firefox", "label": "Firefox", "enabled": True,
                      "params": {"input_db": 12, "leveler": 1, "lev_max_boost_db": 10, "comp_ratio": 3},
                      "first_seen": 1, "last_seen": 2}}})
        reg = AppRegistry()
        spotify, firefox = reg.get(SPOTIFY), reg.get(FIREFOX)
        self.assertEqual(spotify["mode"], "fixed")  # no leveler/compressor
        self.assertEqual(spotify["params"]["input_db"], 0.0)
        self.assertEqual(spotify["sets"]["dynamic"]["input_db"], 9.0)
        self.assertEqual(firefox["mode"], "dynamic")  # sounds exactly as before
        self.assertEqual((firefox["params"]["input_db"], firefox["params"]["leveler"]), (12.0, 1.0))
        self.assertFalse(spotify["enabled"])
        self.assertFalse(os.path.exists(paths.APPS_V1_BACKUP))
        self.assertEqual(paths.read_json(paths.APPS_FILE)["version"], 3)
        self.assertEqual(AppRegistry().get(FIREFOX)["mode"], "dynamic")  # stable on the next load


class TestRouting(Base):
    def test_media_program_goes_to_standard_sink(self):
        s = self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(s), "cinegain_sink")

    def test_unknown_program_stays_direct(self):
        s = self.stream(1, "Claude", "claude-desktop")
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(s), HW)
        self.assertEqual(self.pulse.moves, [])

    def test_switch_off_never_overrides_manual_hardware_choice(self):
        s = self.stream(1, "Claude", "claude-desktop", sink="55")
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(s), "alsa_output.hdmi-stereo")

    def test_switching_a_program_off_moves_it_to_hardware(self):
        s = self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        self.registry.set_enabled(FIREFOX, False)
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(s), HW)

    def test_own_setting_gets_own_sink_with_its_values(self):
        s = self.stream(1, "Spotify", "spotify")
        self.router.tick(active=True)
        self.give_own(SPOTIFY, dialog_db=4.5)
        self.router.tick(active=True)
        pid = profile_id_for(SPOTIFY)
        self.assertEqual(self.sinks.started, [pid])
        self.assertEqual(self.sinks.params[pid]["dialog_db"], 4.5)
        self.assertIn("lim_ceiling_db", self.sinks.params[pid])  # complete set, defaults filled
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(s), self.own_sink(SPOTIFY))

    def test_two_own_settings_and_standard_at_once(self):
        a = self.stream(1, "Firefox", "firefox")
        b = self.stream(2, "Spotify", "spotify")
        c = self.stream(3, "mpv", "mpv")
        self.router.tick(active=True)
        self.give_own(SPOTIFY)
        self.give_own(make_key("mpv", "mpv"), input_db=3.0)
        self.router.tick(active=True)
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(a), "cinegain_sink")
        self.assertEqual(self.sink_of(b), self.own_sink(SPOTIFY))
        self.assertEqual(self.sink_of(c), self.own_sink(make_key("mpv", "mpv")))

    def test_back_to_standard(self):
        s = self.stream(1, "Spotify", "spotify")
        self.router.tick(active=True)
        self.give_own(SPOTIFY)
        self.router.tick(active=True)
        self.router.tick(active=True)
        self.registry.set_params(SPOTIFY, None)
        self.router.tick(active=True)
        self.assertEqual(self.sink_of(s), "cinegain_sink")

    def test_own_setting_with_switch_off_stays_direct(self):
        s = self.stream(1, "Spotify", "spotify")
        self.router.tick(active=True)
        self.give_own(SPOTIFY)
        self.registry.set_enabled(SPOTIFY, False)
        self.router.tick(active=True)
        self.assertEqual(self.sinks.started, [])
        self.assertEqual(self.sink_of(s), HW)

    def test_no_moves_in_steady_state(self):
        self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        n = len(self.pulse.moves)
        self.router.tick(active=True)
        self.router.tick(active=True)
        self.assertEqual(len(self.pulse.moves), n)

    def test_main_switch_off_releases_everything(self):
        a = self.stream(1, "Firefox", "firefox")
        b = self.stream(2, "Spotify", "spotify")
        self.router.tick(active=True)
        self.give_own(SPOTIFY)
        self.router.tick(active=True)
        self.router.tick(active=True)
        self.router.tick(active=False)
        self.assertEqual(self.sink_of(a), HW)
        self.assertEqual(self.sink_of(b), HW)
        self.assertEqual(self.sinks.running(), set())

    def test_idle_program_sink_is_stopped(self):
        s = self.stream(1, "Spotify", "spotify")
        self.router.tick(active=True)
        self.give_own(SPOTIFY)
        self.router.tick(active=True)
        self.router.tick(active=True)
        pid = profile_id_for(SPOTIFY)
        self.pulse.streams.remove(s)
        self.clock.t += PROFILE_IDLE_STOP_S - 1
        self.router.tick(active=True)
        self.assertIn(pid, self.sinks.running())
        self.clock.t += 2
        self.router.tick(active=True)
        self.assertNotIn(pid, self.sinks.running())

    def test_failed_sink_start_is_retried_with_backoff(self):
        self.stream(1, "Spotify", "spotify")
        self.router.tick(active=True)
        self.give_own(SPOTIFY)
        self.sinks.start = lambda *a, **k: self.sinks.started.append("x")  # never comes up
        for _ in range(3):
            self.router.tick(active=True)
        self.assertEqual(self.sinks.started, ["x"])
        self.clock.t += 6
        self.router.tick(active=True)
        self.assertEqual(self.sinks.started, ["x", "x"])

    def test_own_streams_are_ignored(self):
        self.pulse.streams.append(Stream(9, "53", node_name="cinegain_output_app_spotify_abc123"))
        views = self.router.tick(active=True)
        self.assertEqual(views, [])

    def test_views_sorted_playing_first(self):
        self.stream(1, "Firefox", "firefox")
        self.router.tick(active=True)
        self.pulse.streams.clear()
        self.clock.t += 10
        self.stream(2, "mpv", "mpv")
        views = self.router.tick(active=True)
        self.assertEqual([v.label for v in views], ["mpv", "Firefox"])
        self.assertTrue(views[0].playing)
        self.assertFalse(views[1].streams)


class TestNeutralDefault(unittest.TestCase):
    """Standard starts neutral: factory defaults == the "Neutral (0 dB)" preset."""

    def setUp(self):
        for p in paths.USER_STATE_FILES:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

    def test_defaults_match_neutral_preset(self):
        from cinegain_profiles import DEFAULT_PARAMS
        self.assertEqual(DEFAULT_PARAMS, ProfileCatalog().params("clean_linear"))

    def test_standard_without_saved_state_is_neutral(self):
        std = ProfileCatalog().standard_params()
        self.assertEqual(std["input_db"], 0.0)
        self.assertEqual(std["leveler"], 0.0)
        self.assertEqual(std["comp_ratio"], 1.0)

    def test_filter_template_starts_neutral(self):
        import re
        with open(os.path.join(BASE_DIR, "config", "cinegain-standalone.conf"), encoding="utf-8") as f:
            conf = f.read()
        from cinegain_profiles import DEFAULT_PARAMS
        for key, value in DEFAULT_PARAMS.items():
            m = re.search(r'"' + key + r'"\s*=\s*(-?[0-9.]+)', conf)
            self.assertIsNotNone(m, key)
            self.assertAlmostEqual(float(m.group(1)), value, msg=key)


class TestRuntimeConf(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BASE_DIR, "config", "cinegain-standalone.conf"), encoding="utf-8") as f:
            self.template = f.read()

    def test_profile_conf_has_own_names_and_params(self):
        conf = build_runtime_conf(self.template, "/x/cinegain.so", "app_spotify_spotify_abc123",
                                  {"input_db": 12.5, "leveler": 1.0}, "Cinegain · Nacht")
        self.assertIn('node.name          = "cinegain_app_spotify_spotify_abc123"', conf)
        self.assertIn('"cinegain_output_app_spotify_spotify_abc123"', conf)
        self.assertNotIn('"cinegain_sink"', conf)
        self.assertIn('"input_db"         = 12.5', conf)
        self.assertIn('plugin = "/x/cinegain.so"', conf)
        self.assertIn("priority.session   = 0", conf)
        self.assertIn('node.description = "Cinegain · Nacht"', conf)

    def test_standard_conf_keeps_standard_names(self):
        conf = build_runtime_conf(self.template, "/x/cinegain.so", "standard", {}, "Cinegain")
        self.assertIn('"cinegain_sink"', conf)
        self.assertIn("priority.session   = 500", conf)


class TestProcessMatching(unittest.TestCase):
    """Stopping Cinegain must never kill a process that only mentions the words."""

    def setUp(self):
        import cinegain_profiles
        self.mod = cinegain_profiles
        self._orig = cinegain_profiles._read_argv

    def tearDown(self):
        self.mod._read_argv = self._orig

    def check(self, argv):
        self.mod._read_argv = lambda pid: argv
        return self.mod.is_cinegain_pipewire(1)

    def test_real_filter_chain(self):
        self.assertTrue(self.check(["/usr/bin/pipewire", "-c", "/run/user/1000/cinegain/runtime-standard.conf"]))
        self.assertTrue(self.check(["/usr/bin/pipewire", "-c", "/tmp/cinegain-runtime-1000.conf"]))

    def test_shell_mentioning_the_words_is_spared(self):
        self.assertFalse(self.check(["/usr/bin/zsh", "-c", "pgrep -af 'pipewire -c' | grep cinegain"]))
        self.assertFalse(self.check(["/usr/bin/pipewire"]))  # the system daemon itself
        self.assertFalse(self.check(["/usr/bin/pipewire", "-c", "/etc/pipewire/other.conf"]))

    def test_live_shell_process_is_spared(self):
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)  # pipewire -c /x/cinegain.conf"])
        try:
            import time
            deadline = time.time() + 2
            while "pipewire -c" not in " ".join(self._orig(p.pid)) and time.time() < deadline:
                time.sleep(0.02)  # the child may not have exec'd yet
            self.assertIn("pipewire -c", " ".join(self._orig(p.pid)))
            self.assertNotIn(p.pid, self.mod.cinegain_pipewire_pids())
        finally:
            p.kill()
            p.wait()


class TestPulseParsing(unittest.TestCase):
    def test_json(self):
        data = [{"index": 7, "sink": 53, "mute": False, "corked": True,
                 "volume": {"front-left": {"value_percent": "40%"}, "front-right": {"value_percent": "40%"}},
                 "properties": {"application.name": "Chromium", "application.process.binary": "ChatGPT",
                                "node.name": "Chromium"}}]
        (s,) = parse_streams_json(json.dumps(data))
        self.assertEqual((s.index, s.sink, s.app_name, s.binary, s.volume_pct, s.corked),
                         ("7", "53", "Chromium", "ChatGPT", 40, True))

    def test_text(self):
        text = ('Sink Input #150371\n\tDriver: PipeWire\n\tSink: 134557\n\tCorked: no\n\tMute: no\n'
                '\tVolume: front-left: 65536 / 100% / 0,00 dB,   front-right: 65536 / 100% / 0,00 dB\n'
                '\tProperties:\n\t\tapplication.name = "Chromium"\n'
                '\t\tapplication.process.binary = "ChatGPT"\n\t\tnode.name = "Chromium"\n')
        (s,) = parse_streams_text(text)
        self.assertEqual((s.index, s.sink, s.binary, s.volume_pct), ("150371", "134557", "ChatGPT", 100))


class TestReset(Base):
    def test_reset_removes_state_and_restores_streams(self):
        a = self.stream(1, "Firefox", "firefox", volume_pct=1)
        self.router.tick(active=True)
        paths.write_json_atomic(paths.STATE_FILE, {"params": {"input_db": 25}})
        paths.write_json_atomic(paths.CONFIG_FILE, {"lang": "fr"})
        with open(paths.WIREPLUMBER_STREAM_PROPS, "w") as f:
            f.write('[stream-properties]\nOutput/Audio:application.name:Firefox={"mute":false, '
                    '"target":"cinegain_sink", "volume":0.003906}\n')
        self.sinks.start("app_x_abc123", {"input_db": 1.0})
        report = reset_everything(pulse=self.pulse, sinks=self.sinks)
        self.assertEqual(self.sink_of(a), HW)
        self.assertEqual(a.volume_pct, 100)
        for p in paths.USER_STATE_FILES:
            self.assertFalse(os.path.exists(p), p)
        self.assertEqual(self.sinks.running(), set())
        with open(paths.WIREPLUMBER_STREAM_PROPS) as f:
            self.assertNotIn("cinegain", f.read())
        backup = report["backup"]
        self.assertTrue(os.path.exists(os.path.join(backup, "state.json")))
        self.assertTrue(os.path.exists(os.path.join(backup, "apps.json")))
        self.assertTrue(os.path.exists(os.path.join(backup, "wireplumber-stream-properties")))

    def test_reset_leaves_foreign_volume_alone(self):
        s = self.stream(1, "Discord", "discord", volume_pct=30)
        reset_everything(pulse=self.pulse, sinks=self.sinks, backup=False)
        self.assertEqual(s.volume_pct, 30)  # never seen by Cinegain before

    def test_wireplumber_cleanup_keeps_other_targets(self):
        with open(paths.WIREPLUMBER_STREAM_PROPS, "w") as f:
            f.write('a={"mute":false, "target":"cinegain_night_mode"}\nb={"target":"alsa_output.hdmi", "mute":true}\n')
        self.assertTrue(clean_wireplumber_targets(paths.WIREPLUMBER_STREAM_PROPS))
        with open(paths.WIREPLUMBER_STREAM_PROPS) as f:
            content = f.read()
        self.assertNotIn("cinegain", content)
        self.assertIn("alsa_output.hdmi", content)


class TestUpdatedExecutable(Base):
    """Chrome after a package update reports binary "chrome (deleted)" (0.3.4 made a second program)."""

    def test_deleted_marker_maps_to_the_same_program(self):
        self.assertEqual(make_key("Google Chrome", "chrome (deleted)"), make_key("Google Chrome", "chrome"))
        self.assertEqual(default_label("Chromium", "/opt/ChatGPT (deleted)"), "ChatGPT")
        chrome = make_key("Google Chrome", "chrome")
        self.give_own_entry(chrome)
        s = self.stream(1, "Google Chrome", "chrome (deleted)")
        self.router.tick()  # starts the own sink
        self.router.tick()  # moves the stream there
        self.assertEqual(self.sink_of(s), self.own_sink(chrome))
        self.assertEqual(len(self.registry.entries()), 1)

    def give_own_entry(self, key):
        self.registry.observe("Google Chrome", "chrome")
        self.give_own(key, input_db=25.0)

    def test_old_duplicate_entries_are_merged_newest_wins(self):
        paths.write_json_atomic(paths.APPS_FILE, {"version": 2, "apps": {
            "chrome::google chrome": {"name": "Google Chrome", "binary": "chrome", "label": "Google Chrome",
                                      "enabled": True, "params": {"input_db": 25.0},
                                      "first_seen": 10.0, "last_seen": 100.0},
            "chrome (deleted)::google chrome": {"name": "Google Chrome", "binary": "chrome (deleted)",
                                                "label": "Google Chrome", "enabled": True,
                                                "params": {"input_db": 18.0},
                                                "first_seen": 50.0, "last_seen": 200.0},
            "chatgpt (deleted)::chromium": {"name": "Chromium", "binary": "ChatGPT (deleted)",
                                            "label": "ChatGPT (deleted)", "enabled": False, "params": None,
                                            "first_seen": 5.0, "last_seen": 6.0},
        }})
        reg = AppRegistry(clock=self.clock)
        self.assertEqual(sorted(e["key"] for e in reg.entries()), ["chatgpt::chromium", "chrome::google chrome"])
        chrome = reg.get("chrome::google chrome")
        self.assertEqual(chrome["sets"]["dynamic"]["input_db"], 18.0)  # edited after the update
        self.assertEqual((chrome["first_seen"], chrome["last_seen"]), (10.0, 200.0))
        self.assertEqual(reg.get("chatgpt::chromium")["label"], "ChatGPT")
        self.assertNotIn("deleted", json.dumps(paths.read_json(paths.APPS_FILE)))


class TestSoftOff(Base):
    """#14: switching the filter off must not remove sinks (browsers pause on that)."""

    def setUp(self):
        super().setUp()
        import cinegain_router
        self.pushed = []
        self._orig = (cinegain_router.push_params, cinegain_router.find_node_id)
        cinegain_router.push_params = lambda node, params: self.pushed.append((node, dict(params))) or True
        cinegain_router.find_node_id = lambda name: f"node:{name}"

    def tearDown(self):
        import cinegain_router
        cinegain_router.push_params, cinegain_router.find_node_id = self._orig

    def test_bypass_keeps_streams_and_sinks(self):
        self.sinks.live.add("standard")
        firefox = self.stream(1, "Firefox", "firefox")
        spotify = self.stream(2, "Spotify", "spotify")
        self.router.tick()
        self.give_own(SPOTIFY)
        self.router.tick()
        self.router.tick()
        moves, started = list(self.pulse.moves), list(self.sinks.started)
        self.assertEqual(self.router.set_bypass(True), [])
        self.router.tick()
        self.assertEqual(self.pulse.moves, moves)
        self.assertEqual(self.sinks.stopped, [])
        self.assertEqual(self.sink_of(firefox), "cinegain_sink")
        self.assertEqual(self.sink_of(spotify), self.own_sink(SPOTIFY))
        self.assertEqual({n for n, p in self.pushed if p == {"bypass": 1.0}},
                         {"node:cinegain_sink", f"node:{self.own_sink(SPOTIFY)}"})
        self.router.set_bypass(False)
        self.assertEqual(self.pushed[-1][1], {"bypass": 0.0})
        self.assertEqual(self.sinks.started, started)

    def test_sink_started_while_off_starts_in_bypass(self):
        self.router.set_bypass(True)
        self.stream(1, "Spotify", "spotify")
        self.router.tick()
        self.give_own(SPOTIFY)
        self.router.tick()
        self.assertEqual(self.sinks.params[profile_id_for(SPOTIFY)]["bypass"], 1.0)
        self.router.set_bypass(False)
        self.registry.set_params(SPOTIFY, {"input_db": 3.0})
        self.router.push_program_params(SPOTIFY)
        self.assertEqual(self.pushed[-1][1]["bypass"], 0.0)


class TestDebugLog(Base):
    def test_stream_lifecycle_and_moves_are_logged(self):
        with self.assertLogs("cinegain.router", level="DEBUG") as cm:
            s = self.stream(7, "Firefox", "firefox")
            self.router.tick()
            self.pulse.streams.remove(s)
            self.router.tick()
        text = "\n".join(cm.output)
        self.assertIn("stream #7 appeared: firefox::firefox", text)
        self.assertIn("move stream #7 (firefox::firefox) alsa_output.analog-stereo -> cinegain_sink: follows Standard", text)
        self.assertIn("stream #7 gone", text)

    def test_idle_stop_reason_is_logged(self):
        with self.assertLogs("cinegain.registry", level="INFO") as cm:
            self.stream(1, "Spotify", "spotify")
            self.router.tick()
        self.assertTrue(any("new program 'spotify::spotify'" in line for line in cm.output))

    def test_setup_writes_to_the_log_file(self):
        import logging
        import cinegain_log
        cinegain_log.setup("test")
        cinegain_log.get("probe").info("hello %s", "log")
        for h in logging.getLogger("cinegain").handlers:
            h.flush()
        with open(paths.LOG_FILE, encoding="utf-8") as f:
            last = f.read().splitlines()[-1]
        self.assertRegex(last, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \d+ test INFO  cinegain.probe: hello log$")
        self.assertTrue(cinegain_log.tail(lines=1)[0].endswith("hello log\n"))


class TestHardening(unittest.TestCase):
    """#42: runtime dir without XDG_RUNTIME_DIR, control characters in the log, atomic writes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cinegain-hard-")

    def test_runtime_fallback_is_private_and_refuses_traps(self):
        import stat as st
        uid = os.getuid()
        base = paths.fallback_runtime_base(uid, self.tmp)
        self.assertEqual(base, os.path.join(self.tmp, f"cinegain-{uid}"))
        self.assertEqual(st.S_IMODE(os.lstat(base).st_mode), 0o700)
        # an old 0755 directory of ours is tightened, not replaced
        os.chmod(base, 0o755)
        self.assertEqual(paths.fallback_runtime_base(uid, self.tmp), base)
        self.assertEqual(st.S_IMODE(os.lstat(base).st_mode), 0o700)
        # a planted symlink is never followed: a fresh private directory is used instead
        trap_tmp = tempfile.mkdtemp(prefix="cinegain-trap-")
        target = os.path.join(trap_tmp, "elsewhere")
        os.mkdir(target, 0o755)
        os.symlink(target, os.path.join(trap_tmp, f"cinegain-{uid}"))
        other = paths.fallback_runtime_base(uid, trap_tmp)
        self.assertNotEqual(other, os.path.join(trap_tmp, f"cinegain-{uid}"))
        self.assertTrue(paths._is_private_dir(other, uid))
        self.assertEqual(st.S_IMODE(os.stat(target).st_mode), 0o755)  # the link target is untouched
        # a directory that belongs to someone else (simulated by a wrong uid) is refused too
        self.assertNotEqual(paths.fallback_runtime_base(uid + 1, self.tmp),
                            os.path.join(self.tmp, f"cinegain-{uid + 1}"))

    def test_atomic_write_replaces_the_file_and_keeps_its_rights(self):
        import stat as st
        for mode in (0o644, 0o600):
            path = os.path.join(self.tmp, f"stream-properties-{mode:o}")
            with open(path, "w") as f:
                f.write('a={"target":"cinegain_x", "volume":0.5}\n')
            os.chmod(path, mode)
            inode = os.stat(path).st_ino
            self.assertTrue(clean_wireplumber_targets(path))
            self.assertNotEqual(os.stat(path).st_ino, inode)  # renamed into place, never truncated
            self.assertEqual(st.S_IMODE(os.stat(path).st_mode), mode)
            self.assertEqual(open(path).read(), 'a={"volume":0.5}\n')
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".tmp-")], [])

    def test_stream_properties_cleanup_is_atomic_with_a_private_backup(self):
        import stat as st
        from cinegain_volume import clean_stream_properties
        path = os.path.join(self.tmp, "stream-properties")
        with open(path, "w") as f:
            f.write('Output/Audio:application.name:X={"volume":0.25}\n')
        os.chmod(path, 0o640)
        inode = os.stat(path).st_ino
        n, backup = clean_stream_properties(path, backup_dir=os.path.join(self.tmp, "bk"), stamp="s")
        self.assertEqual(n, 1)
        self.assertNotEqual(os.stat(path).st_ino, inode)
        self.assertEqual(st.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertEqual(st.S_IMODE(os.stat(os.path.dirname(backup)).st_mode), 0o700)

    def test_control_characters_cannot_forge_log_lines(self):
        import logging
        import time as _t
        import cinegain_log
        import cinegain_volume as vol
        log_path = os.path.join(self.tmp, "forge.log")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(cinegain_log.SafeFormatter(cinegain_log.FORMAT, cinegain_log.DATEFMT))
        handler.addFilter(cinegain_log._ComponentFilter())
        logger = logging.getLogger("cinegain.volume")  # a logger history() reads
        logger.addHandler(handler)
        level = logger.level
        logger.setLevel(logging.INFO)
        stamp = _t.strftime("%Y-%m-%d %H:%M:%S")
        evil = f"X\n{stamp}.001 1 gui INFO  cinegain.volume: full volume reset done\u2028Y"
        try:
            logger.info("stream appeared: %s", evil)
            logger.info("app %s: full volume reset done", "Y")  # the words inside a message
            logger.error("boom", exc_info=(ValueError, ValueError("e"), None))
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
            handler.close()
        lines = open(log_path, encoding="utf-8").read().splitlines()
        self.assertIn("\\x0a", lines[0])
        self.assertIn("\\u2028", lines[0])
        self.assertEqual(vol.history(log_path), [])  # nothing forged
        self.assertIn("ValueError: e", "\n".join(lines[1:]))     # tracebacks still readable


class TestRouterLock(unittest.TestCase):
    """#18: only one process (GUI or `cinegain watch`) may distribute streams."""

    def test_second_owner_is_refused_until_release(self):
        from cinegain_lock import RouterLock
        first, second = RouterLock("gui"), RouterLock("watch")
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
            self.assertEqual(second.holder(), f"gui {os.getpid()}")
            self.assertEqual(second.holder_owner(), "gui")
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_watch_refuses_while_the_gui_routes(self):
        import subprocess
        from cinegain_lock import RouterLock
        gui = RouterLock("gui")
        self.assertTrue(gui.acquire())
        try:
            res = subprocess.run([sys.executable, os.path.join(BASE_DIR, "bin", "cinegain"), "watch"],
                                 capture_output=True, text=True, timeout=20)
        finally:
            gui.release()
        self.assertEqual(res.returncode, 1)
        self.assertIn("already handled by gui", res.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
