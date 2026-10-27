# Offline checks for the motor reply parser/decoder (k2_motor_proto) and the
# hark_safety trip rules. Run from the repo root:
#   python -m unittest tests/test_motor_proto.py
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import k2_motor_proto as proto  # noqa: E402
from extras import hark_safety  # noqa: E402


def reply(addr, status, func, payload):
    """A motor/CFS reply frame: f7 addr len status func payload crc."""
    return proto.build_frame(addr, [status, func] + list(payload))


HEALTHY = bytes.fromhex("f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb")
# Power-on reply captured from stock 10-24: status 02, error bit 8
FAULTED = reply(0x81, 0x02, 0x0c, [0x00, 0x01, 0, 0, 0, 0, 0, 0])
# Low supply voltage warning (warning bit 1), status 04
WARNED = reply(0x82, 0x04, 0x0c, [0, 0, 0, 0, 0x02, 0, 0, 0])
# Real CFS reply (EXTRUDE_ERR8 to a stage-5 load), captured 10-24
CFS_ERR8 = bytes.fromhex("f7 01 07 0c 10 c4 a7 18 42 50")


class CrcTest(unittest.TestCase):
    def test_documented_healthy_reply(self):
        self.assertTrue(proto.check_crc(HEALTHY))

    def test_captured_cfs_replies(self):
        self.assertTrue(proto.check_crc(CFS_ERR8))
        self.assertTrue(proto.check_crc(
            bytes.fromhex("f7 01 07 0c 10 44 b1 65 0c 18")))

    def test_known_request_frames(self):
        self.assertEqual(proto.build_frame(0x81, [0, 0x11, 1]).hex(),
                         "f781040011011d")

    def test_corrupt_byte_fails(self):
        bad = bytearray(HEALTHY)
        bad[7] ^= 0x10
        self.assertFalse(proto.check_crc(bad))
        with self.assertRaises(ValueError):
            proto.parse_frame(bytes(bad))


class ParseDecodeTest(unittest.TestCase):
    def test_healthy(self):
        d = proto.decode_protection(proto.parse_frame(HEALTHY))
        self.assertFalse(d['has_error'])
        self.assertFalse(d['active'])
        self.assertEqual(d['status'], 0)
        self.assertEqual(proto.describe_protection(d), "ok")

    def test_faulted_status_byte_02(self):
        f = proto.parse_frame(FAULTED)
        self.assertEqual((f['addr'], f['status'], f['func']),
                         (0x81, 0x02, 0x0c))
        d = proto.decode_protection(f)
        self.assertTrue(d['faulted'])
        self.assertTrue(d['has_error'])
        self.assertEqual(d['error_code'], 0x100)
        self.assertEqual(d['errors'], ["excessive position tracking error"])

    def test_old_search_missed_the_fault(self):
        # The old parser looked for 00 0c and found nothing in a 02 0c reply
        self.assertEqual(FAULTED.find(bytes([0x00, 0x0c])), -1)

    def test_warning_only(self):
        d = proto.decode_protection(proto.parse_frame(WARNED))
        self.assertFalse(d['has_error'])
        self.assertTrue(d['active'])
        self.assertEqual(d['warnings'], ["motor power supply too low"])
        self.assertIn("warning: motor power supply too low",
                      proto.describe_protection(d))

    def test_unknown_bits_named(self):
        self.assertEqual(proto.decode_bits(1 << 20, proto.ERROR_CODE_LABELS),
                         ["bit 20"])

    def test_short_and_empty(self):
        for rx in (b'', None, b'\xf7\x81\x04'):
            with self.assertRaises(ValueError):
                proto.parse_frame(rx)

    def test_near_zero_calibration(self):
        self.assertTrue(proto.is_near_zero_calibration(0.0))
        self.assertTrue(proto.is_near_zero_calibration(-0.15))
        self.assertFalse(proto.is_near_zero_calibration(2.3))
        self.assertFalse(proto.is_near_zero_calibration(None))


