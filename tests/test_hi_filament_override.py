"""hark_filament_override: the loaded slot's record over the sliced file's
temperatures, flow, retraction, part fan and pressure advance, on the file's
own lines only."""
import importlib
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
fo = importlib.import_module("extras.hark_filament_override")

CONFIG_BLOCK = """
; CONFIG_BLOCK_START
; filament_flow_ratio = 0.98,0.95
; filament_retraction_length = nil,1.2
; filament_type = PLA;PETG
; nozzle_temperature = 220,250
; nozzle_temperature_initial_layer = 225,250
; retraction_length = 0.8
; fan_max_speed = 100,80
; use_relative_e_distances = 1
; CONFIG_BLOCK_END
"""

PLA = {"loaded": True, "name": "Elegoo PLA Matte", "type": "PLA",
       "nozzle_temp": 215, "nozzle_first": 220, "bed_temp": 55,
       "flow_ratio": 0.96, "retract_length": 0.4, "fan_max": 90,
       "pressure_advance": 0.042, "max_flow": 16}
PETG = {"loaded": True, "name": "CR-PETG", "type": "PETG",
        "nozzle_temp": 245, "nozzle_first": 0, "bed_temp": 75,
        "flow_ratio": 0.95, "retract_length": 0.6, "fan_max": 50,
        "pressure_advance": 0.063, "max_flow": 12}


class Gcode:
    def __init__(self):
        self.info = []
        self.cmds = {}

    def respond_info(self, msg):
        self.info.append(msg)

    def register_command(self, name, fn, desc=None):
        self.cmds[name] = fn


class Reactor:
    def monotonic(self):
        return 0.


class Slots:
    def __init__(self, slots, tmap=None):
        self.slots = slots
        self.tmap = tmap or {"T0": "A", "T1": "B", "T2": "C", "T3": "D"}

    def tool_slots(self, eventtime=None):
        return self.tmap


class PrintStats:
    layer = 1

    def get_status(self, eventtime):
        return {"info": {"current_layer": self.layer}}


class VSD:
    def __init__(self, path):
        self.path = path
        self.file_position = 0

    def file_path(self):
        return self.path


class Printer:
    def __init__(self, objects, config_dir):
        self.objects = objects
        self.config_dir = config_dir

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return Reactor()

    def get_start_args(self):
        return {"config_file": os.path.join(self.config_dir, "printer.cfg")}

    def register_event_handler(self, *a):
        pass


class Config:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get(self, key, default=None):
        return default


class Base(unittest.TestCase):
    name = "part_PLA_1h.gcode"
    block = CONFIG_BLOCK

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, self.name)
        with open(self.path, "w") as f:
            f.write("G1 X1 Y1\n" * 50 + self.block)
        self.gcode = Gcode()
        self.vsd = VSD(self.path)
        self.slots = Slots({"A": dict(PLA), "B": dict(PETG)})
        self.ps = PrintStats()
        printer = Printer({"gcode": self.gcode, "hark_cfs_slots": self.slots,
                           "print_stats": self.ps}, self.dir)
        self.mod = fo.HarkFilamentOverride(Config(printer))
        self.mod.vsd = self.vsd

    def run_line(self, line):
        self.vsd.file_position += 10
        return self.mod.file_line(line)

    def start(self, tool=0):
        return self.run_line(
            "START_PRINT EXTRUDER_TEMP=225 BED_TEMP=60 MATERIAL=PLA "
            "TOTAL_LAYER=100 VOLUMETRIC=20 TOOL=%d" % tool)


class StartTest(Base):
    def test_start_print_takes_the_slot_values(self):
        out = self.start().split("\n")
        self.assertIn("EXTRUDER_TEMP=220", out[0])
        self.assertIn("BED_TEMP=55", out[0])
        self.assertIn("VOLUMETRIC=16", out[0])
        self.assertIn("TOOL=0", out[0])
        # flow 0.96 over the file's 0.98
        self.assertEqual(out[1], "M221 S98.0")
        self.assertTrue(any("slot A" in m for m in self.gcode.info))

    def test_lines_before_start_print_pass(self):
        self.assertEqual(self.run_line("M104 S225"), "M104 S225")
        self.assertEqual(self.run_line("G1 E-0.8 F2100"), "G1 E-0.8 F2100")


class TempTest(Base):
    def test_first_layer_and_later_nozzle(self):
        self.start()
        self.assertEqual(self.run_line("M104 S225"), "M104 S220")
        self.assertEqual(self.run_line("M109 S220"), "M109 S215")
        self.assertEqual(self.run_line("M104 S0"), "M104 S0")
        self.assertEqual(self.run_line("M104 S140"), "M104 S140")
        self.assertEqual(self.run_line("M104 S230"), "M104 S230")

    def test_other_tools_temperature_maps_to_its_slot(self):
        self.start()
        # T1's file temperature 250 -> slot B 245 (its first is unset)
        self.assertEqual(self.run_line("M104 S250"), "M104 S245")

    def test_bed(self):
        self.start()
        self.assertEqual(self.run_line("M140 S60"), "M140 S55")
        self.assertEqual(self.run_line("M190 S60 ; bed"), "M190 S55")
        self.assertEqual(self.run_line("M140 S0"), "M140 S0")


