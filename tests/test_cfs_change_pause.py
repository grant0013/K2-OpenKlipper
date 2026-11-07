import importlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class FakeReactor:
    def monotonic(self):
        return 0.

    def pause(self, until):
        pass


class FakeCmd:
    def __init__(self, params=None):
        self.params = params or {}
        self.info, self.raw = [], []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_float(self, name, default=None, **kw):
        v = self.params.get(name, default)
        return None if v is None else float(v)

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


class FakeGcode:
    def __init__(self):
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def respond_info(self, msg):
        pass

    def create_gcode_command(self, name, line, params):
        cmd = FakeCmd(params)
        cmd.name = name
        return cmd


class Obj:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class FakeToolhead(Obj):
    def get_position(self):
        return [100., 120., 2.4, 0.]


class FakeExtruder:
    def get_heater(self):
        return Obj({'target': 240.})


class FakePrinter:
    def __init__(self, state):
        self.print_stats = Obj({'state': state})
        self.objects = {'print_stats': self.print_stats,
                        'toolhead': FakeToolhead({}),
                        'extruder': FakeExtruder()}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return FakeReactor()


def make_cfs(load_errors=0, state='printing'):
    """CFS at a colour change A>B; the first load_errors loads fail."""
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.printer = FakePrinter(state)
    cfs.gcode = FakeGcode()
    cfs.status = {'change_pending': None}
    cfs.load_retries = 2
    cfs.purge_extrude_length = 180.
    cfs.stats_path = '/nonexistent/hark_cfs_stats.json'
    cfs.slot_stats = cfs._load_stats()
    cfs._save_stats = lambda: None
    cfs.pending_change = None
    cfs.print_start_transaction = False
    cfs.toolchange_purge_from_file = False
    cfs.toolchange_lift = 3.
    cfs.purge_min_z = 4.5
    cfs.toolchange_temp_margin = 3.
    cfs.purge_speed = 300.
    cfs.calls = calls = []
    cfs.nozzle = True
    cfs.active = 0x01
    for name in ('_require_cfs', '_require_actuation'):
        setattr(cfs, name, lambda *a: None)
    cfs._measuring = lambda: {}
    cfs._path_mask = lambda m: cfs.active
    cfs._nozzle_has_filament = lambda: cfs.nozzle
    cfs._fan_speeds = lambda: {'fan': .5}
    cfs._set_fans = lambda speeds: calls.append(('fans', dict(speeds)))
    cfs._slot_nozzle_temp = lambda slot: 240.
    cfs._gcode_z = lambda z: z
    cfs._move_to_cut_start = lambda: calls.append(('cut_start',))
    cfs._ensure_purge_position = lambda g: calls.append(('purge_pos',))
    cfs._best_effort_stop = lambda mask: calls.append(('stop', mask))
    left = [load_errors]

    def unload(g):
        calls.append(('unload', g.params['SLOT']))
        cfs.nozzle, cfs.active = False, 0

    def load(g):
        calls.append(('load', g.params['SLOT']))
        if left[0]:
            left[0] -= 1
            raise RuntimeError(
                "CFS load failed: EXTRUDE STAGE 0 failed: box status 0x09")
        cfs.nozzle, cfs.active = True, cfs.SLOT_MASKS[g.params['SLOT']]

    cfs.cmd_CFS_CUT_UNLOAD = unload
    cfs.cmd_CFS_LOAD = load
    cfs.cmd_CFS_PURGE = lambda g: calls.append(('purge',))
    return cfs


