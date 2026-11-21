"""CFS quality changes of 18 Nov 2026.

1. M220/M221 S100 inside every CFS sequence's SAVE/RESTORE_GCODE_STATE, so
   the print's speed/flow sliders never scale the cut ram or the purge.
2. Purge speed from the slot's filament_max_volumetric_speed (max_flow).
3. purge_slow_fraction: the 1 mm/s pauses of the flush (0.2 = as before).
4. Purge clog check from the hub measuring wheel (stock key845).
5. Box alarms 0x51/0x52 pause a print (stock key846/key847).
6. The 4-byte box-state reply.
7. load_stall_timeout: a load whose hub wheel stands still ends early.
8. Creality key numbers in the messages.
"""
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")
slots_mod = importlib.import_module("extras.hark_cfs_slots")
gcode_move_mod = importlib.import_module("extras.gcode_move")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
hi_layout = importlib.import_module("test_cfs_hi_layout")
stage5 = importlib.import_module("test_cfs_load_stage5")


class Reactor:
    def __init__(self):
        self.now = 0.
        self.callbacks = []

    def monotonic(self):
        return self.now

    def pause(self, until):
        self.now = max(self.now, until)

    def register_callback(self, cb):
        self.callbacks.append(cb)


class Gcode:
    def __init__(self):
        self.scripts = []
        self.raw = []
        self.run = []

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def run_script(self, script):
        self.run.append(script)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def respond_info(self, msg):
        pass

    def lines(self):
        return [ln for s in self.scripts for ln in s.splitlines()]


class Cmd(hi_layout.Cmd):
    def get_int(self, name, default=None, minval=None, maxval=None):
        v = self.params.get(name, default)
        return None if v is None else int(v)


class Slots:
    def __init__(self, records):
        self.records = records

    def get_status(self, eventtime):
        return {'slots': self.records}


class Obj:
    def __init__(self, status=None, **attrs):
        self.status = status or {}
        self.__dict__.update(attrs)

    def get_status(self, eventtime=None):
        return self.status


class PauseResume:
    def __init__(self):
        self.paused = 0

    def send_pause_command(self):
        self.paused += 1


class Printer:
    def __init__(self, objects, reactor):
        self.objects = objects
        self.reactor = reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return self.reactor


def bare_cfs(objects=None):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    reactor = Reactor()
    cfs.printer = Printer(dict(objects or {}), reactor)
    cfs.gcode = Gcode()
    cfs.status = {}
    cfs.slot_stats = {}
    return cfs


# --- 1. fixed speed/flow inside the CFS sequences ------------------------

class GcodeMoveStateTest(unittest.TestCase):
    """SAVE/RESTORE_GCODE_STATE carry the M220/M221 factors back."""

    class G:
        def __init__(self, **params):
            self.params = params

        def get(self, name, default=None):
            return self.params.get(name, default)

        def get_float(self, name, default=None, **kw):
            return float(self.params.get(name, default))

        def get_int(self, name, default=None, **kw):
            return int(self.params.get(name, default))

    def test_restore_brings_back_the_users_sliders(self):
        gm = gcode_move_mod.GCodeMove.__new__(gcode_move_mod.GCodeMove)
        gm.absolute_coord = gm.absolute_extrude = True
        gm.base_position = [0., 0., 0., 0.]
        gm.last_position = [10., 10., 1., 50.]
        gm.homing_position = [0., 0., 0., 0.]
        gm.speed = 25.
        gm.speed_factor = 1. / 60.
        gm.extrude_factor = 1.
        gm.saved_states = {}
        G = self.G
        gm.cmd_M220(G(S=150.))
        gm.cmd_M221(G(S=90.))
        user_e = (gm.last_position[3] - gm.base_position[3]) \
            / gm.extrude_factor
        gm.cmd_SAVE_GCODE_STATE(G(NAME='K2_CFS_PURGE'))
        gm.cmd_M220(G(S=100.))
        gm.cmd_M221(G(S=100.))
        self.assertAlmostEqual(gm.speed_factor, 1. / 60.)
        self.assertEqual(gm.extrude_factor, 1.)
        gm.last_position[3] += 140.     # the purge
        gm.cmd_RESTORE_GCODE_STATE(G(NAME='K2_CFS_PURGE', MOVE=0))
        self.assertAlmostEqual(gm.speed_factor, 1.5 / 60.)
        self.assertAlmostEqual(gm.extrude_factor, .9)
        # the print's own E position is where it was
        self.assertAlmostEqual(
            (gm.last_position[3] - gm.base_position[3]) / gm.extrude_factor,
            user_e)


