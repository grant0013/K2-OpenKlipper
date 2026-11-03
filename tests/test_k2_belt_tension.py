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
proto = _load("k2_motor_proto")
bt = _load("k2_belt_tension")


class HelpersTest(unittest.TestCase):
    def test_be32_round_trip(self):
        self.assertEqual(bt.be32(0x11223344), [0x11, 0x22, 0x33, 0x44])
        self.assertEqual(bt.from_be32(b"\x11\x22\x33\x44"), 0x11223344)

    def test_parse_flash(self):
        data = bytes([3]) + bytes(bt.be32(1234) + bt.be32(500000)
                                  + bt.be32(560000))
        self.assertEqual(bt.parse_flash(data), (1234, 500000, 560000))
        self.assertIsNone(bt.parse_flash(b"\x03\x00"))

    def test_tension_fit_is_a_line_through_the_two_points(self):
        fit = bt.tension_fit(500000, 560000)
        self.assertAlmostEqual(bt.tension_from_adc(500000, fit), 140.)
        self.assertAlmostEqual(bt.tension_from_adc(560000, fit), 160.)
        self.assertAlmostEqual(bt.tension_from_adc(530000, fit), 150.)

    def test_uncalibrated_module_has_no_fit(self):
        self.assertIsNone(bt.tension_fit(bt.UNSET, 560000))
        self.assertIsNone(bt.tension_fit(0, 560000))
        self.assertIsNone(bt.tension_fit(500000, 500000))


class NextMoveTest(unittest.TestCase):
    def test_on_target_or_over_stops(self):
        self.assertEqual(bt.next_move(140., 139., 1), 0)   # within 2%
        self.assertEqual(bt.next_move(140., 180., 1), 0)   # never loosens

    def test_big_gap_moves_in_capped_steps_that_shrink(self):
        # gap capped at 10 -> 12 steps, less the attempt number
        self.assertEqual(bt.next_move(140., 100., 1), 11)
        self.assertEqual(bt.next_move(140., 100., 5), 7)
        self.assertEqual(bt.next_move(140., 100., 60), 3)
        self.assertEqual(bt.next_move(140., 100., 120), 1)

    def test_gives_up_after_200(self):
        self.assertIsNone(bt.next_move(140., 100., 201))


class FakeBus:
    """A tensioner at 0x21 that answers like the stock module."""
    def __init__(self):
        self.place, self.idle, self.full = 100, 500000, 560000
        self.adc = 512000           # 144 N on the line above
        self.sent = []

    def xfer(self, addr, payload, read_len=64, timeout=0.2, check=None,
             retry=None):
        payload = list(payload)
        self.sent.append((addr, payload))
        func = payload[1]
        if func == bt.FUNC_VERSION:
            data = [1, 2, 3, 4]
        elif func == bt.FUNC_READ_FLASH:
            data = [3] + bt.be32(self.place) + bt.be32(self.idle) \
                + bt.be32(self.full)
        elif func == bt.FUNC_WRITE_FLASH:
            self.place = bt.from_be32(payload[3:7])
            self.idle = bt.from_be32(payload[7:11])
            self.full = bt.from_be32(payload[11:15])
            data = payload[2:]
        elif func == bt.FUNC_READ_ADC:
            data = bt.be32(self.adc)
        elif func == bt.FUNC_MOVE:
            steps = bt.from_be32(payload[3:7])
            self.adc += 3000 * steps * (1 if payload[2] else -1)
            data = [payload[2]] + bt.be32(steps)
        frame = proto.build_frame(addr, [0x00, func] + data)
        return frame, frame


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def getint(self, name, default=None, **kw):
        return default

    def getfloat(self, name, default=None, **kw):
        return default


def make():
    reactor = types.SimpleNamespace(monotonic=lambda: 0., pause=lambda t: None)
    gcode = types.SimpleNamespace(register_command=lambda *a, **k: None,
                                  run_script_from_command=lambda s: None)
    printer = types.SimpleNamespace(
        get_reactor=lambda: reactor,
        lookup_object=lambda n, d=None: gcode if n == 'gcode' else d,
        register_event_handler=lambda *a: None,
        command_error=Exception)
    mod = bt.K2BeltTension(FakeConfig(printer))
    mod.bus = FakeBus()
    return mod


class ModuleTest(unittest.TestCase):
    def test_read_reports_tension_and_calibration(self):
        mod = make()
        info = mod.read('x')
        self.assertEqual(info['hw'], '0102')
        self.assertTrue(info['calibrated'])
        self.assertAlmostEqual(info['tension'], 144.)
        self.assertTrue(info['ok'])
        self.assertEqual(mod.bus.sent[0][0], 0x21)

    def test_tighten_moves_until_on_target_and_saves_the_place(self):
        mod = make()
        mod.bus.adc = 470000            # 130 N
        gcmd = types.SimpleNamespace(respond_info=lambda m: None)
        tension = mod._tighten('x', 140., gcmd)
        self.assertGreaterEqual(tension, 140. * 0.98)
        moves = [p for a, p in mod.bus.sent if p[1] == bt.FUNC_MOVE]
        self.assertTrue(moves)
        self.assertTrue(all(p[2] == 1 for p in moves))   # tighter only
        self.assertEqual(mod.bus.place,
                         100 + sum(bt.from_be32(p[3:7]) for p in moves))

    def test_over_target_is_left_alone(self):
        mod = make()
        mod.bus.adc = 540000            # 153 N
        gcmd = types.SimpleNamespace(respond_info=lambda m: None)
        mod._tighten('x', 140., gcmd)
        self.assertEqual([p for a, p in mod.bus.sent
                          if p[1] == bt.FUNC_MOVE], [])

    def test_wild_reading_raises(self):
        mod = make()
        mod.bus.adc = 5000000           # way past 600 N
        with self.assertRaises(Exception):
            mod.read('x')


if __name__ == "__main__":
    unittest.main()
