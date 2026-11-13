import importlib
import pathlib
import struct
import sys
import types
import unittest

# k2_motor_bus imports Klipper's mcu module, so load it as klippy's extras
# package (needs Klipper's Python environment: pyserial, cffi, greenlet).
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
bus_mod = importlib.import_module("extras.k2_motor_bus")
proto = importlib.import_module("extras.k2_motor_proto")
safety_mod = importlib.import_module("extras.hark_safety")


class GcmdError(Exception):
    pass


class FakeGcmd:
    def __init__(self, **params):
        self.params = dict((k.upper(), str(v)) for k, v in params.items())
        self.info = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, minval=None, maxval=None):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return GcmdError(msg)


class FakeReactor:
    def __init__(self):
        self.now = 100.

    def monotonic(self):
        return self.now


class FakeStats:
    def __init__(self):
        self.state = 'standby'

    def get_status(self, eventtime):
        return {'state': self.state}


class FakeStepperEnable:
    def __init__(self):
        self.off_calls = 0

    def motor_off(self):
        self.off_calls += 1


class FakeToolhead:
    def __init__(self):
        self.waits = 0

    def wait_moves(self):
        self.waits += 1


class FakePrinter:
    def __init__(self, objects):
        self.objects = objects

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


def reply(addr, func, data, status=0x00):
    """A motor reply frame f7 addr len status func data crc."""
    return bus_mod.K2MotorBus._build(None, addr, [status, func] + list(data))


def float_bytes(value):
    return list(struct.pack('<f', value))


def make_bus(offsets=(250.0, 312.5), cal_reply=None, enabled=True):
    """A K2MotorBus without Klipper: xfer answers param reads with
    offsets[0] before the calibration and offsets[1] after it."""
    b = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
    b.reactor = FakeReactor()
    b.stats = FakeStats()
    b.stepper_enable = FakeStepperEnable()
    b.toolhead = FakeToolhead()
    b.printer = FakePrinter({'print_stats': b.stats,
                             'stepper_enable': b.stepper_enable,
                             'toolhead': b.toolhead})
    b.bus_motors = (('x', 0x81), ('y', 0x82))
    b.extruder_addr = 0x81
    b.e_send = None
    b.calibration_enabled = enabled
    b.calibration_timeout = 60.
    b.calibrating = False
    b._cal_pending = None
    b.last_calibration = None
    b.calibration_warnings = []
    b.last_faults = {}
    b.stall_armed = False
    b.stall_changed = 0.
    b.motors_ready = True
    b.console_frames = False
    b.sent = []
    b.calibrated = False
    if cal_reply is None:
        cal_reply = [0x01] + float_bytes(0.42) + float_bytes(offsets[1])

    def xfer(addr, payload, read_len=64, timeout=0.2, check=None,
             retry=None):
        payload = tuple(payload)
        b.sent.append((addr, payload, timeout, check, retry,
                       b.calibrating))
        frame = bus_mod.K2MotorBus._build(None, addr, payload)
        if payload[1] == proto.FUNC_ELEC_OFFSET_CALIBRATE:
            b.calibrated = True
            if cal_reply == 'none':
                return frame, b''
            return frame, reply(addr, proto.FUNC_ELEC_OFFSET_CALIBRATE,
                                cal_reply)
        if payload[:3] == (0x00, 0x06, 0x02):
            pid = payload[3]
            value = {0x09: offsets[1] if b.calibrated else offsets[0],
                     0x0a: 0.0, 0x19: 0.3}[pid]
            # Real replies carry the float alone (k2_motor_bus._read_params)
            return frame, reply(addr, 0x06, float_bytes(value))
        if payload == bus_mod.END_PARAM_READ:
            return frame, reply(addr, 0x06, [0x01])
        if payload == bus_mod.STATUS:
            return frame, reply(addr, 0x0c, [0] * 8)
        return frame, b''
    b.xfer = xfer
    return b


def calibration_frames(b):
    return [s for s in b.sent if s[1][1] == proto.FUNC_ELEC_OFFSET_CALIBRATE]


class ProtoTest(unittest.TestCase):
    def test_request_frame(self):
        frame = bus_mod.K2MotorBus._build(None, 0x82,
                                          proto.elec_offset_request())
        self.assertEqual(frame[:6].hex(), "f78204000400")
        self.assertTrue(proto.check_crc(frame))

    def test_ok_reply_decodes(self):
        frame = proto.parse_frame(reply(
            0x82, 0x04, [0x01] + float_bytes(0.5) + float_bytes(181.25)))
        d = proto.decode_elec_offset_reply(frame)
        self.assertTrue(d['ok'])
        self.assertAlmostEqual(d['max_deviation_deg'], 0.5)
        self.assertAlmostEqual(d['mean_pulse'], 181.25)

    def test_failed_status_is_not_ok(self):
        frame = proto.parse_frame(reply(
            0x82, 0x04, [0x02] + float_bytes(9.0) + float_bytes(0.0)))
        self.assertFalse(proto.decode_elec_offset_reply(frame)['ok'])

    def test_short_reply_is_not_ok(self):
        frame = proto.parse_frame(reply(0x82, 0x04, [0x01]))
        d = proto.decode_elec_offset_reply(frame)
        self.assertFalse(d['ok'])
        self.assertEqual(d['status_code'], 1)
        self.assertIsNone(d['mean_pulse'])


