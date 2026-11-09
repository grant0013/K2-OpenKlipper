"""CFS_RUNOUT_REFILL RESUME=0: the RESUME macro loads a spool replaced by
hand, and resumes itself, so a failure has to raise."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_cfs_change_pause import FakeCmd, make_cfs


def refill_cfs(load_fails=False):
    cfs = make_cfs(state='paused')
    cfs.status['last_runout_refill'] = None
    cfs.nozzle = False
    cfs.runout_push_length = 30.
    cfs.runout_purge_length = 40.
    cfs.purge_retract_length = 0.
    cfs.purge_retract_speed = 30.
    cfs._require_hot_extruder = lambda g: None
    cfs._clear_runout_warning = lambda: None

    def load(g):
        cfs.calls.append(('load', g.params['SLOT']))
        if load_fails:
            raise RuntimeError("CFS slot B does not report filament")

    cfs.cmd_CFS_LOAD = load
    cfs.cmd_CFS_PURGE = lambda g: cfs.calls.append(('purge',))
    return cfs


class ResumeRefillTest(unittest.TestCase):
    def test_loads_and_purges_without_resuming(self):
        cfs = refill_cfs()
        cfs.cmd_CFS_RUNOUT_REFILL(FakeCmd(
            {'SLOT': 'B', 'CONFIRM': 1, 'RESUME': 0}))
        self.assertEqual([c for c in cfs.calls if c[0] in ('load', 'purge')],
                         [('load', 'B'), ('purge',)])
        self.assertNotIn("RESUME", cfs.gcode.scripts)
        self.assertEqual(cfs.status['last_runout_refill'], 'B')

    def test_raises_to_the_chute_height_before_moving_there(self):
        # a runout in the first layers pauses below purge_min_z
        cfs = refill_cfs()
        cfs._raise_to_gate = lambda: cfs.calls.append(('raise',))
        cfs.cmd_CFS_RUNOUT_REFILL(FakeCmd(
            {'SLOT': 'B', 'CONFIRM': 1, 'RESUME': 0}))
        names = [c[0] for c in cfs.calls]
        self.assertLess(names.index('raise'), names.index('purge_pos'))

    def test_a_failure_raises_so_the_macro_stops(self):
        cfs = refill_cfs(load_fails=True)
        with self.assertRaisesRegex(RuntimeError, "stays paused"):
            cfs.cmd_CFS_RUNOUT_REFILL(FakeCmd(
                {'SLOT': 'B', 'CONFIRM': 1, 'RESUME': 0}))
        self.assertFalse(cfs.print_start_transaction)
        self.assertNotIn("RESUME", cfs.gcode.scripts)

    def test_the_automatic_refill_still_resumes(self):
        cfs = refill_cfs()
        cfs.cmd_CFS_RUNOUT_REFILL(FakeCmd({'SLOT': 'C', 'CONFIRM': 1}))
        self.assertIn("RESUME", cfs.gcode.scripts)

    def test_a_failed_automatic_refill_stays_paused_quietly(self):
        cfs = refill_cfs(load_fails=True)
        cmd = FakeCmd({'SLOT': 'C', 'CONFIRM': 1})
        cfs.cmd_CFS_RUNOUT_REFILL(cmd)
        self.assertIn("stays paused", cmd.raw[0])
        self.assertNotIn("RESUME", cfs.gcode.scripts)


if __name__ == "__main__":
    unittest.main()
