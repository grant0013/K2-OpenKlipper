"""Filament map (CFS_TOOL_MAP): which slot each of a print file's tools
prints from, the external spool with a CFS fitted, the reset after a print,
and the T0-T3 / START_PRINT / runout macros rendered from the real config
with a fake printer (those need jinja2, as on the K2; skipped without it)."""
import configparser
import importlib
import json
import logging
import os
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
slots_mod = importlib.import_module("extras.hark_cfs_slots")
cfs_mod = importlib.import_module("extras.k2_cfs")
gcode_mod = importlib.import_module("gcode")

try:
    import jinja2
except ImportError:      # the Mac without klippy's env: macro tests skip
    jinja2 = None


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.now = 100.
        self.timers, self.callbacks = [], []

    def monotonic(self):
        return self.now

    def register_timer(self, fn, when):
        self.timers.append([fn, when])
        return fn

    def register_callback(self, fn, when=None):
        self.callbacks.append(fn)

    def run_timers(self, seconds=1.):
        self.now += seconds
        for t in list(self.timers):
            if t[1] <= self.now:
                t[1] = t[0](self.now)
                if t[1] >= self.NEVER:
                    self.timers.remove(t)

    def run_callbacks(self):
        while self.callbacks:
            self.callbacks.pop(0)(self.now)


class FakeGcode:
    error = gcode_mod.CommandError

    def __init__(self):
        self.commands, self.out, self.scripts = {}, [], []

    def register_command(self, name, fn, desc=None):
        self.commands[name] = fn

    def respond_info(self, msg, log=True):
        self.out.append(msg)

    def respond_raw(self, msg):
        self.out.append(msg)

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def run(self, line):
        parts = line.split()
        params = {}
        for p in parts[1:]:
            k, _, v = p.partition('=')
            params[k.upper()] = v.strip('"')
        gcmd = gcode_mod.GCodeCommand(self, parts[0], line, params, False)
        return self.commands[parts[0]](gcmd)


class FakeObj:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime):
        return self.status


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.handlers = {}
        self.objects = {'gcode': self.gcode,
                        'print_stats': FakeObj({'state': 'standby',
                                                'filename': ''})}

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)


class FakeConfig:
    def __init__(self, printer, values):
        self.printer, self.values = printer, values

    def get_printer(self):
        return self.printer

    def get(self, key, default=None):
        return self.values.get(key, default)


def record(type_, colour, loaded=True):
    r = dict(slots_mod.EMPTY)
    r.update(loaded=loaded, name='Test ' + type_, type=type_, color=colour)
    return r


class ToolMapBase(unittest.TestCase):
    fitted = True

    def setUp(self):
        # no Creality files here: the slot seed's logged tracebacks are
        # expected
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, 'slots.json')
        self.orig_publish = slots_mod.HarkCfsSlots._publish_lanes
        slots_mod.HarkCfsSlots._publish_lanes = lambda self, *a: None
        self.make()

    def tearDown(self):
        slots_mod.HarkCfsSlots._publish_lanes = self.orig_publish
        self.tmp.cleanup()

    def make(self):
        self.printer = FakePrinter()
        self.printer.objects['k2_cfs'] = FakeObj({})
        self.printer.objects['k2_cfs'].fitted = self.fitted
        self.s = slots_mod.HarkCfsSlots(FakeConfig(self.printer, {
            'store_path': self.store,
            'creality_dir': os.path.join(self.tmp.name, 'none')}))
        self.s.slots.update({'A': record('PLA', '000000'),
                             'B': record('PETG', 'FFFFFF'),
                             'E': record('PLA', 'E53935')})
        return self.s

    def gc(self, line):
        return self.printer.gcode.run(line)

    def status(self):
        return self.s.get_status(self.printer.reactor.now)

    def printing(self, state='printing', filename='a.gcode'):
        self.printer.objects['print_stats'].status = {'state': state,
                                                      'filename': filename}