class FlowRetractTest(Base):
    def test_retract_wipe_and_unretract_scaled(self):
        self.start()
        k = 0.4 / 0.8 / (0.96 / 0.98)
        r = self.run_line("G1 X10 Y10 E-0.3 F3000")
        self.assertAlmostEqual(float(r.split("E")[1].split()[0]), -0.3 * k,
                               4)
        r = self.run_line("G1 E-0.5 F2100")
        self.assertAlmostEqual(float(r.split("E")[1].split()[0]), -0.5 * k,
                               4)
        # the unretract puts back what was taken (+ the file's extra 0.02)
        r = self.run_line("G1 E0.82 F2100")
        self.assertAlmostEqual(float(r.split("E")[1].split()[0]),
                               0.8 * k + 0.02, 4)
        # ordinary extrusion untouched
        self.assertEqual(self.run_line("G1 X20 Y20 E1.234"),
                         "G1 X20 Y20 E1.234")
        self.assertEqual(self.run_line("G1 E2.0 F300"), "G1 E2.0 F300")

    def test_tool_change_sets_that_slots_flow(self):
        self.start()
        out = self.run_line("T1").split("\n")
        self.assertEqual(out, ["M221 S100", "T1", "M221 S100.0"])
        # T1's retraction: file 1.2 (filament), slot 0.6
        r = self.run_line("G1 E-1.2 F2100")
        self.assertAlmostEqual(float(r.split("E")[1].split()[0]), -0.6, 4)

    def test_retraction_balance_kept_across_a_tool_change(self):
        self.start()
        self.run_line("G1 E-0.8")                 # T0: sent -0.4/0.98
        self.run_line("T1")
        r = self.run_line("G1 E0.8")
        self.assertAlmostEqual(float(r.split("E")[1]), 0.4 / (0.96 / 0.98),
                               4)


class FanPaTest(Base):
    def test_fan_capped(self):
        self.start()
        self.assertEqual(self.run_line("M106 S255"), "M106 S229")
        self.assertEqual(self.run_line("M106 S100"), "M106 S100")
        self.assertEqual(self.run_line("M106 P2 S255"), "M106 P2 S255")

    def test_pa_from_the_slot_until_the_file_varies_it(self):
        self.start()
        out = self.run_line("SET_PRESSURE_ADVANCE ADVANCE=0.02")
        self.assertEqual(out, "SET_PRESSURE_ADVANCE ADVANCE=0.0420")
        self.assertEqual(self.run_line("SET_PRESSURE_ADVANCE ADVANCE=0.02"),
                         "SET_PRESSURE_ADVANCE ADVANCE=0.0420")
        self.assertEqual(self.run_line("SET_PRESSURE_ADVANCE ADVANCE=0.03"),
                         "SET_PRESSURE_ADVANCE ADVANCE=0.03")


class OptOutTest(Base):
    def test_print_zero_line_keeps_the_file(self):
        self.run_line("FILAMENT_OVERRIDE PRINT=0")
        self.mod.cmd_FILAMENT_OVERRIDE(FakeCmd({"PRINT": 0}))
        self.assertIn("EXTRUDER_TEMP=225", self.start())
        self.assertEqual(self.run_line("M104 S220"), "M104 S220")

    def test_switched_off(self):
        self.mod.enabled = False
        self.assertIn("EXTRUDER_TEMP=225", self.start())

    def test_new_print_resets(self):
        self.mod.cmd_FILAMENT_OVERRIDE(FakeCmd({"PRINT": 0}))
        self.vsd.file_position = 0
        self.assertIn("EXTRUDER_TEMP=220", self.mod.file_line(
            "START_PRINT EXTRUDER_TEMP=225 BED_TEMP=60"))


class CalibNameTest(Base):
    name = "calib_temp_slotA.gcode"

    def test_calibration_file_prints_as_sliced(self):
        self.assertIn("EXTRUDER_TEMP=225", self.start())
        self.assertEqual(self.run_line("M104 S220"), "M104 S220")


class LayerTwoTest(Base):
    block = CONFIG_BLOCK.replace("225,250", "220,250")

    def test_same_file_temps_get_the_slots_drop_at_layer_2(self):
        self.start()
        out = self.run_line("SET_PRINT_STATS_INFO CURRENT_LAYER=2")
        self.assertEqual(out.split("\n")[1], "M104 S215")
        self.ps.layer = 2
        self.assertEqual(self.run_line("M104 S220"), "M104 S215")


class NoConfigTest(Base):
    block = ""

    def test_without_the_files_values_only_absolute_ones_change(self):
        out = self.start().split("\n")
        self.assertIn("EXTRUDER_TEMP=220", out[0])
        self.assertEqual(out[1], "M221 S100")
        self.assertEqual(self.run_line("G1 E-0.8"), "G1 E-0.8")


class TypeMismatchTest(Base):
    def setUp(self):
        super().setUp()
        # slot B labelled PLA, the file's T1 was sliced for PETG
        self.slots.slots["B"] = dict(PLA, name="CR-PLA")

    def test_that_tool_prints_as_sliced(self):
        self.start()
        out = self.run_line("T1").split("\n")
        self.assertEqual(out[-1], "M221 S100")
        self.assertEqual(self.run_line("M104 S250"), "M104 S250")
        self.assertEqual(self.run_line("G1 E-1.2"), "G1 E-1.2")
        self.assertTrue(any("sliced for PETG" in m for m in self.gcode.info))

    def test_start_slot_mismatch_keeps_the_whole_start(self):
        self.assertIn("EXTRUDER_TEMP=225", self.start(tool=1))

    def test_family(self):
        self.assertEqual(fo.family("PLA-CF"), "PLA")
        self.assertEqual(fo.family("PETG HF"), "PETG")
        self.assertNotEqual(fo.family("PCTG"), fo.family("PETG"))


class FakeCmd:
    def __init__(self, params):
        self.params = params

    def get_int(self, name, default=None, minval=None, maxval=None):
        return int(self.params.get(name, default)) \
            if self.params.get(name, default) is not None else None

    def respond_info(self, msg):
        pass

    def error(self, msg):
        return Exception(msg)


if __name__ == "__main__":
    unittest.main()
