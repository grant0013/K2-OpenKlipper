# Checks for prtouch_open/scrub.py (PRTOUCH_SCRUB helpers).
# Portions adapted from Jacob10383's K2 Plus custom firmware (https://github.com/Jacob10383/k2-plus-custom-firmware, extras/prtouch.py), GPLv3.

import importlib.util
import pathlib
import random
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras"
    / "prtouch_open" / "scrub.py"
)
SPEC = importlib.util.spec_from_file_location("scrub", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def run(end_readings, z_start, z_centre, z_end, max_passes=3,
        shorten_done=0.01):
    """run_passes() against a scripted list of (z_start, z_end) re-taps."""
    feed = iter(end_readings)
    scrubbed = []
    result = MODULE.run_passes(
        lambda zs, zc, ze: scrubbed.append((zs, zc, ze)),
        lambda: next(feed),
        z_start, z_centre, z_end, max_passes, shorten_done)
    return result, scrubbed


class HoldScalingTest(unittest.TestCase):
    def test_ratio_one_is_unchanged(self):
        self.assertEqual(
            MODULE.scaled_holds(4000, 10000, 500, 1.0), (4000, 10000, 500))
        # Even values outside the clamp ranges pass through untouched.
        self.assertEqual(
            MODULE.scaled_holds(60000, 200000, 0, 1.0), (60000, 200000, 0))

    def test_k2_firm_press(self):
        # Our pres_tri_hold 4000, 10000, 500 at the K2 Plus ratio 2.5.
        self.assertEqual(
            MODULE.scaled_holds(4000, 10000, 500, 2.5), (10000, 25000, 500))

    def test_clamped_to_cfg_ranges(self):
        self.assertEqual(
            MODULE.scaled_holds(40000, 90000, 7, 2.5), (50000, 100000, 7))

    def test_max_never_below_min(self):
        lo, hi, _add = MODULE.scaled_holds(30000, 30000, 0, 3.0)
        self.assertEqual(lo, 50000)
        self.assertGreaterEqual(hi, lo)

    def test_bad_ratio(self):
        with self.assertRaises(ValueError):
            MODULE.scaled_holds(4000, 10000, 500, 0.0)


class DetectionTest(unittest.TestCase):
    def test_deflection_is_median_of_pairs(self):
        normal = [0.100, 0.102, 0.098]
        firm = [0.050, 0.070, 0.090]  # differences 0.05, 0.032, 0.008
        self.assertAlmostEqual(MODULE.deflection(normal, firm), 0.032)

    def test_deflection_uses_absolute_difference(self):
        self.assertAlmostEqual(
            MODULE.deflection([0.0, 0.0, 0.0], [0.04, -0.04, 0.05]), 0.04)

    def test_one_outlier_pair_ignored(self):
        normal = [0.10, 0.10, 0.10]
        firm = [0.09, 0.09, 0.50]
        self.assertAlmostEqual(MODULE.deflection(normal, firm), 0.01)

    def test_deflection_needs_pairs(self):
        with self.assertRaises(ValueError):
            MODULE.deflection([], [])
        with self.assertRaises(ValueError):
            MODULE.deflection([0.1, 0.1], [0.1])

    def test_tab_depth(self):
        self.assertEqual(
            MODULE.choose_depth(0.060, 0.035, 0.15, 0.05), (True, 0.15))

    def test_no_tab_depth(self):
        self.assertEqual(
            MODULE.choose_depth(0.010, 0.035, 0.15, 0.05), (False, 0.05))

    def test_threshold_itself_is_no_tab(self):
        self.assertEqual(
            MODULE.choose_depth(0.035, 0.035, 0.15, 0.05), (False, 0.05))


class LineTest(unittest.TestCase):
    def test_ends_within_limit(self):
        MODULE.check_ends(0.10, 0.55)

    def test_ends_too_far_apart(self):
        with self.assertRaises(MODULE.ScrubError):
            MODULE.check_ends(0.10, 0.61)

    def test_centre_too_far(self):
        MODULE.check_centre(0.0, 0.4, 0.0)
        with self.assertRaises(MODULE.ScrubError):
            MODULE.check_centre(0.0, 0.6, 0.0)

    def test_round_trip_follows_slope(self):
        path = MODULE.round_trip(
            (155., 265.), (165., 265.), 0.10, 0.12, 0.20, 0.05)
        xs = [p[0] for p in path]
        self.assertEqual(xs, [155., 160., 165., 160., 155.])
        self.assertTrue(all(p[1] == 265. for p in path))
        zs = [round(p[2], 6) for p in path]
        self.assertEqual(zs, [0.05, 0.07, 0.15, 0.07, 0.05])

    def test_clear_z_above_highest_contact(self):
        self.assertAlmostEqual(MODULE.clear_z(0.1, 0.3, 0.2, 2.0), 2.3)

    def test_pick_y_inside_band(self):
        rng = random.Random(1)
        for _ in range(100):
            y = MODULE.pick_y(264.5, 266.5, rng)
            self.assertTrue(264.5 <= y <= 266.5)


class PassLoopTest(unittest.TestCase):
    def test_clean_after_one_pass(self):
        (passes, shortened, zs, zc, ze), scrubbed = run(
            [(0.095, 0.105)], 0.10, 0.11, 0.11)
        self.assertEqual(passes, 1)
        self.assertAlmostEqual(shortened, 0.005)
        self.assertEqual(len(scrubbed), 1)
        # The centre keeps its first offset (+0.005) from the ends' average.
        self.assertAlmostEqual(zc, 0.105)

    def test_dropping_then_clean(self):
        (passes, shortened, zs, zc, ze), scrubbed = run(
            [(0.07, 0.07), (0.065, 0.066)], 0.10, 0.10, 0.10)
        self.assertEqual(passes, 2)
        self.assertAlmostEqual(shortened, 0.0045)
        self.assertEqual(scrubbed[1], (0.07, 0.07, 0.07))

    def test_stops_at_max_passes(self):
        (passes, shortened, _zs, _zc, _ze), scrubbed = run(
            [(0.08, 0.08), (0.06, 0.06), (0.04, 0.04), (0.0, 0.0)],
            0.10, 0.10, 0.10, max_passes=3)
        self.assertEqual(passes, 3)
        self.assertAlmostEqual(shortened, 0.02)
        self.assertEqual(len(scrubbed), 3)

    def test_rising_contact_counts_as_done(self):
        (passes, shortened, _zs, _zc, _ze), _scrubbed = run(
            [(0.12, 0.12)], 0.10, 0.10, 0.10)
        self.assertEqual(passes, 1)
        self.assertLess(shortened, 0.)

    def test_each_pass_uses_latest_contacts(self):
        _result, scrubbed = run(
            [(0.05, 0.07), (0.05, 0.07)], 0.10, 0.12, 0.10)
        self.assertEqual(scrubbed[0], (0.10, 0.12, 0.10))
        zs, zc, ze = scrubbed[1]
        self.assertEqual((zs, ze), (0.05, 0.07))
        self.assertAlmostEqual(zc, 0.08)

    def test_needs_a_pass(self):
        with self.assertRaises(ValueError):
            run([], 0.1, 0.1, 0.1, max_passes=0)


if __name__ == "__main__":
    unittest.main()
