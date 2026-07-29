import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras"
    / "k2_motor_bus.py"
)
SPEC = importlib.util.spec_from_file_location("k2_motor_bus", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MotorBusProtocolTest(unittest.TestCase):
    def setUp(self):
        self.bus = MODULE.K2MotorBus.__new__(MODULE.K2MotorBus)

    def test_known_mode_one_frame(self):
        frame = self.bus._build(0x81, [0x00, 0x11, 0x01])
        self.assertEqual(frame.hex(), "f781040011011d")

    def test_known_mode_two_frame(self):
        frame = self.bus._build(0x81, [0x00, 0x11, 0x02])
        self.assertEqual(frame.hex(), "f7810400110214")

    def test_address_is_excluded_from_crc(self):
        first = self.bus._build(0x81, [0x00, 0x11, 0x01])
        second = self.bus._build(0x82, [0x00, 0x11, 0x01])
        self.assertEqual(first[-1], second[-1])
        self.assertEqual(first[:1] + first[2:], second[:1] + second[2:])

    def test_length_counts_payload_and_crc(self):
        frame = self.bus._build(0x01, [0xff, 0x0a])
        self.assertEqual(frame[2], 3)
        self.assertEqual(len(frame), 6)


if __name__ == "__main__":
    unittest.main()
