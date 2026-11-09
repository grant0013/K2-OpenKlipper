import importlib.util
import pathlib
import types
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras" / "hark_chamber.py"
)
SPEC = importlib.util.spec_from_file_location("hark_chamber", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeHeater:
    def __init__(self):
        self.target = 0.
        self.temp = 25.
        self.power = 0.
        self.control = types.SimpleNamespace(heater_max_power=1.)

    def set_temp(self, degrees):
        self.target = degrees

    def get_temp(self, eventtime):
        return self.temp, self.target

    def get_status(self, eventtime):
        return {'temperature': self.temp, 'target': self.target,
                'power': self.power}


class FakeTach:
    """The PTC heater_fan: its status carries Klipper's rpm."""
    def __init__(self):
        self.speed = 0

    def get_status(self, eventtime):
        return {'speed': 1.0, 'rpm': self.speed}


class FakeGcode:
    def __init__(self):
        self.raw = []
        self.info = []

    def register_command(self, *a, **k):
        pass

    def respond_raw(self, msg):
        self.raw.append(msg)

    def respond_info(self, msg):
        self.info.append(msg)


class FakeConfig:
    def __init__(self, printer, values):
        self.printer = printer
        self.values = values

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getboolean(self, name, default=None):
        return self.values.get(name, default)

    def getfloat(self, name, default=None, **kw):
        return float(self.values.get(name, default))


def make(enable=True):
    gcode = FakeGcode()
    chamber = FakeHeater()
    bed = FakeHeater()
    tach = FakeTach()
    pheaters = types.SimpleNamespace(
        lookup_heater=lambda n: chamber if n == "chamber_heater" else bed)
    objects = {'gcode': gcode, 'heaters': pheaters, 'heater_bed': object(),
               'heater_fan ptc_fan': tach}
    reactor = types.SimpleNamespace(
        monotonic=lambda: 0., NOW=0., register_timer=lambda *a: None)
    printer = types.SimpleNamespace(
        get_reactor=lambda: reactor,
        lookup_object=lambda n, d=None: objects.get(n, d),
        register_event_handler=lambda *a: None)
    ch = MODULE.HarkChamber(FakeConfig(printer, {'enable': enable}))
    ch._ready()
    return ch, chamber, bed, tach, gcode


class ChamberTest(unittest.TestCase):
    def test_forty_and_under_never_heats(self):
        ch, heater, _, _, _ = make()
        ch._request(40., "test")
        ch._supervise(1.)
        self.assertEqual(heater.target, 0.)

    def test_disabled_never_heats(self):
        ch, heater, _, _, gcode = make(enable=False)
        heater.set_temp(55.)          # SET_HEATER_TEMPERATURE
        self.assertEqual(heater.target, 0.)
        self.assertIn("not enabled", gcode.info[-1])

    def test_capped_and_held_for_bed(self):
        ch, heater, bed, _, _ = make()
        bed.target, bed.temp = 90., 40.
        ch._request(70., "test")
        ch._supervise(1.)
        self.assertEqual(ch.requested, 60.)
        self.assertEqual(heater.target, 0.)
        bed.temp = 89.
        ch._supervise(2.)
        self.assertEqual(heater.target, 60.)

    def test_turn_off_while_held_cancels(self):
        ch, heater, bed, _, _ = make()
        bed.target, bed.temp = 90., 40.
        ch._request(55., "test")
        ch._supervise(1.)
        heater.set_temp(0.)           # TURN_OFF_HEATERS
        bed.temp = 90.
        ch._supervise(2.)
        self.assertEqual(heater.target, 0.)
        self.assertEqual(ch.requested, 0.)

    def test_soft_start_then_full_power(self):
        ch, heater, _, _, _ = make()
        ch._request(50., "test")
        ch._supervise(0.)
        self.assertEqual(heater.control.heater_max_power, .5)
        ch._supervise(61.)
        self.assertEqual(heater.control.heater_max_power, 1.)
        ch._request(0., "test")
        ch._supervise(62.)
        self.assertEqual(heater.control.heater_max_power, 1.)

    def test_dead_fan_switches_off(self):
        ch, heater, _, tach, gcode = make()
        ch._request(50., "test")
        heater.power = 1.
        for t in range(0, 26):
            ch._supervise(float(t))
        self.assertEqual(heater.target, 0.)
        self.assertIn("fan", ch.fault)
        self.assertTrue(gcode.raw[-1].startswith("!! Chamber heater off"))

    def test_spinning_fan_keeps_heating(self):
        ch, heater, _, tach, _ = make()
        ch._request(50., "test")
        heater.power, tach.speed = 1., 3000
        for t in range(0, 60):
            ch._supervise(float(t))
        self.assertEqual(heater.target, 50.)
        self.assertIsNone(ch.fault)

    def test_overtemp_switches_off(self):
        ch, heater, _, tach, _ = make()
        ch._request(50., "test")
        heater.power, tach.speed, heater.temp = 1., 3000, 61.
        for t in range(0, 32):
            ch._supervise(float(t))
        self.assertEqual(heater.target, 0.)
        self.assertIn("over", ch.fault)

    def test_overtemp_trips_with_the_heater_commanded_off(self):
        # watermark control has cut the power long before target + 10:
        # this is what a stuck-on driver looks like
        ch, heater, _, tach, _ = make()
        ch._request(50., "test")
        heater.power, tach.speed, heater.temp = 0., 3000, 61.
        for t in range(0, 32):
            ch._supervise(float(t))
        self.assertEqual(heater.target, 0.)
        self.assertIn("over", ch.fault)

    def test_new_request_clears_fault(self):
        ch, heater, _, _, _ = make()
        ch._trip("test")
        ch._request(50., "test")
        ch._supervise(1.)
        self.assertIsNone(ch.fault)
        self.assertEqual(heater.target, 50.)


if __name__ == "__main__":
    unittest.main()
