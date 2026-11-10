#!/usr/bin/env python3
"""
Regression test for the look-ahead limiter ceiling in src/cinegain.c.

Builds the LADSPA plugin into a temporary directory and drives it through
ctypes with synthetic signals at +25 dB input gain. Needs gcc, but no
PipeWire and no audio hardware.
"""
import ctypes as C
import math
import os
import random
import shutil
import subprocess
import tempfile
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_PATH = os.path.join(BASE_DIR, "src", "cinegain.c")

RATE = 48000
BLOCK = 256
# Measured overshoot is about 0.2 dB. The bound leaves margin for platform
# float differences while still catching a real regression.
MAX_OVERSHOOT_DB = 0.5


class Desc(C.Structure):
    """Mirror of the LADSPA 1.1 LADSPA_Descriptor struct (fields we use)."""

    _fields_ = [
        ("UniqueID", C.c_ulong),
        ("Label", C.c_char_p),
        ("Properties", C.c_int),
        ("Name", C.c_char_p),
        ("Maker", C.c_char_p),
        ("Copyright", C.c_char_p),
        ("PortCount", C.c_ulong),
        ("PortDescriptors", C.c_void_p),
        ("PortNames", C.POINTER(C.c_char_p)),
        ("PortRangeHints", C.c_void_p),
        ("ImplementationData", C.c_void_p),
        ("instantiate", C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_ulong)),
        ("connect_port", C.CFUNCTYPE(None, C.c_void_p, C.c_ulong, C.c_void_p)),
        ("activate", C.CFUNCTYPE(None, C.c_void_p)),
        ("run", C.CFUNCTYPE(None, C.c_void_p, C.c_ulong)),
        ("run_adding", C.c_void_p),
        ("set_run_adding_gain", C.c_void_p),
        ("deactivate", C.c_void_p),
        ("cleanup", C.CFUNCTYPE(None, C.c_void_p)),
    ]


def db_to_lin(db):
    return 10.0 ** (db / 20.0)


def lin_to_db(value):
    return 20.0 * math.log10(max(value, 1e-12))


class LimiterCeilingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("gcc") is None:
            raise unittest.SkipTest("gcc is required to build the DSP plugin")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.so_path = os.path.join(cls._tmp.name, "cinegain.so")
        subprocess.run(
            ["gcc", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
             SRC_PATH, "-o", cls.so_path, "-lm"],
            check=True,
        )
        cls.lib = C.CDLL(cls.so_path)
        cls.lib.ladspa_descriptor.restype = C.POINTER(Desc)
        cls.lib.ladspa_descriptor.argtypes = [C.c_ulong]
        cls.desc_ptr = cls.lib.ladspa_descriptor(0)
        cls.desc = cls.desc_ptr.contents
        cls.names = [cls.desc.PortNames[i].decode() for i in range(cls.desc.PortCount)]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _controls(self, ceiling_db):
        """Control values: all DSP stages neutral except +25 dB input and the limiter."""
        values = {
            "bypass": 0, "input_db": 25.0, "highpass_hz": 10, "bass_db": 0,
            "dialog_db": 0, "dialog_hz": 1000, "dialog_q": 1, "treble_db": 0,
            "mid_db": 0, "side_db": 0, "leveler": 0, "lev_target_db": -20,
            "lev_max_boost_db": 0, "lev_max_cut_db": 0, "lev_speed_s": 4,
            "lev_gate_db": -55, "comp_thresh_db": 0, "comp_ratio": 1,
            "comp_knee_db": 0, "comp_attack_ms": 10, "comp_release_ms": 100,
            "makeup_db": 0, "lim_ceiling_db": ceiling_db, "lim_release_ms": 100,
        }
        controls = {}
        for index, name in enumerate(self.names):
            if index >= 4:  # ports 0-3 are the stereo audio ports
                # Meter outputs (not listed above) start at 0 and are written by the plugin.
                controls[index] = C.c_float(float(values.get(name, 0.0)))
        return controls

    def _process(self, signal, ceiling_db):
        """Run a mono signal through both channels and return the left output."""
        n = len(signal)
        in_l = (C.c_float * n)(*signal)
        in_r = (C.c_float * n)(*signal)
        out_l = (C.c_float * n)()
        out_r = (C.c_float * n)()
        controls = self._controls(ceiling_db)

        handle = self.desc.instantiate(C.cast(self.desc_ptr, C.c_void_p), RATE)
        for index, control in controls.items():
            self.desc.connect_port(handle, index, C.addressof(control))
        self.desc.activate(handle)

        pos = 0
        while pos < n:
            count = min(BLOCK, n - pos)
            self.desc.connect_port(handle, 0, C.addressof(in_l) + pos * 4)
            self.desc.connect_port(handle, 1, C.addressof(in_r) + pos * 4)
            self.desc.connect_port(handle, 2, C.addressof(out_l) + pos * 4)
            self.desc.connect_port(handle, 3, C.addressof(out_r) + pos * 4)
            self.desc.run(handle, count)
            pos += count

        self.desc.cleanup(handle)
        return list(out_l)

    def _assert_ceiling(self, samples, ceiling_db):
        ceiling = db_to_lin(ceiling_db)
        peak = max(abs(v) for v in samples)
        # The hard stop guarantees the output never exceeds full scale.
        self.assertLessEqual(peak, 1.0 + 1e-6, "output exceeds 0 dBFS")
        overs = [abs(v) for v in samples if abs(v) > ceiling + 1e-6]
        if overs:
            overshoot_db = lin_to_db(max(overs) / ceiling)
            self.assertLessEqual(
                overshoot_db, MAX_OVERSHOOT_DB,
                f"overshoot {overshoot_db:+.2f} dB above {ceiling_db} dBFS ceiling",
            )

    def test_01_steady_sine_at_plus_25_db(self):
        signal = [0.5 * math.sin(2 * math.pi * 1000 * t / RATE) for t in range(RATE // 2)]
        self._assert_ceiling(self._process(signal, -0.5), -0.5)

    def test_02_quiet_passage_then_sudden_burst(self):
        quiet = [0.02 * math.sin(2 * math.pi * 1000 * t / RATE) for t in range(RATE // 4)]
        burst = [0.5 * math.sin(2 * math.pi * 1000 * t / RATE) for t in range(RATE // 4)]
        self._assert_ceiling(self._process(quiet + burst, -1.0), -1.0)

    def test_03_random_bursts_at_varying_levels(self):
        rng = random.Random(1)
        signal = []
        for _ in range(40):
            amplitude = rng.choice([0.01, 0.05, 0.2, 0.5, 0.7])
            length = rng.randint(200, 4000)
            signal += [amplitude * rng.uniform(-1.0, 1.0) for _ in range(length)]
        self._assert_ceiling(self._process(signal, -0.5), -0.5)


if __name__ == "__main__":
    unittest.main()
