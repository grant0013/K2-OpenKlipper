import importlib.util
import pathlib
import sys
import types
import unittest


EXTRAS = pathlib.Path(__file__).parents[1] / "klippy" / "extras"


def _load(name):
    spec = importlib.util.spec_from_file_location(
        "extras." + name, EXTRAS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["extras." + name] = module
    spec.loader.exec_module(module)
    return module


if "extras" not in sys.modules:
    package = types.ModuleType("extras")
    package.__path__ = [str(EXTRAS)]
    sys.modules["extras"] = package
_load("k2_motor_proto")
MODULE = _load("hark_safety")


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def getfloat(self, name, default=None, **kw):
        return default

    def getint(self, name, default=None, **kw):
        return default

    def getchoice(self, name, choices, default=None):
        return choices[default]


class FakeFan:
    """A Klipper fan with a tachometer: status carries speed and rpm (None
    when the fan has no tachometer_pin)."""
    def __init__(self, speed, rpm=0):
        self.speed = speed
        self.rpm = rpm

    def get_status(self, eventtime):
        return {'speed': self.speed, 'rpm': self.rpm}


class FakeGcode:
    def __init__(self):
        self.raw = []
        self.scripts = []

    def register_command(self, *a, **k):
        pass

    def respond_raw(self, msg):
        self.raw.append(msg)

    def respond_info(self, msg):
        pass

    def run_script(self, script):
        self.scripts.append(script)


class FakePrinter:
    def __init__(self):
        self.gcode = FakeGcode()

    def get_reactor(self):
        return types.SimpleNamespace(monotonic=lambda: 0.)

    def lookup_object(self, name, default=None):
        return self.gcode if name == 'gcode' else default

    def load_object(self, config, name):
        return types.SimpleNamespace(get_status=lambda t: {})

    def register_event_handler(self, *a):
        pass


def make():
    printer = FakePrinter()
    watch = MODULE.HarkSafety(FakeConfig(printer))
    watch.hotend_fan = FakeFan(1.)
    watch.part_fan = FakeFan(0.)
    return watch, printer.gcode


def run(watch, start, seconds, step=3.):
    t = start
    # as _poll(): no more checks once the watch has tripped
    while t < start + seconds and not watch.tripped:
        watch._check_fans(t)
        t += step
    return t


class HotendFanCheckTest(unittest.TestCase):
    def test_fan_without_a_tachometer_is_not_watched(self):
        watch, gcode = make()
        watch.hotend_fan.rpm = None
        run(watch, 0., 60.)
        self.assertEqual(gcode.scripts, [])
        self.assertNotIn('hotend fan', watch.last.get('fans', {}))

    def test_slowed_for_probing_is_not_a_fault(self):
        # 11-01: capped at 60% the tachometer reads 0
        watch, gcode = make()
        watch.hotend_fan.speed = 0.6
        watch.hotend_fan.rpm = 0
        run(watch, 0., 40.)
        self.assertEqual(gcode.scripts, [])
        self.assertFalse(watch.tripped)

    def test_full_power_and_turning_is_fine(self):
        watch, gcode = make()
        watch.hotend_fan.rpm = 12000
        run(watch, 0., 60.)
        self.assertEqual(gcode.scripts, [])

    def test_full_power_and_stopped_pauses(self):
        watch, gcode = make()
        watch.hotend_fan.rpm = 0
        run(watch, 0., 40.)
        self.assertEqual(gcode.scripts, ["PAUSE"])
        self.assertIn("hotend fan", gcode.raw[-1])

    def test_back_to_full_after_probing_gets_spinup_time(self):
        watch, gcode = make()
        watch.hotend_fan.speed = 0.6
        t = run(watch, 0., 30.)
        watch.hotend_fan.speed = 1.
        watch.hotend_fan.rpm = 0
        run(watch, t, 9.)        # within fan_spinup_time (10 s)
        self.assertEqual(gcode.scripts, [])


class BusQuietTest(unittest.TestCase):
    # 10-31: a protection query between MOTOR_STALL_MODE DATA=1 and G28 Y
    # made Y homing fail at a print start
    def setUp(self):
        self.watch, _ = make()
        self.watch.bus = types.SimpleNamespace(stall_armed=False,
                                               stall_changed=0.)

    def test_idle_bus_is_free(self):
        self.assertFalse(self.watch.bus_quiet(100.))

    def test_stall_armed_is_quiet(self):
        self.watch.bus.stall_armed = True
        self.assertTrue(self.watch.bus_quiet(100.))

    def test_quiet_while_homing_and_just_after(self):
        self.watch._homing_begin(None, [])
        self.assertTrue(self.watch.bus_quiet(100.))
        self.watch.homing_changed = 100.
        self.watch._homing_end(None, [])
        self.watch.homing_changed = 100.
        self.assertTrue(self.watch.bus_quiet(103.))
        self.assertFalse(self.watch.bus_quiet(107.))

    def test_quiet_just_after_stall_mode_change(self):
        self.watch.bus.stall_changed = 50.
        self.assertTrue(self.watch.bus_quiet(55.))
        self.assertFalse(self.watch.bus_quiet(57.))


if __name__ == "__main__":
    unittest.main()
