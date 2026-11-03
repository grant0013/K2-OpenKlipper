import os
import pathlib
import struct
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import k2_motor_bus as bus  # noqa: E402
from extras import k2_motor_proto as proto  # noqa: E402


def make(values, short=()):
    """A bus whose 'get value' reads answer values {index: float}; indices
    in `short` answer with a 2-byte payload, others not listed give no
    reply."""
    m = types.SimpleNamespace()

    def motor_request(name, payload, addr=None, timeout=0.2):
        index = payload[2]
        if index in short:
            return {'addr': 0x81, 'status': 0, 'func': proto.FUNC_GET,
                    'payload': b"\x01\x02"}, b""
        if index not in values:
            return None, b""
        return {'addr': 0x81, 'status': 0, 'func': proto.FUNC_GET,
                'payload': struct.pack('<f', values[index])}, b""
    m.motor_request = motor_request
    m.read_value = lambda name, index: bus.K2MotorBus.read_value(
        m, name, index)
    return m


class ReadValueTest(unittest.TestCase):
    def test_float_reply_is_decoded(self):
        m = make({proto.GET_IQ: 0.4375, proto.GET_POSITION: 12.5})
        self.assertAlmostEqual(m.read_value('e', proto.GET_IQ), 0.4375)
        self.assertAlmostEqual(m.read_value('e', proto.GET_POSITION), 12.5)

    def test_no_reply_is_none(self):
        m = make({})
        self.assertIsNone(m.read_value('e', proto.GET_IQ))

    def test_wrong_length_is_none(self):
        m = make({}, short=(proto.GET_IQ,))
        self.assertIsNone(m.read_value('e', proto.GET_IQ))

    def test_temperature_rounds(self):
        m = make({proto.GET_MCU_TEMP: 41.26})
        self.assertEqual(bus.K2MotorBus.read_temp(m, 'e'), 41.3)


class MotorLogCsvTest(unittest.TestCase):
    def test_csv_has_labels_and_blank_for_missed_reads(self):
        m = types.SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            m.log_dir = os.path.join(d, "logs")
            log = {'indices': [proto.GET_POSITION, proto.GET_IQ, 0x1a],
                   'rows': [(10.5, 1000.25, 3.2, [1.5, 0.25, None]),
                            (10.6, 1000.35, 3.6, [1.6, None, 7.0])]}
            base = bus.K2MotorBus._write_motor_log(m, "petg250", log)
            with open(base) as f:
                lines = f.read().splitlines()
        self.assertTrue(base.endswith("petg250_motor.csv"))
        self.assertEqual(lines[0],
                         "print_time,monotonic,cmd_e,position,iq,get1a")
        self.assertEqual(lines[1],
                         "10.500000,1000.250000,3.2000,1.500000,0.250000,")
        self.assertEqual(lines[2],
                         "10.600000,1000.350000,3.6000,1.600000,,7.000000")


if __name__ == "__main__":
    unittest.main()