class FixedRateCutTest(unittest.TestCase):
    def test_cut_ram_runs_at_100_percent_and_restores(self):
        cfs, gcode = hi_layout.cut_cfs()
        cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        lines = gcode.lines()
        save = lines.index("SAVE_GCODE_STATE NAME=K2_CFS_CUT_UNLOAD")
        self.assertEqual(lines[save + 1:save + 3],
                         ["M220 S100", "M221 S100"])
        ram = lines.index("G1 X279.600 Y130.000 F15000.000")
        self.assertLess(save, ram)
        self.assertEqual(lines[-1], "RESTORE_GCODE_STATE NAME=K2_CFS_CUT_UNLOAD")
        # nothing outside the saved state touches the sliders
        self.assertEqual(sum(ln.startswith("M220") for ln in lines), 1)

    def test_every_cfs_sequence_save_sets_fixed_rates(self):
        src = pathlib.Path(cfs_mod.__file__).read_text()
        for name in ('K2_CUT_CAL', 'K2_CUT_STALL', 'K2_CUT_TEST',
                     'K2_CFS_UNLOAD', 'K2_CFS_CUT_UNLOAD', 'K2_CFS_LOAD',
                     'K2_CFS_PURGE'):
            self.assertIn('"SAVE_GCODE_STATE NAME=%s\\n" + FIXED_RATE_GCODE'
                          % name, src)
        # the toolchange state wraps the print-side moves: left alone, so a
        # failed change's PAUSE_STATE keeps the user's sliders
        self.assertNotIn('NAME=K2_CFS_TOOLCHANGE\\n" + FIXED', src)


# --- 2. purge speed per material ---------------------------------------

class PurgeFlowSpeedTest(unittest.TestCase):
    def cfs(self, records, fraction=.8, cap=9.58):
        cfs = bare_cfs({'hark_cfs_slots': Slots(records)})
        cfs.purge_extrude_speed = 6.
        cfs.purge_flow_fraction = fraction
        cfs.purge_speed_max = cap
        return cfs

    def test_off_by_default_is_the_fixed_speed(self):
        cfs = self.cfs({'A': {'max_flow': 3.}}, fraction=0.)
        self.assertEqual(cfs._purge_flow_speed(['A']), 6.)
        k2 = bare_cfs()
        k2.purge_extrude_speed = 6.
        self.assertEqual(k2.purge_flow_fraction, 0.)
        self.assertEqual(k2._purge_flow_speed(['A']), 6.)

    def test_tpu_purges_slower_pla_faster(self):
        cfs = self.cfs({'A': {'max_flow': 3.6}, 'B': {'max_flow': 21.}})
        self.assertAlmostEqual(cfs._purge_flow_speed(['A']),
                               .8 * 3.6 / cfs_mod.FILAMENT_AREA)
        self.assertAlmostEqual(cfs._purge_flow_speed(['A']), 1.1975, 3)
        self.assertAlmostEqual(cfs._purge_flow_speed(['B']), 6.985, 2)

    def test_capped_at_purge_speed_max(self):
        cfs = self.cfs({'A': {'max_flow': 40.}})
        self.assertEqual(cfs._purge_flow_speed(['A']), 9.58)

    def test_change_uses_the_slower_of_old_and_new(self):
        cfs = self.cfs({'A': {'max_flow': 21.}, 'B': {'max_flow': 3.6}})
        self.assertAlmostEqual(cfs._purge_flow_speed(['A', 'B']), 1.1975, 3)

    def test_no_max_flow_falls_back(self):
        cfs = self.cfs({'A': {'max_flow': 0}, 'B': {}})
        self.assertEqual(cfs._purge_flow_speed(['A', 'B']), 6.)
        self.assertEqual(self.cfs({})._purge_flow_speed(['C']), 6.)

    def test_extruder_filament_area_is_used(self):
        cfs = self.cfs({'A': {'max_flow': 10.}})
        cfs.printer.objects['extruder'] = Obj(filament_area=2.)
        self.assertAlmostEqual(cfs._purge_flow_speed(['A']), 4.)