class CalibrateCommandTest(unittest.TestCase):
    def test_off_unless_enabled(self):
        b = make_bus(enabled=False)
        with self.assertRaises(GcmdError):
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))
        self.assertEqual(b.sent, [])

    def test_unknown_motor(self):
        b = make_bus()
        with self.assertRaises(GcmdError):
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='z'))

    def test_first_call_only_arms_and_turns_motors_off(self):
        b = make_bus()
        g = FakeGcmd(MOTOR='y')
        b.cmd_MOTOR_CALIBRATE(g)
        self.assertEqual(calibration_frames(b), [])
        self.assertEqual(b.stepper_enable.off_calls, 1)
        self.assertEqual(b._cal_pending[0], 'y')
        self.assertIn("CONFIRM=1", g.info[0])

    def test_confirm_without_arming_only_arms(self):
        b = make_bus()
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y', CONFIRM=1))
        self.assertEqual(calibration_frames(b), [])

    def test_confirm_for_other_motor_only_arms(self):
        b = make_bus()
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='x'))
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y', CONFIRM=1))
        self.assertEqual(calibration_frames(b), [])
        self.assertEqual(b._cal_pending[0], 'y')

    def test_confirm_too_late_only_arms(self):
        b = make_bus()
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))
        b.reactor.now += bus_mod.CALIBRATION_CONFIRM_WINDOW + 1
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y', CONFIRM=1))
        self.assertEqual(calibration_frames(b), [])

    def test_confirmed_run(self):
        b = make_bus(offsets=(0.0, 312.5))
        b.calibration_warnings = [
            "motor Y (82) calibration offset (param 09) is near zero: it "
            "may not be calibrated"]
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))
        g = FakeGcmd(MOTOR='y', CONFIRM=1)
        b.cmd_MOTOR_CALIBRATE(g)
        sent = calibration_frames(b)
        self.assertEqual(len(sent), 1)
        addr, payload, timeout, check, retry, calibrating = sent[0]
        self.assertEqual(addr, 0x82)
        self.assertEqual(payload, (0x00, 0x04, 0x00))
        self.assertEqual(timeout, 60.)
        self.assertTrue(check)
        self.assertFalse(retry)
        self.assertTrue(calibrating)
        self.assertFalse(b.calibrating)
        self.assertIsNone(b._cal_pending)
        self.assertGreaterEqual(b.stepper_enable.off_calls, 2)
        self.assertEqual(b.toolhead.waits, 1)
        self.assertTrue(b.last_calibration['ok'])
        self.assertAlmostEqual(b.last_calibration['before'][0x09], 0.0)
        self.assertAlmostEqual(b.last_calibration['after'][0x09], 312.5)
        # The near-zero warning for Y is gone once the offset is real
        self.assertEqual(b.calibration_warnings, [])
        self.assertIn("ok", g.info[-1])
        self.assertIn("Home all axes", g.info[-1])

    def test_failed_status_raises(self):
        b = make_bus(cal_reply=[0x03] + float_bytes(9.) + float_bytes(0.))
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='x'))
        with self.assertRaises(GcmdError) as cm:
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='x', CONFIRM=1))
        self.assertIn("FAILED (status 3)", str(cm.exception))
        self.assertFalse(b.last_calibration['ok'])
        self.assertFalse(b.calibrating)

    def test_no_answer_raises_and_clears_flag(self):
        b = make_bus(cal_reply='none')
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))
        with self.assertRaises(GcmdError) as cm:
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y', CONFIRM=1))
        self.assertIn("no valid answer", str(cm.exception))
        self.assertFalse(b.calibrating)

    def test_refused_while_printing_or_paused(self):
        for state in ('printing', 'paused'):
            b = make_bus()
            b.stats.state = state
            with self.assertRaises(GcmdError):
                b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))
            self.assertEqual(b.stepper_enable.off_calls, 0)
            self.assertEqual(b.sent, [])

    def test_print_started_between_calls_refuses(self):
        b = make_bus()
        b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))
        b.stats.state = 'printing'
        with self.assertRaises(GcmdError):
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y', CONFIRM=1))
        self.assertEqual(calibration_frames(b), [])

    def test_refused_while_stall_homing_armed(self):
        b = make_bus()
        b.stall_armed = True
        with self.assertRaises(GcmdError):
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))

    def test_refused_when_motors_not_awake(self):
        b = make_bus()
        b.motors_ready = False
        with self.assertRaises(GcmdError):
            b.cmd_MOTOR_CALIBRATE(FakeGcmd(MOTOR='y'))


class SafetyKeepsOffBusTest(unittest.TestCase):
    def test_bus_quiet_while_calibrating(self):
        s = safety_mod.HarkSafety.__new__(safety_mod.HarkSafety)
        s.homing = 0
        s.homing_changed = 0.
        s.poll_interval = 1.
        s.bus = types.SimpleNamespace(stall_armed=False, stall_changed=0.,
                                      calibrating=True)
        self.assertTrue(s.bus_quiet(1000.))
        s.bus.calibrating = False
        self.assertFalse(s.bus_quiet(1000.))


if __name__ == '__main__':
    unittest.main()
