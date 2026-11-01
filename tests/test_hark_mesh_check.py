import importlib.util
import pathlib
import types
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras"
    / "hark_mesh_check.py"
)
SPEC = importlib.util.spec_from_file_location("hark_mesh_check", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CommandError(Exception):
    pass


class FakeMesh:
    def __init__(self, matrix):
        self.matrix = matrix

    def get_probed_matrix(self):
        return self.matrix

    def get_mesh_params(self):
        return {'min_x': 50., 'max_x': 210., 'min_y': 50., 'max_y': 210.}


class FakeGcmd:
    def __init__(self, params):
        self.params = params
        self.info = []
        self.raw = []

    def get_float(self, name, default=None, **kw):
        v = self.params.get(name)
        return default if v is None else float(v)

    def get_int(self, name, default=None, **kw):
        v = self.params.get(name)
        return default if v is None else int(v)

    def respond_info(self, msg):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def error(self, msg):
        return CommandError(msg)


def make(matrix):
    mesh = types.SimpleNamespace(get_mesh=lambda: FakeMesh(matrix))
    gcode = types.SimpleNamespace(register_command=lambda *a, **k: None)
    objects = {'gcode': gcode, 'bed_mesh': mesh}
    printer = types.SimpleNamespace(
        lookup_object=lambda n, d=None: objects.get(n, d),
        command_error=CommandError)
    config = types.SimpleNamespace(get_printer=lambda: printer)
    return MODULE.HarkMeshCheck(config)


GOOD = [[0., 0.01, 0.], [0.01, 0.02, 0.01], [0., 0.01, 0.]]
BAD = [[0., 0.01, 0.], [0.01, 0.02, 0.01], [0., 0.01, 0.6]]
LIMITS = {'MAX_DEVIATION': 0.35, 'MAX_SLOPE': 0.006}


class MeshCheckTest(unittest.TestCase):
    def test_good_mesh_passes(self):
        check = make(GOOD)
        gcmd = FakeGcmd(dict(LIMITS))
        check.cmd_BED_MESH_CHECK(gcmd)
        self.assertTrue(check.get_status(0)['ok'])
        self.assertEqual(check.get_status(0)['summary'], "")

    def test_bad_mesh_raises_by_default(self):
        check = make(BAD)
        with self.assertRaises(CommandError):
            check.cmd_BED_MESH_CHECK(FakeGcmd(dict(LIMITS)))
        self.assertFalse(check.get_status(0)['ok'])

    def test_bad_mesh_reports_without_raising(self):
        check = make(BAD)
        gcmd = FakeGcmd(dict(LIMITS, REPORT=1))
        check.cmd_BED_MESH_CHECK(gcmd)
        status = check.get_status(0)
        self.assertFalse(status['ok'])
        self.assertIn("deviation 0.600mm (max 0.350)", status['summary'])
        self.assertIn("slope", status['summary'])
        self.assertTrue(gcmd.raw[0].startswith("!! Bed mesh check"))

    def test_good_after_bad_clears_ok(self):
        check = make(BAD)
        check.cmd_BED_MESH_CHECK(FakeGcmd(dict(LIMITS, REPORT=1)))
        check2 = make(GOOD)
        check2.last = check.last
        check2.cmd_BED_MESH_CHECK(FakeGcmd(dict(LIMITS, REPORT=1)))
        self.assertTrue(check2.get_status(0)['ok'])


if __name__ == "__main__":
    unittest.main()
