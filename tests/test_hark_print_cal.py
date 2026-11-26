"""PRINT_CAL (hark_print_cal): saved print-start switches, with the PA
method and the one-shot PA_NEXT added 26 Nov 2026."""
import json
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))

from extras import hark_print_cal as hpc  # noqa: E402

CFG = (ROOT / "config" / "mainline_stage2.cfg").read_text()


class Err(Exception):
    pass


class Gcmd:
    def __init__(self, **p):
        self.p, self.replies = p, []

    def get_int(self, name, default=None, **kw):
        return int(self.p[name]) if name in self.p else default

    def get(self, name, default=None):
        return self.p.get(name, default)

    def respond_info(self, msg):
        self.replies.append(msg)

    def error(self, msg):
        return Err(msg)


def make(tmp, saved=None, method=True):
    macro = types.SimpleNamespace(variables={
        "on_load": 0, "on_print_start": 1, "measured": []})
    if method:
        macro.variables["method"] = "step"
    path = pathlib.Path(tmp) / "hark_print_cal.json"
    if saved is not None:
        path.write_text(json.dumps(saved))
    p = hpc.HarkPrintCal.__new__(hpc.HarkPrintCal)
    p.printer = types.SimpleNamespace(
        lookup_object=lambda name, default=None:
        macro if name == "gcode_macro _PA_AUTO" else default)
    p.path = str(path)
    p.saved = p._load()
    return p, macro, path


class PrintCalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_method_is_saved_and_applied(self):
        p, macro, path = make(self.tmp.name)
        g = Gcmd(PA_METHOD="Sweep")
        p.cmd_PRINT_CAL(g)
        self.assertEqual(macro.variables["method"], "sweep")
        self.assertEqual(json.loads(path.read_text())["pa_method"], "sweep")
        self.assertIn("method sweep", g.replies[-1])
        # a restart: loaded from the file and applied at ready
        p2, macro2, _ = make(self.tmp.name)
        p2._ready()
        self.assertEqual(macro2.variables["method"], "sweep")
        self.assertEqual(p2.get_status(0)["pa_method"], "sweep")

    def test_bad_method_and_old_firmware_refused(self):
        p, macro, _ = make(self.tmp.name)
        with self.assertRaises(Err):
            p.cmd_PRINT_CAL(Gcmd(PA_METHOD="knee"))
        p, macro, _ = make(self.tmp.name, method=False)
        with self.assertRaises(Err):
            p.cmd_PRINT_CAL(Gcmd(PA_METHOD="blend"))
        self.assertIsNone(p.get_status(0)["pa_method"])

    def test_next_print_is_saved_and_cleared(self):
        p, macro, path = make(self.tmp.name)
        self.assertEqual(p.get_status(0)["pa_next"], 0)
        p.cmd_PRINT_CAL(Gcmd(PA_NEXT=1))
        self.assertEqual(p.get_status(0)["pa_next"], 1)
        self.assertEqual(json.loads(path.read_text())["pa_next"], 1)
        p.cmd_PRINT_CAL(Gcmd(PA_NEXT=0))
        self.assertEqual(p.get_status(0)["pa_next"], 0)

    def test_old_file_without_new_keys(self):
        p, macro, _ = make(self.tmp.name, saved={
            "mesh": 1, "pa_on_load": 0, "pa_on_print_start": 0,
            "pa_method": "nonsense"})
        p._ready()
        self.assertEqual(macro.variables["method"], "step")
        self.assertEqual(macro.variables["on_print_start"], 0)


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


class StartPrintGateTest(unittest.TestCase):
    def test_next_print_runs_once_the_rear_spool_included(self):
        sp = section("gcode_macro START_PRINT")
        self.assertIn("printer.hark_print_cal.pa_next", sp)
        clear = sp.index("PRINT_CAL PA_NEXT=0")
        self.assertLess(clear, sp.index("_PA_AUTO SLOT={'E' if external "
                                        "else slot} KEEP_FEED=1"))
        self.assertIn("[hark_print_cal]", CFG)

    def test_sweep_result_feeds_the_screen_popup(self):
        sv = section("gcode_macro _PA_SWEEP_SAVE")
        self.assertIn("MACRO=_PA_STATIC_SAVE VARIABLE=last", sv)
        self.assertIn("'how':'sweep'", sv)
        self.assertIn("'reason':'not_saved'", sv)
        cal = section("gcode_macro PA_SWEEP_CAL")
        self.assertIn("QUIET=1", cal)


if __name__ == "__main__":
    unittest.main()
