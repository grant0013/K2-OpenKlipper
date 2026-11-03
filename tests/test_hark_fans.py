import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).parents[1] / "klippy" / "extras" / \
    "hark_fans.py"
spec = importlib.util.spec_from_file_location("hark_fans", PATH)
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)


class UsualRpmTest(unittest.TestCase):
    def test_first_readings_set_the_usual(self):
        # Grant's K2 part fan 11-03: 7496 at full; later readings do not
        # move the baseline
        self.assertEqual(hf.usual_rpm([7496, 7510, 7480, 6000, 5900], 3),
                         7496)

    def test_zero_readings_are_ignored(self):
        self.assertEqual(hf.usual_rpm([0, 7500, 0, 7600], 3), 7550)
        self.assertIsNone(hf.usual_rpm([0, 0], 3))
        self.assertIsNone(hf.usual_rpm([], 3))


class JudgeTest(unittest.TestCase):
    def test_ok_slow_stopped_new(self):
        self.assertEqual(hf.judge(7400, 7500, 15.), ('ok', 1.3))
        self.assertEqual(hf.judge(6000, 7500, 15.), ('slow', 20.))
        self.assertEqual(hf.judge(0, 7500, 15.), ('stopped', None))
        self.assertEqual(hf.judge(7500, None, 15.), ('new', None))

    def test_faster_than_usual_is_fine(self):
        verdict, under = hf.judge(8000, 7500, 15.)
        self.assertEqual(verdict, 'ok')
        self.assertLess(under, 0)


class LowestTurningTest(unittest.TestCase):
    def test_lowest_duty_before_it_stopped(self):
        steps = [(30, 4000), (28, 3900), (26, 3500), (24, 0)]
        self.assertEqual(hf.lowest_turning(steps), 26)

    def test_never_stopped_or_never_turned(self):
        self.assertIsNone(hf.lowest_turning([(30, 4000), (28, 3900)]))
        self.assertIsNone(hf.lowest_turning([(30, 0)]))


if __name__ == "__main__":
    unittest.main()
