import math
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from kinematics import extruder as ext  # noqa: E402

AREA = math.pi * 0.875 ** 2


class FakeMove:
    def __init__(self, xy, e, speed=100., accel=5000.):
        self.axes_d = [xy, 0., 0., e]
        self.move_d = abs(xy) if xy else abs(e)
        self.axes_r = [d / self.move_d for d in self.axes_d]
        self.accel = accel
        self.limits = []

    def limit_speed(self, speed, accel):
        self.limits.append((speed, accel))


def make(max_flow):
    e = ext.PrinterExtruder.__new__(ext.PrinterExtruder)
    e.printer = types.SimpleNamespace(command_error=Exception)
    e.heater = types.SimpleNamespace(can_extrude=True)
    e.filament_area = AREA
    e.nozzle_diameter = 0.4
    e.max_extrude_ratio = 100. / AREA
    e.max_e_dist = 1000.
    e.max_e_velocity = 50.
    e.max_e_accel = 2000.
    e.max_flow = max_flow
    return e


class MaxFlowTest(unittest.TestCase):
    def test_print_move_is_slowed_to_the_cap(self):
        # 0.42 x 0.2 mm line: 0.084 mm^2 of plastic per mm of travel
        e = make(13.)
        move = FakeMove(xy=10., e=10. * 0.084 / AREA)
        e.check_move(move, 3)
        self.assertEqual(len(move.limits), 1)
        speed, accel = move.limits[0]
        self.assertAlmostEqual(speed, 13. / 0.084, places=3)   # ~155 mm/s
        self.assertEqual(accel, move.accel)

    def test_no_cap_leaves_print_moves_alone(self):
        e = make(0.)
        move = FakeMove(xy=10., e=0.5)
        e.check_move(move, 3)
        self.assertEqual(move.limits, [])

    def test_extrude_only_moves_are_not_capped_by_flow(self):
        # Purges and the flow ramp itself: only the extrude-only limits
        e = make(13.)
        move = FakeMove(xy=0., e=20.)
        e.check_move(move, 3)
        self.assertEqual(len(move.limits), 1)
        self.assertAlmostEqual(move.limits[0][0], 50.)

    def test_overextrusion_check_still_runs_with_a_cap(self):
        e = make(13.)
        move = FakeMove(xy=1., e=200.)
        with self.assertRaises(Exception):
            e.check_move(move, 3)


if __name__ == "__main__":
    unittest.main()
