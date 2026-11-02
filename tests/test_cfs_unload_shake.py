import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class Toolhead:
    def __init__(self, pos):
        self.pos = pos

    def get_position(self):
        return list(self.pos)

    def get_status(self, eventtime=None):
        return {'max_accel': 20000.}


class Reactor:
    def __init__(self):
        self.t = 0.

    def monotonic(self):
        return self.t

    def pause(self, t):
        self.t = t


class Cmd:
    def __init__(self):
        self.info = []

    def respond_info(self, msg):
        self.info.append(msg)


class FakeGcode:
    def __init__(self, cfs):
        self.cfs = cfs
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)
        if script.count("G1 X") and self.cfs.frees_on_shake:
            self.cfs.pin_in = False


def make_cfs(pos=(110., 291.5, 10.), frees=True, moves=6):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.gcode = FakeGcode(cfs)
    cfs.frees_on_shake = frees
    cfs.pin_in = True
    toolhead = Toolhead(pos)
    reactor = Reactor()

    class P:
        def lookup_object(self, name, default=None):
            return toolhead if name == 'toolhead' else default

        def get_reactor(self):
            return reactor
    cfs.printer = P()
    cfs.purge_x, cfs.purge_y = 110., 291.5
    cfs.purge_position_tolerance = 1.
    cfs.clean_right_x = 137.
    cfs.clean_speed, cfs.clean_accel = 300., 10000.
    cfs.sensor_poll_interval = .1
    cfs.unload_shake_moves = moves
    cfs.unload_shake_distance = 8.
    cfs._nozzle_has_filament = lambda: cfs.pin_in
    return cfs


class UnloadShakeTest(unittest.TestCase):
    def test_shake_frees_a_stuck_pin(self):
        cfs = make_cfs()
        cmd = Cmd()
        self.assertTrue(cfs._shake_sensor_pin(cmd))
        self.assertIn('freed it', cmd.info[-1])
        script = cfs.gcode.scripts[0]
        self.assertEqual(script.count("G1 X118.000 Y291.500"), 6)
        self.assertEqual(script.count("G1 X110.000 Y291.500"), 6)
        self.assertNotIn(" E", script)
        self.assertTrue(script.startswith("SET_VELOCITY_LIMIT ACCEL=10000"))
        # acceleration put back
        self.assertEqual(cfs.gcode.scripts[-1],
                         "SET_VELOCITY_LIMIT ACCEL=20000.000")

    def test_swing_stays_inside_the_wipe_path(self):
        cfs = make_cfs()
        cfs.unload_shake_distance = 20.
        cfs.clean_right_x = 115.
        cfs._shake_sensor_pin(Cmd())
        self.assertIn("G1 X115.000 Y291.500", cfs.gcode.scripts[0])
        self.assertNotIn("G1 X130.000", cfs.gcode.scripts[0])

    def test_pin_still_in_reports_false(self):
        cfs = make_cfs(frees=False)
        self.assertFalse(cfs._shake_sensor_pin(Cmd()))

    def test_no_shake_away_from_the_chute_or_when_off(self):
        cfs = make_cfs(pos=(205., 261., 10.))
        self.assertFalse(cfs._shake_sensor_pin(Cmd()))
        self.assertEqual(cfs.gcode.scripts, [])
        cfs = make_cfs(moves=0)
        self.assertFalse(cfs._shake_sensor_pin(Cmd()))
        self.assertEqual(cfs.gcode.scripts, [])


if __name__ == '__main__':
    unittest.main()
