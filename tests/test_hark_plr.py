import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
plr_mod = importlib.import_module("extras.hark_plr")


class Obj:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class FakeReactor:
    def __init__(self):
        self.now = 100.

    def monotonic(self):
        return self.now

    def register_timer(self, fn, when):
        return fn


class FakeStepper:
    def __init__(self, name, offset):
        self.name, self.offset = name, offset

    def get_name(self):
        return self.name

    def mcu_to_commanded_position(self, mcu):
        return mcu * 0.01 + self.offset


class FakeRail:
    def __init__(self, steppers):
        self.steppers = steppers

    def get_steppers(self):
        return self.steppers


class FakeToolhead:
    def __init__(self, steppers):
        self.rails = [None, None, FakeRail(steppers)]
        self.print_time = 10.
        self.est = 0.
        self.pos = [0., 0., 260., 0.]
        self.set_calls = []

    def get_status(self, eventtime=None):
        return {'print_time': self.print_time,
                'estimated_print_time': self.est,
                'max_velocity': 800., 'max_accel': 12000.,
                'minimum_cruise_ratio': .5,
                'square_corner_velocity': 8.}

    def get_kinematics(self):
        return self

    def get_position(self):
        return list(self.pos)

    def set_position(self, pos, homing_axes=""):
        self.pos = list(pos)
        self.set_calls.append((list(pos), homing_axes))


class FakeGcodeMove:
    def __init__(self):
        self.absolute_coord = True
        self.absolute_extrude = False
        self.base_position = [0., 0., 0., 0.]
        self.last_position = [100., 120., 2.4, 0.]
        self.homing_position = [0., 0., -0.04, 0.]
        self.speed, self.speed_factor, self.extrude_factor = 25., 1., 1.
        self.saved_states = {}


class FakeSD:
    def __init__(self, root, name):
        self.sdcard_dirname = root
        self.path = os.path.join(root, name)
        self.next_file_position = 0
        self.gcode = None

    def is_active(self):
        return True

    def file_path(self):
        return self.path


class FakeGcode:
    def __init__(self):
        self.scripts, self.info = [], []

    def register_command(self, *a, **k):
        pass

    def respond_info(self, msg):
        self.info.append(msg)

    def run_script_from_command(self, script):
        self.scripts.append(script)


class FakeZAlign:
    position_endstop = 263.

    def __init__(self, bottom):
        self.bottom_mcu = bottom


class FakePrinter:
    def __init__(self, objects, reactor, root):
        self.objects, self.reactor, self.root = objects, reactor, root
        self.shutdown = False

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, *a):
        pass

    def get_start_args(self):
        return {'config_file': os.path.join(self.root, 'printer.cfg')}

    def is_shutdown(self):
        return self.shutdown


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return default

    def getboolean(self, name, default=None):
        return default

    def getfloat(self, name, default=None, **kw):
        return default


def make(steppers=None, bottom=None):
    root = tempfile.mkdtemp()
    gfile = os.path.join(root, 'part.gcode')
    with open(gfile, 'w') as f:
        f.write("G1 X1\n" * 2000)
    reactor = FakeReactor()
    steppers = steppers or [FakeStepper('stepper_z', 0.)]
    th = FakeToolhead(steppers)
    gcode = FakeGcode()
    objects = {
        'gcode': gcode, 'toolhead': th, 'gcode_move': FakeGcodeMove(),
        'print_stats': Obj({'state': 'standby'}),
        'virtual_sdcard': FakeSD(root, 'part.gcode'),
        'extruder': Obj({'target': 220., 'pressure_advance': .04,
                         'smooth_time': .06}),
        'heater_bed': Obj({'target': 60.}),
        'fan': Obj({'speed': .5}),
        'k2_z_align': FakeZAlign(bottom or {'stepper_z': 26000}),
    }
    printer = FakePrinter(objects, reactor, root)
    plr = plr_mod.HarkPLR(FakeConfig(printer))
    plr._ready()
    return plr, objects, reactor


