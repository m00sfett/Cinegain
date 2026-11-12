#!/usr/bin/env python3
"""
0.4.0: fixed / dynamic (beta) mode per target and the hidden-volume guard.
Offline: PipeWire, pactl and the CLI's node lookup are replaced by fakes, so
nothing here touches the running sound server.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_routing as tr  # noqa: E402  (isolated config dirs, sys.path, fakes)

import cinegain_modes as modes  # noqa: E402
import cinegain_paths as paths  # noqa: E402
from cinegain_registry import profile_id_for  # noqa: E402
from cinegain_volume import FADE_S, NodeVolume, VolumeGuard, clean_stream_properties, parse_nodes  # noqa: E402

CHROME = tr.make_key("Google Chrome", "chrome")


class TestModes(unittest.TestCase):
    def test_fixed_is_static_gain_tone_and_limiter_only(self):
        p = modes.fixed_effective({"input_db": 12, "dialog_db": 3, "bass_db": -2, "treble_db": 1, "side_db": -3})
        self.assertEqual((p["input_db"], p["dialog_db"], p["bass_db"], p["treble_db"], p["side_db"]),
                         (12.0, 3.0, -2.0, 1.0, -3.0))
        self.assertEqual((p["leveler"], p["lev_max_boost_db"], p["comp_ratio"], p["makeup_db"], p["mid_db"]),
                         (0.0, 0.0, 1.0, 0.0, 0.0))
        self.assertEqual(p["lim_ceiling_db"], modes.FIXED_CEILING_DB)

    def test_dynamic_leveler_follows_boost(self):
        p = modes.dynamic_effective({"lev_max_boost_db": 10})
        self.assertEqual((p["leveler"], p["lev_max_cut_db"]), (1.0, modes.LEVELER_DEFAULT_CUT_DB))
        self.assertEqual(modes.dynamic_effective({"lev_max_boost_db": 0, "leveler": 1})["leveler"], 0.0)

    def test_each_mode_keeps_its_own_values(self):
        sets = modes.apply_values("fixed", None, {"input_db": 10, "comp_ratio": 5})
        sets = modes.apply_values("dynamic", sets, {"input_db": 4})
        self.assertEqual(sets["fixed"]["input_db"], 10.0)
        self.assertNotIn("comp_ratio", sets["fixed"])
        self.assertEqual(sets["dynamic"]["input_db"], 4.0)
        self.assertEqual(modes.effective("fixed", sets)["input_db"], 10.0)
        self.assertEqual(modes.effective("dynamic", sets)["input_db"], 4.0)

    def test_presets_know_their_mode(self):
        self.assertEqual(modes.preset_mode("boost_15db"), "fixed")
        self.assertEqual(modes.preset_mode("night_mode"), "dynamic")

    def test_standard_from_0_3_starts_fixed_and_neutral(self):
        paths.write_json_atomic(paths.STATE_FILE, {"preset": "dialog_boost",
                                                   "params": {"input_db": 8, "leveler": 1, "comp_ratio": 3.5}})
        st = modes.load_standard()
        self.assertTrue(st["migrated"])
        self.assertEqual((st["mode"], st["params"]["input_db"], st["params"]["comp_ratio"]), ("fixed", 0.0, 1.0))
        self.assertEqual(st["sets"]["dynamic"]["input_db"], 8.0)  # nothing lost
        modes.save_standard(st["mode"], st["sets"])
        self.assertFalse(modes.load_standard()["migrated"])
        self.assertEqual(paths.read_json(paths.STATE_FILE)["mode"], "fixed")


class FakeVolumes:
    """pw-dump/pw-cli stand-in: nodes with a hidden volume; setter records and applies."""

    def __init__(self, nodes):
        self.nodes = {n.id: n for n in nodes}
        self.sets = []

    def scan(self):
        return [NodeVolume(n.id, n.app_name, n.binary, n.node_name, n.volume) for n in self.nodes.values()]

    def set(self, node_id, value):
        self.sets.append((node_id, round(value, 6)))
        self.nodes[node_id].volume = value
        return True


class TestVolumeGuard(unittest.TestCase):
    def test_parse_pw_dump(self):
        dump = [{"id": 96, "type": "PipeWire:Interface:Node",
                 "info": {"props": {"media.class": "Stream/Output/Audio", "application.name": "Google Chrome",
                                    "application.process.binary": "chrome (deleted)", "node.name": "Google Chrome"},
                          "params": {"Props": [{"volume": 0.003906, "channelVolumes": [1.0, 1.0]}]}}},
                {"id": 53, "type": "PipeWire:Interface:Node",
                 "info": {"props": {"media.class": "Audio/Sink"}, "params": {"Props": [{"volume": 0.5}]}}}]
        nodes = parse_nodes(dump)
        self.assertEqual([(n.id, n.key, round(n.db)) for n in nodes], [(96, CHROME, -48)])

    def test_routed_programs_fade_others_are_reported(self):
        fake = FakeVolumes([NodeVolume(1, "Google Chrome", "chrome", "Google Chrome", 0.0039),
                            NodeVolume(2, "Chromium", "ChatGPT", "Chromium", 0.00006),
                            NodeVolume(3, "", "", "cinegain_output_app_x", 0.0625)])
        clock = tr.Clock(100.0)
        guard = VolumeGuard(scanner=fake.scan, setter=fake.set, clock=clock)
        with self.assertLogs("cinegain.volume", level="WARNING") as cm:
            hidden = guard.update({CHROME})
        self.assertEqual(list(hidden), ["chatgpt::chromium"])
        self.assertLess(hidden["chatgpt::chromium"], -80)
        self.assertEqual(set(guard.fades), {1, 3})
        self.assertTrue(any("-48.2 dB" in line for line in cm.output))
        clock.t += FADE_S / 2
        guard.step()
        self.assertAlmostEqual(fake.nodes[1].volume, 10 ** (-48.18 / 2 / 20), places=3)  # halfway in dB
        clock.t += FADE_S
        guard.step()
        self.assertEqual((fake.nodes[1].volume, fake.nodes[3].volume), (1.0, 1.0))
        self.assertEqual(guard.fades, {})
        self.assertEqual(fake.nodes[2].volume, 0.00006)  # foreign program untouched

    def test_clean_stream_properties(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stream-properties")
            with open(path, "w") as f:
                f.write("[stream-properties]\n"
                        'Output/Audio:application.name:Google\\sChrome={"mute":true, "volume":0.003906, '
                        '"channelVolumes":[0.5, 0.25]}\n'
                        'Input/Audio:application.name:Mic={"volume":0.4}\n')
            n, backup = clean_stream_properties(path, backup_dir=os.path.join(d, "bk"), stamp="x")
            text = open(path).read()
            self.assertEqual(n, 1)
            self.assertIn('"volume":1.000000', text)
            self.assertIn('"channelVolumes":[1.000000, 1.000000]', text)
            self.assertIn('"mute":false', text)
            self.assertIn('Mic={"volume":0.4}', text)  # inputs untouched
            self.assertTrue(os.path.exists(backup))


class TestRouterVolume(tr.Base):
    def setUp(self):
        super().setUp()
        import cinegain_router
        self.pushed = []
        self._orig = (cinegain_router.push_params, cinegain_router.find_node_id)
        cinegain_router.push_params = lambda node, params: self.pushed.append(dict(params)) or True
        cinegain_router.find_node_id = lambda name: 7
        self.sinks.live.add("standard")
        self.volumes = FakeVolumes([])
        self.router.guard = VolumeGuard(scanner=self.volumes.scan, setter=self.volumes.set, clock=self.clock)
        self.router.full_volume_reset.__func__  # exists
        import cinegain_volume
        self._clean = cinegain_volume.clean_stream_properties
        cinegain_volume.clean_stream_properties = lambda: (0, None)

    def tearDown(self):
        import cinegain_router
        import cinegain_volume
        cinegain_router.push_params, cinegain_router.find_node_id = self._orig
        cinegain_volume.clean_stream_properties = self._clean

    def test_views_report_hidden_volume_of_unrouted_programs(self):
        self.stream(1, "Chromium", "ChatGPT")  # not a media app: not routed
        self.volumes.nodes[11] = NodeVolume(11, "Chromium", "ChatGPT", "Chromium", 0.001)
        views = {v.key: v for v in self.router.tick()}
        self.assertAlmostEqual(views["chatgpt::chromium"].hidden_db, -60.0, places=1)

    def test_full_reset_compensates_own_setting_and_fades_followers(self):
        firefox = tr.FIREFOX
        self.stream(1, "Firefox", "firefox")
        self.stream(2, "Spotify", "spotify")
        self.router.tick()
        self.registry.set_setting(firefox, "fixed", {"fixed": {"input_db": 15.0}})
        self.router.tick()
        self.router.tick()
        self.volumes.nodes[21] = NodeVolume(21, "Firefox", "firefox", "Firefox", 0.0625)       # -24 dB, own setting
        self.volumes.nodes[22] = NodeVolume(22, "Spotify", "spotify", "Spotify", 0.25)        # -12 dB, follows Standard
        self.volumes.nodes[23] = NodeVolume(23, "", "", "cinegain_output", 0.5)               # Standard's output, -6 dB
        saved = {}
        std = {"mode": "fixed", "sets": modes.apply_values("fixed", None, {"input_db": 3.0})}
        report = self.router.full_volume_reset(
            standard=(lambda: std, lambda mode, sets: saved.update(mode=mode, sets=sets)))
        ff = self.registry.get(firefox)
        self.assertEqual(ff["sets"]["fixed"]["input_db"], -9.1)  # 15 - 24.1, offsets what comes back
        self.assertAlmostEqual(saved["sets"]["fixed"]["input_db"], -3.0, places=1)  # 3 - 6
        self.assertEqual((self.volumes.nodes[21].volume, self.volumes.nodes[23].volume), (1.0, 1.0))
        self.assertLess(self.volumes.nodes[22].volume, 1.0)  # follower: fades in, no jump
        for _ in range(int(FADE_S * 10) + 5):
            self.clock.t += 0.1
            self.router.guard.step()
        self.assertEqual(self.volumes.nodes[22].volume, 1.0)
        self.assertEqual([f["target"] for f in report["faded"]], ["spotify::spotify"])
        self.assertEqual(len(report["compensated"]), 2)

    def test_reset_of_one_program_leaves_others(self):
        self.stream(1, "Chromium", "ChatGPT")
        self.stream(2, "Discord", "discord")
        self.volumes.nodes[31] = NodeVolume(31, "Chromium", "ChatGPT", "Chromium", 0.01)
        self.volumes.nodes[32] = NodeVolume(32, "Discord", "discord", "Discord", 0.01)
        self.router.tick()
        self.router.full_volume_reset(only="chatgpt::chromium")
        for _ in range(int(FADE_S * 10) + 5):
            self.clock.t += 0.1
            self.router.guard.step()
        self.assertEqual(self.volumes.nodes[31].volume, 1.0)
        self.assertEqual(self.volumes.nodes[32].volume, 0.01)


    def test_reset_of_standard_leaves_the_programs_alone(self):
        """#32: the key GUI passes for Standard resets only Standard's output, not "everything"."""
        self.stream(1, "Chromium", "ChatGPT")
        self.volumes.nodes[31] = NodeVolume(31, "Chromium", "ChatGPT", "Chromium", 0.01)
        self.volumes.nodes[33] = NodeVolume(33, "", "", "cinegain_output", 0.5)
        self.router.tick()
        std_key = self.volumes.nodes[33].key
        self.assertEqual(std_key, tr.make_key("cinegain_output", ""))
        report = self.router.full_volume_reset(
            standard=(lambda: {"mode": "fixed", "sets": modes.apply_values("fixed", None, {"input_db": 3.0})},
                      lambda mode, sets: None), only=std_key)
        for _ in range(int(FADE_S * 10) + 5):
            self.clock.t += 0.1
            self.router.guard.step()
        self.assertEqual(self.volumes.nodes[33].volume, 1.0)
        self.assertEqual(self.volumes.nodes[31].volume, 0.01)  # the program keeps its hidden volume
        self.assertEqual(report["file_entries"], 0)  # saved WirePlumber file not cleaned


