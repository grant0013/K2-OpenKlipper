import pathlib
import struct
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import k2_motor_bus as bus  # noqa: E402


def make(values):
    """A motor bus whose param reads return values {pid: float}."""
    m = types.SimpleNamespace(calibration_warnings=[])

    def xfer(addr, payload):
        if tuple(payload) == bus.END_PARAM_READ:
            return None, b""
        pid = payload[3]
        value = values.get(pid, 1.0)
        return None, bytes(5) + struct.pack('<f', value) + b"\x00"
    m.xfer = xfer
    return m


def check(values):
    m = make(values)
    bus.K2MotorBus._read_params(m, "y", 0x82)
    return m.calibration_warnings


class CalibrationCheckTest(unittest.TestCase):
    def test_k2_plus_param_19_alone_is_fine(self):
        # 11-01: every K2 Plus motor reads 19 = 0.15 with 09 normal
        self.assertEqual(check({0x09: 159.91, 0x19: 0.15}), [])

    def test_k2_values_are_fine(self):
        self.assertEqual(check({0x09: 181.15, 0x19: 4.95}), [])

    def test_offset_near_zero_warns(self):
        warnings = check({0x09: 0.0, 0x19: 0.15})
        self.assertEqual(len(warnings), 1)
        self.assertIn("motor Y", warnings[0])


if __name__ == "__main__":
    unittest.main()