class HarkPLRTest(unittest.TestCase):
    def start_print(self, plr, objects, reactor):
        objects['print_stats'].status['state'] = 'printing'
        plr._tick(reactor.now)

    def test_notes_only_after_moves_and_once_done(self):
        plr, objects, reactor = make()
        self.start_print(plr, objects, reactor)
        sd = objects['virtual_sdcard']
        sd.next_file_position = 600
        plr.after_line("T1")            # macro: never noted
        self.assertEqual(plr.candidates, [])
        plr.after_line("G28 X")         # not a print move
        self.assertEqual(plr.candidates, [])
        plr.after_line("G1 X10 Y10 E0.5")
        self.assertEqual(plr.candidates[0]['file_position'], 600)
        self.assertIsNone(plr.safe)     # not made yet
        objects['toolhead'].est = 11.5
        reactor.now += 20
        plr._tick(reactor.now)
        self.assertEqual(plr.safe['file_position'], 600)
        with open(plr.state_path) as f:
            j = json.load(f)
        self.assertEqual(j['file']['position'], 600)
        self.assertEqual(j['file']['path'], 'part.gcode')
        self.assertEqual(j['heaters']['extruder'], 220.)
        self.assertEqual(j['z_frame']['motors']['stepper_z'], 260.)

    def test_complete_and_own_error_delete_but_shutdown_keeps(self):
        for end, shutdown, kept in (('complete', False, False),
                                    ('cancelled', False, False),
                                    ('error', False, False),
                                    ('error', True, True)):
            plr, objects, reactor = make()
            self.start_print(plr, objects, reactor)
            objects['virtual_sdcard'].next_file_position = 300
            plr.after_line("G1 X1")
            objects['toolhead'].est = 99.
            reactor.now += 20
            plr._tick(reactor.now)
            self.assertTrue(os.path.exists(plr.state_path))
            plr.printer.shutdown = shutdown
            objects['print_stats'].status['state'] = end
            plr._tick(reactor.now + 1)
            self.assertEqual(os.path.exists(plr.state_path), kept, end)

    def test_restart_offers_the_journal(self):
        plr, objects, reactor = make()
        self.start_print(plr, objects, reactor)
        objects['virtual_sdcard'].next_file_position = 300
        plr.after_line("G1 X1")
        objects['toolhead'].est = 99.
        reactor.now += 20
        plr._tick(reactor.now)
        again = plr_mod.HarkPLR(FakeConfig(plr.printer))
        again._ready()
        self.assertTrue(again.status['available'])
        self.assertEqual(again.status['z'], 2.4)

    def test_z_frame_maps_the_switches_into_the_print(self):
        plr, objects, reactor = make()
        th = objects['toolhead']
        th.pos = [0., 0., 263. - 5., 0.]    # KZ_ALIGN_BOTTOM retracted 5
        plr._apply_z_frame(FakeGcode(), {
            'motors': {'stepper_z': 261.3}, 'position_endstop': 263.})
        self.assertAlmostEqual(th.pos[2], 261.3 - 5.)
        self.assertEqual(th.set_calls[-1][1], 'z')

    def test_validate_refuses_a_changed_file(self):
        plr, objects, reactor = make()
        self.start_print(plr, objects, reactor)
        objects['virtual_sdcard'].next_file_position = 300
        plr.after_line("G1 X1")
        objects['toolhead'].est = 99.
        reactor.now += 20
        plr._tick(reactor.now)
        objects['print_stats'].status['state'] = 'error'
        plr.printer.shutdown = True
        plr._tick(reactor.now + 1)
        journal = plr._read_journal()
        self.assertEqual(plr._validate(journal), 'part.gcode')
        with open(objects['virtual_sdcard'].path, 'a') as f:
            f.write("G1 X2\n")
        with self.assertRaises(ValueError):
            plr._validate(journal)

    def test_resume_order_and_file_start(self):
        plr, objects, reactor = make()
        self.start_print(plr, objects, reactor)
        objects['virtual_sdcard'].next_file_position = 300
        plr.after_line("G1 X1")
        objects['toolhead'].est = 99.
        reactor.now += 20
        plr._tick(reactor.now)
        objects['print_stats'].status['state'] = 'error'
        plr.printer.shutdown = True
        plr._tick(reactor.now + 1)
        gcode = objects['gcode']
        gcode.scripts[:] = []
        plr.journal = plr._read_journal()

        class Cmd(FakeGcode):
            def get_int(self, name, default=None, **kw):
                return 1

            def error(self, msg):
                return RuntimeError(msg)
        plr.cmd_PLR_RESUME(Cmd())
        text = "\n".join(gcode.scripts)
        order = [text.index(k) for k in (
            "M190 S60.0", "M109 S220.0", "KZ_ALIGN_BOTTOM", "G28 X Y",
            "CFS_PURGE_POSITION", "SET_PRESSURE_ADVANCE",
            "G1 X100.000 Y120.000", "RESTORE_GCODE_STATE NAME=_HARK_PLR",
            "M23 part.gcode", "M26 S300", "M24")]
        self.assertEqual(order, sorted(order))
        # the nozzle is stuck in the part until it is at print temperature,
        # so nothing moves before that wait
        hot = text.index("M109 S220.0")
        for move in ("KZ_ALIGN_BOTTOM", "G28", "G1 "):
            self.assertGreater(text.index(move), hot, move)
        # off the part it waits at standby, then heats again to prime
        self.assertLess(text.index("KZ_ALIGN_BOTTOM"),
                        text.index("M104 S140.0"))
        self.assertLess(text.index("M104 S140.0"), text.index("G28 X Y"))
        self.assertGreater(text.index("M109 S220.0", hot + 1),
                           text.index("CFS_PURGE_POSITION"))
        # the bed goes up clear of the part before anything moves over it
        self.assertIn("G1 Z5.400", text)
        self.assertNotIn("NOZZLE_WIPE", text)

    def test_resume_needs_confirm(self):
        plr, objects, reactor = make()

        class Cmd(FakeGcode):
            def get_int(self, name, default=None, **kw):
                return 0

            def error(self, msg):
                return RuntimeError(msg)
        with self.assertRaises(RuntimeError):
            plr.cmd_PLR_RESUME(Cmd())

    def test_line_watch_passes_through(self):
        plr, objects, reactor = make()
        watch = objects['virtual_sdcard'].gcode
        self.assertIsInstance(watch, plr_mod._LineWatch)


if __name__ == '__main__':
    unittest.main()
