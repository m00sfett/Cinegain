#!/usr/bin/env python3
"""
Offscreen GUI test for per-program editing: select a program in the sidebar,
edit it, load a template, switch with PageDown, go back to Standard, switch a
program off. The sound server is the in-memory fake from test_routing and the
CLI is stubbed, so nothing touches PipeWire or real user settings.
Skipped when PyQt6 is not installed.
"""
import importlib.util
import json
import os
import subprocess
import tempfile
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_routing as tr  # noqa: E402  (sets up isolated config dirs and sys.path)

try:
    import PyQt6  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

import cinegain_paths as paths  # noqa: E402
from cinegain_registry import AppRegistry, make_key, profile_id_for  # noqa: E402
from cinegain_router import Router  # noqa: E402

CHROME = make_key("Google Chrome", "chrome")
SPOTIFY = make_key("Spotify", "spotify")


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestProgramEditing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("cinegain_gui", os.path.join(BASE_DIR, "ui", "cinegain-gui.py"))
        cls.gui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gui)
        cls.app = cls.gui.QApplication.instance() or cls.gui.QApplication([])

    def setUp(self):
        for p in paths.USER_STATE_FILES:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        paths.write_json_atomic(paths.CONFIG_FILE, {"lang": "en"})
        slot = {k: 1.0 for k in ("input_db",)}
        paths.write_json_atomic(paths.USER_PRESETS_FILE, {"user_1": {"name": "Music", "params": slot}})
        paths.write_json_atomic(paths.STATE_FILE, {"preset": "custom", "params": {"input_db": 8.0}})
        self.calls = []

        def fake_run(*args):
            self.calls.append(args)
            if args[:1] == ("status",):
                state = paths.read_json(paths.STATE_FILE)
                return json.dumps({"active": True, "params": state["params"], "preset": "custom"}), 0
            return "", 0

        self.gui.run_cinegain = fake_run
        self.pulse = tr.FakePulse()
        self.pulse.add_sink("cinegain_sink")
        self.sinks = tr.FakeSinks(self.pulse)
        self.chrome = tr.Stream(1, "53", "Google Chrome", "chrome", "Google Chrome")
        self.spotify = tr.Stream(2, "53", "Spotify", "spotify", "Spotify")
        self.pulse.streams += [self.chrome, self.spotify]
        self.router = Router(registry=AppRegistry(), sinks=self.sinks, pulse=self.pulse)
        self.win = self.gui.CinegainMainWindow(self.router)
        self.win.timer.stop()
        self.win.meter_timer.stop()
        self.app.processEvents()

    def tearDown(self):
        self.win.tray.hide()
        self.win.deleteLater()
        self.app.processEvents()

    def slider(self, name="input_db"):
        return self.win.sliders[name]["slider"]

    def sink_of(self, stream):
        return self.pulse.sinks[stream.sink]

    def test_opens_on_standard_when_several_programs_play(self):
        self.assertIsNone(self.win.selected_key)
        self.assertEqual(self.win.lbl_edit_target.text(), "STANDARD")

    def test_editing_a_program_gives_it_own_values_and_leaves_standard_alone(self):
        self.win.sidebar.select(CHROME)
        self.assertEqual(self.slider().value(), 80)  # follows Standard
        self.slider().setValue(150)
        self.win.flush_pending_params()
        self.win.refresh_apps()
        self.win.refresh_apps()
        self.assertEqual(AppRegistry().get(CHROME)["params"]["input_db"], 15.0)
        self.assertEqual(paths.read_json(paths.STATE_FILE)["params"]["input_db"], 8.0)
        self.assertFalse([c for c in self.calls if c[0] in ("batch", "preset")])
        self.assertTrue(self.sink_of(self.chrome).startswith("cinegain_app_"))
        self.assertEqual(self.sink_of(self.spotify), "cinegain_sink")
        self.assertTrue(self.win.btn_back_standard.isVisibleTo(self.win))

    def test_editing_standard_still_goes_through_the_cli(self):
        self.slider().setValue(120)
        self.win.flush_pending_params()
        self.assertTrue([c for c in self.calls if c[0] == "batch"])
        self.assertIsNone(AppRegistry().get(CHROME)["params"])

    def test_template_loads_into_selected_program(self):
        self.win.sidebar.select(SPOTIFY)
        self.win.apply_factory_preset("night_mode")
        night = self.win.catalog.params("night_mode")
        self.assertAlmostEqual(AppRegistry().get(SPOTIFY)["params"]["input_db"], night["input_db"])
        self.assertFalse([c for c in self.calls if c[0] == "preset"])

    def test_program_boost_slider_switches_its_leveler_on_and_off(self):
        self.win.sidebar.select(CHROME)
        self.slider("lev_max_boost_db").setValue(150)
        self.win.flush_pending_params()
        params = AppRegistry().get(CHROME)["params"]
        self.assertEqual((params["leveler"], params["lev_max_boost_db"]), (1.0, 15.0))
        self.assertEqual(params["lev_max_cut_db"], 6.0)
        self.slider("lev_max_boost_db").setValue(0)
        self.win.flush_pending_params()
        self.assertEqual(AppRegistry().get(CHROME)["params"]["leveler"], 0.0)

    def test_cli_route_lists_programs_without_crashing(self):
        import contextlib
        import io
        from importlib.machinery import SourceFileLoader
        cli = SourceFileLoader("cinegain_cli", os.path.join(BASE_DIR, "bin", "cinegain")).load_module()
        self.win.sidebar.select(CHROME)
        self.slider().setValue(150)
        self.win.flush_pending_params()
        cli.get_cinegain_node_id = lambda: 1
        cli.make_router = lambda: self.router
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_route(None), 0)
        self.assertIn("Google Chrome", out.getvalue())
        self.assertIn("own", out.getvalue())

    def test_unknown_status_recovers_to_confirmed_inactive(self):
        self.gui.run_cinegain = lambda *args: ("", 1)
        self.win.on_heartbeat()
        self.assertFalse(self.win.btn_power.isEnabled())
        self.assertEqual(self.win.lbl_status.text(), self.gui.t("unknown_status"))
        self.gui.run_cinegain = lambda *args: ('{"active": false}', 1)
        self.win.on_heartbeat()
        self.assertTrue(self.win.btn_power.isEnabled())
        self.assertEqual(self.win.lbl_status.text(), self.gui.t("status_inactive"))

    def test_keyboard_and_details(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.win.show()
        self.slider().setFocus()
        self.app.processEvents()
        QTest.keyClick(self.slider(), Qt.Key.Key_Tab)
        self.assertFalse(self.slider().hasFocus())
        self.assertTrue(self.win.grp_adv.isHidden())
        self.win.details.setFocus()
        QTest.keyClick(self.win.details, Qt.Key.Key_Space)
        self.assertTrue(self.win.grp_adv.isVisible())
        self.assertTrue(self.win.user_preset_buttons["user_1"].isVisible())

    def test_loaded_user_slot_preserves_template_until_explicit_save(self):
        self.win.on_user_preset_clicked("user_1")  # filled slot: loads into Standard
        self.slider("input_db").setValue(77)
        self.win.flush_pending_params()
        saved = paths.read_json(paths.USER_PRESETS_FILE)["user_1"]["params"]
        self.assertEqual(saved["input_db"], 1.0)
        self.win._overwrite_user_slot("user_1")
        self.assertEqual(self.win.user_presets["user_1"]["params"]["input_db"], 7.7)

    def test_user_slot_stops_auto_saving_after_factory_preset_or_program_switch(self):
        self.win.on_user_preset_clicked("user_1")
        self.win.apply_factory_preset("boost_20db")
        self.slider("input_db").setValue(33)
        self.win.flush_pending_params()
        self.assertEqual(paths.read_json(paths.USER_PRESETS_FILE)["user_1"]["params"]["input_db"], 1.0)
        self.win.on_user_preset_clicked("user_1")
        self.win.sidebar.select(CHROME)
        self.slider("input_db").setValue(44)
        self.win.flush_pending_params()
        self.assertEqual(paths.read_json(paths.USER_PRESETS_FILE)["user_1"]["params"]["input_db"], 1.0)

    def test_meter_strip_shows_live_values_and_goes_empty_when_idle(self):
        import cinegain_profiles as cp
        cp_read = self.gui.read_meter
        seq = [0]

        def fake(pid):
            seq[0] += 1
            return (seq[0], -12.0, 6.0, -3.0, -1.0)

        self.gui.read_meter = fake
        try:
            self.win.is_active = True
            self.win.sinks = None
            self.win.router.sinks.live.add("standard")
            self.win.update_meters()
            self.assertEqual(self.win.meter_strip.values, (-12.0, 6.0, -3.0, -1.0))
            self.gui.read_meter = lambda pid: (5, -12.0, 6.0, -3.0, -1.0)
            for _ in range(6):
                self.win.update_meters()
            self.assertIsNone(self.win.meter_strip.values)
        finally:
            self.gui.read_meter = cp_read

    def test_power_off_is_pass_through_without_removing_sinks(self):
        """#14: off/on keeps every sink and stream; browsers pause when a sink disappears."""
        import cinegain_router
        pushed = []
        orig = (cinegain_router.push_params, cinegain_router.find_node_id)
        cinegain_router.push_params = lambda node, params: pushed.append(dict(params)) or True
        cinegain_router.find_node_id = lambda name: 1
        try:
            self.win.on_heartbeat()
            self.sinks.live.add("standard")
            moves = list(self.pulse.moves)
            self.win.toggle_power()
            self.assertTrue(self.router.bypass)
            self.assertFalse(self.win.processing)
            self.assertEqual(self.win.btn_power.text(), self.gui.t("btn_power_on"))
            self.assertEqual(self.win.lbl_status.text(), self.gui.t("status_inactive"))
            self.assertNotIn(("stop",), self.calls)
            self.assertEqual(self.sinks.stopped, [])
            self.assertEqual(self.pulse.moves, moves)
            self.assertIn({"bypass": 1.0}, pushed)
            # Loading a Standard preset while off must not switch processing back on.
            pushed.clear()
            self.win.apply_standard_preset("boost_20db")
            self.assertEqual(pushed[-1], {"bypass": 1.0})
            self.win.toggle_power()
            self.assertFalse(self.router.bypass)
            self.assertEqual(pushed[-1], {"bypass": 0.0})
            self.assertEqual(self.win.btn_power.text(), self.gui.t("btn_power_off"))
        finally:
            cinegain_router.push_params, cinegain_router.find_node_id = orig

    def test_language_switch_reaches_meter_tray_and_focused_labels(self):
        """#15: nothing keeps the previous language after switching back and forth."""
        self.win.change_language("de")
        for i in range(len(self.win.slider_order)):
            self.win.set_focused_slider(i)
        self.win.set_focused_slider(0)
        self.assertEqual(self.win.meter_strip.toolTip(), self.gui.t("meter_tip"))
        self.assertEqual(self.win.tray_preset_actions["night_mode"].text(), self.gui.t("preset_night_mode"))
        self.assertEqual(self.win.tray_log_action.text(), "Debug-Log öffnen")
        titles = [self.win.slider_ui_elements[n]["label"].text().lstrip("▶ ") for n in self.win.slider_order]
        self.win.change_language("en")
        self.win.change_language("de")
        self.win.set_focused_slider(3)
        again = [self.win.slider_ui_elements[n]["label"].text().lstrip("▶ ") for n in self.win.slider_order]
        self.assertEqual(titles, again)
        self.win.change_language("en")
        self.win.set_focused_slider(1)
        english = [self.win.slider_ui_elements[n]["label"].text().lstrip("▶ ") for n in self.win.slider_order]
        self.assertTrue(all(a != b for a, b in zip(titles, english) if a not in ("", )), list(zip(titles, english)))

    def test_meter_follows_the_playing_program_when_standard_is_idle(self):
        """#16: Standard selected, Chrome plays on its own setting -> the meter shows Chrome."""
        self.win.sidebar.select(CHROME)
        self.slider().setValue(self.slider().value() + 20)
        self.win.flush_pending_params()
        self.win.refresh_apps()  # starts Chrome's own sink
        self.win.refresh_apps()  # moves Chrome there
        self.win.sidebar.select(None)
        self.win.on_selection_changed(None)
        self.win.refresh_apps()
        chrome_profile = profile_id_for(CHROME)
        self.assertEqual(self.win.meter_candidates(), [("standard", "Standard"), (chrome_profile, "Google Chrome")])
        pids = {"standard": 1, chrome_profile: 2}
        orig_pid, orig_read = self.sinks.pid, self.gui.read_meter
        seq = [0]

        def read(pid):
            seq[0] += 1
            return (7, -60.0, 0.0, 0.0, 0.0) if pid == 1 else (seq[0], -20.0, 12.0, -2.0, 0.0)

        self.sinks.pid = lambda profile: pids.get(profile)
        self.gui.read_meter = read
        try:
            self.win.is_active = True
            for _ in range(6):
                self.win.update_meters()
            self.assertEqual(self.win.meter_strip.values, (-20.0, 12.0, -2.0, 0.0))
            self.assertEqual(self.win.meter_strip.source, self.gui.t("meter_source", name="Google Chrome"))
        finally:
            self.sinks.pid, self.gui.read_meter = orig_pid, orig_read

    def test_second_gui_start_raises_the_running_window(self):
        """#18: a second start brings the first window to the front instead of opening a second router."""
        import tempfile
        raised = []
        self.win.show_and_raise = lambda: raised.append(True)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gui.sock")
            self.assertFalse(self.gui.ask_running_gui_to_raise(path))  # nobody listening
            server = self.gui.start_raise_server(self.win, path)
            self.assertIsNotNone(server)
            self.assertTrue(self.gui.ask_running_gui_to_raise(path))
            for _ in range(50):
                self.app.processEvents()
                if raised:
                    break
            server.close()
        self.assertEqual(raised, [True])

    def test_read_meter_parses_plugin_file(self):
        import struct
        import tempfile
        import cinegain_profiles as cp
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "meter-42.bin"), "wb") as f:
                f.write(struct.pack(cp.METER_FORMAT, cp.METER_MAGIC, 9, -10.0, 4.0, -2.0, -0.5))
            self.assertEqual(cp.read_meter(42, d), (9, -10.0, 4.0, -2.0, -0.5))
            self.assertIsNone(cp.read_meter(43, d))

    def test_tray_preset_always_targets_standard(self):
        self.win.sidebar.select(SPOTIFY)
        before = self.slider().value()
        self.win.apply_standard_preset("boost_20db")
        self.assertIn(("preset", "boost_20db"), self.calls)
        self.assertEqual(self.slider().value(), before)
        self.assertIsNone(AppRegistry().get(SPOTIFY)["params"])

    def test_failed_pw_dump_does_not_start_filter_or_enable_power(self):
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory, "pw-dump")
            dump.write_text("#!/bin/sh\nexit 1\n")
            dump.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"])
            result = subprocess.run([self.gui.BIN_PATH, "status", "--json"], env=env,
                                    text=True, capture_output=True, check=False)
        with patch.object(self.gui, "run_cinegain", return_value=(result.stdout, result.returncode)) as run:
            window = self.gui.CinegainMainWindow(self.router)
            try:
                self.assertNotIn(("start", "--route"), [call.args for call in run.call_args_list])
                self.assertFalse(window.btn_power.isEnabled())
                self.assertFalse(window.tray_toggle_action.isEnabled())
                self.assertEqual(window.lbl_status.text(), self.gui.t("unknown_status"))
            finally:
                window.timer.stop()
                window.meter_timer.stop()
                window.tray.hide()
                window.deleteLater()
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(json.loads(result.stdout)["active"])

    def test_page_keys_from_focused_program_row(self):
        from PyQt6.QtTest import QTest

        sidebar = self.win.sidebar
        first, second = sidebar.order
        self.win.show()
        sidebar.select(first)
        sidebar.rows[first].setFocus()
        self.app.processEvents()
        for key, target in ((self.gui.Qt.Key.Key_PageDown, second),
                            (self.gui.Qt.Key.Key_PageUp, first)):
            QTest.keyClick(self.app.focusWidget(), key)
            self.assertEqual(self.win.selected_key, target)
            self.assertTrue(sidebar.rows[target].hasFocus())

    def test_page_down_walks_standard_then_programs(self):
        down = self.gui.QKeyEvent(self.gui.QKeyEvent.Type.KeyPress, self.gui.Qt.Key.Key_PageDown,
                                  self.gui.Qt.KeyboardModifier.NoModifier)
        self.win.keyPressEvent(down)
        self.assertEqual(self.win.selected_key, self.win.sidebar.order[0])
        up = self.gui.QKeyEvent(self.gui.QKeyEvent.Type.KeyPress, self.gui.Qt.Key.Key_PageUp,
                                self.gui.Qt.KeyboardModifier.NoModifier)
        self.win.keyPressEvent(up)
        self.assertIsNone(self.win.selected_key)

    def test_back_to_standard(self):
        self.win.sidebar.select(CHROME)
        self.slider().setValue(150)
        self.win.flush_pending_params()
        self.win.refresh_apps()
        self.win.back_to_standard()
        self.win.refresh_apps()
        self.assertIsNone(AppRegistry().get(CHROME)["params"])
        self.assertEqual(self.sink_of(self.chrome), "cinegain_sink")
        self.assertEqual(self.slider().value(), 80)

    def test_switch_in_row_takes_program_out(self):
        self.win.sidebar.rows[SPOTIFY].switch.click()
        self.win.refresh_apps()
        self.assertFalse(AppRegistry().get(SPOTIFY)["enabled"])
        self.assertEqual(self.sink_of(self.spotify), tr.HW)

    def test_brand_link_click_opens_url_and_does_not_crash(self):
        from unittest import mock
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        label = self.gui.brand_label(self.gui.BRAND_ROTESON_PATH, 50, "t", "https://roteson.de")
        label.show()
        with mock.patch.object(self.gui.QDesktopServices, "openUrl", return_value=True) as opened:
            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        self.assertEqual(opened.call_args[0][0].toString(), "https://roteson.de")
        with mock.patch.object(self.gui.QDesktopServices, "openUrl", return_value=False), \
                mock.patch.object(self.gui.subprocess, "Popen") as popen:
            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        self.assertEqual(popen.call_args[0][0], ["xdg-open", "https://roteson.de"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
