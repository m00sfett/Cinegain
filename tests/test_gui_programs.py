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

import cinegain_bypass as bypass_state  # noqa: E402
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
        paths.write_json_atomic(paths.CONFIG_FILE, {"lang": "en", "view_level": "advanced"})
        slot = {k: 1.0 for k in ("input_db",)}
        paths.write_json_atomic(paths.USER_PRESETS_FILE, {"user_1": {"name": "Music", "params": slot}})
        paths.write_json_atomic(paths.STATE_FILE, {
            "preset": "custom", "mode": "fixed", "params": {"input_db": 8.0},
            "sets": {"fixed": {"input_db": 8.0}, "dynamic": {"input_db": 8.0}}})
        bypass_state.set_on(False)
        self.addCleanup(bypass_state.set_on, False)
        self.calls = []

        def fake_run(*args):
            self.calls.append(args)
            if args[:1] == ("batch",):
                # like `cinegain batch`: values go into Standard's (new) mode and are saved
                import cinegain_modes as modes
                st = modes.load_standard()
                mode = args[args.index("--mode") + 1] if "--mode" in args else st["mode"]
                modes.save_standard(mode, modes.apply_values(mode, st["sets"], json.loads(args[1])))
                return "", 0
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
        self.win.set_mode("dynamic")
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

    def test_keyboard_and_mode_layout(self):
        """Everything is always visible (no "more settings"); fixed mode shows the five shared sliders."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.win.show()
        self.slider().setFocus()
        self.app.processEvents()
        QTest.keyClick(self.slider(), Qt.Key.Key_Tab)
        self.assertFalse(self.slider().hasFocus())
        self.assertFalse(hasattr(self.win, "details"))
        self.assertEqual(len(self.win.user_preset_buttons), 6)
        self.assertTrue(all(b.isVisible() for b in self.win.preset_buttons.values()))
        self.assertTrue(all(b.isVisible() for b in self.win.user_preset_buttons.values()))
        visible = [n for n in self.win.slider_order if self.win.sliders[n]["row_frame"].isVisible()]
        self.assertEqual(visible, ["input_db", "dialog_db", "bass_db", "treble_db", "side_db"])
        self.win.set_focused_slider(0)
        self.win.navigate_slider(0, 1)
        self.assertEqual(self.win.slider_order[self.win.focused_slider_idx], "dialog_db")
        self.win.navigate_slider(self.win.focused_slider_idx, 100)  # F6: other column, same row
        self.assertEqual(self.win.slider_order[self.win.focused_slider_idx], "treble_db")
        self.win.set_mode("dynamic")
        visible = [n for n in self.win.slider_order if self.win.sliders[n]["row_frame"].isVisible()]
        self.assertEqual(len(visible), 12)
        self.assertEqual(visible[:2], ["input_db", "dialog_db"])

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
        self.win.apply_factory_preset("boost_12db")
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
            self.assertTrue(bypass_state.is_on())  # the CLI reads the same state (#33)
            # Loading a Standard preset while off must not switch processing back on: the CLI
            # keeps the flag (tested in test_modes_volume), the GUI does not push bypass itself.
            pushed.clear()
            self.win.apply_standard_preset("boost_12db")
            self.assertNotIn({"bypass": 0.0}, pushed)
            self.assertTrue(self.router.bypass)
            self.assertTrue(bypass_state.is_on())
            self.win.toggle_power()
            self.assertFalse(self.router.bypass)
            self.assertFalse(bypass_state.is_on())
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

    def test_mode_switch_on_a_follower_gives_it_its_own_setting(self):
        """Q15a: switching the mode of a program that follows Standard gives it its own setting."""
        self.win.sidebar.select(CHROME)
        self.assertFalse(self.win.btn_mode_dynamic.isChecked())
        self.win.set_mode("dynamic")
        entry = AppRegistry().get(CHROME)
        self.assertEqual(entry["mode"], "dynamic")
        self.assertEqual(entry["sets"]["fixed"]["input_db"], 8.0)  # copied from Standard
        self.assertTrue(self.win.btn_mode_dynamic.isChecked())
        self.assertTrue(self.win.sliders["comp_ratio"]["row_frame"].isVisibleTo(self.win))
        self.assertIsNone(AppRegistry().get(SPOTIFY)["params"])  # others untouched

    def test_each_mode_keeps_its_values_and_presets_bring_their_mode(self):
        """Q17b + Q6b: separate value sets; a film preset switches to dynamic, a boost step to fixed."""
        self.win.sidebar.select(CHROME)
        self.slider().setValue(120)
        self.win.flush_pending_params()
        self.win.apply_factory_preset("night_mode")
        self.assertEqual(self.win._mode, "dynamic")
        self.assertEqual(self.slider().value(), 120)  # night mode input_db 12
        self.assertEqual(AppRegistry().get(CHROME)["params"]["comp_ratio"], 6.0)
        self.assertEqual(self.win.preset_buttons["night_mode"].property("presetActive"), "true")
        self.win.set_mode("fixed")
        self.assertEqual(self.slider().value(), 120)  # fixed set: the 12.0 typed earlier
        self.slider().setValue(30)
        self.win.flush_pending_params()
        self.win.set_mode("dynamic")
        self.assertEqual(self.slider().value(), 120)  # dynamic set untouched by the fixed edit
        self.win.apply_factory_preset("boost_9db")
        self.assertEqual((self.win._mode, self.slider().value()), ("fixed", 90))
        self.assertEqual(AppRegistry().get(CHROME)["params"]["comp_ratio"], 1.0)

    def test_user_slot_remembers_its_mode(self):
        self.win.sidebar.select(CHROME)
        self.win.set_mode("dynamic")
        self.win._overwrite_user_slot("user_2")
        self.assertEqual(self.win.user_presets["user_2"]["mode"], "dynamic")
        self.assertEqual(self.win.user_preset_buttons["user_2"].property("beta"), "true")  # dashed outline
        self.win.set_mode("fixed")
        self.win.on_user_preset_clicked("user_2")
        self.assertEqual(AppRegistry().get(CHROME)["mode"], "dynamic")

    def test_hidden_volume_bar_badge_and_header_hint(self):
        """Q10a+b: badge on the row, hint in the header, bar with the full reset."""
        from cinegain_volume import NodeVolume, VolumeGuard
        nodes = [NodeVolume(5, "Spotify", "spotify", "Spotify", 0.004)]
        resets = []
        self.router.guard = VolumeGuard(scanner=lambda: list(nodes), setter=lambda i, v: resets.append((i, v)) or True)
        self.win.is_active = False  # Spotify not routed -> only reported
        self.win.refresh_apps()
        self.assertTrue(self.win.hidden_bar.isVisibleTo(self.win))
        self.assertIn("Spotify -48 dB", self.win.lbl_hidden_bar.text())
        row = self.win.sidebar.rows[SPOTIFY]
        self.assertTrue(row.hidden.isVisibleTo(self.win))
        self.assertEqual(row.hidden.text(), "-48 dB")
        self.win.sidebar.select(SPOTIFY)
        self.win.update_edit_header()
        self.assertTrue(self.win.btn_reset_hidden.isVisibleTo(self.win))
        import cinegain_volume
        orig = cinegain_volume.clean_stream_properties
        cinegain_volume.clean_stream_properties = lambda: (0, None)
        try:
            self.win.full_volume_reset(only=SPOTIFY)
        finally:
            cinegain_volume.clean_stream_properties = orig
        self.assertIn(5, self.router.guard.fades)  # follows Standard: fades in, no jump

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
        self.win.apply_standard_preset("boost_12db")
        self.assertIn(("preset", "boost_12db"), self.calls)
        self.assertEqual(self.slider().value(), before)
        self.assertIsNone(AppRegistry().get(SPOTIFY)["params"])

    def test_startup_keeps_standard_selected_even_when_one_program_plays(self):
        # #60: auto-selecting a lone playing program caused accidental own 0 dB sinks.
        self.win.refresh_apps()
        self.assertIs(self.win.selected_key, self.gui.STANDARD)
        self.win.apply_factory_preset("clean_linear")
        self.assertIsNone(AppRegistry().get(SPOTIFY)["params"])
        self.assertIsNone(AppRegistry().get(CHROME)["params"])

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

    # -- 0.5.0 ---------------------------------------------------------------------
    def test_steps_keys_wheel_and_buttons(self):
        Qt = self.gui.Qt
        mods = Qt.KeyboardModifier
        self.assertEqual(self.gui.step_units("input_db", mods.NoModifier), 10)
        self.assertEqual(self.gui.step_units("input_db", mods.ShiftModifier), 1)
        self.assertEqual(self.gui.step_units("input_db", mods.ControlModifier), 50)
        self.assertEqual(self.gui.step_units("comp_ratio", mods.NoModifier), 5)
        s = self.slider()
        s.setValue(0)
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtCore import QEvent
        s.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, mods.ControlModifier))
        self.assertEqual(s.value(), 50)
        s.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, mods.ShiftModifier))
        self.assertEqual(s.value(), 49)
        minus, plus = self.win.sliders["input_db"]["step_buttons"]
        plus.click()
        self.assertEqual(s.value(), 59)
        minus.click()
        minus.click()
        self.assertEqual(s.value(), 39)
        self.assertTrue(plus.autoRepeat())
        s.setValue(145)
        plus.click()  # clamped at the top of the normal scale
        self.assertEqual(s.value(), 150)

    def test_right_click_resets_to_neutral(self):
        from PyQt6.QtTest import QTest
        Qt = self.gui.Qt
        s = self.slider("treble_db")
        s.setValue(-55)
        QTest.mouseClick(s, Qt.MouseButton.RightButton)
        self.assertEqual(s.value(), 0)
        self.win.set_mode("dynamic")
        c = self.slider("comp_ratio")
        c.setValue(60)
        c.resetRequested.emit()
        self.assertEqual(c.value(), 10)  # ratio 1:1
        self.slider("lim_ceiling_db").resetRequested.emit()
        self.assertEqual(self.slider("lim_ceiling_db").value(), -10)

    def test_view_levels_show_and_hide_and_are_remembered(self):
        self.win.set_view_level("simple")
        w = self.win
        visible = lambda wid: wid.isVisibleTo(w)  # noqa: E731
        self.assertFalse(visible(w.grp_adv))
        self.assertFalse(visible(w.sliders["dialog_db"]["row_frame"]))
        self.assertTrue(visible(w.sliders["input_db"]["row_frame"]))
        self.assertFalse(visible(w.film_row[0]))
        self.assertFalse(visible(w.btn_mode_dynamic))
        self.assertFalse(w.tabs.tabBar().isVisibleTo(w))
        self.assertTrue(visible(w.btn_overdrive))  # Q17: everywhere
        self.assertEqual(paths.config_get("view_level"), "simple")
        self.assertEqual(paths.config_get("lang"), "en")  # other settings survive
        w.set_view_level("advanced")
        self.assertTrue(visible(w.grp_adv))
        self.assertTrue(w.tabs.isTabVisible(1))
        self.assertFalse(w.tabs.isTabVisible(2))
        w.set_view_level("pro")
        self.assertTrue(w.tabs.isTabVisible(2))
        self.assertIn("pid", w.lbl_edit_state.text())

    def test_simple_view_names_a_dynamic_target(self):
        self.win.sidebar.select(CHROME)
        self.win.set_mode("dynamic")
        self.win.set_view_level("simple")
        self.assertIn(self.gui.t("simple_dynamic_hint"), self.win.lbl_edit_state.text())

    def test_heartbeat_follows_the_filter_state_of_the_cli(self):
        """#33: `cinegain filter off` in a terminal shows up in the GUI; a stopped filter clears the flag."""
        self.win.on_heartbeat()
        self.assertFalse(self.router.bypass)
        bypass_state.set_on(True)
        self.win.on_heartbeat()
        self.assertTrue(self.router.bypass)
        self.assertFalse(self.win.processing)
        bypass_state.set_on(False)
        self.win.on_heartbeat()
        self.assertFalse(self.router.bypass)
        self.assertTrue(self.win.processing)

    def test_header_reset_button_never_means_everything(self):
        """#32: with Standard selected only=None used to run the global full reset."""
        from cinegain_registry import make_key as mk
        seen = []
        self.router.full_volume_reset = lambda standard=None, clean_file=True, only=None: seen.append(only) or {}
        self.win.sidebar.select(CHROME)
        self.win.btn_reset_hidden.click()
        self.assertEqual(seen, [CHROME])
        self.win.sidebar.select(None)
        self.assertIsNone(self.win.selected_key)
        self.win.btn_reset_hidden.click()
        self.assertEqual(seen, [CHROME, mk("cinegain_output", "")])
        self.assertNotIn(None, seen)
        self.win.full_volume_reset()  # the global buttons (sidebar, tray) still reset everything
        self.assertEqual(seen[-1], None)

    def test_overdrive_widens_the_scale_and_off_caps_at_once(self):
        import cinegain_overdrive as od
        s = self.slider()
        self.assertEqual(s.maximum(), 150)
        self.win.set_overdrive(True)
        try:
            self.assertTrue(od.is_on())
            self.assertEqual(s.maximum(), 400)
            self.assertEqual(s.danger_from, 150)
            self.win.sidebar.select(CHROME)
            s.setValue(300)
            self.win.flush_pending_params()
            self.assertEqual(AppRegistry().get(CHROME)["params"]["input_db"], 30.0)
            self.assertIn("#FF8A1F", self.win.sliders["input_db"]["val_lbl"].styleSheet())
        finally:
            self.win.set_overdrive(False)
        self.assertFalse(od.is_on())
        self.assertEqual(s.maximum(), 150)
        self.assertEqual(s.value(), 150)
        self.assertEqual(AppRegistry().get(CHROME)["params"]["input_db"], 15.0)

    def _start_after_overdrive_session(self, chrome_db, standard_db):
        """Stored values of a session that ran with overdrive; the GUI then restarts (flag cleared)."""
        import cinegain_modes as modes
        import cinegain_overdrive as od
        import cinegain_router
        reg = AppRegistry()
        od.set_on(True)
        reg.set_setting(CHROME, "fixed", {"fixed": {"input_db": chrome_db}})
        modes.save_standard("fixed", {"fixed": {"input_db": standard_db}})
        od.set_on(False)
        self.sinks.live.update({"standard", profile_id_for(CHROME)})
        pushed = []
        orig = (cinegain_router.push_params, cinegain_router.find_node_id)
        cinegain_router.push_params = lambda node, params: pushed.append(dict(params)) or True
        cinegain_router.find_node_id = lambda name: 1
        self.calls.clear()
        try:
            win = self.gui.open_main_window(Router(registry=AppRegistry(), sinks=self.sinks, pulse=self.pulse))
        finally:
            cinegain_router.push_params, cinegain_router.find_node_id = orig
        win.timer.stop()
        win.meter_timer.stop()
        win.tray.hide()
        win.deleteLater()
        self.app.processEvents()
        return pushed

    def test_start_pushes_the_capped_values_to_filters_that_kept_running(self):
        """#30: after a GUI restart the saved values are capped; surviving filters must follow at once."""
        pushed = self._start_after_overdrive_session(chrome_db=28.0, standard_db=30.0)
        self.assertEqual(AppRegistry().get(CHROME)["params"]["input_db"], 15.0)
        self.assertEqual(paths.read_json(paths.STATE_FILE)["params"]["input_db"], 15.0)
        self.assertIn(("batch", "{}"), [c[:2] for c in self.calls])  # Standard filter gets the capped state
        self.assertEqual([p["input_db"] for p in pushed], [15.0])  # the program's own sink, capped

    def test_start_pushes_nothing_when_no_value_was_capped(self):
        pushed = self._start_after_overdrive_session(chrome_db=12.0, standard_db=9.0)
        self.assertEqual(pushed, [])
        self.assertFalse([c for c in self.calls if c[0] == "batch"])

    def test_overdrive_needs_the_confirmed_dialog(self):
        import cinegain_overdrive as od
        Dlg = self.gui.OverdriveDialog
        orig = Dlg.exec
        Dlg.exec = lambda self_: self.gui.QDialog.DialogCode.Rejected
        try:
            self.win.toggle_overdrive()
            self.assertFalse(od.is_on())
            self.assertFalse(self.win.btn_overdrive.isChecked())
        finally:
            Dlg.exec = orig
        dlg = Dlg(self.win)
        self.assertFalse(dlg.btn_confirm.isEnabled())
        dlg.check.setChecked(True)
        self.assertTrue(dlg.btn_confirm.isEnabled())

    def test_sidebar_width_is_adjustable_and_remembered(self):
        w = self.win
        self.assertEqual(w._sidebar_width(), 280)
        w.resize(1400, 900)
        w.show()
        self.app.processEvents()
        w.splitter.setSizes([400, 800])
        w._save_sidebar_width()
        self.assertGreaterEqual(paths.config_get("sidebar_width"), 300)
        w.reset_sidebar_width()
        self.assertEqual(paths.config_get("sidebar_width"), 280)
        self.assertGreater(self.win.sidebar.maximumWidth(), 1000)

    def test_beta_marks_are_dashed_not_symbols(self):
        for b in self.win.film_row:
            self.assertEqual(b.property("beta"), "true")
            self.assertNotIn("β", b.text())
        self.assertEqual(self.win.btn_mode_dynamic.property("beta"), "true")
        self.assertIn("Beta", self.win.btn_mode_dynamic.text())

    def test_header_shows_hidden_volume_even_at_0_db(self):
        self.win.update_edit_header()
        self.assertTrue(self.win.lbl_hidden.isVisibleTo(self.win))
        self.assertEqual(self.win.lbl_hidden.text(), self.gui.t("hidden_none"))
        self.assertFalse(self.win.btn_reset_hidden.isVisibleTo(self.win))

    def test_analysis_panel_lists_sets_and_resets(self):
        import cinegain_volume as vol
        rows = [
            {"id": 5, "kind": "stream", "label": "Spotify", "key": SPOTIFY, "node_name": "Spotify",
             "hidden": 0.25, "normal": 1.0, "mute": False, "editable": True, "stored": 0.25},
            {"id": 9, "kind": "hardware", "label": "Soundcard", "key": "hw", "node_name": "alsa",
             "hidden": 1.0, "normal": 0.8, "mute": False, "editable": False, "stored": None},
        ]
        sets = []
        panel = self.gui.AnalysisPanel(scanner=lambda: rows, setter=lambda *a: sets.append(a) or True,
                                       history=lambda key=None: [])
        panel.refresh()
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertIn("−12.0 dB", panel.table.item(0, 2).text())
        self.assertIsNotNone(panel.table.cellWidget(0, 6))   # reset button for the stream
        self.assertIsNone(panel.table.cellWidget(1, 6))      # hardware: read-only
        self.assertEqual(panel.table.item(1, 5).text(), self.gui.t("analysis_readonly"))
        panel.table.cellWidget(0, 6).click()
        self.assertEqual(sets[-1][:2], (5, 1.0))
        panel.set_pro(True)
        self.assertFalse(panel.table.isColumnHidden(7))
        panel.refresh_history(force=True)
        self.assertEqual(panel.timeline.item(0, 2).text(), self.gui.t("analysis_no_events"))
        del vol

    def test_auto_reset_switch_reaches_the_guard(self):
        from cinegain_volume import VolumeGuard
        self.router.guard = VolumeGuard(scanner=lambda: [], setter=lambda *a: True)
        self.win.set_auto_reset(False)
        self.assertFalse(self.router.guard.auto)
        self.assertFalse(paths.config_get("volume_auto_reset"))

    def test_meter_strip_hides_leveler_and_comp_in_fixed_mode(self):
        """#43 U1: fixed mode shows only IN and LIMIT; dynamic shows all four."""
        strip = self.win.meter_strip
        names = lambda: [name for _i, name, _lo, _hi in strip.shown_bars()]  # noqa: E731
        self.assertEqual(names(), ["IN", "LIMIT"])
        self.assertEqual([i for i, *_ in strip.shown_bars()], [0, 3])  # values keep their index
        self.win.set_mode("dynamic")
        self.assertEqual(names(), ["IN", "LEVELER", "COMP", "LIMIT"])
        self.win.set_mode("fixed")
        self.assertEqual(names(), ["IN", "LIMIT"])
        strip.set_values((-12.0, 6.0, -3.0, -1.0), "x")
        self.assertFalse(strip.grab().isNull())  # paints with and without values

    def test_subtitle_is_one_line_and_keyboard_help_sits_in_a_tooltip(self):
        """#43 U2."""
        for lang in ("en", "de"):
            self.win.change_language(lang)
            sub = self.win.lbl_subtitle.text()
            self.assertEqual(sub, self.gui.t("app_subtitle"))
            self.assertNotIn("↑", sub)
            self.assertFalse(self.win.lbl_subtitle.wordWrap())
            self.assertIn("↑↓", self.win.lbl_kbd_help.toolTip())
            self.assertIn(self.gui.t("kbd_program"), self.win.lbl_kbd_help.toolTip())
        self.win.change_language("en")

    def test_hero_gradient_has_no_red_at_the_quiet_end(self):
        """#43 U3: red means alarm/off; the hero fill starts in dark gold."""
        rule = self.gui.CI_STYLESHEET.split("QSlider#hero_slider::sub-page:horizontal", 1)[1].split("}", 1)[0]
        self.assertIn("qlineargradient", rule)
        gradient = rule.split("border:")[0]
        for red in ("#D01822", "#B11218", "#FF5546", "#6E1516"):
            self.assertNotIn(red, gradient)

    def test_empty_slots_get_translated_default_names_saved_names_stay(self):
        """#43 U4: only the stored default "User N" is translated; typed names are kept."""
        stored = {"user_1": {"name": "Music", "params": {"input_db": 1.0}},
                  "user_3": {"name": "User 3", "params": None},
                  "user_4": {"name": "User 2", "params": None},   # typed by the user, not slot 4's default
                  "user_5": {"name": "Kino", "params": None}}
        paths.write_json_atomic(paths.USER_PRESETS_FILE, stored)
        self.win.user_presets = self.win._load_user_presets()
        self.win.change_language("de")
        text = {sid: b.text().replace("&&", "&") for sid, b in self.win.user_preset_buttons.items()}
        self.assertIn("Music", text["user_1"])
        self.assertEqual(text["user_2"], "Platz 2 (frei)")      # no entry at all
        self.assertEqual(text["user_3"], "Platz 3 (frei)")      # stored default
        self.assertEqual(text["user_4"], "User 2 (frei)")       # a name the user chose stays
        self.assertEqual(text["user_5"], "Kino (frei)")
        self.assertEqual(paths.read_json(paths.USER_PRESETS_FILE), stored)  # nothing rewritten
        self.win.change_language("en")
        self.assertEqual(self.win.user_preset_buttons["user_3"].text(), "User 3 (empty)")


