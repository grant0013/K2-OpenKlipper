"""Per-axis max_x_accel / max_y_accel in the cartesian kinematics (Hi).

The Creality Hi is a bed-slinger: at the toolhead's 12000 mm/s^2 the bed's
stops shift the whole printer. A move's accel is capped so the Y (or X) share
of it stays under the per-axis limit; X-only moves keep the full accel.
"""
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class FakeMove:
    """The parts of toolhead.Move that check_move uses."""
    def __init__(self, start, end, speed, accel):
        self.start_pos, self.end_pos = start, end
        self.axes_d = [e - s for s, e in zip(start, end)]
        self.move_d = sum(d * d for d in self.axes_d[:3]) ** .5
        self.accel = accel
        self.max_cruise_v2 = speed ** 2

    def limit_speed(self, speed, accel):
        self.max_cruise_v2 = min(self.max_cruise_v2, speed ** 2)
        self.accel = min(self.accel, accel)

    def move_error(self, msg="Move out of range"):
        return RuntimeError(msg)


class FakeGcode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, func, desc=None):
        self.commands[name] = func


class FakePrinter:
    def __init__(self):
        self.gcode = FakeGcode()

    def lookup_object(self, name):
        return self.gcode


class FakeConfig:
    def __init__(self, values):
        self.values = values
        self.printer = FakePrinter()

    def get_printer(self):
        return self.printer

    def getfloat(self, name, default, **kw):
        return self.values.get(name, default)

    def has_section(self, name):
        return False

    def getsection(self, name):
        return self


class FakeRail:
    def setup_itersolve(self, *a):
        pass

    def get_range(self):
        return (0., 300.)

    def get_steppers(self):
        return []


class FakeToolhead:
    def Coord(self, v):
        return v

    def get_max_velocity(self):
        return 300., 12000.

    def get_trapq(self):
        return None


class FakeGcmd:
    def __init__(self, params):
        self.params = params
        self.replies = []

    def get_float(self, name, default, **kw):
        return float(self.params.get(name, default))

    def respond_info(self, msg):
        self.replies.append(msg)


def load_kin(values):
    stepper = types.ModuleType("stepper")
    stepper.LookupMultiRail = lambda cfg: FakeRail()
    sys.modules["stepper"] = stepper
    pkg = types.ModuleType("kinematics")
    pkg.__path__ = [str(ROOT / "klippy" / "kinematics")]
    sys.modules["kinematics"] = pkg
    sys.modules["kinematics.idex_modes"] = types.ModuleType("idex_modes")
    spec = importlib.util.spec_from_file_location(
        "kinematics.cartesian", ROOT / "klippy/kinematics/cartesian.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    config = FakeConfig(values)
    kin = mod.CartKinematics(FakeToolhead(), config)
    kin.limits = [(-20., 300.), (-10., 300.), (0., 300.)]
    return kin, config


class AxisAccelTest(unittest.TestCase):
    def test_default_is_unlimited(self):
        kin, _ = load_kin({})
        m = FakeMove([0, 0, 5, 0], [0, 100, 5, 0], 300., 12000.)
        kin.check_move(m)
        self.assertEqual(m.accel, 12000.)

    def test_y_only_move_capped(self):
        kin, _ = load_kin({'max_y_accel': 3000.})
        m = FakeMove([0, 0, 5, 0], [0, 100, 5, 0], 300., 12000.)
        kin.check_move(m)
        self.assertAlmostEqual(m.accel, 3000.)
        self.assertEqual(m.max_cruise_v2, 300. ** 2)  # speed untouched

    def test_x_only_move_keeps_full_accel(self):
        kin, _ = load_kin({'max_y_accel': 3000.})
        m = FakeMove([0, 0, 5, 0], [100, 0, 5, 0], 300., 12000.)
        kin.check_move(m)
        self.assertEqual(m.accel, 12000.)

    def test_diagonal_y_share_under_limit(self):
        kin, _ = load_kin({'max_y_accel': 3000.})
        m = FakeMove([0, 0, 5, 0], [100, 100, 5, 0], 300., 12000.)
        kin.check_move(m)
        # Y share of the accel = accel * dy / d = the limit
        self.assertAlmostEqual(m.accel * 100 / m.move_d, 3000.)

    def test_lower_slicer_accel_not_raised(self):
        kin, _ = load_kin({'max_y_accel': 3000.})
        m = FakeMove([0, 0, 5, 0], [0, 100, 5, 0], 300., 1000.)
        kin.check_move(m)
        self.assertEqual(m.accel, 1000.)

    def test_z_limit_still_applies(self):
        kin, _ = load_kin({'max_y_accel': 3000., 'max_z_accel': 500.,
                           'max_z_velocity': 20.})
        m = FakeMove([0, 0, 5, 0], [0, 0, 10, 0], 300., 12000.)
        kin.check_move(m)
        self.assertAlmostEqual(m.accel, 500.)

    def test_runtime_command(self):
        kin, config = load_kin({'max_y_accel': 3000.})
        gcmd = FakeGcmd({'Y_ACCEL': '2000'})
        config.printer.gcode.commands['SET_KINEMATICS_LIMIT'](gcmd)
        self.assertEqual(kin.max_y_accel, 2000.)
        m = FakeMove([0, 0, 5, 0], [0, 100, 5, 0], 300., 12000.)
        kin.check_move(m)
        self.assertAlmostEqual(m.accel, 2000.)
        self.assertIn("max_y_accel: 2000", gcmd.replies[0])


if __name__ == "__main__":
    unittest.main()
