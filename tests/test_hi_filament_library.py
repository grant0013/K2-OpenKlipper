"""The Hi's OrcaSlicer filament library, the shop colour palette, tuned
values remembered per filament, the PA pattern / temperature tower picks,
and the KlipperScreen panel's helpers (26 Nov 2026)."""
import importlib
import json
import logging
import os
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_cfs_tool_map import (FakeConfig, FakeObj, FakePrinter,  # noqa
                               slots_mod)
hc = importlib.import_module("extras.hark_calib")

LIB = json.loads((ROOT / "tools" / "filaments" /
                  "filament_library_hi.json").read_text())["library"]


def load_panel():
    """host/klipperscreen/cfs.py without GTK or KlipperScreen."""
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a: None
    repo = types.ModuleType("gi.repository")
    for name in ("GLib", "Gtk", "Pango"):
        setattr(repo, name, types.SimpleNamespace(
            DrawingArea=object, PositionType=None))
    ks = types.ModuleType("ks_includes")
    sp = types.ModuleType("ks_includes.screen_panel")
    sp.ScreenPanel = object
    saved = {k: sys.modules.get(k) for k in (
        "gi", "gi.repository", "ks_includes", "ks_includes.screen_panel")}
    sys.modules.update({"gi": gi, "gi.repository": repo, "ks_includes": ks,
                        "ks_includes.screen_panel": sp})
    try:
        path = ROOT / "host" / "klipperscreen" / "cfs.py"
        spec = importlib.util.spec_from_file_location("ks_cfs", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


panel = load_panel()


class LibraryTest(unittest.TestCase):
    def test_has_the_matte_and_brand_profiles(self):
        names = {e["name"] for e in LIB}
        for name in ("Generic PLA Matte", "Elegoo PLA Matte",
                     "Bambu PLA Matte", "eSUN PLA-Matte", "CR-PLA Matte",
                     "SUNLU PLA Matte", "eSUN PLA+", "Generic PLA Silk"):
            self.assertIn(name, names)
        self.assertGreater(len(LIB), 300)

    def test_entries_are_complete_and_unique(self):
        ids = [e["id"] for e in LIB]
        self.assertEqual(len(ids), len(set(ids)))
        names = [e["name"].lower() for e in LIB]
        self.assertEqual(len(names), len(set(names)))
        for e in LIB:
            self.assertTrue(e["id"].startswith("lib-"), e["id"])
            self.assertEqual(e["pressure_advance"], 0)
            self.assertTrue(150 <= e["nozzle_temp"] <= 400, e["name"])
            self.assertTrue(e["type"] and e["type"] == e["type"].upper())
            self.assertTrue(e["source_ref"].startswith("OrcaSlicer v2.5.2"))

    def test_hi_database_names_are_not_duplicated(self):
        names = {e["name"] for e in LIB}
        for name in ("Hyper PLA", "CR-PETG", "Generic PLA", "Generic PETG"):
            self.assertNotIn(name, names)

    def test_finish_marked(self):
        by = {e["name"]: e for e in LIB}
        self.assertEqual(by["Elegoo PLA Matte"]["finish"], "Matte")
        self.assertEqual(by["Generic PLA Silk"]["finish"], "Silk")


class SlotsBase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = os.path.join(self.tmp.name, "slots.json")
        with open(self.store, "w") as f:
            json.dump({"revision": 1, "slots": {}, "library": LIB}, f)
        orig = slots_mod.HarkCfsSlots._publish_lanes
        slots_mod.HarkCfsSlots._publish_lanes = lambda self, *a: None
        self.addCleanup(setattr, slots_mod.HarkCfsSlots, "_publish_lanes",
                        orig)
        self.make()

    def make(self):
        self.printer = FakePrinter()
        self.printer.objects["k2_cfs"] = FakeObj({})
        self.printer.objects["k2_cfs"].fitted = True
        self.s = slots_mod.HarkCfsSlots(FakeConfig(self.printer, {
            "store_path": self.store,
            "creality_dir": os.path.join(self.tmp.name, "none")}))

    def gc(self, line):
        return self.printer.gcode.run(line)


class PaletteTest(SlotsBase):
    def test_shop_colours_by_name(self):
        self.gc("CFS_SLOT_SET SLOT=A FILAMENT=lib-elegoo-pla-matte "
                "COLOR=1F2A44")
        self.assertEqual(self.s.slots["A"]["color"], "1F2A44")
        self.assertEqual(slots_mod._colour_name("1F2A44"), "Matte Navy")
        self.assertEqual(slots_mod._norm_colour("silk gold"), "D4A62A")
        groups = {c["group"] for c in slots_mod.PALETTE}
        self.assertEqual(groups, {"Solid", "Matte", "Silk", "Multi",
                                  "Carbon fibre"})
        status = self.s.get_status(0)
        self.assertEqual(status["colours"], slots_mod.PALETTE)

    def test_basic_names_keep_their_colours(self):
        self.assertEqual(slots_mod._norm_colour("Black"), "000000")
        self.assertEqual(slots_mod._norm_colour("Navy"), "1A237E")


class TunedTest(SlotsBase):
    def test_calibration_comes_back_with_the_filament(self):
        self.gc("CFS_SLOT_SET SLOT=A FILAMENT=lib-elegoo-pla-matte "
                "COLOR=1F2A44")
        self.assertEqual(self.s.slots["A"]["pressure_advance"], 0)
        self.gc("CFS_SLOT_SET SLOT=A PA=0.044 FLOW=0.96 RETRACT=0.4")
        # loaded again later, into another slot, same colour
        self.gc("CFS_SLOT_SET SLOT=C FILAMENT=lib-elegoo-pla-matte "
                "COLOR=1F2A44")
        rec = self.s.slots["C"]
        self.assertEqual(rec["pressure_advance"], 0.044)
        self.assertEqual(rec["flow_ratio"], 0.96)
        self.assertEqual(rec["retract_length"], 0.4)
        # kept over a restart
        self.make()
        self.gc("CFS_SLOT_SET SLOT=D FILAMENT=lib-elegoo-pla-matte "
                "COLOR=F2F2EF")
        self.assertEqual(self.s.slots["D"]["pressure_advance"], 0.044)

    def test_colour_specific_values_win(self):
        self.gc("CFS_SLOT_SET SLOT=A FILAMENT=lib-elegoo-pla-matte "
                "COLOR=1F2A44 PA=0.05")
        self.gc("CFS_SLOT_SET SLOT=B FILAMENT=lib-elegoo-pla-matte "
                "COLOR=F2F2EF PA=0.04")
        self.gc("CFS_SLOT_SET SLOT=C FILAMENT=lib-elegoo-pla-matte "
                "COLOR=1F2A44")
        self.assertEqual(self.s.slots["C"]["pressure_advance"], 0.05)
        # an untested colour: the filament's latest
        self.gc("CFS_SLOT_SET SLOT=D FILAMENT=lib-elegoo-pla-matte "
                "COLOR=000000")
        self.assertEqual(self.s.slots["D"]["pressure_advance"], 0.04)

    def test_zero_and_remember_0_are_not_kept(self):
        self.gc("CFS_SLOT_SET SLOT=A FILAMENT=lib-elegoo-pla-matte PA=0")
        self.gc("CFS_SLOT_SET SLOT=A PA=0.06 REMEMBER=0")
        self.assertEqual(self.s.tuned, {})


class BrandPromptTest(SlotsBase):
    def test_many_plas_ask_for_the_brand_first(self):
        self.gc("CFS_SLOT_EDIT SLOT=A TYPE=PLA")
        self.printer.reactor.run_timers(2.)
        self.printer.reactor.run_callbacks()
        text = "\n".join(self.printer.gcode.out)
        self.assertIn("which brand?", text)
        self.assertIn("BRAND=", text)
        self.printer.gcode.out.clear()
        self.gc('CFS_SLOT_EDIT SLOT=A TYPE=PLA BRAND=Elegoo')
        self.printer.reactor.run_timers(2.)
        self.printer.reactor.run_callbacks()
        text = "\n".join(self.printer.gcode.out)
        self.assertIn("Elegoo PLA Matte", text)
        self.assertNotIn("Bambu", text)


class CalibTest(unittest.TestCase):
    REC = {"type": "PLA", "nozzle_temp": 215, "nozzle_first": 220,
           "bed_temp": 55, "flow_ratio": 0.98, "retract_length": 0.6,
           "min_temp": 190, "max_temp": 240, "fan_max": 100,
           "pressure_advance": 0.042, "loaded": True}

    def test_every_test_print_opts_out_of_the_override(self):
        for lines in (hc.flow_pads("A", self.REC, 0.98, 0.02, "hi")[0],
                      hc.retraction_test("A", self.REC, 0.2, 0.2,
                                         style="hi")[0],
                      hc.pa_pattern("A", self.REC, [0.03, 0.04], 0.035,
                                    "hi"),
                      hc.temp_tower("A", self.REC, [230, 225], "hi")):
            i = lines.index("FILAMENT_OVERRIDE PRINT=0")
            start = [n for n, l in enumerate(lines)
                     if l.startswith("START_PRINT")][0]
            self.assertLess(i, start)

    def test_pa_pattern_releases_the_hold_and_sets_each_value(self):
        lines = hc.pa_pattern("A", self.REC, [0.03, 0.036, 0.042], 0.036,
                              "hi")
        start = [n for n, l in enumerate(lines)
                 if l.startswith("START_PRINT")][0]
        hold = lines.index("PA_HOLD_MEASURED")
        first_pa = [n for n, l in enumerate(lines)
                    if l.startswith("SET_PRESSURE_ADVANCE")][0]
        self.assertTrue(start < hold < first_pa)
        for v in ("0.0300", "0.0360", "0.0420"):
            self.assertIn("SET_PRESSURE_ADVANCE ADVANCE=%s" % v, lines)

    def test_temp_tower_steps_down_and_waits_off_the_part(self):
        lines = hc.temp_tower("A", self.REC, [230, 225, 220], "hi")
        self.assertIn("START_PRINT EXTRUDER_TEMP=230", " ".join(
            l for l in lines if l.startswith("START_PRINT")))
        sets = [l for l in lines if l.startswith("M104 S")]
        self.assertEqual(sets[1:], ["M104 S225", "M104 S220"])
        self.assertEqual(len([l for l in lines
                              if l.startswith("TEMPERATURE_WAIT")]), 2)
        layers = [l for l in lines if l.startswith(";LAYER:")]
        self.assertEqual(len(layers), 3 * 30)

    def test_pick_labels(self):
        self.assertEqual(hc.HarkCalib._label("pa", 0.042), "42")
        self.assertEqual(hc.HarkCalib._label("temp", 225.), "225 C")
        self.assertEqual(hc.HarkCalib._fmt("pa", 0.0423), "0.0423")


class CalibSetTest(SlotsBase):
    def test_temperature_pick_keeps_the_first_layer_lift(self):
        self.gc("CFS_SLOT_SET SLOT=A FILAMENT=lib-elegoo-pla-matte "
                "NOZZLE=220 NOZZLE_FIRST=225")
        cfg = FakeConfig(self.printer, {})
        cfg.getchoice = lambda key, choices, default: default
        calib = hc.HarkCalib(cfg)
        self.printer.objects["hark_cfs_slots"] = self.s
        self.printer.gcode.run_script_from_command = self.gc
        calib.cmd_CALIB_SET(gcode_cmd(self.printer.gcode, {
            "KIND": "temp", "SLOT": "A", "VALUE": "210"}))
        self.assertEqual(self.s.slots["A"]["nozzle_temp"], 210)
        self.assertEqual(self.s.slots["A"]["nozzle_first"], 215)


def gcode_cmd(gcode, params):
    from test_cfs_tool_map import gcode_mod
    return gcode_mod.GCodeCommand(gcode, "X", "X", params, False)


class PanelTest(unittest.TestCase):
    FILS = [
        {"id": "a", "name": "Elegoo PLA Matte", "type": "PLA",
         "brand": "Elegoo", "finish": "Matte", "source": "library"},
        {"id": "b", "name": "CR-Silk", "type": "PLA", "brand": "Creality",
         "source": "creality"},
        {"id": "c", "name": "Hyper PLA", "type": "PLA", "brand": "Creality",
         "source": "creality"},
        {"id": "d", "name": "Bambu PETG HF", "type": "PETG",
         "brand": "Bambu Lab", "source": "library"},
        {"id": "e", "name": "Generic PP", "type": "PP", "brand": "Generic",
         "source": "library"},
    ]

    def test_types_show_finishes_after_their_type(self):
        self.assertEqual(panel.filament_types(self.FILS),
                         ["PLA", "PLA Matte", "PLA Silk", "PETG", "PP"])
        self.assertEqual([f["name"] for f in panel.filaments_of_type(
            self.FILS, "PLA Matte")], ["Elegoo PLA Matte"])

    def test_brands(self):
        self.assertEqual(panel.brands_of(self.FILS)[0], ("Creality", 2))

    def test_tuned_values_fill_the_draft(self):
        draft = panel.draft_from_filament(self.FILS[0], "1F2A44")
        tuned = {"a": {"*": {"pressure_advance": 0.04},
                       "1F2A44": {"pressure_advance": 0.05,
                                  "retract_length": 0.4}}}
        panel.apply_tuned(draft, tuned, "1F2A44")
        self.assertEqual(draft["pressure_advance"], 0.05)
        self.assertEqual(draft["retract_length"], 0.4)

    def test_save_sends_retraction(self):
        draft = panel.draft_from_filament(self.FILS[0], "1F2A44")
        draft.update(retract_length=0.4, nozzle_temp=215)
        script = panel.slot_set_script("A", draft)
        self.assertIn("RETRACT=0.400", script)
        self.assertIn("FILAMENT=a", script)

    def test_calibrations_listed(self):
        self.assertEqual([c[2] for c in panel.CALIBRATIONS],
                         ["CALIBRATE_PA", "CALIBRATE_FLOW",
                          "CALIBRATE_RETRACTION", "CALIBRATE_TEMP"])


if __name__ == "__main__":
    unittest.main()