class TestCliModes(unittest.TestCase):
    """CLI functions with the live node lookup replaced: nothing reaches PipeWire."""

    @classmethod
    def setUpClass(cls):
        cls.cli = SourceFileLoader("cinegain_cli_modes", os.path.join(tr.BASE_DIR, "bin", "cinegain")).load_module()
        cls.cli.get_cinegain_node_id = lambda: None

    def setUp(self):
        for p in paths.USER_STATE_FILES:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.cli.main(list(argv))
        return rc, out.getvalue()

    def test_preset_brings_its_mode_and_batch_fills_the_active_set(self):
        self.assertEqual(self.run_cli("preset", "night_mode")[0], 0)
        st = modes.load_standard()
        self.assertEqual((st["mode"], st["params"]["comp_ratio"]), ("dynamic", 6.0))
        self.assertEqual(self.run_cli("preset", "boost_15db")[0], 0)
        st = modes.load_standard()
        self.assertEqual((st["mode"], st["params"]["input_db"], st["params"]["comp_ratio"]), ("fixed", 15.0, 1.0))
        self.assertEqual(st["sets"]["dynamic"]["comp_ratio"], 6.0)  # dynamic values kept
        self.assertEqual(self.run_cli("batch", '{"bass_db": -4}')[0], 0)
        self.assertEqual(modes.load_standard()["sets"]["fixed"]["bass_db"], -4.0)
        rc, out = self.run_cli("mode", "dynamic")
        self.assertEqual((rc, out.strip()), (0, "Standard: dynamic"))
        self.assertEqual(modes.load_standard()["params"]["comp_ratio"], 6.0)


