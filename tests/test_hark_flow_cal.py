import importlib.util
import pathlib
import unittest

# The module imports nothing from Klipper at the top level
PATH = pathlib.Path(__file__).parents[1] / "klippy" / "extras" / \
    "hark_flow_cal.py"
spec = importlib.util.spec_from_file_location("hark_flow_cal", PATH)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

# Gauge force per flow step from the chute ramps on Grant's K2, CR-PETG
# slot B (tools/flow/flow_test.py, 11-03)
RAMP_250 = [(6, 108190), (8, 132574), (10, 158018), (12, 187019),
            (14, 228334), (16, 266898), (18, 298770), (20, 335376),
            (22, 367265)]
RAMP_260 = [(6, 68316), (8, 91175), (10, 118781), (12, 149620),
            (14, 184729), (16, 223721), (18, 262963), (20, 300705),
            (22, 333976)]
RAMP_250_3S = [(6, 93806), (8, 118382), (10, 147518), (12, 175206),
               (14, 210173), (16, 259539), (18, 290065), (20, 335929)]


# The first automatic run (FLOW_CAL_STATIC 11-03): the rest level was read
# 0.6 s after the prime, so every force is understated; the module now
# relaxes 3 s first. Kept as the "slowly steepening, no clean knee" case.
AUTO_RUN_1 = [(8, 117829), (10, 145003), (12, 168514), (14, 204682),
              (16, 234911), (18, 272316), (20, 313170), (22, 353247),
              (24, 391882)]

KNEE = 4.   # module default knee_percent


def from8(ramp):
    """The ramp as the module runs it by default: from 8 mm^3/s."""
    return [p for p in ramp if p[0] >= 8]


# Third and fourth automatic runs (3 s steps, first step 2 s longer, rest
# relaxed; run 4 also takes the rest drift out): every step on the line,
# the bend from 16, stopped past 7%
AUTO_RUN_3 = [(8, 122673), (10, 151964), (12, 184795), (14, 218258),
              (16, 255212), (18, 297402)]
AUTO_RUN_4 = [(8, 122518), (10, 155378), (12, 187618), (14, 222508),
              (16, 260403), (18, 303149), (20, 342601)]
# Fifth run: the 8-10 step came in low, so a two-point line made 12 read
# +5.0% and the ramp stopped at 12 with a cap of 8.5. Three points are
# needed before anything is judged.
AUTO_RUN_5 = [(8, 124846), (10, 152935), (12, 190112)]
# Sixth run: a gentle bend, 3.6 / 4.3 / 4.8% at 14 / 16 / 18
AUTO_RUN_6 = [(8, 110208), (10, 144887), (12, 179792), (14, 222324),
              (16, 259973), (18, 297826)]

MARGIN = 3.  # module default


