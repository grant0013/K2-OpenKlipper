import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import heaters  # noqa: E402


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def getfloat(self, name, default=None, **kw):
        return self.values.get(name, default)


class FakeHeater:
    def __init__(self):
        self.pwm = []

    def get_max_power(self):
        return 1.

    def get_smooth_time(self):
        return 1.

    def set_pwm(self, read_time, value):
        self.pwm.append(value)


def make(high=True):
    values = {'pid_Kp': 16.5, 'pid_Ki': 1.36, 'pid_Kd': 50.2}
    if high:
        values.update({'pid_Kp_high_temp': 32.827, 'pid_Ki_high_temp': 3.367,
                       'pid_Kd_high_temp': 80.016})
    heater = FakeHeater()
    return heaters.ControlPID(heater, FakeConfig(values)), heater


class HighTempPidTest(unittest.TestCase):
    def test_gains_switch_above_high_temp_value(self):
        pid, heater = make()
        pid.prev_temp_time, pid.prev_temp = 0., 248.
        # 2 C under target, no derivative: output is (Kp + Ki) * 2, well
        # under full power for both sets
        pid.temperature_update(1., 248., 250.)
        pid2, heater2 = make()
        pid2.prev_temp_time, pid2.prev_temp = 0., 288.
        pid2.temperature_update(1., 288., 290.)
        self.assertLess(heater.pwm[0], heater2.pwm[0])
        self.assertAlmostEqual(heater.pwm[0] / heater2.pwm[0],
                               (16.5 + 1.36) / (32.827 + 3.367), places=2)

    def test_without_the_high_set_nothing_changes(self):
        pid, heater = make(high=False)
        self.assertIsNone(pid.high_gains)
        pid.prev_temp_time, pid.prev_temp = 0., 288.
        pid.temperature_update(1., 288., 290.)
        pid2, heater2 = make(high=False)
        pid2.prev_temp_time, pid2.prev_temp = 0., 248.
        pid2.temperature_update(1., 248., 250.)
        self.assertAlmostEqual(heater.pwm[0], heater2.pwm[0])


if __name__ == "__main__":
    unittest.main()