class CfsToolMapTest(ToolMapBase):
    def test_default_is_a_to_d(self):
        st = self.status()
        self.assertEqual(st['tool_map'], {'T0': 'A', 'T1': 'B', 'T2': 'C',
                                          'T3': 'D'})
        self.assertEqual(st['tool_map_set'], {})
        self.assertFalse(st['tool_map_external'])
        self.gc('CFS_TOOL_MAP')
        self.assertIn('T0 A, T1 B, T2 C, T3 D (default)',
                      self.printer.gcode.out[-1])

    def test_set_and_merge_and_clear_one(self):
        self.gc('CFS_TOOL_MAP T0=C T1=a')
        self.assertEqual(self.status()['tool_map'],
                         {'T0': 'C', 'T1': 'A', 'T2': 'C', 'T3': 'D'})
        self.gc('CFS_TOOL_MAP T2=B')
        self.assertEqual(self.status()['tool_map_set'],
                         {'T0': 'C', 'T1': 'A', 'T2': 'B'})
        self.gc('CFS_TOOL_MAP T1=AUTO')
        self.assertEqual(self.status()['tool_map']['T1'], 'B')
        # slot numbers 1-4 work too; RESET=1 with tools replaces the map
        self.gc('CFS_TOOL_MAP RESET=1 T3=2')
        self.assertEqual(self.status()['tool_map_set'], {'T3': 'B'})
        self.gc('CFS_TOOL_MAP RESET=1')
        self.assertEqual(self.status()['tool_map_set'], {})

    def test_external_spool_prints_every_tool(self):
        self.gc('CFS_TOOL_MAP T1=E')
        st = self.status()
        self.assertEqual(st['tool_map'], {t: 'E' for t in slots_mod.TOOLS})
        self.assertTrue(st['tool_map_external'])
        self.assertIn('LOAD_FILAMENT SLOT=E', self.printer.gcode.out[-1])
        for alias in ('EXT', 'spool', 'external'):
            self.gc('CFS_TOOL_MAP RESET=1 T0=%s' % alias)
            self.assertEqual(self.status()['tool_map_set'], {'T0': 'E'})

    def test_external_and_cfs_cannot_mix(self):
        self.gc('CFS_TOOL_MAP T0=E')
        with self.assertRaises(gcode_mod.CommandError) as cm:
            self.gc('CFS_TOOL_MAP T1=B')
        self.assertIn('not both', str(cm.exception))
        self.assertEqual(self.status()['tool_map_set'], {'T0': 'E'})

    def test_bad_slot(self):
        for bad in ('F', '7A', '0', '17', 'AB'):
            with self.assertRaises(gcode_mod.CommandError):
                self.gc('CFS_TOOL_MAP T0=%s' % bad)

    def test_slot_ids_leave_room_for_more_boxes(self):
        # box 1: A-D, 1A-1D, or Creality's slot numbers 1-4
        self.gc('CFS_TOOL_MAP T0=1C T1=4 T2=b')
        self.assertEqual(self.status()['tool_map_set'],
                         {'T0': 'C', 'T1': 'D', 'T2': 'B'})
        # boxes 2-4 (2A..4D, slots 5-16) are recognised but not driven yet
        for later in ('2A', '4D', '5', '16'):
            with self.assertRaises(gcode_mod.CommandError) as cm:
                self.gc('CFS_TOOL_MAP T0=%s' % later)
            self.assertIn('only CFS box 1', str(cm.exception))
        self.assertEqual(slots_mod.map_targets(2)[4:],
                         ['2A', '2B', '2C', '2D', 'E'])
        st = self.status()
        self.assertEqual(st['map_targets'], ['A', 'B', 'C', 'D', 'E'])
        self.assertEqual(st['map_tools'], ['T0', 'T1', 'T2', 'T3'])

    def test_tools_beyond_the_printer(self):
        with self.assertRaises(gcode_mod.CommandError) as cm:
            self.gc('CFS_TOOL_MAP T4=A')
        self.assertIn('T0-T3', str(cm.exception))

    def test_not_while_printing_but_reset_is_allowed(self):
        self.gc('CFS_TOOL_MAP T0=B')
        self.printing()
        with self.assertRaises(gcode_mod.CommandError):
            self.gc('CFS_TOOL_MAP T0=C')
        self.gc('CFS_TOOL_MAP RESET=1 QUIET=1')        # END_PRINT
        self.assertEqual(self.status()['tool_map_set'], {})

    def test_empty_slot_is_noted(self):
        self.gc('CFS_TOOL_MAP T0=D')
        self.assertIn('no filament recorded in slot D',
                      self.printer.gcode.out[-1])

    def test_file_binding(self):
        self.gc('CFS_TOOL_MAP T0=B FILE="/sub/a.gcode"')
        self.assertEqual(self.status()['tool_map_file'], 'sub/a.gcode')
        # shown while idle, used by its own file, ignored by another
        self.assertEqual(self.status()['tool_map']['T0'], 'B')
        self.printing(filename='sub/a.gcode')
        self.assertEqual(self.status()['tool_map']['T0'], 'B')
        self.printing(filename='other.gcode')
        self.assertEqual(self.status()['tool_map']['T0'], 'A')

    def test_saved_with_the_slots(self):
        self.gc('CFS_TOOL_MAP T0=E FILE=x.gcode')
        rev = self.s.revision
        with open(self.store) as f:
            data = json.load(f)
        self.assertEqual(data['tool_map'], {'map': {'T0': 'E'},
                                            'file': 'x.gcode'})
        # only the map changed: the slot revision stays
        self.gc('CFS_TOOL_MAP T0=E T1=E')
        self.assertEqual(self.s.revision, rev)

    def test_cleared_when_its_print_ends(self):
        self.gc('CFS_TOOL_MAP T0=B')
        r = self.printer.reactor
        r.run_timers()
        self.assertEqual(self.status()['tool_map_set'], {'T0': 'B'})
        self.printing()
        r.run_timers()
        self.printing('paused')
        r.run_timers()
        self.assertEqual(self.status()['tool_map_set'], {'T0': 'B'})
        self.printing('error')          # no END_PRINT after an error
        r.run_timers()
        self.assertEqual(self.status()['tool_map_set'], {})
        self.assertFalse(r.timers)

    def test_kept_while_idle_before_its_print(self):
        self.printing('complete', 'last.gcode')   # the previous print
        self.gc('CFS_TOOL_MAP T0=C')
        for _ in range(5):
            self.printer.reactor.run_timers()
        self.assertEqual(self.status()['tool_map_set'], {'T0': 'C'})

    def test_restart_clears_unless_a_power_loss_resume_waits(self):
        self.gc('CFS_TOOL_MAP T0=C')
        s = self.make()
        self.assertEqual(s.tool_map, {'T0': 'C'})
        for fn in self.printer.handlers['klippy:ready']:
            fn()
        self.printer.reactor.run_callbacks()
        self.assertEqual(s.tool_map, {})
        self.gc('CFS_TOOL_MAP T0=C')
        s = self.make()
        self.printer.objects['hark_plr'] = FakeObj({'available': True})
        for fn in self.printer.handlers['klippy:ready']:
            fn()
        self.printer.reactor.run_callbacks()
        self.assertEqual(s.tool_map, {'T0': 'C'})
        self.assertTrue(self.printer.reactor.timers)


