"""Hi START_PRINT slower first layer per material (19 Nov 2026: PETG
dragged into wavy lines at the Fast process's first-layer speeds), ended by
the SET_PRINT_STATS_INFO wrapper when the file reports layer 2."""
import pathlib
import textwrap
import unittest

try:
    import jinja2
except ImportError:  # the Mac has no jinja2; the host's klippy-env does
    jinja2 = None

CFG = (pathlib.Path(__file__).parents[1] / "config"
       / "mainline_stage2_hi.cfg").read_text()


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


def gcode(sec):
    # full-line '#' comments are dropped by Klipper's config parser
    body = sec[sec.index("gcode:\n") + 7:]
    return textwrap.dedent("\n".join(
        l for l in body.splitlines() if not l.lstrip().startswith("#")))


def render(src, **ctx):
    env = jinja2.Environment(
        block_start_string='{%', block_end_string='%}',
        variable_start_string='{', variable_end_string='}')
    out = env.from_string(src).render(**ctx)
    return [l.strip() for l in out.splitlines() if l.strip()]


class TextTest(unittest.TestCase):
    def test_default_and_wiring(self):
        sp = section("gcode_macro START_PRINT")
        self.assertIn("variable_first_layer_speed: {'PETG': 50}", sp)
        # set after the prime, so the purge line runs at its own speed
        self.assertLess(sp.index("_HI_PRIME_LINE VOLUMETRIC"),
                        sp.index("M220 S{fl.pct}"))
        # a stale flag from a cancelled print is cleared at start and end
        clear = ("SET_GCODE_VARIABLE MACRO=SET_PRINT_STATS_INFO "
                 "VARIABLE=first_layer_speed VALUE=0")
        self.assertIn(clear, sp)
        self.assertIn(clear, section("gcode_macro END_PRINT"))
        w = section("gcode_macro SET_PRINT_STATS_INFO")
        self.assertIn("rename_existing: SET_PRINT_STATS_INFO_BASE", w)
        self.assertIn("SET_PRINT_STATS_INFO_BASE {rawparams}", w)


@unittest.skipIf(jinja2 is None, "jinja2 not installed")
class RenderTest(unittest.TestCase):
    def start_snippet(self):
        sp = gcode(section("gcode_macro START_PRINT"))
        a = sp.index("{% set fl = namespace(pct=100) %}")
        b = sp.index('RESPOND MSG="Print start done"')
        return sp[a:b]

    def start(self, **params):
        sp = {"first_layer_speed": {'PETG': 50}}
        return render(self.start_snippet(), params=params, sp=sp)

    def test_petg_slowed(self):
        out = self.start(MATERIAL="PETG", TOTAL_LAYER="50")
        self.assertIn("M220 S50", out)
        self.assertIn("SET_GCODE_VARIABLE MACRO=SET_PRINT_STATS_INFO "
                      "VARIABLE=first_layer_speed VALUE=50", out)

    def test_petg_variant_prefix(self):
        self.assertIn("M220 S50",
                      self.start(MATERIAL="petg-cf", TOTAL_LAYER="9"))

    def test_pla_untouched(self):
        out = self.start(MATERIAL="PLA", TOTAL_LAYER="50")
        self.assertFalse([l for l in out if l.startswith("M220")])

    def test_old_start_without_layers_untouched(self):
        # no TOTAL_LAYER = older file that may never report layer 2
        out = self.start(MATERIAL="PETG")
        self.assertFalse([l for l in out if l.startswith("M220")])

    def test_param_overrides(self):
        self.assertIn("M220 S70", self.start(
            MATERIAL="PLA", TOTAL_LAYER="5", FIRST_LAYER_SPEED="70"))
        out = self.start(MATERIAL="PETG", TOTAL_LAYER="5",
                         FIRST_LAYER_SPEED="100")
        self.assertFalse([l for l in out if l.startswith("M220")])

    def wrapper(self, fls, factor, rawparams, **params):
        src = gcode(section("gcode_macro SET_PRINT_STATS_INFO"))
        printer = {
            "gcode_macro SET_PRINT_STATS_INFO": {"first_layer_speed": fls},
            "gcode_move": {"speed_factor": factor}}
        return render(src, printer=printer, params=params,
                      rawparams=rawparams)

    def test_layer1_passes_through_only(self):
        out = self.wrapper(50, 0.5, "CURRENT_LAYER=1", CURRENT_LAYER="1")
        self.assertEqual(out, ["SET_PRINT_STATS_INFO_BASE CURRENT_LAYER=1"])

    def test_layer2_restores(self):
        out = self.wrapper(50, 0.5, "CURRENT_LAYER=2", CURRENT_LAYER="2")
        self.assertEqual(out[0], "SET_PRINT_STATS_INFO_BASE CURRENT_LAYER=2")
        self.assertIn("M220 S100", out)
        self.assertIn("SET_GCODE_VARIABLE MACRO=SET_PRINT_STATS_INFO "
                      "VARIABLE=first_layer_speed VALUE=0", out)

    def test_layer2_keeps_slider_change(self):
        out = self.wrapper(50, 0.8, "CURRENT_LAYER=2", CURRENT_LAYER="2")
        self.assertNotIn("M220 S100", out)
        self.assertTrue(any("speed left at 80%" in l for l in out))

    def test_inactive_never_touches_speed(self):
        out = self.wrapper(0, 0.5, "CURRENT_LAYER=7", CURRENT_LAYER="7")
        self.assertEqual(out, ["SET_PRINT_STATS_INFO_BASE CURRENT_LAYER=7"])
        out = self.wrapper(0, 1.0, "TOTAL_LAYER=50", TOTAL_LAYER="50")
        self.assertEqual(out, ["SET_PRINT_STATS_INFO_BASE TOTAL_LAYER=50"])


if __name__ == "__main__":
    unittest.main()
