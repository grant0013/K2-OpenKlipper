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


class FakeExtruder:
    def __init__(self, target):
        self.heater = Obj({'target': target})

    def get_heater(self):
        return self.heater


class FakePrinter:
    def __init__(self, target, temps):
        self.objects = {
            'extruder': FakeExtruder(target),
            'hark_cfs_slots': Obj({'slots': {
                s: {'nozzle_temp': t} for s, t in temps.items()}})}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return None


class FakeGcode:
    def __init__(self):
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def create_gcode_command(self, name, line, params):
        return Cmd(params)


class Cmd:
    def __init__(self, params=None):
        self.params = params or {}
        self.info = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


TEMPS = {'A': 250., 'B': 250., 'C': 220., 'D': 220.}


def make_cfs(target, in_nozzle, temps=TEMPS):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.printer = FakePrinter(target, temps)
    cfs.gcode = FakeGcode()
    cfs.toolchange_temp_margin = 3.
    cfs.print_start_transaction = False
    cfs.status = {}
    cfs.calls = calls = []
    cfs._require_actuation = cfs._require_safe_motion = \
        cfs._require_hot_extruder = lambda *a: None
    cfs._run_query = lambda name: {'slots_loaded': [True] * 4}
    cfs._measuring = lambda: {}
    cfs._path_mask = lambda m: cfs.SLOT_MASKS[in_nozzle]
    cfs._nozzle_has_filament = lambda: True
    cfs._move_to_cut_start = lambda: calls.append('cut_start')
    cfs.cmd_CFS_CUT_UNLOAD = lambda g: calls.append(
        ('unload', list(cfs.gcode.scripts)))
    cfs.cmd_CFS_LOAD = lambda g: calls.append('load')
    cfs.cmd_CFS_PURGE = lambda g: calls.append('purge')
    cfs._best_effort_stop = lambda mask: None
    return cfs


class OldFilamentTempTest(unittest.TestCase):
    def start(self, cfs, slot):
        cfs._load_and_purge(Cmd({'SLOT': slot, 'CONFIRM': '1'}),
                            'CFS_PRINT_START')

    def test_petg_left_in_for_a_pla_print_is_cleared_hot(self):
        cfs = make_cfs(220., 'A')
        self.start(cfs, 'C')
        unload = [c for c in cfs.calls if c[0] == 'unload'][0]
        # heated to PETG's 250 and waited before the cut
        self.assertEqual(unload[1], [
            "M104 S250.0\nTEMPERATURE_WAIT SENSOR=extruder MINIMUM=247.0"])
        # and back to the print's 220 afterwards
        self.assertEqual(cfs.gcode.scripts[-1], "M104 S220.0")

    def test_pla_left_in_for_a_petg_print_changes_nothing(self):
        cfs = make_cfs(250., 'C')
        self.start(cfs, 'A')
        self.assertEqual(cfs.gcode.scripts, [])

    def test_same_slot_loaded_changes_nothing(self):
        cfs = make_cfs(220., 'C')
        cfs._start_feed = lambda *a, **k: None
        cfs._ensure_purge_position = lambda g: None
        self.start(cfs, 'C')
        self.assertEqual(cfs.gcode.scripts, [])

    def test_unknown_old_temperature_changes_nothing(self):
        cfs = make_cfs(220., 'A', temps={'C': 220.})
        self.start(cfs, 'C')
        self.assertEqual(cfs.gcode.scripts, [])

    def test_failure_still_goes_back_to_the_print_temperature(self):
        cfs = make_cfs(220., 'A')

        def fail(g):
            raise RuntimeError("load failed")
        cfs.cmd_CFS_LOAD = fail
        with self.assertRaises(RuntimeError):
            self.start(cfs, 'C')
        self.assertEqual(cfs.gcode.scripts[-1], "M104 S220.0")


if __name__ == '__main__':
    unittest.main()