class TestFilterStateInCli(unittest.TestCase):
    """#33: set/batch/preset/start keep the filter in the state the user chose (on or off)."""

    @classmethod
    def setUpClass(cls):
        cls.cli = SourceFileLoader("cinegain_cli_filter", os.path.join(tr.BASE_DIR, "bin", "cinegain")).load_module()

    def setUp(self):
        import cinegain_bypass as bp
        for p in paths.USER_STATE_FILES:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
        self.bp = bp
        bp.set_on(False)
        self.addCleanup(bp.set_on, False)
        cli = self.cli
        self.pushed = []
        self.started = []
        self.node = [7]  # node id of the running filter, or None
        outer = self

        class FakeSinks:
            def start(self, profile, params, description=""):
                outer.started.append(dict(params))
                outer.node[0] = 7
                return 1

            def stop(self, *a, **kw):
                outer.node[0] = None

            def running(self):
                return set()

        class FakeRouter:
            bypass = False

            def release_all(self):
                pass

            def set_bypass(self, on):
                outer.pushed.append(("router.set_bypass", on))
                return []

        class FakePulse:
            def set_sink_volume_100(self, name):
                pass

        self.saved = {n: getattr(cli, n) for n in ("get_cinegain_node_id", "push_params", "wait_for_node",
                                                     "standard_sinks", "make_router", "PulseControl",
                                                     "_kill_legacy_processes", "clean_wireplumber_targets")}
        cli.get_cinegain_node_id = lambda: outer.node[0]
        cli.push_params = lambda node, params: outer.pushed.append(dict(params)) or True
        cli.wait_for_node = lambda name: outer.node[0]
        cli.standard_sinks = FakeSinks
        cli.make_router = FakeRouter
        cli.PulseControl = FakePulse
        cli._kill_legacy_processes = lambda: None
        cli.clean_wireplumber_targets = lambda path: False

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(self.cli, n, v)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def live_bypass(self):
        """bypass of the last parameter set pushed to the filter."""
        sets = [p for p in self.pushed if isinstance(p, dict)]
        return sets[-1]["bypass"]

    COMMANDS = (("set", "input_db", "6"), ("set", "bass_db", "-2"), ("batch", '{"input_db": 4}'),
                ("preset", "boost_6db"), ("preset", "night_mode"), ("start",))

    def test_commands_leave_a_switched_off_filter_off(self):
        self.bp.set_on(True)
        for argv in self.COMMANDS:
            with self.subTest(argv=argv):
                self.pushed.clear()
                self.assertEqual(self.run_cli(*argv)[0], 0)
                self.assertEqual(self.live_bypass(), 1.0)  # never a blip of processing
                self.assertTrue(self.bp.is_on())
                self.assertEqual({p["bypass"] for p in self.pushed if isinstance(p, dict)}, {1.0})

    def test_commands_leave_a_switched_on_filter_on(self):
        for argv in self.COMMANDS:
            with self.subTest(argv=argv):
                self.pushed.clear()
                self.assertEqual(self.run_cli(*argv)[0], 0)
                self.assertEqual(self.live_bypass(), 0.0)  # and never switch it off on their own
                self.assertFalse(self.bp.is_on())

    def test_a_fresh_start_creates_the_sink_in_the_remembered_state(self):
        for off in (True, False):
            with self.subTest(filter_off=off):
                self.bp.set_on(off)
                self.node[0] = None
                self.started.clear()
                self.assertEqual(self.run_cli("start")[0], 0)
                self.assertEqual(self.started[0]["bypass"], 1.0 if off else 0.0)

    def test_filter_command_switches_on_purpose_and_reports(self):
        rc, out, _ = self.run_cli("filter", "off")
        self.assertEqual((rc, self.bp.is_on()), (0, True))
        self.assertIn("OFF", out)
        self.assertIn(("router.set_bypass", True), self.pushed)
        self.assertIn("OFF", self.run_cli("filter")[1])
        self.assertTrue(json.loads(self.run_cli("status", "--json")[1])["bypass"])
        rc, out, _ = self.run_cli("filter", "on")
        self.assertEqual((rc, self.bp.is_on()), (0, False))
        self.assertIn(("router.set_bypass", False), self.pushed)

    def test_explicit_bypass_values_in_set_and_batch_still_switch(self):
        self.assertEqual(self.run_cli("set", "bypass", "1")[0], 0)
        self.assertTrue(self.bp.is_on())
        self.assertEqual(self.run_cli("batch", '{"bypass": 0, "bass_db": 1}')[0], 0)
        self.assertFalse(self.bp.is_on())
        self.assertEqual(modes.load_standard()["sets"]["fixed"]["bass_db"], 1.0)

    def test_filter_needs_a_running_filter_and_stop_clears_the_state(self):
        self.node[0] = None
        rc, _out, err = self.run_cli("filter", "off")
        self.assertEqual(rc, 1)
        self.assertIn("not active", err)
        self.assertFalse(self.bp.is_on())
        self.node[0] = 7
        self.bp.set_on(True)
        self.assertEqual(self.run_cli("stop")[0], 0)
        self.assertFalse(self.bp.is_on())  # the next start is a real "on"