class SlotMaxFlowTest(unittest.TestCase):
    def test_setting_maps_to_the_creality_profile_key(self):
        row = [s for s in slots_mod.SETTINGS if s[0] == 'max_flow']
        self.assertEqual(row, [('max_flow', 'MAX_FLOW', float,
                                'filament_max_volumetric_speed')])
        self.assertEqual(slots_mod.EMPTY['max_flow'], 0)

    def test_slot_set_accepts_a_real_flow(self):
        s = slots_mod.HarkCfsSlots.__new__(slots_mod.HarkCfsSlots)
        rec = dict(slots_mod.EMPTY)
        s._apply_setting_params(Cmd({'MAX_FLOW': '23', 'PA': '0.04'}), rec)
        self.assertEqual(rec['max_flow'], 23.)
        self.assertEqual(rec['pressure_advance'], .04)
        with self.assertRaises(RuntimeError):
            s._apply_setting_params(Cmd({'PA': '23'}), dict(rec))

    def test_material_database_and_old_store_pick_it_up(self):
        tmp = tempfile.mkdtemp()
        db = {'result': {'list': [{
            'base': {'id': '01001', 'name': 'Hyper PLA', 'brand': 'Creality',
                     'meterialType': 'PLA'},
            'kvParam': {'filament_max_volumetric_speed': '23',
                        'pressure_advance': '0.04'}}]}}
        with open(os.path.join(tmp, 'material_database.json'), 'w') as f:
            json.dump(db, f)
        old = dict(slots_mod.EMPTY)
        old.update({'loaded': True, 'filament': 'cr-01001',
                    'name': 'Hyper PLA', 'type': 'PLA', 'color': 'FFFFFF',
                    'pressure_advance': .05})
        del old['max_flow']
        store = os.path.join(tmp, 'slots.json')
        with open(store, 'w') as f:
            json.dump({'revision': 1, 'slots': {'A': old}}, f)
        s = slots_mod.HarkCfsSlots.__new__(slots_mod.HarkCfsSlots)
        s.creality_dir, s.store_path = tmp, store
        s.materials, s.library = [], {}
        saved = []
        s._save = lambda *a, **k: saved.append(1)
        s._load_database()
        self.assertEqual(s.materials[0]['max_flow'], 23.)
        s._load_store()
        self.assertEqual(s.slots['A']['max_flow'], 23.)
        # the slot's own tuned PA is kept
        self.assertEqual(s.slots['A']['pressure_advance'], .05)
        self.assertTrue(saved)


# --- 3. purge slow fraction --------------------------------------------

def old_pushes(chunk, rate, pre, change_first):
    # the flush moves before purge_slow_fraction was wired (k2_cfs 11-17)
    lines = []
    if change_first:
        rest = chunk - 36.
        lines.append("G1 E36.000 F%.3f" % rate)
        for unused in range(4):
            lines.append("G1 E%.3f F60" % (rest * .02))
            lines.append("G1 E%.3f F%.3f" % (rest * .23, rate))
    else:
        for pulse in range(5):
            lines.append("G1 E%.3f F%.3f"
                         % (chunk * .18 + (pre if not pulse else 0.), rate))
            lines.append("G1 E%.3f F60" % (chunk * .02))
    return lines


