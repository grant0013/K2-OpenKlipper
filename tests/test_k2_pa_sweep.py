"""The Creality Hi's strain-gauge PA sweep ported to the K2 (PA_SWEEP_CAL,
26 Nov 2026): a scaled copy that keeps the Hi's extruder timing while the
head stays between the bucket lever (purge_x) and the wiper pad."""
import configparser
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).parents[1]
CFG = (ROOT / "config" / "mainline_stage2.cfg").read_text()
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "klippy"))

try:
    import jinja2
except ImportError:
    jinja2 = None


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


def macro_vars(cp, name):
    import ast
    sec = "gcode_macro " + name
    return {k[9:]: ast.literal_eval(cp.get(sec, k)) for k in cp.options(sec)
            if k.startswith("variable_")}


class Raised(Exception):
    pass


def raise_error(msg):
    raise Raised(msg)


class WiringTest(unittest.TestCase):
    def test_auto_stays_on_the_step_test_until_checked(self):
        auto = section("gcode_macro _PA_AUTO")
        self.assertIn("variable_method: 'step'", auto)
        self.assertIn("RUN=_PA_STATIC_TEST FINALLY=_PA_STATIC_END "
                      "ON_ERROR=report SLOT={slot} SAVE=1", auto)
        self.assertIn("RUN=_PA_BLEND_TEST FINALLY=_PA_STATIC_END "
                      "ON_ERROR=report", auto)

    def test_manual_sweep_cleans_up_then_reads_the_result(self):
        cal = section("gcode_macro PA_SWEEP_CAL")
        run = cal.index("RUN_SAFE RUN=_PA_SWEEP_RUN FINALLY=_PA_STATIC_DONE")
        self.assertLess(cal.index("CFS_PURGE_POSITION CONFIRM=1"), run)
        self.assertLess(cal.index("M109 S{temp}"), run)
        # the result comes after _PA_STATIC_DONE's CFS_SLOT_APPLY, so SET=1
        # is not undone by the slot's saved PA
        self.assertLess(run, cal.index("_PA_SWEEP_RESULT SLOT="))

    def test_gauge_log_holds_high_and_uses_the_k2_name(self):
        sw = section("gcode_macro _PA_SWEEP")
        self.assertIn("PA_LOG_START SRC=", sw)
        self.assertIn("HIGH_HOLD=1", sw)
        self.assertIn("PA_LOG_STOP NAME=k2_pa_sweep", sw)
        res = section("gcode_macro _PA_SWEEP_RESULT")
        self.assertIn("PA_SWEEP_COMPUTE NAME=k2_pa_sweep", res)
        self.assertIn("STROKE={sw.used_stroke}", res)
        self.assertIn("FAST={sw.used_fast}", res)

    def test_macro_names_klipper_accepts(self):
        # gcode.py register_command: a name that is not a letter plus a
        # number must not have a digit second ('K2_PA_CAL' failed to load
        # in the 26 Nov dry run)
        for name in re.findall(r"^\[gcode_macro (\S+)\]", CFG, re.M):
            try:
                float(name[1:])
                continue                       # M106, T0: traditional
            except ValueError:
                pass
            self.assertFalse(name[1:2].isdigit() or name[0].isdigit(),
                             name)

    def test_no_jinja_minus_trap(self):
        # '{-x}' is Jinja whitespace control, not a minus
        for name in ("PA_SWEEP_CAL", "_PA_SWEEP", "_PA_SWEEP_TEST"):
            self.assertNotRegex(section("gcode_macro " + name), r"\{-")

    def test_blend_sweep_centres_on_the_saved_pa(self):
        sw = section("gcode_macro _PA_SWEEP_TEST")
        self.assertLess(sw.index("CFS_FEED_START SLOT={slot} CONFIRM=1"),
                        sw.index("_PA_SWEEP START="))
        self.assertIn("rec.pressure_advance", sw)
        self.assertIn("centre - step * (count - 1) / 2", sw)
        self.assertLess(sw.index("_PA_SWEEP_RESULT"),
                        sw.index("VARIABLE=sweep_ok VALUE=1"))


def run_moves(text):
    """Follow G90/G91 G1 X/E/F through rendered G-code: [(x0, x1, e, f)]."""
    absolute, x, f, out = True, 0., 0., []
    for line in text.splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "G90":
            absolute = True
        elif w[0] == "G91":
            absolute = False
        elif w[0] == "G1":
            args = {a[0]: float(a[1:]) for a in w[1:]}
            f = args.get("F", f)
            if "X" in args:
                nx = args["X"] if absolute else x + args["X"]
                out.append((x, nx, args.get("E", 0.), f))
                x = nx
            elif "E" in args:
                out.append((x, x, args["E"], f))
    return out


