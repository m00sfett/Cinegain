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
from cinegain_registry import AppRegistry, make_key  # noqa: E402
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

    def test_tray_preset_always_targets_standard(self):
        self.win.sidebar.select(SPOTIFY)
        before = self.slider().value()
        self.win.apply_standard_preset("boost_20db")
        self.assertIn(("preset", "boost_20db"), self.calls)
        self.assertEqual(self.slider().value(), before)
        self.assertIsNone(AppRegistry().get(SPOTIFY)["params"])

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

    def test_user_slots_no_longer_save_in_the_background(self):
        self.win.on_user_preset_clicked("user_1")  # load into Standard
        self.slider().setValue(170)
        self.win.flush_pending_params()
        self.assertEqual(paths.read_json(paths.USER_PRESETS_FILE)["user_1"]["params"]["input_db"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
