import importlib.util
import pathlib
import types
import unittest

PATH = pathlib.Path(__file__).parents[1] / "klippy" / "extras" / \
    "hark_bed_power.py"
spec = importlib.util.spec_from_file_location("hark_bed_power", PATH)
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)


class SlopeTest(unittest.TestCase):
    def test_slope_of_a_line(self):
        samples = [(10. + i, 40. + 0.5 * i) for i in range(20)]
        self.assertAlmostEqual(bp.slope_of(samples), 0.5)

    def test_too_few_samples(self):
        self.assertIsNone(bp.slope_of([(0, 1), (1, 2)]))

    def test_decide_uses_stocks_divide(self):
        self.assertEqual(bp.decide(0.30, 0.35), '110')
        self.assertEqual(bp.decide(0.35, 0.35), '110')
        self.assertEqual(bp.decide(0.55, 0.35), '220')


class FakeControl:
    def __init__(self):
        self.heater_max_power = 0.5
        self.Kp = self.Ki = self.Kd = 0.
        self.temp_integ_max = 0.


class FakeHeater:
    def __init__(self):
        self.max_power = 0.5
        self.control = FakeControl()
        self.last_temp = 25.


class FakeConfig:
    def __init__(self, printer, values=None):
        self.printer = printer
        self.values = values or {}

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getchoice(self, name, choices, default=None):
        return choices[self.values.get(name, default)]

    def getfloat(self, name, default=None, **kw):
        return self.values.get(name, default)

    def getfloatlist(self, name, default=None, **kw):
        return self.values.get(name, default)


def make(values=None, tmpdir=None):
    gcode = types.SimpleNamespace(register_command=lambda *a, **k: None)
    reactor = types.SimpleNamespace(monotonic=lambda: 0., pause=lambda t: t)
    heater = FakeHeater()
    pheaters = types.SimpleNamespace(
        lookup_heater=lambda n: heater,
        set_temperature=lambda h, t, w: None)
    objs = {'gcode': gcode, 'heaters': pheaters}
    printer = types.SimpleNamespace(
        get_reactor=lambda: reactor,
        lookup_object=lambda n, d=None: objs.get(n, d),
        register_event_handler=lambda *a: None,
        get_start_args=lambda: {'config_file': str(tmpdir / "printer.cfg")
                                if tmpdir else ''})
    mod = bp.HarkBedPower(FakeConfig(printer, values))
    return mod, heater


class ApplyTest(unittest.TestCase):
    def test_110_set_gives_full_power_and_its_pid(self):
        mod, heater = make()
        mod._ready()
        self.assertIsNone(mod.applied)      # auto, nothing saved yet
        mod.apply('110')
        self.assertEqual(heater.max_power, 1.0)
        self.assertEqual(heater.control.heater_max_power, 1.0)
        self.assertAlmostEqual(heater.control.Kp * 255., 52.98)
        self.assertAlmostEqual(heater.control.Kd * 255., 826.491)
        self.assertEqual(mod.applied, '110')

    def test_config_voltage_is_applied_at_ready(self):
        mod, heater = make({'voltage': '220'})
        mod._ready()
        self.assertEqual(mod.applied, '220')
        self.assertEqual(heater.max_power, 0.5)
        self.assertAlmostEqual(heater.control.Kp * 255., 18.578)

    def test_saved_result_is_applied_at_ready(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "hark_bed_power.json").write_text(
                '{"voltage": "110", "slope": 0.21}')
            mod, heater = make(tmpdir=d)
            mod._ready()
            self.assertEqual(mod.applied, '110')
            self.assertEqual(heater.max_power, 1.0)
            st = mod.get_status(0.)
            self.assertTrue(st['calibrated'])
            self.assertEqual(st['slope'], 0.21)


if __name__ == "__main__":
    unittest.main()
