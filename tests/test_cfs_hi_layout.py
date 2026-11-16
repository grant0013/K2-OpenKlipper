"""k2_cfs on the Creality Hi layout.

The Hi's cutter is on the right (stock box.cfg pre_cut_pos_x 240, calibrated
cut_pos_x 279.6), its chute is on the left at X-18.5 with a PTFE strip
between the chute and the bed, and stock unloads with one 15 mm pull
(stock klippy.log 2026-11-16 20:07: one RETRUDE start, extrude -15 at
360 mm/min, RETRUDE finish). None of that may change what the K2 does.
"""
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


class Reactor:
    def monotonic(self):
        return 0.

    def pause(self, t):
        pass


class Cmd:
    def __init__(self, params=None):
        self.params = params or {}
        self.info = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None, minval=None, maxval=None,
                  above=None, below=None):
        v = self.params.get(name, default)
        v = None if v is None else float(v)
        if v is not None:
            if ((minval is not None and v < minval)
                    or (maxval is not None and v > maxval)
                    or (above is not None and v <= above)
                    or (below is not None and v >= below)):
                raise RuntimeError("%s out of range: %s" % (name, v))
        return v

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


class FakeGcode:
    """Records scripts; `on_line` sees every line (sensor / stall models)."""
    def __init__(self, on_line=None):
        self.scripts = []
        self.on_line = on_line

    def run_script_from_command(self, script):
        self.scripts.append(script)
        if self.on_line is not None:
            for line in script.splitlines():
                self.on_line(line)

    def lines(self):
        return [ln for s in self.scripts for ln in s.splitlines()]


def base_cfs(gcode):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    cfs.gcode = gcode
    objects = {'toolhead': Obj({'max_accel': 12000.})}

    class P:
        def lookup_object(self, name, default=None):
            return objects.get(name, default)

        def get_reactor(self):
            return Reactor()
    cfs.printer = P()
    cfs.objects = objects
    cfs.status = {}
    # Hi values (config/mainline_stage2_hi.cfg)
    cfs.cutter_dir, cfs.cutter_x_limit = 1., 285.
    cfs.cutter_x, cfs.cutter_y = 240., 130.
    cfs.cutter_cut_x, cfs.cutter_release_x = 279.6, 240.
    cfs.cutter_speed, cfs.cutter_ram_speed = 200., 250.
    cfs.cutter_release_speed, cfs.cutter_ram_accel = 150., 5000.
    cfs.cutter_retract_length, cfs.cutter_retract_speed = 0., 6.
    cfs.cutter_tip_length = 0.
    cfs.cutter_tip_push_speed = cfs.cutter_tip_pull_speed = 5.
    cfs.cutter_cal_start_x = 269.6
    cfs.cutter_cal_min_x, cfs.cutter_cal_max_x = 269.6, 281.1
    cfs.cutter_stall_offset = .4
    cfs.purge_safe_y, cfs.purge_return_x = 261., 240.
    cfs.purge_entry_x, cfs.purge_gate_x = 10., 0.
    cfs.purge_x, cfs.purge_y = -18.5, 130.
    cfs.wipe_style, cfs.clean_passes = 'strip', 7
    cfs.clean_right_x, cfs.clean_left_x, cfs.clean_y = -5., 0., 130.
    cfs.clean_speed = cfs.clean_circle_speed = 200.
    cfs.clean_brush_strokes = 0
    return cfs


