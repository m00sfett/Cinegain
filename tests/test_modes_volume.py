#!/usr/bin/env python3
"""
0.4.0: fixed / dynamic (beta) mode per target and the hidden-volume guard.
Offline: PipeWire, pactl and the CLI's node lookup are replaced by fakes, so
nothing here touches the running sound server.
"""
import contextlib
import io
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
from cinegain_volume import (  # noqa: E402
    FADE_S, MAX_RISE_DB_S, MAX_STEP_DB, NodeVolume, VolumeGuard, clean_stream_properties, parse_nodes, to_db,
)

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


def drain(guard, clock, dt=0.1, limit=60.0):
    """Steps a guard like the GUI timer does until its fades are done (or limit s pass)."""
    end = clock.t + limit
    while guard.fades and clock.t < end:
        clock.t += dt
        guard.step()


class FakeVolumes:
    """pw-dump/pw-cli stand-in: nodes with a hidden volume; setter records and applies."""

    def __init__(self, nodes):
        self.nodes = {n.id: n for n in nodes}
        self.sets = []

    def scan(self):
        return [NodeVolume(n.id, n.app_name, n.binary, n.node_name, n.volume, n.is_sink, n.mixer, n.mute)
                for n in self.nodes.values()]

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
        for _ in range(15):  # 1.5 s at the GUI's 100 ms steps
            clock.t += 0.1
            guard.step()
        self.assertAlmostEqual(to_db(fake.nodes[1].volume), -48.18 + 1.5 * MAX_RISE_DB_S, places=1)  # 10 dB/s
        drain(guard, clock)
        self.assertEqual((fake.nodes[1].volume, fake.nodes[3].volume), (1.0, 1.0))
        self.assertEqual(guard.fades, {})
        self.assertEqual(fake.nodes[2].volume, 0.00006)  # foreign program untouched

    def _fade_trace(self, start, steps):
        """Writes of an auto fade from `start` (linear), stepped at the given intervals."""
        fake = FakeVolumes([NodeVolume(1, "", "", "cinegain_output", start)])
        clock = tr.Clock(0.0)
        guard = VolumeGuard(scanner=fake.scan, setter=fake.set, clock=clock)
        guard.update()
        trace = []
        for dt in steps:
            clock.t += dt
            guard.step()
            trace.append((clock.t, fake.nodes[1].volume))
            if not guard.fades:
                break
        return trace, guard

    def test_fade_rises_at_most_10_db_per_s_in_small_steps_and_lands_on_0_db(self):
        """#40.3: -84 dB came back at 28 dB/s in 2.8 dB steps; now <= 10 dB/s and <= 1 dB per step."""
        start_db = -84.3
        trace, guard = self._fade_trace(10 ** (start_db / 20), [0.02] * 1000)  # 20 ms: the step cap alone allows 50 dB/s
        last = start_db
        for t, v in trace:
            db = to_db(v)
            self.assertLessEqual(db, start_db + MAX_RISE_DB_S * t + 1e-6, f"faster than 10 dB/s at {t:.1f} s")
            self.assertLessEqual(db - last, MAX_STEP_DB + 1e-6, f"step too coarse at {t:.1f} s")
            self.assertGreater(db, last - 1e-9, "a fade-in never dips")
            last = db
        self.assertEqual(trace[-1][1], 1.0)                                  # exactly 0 dB at the end
        self.assertEqual(guard.fades, {})
        self.assertAlmostEqual(trace[-1][0], -start_db / MAX_RISE_DB_S, delta=0.11)  # no slower than needed

    def test_stalled_timer_never_makes_a_jump(self):
        trace, _ = self._fade_trace(10 ** (-60 / 20), [0.1, 2.5, 0.1, 0.1])  # GUI froze for 2.5 s
        dbs = [-60.0] + [to_db(v) for _, v in trace]
        self.assertLessEqual(max(b - a for a, b in zip(dbs, dbs[1:])), MAX_STEP_DB + 1e-6)

    def test_small_lowering_still_takes_the_full_fade_time(self):
        trace, _ = self._fade_trace(0.5, [0.1] * 100)                       # -6 dB
        self.assertAlmostEqual(trace[-1][0], FADE_S, delta=0.11)

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


