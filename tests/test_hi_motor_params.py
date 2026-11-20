"""k2_motor_bus write_params_<motor>: Creality Hi stock servo values at wake.

Motor parameters are RAM only, so after a cold boot the X/Y motors run the
motor boards' flash defaults unless something writes the tuned values, as
stock motor_control did at every start. The write frame (00 06 01 <id>
<value>, float32 or int16 LE) and the commit (00 06 03 -> 00 06 01) are from
the stock motor_init capture in gitstonelabs' creality-klipper-unlocked
(docs/protocol/README.md 3.2 and 5.3).
"""
import configparser
import importlib
import pathlib
import re
import struct
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
try:
    import mcu  # noqa: F401  (Klipper's, needs pyserial)
except ImportError:
    sys.modules['mcu'] = types.ModuleType('mcu')
bus_mod = importlib.import_module("extras.k2_motor_bus")
proto = importlib.import_module("extras.k2_motor_proto")

# Stock Hi v1.1.0.74 motor_control.cfg, x_/y_ keys
HI_X = {0x46: 100., 0x56: 0.085, 0x57: 50., 0x59: 0.5, 0x66: 2.0,
        0x67: 200., 0x74: 10., 0x76: 0., 0x77: 4000., 0x78: 4000.,
        0x79: 400., 0x0d: 0.70, 0x0e: 0.015, 0xbb: 0.3, 0xbc: 0.1,
        0xb4: 4.0, 0xb5: 1.0, 0xc8: 16}
HI_Y = dict(HI_X)
HI_Y[0x56] = 0.065


class FakeGcode:
    def __init__(self):
        self.info, self.raw = [], []

    def respond_info(self, msg):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)


class FakeMotor:
    """One servo's parameter table. ignore: ids whose writes it drops;
    silent: ids whose reads get no reply."""

    def __init__(self, params, ignore=(), silent=()):
        self.params = dict(params)
        self.ignore = set(ignore)
        self.silent = set(silent)


def reply(addr, func, data, status=0x00):
    return proto.build_frame(addr, [status, func] + list(data))


def value_bytes(pid, value):
    return list(proto.encode_param(pid, value))


def make_bus(motors, writes=None):
    b = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
    b.bus_motors = (('x', 0x81), ('y', 0x82))
    b.param_writes = writes or {}
    b.param_results = {}
    b.param_warnings = []
    b.calibration_warnings = []
    b.stall_armed = False
    b.gcode = FakeGcode()
    b.sent = []
    by_addr = {0x81: motors.get('x'), 0x82: motors.get('y')}

    def xfer(addr, payload, read_len=64, timeout=0.2, check=None,
             retry=None):
        payload = tuple(payload)
        b.sent.append((addr, payload))
        frame = proto.build_frame(addr, payload)
        m = by_addr[addr]
        if payload[:3] == (0x00, 0x06, 0x02):
            pid = payload[3]
            if pid in m.silent:
                return frame, b''
            if pid not in m.params:
                return frame, reply(addr, 0x06, [0xaf])
            return frame, reply(addr, 0x06,
                                value_bytes(pid, m.params[pid]))
        if payload[:3] == (0x00, 0x06, 0x01):
            pid = payload[3]
            raw = bytes(payload[4:])
            if pid not in m.ignore:
                m.params[pid] = proto.decode_param(pid, raw)
            return frame, reply(addr, 0x06, raw)
        if payload == bus_mod.PARAM_COMMIT:
            return frame, reply(addr, 0x06, [0x01])
        raise AssertionError("unexpected frame %s" % (frame.hex(' '),))
    b.xfer = xfer
    return b


def writes_sent(b, addr=None):
    return [(a, p[3], bytes(p[4:])) for a, p in b.sent
            if p[:3] == (0x00, 0x06, 0x01) and addr in (None, a)]


class FakeConfig:
    def __init__(self, options):
        self.options = options

    def get(self, name, default=None):
        return self.options.get(name, default)

    def error(self, msg):
        return ValueError(msg)


def section(path, name):
    """One [section] of a Klipper cfg, parsed (continuation lines joined
    with newlines, as Klipper's config does)."""
    text = (ROOT / path).read_text()
    m = re.search(r'^\[%s\]\n(.*?)(?=^\[)' % re.escape(name), text,
                  re.M | re.S)
    if m is None:
        return None
    cp = configparser.RawConfigParser(inline_comment_prefixes=('#', ';'))
    cp.read_string("[s]\n" + m.group(1))
    return dict(cp.items('s'))


