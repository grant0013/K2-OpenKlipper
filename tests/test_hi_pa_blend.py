"""The Hi's automatic PA blend (18 Nov 2026): our step test and Creality's
sweep read for its knee, averaged when they agree, the material's lead
when they do not."""
import ast
import configparser
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]
CFG_PATH = ROOT / "config" / "mainline_stage2_hi.cfg"
CFG = CFG_PATH.read_text()


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


class WiringTest(unittest.TestCase):
    def test_auto_runs_the_blend(self):
        auto = section("gcode_macro _PA_AUTO")
        self.assertIn("variable_method: 'blend'", auto)
        self.assertIn("RUN=_PA_BLEND_TEST FINALLY=_PA_STATIC_END "
                      "ON_ERROR=report", auto)
        self.assertIn('variable_lead: {"PETG": "sweep"}', auto)

    def test_step_first_then_sweep_then_save(self):
        test = section("gcode_macro _PA_BLEND_TEST")
        flags = test.index("VARIABLE=sweep_ok VALUE=0")
        step = test.index("RUN=_PA_STEP_RUN ON_ERROR=report")
        sweep = test.index("RUN=_PA_SWEEP_TEST ON_ERROR=report")
        self.assertLess(flags, step)
        self.assertLess(step, sweep)
        self.assertLess(sweep, test.index("_PA_BLEND_SAVE SLOT="))

    def test_flags_are_set_only_after_each_test(self):
        run = section("gcode_macro _PA_STEP_RUN")
        self.assertLess(run.index("_PA_STATIC_TEST"),
                        run.index("VARIABLE=step_ok VALUE=1"))
        sw = section("gcode_macro _PA_SWEEP_TEST")
        self.assertLess(sw.index("_HI_PA_CAL_RESULT"),
                        sw.index("VARIABLE=sweep_ok VALUE=1"))
        self.assertIn("METHOD=knee", sw)
        self.assertIn("QUIET=1", sw)

    def test_sweep_feeds_and_centres_on_the_saved_pa(self):
        sw = section("gcode_macro _PA_SWEEP_TEST")
        self.assertLess(sw.index("CFS_FEED_START SLOT={slot} CONFIRM=1"),
                        sw.index("_HI_PA_SWEEP START="))
        self.assertIn("rec.pressure_advance", sw)
        self.assertIn("centre - step * (count - 1) / 2", sw)

    def test_measure_pa_saving_uses_the_blend(self):
        cal = section("gcode_macro PA_CAL_STATIC")
        self.assertIn("RUN=_PA_BLEND_TEST FINALLY=_PA_STATIC_DONE", cal)
        self.assertIn("params.SAVE|default(0)|int and not params.METHOD",
                      cal)


try:
    import jinja2
except ImportError:
    jinja2 = None


@unittest.skipIf(jinja2 is None, "needs jinja2 (the Toughpad's klippy-env)")
class SaveTest(unittest.TestCase):
    """_PA_BLEND_SAVE rendered with Klipper's delimiters."""

    @classmethod
    def setUpClass(cls):
        cp = configparser.RawConfigParser(strict=False)
        cp.read_string(CFG)
        cls.template = jinja2.Environment(
            "{%", "%}", "{", "}").from_string(
                cp.get("gcode_macro _PA_BLEND_SAVE", "gcode"))
        cls.static = {k[9:]: ast.literal_eval(cp.get(
            "gcode_macro _PA_STATIC_SAVE", k)) for k in cp.options(
                "gcode_macro _PA_STATIC_SAVE") if k.startswith("variable_")}
        cls.auto = {k[9:]: ast.literal_eval(cp.get(
            "gcode_macro _PA_AUTO", k)) for k in cp.options(
                "gcode_macro _PA_AUTO") if k.startswith("variable_")}

    def render(self, step=None, sweep=None, material="PETG", step_ok=None,
               sweep_ok=None, spread=0.):
        pt = {}
        if step is not None:
            pt = {"last_pressure_advance": step,
                  "last_measured_pressure_advance": [step, step + spread]}
        printer = {
            "gcode_macro _PA_BLEND_SAVE": {
                "step_ok": int(step is not None if step_ok is None
                               else step_ok),
                "sweep_ok": int(sweep is not None if sweep_ok is None
                                else sweep_ok),
                "sweep_scale_material": {}},
            "gcode_macro _PA_STATIC_SAVE": dict(self.static,
                                                scale_material={
                                                    "PLA": 0.87,
                                                    "PETG": 1.0}),
            "gcode_macro _PA_AUTO": self.auto,
            "toolhead": {"estimated_print_time": 12.3},
            "hark_cfs_slots": {"slots": {"B": {"type": material}}},
            "pa_tester": pt,
            "hi_pa_compute": {"pa": sweep},
        }
        out = self.template.render(printer=printer,
                                   params={"SLOT": "B", "HOLD": "1"})
        return [l.strip() for l in out.splitlines() if l.strip()]

    def saved(self, lines):
        for l in lines:
            if l.startswith("CFS_SLOT_SET SLOT=B PA="):
                return float(l.split("PA=")[1])
        return None

    def test_agreeing_tests_are_averaged(self):
        lines = self.render(step=0.070, sweep=0.066)
        self.assertAlmostEqual(self.saved(lines), 0.068)
        self.assertIn("PA_HOLD_MEASURED SLOT=B", lines)
        self.assertTrue(any("mean" in l for l in lines))

    def test_petg_disagreement_takes_the_sweep(self):
        # 18 Nov: step 0.094 straight after a sweep, sweep knee 0.066
        self.assertAlmostEqual(self.saved(self.render(step=0.094,
                                                      sweep=0.066)), 0.066)

    def test_pla_disagreement_takes_the_scaled_step(self):
        # PLA: step 0.085 x 0.87 = 0.074 (the printed chevron)
        self.assertAlmostEqual(self.saved(self.render(
            step=0.085, sweep=0.048, material="PLA")), 0.074, places=3)

    def test_one_test_alone(self):
        self.assertAlmostEqual(self.saved(self.render(sweep=0.066)), 0.066)
        self.assertAlmostEqual(self.saved(self.render(step=0.070)), 0.070)

    def test_stale_results_are_not_used(self):
        # results there from an earlier run, but neither test finished now
        lines = self.render(step=0.070, sweep=0.066, step_ok=0, sweep_ok=0)
        self.assertIsNone(self.saved(lines))
        self.assertTrue(any("no usable result" in l for l in lines))

    def test_out_of_range_and_spread_are_dropped(self):
        self.assertAlmostEqual(self.saved(self.render(step=0.5,
                                                      sweep=0.066)), 0.066)
        self.assertAlmostEqual(self.saved(self.render(
            step=0.070, sweep=None, spread=0.2)) or 0, 0)


if __name__ == "__main__":
    unittest.main()
