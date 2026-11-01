import importlib.util
import math
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "gcode_arcs",
    pathlib.Path(__file__).parents[1] / "klippy" / "extras" / "gcode_arcs.py")
arcs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(arcs)


class Gcmd:
    def __init__(self, params):
        self.params = params

    def get_float(self, key, default=None):
        return self.params.get(key, default)


class Sink:
    def __init__(self):
        self.moves = []

    def create_gcode_command(self, cmd, raw, params):
        return params

    def cmd_G1(self, params):
        self.moves.append(params)


def plan(start, end, ij, clockwise=False, e=None, resolution=0.5,
         max_deviation=0., absolute_e=False):
    arc = arcs.ArcSupport.__new__(arcs.ArcSupport)
    arc.mm_per_arc_segment = resolution
    arc.max_deviation = max_deviation
    sink = Sink()
    arc.gcode = arc.gcode_move = sink
    params = {} if e is None else {'E': e}
    arc.planArc(list(start), list(end), ij, clockwise, Gcmd(params),
                absolute_e, 0, 1, 2)
    return sink.moves


def worst_miss(start, moves, centre, radius):
    worst, prev = 0., start
    for m in moves:
        mx, my = (prev[0] + m['X']) / 2, (prev[1] + m['Y']) / 2
        worst = max(worst, abs(radius - math.hypot(mx - centre[0],
                                                   my - centre[1])))
        prev = (m['X'], m['Y'])
    return worst


class ArcTest(unittest.TestCase):
    def test_small_half_circle_is_not_a_straight_line(self):
        # r 0.3 mm half circle (0.94 mm long): rounding down made it one
        # chord straight across, 0.3 mm off the curve
        moves = plan((0.3, 0., 0., 0.), (-0.3, 0., 0.), (-0.3, 0.))
        self.assertGreaterEqual(len(moves), 2)
        # two 90 degree chords: 0.088 mm, against 0.3 mm as one chord
        self.assertLess(worst_miss((0.3, 0.), moves, (0., 0.), 0.3), 0.1)

    def test_max_deviation_holds_on_small_arcs(self):
        moves = plan((0.3, 0., 0., 0.), (-0.3, 0., 0.), (-0.3, 0.),
                     resolution=2.0, max_deviation=0.01)
        self.assertLessEqual(
            worst_miss((0.3, 0.), moves, (0., 0.), 0.3), 0.01 + 1e-9)

    def test_large_arcs_use_long_segments(self):
        # r 100 mm quarter circle: capped by resolution, not 0.5 mm pieces
        r = 100.
        moves = plan((r, 0., 0., 0.), (0., r, 0.), (-r, 0.),
                     resolution=2.0, max_deviation=0.01)
        self.assertEqual(len(moves), math.ceil(r * math.pi / 2 / 2.0))
        self.assertLessEqual(
            worst_miss((r, 0.), moves, (0., 0.), r), 0.01)

    def test_segments_never_longer_than_resolution(self):
        moves = plan((5., 0., 0., 0.), (0., 5., 0.), (-5., 0.),
                     resolution=0.5)
        prev = (5., 0.)
        for m in moves:
            self.assertLessEqual(math.hypot(m['X'] - prev[0],
                                            m['Y'] - prev[1]), 0.5 + 1e-9)
            prev = (m['X'], m['Y'])

    def test_extrusion_is_kept(self):
        moves = plan((2., 0., 0., 0.), (-2., 0., 0.), (-2., 0.), e=1.5,
                     resolution=2.0, max_deviation=0.01)
        self.assertAlmostEqual(sum(m['E'] for m in moves), 1.5)

    def test_ends_exactly_on_target(self):
        moves = plan((2., 0., 0., 0.), (0., 2., 0.4), (-2., 0.),
                     resolution=2.0, max_deviation=0.01)
        self.assertEqual((moves[-1]['X'], moves[-1]['Y'], moves[-1]['Z']),
                         (0., 2., 0.4))

    def test_full_circle(self):
        moves = plan((2., 0., 0., 0.), (2., 0., 0.), (-2., 0.),
                     resolution=2.0, max_deviation=0.01)
        self.assertGreater(len(moves), 10)


if __name__ == '__main__':
    unittest.main()