def extruded(lines):
    return sum(float(ln.split()[1][1:]) for ln in lines)


def push_time(lines):
    total = 0.
    for ln in lines:
        e, f = ln.split()[1:3]
        total += float(e[1:]) / (float(f[1:]) / 60.)
    return total


class PurgeSlowFractionTest(unittest.TestCase):
    def cfs(self, fraction=None):
        cfs = bare_cfs()
        if fraction is not None:
            cfs.purge_slow_fraction = fraction
        return cfs

    def test_default_is_the_old_flush_exactly(self):
        cfs = self.cfs()
        for chunk in (70., 90., 80., 46.667, 53.333):
            for pre in (0., 1.5):
                for change in (False, True):
                    if change and chunk <= 36.:
                        continue
                    self.assertEqual(
                        cfs._purge_pushes(chunk, 360., pre, change),
                        old_pushes(chunk, 360., pre, change))

    def test_the_k2_value_is_the_default(self):
        self.assertEqual(cfs_mod.K2CFS.purge_slow_fraction, .2)
        cfs = self.cfs(.2)
        self.assertEqual(cfs._purge_pushes(90., 360., 1.5, False),
                         old_pushes(90., 360., 1.5, False))

    def test_zero_is_one_steady_push(self):
        cfs = self.cfs(0.)
        self.assertEqual(cfs._purge_pushes(70., 360., 1.5, False),
                         ["G1 E71.500 F360.000"])
        self.assertEqual(cfs._purge_pushes(70., 360., 0., True),
                         ["G1 E36.000 F360.000", "G1 E34.000 F360.000"])

    def test_length_is_kept_and_slow_share_follows(self):
        for fraction in (0., .1, .2, .5):
            cfs = self.cfs(fraction)
            for change in (False, True):
                lines = cfs._purge_pushes(70., 360., 0., change)
                self.assertAlmostEqual(extruded(lines), 70., places=2)
            lines = cfs._purge_pushes(70., 360., 0., False)
            slow = sum(float(ln.split()[1][1:]) for ln in lines
                       if ln.endswith("F60"))
            self.assertAlmostEqual(slow, 70. * fraction / 2., places=2)

    def test_slow_pushes_never_outrun_a_slow_purge(self):
        # TPU at 0.8 mm/s: the "slow" pushes go no faster than the rest
        lines = self.cfs()._purge_pushes(70., 48., 0., False)
        self.assertTrue(all(ln.endswith("F48.000") for ln in lines))

    def test_hi_time_saved_per_colour_change(self):
        # Hi: 140 mm as 70 + 70 at 6 mm/s, a change opens the first block
        def change_time(fraction, cool):
            cfs = self.cfs(fraction)
            t = push_time(cfs._purge_pushes(70., 360., 0., True))
            t += push_time(cfs._purge_pushes(70., 360., 1.5, False))
            return t + 2 * cool
        before, after = change_time(.2, 5.), change_time(.1, 2.5)
        saved = before - after
        self.assertGreater(saved, 8.5)
        self.assertLess(saved, 9.5)


# --- 4. purge clog check -----------------------------------------------

