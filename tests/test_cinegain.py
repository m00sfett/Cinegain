#!/usr/bin/env python3
"""
Test suite for Cinegain LADSPA DSP and PipeWire Filter Chain integration.
Never plays any audio tones to hardware speakers.
"""
import unittest
import os
import subprocess
import json

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_PATH = os.path.join(BASE_DIR, "bin", "cinegain")
SO_PATH = os.path.join(BASE_DIR, "src", "cinegain.so")


class TestCinegain(unittest.TestCase):
    def test_01_binary_exists(self):
        self.assertTrue(os.path.exists(SO_PATH), f"cinegain.so not found at {SO_PATH}")
        self.assertTrue(os.path.isfile(SO_PATH))

    def test_02_lifecycle_presets_and_batch(self):
        # Stop any lingering instance
        subprocess.run([BIN_PATH, "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Status should be INACTIVE
        res = subprocess.run([BIN_PATH, "status", "--json"], capture_output=True, text=True)
        st = json.loads(res.stdout)
        self.assertFalse(st["active"])

        # Setting pure +25 dB boost preset while OFF must NOT start the filter
        res = subprocess.run([BIN_PATH, "preset", "boost_25db"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        res = subprocess.run([BIN_PATH, "status", "--json"], capture_output=True, text=True)
        st = json.loads(res.stdout)
        self.assertFalse(st["active"])
        self.assertEqual(st.get("preset"), "boost_25db")
        self.assertAlmostEqual(st["params"]["input_db"], 25.0, places=1)
        self.assertAlmostEqual(st["params"]["leveler"], 0.0, places=1)

        # Start filter: must restore the saved +25.0 dB parameter onto the live PipeWire node
        res = subprocess.run([BIN_PATH, "start"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("started successfully", res.stdout)

        res = subprocess.run([BIN_PATH, "status", "--json"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        st = json.loads(res.stdout)
        self.assertTrue(st["active"])
        self.assertAlmostEqual(st["params"]["input_db"], 25.0, places=1)
        self.assertEqual(st["params"]["bypass"], 0.0)

        # Test batch command and auto-enabling leveler when lev_max_boost_db > 0
        res = subprocess.run(
            [BIN_PATH, "batch", json.dumps({"input_db": 10.0, "lev_max_boost_db": 14.0}), "--preset-id", "user_1"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        res = subprocess.run([BIN_PATH, "status", "--json"], capture_output=True, text=True)
        st = json.loads(res.stdout)
        self.assertAlmostEqual(st["params"]["input_db"], 10.0, places=1)
        self.assertAlmostEqual(st["params"]["lev_max_boost_db"], 14.0, places=1)
        self.assertAlmostEqual(st["params"]["leveler"], 1.0, places=1)
        self.assertEqual(st.get("preset"), "user_1")

        # Restore default dialog_boost preset for user
        subprocess.run([BIN_PATH, "preset", "dialog_boost"], capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