class FindReplyTest(unittest.TestCase):
    def test_skips_late_cfs_reply(self):
        frame, rest, rejects = proto.find_reply(CFS_ERR8 + HEALTHY, 0x81,
                                                0x0c)
        self.assertEqual(frame, HEALTHY)
        self.assertEqual(rejects, ['addr'])

    def test_skips_wrong_function(self):
        stall_echo = reply(0x81, 0x00, 0x11, [0x01])
        frame, rest, rejects = proto.find_reply(stall_echo + HEALTHY, 0x81,
                                                0x0c)
        self.assertEqual(frame, HEALTHY)
        self.assertEqual(rejects, ['func'])

    def test_skips_bad_crc_and_noise(self):
        bad = bytearray(HEALTHY)
        bad[-1] ^= 0xff
        frame, rest, rejects = proto.find_reply(
            b'\x00\x13' + bytes(bad) + HEALTHY, 0x81, 0x0c)
        self.assertEqual(frame, HEALTHY)
        self.assertIn('crc', rejects)

    def test_incomplete_waits(self):
        frame, rest, rejects = proto.find_reply(HEALTHY[:7], 0x81, 0x0c)
        self.assertIsNone(frame)
        self.assertEqual(rest, HEALTHY[:7])
        frame, rest, rejects = proto.find_reply(rest + HEALTHY[7:], 0x81,
                                                0x0c)
        self.assertEqual(frame, HEALTHY)

    def test_oversize_len_is_noise(self):
        frame, rest, rejects = proto.find_reply(b'\xf7\x01\xf0' + HEALTHY,
                                                0x81, 0x0c, max_len=64)
        self.assertEqual(frame, HEALTHY)
        self.assertEqual(rejects, ['len'])

    def test_cfs_request_has_no_function_check(self):
        self.assertIsNone(proto.request_func(0x01, [0xff, 0x10, 0x01]))
        self.assertEqual(proto.request_func(0x81, [0x00, 0x0c, 0x0b]), 0x0c)
        frame, rest, rejects = proto.find_reply(CFS_ERR8, 0x01, None)
        self.assertEqual(frame, CFS_ERR8)

    def test_broadcast_accepts_any_sender(self):
        # The CFS answers an addressing broadcast from its new address
        frame, rest, rejects = proto.find_reply(CFS_ERR8, 0xfe, None)
        self.assertEqual(frame, CFS_ERR8)


class FakeBus:
    e_send = None

    def __init__(self, replies):
        self.replies = replies

    def query_protection(self, name, addr=None):
        rx = self.replies.get(name)
        if rx is None:
            return None
        d = proto.decode_protection(proto.parse_frame(rx))
        d['raw'] = rx.hex(' ')
        return d


class FakeGcode:
    def __init__(self):
        self.info, self.raw, self.scripts = [], [], []

    def respond_info(self, msg):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def run_script(self, script):
        self.scripts.append(script)


def make_watch(replies, fail_count=2):
    w = hark_safety.HarkSafety.__new__(hark_safety.HarkSafety)
    w.bus = FakeBus(replies)
    w.gcode = FakeGcode()
    w.motor_fail_count = fail_count
    w.xy_action, w.e_action = 'cancel', 'pause'
    w.xy_addrs, w.e_addr = {'x': 0x81, 'y': 0x82}, 0x81
    w.motor_bad, w.motor_warned = {}, {}
    w.tripped = False
    w.last = {'fans': {}, 'motors': {}, 'event': None}
    return w


class SafetyWatchTest(unittest.TestCase):
    def test_fault_trips_after_fail_count_with_name(self):
        w = make_watch({'x': FAULTED, 'y': HEALTHY})
        w._check_motors(0.)
        self.assertFalse(w.tripped)
        w._check_motors(3.)
        self.assertTrue(w.tripped)
        self.assertIn("excessive position tracking error", w.last['event'])
        self.assertEqual(w.gcode.scripts, ["CANCEL_PRINT\nTURN_OFF_HEATERS"])

    def test_warning_reports_once_and_never_trips(self):
        w = make_watch({'x': HEALTHY, 'y': WARNED}, fail_count=1)
        for t in range(5):
            w._check_motors(float(t))
        self.assertFalse(w.tripped)
        self.assertEqual(w.gcode.scripts, [])
        self.assertEqual(len(w.gcode.info), 1)
        self.assertIn("motor power supply too low", w.gcode.info[0])

    def test_no_reply_never_trips(self):
        w = make_watch({}, fail_count=1)
        w._check_motors(0.)
        self.assertFalse(w.tripped)
        self.assertIsNone(w.last['motors']['x'])

    def test_status_latch_without_code_does_not_trip(self):
        stall_only = reply(0x81, 0x01, 0x0c, [0] * 8)
        w = make_watch({'x': stall_only}, fail_count=1)
        w._check_motors(0.)
        self.assertFalse(w.tripped)
        self.assertEqual(w.gcode.info, [])