def purge_cfs(wheel_steps, clog_check=True, records=None):
    """A CFS at the chute with filament loaded; each MEASURING_WHEEL read
    returns the next of wheel_steps (None = no reply)."""
    cfs = bare_cfs({'toolhead': Obj({'max_accel': 12000.}),
                    'hark_cfs_slots': Slots(records or {})})
    for name in ('_require_cfs', '_require_actuation', '_require_idle',
                 '_require_position', '_require_hot_extruder'):
        setattr(cfs, name, lambda *a: None)
    cfs._require_safe_motion = lambda *a: (-18.5, 130., 5.)
    cfs._nozzle_has_filament = lambda: True
    cfs._measuring = lambda: {}
    cfs._path_mask = lambda m: cfs.SLOT_MASKS['A']
    cfs._fan_speeds = lambda: {}
    cfs._set_fans = lambda speeds: None
    cfs._purge_fan = lambda slots: 51
    wheel = list(wheel_steps)
    cfs.wheel_reads = 0

    def run_query(name):
        if name == 'BOX_MODE':
            return {'box_mode_code': cfs.BOX_MODE_IDLE, 'box_mode': 'idle'}
        if name == 'MEASURING_WHEEL':
            cfs.wheel_reads += 1
            value = wheel.pop(0) if wheel else None
            if value is None:
                return {'raw': ''}
            return {'wheel_mm': value}
        return {}
    cfs._run_query = run_query
    # Hi purge values
    cfs.purge_x, cfs.purge_y = -18.5, 130.
    cfs.purge_safe_y, cfs.purge_entry_x, cfs.purge_return_x = 261., 10., 240.
    cfs.purge_extrude_length = 140.
    cfs.purge_minimum_actual_length = 140.
    cfs.purge_chunk_length = 70.
    cfs.purge_extrude_speed = 6.
    cfs.purge_retract_length, cfs.purge_retract_speed = 1.5, 30.
    cfs.purge_cool_time, cfs.purge_cool_fan = 2.5, 1.
    cfs.clean_accel, cfs.clean_speed = 5000., 200.
    cfs.clean_circle_speed = 200.
    cfs.wipe_style, cfs.clean_passes, cfs.clean_right_x = 'strip', 7, -5.
    cfs.purge_clog_check = clog_check
    return cfs


class PurgeClogCheckTest(unittest.TestCase):
    def purge(self, cfs, **params):
        p = {'CONFIRM': '1'}
        p.update(params)
        cfs.cmd_CFS_PURGE(Cmd(p))

    def test_wheel_turning_passes(self):
        # wheel counts down as filament feeds (negative float, stock)
        cfs = purge_cfs([-500., -545., -545., -600.])
        self.purge(cfs)
        self.assertEqual(cfs.wheel_reads, 4)
        lines = cfs.gcode.lines()
        save = lines.index("SAVE_GCODE_STATE NAME=K2_CFS_PURGE")
        self.assertEqual(lines[save + 1:save + 3],
                         ["M220 S100", "M221 S100"])
        self.assertEqual(lines[-1], "RESTORE_GCODE_STATE NAME=K2_CFS_PURGE")

    def test_still_wheel_stops_the_purge_with_key845(self):
        cfs = purge_cfs([-500., -505.])
        with self.assertRaises(RuntimeError) as ctx:
            self.purge(cfs)
        msg = str(ctx.exception)
        self.assertIn("CFS purge: the measuring wheel barely moved"
                      " (5.0 of 70 mm) - nozzle or feed blocked?"
                      " (Creality key845)", msg)
        # stopped after the first block: no second block, state restored
        self.assertEqual(cfs.wheel_reads, 2)
        self.assertEqual(cfs.gcode.scripts[-1],
                         "RESTORE_GCODE_STATE NAME=K2_CFS_PURGE")

    def test_failed_reads_skip_the_check(self):
        cfs = purge_cfs([None, None, -500., None])
        self.purge(cfs)
        self.assertTrue(cfs.wheel_read_warned)

    def test_a_late_reply_is_read_past(self):
        # 18 Nov 2026 bench: a stage-5 reply landed on the wheel query;
        # asking again gets the wheel, so the check still runs
        cfs = purge_cfs([None, -500., -545., None, -545., -600.])
        self.purge(cfs)
        self.assertEqual(cfs.wheel_reads, 6)
        self.assertFalse(cfs.wheel_read_warned)

    def test_a_box_that_never_answers_gives_up_after_three(self):
        cfs = purge_cfs([None] * 12)
        self.purge(cfs)
        self.assertEqual(cfs.wheel_reads, 2 * cfs.WHEEL_READ_TRIES)
        self.assertTrue(cfs.wheel_read_warned)

    def test_off_reads_nothing(self):
        cfs = purge_cfs([-500., -500., -500., -500.], clog_check=False)
        self.purge(cfs)
        self.assertEqual(cfs.wheel_reads, 0)

    def test_short_blocks_are_not_checked(self):
        cfs = purge_cfs([-500., -500.])
        self.purge(cfs, LENGTH='40', SHORT='1')
        self.assertEqual(cfs.wheel_reads, 0)

    def test_purge_speed_from_the_slot(self):
        cfs = purge_cfs([], clog_check=False,
                        records={'A': {'max_flow': 3.6}})
        cfs.purge_flow_fraction = .8
        self.purge(cfs)
        self.assertAlmostEqual(cfs.status['last_purge_speed'], 1.1975, 3)
        rate = .8 * 3.6 / cfs_mod.FILAMENT_AREA * 60.
        self.assertIn("G1 E12.600 F%.3f" % rate, cfs.gcode.lines())
        self.assertIn("G1 E1.400 F60", cfs.gcode.lines())

    def test_wheel_reply_must_echo_the_wheel_function(self):
        cfs = bare_cfs()
        float_bytes = bytes.fromhex('c499c5bf')
        self.assertAlmostEqual(
            cfs._dec_wheel(b'\x00\x0e' + float_bytes)['wheel_mm'], -1230.18,
            places=2)
        self.assertNotIn('wheel_mm', cfs._dec_wheel(b'\x00\x10'
                                                    + float_bytes))