class ChangePauseTest(unittest.TestCase):
    def test_one_failed_load_is_retried_silently(self):
        cfs = make_cfs(load_errors=1)
        cmd = FakeCmd({'SLOT': 'B'})
        cfs.cmd_CFS_TOOLCHANGE(cmd)
        self.assertNotIn('PAUSE_BASE', cfs.gcode.scripts)
        self.assertEqual([c[0] for c in cfs.calls].count('load'), 2)
        rec = cfs.slot_stats['B']
        self.assertEqual((rec['loads'], rec['load_failures'],
                          rec['retried_loads']), (1, 1, 1))
        self.assertEqual(rec['recent'], [False, True])

    def test_slot_that_keeps_failing_warns(self):
        cfs = make_cfs(load_errors=3)
        warnings = []
        cfs.gcode.respond_info = warnings.append
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        self.assertEqual(cfs.slot_stats['B']['changes_paused'], 1)
        self.assertTrue(any('failed 3 of its last 3' in w
                            for w in warnings))

    def test_slicer_flush_sets_a_material_change_purge(self):
        cfs = make_cfs()
        loads = []
        cfs.cmd_CFS_LOAD = lambda g: (loads.append(dict(g.params)),
                                      setattr(cfs, 'nozzle', True),
                                      setattr(cfs, 'active', 2))
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B', 'FLUSH': '250'}))
        self.assertEqual(loads[0].get('LENGTH'), '250.0')
        self.assertNotIn('SHORT', loads[0])
        cfs = make_cfs()
        loads[:] = []
        cfs.cmd_CFS_LOAD = lambda g: (loads.append(dict(g.params)),
                                      setattr(cfs, 'nozzle', True),
                                      setattr(cfs, 'active', 2))
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B', 'FLUSH': '90'}))
        # at least the stock purge for a change of material
        self.assertEqual(loads[0].get('LENGTH'), '180.0')

    def test_success_is_unchanged(self):
        cfs = make_cfs()
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        steps = [c[0] for c in cfs.calls if c[0] in
                 ('unload', 'load', 'purge')]
        self.assertEqual(steps, ['unload', 'load', 'purge'])
        self.assertIsNone(cfs.pending_change)
        self.assertNotIn('PAUSE_BASE', cfs.gcode.scripts)
        self.assertTrue(any('RESTORE_GCODE_STATE NAME=K2_CFS_TOOLCHANGE' in s
                            for s in cfs.gcode.scripts))

    def test_failed_load_pauses_instead_of_ending_the_print(self):
        cfs = make_cfs(load_errors=3)
        cmd = FakeCmd({'SLOT': 'B'})
        cfs.cmd_CFS_TOOLCHANGE(cmd)          # no exception: print not ended
        self.assertIn('PAUSE_BASE', cfs.gcode.scripts)
        self.assertEqual(cfs.status['change_pending'], 'A>B')
        self.assertIn('stop', [c[0] for c in cfs.calls])
        self.assertTrue(cmd.raw and cmd.raw[0].startswith('!! Colour change'))
        self.assertIn('box status 0x09', cmd.raw[0])
        # fans back on, nozzle still hot (no M104 S0)
        self.assertEqual(cfs.calls[-1], ('fans', {'fan': .5}))
        self.assertFalse(any('M104 S0' in s for s in cfs.gcode.scripts))
        # did not return over the print
        self.assertFalse(any('NAME=K2_CFS_TOOLCHANGE MOVE=1' in s
                             for s in cfs.gcode.scripts))

    def test_resume_retries_the_load_and_returns_over_the_print(self):
        cfs = make_cfs(load_errors=3)
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        cfs.printer.print_stats.status['state'] = 'paused'
        del cfs.calls[:]
        cfs.gcode.scripts[:] = []
        cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        steps = [c[0] for c in cfs.calls if c[0] in
                 ('unload', 'load', 'purge', 'purge_pos')]
        # nozzle was clear: no second cut, straight to the load
        self.assertEqual(steps, ['purge_pos', 'load', 'purge'])
        self.assertIsNone(cfs.pending_change)
        self.assertIsNone(cfs.status['change_pending'])
        scripts = "\n".join(cfs.gcode.scripts)
        self.assertIn('M104 S240.0', scripts)
        self.assertIn('NAME=K2_CFS_TOOLCHANGE MOVE=1', scripts)
        # PAUSE_STATE re-saved over the print, after the return
        self.assertEqual(cfs.gcode.scripts[-1],
                         'SAVE_GCODE_STATE NAME=PAUSE_STATE')

    def test_retry_that_fails_again_stays_paused(self):
        cfs = make_cfs(load_errors=6)
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        cfs.printer.print_stats.status['state'] = 'paused'
        with self.assertRaises(RuntimeError) as ctx:
            cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        self.assertIn('failed again', str(ctx.exception))
        self.assertEqual(cfs.status['change_pending'], 'A>B')
        # the next Resume works
        cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        self.assertIsNone(cfs.pending_change)

    def test_loaded_by_hand_only_purges(self):
        cfs = make_cfs(load_errors=3)
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        cfs.printer.print_stats.status['state'] = 'paused'
        cfs.nozzle, cfs.active = True, cfs.SLOT_MASKS['B']
        del cfs.calls[:]
        cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        steps = [c[0] for c in cfs.calls if c[0] in
                 ('unload', 'load', 'purge')]
        self.assertEqual(steps, ['purge'])

    def test_old_slot_still_in_nozzle_does_a_full_change(self):
        cfs = make_cfs(load_errors=3)
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        cfs.printer.print_stats.status['state'] = 'paused'
        cfs.nozzle, cfs.active = True, cfs.SLOT_MASKS['C']
        del cfs.calls[:]
        cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        self.assertEqual([c for c in cfs.calls if c[0] == 'unload'],
                         [('unload', 'C')])

    def test_no_pending_change_is_a_no_op(self):
        cfs = make_cfs()
        cfs.printer.print_stats.status['state'] = 'paused'
        cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        self.assertEqual(cfs.calls, [])
        self.assertEqual(cfs.gcode.scripts, [])

    def test_not_paused_any_more_drops_the_pending_change(self):
        cfs = make_cfs(load_errors=3)
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        cfs.printer.print_stats.status['state'] = 'cancelled'
        cfs.cmd_CFS_CHANGE_RETRY(FakeCmd())
        self.assertIsNone(cfs.pending_change)