SAMPLE_STREAM_PROPERTIES = (
    "[stream-properties]\n"
    'Output/Audio:application.name:Chromium={"mute":false, "channelMap":["FL", "FR"], "volume":0.000061, '
    '"channelVolumes":[1.000000, 1.000000]}\n'
    'Output/Audio:application.name:Google\\sChrome={"mute":false, "target":"cinegain_app_chrome_google_chrome_eb8b16", '
    '"volume":1.000000, "channelMap":["FL", "FR"], "channelVolumes":[1.000000, 1.000000]}\n'
    'Output/Audio:application.name:Firefox={"target":"cinegain_sink", "volume":1.000000}\n'
    'Audio/Sink:media.name:Cinegain\\s\u00b7\\sGoogle\\sChrome={"volume":0.062500, "channelMap":["FL", "FR"], '
    '"mute":false, "channelVolumes":[0.5, 0.5]}\n'
    'Output/Audio:media.name:Cinegain\\s\u00b7\\sGoogle\\sChrome={"volume":0.062500, "mute":false}\n'
    'Audio/Sink:node.name:alsa_output.usb={"volume":0.4, "mute":false}\n'
    'Output/Audio:application.name:Spotify={"target":"alsa_output.hdmi", "volume":1.000000}\n'
    'Input/Audio:application.name:Mic={"volume":0.4}\n'
)


