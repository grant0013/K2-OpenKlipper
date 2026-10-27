import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras"
    / "prtouch_open" / "tap_select.py"
)
SPEC = importlib.util.spec_from_file_location("tap_select", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def run(taps, samples, tolerance, max_touches, noisy):
    """collect_taps() against a scripted list of contact heights."""
    feed = iter(taps)
    seen = []
    lifts = []

    def tap(collected):
        seen.append(list(collected))
        return next(feed)

    result = MODULE.collect_taps(
        tap, samples, tolerance, max_touches, noisy,
        between=lambda: lifts.append(1))
    return result, seen, lifts


class TapSelectTest(unittest.TestCase):
    def test_single_tap_is_todays_behaviour(self):
        (median, collected, best), seen, lifts = run(
            [0.114063, 9.0], 1, 0.1, 1, 0)
        self.assertEqual(median, 0.114063)
        self.assertEqual(collected, [0.114063])
        self.assertEqual(best, [0.114063])
        self.assertEqual(lifts, [])

    def test_all_good(self):
        (median, collected, best), _, lifts = run(
            [0.110, 0.112, 0.111], 3, 0.010, 10, 3)
        self.assertAlmostEqual(median, 0.111)
        self.assertEqual(len(collected), 3)
        self.assertEqual(len(lifts), 2)

    def test_one_outlier_is_dropped(self):
        (median, collected, best), _, _ = run(
            [0.110, 0.160, 0.112, 0.111], 3, 0.010, 10, 3)
        self.assertAlmostEqual(median, 0.111)
        self.assertEqual(collected, [0.110, 0.160, 0.112, 0.111])
        self.assertEqual(best, [0.110, 0.111, 0.112])

    def test_follow_up_sees_previous_taps(self):
        _, seen, _ = run([0.110, 0.160, 0.112, 0.111], 3, 0.010, 10, 3)
        self.assertEqual(seen[0], [])
        self.assertEqual(seen[3], [0.110, 0.160, 0.112])

    def test_sliding_window_forgets_old_taps(self):
        # Window is samples + noisy = 2: early good taps leave the window.
        (median, collected, best), _, _ = run(
            [0.100, 0.200, 0.300, 0.400, 0.405], 2, 0.010, 10, 0)
        self.assertEqual(best, [0.400, 0.405])
        self.assertAlmostEqual(median, 0.4025)

    def test_too_noisy_raises(self):
        with self.assertRaises(MODULE.TapSelectError) as ctx:
            run([0.10, 0.13, 0.16, 0.19, 0.22, 0.25], 3, 0.010, 6, 3)
        self.assertEqual(len(ctx.exception.collected), 6)
        self.assertIn("after 6 touches", str(ctx.exception))

    def test_plain_spread_check_matches_old_home(self):
        # max_touches == samples and no noisy taps: N taps, abort on spread.
        (median, collected, _), _, _ = run([0.1, 0.15, 0.12], 3, 0.1, 3, 0)
        self.assertAlmostEqual(median, 0.12)
        with self.assertRaises(MODULE.TapSelectError):
            run([0.1, 0.25, 0.12], 3, 0.1, 3, 0)

    def test_bad_settings(self):
        with self.assertRaises(ValueError):
            run([0.1], 3, 0.1, 2, 0)
        with self.assertRaises(ValueError):
            run([0.1], 0, 0.1, 2, 0)

    def test_best_subset(self):
        self.assertEqual(
            MODULE.best_subset([0.5, 0.1, 0.12, 0.3], 2), [0.1, 0.12])
        self.assertIsNone(MODULE.best_subset([0.1], 2))


class HotZCompTest(unittest.TestCase):
    def test_zero_rate_is_todays_offset(self):
        self.assertEqual(MODULE.hot_z_comp(0.04, 0.0, 260.0, 220.0), 0.04)

    def test_hotter_print_raises_nozzle(self):
        # 30C hotter at the K2 Plus rate: 0.025 mm less lowering.
        comp = MODULE.hot_z_comp(0.04, 0.00083, 250.0, 220.0)
        self.assertAlmostEqual(comp, 0.04 - 0.0249)
        self.assertLess(comp, 0.04)

    def test_cooler_print_lowers_nozzle(self):
        comp = MODULE.hot_z_comp(0.04, 0.00083, 200.0, 220.0)
        self.assertAlmostEqual(comp, 0.04 + 0.0166)

    def test_matches_k2_plus_formula(self):
        # Theirs labels contact at z_offset - rate*(T_print - T_touch), so
        # commanding Z=0 holds the cold-measured tip that far above the bed.
        rate, t_print, t_touch = 0.00083, 250.0, 140.0
        theirs_raise = rate * (t_print - t_touch)
        ref = 220.0
        ours_raise_vs_ref = 0.04 - MODULE.hot_z_comp(0.04, rate, t_print, ref)
        self.assertAlmostEqual(
            ours_raise_vs_ref, theirs_raise - rate * (ref - t_touch))


if __name__ == "__main__":
    unittest.main()