class StripWipeTest(unittest.TestCase):
    def test_strip_wipe_is_plain_passes_over_the_strip(self):
        cfs = base_cfs(FakeGcode())
        lines = cfs._purge_wipe_script(False).splitlines()
        self.assertEqual(lines[0], "G90")
        self.assertEqual(lines[-1], "M400")
        moves = lines[1:-1]
        self.assertEqual(len(moves), 14)          # 7 passes out and back
        self.assertTrue(all(m.startswith(("G1 X-5.000 Y130.000",
                                          "G1 X-18.500 Y130.000"))
                            for m in moves))
        self.assertFalse(any(m.startswith(("G2", "G3")) for m in moves))
        # ends over the chute
        self.assertTrue(moves[-1].startswith("G1 X-18.500 Y130.000"))

    def test_final_wipe_leaves_by_the_entry_point(self):
        cfs = base_cfs(FakeGcode())
        lines = cfs._purge_wipe_script(True).splitlines()
        self.assertEqual(lines[-4:-1], ["G1 X10.000 F12000.000",
                                        "G1 Y261.000 F12000.000",
                                        "G1 X240.000 F12000.000"])

    def test_k2_brush_wipe_is_unchanged(self):
        cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
        for name, value in (
                ('clean_speed', 200.), ('clean_circle_speed', 166.666667),
                ('clean_right_x', 137.), ('clean_left_x', 127.),
                ('clean_circle_left_x', 145.), ('clean_circle_right_x', 155.),
                ('clean_y', 291.5), ('clean_brush_strokes', 0),
                ('purge_x', 115.), ('purge_y', 291.5),
                ('purge_safe_y', 261.), ('purge_return_x', 205.)):
            setattr(cfs, name, value)
        lines = cfs._purge_wipe_script(True).splitlines()
        self.assertEqual(sum(ln.startswith("G2") for ln in lines), 1)
        self.assertEqual(sum(ln.startswith("G3") for ln in lines), 1)
        # three passes back over the chute, as before clean_passes existed
        self.assertEqual(
            sum(ln.startswith("G1 X115.000 Y291.500") for ln in lines), 3)


def cut_cfs():
    gcode = FakeGcode()
    cfs = base_cfs(gcode)
    for name in ('_require_cfs', '_require_actuation', '_require_idle',
                 '_require_position', '_set_runout_sensor_enabled',
                 '_require_hot_extruder', '_count'):
        setattr(cfs, name, lambda *a: None)
    cfs._require_safe_motion = lambda *a: (240., 261., 10.)
    cfs._run_query = lambda name: {'slots_loaded': [1, 1, 1, 1]}
    cfs._measuring = lambda: {}
    cfs._path_mask = lambda m: cfs.SLOT_MASKS['A']
    cfs._nozzle_has_filament = lambda: True
    cfs.manage_runout_sensor = True
    cfs.cutter_switch_state = True
    cfs.cutter_events = None
    cfs._cmd_CFS_UNLOAD_AFTER_CUT = lambda gcmd: None
    return cfs, gcode


class RightHandCutTest(unittest.TestCase):
    def test_cut_rams_right_and_releases_to_the_staging_point(self):
        cfs, gcode = cut_cfs()
        cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        lines = gcode.lines()
        stage = lines.index("G1 X240.000 F12000.000")
        ram = lines.index("G1 X279.600 Y130.000 F15000.000")   # 250 mm/s
        release = lines.index("G1 X240.000 Y130.000 F9000.000")
        self.assertLess(stage, ram)
        self.assertLess(ram, release)
        # then the detour along the back of the bed to the chute
        tail = lines[release:]
        self.assertLess(tail.index("G1 Y261.000 F12000.000"),
                        tail.index("G1 X10.000 F12000.000"))
        self.assertIn("G1 X-18.500 Y130.000 F12000.000", tail)

    def test_no_extruder_moves_around_the_cut_when_lengths_are_zero(self):
        # stock Hi: retract_len_before_cut 0, nothing logged at the cut
        cfs, gcode = cut_cfs()
        cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        self.assertFalse(any(ln.startswith("G1 E") for ln in gcode.lines()))

    def test_k2_lengths_still_retract_and_shape_the_tip(self):
        cfs, gcode = cut_cfs()
        cfs.cutter_retract_length, cfs.cutter_tip_length = 2., 7.
        cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        e = [ln for ln in gcode.lines() if ln.startswith("G1 E")]
        self.assertEqual(e, ["G1 E0.010 F60", "G1 E-2.000 F360.000",
                             "G1 E-2.000 F360.000", "G1 E7.000 F300.000",
                             "G1 E-7.000 F300.000"])


