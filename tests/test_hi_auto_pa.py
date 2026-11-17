"""The Hi's automatic pressure advance (the K2's stationary test): wired
into the print start, idle loads and colour changes during a print."""
import importlib
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
CFG = (ROOT / "config" / "mainline_stage2_hi.cfg").read_text()


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


class HiWiringTest(unittest.TestCase):
    def test_modules_are_loaded(self):
        for name in ("pa_pressure_log", "pa_tester", "hark_flow_cal"):
            self.assertIn("\n[%s]\n" % name, CFG)
        self.assertIn("force_sensor: pa_pressure_log", section("pa_tester"))

    def test_every_print_start_measures_after_the_load(self):
        start = section("gcode_macro START_PRINT")
        self.assertLess(start.index("PA_HOLD_MEASURED\n"),
                        start.index("CFS_PRINT_START SLOT={cfs_slot}"))
        self.assertLess(start.index("CFS_PRINT_START SLOT={cfs_slot}"),
                        start.index("_PA_AUTO SLOT={slot} KEEP_FEED=1 HOLD=1"))
        # before the wipe and the prime line
        self.assertLess(start.index("_PA_AUTO SLOT={slot}"),
                        start.index("HI_WIPE"))
        self.assertIn('VARIABLE=measured VALUE="[]"', start)

    def test_idle_loads_measure(self):
        load = section("gcode_macro CFS_LOAD_SLOT")
        self.assertLess(load.index("CFS_CHANGE_SLOT"),
                        load.index("_PA_AUTO SLOT={slot}"))
        self.assertLess(load.index("_PA_AUTO SLOT={slot}"),
                        load.index("CFS_FEED_STOP"))
        self.assertIn("_PA_AUTO SLOT=E", section("gcode_macro LOAD_FILAMENT"))

    def test_colour_change_measures_new_slots_once(self):
        hook = section("gcode_macro _CFS_CHANGE_PURGED")
        self.assertIn("slot not in a.measured", hook)
        self.assertIn('print_stats.state == "printing"', hook)
        self.assertIn("_PA_AUTO SLOT={slot} KEEP_FEED=1 HOLD=1", hook)
        # a slot already measured still gets its own saved PA
        self.assertIn("CFS_SLOT_APPLY SLOT={slot}", hook)
        auto = section("gcode_macro _PA_AUTO")
        self.assertIn("variable_on_change: 1", auto)
        self.assertIn("ON_ERROR=report", auto)
        self.assertIn("a.measured + [slot]", auto)

    def test_m106_halving_is_not_hit(self):
        # the Hi's M106 halves its argument: the cool-down uses the Hi's
        # own wipe macro, not the K2's M106 S255
        self.assertNotIn("M106 S255", section("gcode_macro _PA_STATIC_DONE"))


class FakeGcode:
    def __init__(self, fail=False):
        self.ran, self.raw, self.fail = [], [], fail

    def run_script_from_command(self, script):
        self.ran.append(script)
        if self.fail:
            raise RuntimeError("boom")

    def respond_raw(self, msg):
        self.raw.append(msg)


class ChangeHookTest(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("extras.k2_cfs")

    def make(self, macro=True, fail=False):
        obj = self.mod.K2CFS.__new__(self.mod.K2CFS)
        objects = {'gcode_macro _CFS_CHANGE_PURGED': object()} if macro else {}
        obj.printer = types.SimpleNamespace(
            lookup_object=lambda name, default=None: objects.get(name, default))
        obj.gcode = FakeGcode(fail)
        return obj

    def test_runs_the_macro_with_the_new_slot(self):
        obj = self.make()
        obj._after_change_purge('C', {'old_slot': 'B', 'new_temp': 250.})
        self.assertEqual(obj.gcode.ran,
                         ["_CFS_CHANGE_PURGED SLOT=C OLD=B TEMP=250.0"])

    def test_no_macro_no_call(self):
        obj = self.make(macro=False)
        obj._after_change_purge('C', {'old_slot': 'B', 'new_temp': 250.})
        self.assertEqual(obj.gcode.ran, [])

    def test_failure_is_reported_not_raised(self):
        obj = self.make(fail=True)
        obj._after_change_purge('C', {'old_slot': 'B', 'new_temp': 250.})
        self.assertIn("carries on", obj.gcode.raw[0])


class KampAndRubTest(unittest.TestCase):
    def test_print_start_uses_kamp_with_labelled_objects(self):
        start = section("gcode_macro START_PRINT")
        self.assertIn("variable_purge_style: 'kamp'", start)
        self.assertIn("printer.exclude_object.objects|length > 0", start)
        park = start.index("SMART_PARK")
        self.assertLess(park, start.index("M109 S{extruder_temp}", park))
        self.assertLess(start.index("M109 S{extruder_temp}", park),
                        start.index("LINE_PURGE FLOW={volumetric}"))
        # no labelled objects (or 'stock'): the old prime line
        self.assertIn("_HI_PRIME_LINE VOLUMETRIC={volumetric}", start)

    def test_purge_stays_on_the_bed(self):
        purge = section("gcode_macro LINE_PURGE")
        self.assertIn("bmx - purge_amount - 10", purge)
        self.assertIn("bmy - purge_amount - 10", purge)
        park = section("gcode_macro SMART_PARK")
        self.assertIn("[x_min, 0]|max", park)
        self.assertNotIn("axis_minimum", park)

    def test_rub_before_both_wiper_rezeros(self):
        scrub = section("gcode_macro _NOZZLE_CLEAR_SCRUB")
        self.assertEqual(scrub.count(
            "SET_KINEMATIC_POSITION Z=0\n    _NOZZLE_CONTACT_RUB"), 2)
        rub = section("gcode_macro _NOZZLE_CONTACT_RUB")
        self.assertLess(rub.index("G1 Z{0 - r.press|float}"),
                        rub.index("PROBE"))
        self.assertIn("SET_KINEMATIC_POSITION Z=0", rub)


if __name__ == '__main__':
    unittest.main()
