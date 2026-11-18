"""Latched X/Y faults around homing (jperry004's creality-hi-homing-fix,
bug 2) and MOTOR_INIT's guard (bug 1).

A failed HI_HOME_X / G28 Y aborts homing_override before MOTOR_STALL_MODE
DATA=2, so the tracking fault stays latched, every later home fails until a
power cycle, and stall_armed stays True (which keeps hark_safety off the
bus). k2_motor_bus now returns X/Y to mode 2 and clears faults on
stepper_enable:motor_off (a failed G28 sends it) when it armed stall homing.
"""
import importlib
import pathlib
import re
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


class FakeGcode:
    def __init__(self):
        self.raw = []

    def respond_info(self, msg):
        pass

    def respond_raw(self, msg):
        self.raw.append(msg)


class FakeMutex:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def test(self):
        return False


class FakeReactor:
    def __init__(self):
        self.callbacks = []

    def monotonic(self):
        return 100.

    def register_callback(self, cb):
        self.callbacks.append(cb)

    def run_callbacks(self):
        cbs, self.callbacks = self.callbacks, []
        for cb in cbs:
            cb(100.)


class FakeStats:
    def __init__(self, state='standby'):
        self.state = state

    def get_status(self, eventtime):
        return {'state': self.state}


class FakeToolhead:
    def __init__(self, log):
        self.log = log

    def wait_moves(self):
        self.log.append('wait_moves')


class FakePrinter:
    def __init__(self, objects):
        self.objects = objects

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


HEALTHY = [0] * 8
# Error bit 8: excessive position tracking error
TRACKING = [0x00, 0x01, 0, 0, 0, 0, 0, 0]


def reply(addr, func, data, status=0x00):
    return proto.build_frame(addr, [status, func] + list(data))


def make_bus(faulted=('y',), stall_armed=True):
    b = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
    b.reactor = FakeReactor()
    b.events = []
    b.stats = FakeStats()
    b.printer = FakePrinter({'print_stats': b.stats,
                             'toolhead': FakeToolhead(b.events)})
    b.gcode = FakeGcode()
    b.bus_motors = (('x', 0x81), ('y', 0x82))
    b.stall_motors = b.bus_motors
    b.extruder_addr = 0x81
    b.e_send = None
    b.stall_mode_check = 'warn'
    b.homing_fault_check = 'off'
    b.console_frames = False
    b.last_faults = {}
    b.stall_armed = stall_armed
    b.stall_changed = 0.
    b._stall_gen = 0
    b._stall_lock = FakeMutex()
    b._closed = False
    b.sent = []
    latched = dict((a, a in [dict(b.bus_motors)[n] for n in faulted])
                   for n, a in b.bus_motors)

    def xfer(addr, payload, read_len=64, timeout=0.2, check=None,
             retry=None):
        payload = tuple(payload)
        b.sent.append((addr, payload))
        frame = proto.build_frame(addr, payload)
        if payload[:2] == (0x00, 0x11):
            return frame, reply(addr, 0x11, [payload[2]])
        if payload == bus_mod.STATUS:
            if latched[addr]:
                return frame, reply(addr, 0x0c, TRACKING, status=0x02)
            return frame, reply(addr, 0x0c, HEALTHY)
        if payload == bus_mod.CLEAR_ERRORS:
            latched[addr] = False
            return frame, b''
        raise AssertionError("unexpected frame %s" % (frame.hex(' '),))
    b.xfer = xfer
    return b


def kinds(b):
    names = {(0x00, 0x11, 0x02): 'mode2', (0x00, 0x11, 0x01): 'mode1',
             bus_mod.STATUS: 'status', bus_mod.CLEAR_ERRORS: 'clear'}
    return [(a, names.get(p, p)) for a, p in b.sent]


