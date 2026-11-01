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
    def __init__(self, speed):
        self.speed = speed

    def get_status(self, eventtime):
        return {'speed': self.speed}


class FakeTach:
    def __init__(self):
        self.rpm = 0

    def get_status(self, eventtime):
        return {'fan1_speed': self.rpm, 'fan2_speed': 0}


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
    watch.fans = FakeTach()
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
    def test_slowed_for_probing_is_not_a_fault(self):
        # 11-01: capped at 60% the tachometer reads 0
        watch, gcode = make()
        watch.hotend_fan.speed = 0.6
        watch.fans.rpm = 0
        run(watch, 0., 40.)
        self.assertEqual(gcode.scripts, [])
        self.assertFalse(watch.tripped)

    def test_full_power_and_turning_is_fine(self):
        watch, gcode = make()
        watch.fans.rpm = 12000
        run(watch, 0., 60.)
        self.assertEqual(gcode.scripts, [])

    def test_full_power_and_stopped_pauses(self):
        watch, gcode = make()
        watch.fans.rpm = 0
        run(watch, 0., 40.)
        self.assertEqual(gcode.scripts, ["PAUSE"])
        self.assertIn("hotend fan", gcode.raw[-1])

    def test_back_to_full_after_probing_gets_spinup_time(self):
        watch, gcode = make()
        watch.hotend_fan.speed = 0.6
        t = run(watch, 0., 30.)
        watch.hotend_fan.speed = 1.
        watch.fans.rpm = 0
        run(watch, t, 9.)        # within fan_spinup_time (10 s)
        self.assertEqual(gcode.scripts, [])


if __name__ == "__main__":
    unittest.main()
