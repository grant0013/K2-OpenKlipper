import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class FakeGcode:
    def __init__(self):
        self.info = []

    def respond_info(self, msg):
        self.info.append(msg)


class Cmd(FakeGcode):
    def __init__(self, params=None):
        FakeGcode.__init__(self)
        self.params = params or {}

    def get(self, name, default=None):
        return self.params.get(name, default)


def make_cfs(path):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.gcode = FakeGcode()
    cfs.status = {}
    cfs.stats_path = path
    cfs.slot_stats = cfs._load_stats()
    return cfs


class SlotStatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'hark_cfs_stats.json')

    def test_counts_survive_a_restart(self):
        cfs = make_cfs(self.path)
        cfs._count('A', 'loads', ok=True)
        cfs._count('A', 'load_failures', ok=False)
        cfs._count('C', 'cut_misses')
        again = make_cfs(self.path)
        self.assertEqual(again.slot_stats['A']['loads'], 1)
        self.assertEqual(again.slot_stats['A']['recent'], [True, False])
        self.assertEqual(again.slot_stats['C']['cut_misses'], 1)

    def test_recent_history_is_capped_and_warns(self):
        cfs = make_cfs(self.path)
        for unused in range(12):
            cfs._count('B', 'loads', ok=True)
        self.assertEqual(len(cfs.slot_stats['B']['recent']), 10)
        for unused in range(3):
            cfs._count('B', 'load_failures', ok=False)
        self.assertTrue(any('failed 3 of its last 10' in m
                            for m in cfs.gcode.info))
        summary = cfs.status['slot_stats']['B']
        self.assertEqual(summary['recent_failures'], 3)

    def test_reset_and_report(self):
        cfs = make_cfs(self.path)
        cfs._count('D', 'clogs')
        cmd = Cmd()
        cfs.cmd_CFS_SLOT_STATS(cmd)
        self.assertIn('D: 0 loads', cmd.info[0])
        self.assertIn('1 clogs', cmd.info[0])
        cfs.cmd_CFS_SLOT_STATS(Cmd({'RESET': 'D'}))
        self.assertEqual(cfs.slot_stats['D']['clogs'], 0)
        with open(self.path) as f:
            self.assertEqual(json.load(f)['slots']['D']['clogs'], 0)

    def test_unwritable_file_does_not_raise(self):
        cfs = make_cfs('/nonexistent-dir/x.json')
        cfs._count('A', 'loads', ok=True)   # logged, not raised
        self.assertEqual(cfs.slot_stats['A']['loads'], 1)


if __name__ == '__main__':
    unittest.main()