# --- 5. box alarms -----------------------------------------------------

class BoxAlarmTest(unittest.TestCase):
    def cfs(self, on=True, printing=True):
        pr = PauseResume()
        cfs = bare_cfs({'pause_resume': pr})
        cfs.box_alarm_pause = on
        cfs._printing = lambda: printing
        cfs.status['feed_slot'] = 'B'
        cfs.slot_stats = {'B': {}}
        cfs.counted = []
        cfs._count = lambda slot, key, ok=None: cfs.counted.append(
            (slot, key))
        return cfs, pr

    def fire(self, cfs):
        for cb in cfs.printer.reactor.callbacks:
            cb(0.)
        cfs.printer.reactor.callbacks[:] = []

    def test_two_polls_in_a_row_pause_with_the_key(self):
        cfs, pr = self.cfs()
        cfs._check_box_alarm({'status': 0x51})
        self.assertEqual(pr.paused, 0)
        cfs._check_box_alarm({'status': 0x51})
        self.assertEqual(pr.paused, 1)
        self.fire(cfs)
        self.assertEqual(cfs.gcode.run, ["PAUSE"])
        msg = cfs.gcode.raw[0]
        self.assertIn("CFS slot B", msg)
        self.assertIn("Creality key846", msg)
        self.assertIn("0x51", msg)
        self.assertEqual(cfs.counted, [('B', 'clogs')])

    def test_tangle_is_key847(self):
        cfs, pr = self.cfs()
        cfs._check_box_alarm({'status': 0x52})
        cfs._check_box_alarm({'status': 0x52})
        self.fire(cfs)
        self.assertIn("Creality key847", cfs.gcode.raw[0])
        self.assertIn("tangled", cfs.gcode.raw[0])

    def test_a_single_or_mixed_alarm_does_not_pause(self):
        cfs, pr = self.cfs()
        for code in (0x51, 0x00, 0x51, 0x52, 0x51):
            cfs._check_box_alarm({'status': code})
        self.assertEqual(pr.paused, 0)

    def test_off_or_not_printing_does_nothing(self):
        for on, printing in ((False, True), (True, False)):
            cfs, pr = self.cfs(on, printing)
            for unused in range(3):
                cfs._check_box_alarm({'status': 0x52})
            self.assertEqual(pr.paused, 0)

    def test_no_armed_slot_does_nothing(self):
        # A stale alarm while START_PRINT swaps slots (feed disarmed)
        cfs, pr = self.cfs()
        cfs.status['feed_slot'] = None
        for unused in range(3):
            cfs._check_box_alarm({'status': 0x52})
        self.assertEqual(pr.paused, 0)

    def test_k2_default_is_off(self):
        self.assertFalse(cfs_mod.K2CFS.box_alarm_pause)

    def test_runout_is_not_a_box_alarm(self):
        cfs, pr = self.cfs()
        for unused in range(3):
            cfs._check_box_alarm({'status': 0x50})
        self.assertEqual(pr.paused, 0)


