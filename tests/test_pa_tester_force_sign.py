import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
sys.modules.setdefault('chelper', types.ModuleType('chelper'))
sys.modules.setdefault('mathutil', types.ModuleType('mathutil'))
sys.modules.setdefault('queuelogger', types.ModuleType('queuelogger'))

from extras import pa_tester as pt  # noqa: E402

FAST, SLOW = 4.16, 0.83
# Gauge levels of the 11-09 run: the fast windows sat near -1.45M and the
# slow ones near -1.57M, so force reads higher with speed
FAST_F, SLOW_F = -1450000., -1570000.


def run(speeds, forces, sign=1.):
    moves, samples = [], []
    t = 0.
    for v, f in zip(speeds, forces):
        moves.append(pt.MoveData(t, 1., v, 0.))
        samples.extend((t + i * .05, sign * f) for i in range(20))
        t += 1.
        moves.append(pt.MoveData(t, .1, v, 1.))
        t += .1
    return samples, moves


class ForceSignTest(unittest.TestCase):
    def speeds(self):
        return [SLOW, FAST, SLOW, FAST, SLOW, FAST, SLOW, FAST, FAST, SLOW]

    def forces(self):
        return [FAST_F if v == FAST else SLOW_F for v in self.speeds()]

    def test_force_rising_with_speed_is_positive(self):
        self.assertEqual(pt.detect_force_sign(*run(self.speeds(),
                                                   self.forces())), 1)

    def test_an_inverted_gauge_is_negative(self):
        self.assertEqual(pt.detect_force_sign(*run(self.speeds(),
                                                   self.forces(), -1.)), -1)

    def test_last_two_moves_reading_backwards_do_not_flip_it(self):
        # The 11-09 failure
        forces = self.forces()
        forces[-2], forces[-1] = SLOW_F, FAST_F
        self.assertEqual(pt.detect_force_sign(*run(self.speeds(), forces)), 1)

    def test_a_two_move_test_still_works(self):
        self.assertEqual(pt.detect_force_sign(*run([FAST, SLOW],
                                                   [FAST_F, SLOW_F])), 1)

    def test_no_data_is_none(self):
        self.assertIsNone(pt.detect_force_sign([], []))

    def test_one_speed_cannot_tell(self):
        self.assertIsNone(pt.detect_force_sign(
            *run([FAST, FAST, FAST], [FAST_F] * 3)))


if __name__ == "__main__":
    unittest.main()
