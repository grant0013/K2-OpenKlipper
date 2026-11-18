"""The K2's printed flow-ratio pads and retraction test on the Hi
(hark_calib start_style hi, the slot's own fan), the Hi screen's Calibrate
page and pick, and pa_pattern's TOOL= on the Hi."""
import importlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
CFG = (ROOT / "config" / "mainline_stage2_hi.cfg").read_text()
hc = importlib.import_module("extras.hark_calib")

PETG = {"type": "PETG", "nozzle_temp": 250, "bed_temp": 80,
        "flow_ratio": 0.97, "retract_length": 1.5, "retract_speed": 0,
        "fan_max": 80, "loaded": True}


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


def starts(lines):
    return [l for l in lines if l.startswith("START_PRINT")]


class HiConfigTest(unittest.TestCase):
    def test_hark_calib_is_loaded_with_the_hi_start_and_slot_fan(self):
        calib = section("hark_calib")
        self.assertIn("start_style: hi", calib)
        self.assertIn("fan: slot", calib)
        self.assertIn("printer_name: Hi OpenKlipper", calib)


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get_printer(self):
        gcode = type("G", (), {"register_command":
                               lambda *a, **k: None})()
        return type("P", (), {"lookup_object": lambda s, n: gcode})()

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getchoice(self, name, choices, default=None):
        return choices[self.values.get(name, default)]


class OptionsTest(unittest.TestCase):
    def test_defaults_are_the_k2s(self):
        c = hc.HarkCalib(FakeConfig({}))
        self.assertEqual((c.style, c.fan, c.name),
                         ("k2", "full", "K2 OpenKlipper"))

    def test_hi_options(self):
        c = hc.HarkCalib(FakeConfig({"start_style": "hi", "fan": "slot",
                                     "printer_name": "Hi OpenKlipper"}))
        self.assertEqual((c.style, c.fan, c.name),
                         ("hi", "slot", "Hi OpenKlipper"))


class FlowPadsTest(unittest.TestCase):
    def test_hi_start_loads_the_slot_with_its_material(self):
        lines, ratios = hc.flow_pads("B", PETG, 0.97, 0.02, "hi", "slot",
                                     "Hi OpenKlipper")
        self.assertEqual(starts(lines), [
            "START_PRINT EXTRUDER_TEMP=250 BED_TEMP=80 TOOL=1 MATERIAL=PETG"
            " TOTAL_LAYER=7 VOLUMETRIC=12"])
        self.assertTrue(lines[0].startswith("; Hi OpenKlipper flow-ratio"))
        # the objects are defined before START_PRINT (KAMP purges by them)
        self.assertLess(
            max(i for i, l in enumerate(lines)
                if l.startswith("EXCLUDE_OBJECT_DEFINE")),
            lines.index(starts(lines)[0]))
        self.assertEqual(ratios[4], 0.97)
        self.assertEqual(len(ratios), 9)

    def test_slot_fan_like_a_print_of_that_filament(self):
        lines, _ = hc.flow_pads("B", PETG, 0.97, 0.02, "hi", "slot")
        self.assertIn("M106 S204", lines)        # 80% through the Hi's M106
        self.assertNotIn("M106 S255", lines)

    def test_k2_output_is_unchanged(self):
        lines, _ = hc.flow_pads("B", PETG, 0.97, 0.02)
        self.assertEqual(starts(lines), [
            "START_PRINT EXTRUDER_TEMP=250 BED_TEMP=80 TOOL=1"])
        self.assertIn("M106 S255", lines)
        self.assertTrue(lines[0].startswith("; K2 OpenKlipper flow-ratio"))


class RetractionTest(unittest.TestCase):
    def test_hi_start_counts_all_layers(self):
        lines, lengths = hc.retraction_test("C", PETG, 0.9, 0.2,
                                            style="hi", fan="slot")
        self.assertEqual(starts(lines), [
            "START_PRINT EXTRUDER_TEMP=250 BED_TEMP=80 TOOL=2 MATERIAL=PETG"
            " TOTAL_LAYER=60 VOLUMETRIC=12"])
        self.assertIn("M106 S204", lines)
        self.assertEqual(lengths[0], 0.9)


class FanValueTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(hc.fan_value(PETG), 255)
        self.assertEqual(hc.fan_value(PETG, "slot"), 204)
        self.assertEqual(hc.fan_value({}, "slot"), 255)
        self.assertEqual(hc.fan_value({"fan_max": 150}, "slot"), 255)
        self.assertEqual(hc.fan_value({"fan_max": "x"}, "slot"), 255)


try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "the screen needs Pillow")
class ScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "harkscreen"))
        cls.hs = importlib.import_module("harkscreen")
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = pathlib.Path(cls.tmp.name) / "harkscreen.cfg"
        cfg.write_text("[theme]\n")
        cls.hs.CFG_PATH = str(cfg)
        cls.hs.PREFS_FILE = str(pathlib.Path(cls.tmp.name) / "prefs.json")
        cls.hs.FB = str(pathlib.Path(cls.tmp.name) / "fb.bin")
        open(cls.hs.FB, "wb").close()
        hi = importlib.import_module("harkscreen_hi")
        cls.scr = hi.Screen(cls.hs.Config(str(cfg)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def state(self, **extra):
        st = {"webhooks": {"state": "ready"},
              "print_stats": {"state": "standby"},
              "k2_cfs": {"fitted": True, "online": True,
                         "active_slot": "B"},
              "hark_cfs_slots": {"slots": {"C": dict(PETG)}}}
        st.update(extra)
        return st

    def actions(self, st, sub, page="filament"):
        self.scr.page, self.scr.sub, self.scr.confirm = page, sub, None
        self.scr.frame(st, None)
        return [a for _, a in self.scr.buttons]

    def test_slot_page_opens_calibrate(self):
        self.assertIn(("sub", "calib:C"), self.actions(self.state(),
                                                        "slot:C"))

    def test_calibrate_page_runs_the_tests(self):
        runs = [a[2][1] for a in self.actions(self.state(), "calib:C")
                if a[0] == "confirm"]
        self.assertIn("CALIBRATE_FLOW SLOT=C", runs)
        self.assertIn("CALIBRATE_RETRACTION SLOT=C", runs)
        self.assertTrue(any("PA_CAL_STATIC SLOT=C SAVE=1" in r
                            for r in runs))

    def test_pick_saves_to_the_slot(self):
        st = self.state(hark_calib={"pending": {
            "kind": "flow", "slot": "C",
            "values": [0.93, 0.95, 0.97]}})
        acts = self.actions(st, None, "status")
        self.assertIn(("gcode", "CALIB_SET KIND=flow SLOT=C VALUE=0.95",
                       "Slot C saved"), acts)
        self.assertIn(("gcode", "CALIB_SKIP", "Kept the old value"), acts)

    def test_pick_waits_for_the_print_to_end(self):
        st = self.state(hark_calib={"pending": {
            "kind": "flow", "slot": "C", "values": [0.95]}},
            print_stats={"state": "printing"})
        acts = self.actions(st, None, "status")
        self.assertFalse(any("CALIB_SET" in str(a) for a in acts))


class PaPatternToolTest(unittest.TestCase):
    def start_line(self, *args):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "pa.gcode"
            subprocess.run([sys.executable,
                            str(ROOT / "tools" / "pa" / "pa_pattern.py"),
                            "--printer", "hi", "--start", "0.04", "-o",
                            str(out)] + list(args), check=True,
                           capture_output=True)
            return [l for l in out.read_text().splitlines()
                    if l.startswith("START_PRINT")][0]

    def test_hi_without_slot_keeps_the_tool_map(self):
        self.assertNotIn("TOOL=", self.start_line())

    def test_hi_slot_picks_the_tool(self):
        self.assertTrue(self.start_line("--slot", "B").endswith(" TOOL=1"))


if __name__ == "__main__":
    unittest.main()