class NoCfsToolMapTest(ToolMapBase):
    fitted = False

    def test_t0_is_the_rear_spool_others_only_when_mapped(self):
        self.assertEqual(self.status()['tool_map'],
                         {'T0': 'E', 'T1': '', 'T2': '', 'T3': ''})
        self.assertTrue(self.status()['tool_map_external'])
        self.gc('CFS_TOOL_MAP T1=E T3=E')
        self.assertEqual(self.status()['tool_map'],
                         {'T0': 'E', 'T1': 'E', 'T2': '', 'T3': 'E'})
        with self.assertRaises(gcode_mod.CommandError) as cm:
            self.gc('CFS_TOOL_MAP T2=A')
        self.assertIn('no CFS', str(cm.exception))
        self.assertEqual(self.status()['map_targets'], ['E'])


class FlushMatrixToolsTest(unittest.TestCase):
    def test_matrix_follows_the_tools_not_the_slots(self):
        cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
        # 4 tools: tool i -> tool j costs 100*i + 10*j + 100 mm3
        matrix = [[100 * i + 10 * j + 100 for j in range(4)]
                  for i in range(4)]
        cfs._flush_matrix = lambda: {'matrix': matrix, 'multiplier': 1.}
        objects = {'extruder': type('E', (), {'filament_area': 1.})(),
                   'hark_cfs_slots': FakeObj({'slots': {
                       'A': {'type': 'PLA'}, 'C': {'type': 'PLA'}}})}
        cfs.printer = type('P', (), {
            'lookup_object': lambda self, n, d=None: objects.get(n, d)})()
        gcmd = type('G', (), {'respond_info': lambda self, m: None})()
        # slots A -> C without tools: indexes 0 -> 2 = 120
        self.assertEqual(cfs._file_purge(gcmd, 'A', 'C')[0], 120.)
        # T3 (slot A) -> T1 (slot C): matrix[3][1] = 410, clamped to 360
        self.assertEqual(cfs._file_purge(gcmd, 'A', 'C', 3, 1)[0], 360.)
        self.assertEqual(cfs._file_purge(gcmd, 'A', 'C', 0, 1)[0], 110.)

    def test_feed_start_for_the_external_spool_is_a_no_op(self):
        cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
        cfs.fitted = True
        out = []
        gcmd = type('G', (), {'get': lambda self, k: 'E',
                              'respond_info': lambda self, m: out.append(m)})()
        cfs.cmd_CFS_FEED_START(gcmd)
        self.assertIn('External spool', out[0])


