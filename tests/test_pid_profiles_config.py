import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import heaters  # noqa: E402
from extras import hark_pid_profiles as pp  # noqa: E402


class FakeHeater:
    def get_max_power(self):
        return 1.

    def get_smooth_time(self):
        return 1.


class PIDConfigTest(unittest.TestCase):
    def test_control_pid_builds_from_a_profile(self):
        # Regression: heaters.ControlPID asks for optional options too
        # (high_temp_value, pid_Kp_high_temp); the profile config must hand
        # back their defaults instead of raising
        pid = heaters.ControlPID(FakeHeater(), pp._PIDConfig(16.5, 1.36, 50.2))
        self.assertAlmostEqual(pid.Kp * 255., 16.5)
        self.assertIsNone(pid.high_gains)
        self.assertEqual(pid.high_temp_value, 280.)

    def test_required_option_still_raises(self):
        with self.assertRaises(KeyError):
            pp._PIDConfig(1., 1., 1.).getfloat('pid_Kx')


if __name__ == "__main__":
    unittest.main()
