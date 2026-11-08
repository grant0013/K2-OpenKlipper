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


class FakeToolhead:
    def __init__(self, raw_z):
        self.raw_z = raw_z

    def get_position(self):
        return [110., 291.5, self.raw_z, 0.]


class FakeGcode:
    def __init__(self):
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)


class GatePrinter(FakePrinter):
    def __init__(self, gcode_move, toolhead):
        FakePrinter.__init__(self, gcode_move)
        self.toolhead = toolhead

    def lookup_object(self, name, default=None):
        if name == 'toolhead':
            return self.toolhead
        return FakePrinter.lookup_object(self, name, default)


def gate_cfs(raw_z, gcode_z):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.printer = GatePrinter(FakeGcodeMove(raw_z, gcode_z),
                              FakeToolhead(raw_z))
    cfs.gcode = FakeGcode()
    cfs.purge_min_z = 4.5
    return cfs


class ChuteGateTest(unittest.TestCase):
    def test_tops_up_the_mesh_shortfall(self):
        # The printer on 11-07: G-code Z 4.511, raw 4.4964 at the chute
        cfs = gate_cfs(4.4964, 4.511)
        cfs._raise_to_gate()
        self.assertEqual(cfs.gcode.scripts, ["G90\nG1 Z4.516 F600\nM400"])
        z = float(cfs.gcode.scripts[0].split()[2][1:])
        self.assertGreaterEqual(4.4964 + (z - 4.511), cfs.purge_min_z)

    def test_a_large_mesh_offset_is_covered(self):
        cfs = gate_cfs(4.1, 4.511)
        cfs._raise_to_gate()
        z = float(cfs.gcode.scripts[0].split()[2][1:])
        self.assertGreaterEqual(4.1 + (z - 4.511), cfs.purge_min_z)
        self.assertLess(4.1 + (z - 4.511), cfs.purge_min_z + 0.01)

    def test_no_move_when_already_clear(self):
        for raw_z in (4.5, 4.51, 12.):
            cfs = gate_cfs(raw_z, raw_z)
            cfs._raise_to_gate()
            self.assertEqual(cfs.gcode.scripts, [])


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
