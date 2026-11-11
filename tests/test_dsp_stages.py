#!/usr/bin/env python3
"""
DSP stage tests for src/cinegain.c: high-pass off switch, fast leveler cut and the
meter file the GUI reads. Builds the plugin into a temp dir; needs gcc only.
"""
import ctypes as C
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RATE = 48000
BLOCK = 256


class Desc(C.Structure):
    _fields_ = [
        ("UniqueID", C.c_ulong), ("Label", C.c_char_p), ("Properties", C.c_int),
        ("Name", C.c_char_p), ("Maker", C.c_char_p), ("Copyright", C.c_char_p),
        ("PortCount", C.c_ulong), ("PortDescriptors", C.c_void_p),
        ("PortNames", C.POINTER(C.c_char_p)), ("PortRangeHints", C.c_void_p),
        ("ImplementationData", C.c_void_p),
        ("instantiate", C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_ulong)),
        ("connect_port", C.CFUNCTYPE(None, C.c_void_p, C.c_ulong, C.c_void_p)),
        ("activate", C.CFUNCTYPE(None, C.c_void_p)),
        ("run", C.CFUNCTYPE(None, C.c_void_p, C.c_ulong)),
        ("run_adding", C.c_void_p), ("set_run_adding_gain", C.c_void_p),
        ("deactivate", C.c_void_p),
        ("cleanup", C.CFUNCTYPE(None, C.c_void_p)),
    ]


NEUTRAL = dict(
    bypass=0, input_db=0, highpass_hz=10, bass_db=0, dialog_db=0, dialog_hz=2500, dialog_q=1,
    treble_db=0, mid_db=0, side_db=0, leveler=0, lev_target_db=-18, lev_max_boost_db=0,
    lev_max_cut_db=0, lev_speed_s=2, lev_gate_db=-55, comp_thresh_db=0, comp_ratio=1,
    comp_knee_db=0, comp_attack_ms=20, comp_release_ms=250, makeup_db=0,
    lim_ceiling_db=-0.2, lim_release_ms=80,
)


def rms_db(x):
    return 20 * math.log10(max(math.sqrt(sum(v * v for v in x) / len(x)), 1e-12))


class StageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("gcc") is None:
            raise unittest.SkipTest("gcc is required")
        cls._tmp = tempfile.TemporaryDirectory()
        so = os.path.join(cls._tmp.name, "cinegain.so")
        subprocess.run(["gcc", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
                        os.path.join(BASE_DIR, "src", "cinegain.c"), "-o", so, "-lm"], check=True)
        cls.lib = C.CDLL(so)
        cls.lib.ladspa_descriptor.restype = C.POINTER(Desc)
        cls.lib.ladspa_descriptor.argtypes = [C.c_ulong]
        cls.dptr = cls.lib.ladspa_descriptor(0)
        cls.desc = cls.dptr.contents
        cls.names = [cls.desc.PortNames[i].decode() for i in range(cls.desc.PortCount)]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, signal, cleanup=True, **params):
        p = dict(NEUTRAL)
        p.update(params)
        n = len(signal)
        inp = (C.c_float * n)(*signal)
        out_l, out_r = (C.c_float * n)(), (C.c_float * n)()
        ctl = {i: C.c_float(float(p.get(name, 0.0))) for i, name in enumerate(self.names) if i >= 4}
        h = self.desc.instantiate(C.cast(self.dptr, C.c_void_p), RATE)
        for i, c in ctl.items():
            self.desc.connect_port(h, i, C.addressof(c))
        self.desc.activate(h)
        for pos in range(0, n, BLOCK):
            cnt = min(BLOCK, n - pos)
            for port, arr in ((0, inp), (1, inp), (2, out_l), (3, out_r)):
                self.desc.connect_port(h, port, C.addressof(arr) + pos * 4)
            self.desc.run(h, cnt)
        if cleanup:
            self.desc.cleanup(h)
            return list(out_l)
        return list(out_l), h

    @staticmethod
    def _sine(freq, level_db, seconds):
        amp = 10 ** (level_db / 20)
        return [amp * math.sin(2 * math.pi * freq * t / RATE) for t in range(int(RATE * seconds))]

    def test_highpass_at_minimum_is_transparent(self):
        x = self._sine(20, -30, 1.0)
        off = self._run(x, highpass_hz=10)
        on = self._run(x, highpass_hz=20)
        self.assertAlmostEqual(rms_db(off[RATE // 2:]) - rms_db(x[RATE // 2:]), 0.0, delta=0.1)
        self.assertLess(rms_db(on[RATE // 2:]) - rms_db(x[RATE // 2:]), -2.5)

    def test_leveler_pulls_back_quickly_after_a_loud_jump(self):
        quiet, loud = self._sine(1000, -40, 6), self._sine(1000, -10, 3)
        out = self._run(quiet + loud, leveler=1, lev_target_db=-16, lev_max_boost_db=15,
                        lev_max_cut_db=6, lev_speed_s=4)
        start = len(quiet)
        # 1.5 s after the jump the output must be close to the target, not +6 dB above input
        seg = out[start + int(1.5 * RATE): start + int(1.75 * RATE)]
        self.assertLess(rms_db(seg), -16 + 4.0)

    def test_meter_file_is_written_and_removed(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CINEGAIN_METER_DIR"] = d
            try:
                _, h = self._run(self._sine(1000, -20, 0.5), cleanup=False)
                path = os.path.join(d, f"meter-{os.getpid()}.bin")
                with open(path, "rb") as f:
                    magic, seq, level, leveler, gr, limit = struct.unpack("<IIffff", f.read(24))
                self.assertEqual(magic, 0x434E4D31)
                self.assertGreater(seq, 0)
                self.assertAlmostEqual(level, -20.0, delta=0.5)
                self.desc.cleanup(h)
                self.assertFalse(os.path.exists(path))
            finally:
                del os.environ["CINEGAIN_METER_DIR"]

    def _open(self):
        """New activated instance with neutral controls; returns (handle, controls)."""
        ctl = {i: C.c_float(float(NEUTRAL.get(name, 0.0))) for i, name in enumerate(self.names) if i >= 4}
        h = self.desc.instantiate(C.cast(self.dptr, C.c_void_p), RATE)
        for i, c in ctl.items():
            self.desc.connect_port(h, i, C.addressof(c))
        self.desc.activate(h)
        return h, ctl

    def _feed(self, h, signal):
        n = len(signal)
        inp = (C.c_float * n)(*signal)
        out_l, out_r = (C.c_float * n)(), (C.c_float * n)()
        for pos in range(0, n, BLOCK):
            cnt = min(BLOCK, n - pos)
            for port, arr in ((0, inp), (1, inp), (2, out_l), (3, out_r)):
                self.desc.connect_port(h, port, C.addressof(arr) + pos * 4)
            self.desc.run(h, cnt)

    def test_meter_file_survives_cleanup_of_an_older_instance(self):
        """#35: a graph reset creates the new instance before the old one is cleaned up."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["CINEGAIN_METER_DIR"] = d
            path = os.path.join(d, f"meter-{os.getpid()}.bin")

            def level():
                with open(path, "rb") as f:
                    return struct.unpack("<IIffff", f.read(24))[2]
            try:
                old, old_ctl = self._open()
                self._feed(old, self._sine(1000, -30, 0.1))
                new, new_ctl = self._open()
                self._feed(new, self._sine(1000, -10, 0.1))
                self.assertAlmostEqual(level(), -10.0, delta=0.5)
                # the old instance keeps running for a moment: it must not write into the new file
                self._feed(old, self._sine(1000, -30, 0.1))
                self.assertAlmostEqual(level(), -10.0, delta=0.5)
                self.desc.cleanup(old)
                self.assertTrue(os.path.exists(path), "cleanup of the old instance removed the new file")
                self._feed(new, self._sine(1000, -20, 0.1))
                self.assertAlmostEqual(level(), -20.0, delta=0.5)
                self.desc.cleanup(new)
                self.assertEqual(os.listdir(d), [])  # owner removes it, no temp files left
            finally:
                del os.environ["CINEGAIN_METER_DIR"]

    def _run_steps(self, steps, base=None):
        """
        Runs (seconds, params[, signal]) steps on one instance; returns the output per
        step. Without a signal a step plays a -40 dBFS 1 kHz sine.
        """
        p = dict(NEUTRAL)
        p.update(base or {})
        ctl = {i: C.c_float(float(p.get(name, 0.0))) for i, name in enumerate(self.names) if i >= 4}
        h = self.desc.instantiate(C.cast(self.dptr, C.c_void_p), RATE)
        for i, c in ctl.items():
            self.desc.connect_port(h, i, C.addressof(c))
        self.desc.activate(h)
        outs = []
        for step in steps:
            seconds, params = step[0], step[1]
            for i, name in enumerate(self.names):
                if name in params:
                    ctl[i].value = float(params[name])
            x = step[2] if len(step) > 2 else self._sine(1000, -40, seconds)
            n = len(x)
            inp = (C.c_float * n)(*x)
            out_l, out_r = (C.c_float * n)(), (C.c_float * n)()
            for pos in range(0, n, BLOCK):
                cnt = min(BLOCK, n - pos)
                for port, arr in ((0, inp), (1, inp), (2, out_l), (3, out_r)):
                    self.desc.connect_port(h, port, C.addressof(arr) + pos * 4)
                self.desc.run(h, cnt)
            outs.append(list(out_l))
        self.desc.cleanup(h)
        return outs

    def test_boost_rises_slowly_on_a_fresh_instance(self):
        out = self._run(self._sine(1000, -40, 3.0), input_db=20)
        early = rms_db(out[int(0.45 * RATE):int(0.55 * RATE)]) - (-43.0)  # -40 dBpk sine = -43 dB rms
        late = rms_db(out[int(2.6 * RATE):int(2.9 * RATE)]) - (-43.0)
        self.assertAlmostEqual(early, 5.0, delta=1.0)   # 10 dB/s
        self.assertAlmostEqual(late, 20.0, delta=0.3)

    def test_turning_down_is_immediate_and_up_again_ramps(self):
        settled, down, up = self._run_steps([(3.0, {"input_db": 20}), (0.2, {"input_db": 0}),
                                             (0.3, {"input_db": 10})])
        self.assertAlmostEqual(rms_db(settled[-RATE // 10:]) + 43.0, 20.0, delta=0.3)
        # right after the 5 ms look-ahead delay the level is already down
        self.assertAlmostEqual(rms_db(down[RATE // 100: RATE // 20]) + 43.0, 0.0, delta=0.5)
        self.assertLess(rms_db(up[RATE // 100: RATE // 20]) + 43.0, 2.0)

    def test_bypass_fades_the_boost_in_again(self):
        _, _, back = self._run_steps([(3.0, {"input_db": 12}), (0.1, {"bypass": 1}),
                                      (0.2, {"bypass": 0})])
        self.assertLess(rms_db(back[RATE // 100: RATE // 20]) + 43.0, 2.0)

    # Night-mode style leveler: quiet dialog at -45 dBFS gets the full +20 dB boost.
    NIGHT = dict(leveler=1, lev_target_db=-14, lev_max_boost_db=20, lev_max_cut_db=10,
                 lev_speed_s=1, lev_gate_db=-60)

    def test_bypass_return_plays_no_stale_lookahead_audio(self):
        """#28: after bypass the look-ahead line must not replay old processed audio."""
        quiet = self._sine(1000, -45, 6.0)
        silence = [0.0] * (RATE // 10)
        boosted, _, back = self._run_steps([(6.0, {}, quiet), (0.1, {"bypass": 1}, silence),
                                            (0.1, {"bypass": 0}, silence)], base=self.NIGHT)
        self.assertGreater(rms_db(boosted[-RATE // 10:]), -45 - 3 + 15)  # leveler really boosted
        self.assertEqual(max(abs(v) for v in back), 0.0)  # silence in, silence out

    def test_bypass_return_starts_the_leveler_at_0_db(self):
        """#28: the leveler boost fades in again instead of being there at once."""
        quiet = self._sine(1000, -45, 6.0)
        _, _, back = self._run_steps([(6.0, {}, quiet), (0.1, {"bypass": 1}, quiet[:RATE // 10]),
                                      (0.3, {"bypass": 0}, quiet[:int(0.3 * RATE)])], base=self.NIGHT)
        # 10-20 ms after switching back on: about the dry level, not +20 dB (rms of -45 dBpk = -48)
        self.assertLess(rms_db(back[RATE // 100: RATE // 50]) + 48.0, 2.0)
        # and it still glides up afterwards: the leveler is not switched off for good
        self.assertGreater(rms_db(back[-RATE // 20:]) + 48.0, 3.0)

    def test_bypass_return_crossfades_without_a_gap(self):
        """#28: the dry signal hands over to the delayed processed one without a dropout."""
        x = self._sine(1000, -20, 0.6)
        _, _, back = self._run_steps([(0.5, {}, x[:RATE // 2]), (0.05, {"bypass": 1}, x[:RATE // 20]),
                                      (0.05, {"bypass": 0}, x[RATE // 20: RATE // 10])])
        amp = 10 ** (-20 / 20)
        max_step = 2 * math.pi * 1000 / RATE * amp  # steepest sample-to-sample step of the sine
        steps = [abs(back[i] - back[i - 1]) for i in range(1, 3 * RATE // 200)]
        self.assertLess(max(steps), 1.5 * max_step)
        # no dropout while the look-ahead line refills: every 1 ms window keeps level
        for w in range(0, 3 * RATE // 200, RATE // 1000):
            self.assertGreater(rms_db(back[w: w + RATE // 1000]), -20 - 3 - 6, w)

    def test_meter_reports_the_remaining_ramp(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CINEGAIN_METER_DIR"] = d
            try:
                _, h = self._run(self._sine(1000, -40, 0.5), cleanup=False, input_db=15)
                with open(os.path.join(d, f"meter-{os.getpid()}.bin"), "rb") as f:
                    raw = f.read(28)
                ramp = struct.unpack("<IIfffff", raw)[6]
                self.assertAlmostEqual(ramp, 10.0, delta=0.5)
                self.desc.cleanup(h)
            finally:
                del os.environ["CINEGAIN_METER_DIR"]

    def assertSameSamples(self, a, b, msg=None):
        """List equality without unittest's (very slow) diff of long sample lists."""
        self.assertEqual(len(a), len(b), msg)
        diff = next((i for i, (u, v) in enumerate(zip(a, b)) if u != v), None)
        if diff is not None:
            self.fail(f"samples differ from index {diff}: {a[diff]!r} != {b[diff]!r} {msg or ''}")

    def test_control_values_are_clamped_and_finite(self):
        """#37: out-of-range controls act like the port bound, NaN/Inf like the default."""
        x = self._sine(1000, 0, 0.3)  # full scale, so every ceiling setting matters
        same = self.assertSameSamples
        # above the upper bound: ceiling +6 dB acts like 0 dB (limiter, not the hard clip)
        hot = self._sine(1000, -6, 1.5)
        same(self._run(hot, makeup_db=12, lim_ceiling_db=6), self._run(hot, makeup_db=12, lim_ceiling_db=0))
        same(self._run(x, dialog_db=6, dialog_hz=20000), self._run(x, dialog_db=6, dialog_hz=8000))
        same(self._run(x, comp_thresh_db=20, comp_ratio=4), self._run(x, comp_thresh_db=0, comp_ratio=4))
        # below the lower bound: ceiling -40 dB acts like -24 dB
        same(self._run(x, lim_ceiling_db=-40), self._run(x, lim_ceiling_db=-24))
        # below the lower bound: q 0 acts like 0.2, ratio 0.1 like 1
        same(self._run(x, dialog_db=6, dialog_q=0), self._run(x, dialog_db=6, dialog_q=0.2))
        same(self._run(x, dialog_db=6, dialog_q=-5), self._run(x, dialog_db=6, dialog_q=0.2))
        same(self._run(x, comp_thresh_db=-30, comp_ratio=0.1), self._run(x, comp_thresh_db=-30))
        # non-finite values fall back to the default instead of poisoning the filters
        for bad in (math.nan, math.inf, -math.inf):
            out = self._run(x, bass_db=bad)
            self.assertTrue(all(math.isfinite(v) for v in out), bad)
            same(out, self._run(x, bass_db=0), bad)
            same(self._run(x, input_db=bad), self._run(x, input_db=0), bad)
            same(self._run(x, lim_ceiling_db=bad), self._run(x, lim_ceiling_db=-1), bad)
        # values inside the range are untouched
        self.assertTrue(self._run(x, lim_ceiling_db=-3) != self._run(x, lim_ceiling_db=-1))


if __name__ == "__main__":
    unittest.main()
