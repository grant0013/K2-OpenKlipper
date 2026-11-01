import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class FakeGcodeMove:
    def __init__(self, raw_z, gcode_z):
        self.raw_z, self.gcode_z = raw_z, gcode_z

    def get_status(self, eventtime=None):
        return {'position': [0., 0., self.raw_z, 0.],
                'gcode_position': [0., 0., self.gcode_z, 0.]}


class FakePrinter:
    def __init__(self, gcode_move):
        self.gcode_move = gcode_move

    def lookup_object(self, name, default=None):
        return self.gcode_move if name == 'gcode_move' else default


def make_cfs(offset):
    # offset = SET_GCODE_OFFSET Z, so raw Z = G-code Z + offset
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.printer = FakePrinter(FakeGcodeMove(2.12 + offset, 2.12))
    cfs.purge_min_z = 4.5
    return cfs


class ToolchangeLiftTest(unittest.TestCase):
    def test_lift_clears_the_purge_gate_with_hot_nozzle_comp(self):
        # HT-P-2026-0416: PETG hot comp -0.039 put a G1 Z4.5 lift at raw
        # 4.461 and the change aborted on purge_min_z 4.5
        for offset in (-0.0457, -0.039, -0.01, 0., 0.02):
            cfs = make_cfs(offset)
            gcode_z = cfs._gcode_z(cfs.purge_min_z)
            self.assertGreaterEqual(gcode_z + offset, cfs.purge_min_z)
            self.assertLess(gcode_z + offset, cfs.purge_min_z + 0.01)

    def test_return_move_lands_on_the_raw_height(self):
        cfs = make_cfs(-0.0457)
        self.assertAlmostEqual(cfs._gcode_z(4.2) - 0.0457, 4.2, delta=0.002)


if __name__ == '__main__':
    unittest.main()