class PickCapTest(unittest.TestCase):
    def test_auto_runs_agree_within_half_a_step(self):
        # Reading the crossing between steps keeps two runs on the same
        # filament together; at whole steps they were a step apart
        cap3, knee3, rows = fc.pick_cap(AUTO_RUN_3, KNEE, MARGIN)
        cap4, knee4, rows = fc.pick_cap(AUTO_RUN_4, KNEE, MARGIN)
        cap6, knee6, rows = fc.pick_cap(AUTO_RUN_6, KNEE, MARGIN)
        self.assertEqual((cap3, cap4, cap6), (13.0, 13.5, 12.0))
        self.assertAlmostEqual(knee3, 16.2, delta=0.15)
        self.assertAlmostEqual(knee4, 16.6, delta=0.15)
        self.assertAlmostEqual(knee6, 15.2, delta=0.15)

    def test_live_ramp_does_not_stop_on_its_newest_step(self):
        # While the ramp runs the newest step cannot confirm itself
        cap, knee, rows = fc.pick_cap(AUTO_RUN_6[:5], KNEE, MARGIN,
                                      final=False)
        self.assertIsNone(knee)
        cap, knee, rows = fc.pick_cap(AUTO_RUN_6[:5], KNEE, MARGIN,
                                      final=True)
        self.assertIsNotNone(knee)

    def test_manual_ramps_bend_earlier(self):
        # 4 s steps with a dwell between them read the high flows higher,
        # so the crossing lands lower: still PETG in the low teens
        for ramp in (RAMP_250, RAMP_260, RAMP_250_3S):
            cap, knee, rows = fc.pick_cap(from8(ramp), KNEE, MARGIN)
            self.assertTrue(13. <= knee <= 15., knee)
            self.assertTrue(10. <= cap <= 12., cap)

    def test_three_points_are_not_judged_yet(self):
        cap, knee, rows = fc.pick_cap(AUTO_RUN_5, KNEE, MARGIN)
        self.assertIsNone(knee)
        self.assertIsNone(cap)
        self.assertEqual([ex for f, v, ex in rows], [0., 0., 0.])

    def test_one_step_over_the_line_waits_for_the_next(self):
        base = [(8, 100), (10, 130), (12, 160), (14, 190)]
        # 16 reads high alone, 18 is back on the line: no knee
        cap, knee, rows = fc.pick_cap(base + [(16, 240), (18, 250)], KNEE, 1.)
        self.assertIsNone(knee)
        # 16 high and 18 higher: knee between 14 and 16
        cap, knee, rows = fc.pick_cap(base + [(16, 240), (18, 300)], KNEE, 1.)
        self.assertTrue(14. < knee < 16., knee)
        # The ramp ends on the first high step: taken as the knee
        cap, knee, rows = fc.pick_cap(base + [(16, 240)], KNEE, 1.)
        self.assertTrue(14. < knee < 16., knee)

    def test_biased_auto_run_still_caps_low(self):
        cap, knee, rows = fc.pick_cap(AUTO_RUN_1, KNEE, MARGIN)
        self.assertLessEqual(cap, 14.)

    def test_forces_follow_a_drifting_rest(self):
        levels = [(8, 10., -1000.), (10, 20., -1100.), (12, 30., -1200.)]
        # Rest drifts -100 over 0..40 s: at t=20 it is -50 lower
        pts = fc.HarkFlowCal._forces(levels, [(0., 0.), (40., -100.)])
        self.assertEqual([round(v) for f, v in pts], [975, 1050, 1125])
        # One reading only: flat rest
        pts = fc.HarkFlowCal._forces(levels, [(0., 0.)])
        self.assertEqual([round(v) for f, v in pts], [1000, 1100, 1200])
        # Flat beyond the last reading, and a failed reading is skipped
        pts = fc.HarkFlowCal._forces(levels, [(0., 0.), (15., -60.),
                                              (16., None)])
        self.assertEqual([round(v) for f, v in pts], [960, 1040, 1140])

    def test_excess_is_relative_to_the_earlier_low_flow_line(self):
        rows = fc.excess_percent(from8(RAMP_250), 12.)
        by_flow = {f: ex for f, v, ex in rows}
        self.assertEqual(by_flow[8], 0.)
        self.assertEqual(by_flow[10], 0.)
        self.assertLess(abs(by_flow[12]), 3.)
        self.assertGreater(by_flow[16], 10.)
        self.assertGreater(by_flow[22], by_flow[16])

    def test_straight_ramp_has_no_knee(self):
        points = [(f, 10000 + 12000 * f) for f in (6, 8, 10, 12, 14, 16)]
        cap, knee, rows = fc.pick_cap(points)
        self.assertIsNone(cap)
        self.assertIsNone(knee)

    def test_crossing_is_placed_between_the_steps(self):
        # 10 sits on the line (0%), 12 is 100% over: 5% is crossed at 10.1
        points = [(6, 100), (8, 130), (10, 160), (12, 380)]
        cap, knee, rows = fc.pick_cap(points, 5., margin=1.)
        self.assertEqual(knee, 10.1)
        self.assertEqual(cap, 9.0)
        cap, knee, rows = fc.pick_cap(points, 5., margin=8., min_cap=4.)
        self.assertEqual(cap, 4.0)

    def test_two_points_cannot_be_judged(self):
        # Nothing to fit a line through yet: no knee, no cap
        points = [(6, 100), (8, 1000)]
        cap, knee, rows = fc.pick_cap(points)
        self.assertIsNone(knee)
        self.assertIsNone(cap)

    def test_fourth_point_is_judged_against_the_first_three(self):
        points = [(6, 100), (8, 130), (10, 160), (12, 500)]
        cap, knee, rows = fc.pick_cap(points, 5., margin=1.)
        # 10 reads 0 (unjudged), 12 reads +163%: crossing just past 10
        self.assertEqual(knee, 10.06)
        self.assertEqual(cap, 9.0)

    def test_median(self):
        self.assertEqual(fc.median([3, 1, 2]), 2)
        self.assertEqual(fc.median([4, 1, 3, 2]), 2.5)
        self.assertIsNone(fc.median([]))


if __name__ == "__main__":
    unittest.main()