# --- 6. box-state reply lengths ----------------------------------------

class BoxStateReplyTest(unittest.TestCase):
    def test_six_byte_reply(self):
        out = bare_cfs()._dec_measuring(
            bytes.fromhex('00 0a 1a 20 00 02 02 02'))
        self.assertEqual(out['temperature'], 26)
        self.assertEqual(out['humidity'], 32)
        self.assertEqual(out['box_state'], 2)
        self.assertEqual(out['box_state_name'], 'print')
        self.assertEqual(out['active_mask'], 2)
        self.assertEqual(out['active_slot'], 'B')
        self.assertEqual(out['selection_setting'], 2)
        self.assertNotIn('short_reply', out)

    def test_four_byte_reply(self):
        # community capture of a Hi's CFS: f7 01 07 00 0a 17 2b 08 00
        out = bare_cfs()._dec_measuring(bytes.fromhex('00 0a 17 2b 08 00'))
        self.assertEqual(out['temperature'], 23)
        self.assertEqual(out['humidity'], 43)
        self.assertEqual(out['active_mask'], 8)
        self.assertEqual(out['active_slot'], 'D')
        self.assertEqual(out['box_state'], 0)
        self.assertEqual(out['box_state_name'], 'idle')
        self.assertFalse(out['box_runout'])
        self.assertNotIn('selection_setting', out)
        self.assertTrue(out['short_reply'])

    def test_four_byte_reply_with_an_alarm_status(self):
        out = bare_cfs()._dec_measuring(bytes.fromhex('0c 0a 17 2a 02 04'))
        self.assertEqual(out['status'], 0x0c)
        self.assertEqual(out['box_state_name'], 'error')
        self.assertEqual(out['active_slot'], 'B')

    def test_too_short_and_slot_events_unchanged(self):
        out = bare_cfs()._dec_measuring(bytes.fromhex('00 0a 17 2b 08'))
        self.assertEqual(out['temperature'], 23)
        self.assertNotIn('box_state', out)
        out = bare_cfs()._dec_measuring(bytes.fromhex('30 0a 00 00 03 03'))
        self.assertEqual(out['slot_events'],
                         {'C': 'rfid_ready', 'D': 'rfid_ready'})
        self.assertNotIn('box_state', out)

    def test_poll_takes_the_short_reply(self):
        cfs = bare_cfs()
        cfs.STATE_EVENT_DRAIN = 1
        cfs._run_query = lambda name: cfs._dec_measuring(
            bytes.fromhex('00 0a 17 2b 08 02'))
        m = cfs._measuring()
        self.assertEqual(cfs.status['box_state_name'], 'print')
        self.assertEqual(m['active_mask'], 8)


# --- 7. load stall timeout ---------------------------------------------

