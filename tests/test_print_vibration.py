"""tools/hi/print_vibration.py: move sampling, vibration RMS and the
by-speed report on synthetic data."""
import contextlib
import csv
import importlib.util
import io
import math
import pathlib
import random
import tempfile
import types
import unittest

PATH = pathlib.Path(__file__).parents[1] / "tools" / "hi" / "print_vibration.py"
SPEC = importlib.util.spec_from_file_location("print_vibration", PATH)
pv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pv)

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


class MovesTest(unittest.TestCase):
    def test_cruise_and_accel(self):
        m = pv.Moves()
        # accelerate 0 -> 100 over 0.1 s along X, then cruise 1 s
        m.add([[0., .1, 0., 1000., [0, 0, 0], [1, 0, 0]],
               [.1, 1., 100., 0., [5, 0, 0], [1, 0, 0]]])
        s = m.sample(.05)
        self.assertAlmostEqual(s[0], 50.)
        self.assertTrue(s[2])
        speed, vec, frac = pv.window_motion(m, .3, .55)
        self.assertAlmostEqual(speed, 100.)
        self.assertAlmostEqual(vec[0], 100.)
        self.assertEqual(frac, 0.)
        self.assertIsNone(pv.window_motion(m, -1., -.5))

    def test_gap_after_last_move_is_standstill(self):
        m = pv.Moves()
        m.add([[0., .1, 50., 0., [0, 0, 0], [0, 1, 0]]])
        self.assertEqual(m.sample(.5)[0], 0.)


@unittest.skipUnless(HAVE_NUMPY, "numpy")
class VibrationTest(unittest.TestCase):
    def test_peak_and_trend_removed(self):
        fs, f = 400., 60.
        s = [(k / fs, 500. * k / fs + 100 * math.sin(2 * math.pi * f * k / fs),
              0., 0.) for k in range(100)]
        rx, ry, rz, hz, amp = pv.window_vibration(s)
        self.assertAlmostEqual(rx, 100 / math.sqrt(2), delta=8)
        self.assertAlmostEqual(hz, f, delta=4)


@unittest.skipUnless(HAVE_NUMPY, "numpy")
class ReportTest(unittest.TestCase):
    def test_flags_the_resonant_speed(self):
        rnd = random.Random(1)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        w = csv.writer(tmp)
        w.writerow(pv.FIELDS + ["y_rms_x", "y_rms_y", "y_rms_z",
                                "y_peak_hz", "y_peak_amp"])
        for i in range(3000):
            speed = rnd.choice(range(20, 200, 5)) + 2.
            bad = 120 <= speed < 125
            rms = (600 if bad else 200) + rnd.uniform(-20, 20)
            w.writerow([i * .25, 1, speed, 0., speed, 0., 0.,
                        rms / 2, rms / 2, rms / 2, speed / 2., 10])
        tmp.close()
        args = types.SimpleNamespace(file=tmp.name, window=.25, bin=5,
                                     min_count=12, flag=1.4, png=None,
                                     max_accel=.3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pv.cmd_report(args)
        text = out.getvalue()
        self.assertIn("bed, along Y, 120-125 mm/s", text)
        self.assertIn("GT2 belt tooth", text)
        self.assertNotIn("along Y, 60-65", text.split("Problem speeds")[1])


if __name__ == "__main__":
    unittest.main()