@unittest.skipIf(jinja2 is None, "needs jinja2 (the host's klippy-env)")
class SweepRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cp = configparser.RawConfigParser(strict=False)
        cls.cp.read_string(CFG)
        env = jinja2.Environment("{%", "%}", "{", "}")
        cls.sweep = env.from_string(
            cls.cp.get("gcode_macro _PA_SWEEP", "gcode"))
        cls.sv = macro_vars(cls.cp, "_PA_SWEEP")

    def render(self, purge_x=110., clean_left_x=127., count=31):
        printer = {
            "gcode_macro _PA_SWEEP": self.sv,
            "configfile": {"settings": {"k2_cfs": {
                "purge_x": purge_x, "purge_y": 291.5,
                "clean_left_x": clean_left_x}}},
            "extruder": {"pressure_advance": 0.05},
            "toolhead": {"max_accel": 20000, "minimum_cruise_ratio": 0.,
                         "square_corner_velocity": 10},
        }
        return self.sweep.render(
            printer=printer, action_raise_error=raise_error,
            params={"START": "0.010", "STEP": "0.002", "COUNT": str(count)})

    def test_head_stays_between_lever_and_wiper(self):
        moves = run_moves(self.render())
        xs = [m[1] for m in moves] + [m[0] for m in moves[1:]]
        self.assertGreaterEqual(min(xs), 110. - 1e-6)
        self.assertLessEqual(max(xs), 127. - 3. + 1e-6)
        self.assertAlmostEqual(moves[-1][1], 110.)      # back on the lever

    def test_extruder_timing_is_the_his(self):
        text = self.render()
        sweep = [m for m in run_moves(text) if m[0] != m[1] and m[2]]
        self.assertEqual(len(sweep), 31 * 3)
        fast = [m for m in sweep if m[2] == 1.853]
        slow = [m for m in sweep if m[2] == 0.92643]
        self.assertEqual((len(fast), len(slow)), (31, 62))
        # filament speed (mm/min) = E / X distance x feed: the Hi's
        # 1.853 / 40 x 9000 and 0.92643 / 20 x 900
        for x0, x1, e, f in fast:
            self.assertAlmostEqual(e / abs(x1 - x0) * f,
                                   1.853 / 40 * 9000, places=2)
            self.assertAlmostEqual(abs(x1 - x0), 14.)
            self.assertLess(x1, x0)                    # fast stroke leftwards
        for x0, x1, e, f in slow:
            self.assertAlmostEqual(e / abs(x1 - x0) * f,
                                   0.92643 / 20 * 900, places=2)
        # accel and square corner scaled alike (time profile unchanged)
        s = 14. / 40.
        self.assertIn("ACCEL=%.1f MINIMUM_CRUISE_RATIO=0.75 "
                      "SQUARE_CORNER_VELOCITY=%.3f" % (2000 * s, 8 * s), text)
        self.assertIn("SET_VELOCITY_LIMIT ACCEL=20000 MINIMUM_CRUISE_RATIO=0.0"
                      " SQUARE_CORNER_VELOCITY=10", text)
        self.assertIn("SET_PRESSURE_ADVANCE ADVANCE=0.05\n", text)
        pas = re.findall(r"SET_PRESSURE_ADVANCE ADVANCE=([0-9.]+)", text)
        self.assertEqual(pas[0], "0.01000")
        self.assertEqual(pas[30], "0.07000")
        self.assertIn("VARIABLE=used_stroke VALUE=14.000", text)
        self.assertIn("VARIABLE=used_fast VALUE=3150.00", text)

    def test_stock_purge_x_gets_a_shorter_stroke(self):
        moves = run_moves(self.render(purge_x=115.))
        xs = [m[1] for m in moves]
        self.assertGreaterEqual(min(xs), 115. - 1e-6)
        self.assertLessEqual(max(xs), 124. + 1e-6)

    def test_too_little_room_refuses(self):
        with self.assertRaises(Raised):
            self.render(purge_x=120.)