class EncodingTest(unittest.TestCase):
    def test_float_write_matches_stock_capture(self):
        # Stock X: 81 09 00 06 01 46 00 00 c8 42 (0x46 = 100.0)
        frame = proto.build_frame(0x81, proto.param_write_request(0x46, 100))
        self.assertEqual(frame[:-1].hex(' '),
                         "f7 81 09 00 06 01 46 00 00 c8 42")
        self.assertEqual(frame[-1], proto.crc8(frame[2:-1]))

    def test_int16_write_matches_stock_capture(self):
        # Stock: 81 07 00 06 01 ad 01 00 (0xad protect_en = 1)
        frame = proto.build_frame(0x81, proto.param_write_request(0xad, 1))
        self.assertEqual(frame[:-1].hex(' '), "f7 81 07 00 06 01 ad 01 00")

    def test_microsteps_is_int16_gains_are_float32(self):
        self.assertEqual(proto.param_write_request(0xc8, 16),
                         (0x00, 0x06, 0x01, 0xc8, 0x10, 0x00))
        self.assertEqual(proto.param_write_request(0x56, 0.085)[4:],
                         tuple(struct.pack('<f', 0.085)))

    def test_decode_by_width(self):
        self.assertEqual(proto.decode_param(0xc8, b'\x10\x00'), 16)
        self.assertIsNone(proto.decode_param(0xc8, struct.pack('<f', 16.)))
        self.assertAlmostEqual(
            proto.decode_param(0x56, struct.pack('<f', 0.085)), 0.085, 6)
        # 00 06 af: not readable
        self.assertIsNone(proto.decode_param(0x56, b'\xaf'))

    def test_float32_rounding_counts_as_equal(self):
        stored = struct.unpack('<f', struct.pack('<f', 0.085))[0]
        self.assertTrue(proto.param_matches(0x56, stored, 0.085))
        self.assertFalse(proto.param_matches(0x56, 0.035, 0.085))
        self.assertFalse(proto.param_matches(0x56, None, 0.085))

    def test_no_frame_for_refused_ids(self):
        for pid in (0x09, 0x0a, 0x19, 0x29, 0x2a, 0x2b, 0x5e):
            with self.assertRaises(ValueError):
                proto.param_write_request(pid, 1)


class ConfigTest(unittest.TestCase):
    def test_dangerous_ids_refused_by_name(self):
        for pid in (0x09, 0x0a, 0x19, 0x29, 0x2a, 0x2b, 0x5e):
            b = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
            b.bus_motors = (('x', 0x81), ('y', 0x82))
            config = FakeConfig({'write_params_y':
                                 '0x46=100, 0x%02x=1' % pid})
            with self.assertRaises(ValueError) as cm:
                b._parse_param_writes(config)
            self.assertIn('0x%02x' % pid, str(cm.exception))
            self.assertIn('write_params_y', str(cm.exception))

    def test_unknown_and_bad_values_refused(self):
        for text in ('0x92=1', '0xc8=16.5', '0x46=abc', '0x46=100, 0x46=90',
                     '0x46', '0x56=nan'):
            with self.assertRaises(ValueError, msg=text):
                proto.parse_param_writes(text)

    def test_empty_means_nothing(self):
        b = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
        b.bus_motors = (('x', 0x81), ('y', 0x82))
        self.assertEqual(b._parse_param_writes(FakeConfig({})), {})

    def test_multiline_value(self):
        self.assertEqual(proto.parse_param_writes(
            "0x46=100, 0x56=0.085,\n0xc8=16"),
            [(0x46, 100.), (0x56, 0.085), (0xc8, 16)])

    def test_hi_config_has_stock_values(self):
        opts = section("config/mainline_stage2_hi.cfg", "k2_motor_bus")
        x = dict(proto.parse_param_writes(opts['write_params_x']))
        y = dict(proto.parse_param_writes(opts['write_params_y']))
        self.assertEqual(x, HI_X)
        self.assertEqual(y, HI_Y)
        self.assertIsInstance(x[0xc8], int)

    def test_k2_configs_write_nothing(self):
        for path in ("config/mainline_stage2.cfg",
                     "config/mainline_stage2_k2plus.cfg"):
            opts = section(path, "k2_motor_bus")
            self.assertIsNotNone(opts, path)
            self.assertFalse([k for k in opts if k.startswith('write_params')],
                             path)

    def test_read_list_covers_new_ids(self):
        for pid in (0x0d, 0x0e, 0x59, 0x7f, 0xb4, 0xc8):
            self.assertIn(pid, bus_mod.PARAM_IDS)


