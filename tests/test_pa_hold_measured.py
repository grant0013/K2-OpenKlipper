import importlib
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
fc = importlib.import_module("extras.hark_flow_cal")


class FakeReactor:
    def monotonic(self):
        return 1.


class FakeGcode:
    def __init__(self):
        self.info, self.ran = [], []

    def register_command(self, *a, **k):
        pass

    def respond_info(self, msg):
        self.info.append(msg)

    def run_script(self, script):
        self.ran.append(script)

    def get_mutex(self):
        return "mutex"


class FakeCfs:
    def __init__(self):
        self.status = {'active_slot': 'A', 'feed_slot': None}

    def get_status(self, eventtime):
        return self.status


class FakeSlots:
    def __init__(self):
        self.slots = {'A': {'pressure_advance': .052},
                      'B': {'pressure_advance': .0546},
                      'C': {'pressure_advance': 0.}}


class FakeVSD:
    def __init__(self, gcode):
        self.gcode = gcode


class FakePrinter:
    def __init__(self, root):
        self.root = root
        self.handlers = {}
        self.gcode = FakeGcode()
        self.objects = {'gcode': self.gcode, 'k2_cfs': FakeCfs(),
                        'hark_cfs_slots': FakeSlots(),
                        'virtual_sdcard': FakeVSD(self.gcode)}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return FakeReactor()

    def get_start_args(self):
        return {'config_file': self.root + '/printer.cfg'}

    def register_event_handler(self, name, fn):
        self.handlers[name] = fn


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return default

    def getfloat(self, name, default=None, **kw):
        return default

    def getfloatlist(self, name, default=None):
        return default


class FakeCmd:
    def __init__(self, **params):
        self.params = params

    def get(self, name, default=None):
        return self.params.get(name, default)


LINE = "SET_PRESSURE_ADVANCE ADVANCE=0.05; Override pressure advance value"
HELD_A = "SET_PRESSURE_ADVANCE ADVANCE=0.0520"


class HoldTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.printer = FakePrinter(tmp.name)
        self.cal = fc.HarkFlowCal(FakeConfig(self.printer))
        self.printer.handlers['klippy:ready']()
        self.file = self.printer.objects['virtual_sdcard'].gcode
        self.gcode = self.printer.gcode

    def hold(self, slot='A'):
        self.cal.cmd_PA_HOLD_MEASURED(FakeCmd(SLOT=slot))

    def run_lines(self, *lines):
        for line in lines:
            self.file.run_script(line)
        return self.gcode.ran

    def test_file_decides_until_a_result_is_saved(self):
        self.assertEqual(self.run_lines(LINE), [LINE])

    def test_saved_result_beats_the_file(self):
        self.hold()
        self.assertEqual(self.run_lines(LINE, "G1 X1"),
                         [HELD_A, "G1 X1"])
        self.assertIn("0.0520", self.gcode.info[0])
        self.assertIn("0.0500", self.gcode.info[0])

    def test_the_same_value_again_is_quiet(self):
        self.hold()
        self.assertEqual(self.run_lines(LINE, LINE), [HELD_A, HELD_A])
        self.assertEqual(len(self.gcode.info), 1)

    def test_changing_value_in_a_slot_is_a_test_and_runs(self):
        self.hold()
        second = "SET_PRESSURE_ADVANCE ADVANCE=0.06"
        third = "SET_PRESSURE_ADVANCE ADVANCE=0.05"
        self.assertEqual(self.run_lines(LINE, second, third),
                         [HELD_A, second, third])
        self.assertIsNone(self.cal.hold_slot)

    def test_tool_change_with_another_value_stays_held(self):
        # After a colour change the new slot's own saved PA is set (the
        # slicer's is still ignored), not the old slot's left running
        self.hold()
        self.run_lines(LINE)
        self.printer.objects['k2_cfs'].status['active_slot'] = 'B'
        self.assertEqual(self.run_lines("SET_PRESSURE_ADVANCE ADVANCE=0.07"),
                         [HELD_A, "SET_PRESSURE_ADVANCE ADVANCE=0.0546"])
        self.assertEqual(self.cal.hold_slot, 'A')

    def test_slot_with_no_saved_value_keeps_the_files(self):
        self.hold()
        self.printer.objects['k2_cfs'].status['active_slot'] = 'C'
        self.assertEqual(self.run_lines(LINE), [LINE])

    def test_smooth_time_survives(self):
        self.hold()
        self.assertEqual(
            self.run_lines("set_pressure_advance ADVANCE=.05 SMOOTH_TIME=.03"),
            ["SET_PRESSURE_ADVANCE ADVANCE=0.0520 SMOOTH_TIME=.03"])

    def test_smooth_time_only_line_runs(self):
        self.hold()
        line = "SET_PRESSURE_ADVANCE SMOOTH_TIME=.03"
        self.assertEqual(self.run_lines(line), [line])

    def test_clear_gives_it_back(self):
        self.hold()
        self.cal.cmd_PA_HOLD_MEASURED(FakeCmd())
        self.assertEqual(self.run_lines(LINE), [LINE])

    def test_other_attributes_pass_through(self):
        self.assertEqual(self.file.get_mutex(), "mutex")


class WiringTest(unittest.TestCase):
    def test_held_only_at_print_start_after_a_save(self):
        root = pathlib.Path(__file__).parents[1] / "config"
        for name in ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg",
                     "mainline_stage2_hi.cfg"):
            cfg = (root / name).read_text()
            if name != "mainline_stage2_hi.cfg" and "PA_HOLD_MEASURED" not in cfg:
                # the K2 configs get the hold with branch print-start-auto
                continue
            start = cfg[cfg.index("[gcode_macro START_PRINT]"):]
            self.assertLess(start.index("PA_HOLD_MEASURED\n"),
                            start.index("_PA_AUTO SLOT={slot} KEEP_FEED=1 HOLD=1"),
                            name)
            save = cfg[cfg.index("[gcode_macro _PA_STATIC_SAVE]"):]
            save = save[:save.index("[gcode_macro _PA_STATIC_END]")]
            self.assertLess(save.index("CFS_SLOT_SET SLOT={slot}"),
                            save.index("PA_HOLD_MEASURED SLOT={slot}"), name)
            self.assertEqual(save.count("PA_HOLD_MEASURED"), 1, name)
            self.assertIn("{% if params.HOLD|default(0)|int %}", save, name)
            self.assertIn("HOLD={params.HOLD|default(0)|int}\n", cfg, name)


if __name__ == '__main__':
    unittest.main()
