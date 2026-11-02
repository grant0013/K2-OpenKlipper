import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class Obj:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class FakeBus:
    """X motor that reports a stall for any ram at or beyond stop_x."""
    def __init__(self, cfs, stop_x, noisy=()):
        self.cfs, self.stop_x, self.noisy = cfs, stop_x, set(noisy)
        self.cleared = 0

    def query_protection(self, name):
        x = self.cfs.last_ram
        stalled = x is not None and (x <= self.stop_x + 1e-9
                                     or round(x, 2) in self.noisy)
        self.cfs.last_ram = None
        return {'stalled': stalled, 'has_error': False}

    def clear_faults(self, name):
        self.cleared += 1


class FakeGcode:
    def __init__(self, cfs):
        self.cfs = cfs
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)
        for line in script.splitlines():
            if line.startswith("G1 X") and " Y" in line and "F30000" in line:
                self.cfs.last_ram = float(line.split()[1][1:])


class Cmd:
    def __init__(self, params):
        self.params = params
        self.info = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None, **kw):
        v = self.params.get(name, default)
        return None if v is None else float(v)

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


def make_cfs(stop_x, noisy=()):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.last_ram = None
    cfs.gcode = FakeGcode(cfs)
    bus = FakeBus(cfs, stop_x, noisy)
    objects = {'k2_motor_bus': bus,
               'toolhead': Obj({'max_accel': 12000.})}

    class P:
        def lookup_object(self, name, default=None):
            return objects.get(name, default)
    cfs.printer = P()
    cfs.status = {}
    cfs.cutter_cal_start_x = -5.
    cfs.cutter_stall_offset = .4
    cfs.cutter_x, cfs.cutter_y, cfs.cutter_speed = 10., 150., 200.
    cfs.cutter_ram_speed, cfs.cutter_ram_accel = 500., 5000.
    cfs.cutter_cut_x = -7.2
    cfs._require_idle = cfs._require_safe_motion = lambda *a: None
    cfs._nozzle_has_filament = lambda: False
    return cfs, bus


class CutterStallTest(unittest.TestCase):
    def run_cal(self, cfs, **params):
        p = {'METHOD': 'STALL', 'CONFIRM': '1'}
        p.update(params)
        cmd = Cmd(p)
        cfs.cmd_CFS_CUTTER_CALIBRATE(cmd)
        return cmd

    def test_two_hits_in_a_row_give_the_cut_position(self):
        cfs, bus = make_cfs(-8.0)
        cmd = self.run_cal(cfs)
        # hits at -8.0 and -8.1; cut 0.4 short of the second
        self.assertEqual(cfs.status['cutter_cal_x'], -7.7)
        self.assertIn('cutter_cut_x: -7.70', cmd.info[-1])
        # X re-homed after each hit
        self.assertEqual(sum('G28 X' in s for s in cfs.gcode.scripts), 2)

    def test_a_single_noisy_hit_is_not_the_answer(self):
        cfs, bus = make_cfs(-8.0, noisy=(-6.0,))
        self.run_cal(cfs)
        self.assertEqual(cfs.status['cutter_cal_x'], -7.7)

    def test_never_hits_fails_and_changes_nothing(self):
        cfs, bus = make_cfs(-20.)
        with self.assertRaises(RuntimeError) as ctx:
            self.run_cal(cfs)
        self.assertIn('unchanged', str(ctx.exception))
        self.assertIsNone(cfs.status['cutter_cal_x'])
        self.assertTrue(cfs.gcode.scripts[-1].endswith(
            'RESTORE_GCODE_STATE NAME=K2_CUT_STALL'))

    def test_needs_confirm_and_no_filament(self):
        cfs, bus = make_cfs(-8.0)
        with self.assertRaises(RuntimeError):
            self.run_cal(cfs, CONFIRM='0')
        cfs._nozzle_has_filament = lambda: True
        with self.assertRaises(RuntimeError):
            self.run_cal(cfs)


if __name__ == '__main__':
    unittest.main()