def k2_sweep(zero_pa=0.050, rounds=31, start=0.010, step=0.002,
             loading=4, glitch_rounds=()):
    """A synthetic K2 sweep: per round a slow stretch, a fast stroke whose
    force steps up and settles flat within ~0.1 s (as the K2's does), then
    a tail after the stroke proportional to (zero_pa - PA). The first
    `loading` rounds have no flow. Returns (samples, moves)."""
    samples, moves, t = [], [], 0.
    for r in range(rounds):
        pa = start + r * step
        flow = r >= loading
        for i in range(140):                                 # slow before
            samples.append((t + i * 0.01, 1000))
        t += 1.4
        moves.append((t, 0.0707, 0., 92.7))                  # accel
        moves.append((t + 0.0707, 0.2121, 6.551, 0.))        # cruise
        moves.append((t + 0.2828, 0.0707, 6.551, -92.7))     # decel
        e = t + 0.3536
        for i in range(int(0.3536 / 0.005)):
            x = i * 0.005
            f = 80000 * min(1., x / 0.1) if flow else 0
            samples.append((t + x, 1000 + int(f)))
        tail = 3. * (zero_pa - pa) * 80000 if flow else 0
        for i in range(150):                                 # slow after
            x = i * 0.01
            v = 1000 + (tail if x < 0.15 else 0)
            if r in glitch_rounds and 0.05 < x < 0.08:
                v += 400000                                  # gauge glitch
            samples.append((e + x, int(v)))
        t = e + 1.5
    return samples, moves


class TailTest(unittest.TestCase):
    """METHOD=tail, the K2's reading (26 Nov 2026)."""

    def run_tail(self, samples, moves, start=0.010, step=0.002):
        from extras import hi_pa_compute as h
        starts = h.fast_strokes(moves, 1.853 / (14. / 52.5))
        self.assertEqual(len(starts), 31)
        ends = h.stroke_ends(moves, starts)
        self.assertAlmostEqual(ends[0] - starts[0], 0.3535, places=3)
        return h.tail_result(h.tail_ratios(sorted(samples), starts, ends),
                             start, step)

    def test_zero_crossing_is_the_pa(self):
        r = self.run_tail(*k2_sweep(0.050))
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["pa"], 0.050, places=3)

    def test_loading_rounds_and_glitches_are_left_out(self):
        r = self.run_tail(*k2_sweep(0.042, loading=8,
                                    glitch_rounds=(12, 20)))
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["pa"], 0.042, places=3)
        self.assertLessEqual(r["points"], 23)

    def test_crossing_past_either_end(self):
        self.assertEqual(self.run_tail(*k2_sweep(0.090))["status"], "high")
        self.assertEqual(self.run_tail(*k2_sweep(0.004))["status"], "low")

    def test_the_knee_reads_this_sweep_as_low(self):
        # why the K2 needs the tail: a flat plateau peaks anywhere
        from extras import hi_pa_compute as h
        samples, moves = k2_sweep(0.050)
        starts = h.fast_strokes(moves, 1.853 / (14. / 52.5))
        pos = h.peak_positions(sorted(samples), starts, 14. / 52.5)
        self.assertNotEqual(h.knee_result(pos, 0.010, 0.002)["status"], "ok")

    def test_k2_macros_use_the_tail(self):
        self.assertIn('METHOD={params.METHOD|default("tail")}',
                      section("gcode_macro PA_SWEEP_CAL"))
        self.assertIn("METHOD=tail", section("gcode_macro _PA_SWEEP_TEST"))
        self.assertIn('METHOD={params.METHOD|default("tail")}',
                      section("gcode_macro _PA_SWEEP_RESULT"))


class KneeOnScaledStrokeTest(unittest.TestCase):
    """The K2's 14 mm stroke at F3150 lasts as long as the Hi's 40 mm at
    F9000, so the same capture reads the same knee once STROKE is given."""

    def test_same_knee_with_k2_stroke(self):
        import test_hi_pa_compute as t
        p = t.KneeCommandTest("test_knee_is_the_default").make(20)
        g = t.FakeGcmd(NAME="k2_pa_sweep", MATERIAL="PETG", FAST=3150,
                       STROKE=14)
        p.cmd_HI_PA_COMPUTE(g)
        self.assertAlmostEqual(p.last["pa"], 0.050)
        self.assertTrue(g.replies[-1].startswith("PA sweep k2_pa_sweep"))

    def test_without_stroke_the_fast_strokes_are_missed(self):
        import test_hi_pa_compute as t
        p = t.KneeCommandTest("test_knee_is_the_default").make(20)
        g = t.FakeGcmd(NAME="k2_pa_sweep", FAST=3150, QUIET=1)
        p.cmd_HI_PA_COMPUTE(g)
        self.assertIsNone(p.last["pa"])


if __name__ == "__main__":
    unittest.main()
