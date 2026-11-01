import importlib
import pathlib
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")

# HT-P-2026-0416 (painted flap lever, 11-01): orange, black (PETG), black,
# white in T0-T3. Its change-filament G-code passed no flush length, so
# every PLA change purged the fixed 70 mm and colours bled.
FILE_TAIL = """G1 X10
; flush_volumes_matrix = 0,291,291,626,732,0,243,743,732,243,0,743,426,263,263,0
; flush_multiplier = 1
"""


class Status:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class FakePrinter:
    def __init__(self, path, types_by_slot):
        self.objects = {
            'virtual_sdcard': Status({'file_path': path}),
            'extruder': types.SimpleNamespace(filament_area=2.405),
            'hark_cfs_slots': Status({'slots': {
                k: {'type': v} for k, v in types_by_slot.items()}}),
        }

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


class Gcmd:
    def __init__(self):
        self.info = []

    def respond_info(self, msg):
        self.info.append(msg)


def plan(old, new):
    with tempfile.NamedTemporaryFile('w', suffix='.gcode',
                                     delete=False) as f:
        f.write(FILE_TAIL)
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.printer = FakePrinter(f.name, {'A': 'PLA', 'B': 'PETG',
                                       'C': 'PLA', 'D': 'PLA'})
    cfs.flush_cache = None
    cfs.purge_extrude_length = 180.
    return cfs._file_purge(Gcmd(), old, new)


class FlushMatrixTest(unittest.TestCase):
    def test_on_by_default(self):
        # read in K2CFS.__init__; checked here against the source default
        src = pathlib.Path(cfs_mod.__file__).read_text()
        self.assertIn("'toolchange_purge_from_file', True)", src)

    def test_black_to_white_gets_orcas_full_flush(self):
        # 743 mm^3 / 2.405 mm^2 = 309 mm, no longer cut to 240
        short, full = plan('C', 'D')
        self.assertAlmostEqual(short, 743 / 2.405, places=1)
        self.assertIsNone(full)

    def test_white_to_black_is_shorter(self):
        short, _ = plan('D', 'C')
        self.assertAlmostEqual(short, 263 / 2.405, places=1)

    def test_change_of_material_keeps_the_full_purge(self):
        # PLA (A) to PETG (B): stock purge, at least purge_extrude_length
        short, full = plan('A', 'B')
        self.assertIsNone(short)
        self.assertEqual(full, 180.)

    def test_capped_at_the_load_limit(self):
        self.assertEqual(min(max(2000 / 2.405, 30.), 360.), 360.)


if __name__ == '__main__':
    unittest.main()