@unittest.skipUnless(HAVE_QT, "PyQt6 not installed")
class TestHeartbeatCost(unittest.TestCase):
    """
    #38: what one idle heartbeat costs. Every process the GUI would start is counted; the
    sound server is faked. Before: `cinegain status` (a Python process with two pw-dumps of
    its own), three pactl calls and one pw-dump = 5 processes seen by the GUI, 7 in all.
    """

    SINK_INPUT = [{"index": 7, "sink": 53, "corked": False, "mute": False,
                   "properties": {"application.name": "Spotify", "application.process.binary": "spotify",
                                  "node.name": "Spotify", "object.id": "20"},
                   "volume": {"front-left": {"value_percent": "100%"}}}]

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("cinegain_gui_cost", os.path.join(BASE_DIR, "ui", "cinegain-gui.py"))
        cls.gui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gui)
        cls.app = cls.gui.QApplication.instance() or cls.gui.QApplication([])

    def setUp(self):
        import cinegain_inproc
        import cinegain_pwdump
        import test_modes_volume as tmv
        from cinegain_pulse import PulseControl
        from cinegain_volume import VolumeGuard
        for p in paths.USER_STATE_FILES:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        paths.write_json_atomic(paths.CONFIG_FILE, {"lang": "en", "view_level": "advanced"})
        self.spawned = []
        self.sink_inputs = []
        dump = json.dumps(tmv.fake_dump()).encode()

        def fake_process(args, **kwargs):
            self.spawned.append(os.path.basename(args[0]))
            text = kwargs.get("text")
            if args[0] == "pw-dump":
                return dump
            if args[:3] == ["pactl", "-f", "json"]:
                out = json.dumps(self.sink_inputs)
            elif args[:3] == ["pactl", "list", "sinks"]:
                out = "53\thw_sink\tPipeWire\n54\tcinegain_sink\tPipeWire\n"
            elif args[:2] == ["pactl", "get-default-sink"]:
                out = "hw_sink\n"
            else:
                out = ""
            return out if text else out.encode()

        def fake_run(args, **kwargs):
            out = fake_process(args, **kwargs)
            return subprocess.CompletedProcess(args, 0, out, "")

        cli = cinegain_inproc.load(self.gui.BIN_PATH)
        self._saved = (subprocess.run, subprocess.check_output, cli.standard_sinks, cli.push_params)

        class NoSinks:
            def running(self):
                return set()
        cli.standard_sinks = NoSinks
        cli.push_params = lambda node, params: True
        subprocess.run, subprocess.check_output = fake_run, fake_process
        self.cli = cli
        self.clock = tr.Clock(1_000_000.0)
        self.guard = VolumeGuard(clock=self.clock)
        self.router = Router(registry=AppRegistry(clock=self.clock), sinks=tr.FakeSinks(tr.FakePulse()),
                             pulse=PulseControl(), clock=self.clock, guard=self.guard)
        self.win = self.gui.CinegainMainWindow(self.router)
        self.win.timer.stop()
        self.win.meter_timer.stop()
        self.app.processEvents()
        self.inproc, self.pwdump = cinegain_inproc, cinegain_pwdump

    def tearDown(self):
        subprocess.run, subprocess.check_output, self.cli.standard_sinks, self.cli.push_params = self._saved
        self.win.hide()
        self.win.tray.hide()
        self.win.deleteLater()
        self.app.processEvents()

    def test_an_idle_heartbeat_starts_one_pw_dump_and_two_pactl_and_no_cinegain(self):
        self.spawned.clear()
        self.win.on_heartbeat()
        self.assertEqual(sorted(self.spawned), ["pactl", "pactl", "pw-dump"])   # was 5 (7 with the CLI's own)
        self.assertEqual(self.win.lbl_status.text(), self.gui.t("status_active"))   # status still arrives ...
        self.assertTrue(self.win.is_active)
        self.assertEqual(self.win.btn_power.isEnabled(), True)
        self.assertEqual(self.win._std_mode, "fixed")                              # ... with its values

    def test_status_failure_still_reads_as_unknown(self):
        real = self.pwdump.fetch
        self.pwdump.fetch = lambda: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "pw-dump"))
        try:
            self.win.on_heartbeat()
        finally:
            self.pwdump.fetch = real
        self.assertFalse(self.win.btn_power.isEnabled())
        self.assertEqual(self.win.lbl_status.text(), self.gui.t("unknown_status"))

    def test_tick_relaxes_in_the_tray_or_when_nothing_plays_and_speeds_up_when_playing(self):
        busy, idle = self.gui.HEARTBEAT_BUSY_MS, self.gui.HEARTBEAT_IDLE_MS
        self.assertGreaterEqual(idle, 3000)
        self.win.show()
        self.win.on_heartbeat()
        self.assertEqual(self.win.timer.interval(), idle)         # window open, nothing plays
        self.sink_inputs = self.SINK_INPUT
        self.win.on_heartbeat()
        self.assertEqual(self.win.timer.interval(), busy)         # open and playing
        self.win.hide()
        self.win.on_heartbeat()
        self.assertEqual(self.win.timer.interval(), idle)         # same playback, but in the tray

    def test_meter_timer_runs_only_while_the_window_is_open(self):
        self.assertFalse(self.win.meter_timer.isActive())
        self.win.show()
        self.assertTrue(self.win.meter_timer.isActive())
        self.win.hide()
        self.assertFalse(self.win.meter_timer.isActive())
        self.win.show()
        self.assertTrue(self.win.meter_timer.isActive())          # and it comes back

    def test_fades_keep_running_with_a_hidden_window(self):
        """Ear protection: a raise that started must reach its target, whatever the window does."""
        self.win.hide()
        writes = []
        self.guard.add_fade("probe", -30.0, 0.0, lambda db, final: writes.append((db, final)) or True,
                            now=self.clock.t, what="probe")
        self.assertTrue(self.win.fade_timer.isActive())
        for _ in range(60):
            self.clock.t += 0.1
            self.win._step_fades()
        self.assertEqual(writes[-1], (0.0, True))                 # landed on the target
        self.assertEqual(self.guard.fades, {})
        self.assertTrue(self.win.fade_timer.isActive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