# Hi motor board flash defaults after a cold boot (X)
FLASH_X = dict(HI_X)
FLASH_X.update({0x46: 300., 0x56: 0.035, 0x57: 15., 0x74: 1., 0xbb: 0.1})


class WakeWriteTest(unittest.TestCase):
    def test_writes_only_what_differs_then_commits(self):
        motor = FakeMotor(FLASH_X)
        b = make_bus({'x': motor},
                     {'x': proto.parse_param_writes(
                         ", ".join("0x%02x=%s" % kv for kv in HI_X.items()))})
        b._configure_motor('x', 0x81)
        self.assertEqual(sorted(pid for a, pid, raw in writes_sent(b)),
                         [0x46, 0x56, 0x57, 0x74, 0xbb])
        results = b.param_results['x']
        self.assertEqual(results['0x56']['result'], 'verified')
        self.assertEqual(results['0x59']['result'], 'same')
        self.assertEqual(results['0xc8']['result'], 'same')
        self.assertEqual(b.param_warnings, [])
        # Reads, then writes, then one commit last
        self.assertEqual(b.sent[-1][1], bus_mod.PARAM_COMMIT)
        first_write = min(i for i, (a, p) in enumerate(b.sent)
                          if p[:3] == (0, 6, 1))
        self.assertTrue(all(p[:3] == (0, 6, 2)
                            for a, p in b.sent[:first_write]))
        for pid, value in HI_X.items():
            self.assertTrue(proto.param_matches(pid, motor.params[pid],
                                                value), hex(pid))
        self.assertIn("5 of 18", b.gcode.info[-1])

    def test_log_line(self):
        b = make_bus({'x': FakeMotor({0x56: 0.035})},
                     {'x': [(0x56, 0.085)]})
        with self.assertLogs(level='INFO') as logs:
            b._configure_motor('x', 0x81)
        self.assertTrue(any("motor X param 0x56: 0.035 -> 0.085 (verified)"
                            in line for line in logs.output), logs.output)

    def test_int16_write_on_the_wire(self):
        b = make_bus({'x': FakeMotor({0xc8: 32})}, {'x': [(0xc8, 16)]})
        b._configure_motor('x', 0x81)
        self.assertEqual(writes_sent(b), [(0x81, 0xc8, b'\x10\x00')])
        self.assertEqual(b.param_results['x']['0xc8']['after'], 16)

    def test_read_back_mismatch_warns_but_wake_goes_on(self):
        b = make_bus({'x': FakeMotor({0x56: 0.035, 0x57: 15.},
                                     ignore={0x56})},
                     {'x': [(0x56, 0.085), (0x57, 50.)]})
        b._configure_motor('x', 0x81)
        self.assertEqual(b.param_results['x']['0x56']['result'], 'mismatch')
        self.assertEqual(b.param_results['x']['0x57']['result'], 'verified')
        self.assertEqual(len(b.param_warnings), 1)
        self.assertIn("0x56", b.param_warnings[0])
        self.assertIn("NOT verified", b.param_warnings[0])
        self.assertEqual(b.sent[-1][1], bus_mod.PARAM_COMMIT)

    def test_failed_read_means_no_write(self):
        b = make_bus({'x': FakeMotor({0x56: 0.035, 0x57: 15.},
                                     silent={0x57})},
                     {'x': [(0x56, 0.085), (0x57, 50.)]})
        b._configure_motor('x', 0x81)
        self.assertEqual([pid for a, pid, raw in writes_sent(b)], [0x56])
        self.assertEqual(b.param_results['x']['0x57']['result'],
                         'read failed')
        self.assertIn("0x57", b.param_warnings[0])

    def test_no_option_no_writes(self):
        b = make_bus({'x': FakeMotor(FLASH_X), 'y': FakeMotor(FLASH_X)})
        b._configure_motor('x', 0x81)
        b._configure_motor('y', 0x82)
        self.assertEqual(writes_sent(b), [])
        self.assertEqual(b.gcode.info, [])
        # Still reads and commits, as before
        self.assertEqual([p for a, p in b.sent
                          if p == bus_mod.PARAM_COMMIT],
                         [bus_mod.PARAM_COMMIT] * 2)

    def test_per_axis_values(self):
        x, y = FakeMotor(FLASH_X), FakeMotor(FLASH_X)
        b = make_bus({'x': x, 'y': y}, {'x': [(0x56, 0.085)],
                                        'y': [(0x56, 0.065)]})
        b._configure_motor('x', 0x81)
        b._configure_motor('y', 0x82)
        self.assertAlmostEqual(x.params[0x56], 0.085, 6)
        self.assertAlmostEqual(y.params[0x56], 0.065, 6)

    def test_not_while_stall_armed(self):
        b = make_bus({'x': FakeMotor(FLASH_X)}, {'x': [(0x46, 100.)]})
        b.stall_armed = True
        b._configure_motor('x', 0x81)
        self.assertEqual(writes_sent(b), [])
        self.assertIn("stall homing", b.param_warnings[0])