class FakeMutex:
    def __init__(self):
        self.locked = False

    def test(self):
        return self.locked

    def __enter__(self):
        self.locked = True

    def __exit__(self, *args):
        self.locked = False


class FakeReactor:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def pause(self, when):
        self.now = when
        return when


class FakeSerial:
    """Hands out queued byte chunks, one list per write."""
    def __init__(self, per_write):
        self.per_write = list(per_write)
        self.pending = []
        self.writes = []
        self.timeout = 0

    def reset_input_buffer(self):
        self.pending = []

    def write(self, data):
        self.writes.append(bytes(data))
        self.pending = list(self.per_write.pop(0)) if self.per_write else []

    def flush(self):
        pass

    def read(self, n):
        return self.pending.pop(0) if self.pending else b''


def make_bus(per_write, reply_check='strict'):
    import types
    sys.modules.setdefault('mcu', types.ModuleType('mcu'))
    from extras import k2_motor_bus
    bus = k2_motor_bus.K2MotorBus.__new__(k2_motor_bus.K2MotorBus)
    bus.reactor = FakeReactor()
    bus._xfer_lock = FakeMutex()
    bus.ser = FakeSerial(per_write)
    bus.reply_check = reply_check
    bus.bus_errors = {'crc': 0, 'len': 0, 'addr': 0, 'func': 0,
                      'retries': 0, 'read_failures': 0}
    bus._reject_logged = 0.
    return bus


class BusXferTest(unittest.TestCase):
    def test_late_cfs_reply_is_not_a_motor_reply(self):
        bus = make_bus([[CFS_ERR8, HEALTHY]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, HEALTHY)
        self.assertEqual(bus.bus_errors['addr'], 1)

    def test_read_is_retried_once(self):
        bus = make_bus([[], [HEALTHY]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, HEALTHY)
        self.assertEqual(len(bus.ser.writes), 2)
        self.assertEqual(bus.bus_errors['retries'], 1)

    def test_clear_is_not_retried(self):
        bus = make_bus([[], [HEALTHY]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x05), timeout=.05)
        self.assertEqual(resp, b'')
        self.assertEqual(len(bus.ser.writes), 1)

    def test_cfs_reply_passes(self):
        bus = make_bus([[CFS_ERR8[:4], CFS_ERR8[4:]]])
        frame, resp = bus.xfer(0x01, [0xff, 0x10, 0x01, 0x05])
        self.assertEqual(resp, CFS_ERR8)
        self.assertEqual(len(bus.ser.writes), 1)

    def test_bad_crc_gives_no_reply_in_strict(self):
        bad = bytearray(HEALTHY)
        bad[-1] ^= 1
        bus = make_bus([[bytes(bad)], [bytes(bad)]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, b'')
        self.assertEqual(bus.bus_errors['crc'], 2)
        self.assertEqual(bus.bus_errors['read_failures'], 1)

    def test_off_mode_keeps_old_behaviour(self):
        bus = make_bus([[CFS_ERR8, HEALTHY]], reply_check='off')
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, CFS_ERR8)

    def test_stall_mode_echo(self):
        bus = make_bus([[reply(0x81, 0, 0x11, [1])],
                        [reply(0x82, 0, 0x11, [2])],
                        [reply(0x82, 0, 0x11, [2])]])
        bus.stall_mode_check = 'warn'
        results = bus.set_stall_mode(1)
        self.assertEqual([r[2] for r in results], [True, False])
        # unconfirmed Y got one more try
        self.assertEqual(len(bus.ser.writes), 3)


if __name__ == "__main__":
    unittest.main()