class TestChangeLog(unittest.TestCase):
    """#44: every volume change on every level, both directions, with its source, without flooding."""

    def setUp(self):
        import cinegain_volume as vol
        self.vol = vol
        vol._own_writes.clear()
        self.clock = tr.Clock(1000.0)
        self.fake = FakeVolumes([
            NodeVolume(1, "Google Chrome", "chrome", "Google Chrome", 0.25),
            NodeVolume(53, "", "", "alsa_output.analog-stereo", 1.0, is_sink=True),
        ])
        self.guard = VolumeGuard(scanner=self.fake.scan, setter=self.fake.set, clock=self.clock, auto=False)

    def tick(self, dt=1.5):
        self.clock.t += dt
        self.guard.update()

    def logged(self, fn):
        with self.assertLogs("cinegain.volume", level="DEBUG") as cm:
            self.vol.log.debug("probe")
            fn()
        return [line for line in cm.output if "probe" not in line]

    def test_raise_and_lowering_from_outside_are_both_logged(self):
        first = self.logged(self.tick)
        self.assertTrue(any("hidden volume of node 1 (chrome::google chrome, Google Chrome) is 0.250000 = -12.0 dB"
                            in line for line in first))
        self.fake.nodes[1].volume = 1.0                       # raised by someone else (the 20:23 case)
        up = self.logged(self.tick)
        self.assertEqual(len(up), 1)
        self.assertIn("INFO", up[0])
        self.assertIn("is 1.000000 = 0.0 dB (was -12.0 dB) [external]", up[0])
        self.clock.t += 5
        self.fake.nodes[1].volume = 0.5                       # and lowered again
        down = self.logged(self.tick)
        self.assertIn("WARNING", down[0])
        self.assertIn("= -6.0 dB (was 0.0 dB) [external]", down[0])

    def test_rapid_changes_are_merged_not_flooded_and_none_is_lost(self):
        self.tick()
        values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

        def burst():
            for v in values:
                self.fake.nodes[1].volume = v
                self.tick(0.25)                               # 8 changes within 2 s
            self.tick(2.0)                                    # quiet: the merged line comes
        lines = [line for line in self.logged(burst) if "node 1 " in line]
        self.assertEqual(len(lines), 2)
        self.assertIn("= -10.5 dB (was -12.0 dB)", lines[0])  # the first change at once
        self.assertIn("= 0.0 dB (was -10.5 dB)", lines[1])    # the rest merged up to the latest value

    def test_mixer_mute_and_sink_changes_are_logged(self):
        self.tick()
        self.fake.nodes[1].mixer = 0.125                     # -18 dB = 50 % on the cubic mixer scale
        self.fake.nodes[1].mute = True
        self.fake.nodes[53].mixer = 0.5
        self.fake.nodes[53].volume = 0.5
        text = "\n".join(self.logged(self.tick))
        self.assertIn("mixer volume of node 1 (chrome::google chrome, Google Chrome) is -18.1 dB = 50 % "
                      "(was 0.0 dB) [external]", text)
        self.assertIn("mute of node 1 (chrome::google chrome, Google Chrome) is on (was off) [external]", text)
        self.assertIn("hidden volume of node 53 (::alsa_output.analog-stereo, alsa_output.analog-stereo) is "
                      "0.500000 = -6.0 dB (was 0.0 dB) [external]", text)
        self.assertIn("mixer volume of node 53", text)

    def test_cinegain_changes_are_not_reported_as_external(self):
        self.tick()
        self.guard.fade_hidden(1, 0.25, 1.0)                 # our own fade-in
        drain(self.guard, self.clock)
        self.vol.note_own_write(53, 0.7)                      # e.g. the analysis view's own guard
        self.fake.nodes[53].volume = 0.7
        lines = self.logged(lambda: (self.tick(), self.tick(3.0)))
        self.assertEqual([line for line in lines if "[external]" in line], [])

    def test_history_reads_the_new_events(self):
        import time as _t
        vol = self.vol
        now = _t.time()
        stamp = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(now - 60))
        lines = [
            f"{stamp}.001 1 gui INFO  cinegain.volume: hidden volume of node 3 (chrome, Chrome) is 1.000000 = 0.0 dB "
            "(was -12.0 dB) [external]",
            f"{stamp}.002 1 gui WARNING cinegain.volume: hidden volume of node 3 (chrome, Chrome) is 0.500000 = -6.0 dB "
            "(was 0.0 dB) [external]",
            f"{stamp}.003 1 gui INFO  cinegain.volume: mixer volume of node 3 (chrome, Chrome) is -18.1 dB = 50 % "
            "(was 0.0 dB) [external]",
            f"{stamp}.004 1 gui INFO  cinegain.volume: mute of node 3 (chrome, Chrome) is on (was off) [external]",
            f"{stamp}.005 1 gui INFO  cinegain.volume: mute of node 3 (chrome, Chrome) is off (was on) [external]",
        ]
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
            f.write("\n".join(lines) + "\n")
        journal = [{"time": now - 30, "kind": "link_failed", "key": "", "db": None, "was_db": None,
                    "who": "wireplumber"}]
        orig = vol.wireplumber_events
        vol.wireplumber_events = lambda since: list(journal)
        try:
            ev = vol.history(f.name, now=now, journal=True)
            self.assertNotIn("link_failed", [e["kind"] for e in vol.history(f.name, now=now)])  # test log: no journal
        finally:
            vol.wireplumber_events = orig
            os.unlink(f.name)
        kinds = [(e["kind"], e["db"], e["was_db"], e["who"]) for e in sorted(ev, key=lambda e: e["time"])]
        self.assertEqual(kinds, [
            ("raised", 0.0, -12.0, "external"), ("lowered", -6.0, 0.0, "external"),
            ("mixer", -18.1, 0.0, "external"), ("muted", None, None, "external"),
            ("unmuted", None, None, "external"), ("link_failed", None, None, "wireplumber")])

    def test_timeline_shows_the_new_events(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PyQt6.QtWidgets import QApplication
            import analysis_panel
            from i18n import t
        except ImportError as e:  # pragma: no cover - PyQt6 missing
            self.skipTest(f"PyQt6 not available: {e}")
        app = QApplication.instance() or QApplication([])

        def ev(kind, db=None, was=None, who="external"):
            return {"time": 1e9, "kind": kind, "key": "chrome", "db": db, "was_db": was, "who": who}
        events = [ev("raised", 0.0, -12.0), ev("mixer", -18.1, 0.0), ev("muted"), ev("unmuted"),
                  ev("link_failed", who="wireplumber")]
        panel = analysis_panel.AnalysisPanel(scanner=lambda: [], setter=lambda *a: True,
                                             history=lambda key=None: list(events))
        panel.refresh_history(force=True)
        cells = [(panel.timeline.item(i, 2).text(), panel.timeline.item(i, 3).text()) for i in range(5)]
        self.assertEqual(cells, [
            (t("analysis_ev_raised", db="+0.0 dB", was="−12.0 dB"), t("analysis_by_external")),
            (t("analysis_ev_mixer", db="−18.1 dB", was="+0.0 dB"), t("analysis_by_external")),
            (t("analysis_ev_muted"), t("analysis_by_external")),
            (t("analysis_ev_unmuted"), t("analysis_by_external")),
            (t("analysis_ev_link_failed"), "WirePlumber")])
        del app

    def test_journal_link_failures_become_events(self):
        import json as _json

        class Res:
            returncode, stderr = 0, ""
            stdout = "\n".join(_json.dumps(o) for o in (
                {"__REALTIME_TIMESTAMP": "1794425959369580",
                 "MESSAGE": "wp-event-dispatcher: <WpSiStandardLink:0x5> link failed: 1 of 1 PipeWire links failed"},
                {"__REALTIME_TIMESTAMP": "1794425897811626", "MESSAGE": "wp_properties_get: assertion failed"},
                {"__REALTIME_TIMESTAMP": "x", "MESSAGE": "link failed"}))
        calls = []
        ev = self.vol.wireplumber_events(1794400000, runner=lambda cmd, **kw: calls.append(cmd) or Res())
        self.assertEqual([(round(e["time"]), e["kind"]) for e in ev], [(1794425959, "link_failed")])
        self.assertEqual(calls[0][:4], ["journalctl", "--user", "-u", "wireplumber"])


class TestSavedVolumes(unittest.TestCase):
    """#26: the full reset did not hold: Audio/Sink entries, stale targets, WirePlumber's copy in memory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cinegain-wp-")
        self.path = os.path.join(self.tmp, "stream-properties")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SAMPLE_STREAM_PROPERTIES)

    def entries(self):
        out = {}
        for line in open(self.path, encoding="utf-8").read().splitlines()[1:]:
            k, v = line.split("=", 1)
            out[k] = v
        return out

    def test_cleanup_covers_cinegain_sinks_and_stale_targets_only(self):
        n, backup = clean_stream_properties(self.path, backup_dir=os.path.join(self.tmp, "bk"), stamp="t",
                                            existing_sinks={"cinegain_sink", "alsa_output.hdmi"})
        e = self.entries()
        sink = "Audio/Sink:media.name:Cinegain\\s\u00b7\\sGoogle\\sChrome"
        self.assertIn('"volume":1.000000', e[sink])
        self.assertIn('"channelVolumes":[1.000000, 1.000000]', e[sink])
        self.assertIn('"volume":1.000000', e["Output/Audio:media.name:Cinegain\\s\u00b7\\sGoogle\\sChrome"])
        self.assertIn('"volume":1.000000', e["Output/Audio:application.name:Chromium"])
        # the target on a sink that is gone is dropped; one on a running sink and foreign ones stay
        self.assertNotIn("target", e["Output/Audio:application.name:Google\\sChrome"])
        self.assertIn('"target":"cinegain_sink"', e["Output/Audio:application.name:Firefox"])
        self.assertIn('"target":"alsa_output.hdmi"', e["Output/Audio:application.name:Spotify"])
        # hardware sinks and inputs are not Cinegain's business
        self.assertEqual(e["Audio/Sink:node.name:alsa_output.usb"], '{"volume":0.4, "mute":false}')
        self.assertEqual(e["Input/Audio:application.name:Mic"], '{"volume":0.4}')
        self.assertEqual(n, 4)
        self.assertEqual(open(backup, encoding="utf-8").read(), SAMPLE_STREAM_PROPERTIES)

    def _runner(self, values, fail_set=False):
        calls = []

        class Res:
            def __init__(self, rc=0, out=""):
                self.returncode, self.stdout, self.stderr = rc, out, ""

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] != ["wpctl", "settings"]:
                raise AssertionError(f"unexpected command {cmd}")
            key = cmd[2]
            if len(cmd) == 3:
                return Res(0, f"Value: {'true' if values[key] else 'false'}\n")
            if fail_set and cmd[3] == "false" and key == "node.stream.restore-props":
                return Res(1)
            values[key] = cmd[3] == "true"
            return Res()
        return run, calls

    def test_wireplumber_reloads_the_cleaned_file_and_settings_come_back(self):
        import cinegain_volume as vol
        values = {"node.stream.restore-props": True, "node.stream.restore-target": True}
        run, calls = self._runner(values)
        seen = {}
        slept = []

        def clean():
            seen.update(values)  # what WirePlumber had while the file was rewritten
            seen["slept"] = sum(slept)  # a pending save of the old copy (1 s) must have landed
            return clean_stream_properties(self.path, backup_dir=False)

        (n, _), ok = vol.reload_wireplumber_state(clean, runner=run, sleep=slept.append)
        self.assertTrue(ok)
        self.assertGreater(n, 0)
        self.assertGreater(seen.pop("slept"), 1.0)
        self.assertEqual(seen, {"node.stream.restore-props": False, "node.stream.restore-target": False})
        self.assertEqual(values, {"node.stream.restore-props": True, "node.stream.restore-target": True})
        sets = [c[2:] for c in calls if len(c) == 4]
        self.assertEqual(sets, [["node.stream.restore-target", "false"], ["node.stream.restore-props", "false"],
                                ["node.stream.restore-props", "true"], ["node.stream.restore-target", "true"]])

    def test_user_settings_are_respected_and_restored_after_a_failure(self):
        import cinegain_volume as vol
        values = {"node.stream.restore-props": True, "node.stream.restore-target": False}  # user turned one off
        run, calls = self._runner(values, fail_set=True)
        (n, _), ok = vol.reload_wireplumber_state(lambda: (1, None), runner=run, sleep=lambda s: None)
        self.assertFalse(ok)                                    # reported, so the CLI offers the restart
        self.assertEqual(values, {"node.stream.restore-props": True, "node.stream.restore-target": False})
        self.assertNotIn(["wpctl", "settings", "node.stream.restore-target", "false"], calls)
        self.assertNotIn(["wpctl", "settings", "node.stream.restore-target", "true"], calls)

    def test_tests_never_reach_the_live_wireplumber(self):
        import cinegain_volume as vol

        def run(cmd, **kw):
            raise AssertionError(f"live command {cmd}")
        self.assertNotEqual(paths.WIREPLUMBER_STREAM_PROPS, paths.DEFAULT_WP_STREAM_PROPS)
        self.assertEqual(vol.reset_saved_volumes(runner=run, sleep=lambda s: None)["wireplumber"], "skipped")


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
        cinegain_volume.clean_stream_properties = lambda *a, **k: (0, None)
        self.router.saved_reset_in_background = False

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
        drain(self.router.guard, self.clock)
        self.assertEqual(self.volumes.nodes[22].volume, 1.0)
        self.assertEqual([f["target"] for f in report["faded"]], ["spotify::spotify"])
        self.assertEqual(len(report["compensated"]), 2)

    def _record_order(self):
        events = []
        move, setter = self.pulse.move, self.volumes.set
        self.pulse.move = lambda i, sink: events.append(("move", i)) or move(i, sink)
        self.router.guard.setter = lambda n, v: events.append(("set", n, round(v, 6))) or setter(n, v)
        return events

    def test_stream_is_ducked_before_it_moves_and_fades_in_on_the_new_sink(self):
        """#40.1: a stream moved onto a sink that is already boosted fades in instead of jumping."""
        from cinegain_volume import DUCK_VOLUME
        self.stream(1, "Spotify", "spotify", node_id=41)
        self.volumes.nodes[41] = NodeVolume(41, "Spotify", "spotify", "Spotify", 1.0)
        events = self._record_order()
        self.router.tick()
        moves = [i for i, e in enumerate(events) if e[0] == "move"]
        self.assertEqual(len(moves), 1)
        self.assertGreaterEqual(moves[0], 1)
        self.assertEqual(events[moves[0] - 1], ("set", 41, DUCK_VOLUME))  # ducked right before the move
        self.assertIn(41, self.router.guard.fades)
        drain(self.router.guard, self.clock)
        self.assertEqual(self.volumes.nodes[41].volume, 1.0)
        writes = [e[2] for e in events if e[0] == "set"]
        self.assertLessEqual(rises(writes[1:], writes[0]), MAX_STEP_DB + 1e-3)  # writes are rounded
        self.assertGreaterEqual(len(writes), 30)  # about 3 s at 100 ms steps, not a jump

    def test_duck_never_raises_a_quieter_stream_and_streams_without_node_id_still_move(self):
        self.stream(1, "Chromium", "ChatGPT", node_id=42)
        self.stream(2, "Spotify", "spotify")  # no object.id known
        self.registry.observe("Chromium", "ChatGPT")
        self.registry.set_enabled("chatgpt::chromium", True)
        quiet = 10 ** (-84 / 20)
        self.volumes.nodes[42] = NodeVolume(42, "Chromium", "ChatGPT", "Chromium", quiet)
        events = self._record_order()
        self.router.guard.auto = False  # watch the duck alone, not the auto reset
        self.router.tick()
        self.assertEqual(sorted(e[1] for e in events if e[0] == "move"), ["1", "2"])
        self.assertTrue(all(v <= quiet for _, n, v in [e for e in events if e[0] == "set"] if n == 42))
        self.assertNotIn(42, self.router.guard.fades)

    # -- #29: full reset ---------------------------------------------------------
    def _own_firefox_on_its_sink(self, input_db):
        firefox = tr.FIREFOX
        self.stream(1, "Firefox", "firefox")
        self.router.tick()
        self.registry.set_setting(firefox, "fixed", {"fixed": {"input_db": input_db}})
        self.router.tick()
        self.router.tick()
        self.assertEqual(self.sink_of(self.pulse.streams[0]), self.own_sink(firefox))
        return firefox

    def _writes(self, node_id):
        return [v for n, v in self.volumes.sets if n == node_id]

    def test_full_reset_offsets_down_to_the_floor_and_fades_the_rest(self):
        """#29a: -48 dB back with the gain at -20 dB: 10 dB offset (floor -30 dB), 38 dB fade in."""
        firefox = self._own_firefox_on_its_sink(-20.0)
        self.volumes.nodes[21] = NodeVolume(21, "Firefox", "firefox", "Firefox", 10 ** (-48 / 20))
        report = self.router.full_volume_reset()
        self.assertEqual(self.registry.get(firefox)["sets"]["fixed"]["input_db"], -30.0)
        first = self._writes(21)[0]
        self.assertAlmostEqual(to_db(first), -38.0, places=1)    # exactly the offset comes back at once
        self.assertEqual([f["from_db"] for f in report["faded"]], [-38.0])
        t0 = self.clock.t
        drain(self.router.guard, self.clock)
        self.assertEqual(self.volumes.nodes[21].volume, 1.0)
        self.assertLessEqual(rises(self._writes(21)[1:], first), MAX_STEP_DB + 1e-3)  # writes are rounded
        self.assertGreaterEqual(self.clock.t - t0, 38 / MAX_RISE_DB_S - 0.11)

    def test_full_reset_only_offsets_streams_that_pass_the_filter(self):
        """#29b: own setting, but the stream plays on the hardware (or the filter is in soft off)."""
        firefox = tr.FIREFOX
        self.stream(1, "Firefox", "firefox")       # stays on the hardware: no tick routes it
        self.registry.observe("Firefox", "firefox")
        self.registry.set_setting(firefox, "fixed", {"fixed": {"input_db": 15.0}})
        self.volumes.nodes[21] = NodeVolume(21, "Firefox", "firefox", "Firefox", 10 ** (-24 / 20))
        report = self.router.full_volume_reset()
        self.assertEqual(report["compensated"], [])
        self.assertEqual(self.registry.get(firefox)["sets"]["fixed"]["input_db"], 15.0)  # not lowered for good
        self.assertEqual(self._writes(21), [])                     # nothing jumps ...
        self.assertLess(to_db(self.router.guard.known_volume(21)), -23.9)  # ... it fades from -24 dB
        self.router.guard.fades.clear()
        # on its sink, but every Cinegain sink passes audio untouched: the gain offsets nothing
        self.router.tick()
        self.router.tick()
        self.router.bypass = True
        self.volumes.nodes[21].volume = 10 ** (-24 / 20)
        self.volumes.sets.clear()
        report = self.router.full_volume_reset()
        self.assertEqual(report["compensated"], [])
        self.assertLess(to_db(self.router.guard.known_volume(21)), -22.9)

    def test_full_reset_without_standard_callables_fades_standard_output(self):
        self.volumes.nodes[23] = NodeVolume(23, "", "", "cinegain_output", 0.25)  # -12 dB
        self.router.full_volume_reset()
        self.assertEqual(self._writes(23), [])                     # no jump of +12 dB
        self.assertIn(23, self.router.guard.fades)

    def test_full_reset_fades_mixer_volumes_and_leaves_mute_alone(self):
        """#29c: 20 % (cubic, -42 dB) jumped to 100 % and mute went off at once."""
        from cinegain_volume import mixer_db
        a = self.stream(1, "Spotify", "spotify", volume_pct=40, volume_min_pct=20)
        b = self.stream(2, "Discord", "discord", volume_pct=50, muted=True)
        report = self.router.full_volume_reset()
        self.assertEqual((report["volumes_100"], report["muted_left"]), (2, 1))
        self.assertEqual(self.pulse.volume_resets, [])            # no set_volume_100: no jump, no unmute
        drain(self.router.guard, self.clock)
        self.assertEqual((a.volume_pct, b.volume_pct, b.muted), (100, 100, True))
        series = [p for i, p in self.pulse.volume_sets if i == "1"]
        dbs = [mixer_db(20)] + [mixer_db(p) for p in series]
        self.assertLessEqual(max(y - x for x, y in zip(dbs, dbs[1:])), MAX_STEP_DB + 1e-6)
        self.assertGreaterEqual(len(series), 40)                  # ~4.2 s at 100 ms, from the quiet channel

    def test_sidebar_restore_volume_fades_and_keeps_mute(self):
        """#48: "restore volume" jumped to 100 % via set_volume_100 and unmuted. Now: fade, mute stays."""
        from cinegain_volume import mixer_db
        a = self.stream(1, "Spotify", "spotify", volume_pct=40, volume_min_pct=20)
        b = self.stream(2, "Spotify", "spotify", volume_pct=50, muted=True)
        other = self.stream(3, "Discord", "discord", volume_pct=30)
        self.router.restore_volume(tr.make_key("Spotify", "spotify"))
        self.assertEqual(self.pulse.volume_resets, [])                       # no jump, no unmute
        self.assertEqual((a.volume_pct, other.volume_pct), (40, 30))         # nothing written at once
        drain(self.router.guard, self.clock)
        self.assertEqual((a.volume_pct, b.volume_pct, b.muted), (100, 100, True))   # target reached, mute stays
        self.assertEqual(other.volume_pct, 30)                               # other programs untouched
        series = [p for i, p in self.pulse.volume_sets if i == "1"]
        dbs = [mixer_db(20)] + [mixer_db(p) for p in series]
        self.assertLessEqual(max(y - x for x, y in zip(dbs, dbs[1:])), MAX_STEP_DB + 1e-6)
        self.assertGreaterEqual(len(series), 40)

    def test_sidebar_restore_volume_without_guard_runs_the_fade_to_the_end(self):
        """One-shot use (no GUI timer): the fade runs inside the call and ends at 100 %."""
        a = self.stream(1, "Spotify", "spotify", volume_pct=90)
        self.router.guard = None
        slept = []
        import cinegain_volume
        orig = cinegain_volume.time.sleep
        cinegain_volume.time.sleep = lambda s: (slept.append(s), setattr(self.clock, "t", self.clock.t + s))
        try:
            self.router.restore_volume(tr.make_key("Spotify", "spotify"))
        finally:
            cinegain_volume.time.sleep = orig
        self.assertEqual(a.volume_pct, 100)

    def test_full_reset_includes_cinegain_sinks_not_hardware_and_passes_running_sinks(self):
        import cinegain_volume
        seen = {}
        orig = cinegain_volume.reset_saved_volumes
        cinegain_volume.reset_saved_volumes = lambda existing: seen.setdefault("sinks", set(existing)) and {
            "file_entries": 0, "backup": None, "wireplumber": "skipped"}
        try:
            self.volumes.nodes[61] = NodeVolume(61, "", "", "cinegain_sink", 0.25, is_sink=True)
            self.volumes.nodes[62] = NodeVolume(62, "", "", "alsa_output.analog-stereo", 0.25, is_sink=True)
            report = self.router.full_volume_reset()
        finally:
            cinegain_volume.reset_saved_volumes = orig
        self.assertIn(61, self.router.guard.fades)              # Cinegain's filter input fades back
        self.assertNotIn(62, self.router.guard.fades)           # the sound card stays as it is
        self.assertEqual(self._writes(62), [])
        self.assertIn("cinegain_sink", seen["sinks"])           # targets on running sinks may stay
        self.assertEqual(report["wireplumber"], "skipped")

    def test_full_reset_mixer_fade_is_not_reported_as_external(self):
        self.stream(1, "Spotify", "spotify", volume_pct=50, node_id=21)
        self.volumes.nodes[21] = NodeVolume(21, "Spotify", "spotify", "Spotify", 1.0, mixer=0.125)
        self.router.guard.update()
        self.router.full_volume_reset()
        drain(self.router.guard, self.clock)
        self.volumes.nodes[21].mixer = 1.0                   # PipeWire now shows what pactl set
        with self.assertLogs("cinegain.volume", level="DEBUG") as cm:
            import cinegain_volume
            cinegain_volume.log.debug("probe")
            self.clock.t += 3
            self.router.guard.update()
        self.assertEqual([line for line in cm.output if "[external]" in line], [])

    def test_reset_of_one_program_leaves_others(self):
        self.stream(1, "Chromium", "ChatGPT")
        self.stream(2, "Discord", "discord")
        self.volumes.nodes[31] = NodeVolume(31, "Chromium", "ChatGPT", "Chromium", 0.01)
        self.volumes.nodes[32] = NodeVolume(32, "Discord", "discord", "Discord", 0.01)
        self.router.tick()
        self.router.full_volume_reset(only="chatgpt::chromium")
        drain(self.router.guard, self.clock)
        self.assertEqual(self.volumes.nodes[31].volume, 1.0)
        self.assertEqual(self.volumes.nodes[32].volume, 0.01)


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


def rises(values, start):
    """Largest single upward step in dB of a series of linear writes."""
    dbs = [to_db(start)] + [to_db(v) for v in values]
    return max([b - a for a, b in zip(dbs, dbs[1:])] or [0.0])


class TestSetHidden(unittest.TestCase):
    """#27: the analysis view and `cinegain volume set` raised the hidden volume in one jump."""

    def setUp(self):
        import cinegain_volume as vol
        self.vol = vol
        vol._live = None
        self.fake = FakeVolumes([NodeVolume(5, "Chromium", "ChatGPT", "Chromium", 10 ** (-84 / 20))])
        self.clock = tr.Clock(0.0)

    def test_raise_fades_in_and_lowering_is_instant(self):
        vol = self.vol
        guard = VolumeGuard(scanner=self.fake.scan, setter=self.fake.set, clock=self.clock)
        start = self.fake.nodes[5].volume
        self.assertTrue(vol.set_hidden(5, 1.0, "Chromium", "user", start, guard))
        self.assertEqual(self.fake.sets, [])                    # nothing jumps on the click
        self.assertIn(5, guard.fades)
        drain(guard, self.clock, dt=0.05)
        self.assertEqual(self.fake.nodes[5].volume, 1.0)
        self.assertLessEqual(rises([v for _, v in self.fake.sets], start), MAX_STEP_DB + 1e-6)
        self.assertGreaterEqual(self.clock.t, 84 / MAX_RISE_DB_S - 0.06)  # 8.4 s, not instant
        # lowering: one write, at once, and a running fade is dropped
        vol.set_hidden(5, 0.5, "Chromium", "user", None, guard)
        self.assertEqual(self.fake.sets[-1], (5, 0.5))
        self.assertEqual(guard.fades, {})

    def test_stale_higher_reading_cannot_cause_a_jump(self):
        vol = self.vol
        guard = VolumeGuard(scanner=self.fake.scan, setter=self.fake.set, clock=self.clock)
        guard.update()                                           # guard saw -84 dB
        vol.set_hidden(5, 1.0, "Chromium", "user", 0.5, guard)   # caller thinks -6 dB
        self.clock.t += 0.05
        guard.step()
        self.assertLess(to_db(self.fake.sets[0][1]), -82.0)      # starts from what the guard saw

    def test_without_a_running_guard_the_cli_fades_here(self):
        vol = self.vol
        orig = vol.set_node_volume
        vol.set_node_volume = self.fake.set
        try:
            def sleep(dt):
                self.clock.t += dt
            start = self.fake.nodes[5].volume
            self.assertTrue(vol.set_hidden(5, 1.0, "Chromium", "cli", start, clock=self.clock, sleep=sleep))
        finally:
            vol.set_node_volume = orig
        self.assertEqual(self.fake.nodes[5].volume, 1.0)
        self.assertLessEqual(rises([v for _, v in self.fake.sets], start), MAX_STEP_DB + 1e-6)
        self.assertGreaterEqual(self.clock.t, 8.3)

    def test_analysis_panel_reset_fades_in(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PyQt6.QtWidgets import QApplication
            import analysis_panel
        except ImportError as e:  # pragma: no cover - PyQt6 missing
            self.skipTest(f"PyQt6 not available: {e}")
        app = QApplication.instance() or QApplication([])
        vol = self.vol
        start = self.fake.nodes[5].volume
        rows = [{"id": 5, "kind": "stream", "label": "Chromium", "key": "chatgpt::chromium",
                 "node_name": "Chromium", "hidden": start, "normal": 1.0, "mute": False,
                 "editable": True, "stored": None}]
        panel = analysis_panel.AnalysisPanel(scanner=lambda: rows, setter=vol.set_hidden,
                                             history=lambda key=None: [])
        panel._own_guard.setter = self.fake.set
        panel._own_guard.clock = self.clock
        panel.refresh()
        orig = vol.set_node_volume
        vol.set_node_volume = self.fake.set  # never pw-cli, whatever path the panel takes
        try:
            panel.table.cellWidget(0, analysis_panel.COL_RESET).click()
        finally:
            vol.set_node_volume = orig
        self.assertEqual(self.fake.sets, [])                     # no jump on the click
        self.assertTrue(panel.fade_timer.isActive())
        while panel._own_guard.fades:
            self.clock.t += 0.05
            panel._step_own_fades()
        self.assertFalse(panel.fade_timer.isActive())
        self.assertEqual(self.fake.nodes[5].volume, 1.0)
        self.assertLessEqual(rises([v for _, v in self.fake.sets], start), MAX_STEP_DB + 1e-6)
        del app


if __name__ == "__main__":
    unittest.main(verbosity=2)
