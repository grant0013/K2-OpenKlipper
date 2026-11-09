"""Typos and full disks are G-code errors: anything else raised from a
command handler makes Klipper shut down ("Internal error on command")."""
import importlib
import pathlib
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))


class GcodeError(Exception):
    pass


class Cmd:
    error = GcodeError

    def __init__(self, params=None):
        self.params = params or {}

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))

    def respond_info(self, msg):
        pass


class FlowCalTest(unittest.TestCase):
    def setUp(self):
        mod = importlib.import_module("extras.hark_flow_cal")
        self.mod = mod
        cls = next(v for v in vars(mod).values()
                   if isinstance(v, type) and hasattr(v, 'cmd_FLOW_CAL_RUN'))
        self.cal = cls.__new__(cls)
        self.cal.flows = [6., 8.]

    def test_trailing_comma(self):
        with self.assertRaisesRegex(GcodeError, "FLOWS"):
            self.cal.cmd_FLOW_CAL_RUN(Cmd({'FLOWS': '8,10,'}))

    def test_negative_flow(self):
        with self.assertRaisesRegex(GcodeError, "FLOWS"):
            self.cal.cmd_FLOW_CAL_RUN(Cmd({'FLOWS': '8,-10'}))

    def test_a_flat_gauge_has_no_scatter(self):
        points = [(6., 0.), (8., 0.), (10., 0.)]
        self.assertEqual(self.mod.baseline_scatter(points, 12.), 0.)


class CalibPickTest(unittest.TestCase):
    def test_bad_values(self):
        mod = importlib.import_module("extras.hark_calib")
        cls = next(v for v in vars(mod).values()
                   if isinstance(v, type) and hasattr(v, 'cmd_CALIB_PICK'))
        with self.assertRaisesRegex(GcodeError, "VALUES"):
            cls.__new__(cls).cmd_CALIB_PICK(Cmd(
                {'KIND': 'flow', 'SLOT': 'A', 'VALUES': 'abc'}))


class MotorBusTest(unittest.TestCase):
    def setUp(self):
        try:
            mod = importlib.import_module("extras.k2_motor_bus")
        except ImportError as exc:
            self.skipTest("needs Klipper's modules: %s" % exc)
        cls = next(v for v in vars(mod).values()
                   if isinstance(v, type) and hasattr(v, 'cmd_MOTOR_VALUES'))
        self.bus = cls.__new__(cls)

    def test_bad_index(self):
        with self.assertRaisesRegex(GcodeError, "INDEX"):
            self.bus._indices(Cmd({'INDEX': '5,'}), '5')

    def test_hex_index_still_works(self):
        self.assertEqual(self.bus._indices(Cmd({'INDEX': '5,0x11'}), '5'),
                         [5, 0x11])

    def test_bad_payload(self):
        with self.assertRaisesRegex(GcodeError, "PAYLOAD"):
            self.bus.cmd_MOTOR_BUS_SEND(Cmd(
                {'ADDR': 1, 'PAYLOAD': '0x000c0b'}))


class SlotStoreTest(unittest.TestCase):
    def test_a_failed_save_warns_and_carries_on(self):
        mod = importlib.import_module("extras.hark_cfs_slots")
        cls = next(v for v in vars(mod).values()
                   if isinstance(v, type) and hasattr(v, '_publish_lanes'))
        slots = cls.__new__(cls)
        slots.store_path = tempfile.mkdtemp() + "/missing/slots.json"
        slots.revision, slots.slots, slots.library = 1, {}, {}
        slots.tool_map, slots.tool_map_file = {}, ''
        raw, lanes = [], []
        slots.gcode = types.SimpleNamespace(respond_raw=raw.append)
        slots._publish_lanes = lambda: lanes.append(1)
        slots._save()
        self.assertIn("Could not save", raw[0])
        self.assertEqual(lanes, [1])


class MacroReloadTest(unittest.TestCase):
    def test_refused_during_a_print(self):
        try:
            mod = importlib.import_module("extras.hark_macro_reload")
        except ImportError as exc:
            self.skipTest("needs jinja2: %s" % exc)
        cls = next(v for v in vars(mod).values()
                   if isinstance(v, type)
                   and hasattr(v, 'cmd_RELOAD_GCODE_MACROS'))
        for state in ('printing', 'paused'):
            reload = cls.__new__(cls)
            stats = types.SimpleNamespace(
                get_status=lambda t, s=state: {'state': s})
            reload.printer = types.SimpleNamespace(
                lookup_object=lambda n, d=None: stats,
                get_reactor=lambda: types.SimpleNamespace(
                    monotonic=lambda: 0.))
            with self.assertRaisesRegex(GcodeError, "not while"):
                reload.cmd_RELOAD_GCODE_MACROS(Cmd())


if __name__ == "__main__":
    unittest.main()