class ToolchangeWaitTest(unittest.TestCase):
    # GitHub issue #5: T<n> right after START_PRINT ran the CFS work with the
    # prime line still queued, and the MCUs shut down
    def tracked(self, active):
        cfs = make_cfs()
        cfs.active = active
        order = cfs.order = []
        run = cfs.gcode.run_script_from_command

        def script(text):
            order.append(text)
            run(text)
        cfs.gcode.run_script_from_command = script
        cfs._measuring = lambda: order.append('query') or {}
        cfs._start_feed = lambda *a, **kw: order.append('arm')
        return cfs

    def test_already_loaded_waits_before_the_box_is_touched(self):
        cfs = self.tracked(active=0x02)         # slot B, from START_PRINT
        cmd = FakeCmd({'SLOT': 'B'})
        cfs.cmd_CFS_TOOLCHANGE(cmd)
        self.assertEqual(cfs.order, ['M400', 'query', 'arm'])
        self.assertEqual(cmd.info, ['CFS slot B already loaded'])
        self.assertEqual(cfs.calls, [])

    def test_orcas_second_t_waits_too(self):
        # The start G-code's T line, then Orca's own first tool selection
        # with a Z move queued in between
        cfs = self.tracked(active=0x02)
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        self.assertEqual(cfs.order, ['M400', 'query', 'arm'] * 2)

    def test_real_change_still_runs_in_full(self):
        cfs = self.tracked(active=0x01)         # slot A loaded, change to B
        cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        self.assertEqual(cfs.order[:2], ['M400', 'query'])
        self.assertNotIn('arm', cfs.order)
        self.assertIn('SAVE_GCODE_STATE NAME=K2_CFS_TOOLCHANGE',
                      cfs.order[2])
        steps = [c[0] for c in cfs.calls if c[0] != 'fans']
        self.assertEqual(steps, ['cut_start', 'unload', 'load', 'purge'])
        self.assertIn(('unload', 'A'), cfs.calls)
        self.assertEqual(cfs.active, 0x02)

    def test_refused_change_does_not_wait(self):
        cfs = make_cfs(state='paused')
        with self.assertRaises(RuntimeError):
            cfs.cmd_CFS_TOOLCHANGE(FakeCmd({'SLOT': 'B'}))
        self.assertEqual(cfs.gcode.scripts, [])

    def test_select_tool_waits_before_arming(self):
        # T<n> after a START_PRINT with no TOOL=, through M8800-M8803
        cfs = self.tracked(active=0x02)
        cfs.fitted = True
        cfs._select_tool(FakeCmd(), 'B')
        self.assertEqual(cfs.order, ['M400', 'arm'])

    def test_select_tool_without_a_cfs_does_nothing(self):
        cfs = self.tracked(active=0)
        cfs.fitted = False
        cfs._select_tool(FakeCmd(), 'A')
        self.assertEqual(cfs.order, [])


class StartGcodeWaitTest(unittest.TestCase):
    # The shipped start G-code gives the same M400 for firmware that does
    # not wait yet
    ROOT = pathlib.Path(__file__).parents[1]
    T_LINE = 'T[initial_no_support_extruder]'

    def test_orca_profiles(self):
        for name in ('Creality K2 OpenKlipper.json',
                     'Creality K2 Plus OpenKlipper.json'):
            profile = json.loads((self.ROOT / 'orca' / name).read_text(
                encoding='utf-8'))
            lines = profile['machine_start_gcode'].splitlines()
            self.assertTrue(lines[0].startswith('START_PRINT '), name)
            self.assertEqual(lines[1:3], ['M400', self.T_LINE], name)

    def test_hand_setup_docs(self):
        install = (self.ROOT / 'docs' / 'INSTALL.md').read_text(
            encoding='utf-8')
        self.assertIn(['M400', self.T_LINE],
                      self.pairs(install, '   '))
        notes = (self.ROOT / 'config' / 'ORCA-K2-PROFILE.cfg').read_text(
            encoding='utf-8')
        self.assertIn(['M400', self.T_LINE],
                      self.pairs(notes, '# '))

    @staticmethod
    def pairs(text, prefix):
        # Each line with the one after it, less the block's indent
        lines = [line[len(prefix):] if line.startswith(prefix) else line
                 for line in text.splitlines()]
        return [list(pair) for pair in zip(lines, lines[1:])]


if __name__ == '__main__':
    unittest.main()