if __name__ == "__main__":
    unittest.main()


class SubConfig:
    """[k2_motor_bus] plus the [stepper_*] sections it looks at."""

    def __init__(self, steppers):
        self.steppers = steppers

    def has_section(self, name):
        return name in self.steppers

    def getsection(self, name):
        micro = self.steppers[name]

        class Sec:
            def getint(self, option, default=None, note_valid=True):
                assert option == 'microsteps'
                return micro if micro is not None else default
        return Sec()

    def error(self, msg):
        return ValueError(msg)


class SubdivisionTest(unittest.TestCase):
    """0xc8 must equal the stepper's microsteps, or the axis moves 2x/0.5x
    the commanded distance: the wake marks that motor not ready."""

    def bus(self, writes=None):
        b = make_bus({'x': FakeMotor(HI_X), 'y': FakeMotor(HI_Y)}, writes)
        return b

    def test_expected_from_stepper_sections(self):
        b = self.bus()
        exp = b._parse_subdivision(SubConfig({'stepper_x': 16,
                                              'stepper_y': 32}))
        self.assertEqual(exp, {'x': 16, 'y': 32})

    def test_missing_stepper_section_is_not_checked(self):
        b = self.bus()
        self.assertEqual(b._parse_subdivision(SubConfig({'stepper_x': 16})),
                         {'x': 16})

    def test_write_that_disagrees_with_microsteps_refused_at_load(self):
        b = self.bus({'y': [(0xc8, 16)]})
        with self.assertRaises(ValueError):
            b._parse_subdivision(SubConfig({'stepper_x': 16,
                                            'stepper_y': 32}))

    def test_match_is_ready(self):
        b = self.bus()
        b.expected_subdivision = {'y': 16}
        self.assertTrue(b._check_subdivision('y', {0xc8: 16}))
        self.assertEqual(b.param_warnings, [])

    def test_mismatch_not_ready_and_says_why(self):
        b = self.bus()
        b.expected_subdivision = {'y': 32}
        self.assertFalse(b._check_subdivision('y', {0xc8: 16}))
        self.assertIn("NOT ready", b.param_warnings[0])
        self.assertIn("2x", b.param_warnings[0])

    def test_no_reading_not_ready(self):
        b = self.bus()
        b.expected_subdivision = {'y': 32}
        self.assertFalse(b._check_subdivision('y', {0xc8: None}))
        self.assertFalse(b._check_subdivision('y', {}))

    def test_written_value_is_checked_after_write(self):
        b = self.bus({'y': [(0xc8, 32)]})
        b.expected_subdivision = {'x': 16, 'y': 32}
        values = b._configure_motor('y', 0x82)
        self.assertEqual(values[0xc8], 32)
        self.assertTrue(b._check_subdivision('y', values))

    def test_dropped_write_fails_the_check(self):
        b = make_bus({'x': FakeMotor(HI_X),
                      'y': FakeMotor(HI_Y, ignore=(0xc8,))},
                     {'y': [(0xc8, 32)]})
        b.expected_subdivision = {'y': 32}
        values = b._configure_motor('y', 0x82)
        self.assertFalse(b._check_subdivision('y', values))