class TestStartKeepsSinkVolume(unittest.TestCase):
    """#36: `cinegain start` sets the sink to 100 % only the first time it creates it."""

    @classmethod
    def setUpClass(cls):
        cls.cli = SourceFileLoader("cinegain_cli_start", os.path.join(tr.BASE_DIR, "bin", "cinegain")).load_module()

    def setUp(self):
        for p in paths.USER_STATE_FILES:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
        cli = self.cli
        self.volume_calls = []
        outer = self

        class FakePulse:
            def set_sink_volume_100(self, name):
                outer.volume_calls.append(name)

        class FakeSinks:
            def start(self, *a, **kw):
                return 1

        self.saved = (cli.get_cinegain_node_id, cli.wait_for_node, cli.PulseControl, cli.standard_sinks,
                      cli.set_params_batch)
        cli.get_cinegain_node_id = lambda: None  # sink does not exist yet: every start creates it
        cli.wait_for_node = lambda name: 7
        cli.PulseControl = FakePulse
        cli.standard_sinks = FakeSinks
        cli.set_params_batch = lambda *a, **kw: True

    def tearDown(self):
        cli = self.cli
        (cli.get_cinegain_node_id, cli.wait_for_node, cli.PulseControl, cli.standard_sinks,
         cli.set_params_batch) = self.saved

    def start(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.cli.main(["start"])

    def test_first_creation_sets_100_percent_later_starts_keep_the_users_volume(self):
        self.assertEqual(self.start(), 0)
        self.assertEqual(self.volume_calls, ["cinegain_sink"])  # first creation: set once
        self.assertEqual(self.start(), 0)
        self.assertEqual(self.start(), 0)
        self.assertEqual(self.volume_calls, ["cinegain_sink"])  # restarts leave it alone

    def test_a_reset_counts_as_a_new_first_creation(self):
        self.start()
        os.remove(paths.CONFIG_FILE)  # `cinegain reset` removes config.json
        self.start()
        self.assertEqual(self.volume_calls, ["cinegain_sink", "cinegain_sink"])


class TestWatchCapsOnStart(unittest.TestCase):
    """#30: `cinegain watch` caps saved values and pushes them even when the overdrive flag is already gone."""

    @classmethod
    def setUpClass(cls):
        cls.cli = SourceFileLoader("cinegain_cli_watch", os.path.join(tr.BASE_DIR, "bin", "cinegain")).load_module()

    def setUp(self):
        import cinegain_overdrive as od
        for p in paths.USER_STATE_FILES:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
        outer = self
        self.pushes = []

        class FakeRouter:
            def __init__(self):
                self.registry = tr.AppRegistry()
                self.guard = None

            def push_program_params(self, key):
                outer.pushes.append(("program", key))

            def tick(self, active=True):
                raise KeyboardInterrupt  # one pass is enough

        class FreeLock:  # the real flock would stay held by this process between the tests
            def __init__(self, owner):
                pass

            def acquire(self):
                return True

        self.saved = (self.cli.make_router, self.cli.get_cinegain_node_id, self.cli.set_params_batch,
                      self.cli.RouterLock)
        self.cli.RouterLock = FreeLock
        self.cli.make_router = FakeRouter
        self.cli.get_cinegain_node_id = lambda: 5
        self.cli.set_params_batch = lambda node, params, **kw: self.pushes.append(("standard", node)) or True
        od.set_on(True)
        reg = tr.AppRegistry()
        reg.observe("Firefox", "firefox")
        self.addCleanup(od.set_on, False)
        self.od = od
        self.reg = reg

    def tearDown(self):
        (self.cli.make_router, self.cli.get_cinegain_node_id, self.cli.set_params_batch,
         self.cli.RouterLock) = self.saved

    def watch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.cli.main(["watch"])

    def test_values_above_the_normal_scale_are_pushed_although_the_flag_was_already_cleared(self):
        self.reg.set_setting(tr.FIREFOX, "fixed", {"fixed": {"input_db": 28.0}})
        modes.save_standard("fixed", {"fixed": {"input_db": 22.0}})
        self.od.set_on(False)  # e.g. the GUI restarted before and already cleared the flag
        self.assertEqual(self.watch(), 0)
        self.assertEqual(sorted(self.pushes), [("program", tr.FIREFOX), ("standard", 5)])
        self.assertEqual(modes.load_standard()["params"]["input_db"], 15.0)

    def test_nothing_is_pushed_when_every_value_is_inside_the_scale(self):
        self.reg.set_setting(tr.FIREFOX, "fixed", {"fixed": {"input_db": 10.0}})
        modes.save_standard("fixed", {"fixed": {"input_db": 5.0}})
        self.od.set_on(False)
        self.assertEqual(self.watch(), 0)
        self.assertEqual(self.pushes, [])


class TestParameterRange(unittest.TestCase):
    """#37: values are finite and inside the plugin's port ranges before they are saved or pushed."""

    def setUp(self):
        for p in paths.USER_STATE_FILES:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
        self.cli = TestCliModes.cli

    @classmethod
    def setUpClass(cls):
        TestCliModes.setUpClass()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    @staticmethod
    def c_port_limits():
        """Control-port ranges parsed from port_names / port_hints in src/cinegain.c."""
        import re
        with open(os.path.join(tr.BASE_DIR, "src", "cinegain.c"), encoding="utf-8") as f:
            src = f.read()
        names = re.findall(r'"([^"]+)"', re.search(r"port_names\[P_COUNT\] = \{(.*?)\};", src, re.S).group(1))
        block = re.search(r"port_hints\[P_COUNT\] = \{(.*?)\n\};", src, re.S).group(1)
        entries = re.findall(r"\{\s*([A-Za-z_| 0-9]+?)\s*,\s*(-?[0-9.]+)f?\s*,\s*(-?[0-9.]+)f?\s*\}", block)
        assert len(entries) == len(names), (len(entries), len(names))
        return {n: (float(lo), float(hi)) for n, (_h, lo, hi) in zip(names, entries)
                if n.islower() and not n.startswith("meter_") and " " not in n}

    def test_python_limits_match_the_plugin_port_hints(self):
        from cinegain_profiles import DEFAULT_PARAMS, PARAM_LIMITS
        self.assertEqual(PARAM_LIMITS, self.c_port_limits())
        self.assertEqual(set(PARAM_LIMITS), set(DEFAULT_PARAMS))
        for key, value in DEFAULT_PARAMS.items():
            low, high = PARAM_LIMITS[key]
            self.assertTrue(low <= value <= high, key)

    def test_normalize_clamps_to_range_and_keeps_inside_values(self):
        from cinegain_profiles import normalize_params
        got = normalize_params({"lim_ceiling_db": 6, "input_db": 99, "makeup_db": -99, "comp_ratio": 0.2,
                                "bass_db": 4.5, "cinegain:dialog_hz": 2500})
        self.assertEqual(got, {"lim_ceiling_db": 0.0, "input_db": 40.0, "makeup_db": -24.0, "comp_ratio": 1.0,
                               "bass_db": 4.5, "dialog_hz": 2500.0})

    def test_normalize_drops_non_finite_numbers(self):
        from cinegain_profiles import normalize_params
        self.assertEqual(normalize_params({"bass_db": float("nan"), "treble_db": float("inf"),
                                           "mid_db": float("-inf"), "side_db": 2}), {"side_db": 2.0})
        sets = modes.apply_values("dynamic", None, {"bass_db": float("nan"), "lim_ceiling_db": 6})
        self.assertEqual((sets["dynamic"]["bass_db"], sets["dynamic"]["lim_ceiling_db"]), (0.0, 0.0))

    def test_cli_set_refuses_non_finite_values_and_saves_nothing(self):
        self.assertEqual(self.run_cli("set", "bass_db", "2")[0], 0)
        before = paths.read_json(paths.STATE_FILE)
        for bad in (["nan"], ["inf"], ["--", "-inf"]):
            rc, _out, err = self.run_cli("set", "bass_db", *bad)
            self.assertNotEqual(rc, 0, bad)
            self.assertIn("finite", err)
        self.assertEqual(paths.read_json(paths.STATE_FILE), before)

    def test_cli_batch_refuses_non_finite_values_and_saves_nothing(self):
        self.assertEqual(self.run_cli("batch", '{"bass_db": 2}')[0], 0)
        before = paths.read_json(paths.STATE_FILE)
        for bad in ('{"bass_db": NaN}', '{"bass_db": 1, "treble_db": Infinity}', '{"mid_db": -Infinity}'):
            rc, _out, err = self.run_cli("batch", bad)
            self.assertNotEqual(rc, 0, bad)
            self.assertIn("finite", err)
        self.assertEqual(paths.read_json(paths.STATE_FILE), before)

    def test_cli_set_clamps_the_ceiling_in_dynamic_mode(self):
        self.assertEqual(self.run_cli("mode", "dynamic")[0], 0)
        rc, out, _err = self.run_cli("set", "lim_ceiling_db", "6")
        self.assertEqual(rc, 0)
        self.assertIn("limited", out)
        self.assertEqual(modes.load_standard()["params"]["lim_ceiling_db"], 0.0)
        self.assertEqual(self.run_cli("set", "lim_ceiling_db", "-3")[0], 0)  # inside the range: untouched
        self.assertEqual(modes.load_standard()["params"]["lim_ceiling_db"], -3.0)


class TestUserSlots(unittest.TestCase):
    def test_six_slots_and_mode_inferred_for_old_slots(self):
        from cinegain_profiles import USER_SLOTS, ProfileCatalog
        paths.write_json_atomic(paths.USER_PRESETS_FILE, {
            "user_1": {"name": "Musik", "params": {"input_db": 6}},
            "user_2": {"name": "Film", "params": {"input_db": 6, "lev_max_boost_db": 12}},
            "user_8": {"name": "Alt", "params": {"input_db": 3}}})
        slots = ProfileCatalog().user_slots()
        self.assertEqual(len(USER_SLOTS), 6)
        self.assertNotIn("user_8", slots)
        self.assertEqual((slots["user_1"]["mode"], slots["user_2"]["mode"]), ("fixed", "dynamic"))
        self.assertEqual(ProfileCatalog().template_setting("user_1"), ("fixed", {
            "input_db": 6.0, "dialog_db": 0.0, "bass_db": 0.0, "treble_db": 0.0, "side_db": 0.0}))


class TestOverdriveScale(unittest.TestCase):
    def tearDown(self):
        import cinegain_overdrive as od
        od.set_on(False)

    def test_scale_caps_at_15_and_overdrive_reaches_40(self):
        import cinegain_overdrive as od
        od.set_on(False)
        p = modes.effective("fixed", {"fixed": {"input_db": 30.0}})
        self.assertEqual(p["input_db"], 15.0)
        p = modes.effective("dynamic", {"dynamic": {"input_db": -40.0, "makeup_db": 30.0}})
        self.assertEqual((p["input_db"], p["makeup_db"]), (-30.0, 15.0))
        od.set_on(True)
        self.assertEqual(modes.effective("fixed", {"fixed": {"input_db": 30.0}})["input_db"], 30.0)
        self.assertEqual(modes.effective("fixed", {"fixed": {"input_db": 55.0}})["input_db"], 40.0)

    def test_overdrive_off_caps_saved_values(self):
        import cinegain_overdrive as od
        reg = tr.AppRegistry()
        reg.observe("Firefox", "firefox")
        od.set_on(True)
        reg.set_setting(tr.FIREFOX, "fixed", {"fixed": {"input_db": 28.0}})
        modes.save_standard("fixed", {"fixed": {"input_db": 22.0}})
        self.assertEqual(reg.get(tr.FIREFOX)["sets"]["fixed"]["input_db"], 28.0)
        od.set_on(False)
        changed = modes.clamp_stored(reg, overdrive_on=False)
        self.assertEqual(set(changed), {tr.FIREFOX, "standard"})
        self.assertEqual(reg.get(tr.FIREFOX)["sets"]["fixed"]["input_db"], 15.0)
        self.assertEqual(reg.get(tr.FIREFOX)["params"]["input_db"], 15.0)
        self.assertEqual(modes.load_standard()["sets"]["fixed"]["input_db"], 15.0)
        # values inside the scale stay untouched (the other side of the invariant)
        reg.set_setting(tr.FIREFOX, "fixed", {"fixed": {"input_db": -25.0}})
        self.assertEqual(modes.clamp_stored(reg, overdrive_on=False), [])
        self.assertEqual(reg.get(tr.FIREFOX)["sets"]["fixed"]["input_db"], -25.0)

    def test_boost_presets_cover_the_normal_scale(self):
        from cinegain_profiles import ProfileCatalog
        factory = ProfileCatalog().factory()
        steps = [factory[f"boost_{n}db"]["params"]["input_db"] for n in (3, 6, 9, 12, 15)]
        self.assertEqual(steps, [3.0, 6.0, 9.0, 12.0, 15.0])
        self.assertTrue(all(p["params"].get("input_db", 0) <= 15.0 for p in factory.values()))


class TestAnalysis(unittest.TestCase):
    DUMP = [
        {"id": 5, "type": "PipeWire:Interface:Node", "info": {
            "props": {"media.class": "Stream/Output/Audio", "application.name": "Google Chrome",
                      "application.process.binary": "chrome", "node.name": "Google Chrome"},
            "params": {"Props": [{"volume": 0.25, "mute": False, "channelVolumes": [0.5, 0.4]}]}}},
        {"id": 6, "type": "PipeWire:Interface:Node", "info": {
            "props": {"media.class": "Audio/Sink", "node.name": "alsa_output.pci", "node.description": "Soundcard"},
            "params": {"Props": [{"volume": 1.0, "mute": True, "channelVolumes": [1.0, 1.0]}]}}},
        {"id": 7, "type": "PipeWire:Interface:Node", "info": {
            "props": {"media.class": "Audio/Sink", "node.name": "cinegain_sink", "node.description": "Cinegain"},
            "params": {"Props": [{"volume": 1.0, "channelVolumes": [1.0, 1.0]}]}}},
        {"id": 8, "type": "PipeWire:Interface:Node", "info": {
            "props": {"media.class": "Audio/Source", "node.name": "mic"},
            "params": {"Props": [{"volume": 0.1}]}}},
    ]

    def test_rows_kinds_and_editability(self):
        import cinegain_volume as vol
        rows = vol.inspect_nodes(self.DUMP, {"Google Chrome": {"volume": 0.25, "mute": False}})
        self.assertEqual([r["id"] for r in rows], [5, 7, 6])  # streams, Cinegain, hardware; no inputs
        chrome, cine, hw = rows
        self.assertEqual((chrome["kind"], chrome["editable"], chrome["stored"], chrome["normal"]),
                         ("stream", True, 0.25, 0.5))
        self.assertEqual((cine["kind"], cine["editable"]), ("cinegain_sink", True))
        self.assertEqual((hw["kind"], hw["editable"], hw["mute"]), ("hardware", False, True))

    def test_read_stored_unescapes_names(self):
        import cinegain_volume as vol
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("[stream-properties]\n"
                    'Output/Audio:application.name:Google\\sChrome={"volume":0.003906, "mute":false}\n'
                    'Input/Audio:application.name:Mic={"volume":0.5}\n')
        try:
            self.assertEqual(vol.read_stored(f.name), {"Google Chrome": {"volume": 0.003906, "mute": False}})
        finally:
            os.unlink(f.name)

    def test_history_parses_log_events(self):
        import time as _t
        import cinegain_volume as vol
        now = _t.time()
        stamp = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(now - 60))
        old = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(now - 9 * 86400))
        lines = [
            f"{old}.001 1 gui WARNING cinegain.volume: hidden volume of node 3 (chrome, Chrome) is 0.1 = -20.0 dB",
            f"{stamp}.001 1 gui WARNING cinegain.volume: hidden volume of node 3 (chrome, Chrome) is 0.003906 = -48.2 dB (was 0.0 dB)",
            f"{stamp}.002 1 gui INFO  cinegain.volume: fading node 3 (chrome) from -48.2 dB back to 0 dB over 3 s",
            f"{stamp}.003 1 cli INFO  cinegain.volume: hidden volume of node 9 (Spotify) set to 0.500000 = -6.0 dB by cli",
        ]
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
            f.write("\n".join(lines) + "\n")
        try:
            ev = vol.history(f.name, now=now)
            self.assertEqual(sorted(e["kind"] for e in ev), ["auto", "lowered", "set"])  # 9-day-old one dropped
            low = [e for e in ev if e["kind"] == "lowered"][0]
            self.assertEqual((low["key"], low["db"], low["was_db"]), ("chrome", -48.2, 0.0))
            self.assertEqual(len(vol.history(f.name, now=now, key="chrome")), 2)
        finally:
            os.unlink(f.name)

    def test_guard_without_auto_only_reports(self):
        import cinegain_volume as vol
        nodes = [vol.NodeVolume(1, "Firefox", "firefox", "Firefox", 0.1)]
        g = vol.VolumeGuard(scanner=lambda: nodes, setter=lambda *a: True, clock=lambda: 0.0, auto=False)
        hidden = g.update(auto_keys={nodes[0].key})
        self.assertEqual(g.fades, {})
        self.assertIn(nodes[0].key, hidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