# --- macros, rendered from the real config -------------------------------

class MacroError(Exception):
    pass


def load_macros(name='mainline_stage2.cfg'):
    cp = configparser.RawConfigParser(strict=False,
                                      inline_comment_prefixes=(';', '#'))
    cp.read(str(ROOT / 'config' / name))
    out = {}
    for sec in cp.sections():
        if sec.startswith('gcode_macro '):
            out[sec[len('gcode_macro '):]] = dict(cp.items(sec))
    return out


class Printer(dict):
    """printer[...] for templates: missing objects and fields render as
    jinja's chainable undefined"""


def variables(macro):
    out = {}
    for k, v in macro.items():
        if k.startswith('variable_'):
            import ast
            out[k[len('variable_'):]] = ast.literal_eval(v)
    return out


@unittest.skipIf(jinja2 is None, "jinja2 not installed")
class MacroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.macros = load_macros()
        cls.env = jinja2.Environment('{%', '%}', '{', '}',
                                     undefined=jinja2.ChainableUndefined)

    def printer(self, fitted=True, tmap=None, external=False, active=None,
                detected=True, state='printing', records=None):
        p = Printer()
        for name, m in self.macros.items():
            p['gcode_macro ' + name] = variables(m)
        p['k2_cfs'] = {'fitted': fitted, 'active_slot': active,
                       'feed_slot': active, 'online': True,
                       'detected': True}
        default = {'T0': 'A', 'T1': 'B', 'T2': 'C', 'T3': 'D'}
        p['hark_cfs_slots'] = {
            'tool_map': tmap or default,
            'tool_map_set': {t: s for t, s in (tmap or {}).items()
                             if s and s != default[t]},
            'tool_map_external': external,
            'slots': records or {
                'A': {'loaded': True, 'type': 'PLA', 'color': '000000'},
                'B': {'loaded': True, 'type': 'PLA', 'color': 'FFFFFF'},
                'C': {'loaded': True, 'type': 'PLA', 'color': 'E53935'},
                'E': {'loaded': True, 'type': 'PLA', 'color': 'E53935'}}}
        for k, v in p['hark_cfs_slots']['slots'].items():
            p['hark_cfs_slots']['slots'][k] = dict(slots_mod.EMPTY, **v)
        p['configfile'] = {'settings': {'k2_cfs': {
            k: 1. for k in ('cutter_retract_length', 'cutter_retract_speed',
                            'cutter_y', 'cutter_x', 'cutter_speed',
                            'cutter_ram_accel', 'cutter_cut_x',
                            'cutter_ram_speed', 'cutter_release_x',
                            'cutter_release_speed', 'cutter_tip_length',
                            'cutter_tip_push_speed', 'cutter_tip_pull_speed',
                            'unload_pull_length', 'unload_pull_speed')}}}
        p['print_stats'] = {'state': state}
        p['filament_switch_sensor filament_sensor'] = {
            'filament_detected': detected}
        p['toolhead'] = {'homed_axes': 'xyz', 'position': {'z': 10.},
                         'max_accel': 20000}
        p['heater_bed'] = {'temperature': 60.}
        p['extruder'] = {'temperature': 220., 'target': 220.,
                         'can_extrude': True}
        p['k2_motor_bus'] = {'motors_ready': True}
        return p

    def render(self, name, printer, **params):
        def fail(msg):
            raise MacroError(msg)
        tpl = self.env.from_string(self.macros[name]['gcode'])
        text = tpl.render(printer=printer, action_raise_error=fail,
                          action_respond_info=lambda m: '',
                          params={k.upper(): str(v)
                                  for k, v in params.items()})
        return [l.strip() for l in text.splitlines() if l.strip()]

    def test_t_macros_call_the_shared_body(self):
        for t in range(4):
            self.assertEqual(self.render('T%d' % t, self.printer()),
                             ['_HARK_TOOL T=%d' % t])

    def test_default_change_mid_print(self):
        p = self.printer(active='A')
        p['gcode_macro _HARK_TOOL']['current'] = 0
        out = self.render('_HARK_TOOL', p, T=1)
        self.assertIn('_CFS_TC SLOT=B TOOL=1 OLD_TOOL=0', out)
        self.assertIn('CFS_SLOT_APPLY SLOT=B', out)
        self.assertIn('SET_GCODE_VARIABLE MACRO=_HARK_TOOL VARIABLE=current '
                      'VALUE=1', out)

    def test_mapped_change_mid_print(self):
        p = self.printer(tmap={'T0': 'C', 'T1': 'A', 'T2': 'C', 'T3': 'D'},
                         active='C')
        p['gcode_macro _HARK_TOOL']['current'] = 0
        out = self.render('_HARK_TOOL', p, T=1)
        self.assertIn('_CFS_TC SLOT=A TOOL=1 OLD_TOOL=0', out)
        self.assertIn('CFS_SLOT_APPLY SLOT=A', out)

    def test_cfs_tc_passes_the_tools_to_the_change(self):
        p = self.printer(active='C')
        out = self.render('_CFS_TC', p, SLOT='A', TOOL=1, OLD_TOOL=0)
        self.assertTrue([l for l in out if l.startswith(
            'CFS_TOOLCHANGE SLOT=A') and l.endswith('TOOL=1 OLD_TOOL=0')],
            out)
        out = self.render('_CFS_TC', p, SLOT='A')
        self.assertTrue([l for l in out if l.startswith('CFS_TOOLCHANGE')
                         and 'TOOL=' not in l], out)

    def test_deferred_load_uses_the_mapped_slot(self):
        p = self.printer(tmap={'T0': 'D', 'T1': 'B', 'T2': 'C', 'T3': 'D'})
        p['gcode_macro START_PRINT']['cfs_pending'] = 1
        out = self.render('_HARK_TOOL', p, T=0)
        self.assertIn('CFS_PRINT_START SLOT=D CONFIRM=1', out)
        self.assertIn('M8803', out)

    def test_idle_tool_arms_the_mapped_slot(self):
        p = self.printer(tmap={'T0': 'B', 'T1': 'B', 'T2': 'C', 'T3': 'D'},
                         state='standby')
        out = self.render('_HARK_TOOL', p, T=0)
        self.assertIn('M8801', out)

    def test_external_spool_tool_changes_nothing(self):
        p = self.printer(tmap={t: 'E' for t in ('T0', 'T1', 'T2', 'T3')},
                         external=True)
        p['gcode_macro _HARK_TOOL']['current'] = 0
        out = self.render('_HARK_TOOL', p, T=2)
        self.assertFalse([l for l in out if 'CFS_TC' in l
                          or 'CFS_PRINT_START' in l or l.startswith('M88')])
        self.assertIn('CFS_SLOT_APPLY SLOT=E', out)

    def test_no_cfs(self):
        p = self.printer(fitted=False,
                         tmap={'T0': 'E', 'T1': '', 'T2': 'E', 'T3': ''},
                         external=True)
        out = self.render('_HARK_TOOL', p, T=0)
        self.assertEqual(out[0], '_NO_CFS_TOOL T=0')
        self.assertIn('CFS_SLOT_APPLY SLOT=E', out)
        self.assertEqual(self.render('_NO_CFS_TOOL', p, T=2), [])
        with self.assertRaises(MacroError) as cm:
            self.render('_NO_CFS_TOOL', p, T=1)
        self.assertIn('CFS_TOOL_MAP T1=E', str(cm.exception))

    def start(self, p, **params):
        params.setdefault('BED_TEMP', 60)
        params.setdefault('EXTRUDER_TEMP', 220)
        return self.render('START_PRINT', p, **params)

    def test_start_print_default_loads_slot_a(self):
        out = self.start(self.printer(), TOOL=0)
        self.assertIn('CFS_PRINT_START SLOT=A CONFIRM=1 UNHOMED_Z=1', out)
        self.assertFalse([l for l in out if 'Filament map' in l])

    def test_start_print_loads_the_mapped_slot(self):
        p = self.printer(tmap={'T0': 'A', 'T1': 'C', 'T2': 'C', 'T3': 'D'})
        out = self.start(p, TOOL=1)
        self.assertIn('CFS_PRINT_START SLOT=C CONFIRM=1 UNHOMED_Z=1', out)
        self.assertIn('Loading slot C and purging', ' '.join(out))
        self.assertTrue([l for l in out if l.startswith(
            'RESPOND MSG="Filament map: T0 A, T1 C, T2 C, T3 D"')], out)
        self.assertIn('SET_GCODE_VARIABLE MACRO=_HARK_TOOL VARIABLE=current '
                      'VALUE=1', out)

    def test_start_print_external_spool_with_a_cfs(self):
        p = self.printer(tmap={t: 'E' for t in ('T0', 'T1', 'T2', 'T3')},
                         external=True)
        out = self.start(p, TOOL=2)
        self.assertFalse([l for l in out if 'CFS_PRINT_START' in l])
        self.assertIn('CFS_FEED_STOP', out)
        self.assertTrue([l for l in out if l.startswith(
            '_SPOOL_PRINT_START')])
        self.assertFalse([l for l in out if l.startswith('_PA_AUTO')
                          or l.startswith('_FLOW_AUTO')])
        # the deferred-load path is not used: the spool is primed here
        out = self.start(p)
        self.assertTrue([l for l in out if l.startswith(
            '_SPOOL_PRINT_START')])
        self.assertFalse([l for l in out if 'cfs_pending VALUE=1' in l])

    def test_start_print_external_spool_needs_it_in_the_toolhead(self):
        tmap = {t: 'E' for t in ('T0', 'T1', 'T2', 'T3')}
        with self.assertRaises(MacroError) as cm:
            self.start(self.printer(tmap=tmap, external=True, active='B'),
                       TOOL=0)
        self.assertIn('slot B is loaded', str(cm.exception))
        with self.assertRaises(MacroError) as cm:
            self.start(self.printer(tmap=tmap, external=True,
                                    detected=False), TOOL=0)
        self.assertIn('no filament at the toolhead', str(cm.exception))

    def test_start_print_material_from_the_mapped_slot(self):
        recs = {'A': {'loaded': True, 'type': 'PLA'},
                'C': {'loaded': True, 'type': 'PETG'}}
        p = self.printer(tmap={'T0': 'C', 'T1': 'B', 'T2': 'C', 'T3': 'D'},
                         records=recs)
        p['gcode_macro _CHAMBER_FAN']['material_targets'] = {'PETG': 40}
        out = ' '.join(self.start(p, TOOL=0))
        self.assertIn('Chamber target 40C for PETG', out)

    def test_start_print_no_cfs(self):
        p = self.printer(fitted=False, external=True,
                         tmap={'T0': 'E', 'T1': '', 'T2': '', 'T3': ''})
        p['hark_cfs_slots']['tool_map_set'] = {}
        out = self.start(p, TOOL=0)
        self.assertFalse([l for l in out if 'Filament map' in l])
        self.assertTrue([l for l in out if l.startswith(
            '_SPOOL_PRINT_START')])
        self.assertNotIn('CFS_FEED_STOP', out)
        with self.assertRaises(MacroError) as cm:
            self.start(p, TOOL=1)
        self.assertIn('no CFS', str(cm.exception))
        p['hark_cfs_slots']['tool_map']['T1'] = 'E'
        self.assertTrue([l for l in self.start(p, TOOL=1)
                         if l.startswith('_SPOOL_PRINT_START')])

    def test_runout_on_the_external_spool_does_not_refill(self):
        p = self.printer(tmap={t: 'E' for t in ('T0', 'T1', 'T2', 'T3')},
                         external=True, state='paused')
        p['k2_cfs']['last_loaded_slot'] = 'C'
        out = ' '.join(self.render('_RUNOUT_REFILL', p))
        self.assertNotIn('CFS_RUNOUT_REFILL', out)
        self.assertIn('external spool ran out', out)

    def test_load_rear_spool_with_a_cfs(self):
        # a slot in the toolhead comes out first
        out = self.render('LOAD_FILAMENT', self.printer(active='B',
                                                        state='standby'),
                          SLOT='E')
        self.assertEqual(out[0], 'CFS_UNLOAD_ACTIVE')
        self.assertIn('push the rear spool in', ' '.join(out).lower())
        # then the rear-spool load
        out = self.render('LOAD_FILAMENT', self.printer(state='standby'),
                          SLOT='E')
        self.assertIn('_STAGE TEXT="Loading the external spool"', out)
        out = self.render('LOAD_FILAMENT', self.printer(state='standby'),
                          SLOT='B')
        self.assertEqual(out, ['CFS_LOAD_SLOT SLOT=B'])

    def test_unload_rear_spool_with_a_cfs(self):
        out = self.render('UNLOAD_FILAMENT', self.printer(state='standby'),
                          SLOT='E')
        self.assertIn('_STAGE TEXT="Unloading the external spool"', out)
        # never guessed: filament at the toolhead with no slot loaded
        with self.assertRaises(MacroError) as cm:
            self.render('UNLOAD_FILAMENT', self.printer(state='standby'))
        self.assertIn('UNLOAD_FILAMENT SLOT=E', str(cm.exception))
        out = self.render('UNLOAD_FILAMENT', self.printer(
            state='standby', detected=False))
        self.assertEqual(out, ['CFS_UNLOAD_ACTIVE'])
        out = self.render('UNLOAD_FILAMENT', self.printer(
            state='standby', active='C'))
        self.assertEqual(out, ['CFS_UNLOAD_ACTIVE'])
        with self.assertRaises(MacroError):
            self.render('UNLOAD_FILAMENT', self.printer(
                state='standby', active='C'), SLOT='E')

    def test_end_and_cancel_reset_the_map(self):
        self.assertIn('CFS_TOOL_MAP RESET=1 QUIET=1',
                      self.macros['END_PRINT']['gcode'])
        self.assertIn('CFS_TOOL_MAP RESET=1 QUIET=1',
                      self.macros['CANCEL_PRINT']['gcode'])

    def test_k2_plus_config_has_the_same_macros(self):
        plus = load_macros('mainline_stage2_k2plus.cfg')
        for name in ('_HARK_TOOL', 'T0', 'T3', '_NO_CFS_TOOL', '_CFS_TC',
                     'LOAD_FILAMENT', 'UNLOAD_FILAMENT', '_RUNOUT_REFILL'):
            self.assertEqual(plus[name], self.macros[name], name)


if __name__ == '__main__':
    unittest.main()