class MotorOffTest(unittest.TestCase):
    def test_nothing_when_not_armed(self):
        b = make_bus(stall_armed=False)
        b._handle_motor_off()
        self.assertEqual(b.reactor.callbacks, [])
        self.assertEqual(b.sent, [])

    def test_armed_failed_home_sends_mode2_then_clears(self):
        b = make_bus(faulted=('y',))
        b._handle_motor_off()
        # Nothing on the bus from inside the event itself
        self.assertEqual(b.sent, [])
        self.assertTrue(b.stall_armed)
        b.reactor.run_callbacks()
        sent = kinds(b)
        self.assertEqual(sent[:2], [(0x81, 'mode2'), (0x82, 'mode2')])
        # Both motors cleared (always, even the one that reads clean)
        self.assertIn((0x81, 'clear'), sent)
        self.assertIn((0x82, 'clear'), sent)
        self.assertGreater(sent.index((0x82, 'clear')),
                           sent.index((0x82, 'mode2')))
        self.assertFalse(b.stall_armed)
        self.assertEqual(b.stall_changed, 100.)
        self.assertEqual(b.gcode.raw, [])

    def test_newer_stall_mode_wins(self):
        b = make_bus()
        b._handle_motor_off()
        # The next homing arms again before the callback runs
        b.cmd_MOTOR_STALL_MODE(FakeGcmd(DATA=1))
        b.sent = []
        b.reactor.run_callbacks()
        self.assertEqual(b.sent, [])
        self.assertTrue(b.stall_armed)

    def test_not_after_close(self):
        b = make_bus()
        b._handle_motor_off()
        b._closed = True
        b.reactor.run_callbacks()
        self.assertEqual(b.sent, [])

    def test_disarmed_meanwhile_is_left_alone(self):
        b = make_bus()
        b._handle_motor_off()
        b.cmd_MOTOR_STALL_MODE(FakeGcmd(DATA=2))
        b.sent = []
        b.reactor.run_callbacks()
        self.assertEqual(b.sent, [])
        self.assertFalse(b.stall_armed)

    def test_stall_mode_bumps_generation(self):
        b = make_bus(stall_armed=False)
        b.cmd_MOTOR_STALL_MODE(FakeGcmd(DATA=1))
        self.assertTrue(b.stall_armed)
        self.assertEqual(b._stall_gen, 1)
        b.cmd_MOTOR_STALL_MODE(FakeGcmd(DATA=2))
        self.assertFalse(b.stall_armed)
        self.assertEqual(b._stall_gen, 2)


class MotorInitTest(unittest.TestCase):
    def make(self, state):
        b = make_bus(stall_armed=False)
        b.stats.state = state
        b.wake_motors = lambda: b.events.append('wake') or True
        b.motors = {'x': True, 'y': True}
        return b

    def test_refused_while_printing_or_paused(self):
        for state in ('printing', 'paused'):
            b = self.make(state)
            with self.assertRaises(GcmdError) as cm:
                b.cmd_MOTOR_INIT(FakeGcmd())
            self.assertIn(state, str(cm.exception))
            self.assertEqual(b.events, [])

    def test_waits_for_moves_before_the_wake(self):
        for state in ('standby', 'complete', 'cancelled', 'error'):
            b = self.make(state)
            b.cmd_MOTOR_INIT(FakeGcmd())
            self.assertEqual(b.events, ['wait_moves', 'wake'])


def homing_override(path):
    text = (ROOT / path).read_text()
    m = re.search(r'^\[homing_override\]\n(.*?)(?=^\[)', text, re.M | re.S)
    return m.group(1)


def commands(block):
    return [line.strip() for line in block.splitlines()
            if line.strip() and not line.strip().startswith(('#', '{%'))]


class HiHomingOverrideTest(unittest.TestCase):
    def setUp(self):
        self.text = homing_override("config/mainline_stage2_hi.cfg")

    def axis_block(self, axis):
        m = re.search(r"\{%% if home_all or '%s' in params %%\}(.*?)"
                      r"\{%% endif %%\}" % axis, self.text, re.S)
        return commands(m.group(1))

    def test_x_order(self):
        self.assertEqual(self.axis_block('X'), [
            'MOTOR_STALL_MODE DATA=2', 'MOTOR_CLEAR_FAULTS',
            'MOTOR_STALL_MODE DATA=1', 'HI_HOME_X',
            'MOTOR_STALL_MODE DATA=2', 'MOTOR_CLEAR_FAULTS',
            'G91', 'G1 X19 F12000', 'G90'])

    def test_y_order(self):
        self.assertEqual(self.axis_block('Y'), [
            'MOTOR_STALL_MODE DATA=2', 'MOTOR_CLEAR_FAULTS',
            'MOTOR_STALL_MODE DATA=1', 'G28 Y',
            'MOTOR_STALL_MODE DATA=2', 'MOTOR_CLEAR_FAULTS',
            'G91', 'G1 Y10 F9000', 'G90'])

    def test_raw_status_reads_gone(self):
        self.assertNotIn('000c0b', self.text)
        self.assertNotIn('MOTOR_BUS_SEND', self.text)


if __name__ == "__main__":
    unittest.main()
