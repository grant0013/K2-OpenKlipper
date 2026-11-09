"""The last probe lift must finish before the mesh is built (GitHub #4)."""
import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
bed_mesh = importlib.import_module("extras.bed_mesh")


class Stop(Exception):
    pass


class WaitBeforeBuild(unittest.TestCase):
    def test_waits_before_touching_the_results(self):
        calls = []

        class Toolhead:
            def wait_moves(self):
                calls.append("wait")

        class Printer:
            def lookup_object(self, name, default=None):
                return Toolhead()

        class ProbeMgr:
            def get_zero_ref_mode(self):
                calls.append("build")
                raise Stop()

        cal = bed_mesh.BedMeshCalibrate.__new__(bed_mesh.BedMeshCalibrate)
        cal.printer = Printer()
        cal.probe_mgr = ProbeMgr()
        with self.assertRaises(Stop):
            cal.probe_finalize([])
        self.assertEqual(calls, ["wait", "build"])


if __name__ == "__main__":
    unittest.main()