class SlowKnifeReturnTest(unittest.TestCase):
    # 11-16 bench: the Hi's knife was still pressed 0.2 s after the release
    # and came back a few seconds later; the cut stopped as "did not return"
    def make(self, returns_after):
        cfs, gcode = cut_cfs()
        clock = {'t': 0.}

        class R:
            def monotonic(self):
                return clock['t']

            def pause(self, t):
                clock['t'] = max(t, clock['t'] + .01)
                if clock['t'] >= returns_after:
                    cfs.cutter_switch_state = True
        reactor = R()
        cfs.printer.get_reactor = lambda: reactor

        def on_line(line):
            if line.startswith("G1 X279.600"):       # the ram presses it
                cfs.cutter_switch_state = False
        gcode.on_line = on_line
        done = []
        cfs._cmd_CFS_UNLOAD_AFTER_CUT = lambda gcmd: done.append(1)
        return cfs, done

    def test_a_knife_that_returns_within_the_timeout_is_a_good_cut(self):
        cfs, done = self.make(returns_after=3.)
        cfs.cutter_return_timeout = 8.
        cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        self.assertEqual(done, [1])

    def test_a_knife_that_never_returns_still_stops_the_unload(self):
        cfs, done = self.make(returns_after=1e9)
        cfs.cutter_return_timeout = 8.
        with self.assertRaises(RuntimeError) as ctx:
            cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        self.assertIn("did not return", str(ctx.exception))
        self.assertEqual(done, [])

    def test_without_a_timeout_the_k2_check_is_immediate(self):
        cfs, done = self.make(returns_after=3.)
        with self.assertRaises(RuntimeError):
            cfs.cmd_CFS_CUT_UNLOAD(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))


