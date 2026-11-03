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


class PickCapTest(unittest.TestCase):
    def test_petg_250_caps_at_13(self):
        cap, knee, rows = fc.pick_cap(RAMP_250)
        self.assertEqual((cap, knee), (13.0, 16))

    def test_petg_260_caps_at_13_too(self):
        # 10 C more lowers the force but not where the line bends
        cap, knee, rows = fc.pick_cap(RAMP_260)
        self.assertEqual((cap, knee), (13.0, 16))

    def test_first_ramp_agrees(self):
        cap, knee, rows = fc.pick_cap(RAMP_250_3S)
        self.assertEqual((cap, knee), (13.0, 16))

    def test_excess_is_relative_to_the_low_flow_line(self):
        rows = fc.excess_percent(RAMP_250, 12.)
        by_flow = {f: ex for f, v, ex in rows}
        self.assertLess(abs(by_flow[8]), 2.)
        self.assertGreater(by_flow[16], 10.)
        self.assertGreater(by_flow[22], by_flow[16])

    def test_straight_ramp_has_no_knee(self):
        points = [(f, 10000 + 12000 * f) for f in (6, 8, 10, 12, 14, 16)]
        cap, knee, rows = fc.pick_cap(points)
        self.assertIsNone(cap)
        self.assertIsNone(knee)

    def test_margin_and_floor(self):
        points = [(6, 100), (8, 130), (10, 160), (12, 400)]
        cap, knee, rows = fc.pick_cap(points, margin=1.)
        self.assertEqual((cap, knee), (9.0, 12))
        cap, knee, rows = fc.pick_cap(points, margin=8., min_cap=4.)
        self.assertEqual(cap, 4.0)

    def test_two_points_cannot_be_judged(self):
        # Nothing to fit a line through yet: no knee, no cap
        points = [(6, 100), (8, 1000)]
        cap, knee, rows = fc.pick_cap(points)
        self.assertIsNone(knee)
        self.assertIsNone(cap)

    def test_third_point_is_judged_against_the_first_two(self):
        points = [(6, 100), (8, 130), (10, 500)]
        cap, knee, rows = fc.pick_cap(points)
        self.assertEqual((cap, knee), (7.0, 10))

    def test_median(self):
        self.assertEqual(fc.median([3, 1, 2]), 2)
        self.assertEqual(fc.median([4, 1, 3, 2]), 2.5)
        self.assertIsNone(fc.median([]))


if __name__ == "__main__":
    unittest.main()