class LoadStallTest(unittest.TestCase):
    def make(self, arrive_at, wheel, timeout=10.):
        cfs, reactor = stage5.make_cfs(arrive_at)
        base = cfs._run_query
        cfs.load_stall_timeout = timeout
        cfs.wheel_read_warned = False

        def run_query(name):
            if name == 'MEASURING_WHEEL':
                value = wheel(reactor.now)
                return {'raw': ''} if value is None else {'wheel_mm': value}
            return base(name)
        cfs._run_query = run_query
        return cfs, reactor

    def load(self, cfs):
        cfs.cmd_CFS_LOAD(stage5.FakeGcmd({'SLOT': 'A', 'CONFIRM': '1'}))

    def test_still_wheel_ends_the_attempt_early(self):
        cfs, reactor = self.make(1e9, lambda now: -300.)
        with self.assertRaises(RuntimeError) as ctx:
            self.load(cfs)
        self.assertIn("has not turned", str(ctx.exception))
        self.assertIn("slot A", str(ctx.exception))
        self.assertLess(reactor.now, 20.)     # not load_timeout's 90 s

    def test_turning_wheel_loads_normally(self):
        cfs, reactor = self.make(30., lambda now: -300. - 10. * now)
        self.load(cfs)
        self.assertTrue(any(s[1] == 'EXTRUDE STAGE 7' for s in cfs.sent))

    def test_wheel_that_stops_after_feeding(self):
        # fed for 20 s, then the tube snagged
        cfs, reactor = self.make(
            1e9, lambda now: -300. - 10. * min(now, 20.))
        with self.assertRaises(RuntimeError) as ctx:
            self.load(cfs)
        self.assertIn("has not turned", str(ctx.exception))
        self.assertGreaterEqual(reactor.now, 30.)
        self.assertLess(reactor.now, 40.)

    def test_no_wheel_reading_waits_out_load_timeout(self):
        cfs, reactor = self.make(1e9, lambda now: None)
        with self.assertRaises(RuntimeError) as ctx:
            self.load(cfs)
        self.assertIn("no filament at nozzle sensor", str(ctx.exception))
        self.assertTrue(cfs.wheel_read_warned)

    def test_off_never_reads_the_wheel(self):
        reads = []
        cfs, reactor = self.make(
            1e9, lambda now: reads.append(now) or -300., timeout=0.)
        with self.assertRaises(RuntimeError) as ctx:
            self.load(cfs)
        self.assertIn("no filament at nozzle sensor", str(ctx.exception))
        self.assertEqual(reads, [])


# --- 8. Creality key numbers -------------------------------------------

class CrealityKeyTest(unittest.TestCase):
    def test_status_texts_name_the_stock_key(self):
        for code, key in ((0x13, 851), (0x14, 849), (0x51, 846),
                          (0x52, 847)):
            self.assertIn("(Creality key%d)" % key,
                          cfs_mod.status_label(code))

    def test_messages_name_the_stock_key(self):
        src = pathlib.Path(cfs_mod.__file__).read_text()
        for text in ("detects filament after %.1fs (Creality key863)",
                     "Unload first (or FORCE=1) (Creality key854)",
                     "(Creality key855)", "(Creality key841)",
                     "(Creality key845)"):
            self.assertIn(text, src)


class ConfigTest(unittest.TestCase):
    def test_hi_turns_the_new_options_on(self):
        cfg = (pathlib.Path(__file__).parents[1] / "config"
               / "mainline_stage2_hi.cfg").read_text()
        section = cfg[cfg.index("[k2_cfs]"):cfg.index("[hark_cfs_slots]")]
        for line in ("purge_flow_fraction: 0.8", "purge_speed_max: 9.58",
                     "purge_slow_fraction: 0.1", "purge_cool_time: 2.5",
                     "purge_clog_check: True", "box_alarm_pause: True",
                     "load_stall_timeout: 10"):
            self.assertIn("\n" + line + "\n", section)

    def test_k2_configs_leave_them_off(self):
        root = pathlib.Path(__file__).parents[1] / "config"
        for name in ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg"):
            cfg = (root / name).read_text()
            for option in ("purge_flow_fraction", "purge_clog_check",
                           "box_alarm_pause", "load_stall_timeout"):
                self.assertNotIn("\n" + option + ":", cfg)
            # set there since 10-2x and unused until now: 0.2 is the
            # flush they always had
            self.assertIn("\npurge_slow_fraction: 0.2\n", cfg)


if __name__ == '__main__':
    unittest.main()