class OnePullUnloadTest(unittest.TestCase):
    def make(self, final):
        gcode = FakeGcode()
        cfs = base_cfs(gcode)
        frames = []
        for name in ('_require_actuation', '_require_idle',
                     '_require_hot_extruder'):
            setattr(cfs, name, lambda *a: None)
        cfs._run_query = lambda name: {'slots_loaded': [1, 1, 1, 1]}
        cfs._measuring = lambda: {}
        cfs._path_mask = lambda m: cfs.SLOT_MASKS['A']
        cfs._nozzle_has_filament = lambda: True
        cfs._wait_nozzle_filament = lambda expected, timeout, poll=None: True
        cfs._run_actuation = lambda name, payload, timeout=.2: \
            frames.append((name, list(payload)))
        cfs.unload_rewind_pause_ms = 0
        cfs.unload_fast_pulls, cfs.unload_pull_length = 1, 15.
        cfs.unload_pull_speed = 6.
        cfs.unload_final_length, cfs.unload_final_speed = final, 1.
        cfs.unload_bus_timeout = cfs.unload_sensor_timeout = 1.
        cfs.stall_retry_motion = False
        return cfs, gcode, frames

    def test_stock_hi_unload_is_one_start_one_pull_then_finish(self):
        cfs, gcode, frames = self.make(0.)
        cfs._cmd_CFS_UNLOAD_AFTER_CUT(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        names = [n for n, p in frames]
        self.assertEqual(names, ['SET_BOX_MODE IDLE', 'RETRUDE START',
                                 'RETRUDE FINISH'])
        pulls = [ln for ln in gcode.lines() if ln.startswith("G1 E-")]
        self.assertEqual(pulls, ["G1 E-15.000 F360.000"])

    def test_a_final_pull_still_gets_its_own_start_frame(self):
        cfs, gcode, frames = self.make(10.)
        cfs._cmd_CFS_UNLOAD_AFTER_CUT(Cmd({'SLOT': 'A', 'CONFIRM': '1'}))
        self.assertEqual([n for n, p in frames].count('RETRUDE START'), 2)
        self.assertIn("G1 E-10.000 F60.000", gcode.lines())


class RightHandCalibrationTest(unittest.TestCase):
    def test_sensor_method_pushes_right_until_the_knife_trips(self):
        trip_at = 277.3

        def on_line(line):
            if line.startswith("G1 X") and "F300" in line:   # the slow push
                x = float(line.split()[1][1:])
                cfs.cutter_switch_state = x < trip_at - 1e-9
            elif line.startswith("G1 X240.000"):             # released
                cfs.cutter_switch_state = True
        gcode = FakeGcode(on_line)
        cfs = base_cfs(gcode)
        cfs._require_idle = lambda *a: None
        cfs._require_safe_motion = lambda *a: (240., 261., 10.)
        cfs._nozzle_has_filament = lambda: False
        cfs.cutter_switch_state = True
        cmd = Cmd({'CONFIRM': '1'})
        cfs.cmd_CFS_CUTTER_CALIBRATE(cmd)
        self.assertAlmostEqual(cfs.status['cutter_cal_x'], 277.3, places=2)
        # the cut position is 2.3 mm past where the knife trips
        self.assertIn("2.30mm past it", cmd.info[-1])
        self.assertIn("returned", cmd.info[-1])
        xs = [float(ln.split()[1][1:]) for ln in gcode.lines()
              if ln.startswith("G1 X") and "F300" in ln]
        self.assertLessEqual(max(xs), 281.1 + 1e-6)          # never past it

    def test_sensor_method_stops_at_the_window_and_reports_no_trip(self):
        gcode = FakeGcode()
        cfs = base_cfs(gcode)
        cfs._require_idle = lambda *a: None
        cfs._require_safe_motion = lambda *a: (240., 261., 10.)
        cfs._nozzle_has_filament = lambda: False
        cfs.cutter_switch_state = True
        with self.assertRaises(RuntimeError) as ctx:
            cfs.cmd_CFS_CUTTER_CALIBRATE(Cmd({'CONFIRM': '1'}))
        self.assertIn("never tripped", str(ctx.exception))
        xs = [float(ln.split()[1][1:]) for ln in gcode.lines()
              if ln.startswith("G1 X") and "F300" in ln]
        self.assertLessEqual(max(xs), 281.1 + 1e-6)

    def test_a_stop_beyond_x_travel_is_refused(self):
        cfs = base_cfs(FakeGcode())
        cfs._require_idle = lambda *a: None
        cfs._require_safe_motion = lambda *a: (240., 261., 10.)
        cfs._nozzle_has_filament = lambda: False
        cfs.cutter_switch_state = True
        with self.assertRaises(RuntimeError):
            cfs.cmd_CFS_CUTTER_CALIBRATE(Cmd({'CONFIRM': '1',
                                              'STOP': '286'}))

    def test_stall_method_rams_right_and_backs_off_the_end_stop(self):
        stop_x = 280.0
        latched = {'x': False, 'y': False}

        class Bus:
            def query_protection(self, name):
                return {'stalled': latched[name], 'has_error': False}

            def clear_faults(self, name):
                latched[name] = False

        def on_line(line):
            if line.startswith("G1 X") and " Y" in line and "F15000" in line:
                if float(line.split()[1][1:]) >= stop_x - 1e-9:
                    latched['x'] = True
        gcode = FakeGcode(on_line)
        cfs = base_cfs(gcode)
        cfs.objects['k2_motor_bus'] = Bus()
        cfs._require_idle = cfs._require_safe_motion = lambda *a: None
        cfs._nozzle_has_filament = lambda: False
        cmd = Cmd({'METHOD': 'STALL', 'CONFIRM': '1', 'START': '278'})
        cfs.cmd_CFS_CUTTER_CALIBRATE(cmd)
        # hits at 280.0 and 280.1; cut 0.4 short of the second
        self.assertEqual(cfs.status['cutter_cal_x'], 279.7)
        self.assertIn("cutter_cut_x: 279.70", cmd.info[-1])


if __name__ == "__main__":
    unittest.main()
