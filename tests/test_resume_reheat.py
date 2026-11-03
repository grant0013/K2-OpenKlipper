import pathlib
import re
import unittest

import jinja2

CFG = pathlib.Path(__file__).parents[1] / "config" / "mainline_stage2.cfg"


def macro_gcode(name):
    text = CFG.read_text()
    m = re.search(r"^\[gcode_macro %s\]\n(.*?)(?=^\[)" % name, text,
                  re.S | re.M)
    body = m.group(1)
    lines = body.split("gcode:\n", 1)[1].splitlines()
    return "\n".join(l.strip() for l in lines if l.strip())


def render(name, printer):
    # Klipper's template environment: single-brace expressions
    env = jinja2.Environment('{%', '%}', '{', '}')
    out = env.from_string(macro_gcode(name)).render(printer=printer,
                                                    params={})
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.strip().startswith('#')]


def printer(nozzle_target, nozzle_temp, bed_target, bed_temp,
            saved_nozzle=250., saved_bed=70.):
    return {
        'extruder': {'target': nozzle_target, 'temperature': nozzle_temp},
        'heater_bed': {'target': bed_target, 'temperature': bed_temp},
        'gcode_macro PAUSE': {'extruder_target': saved_nozzle,
                              'bed_target': saved_bed},
        'toolhead': {'homed_axes': 'xyz', 'position': {'z': 10.},
                     'axis_maximum': {'z': 260.}},
    }


class ResumeReheatTest(unittest.TestCase):
    def test_hotend_off_after_the_idle_timeout_is_reheated_first(self):
        out = render("RESUME", printer(0., 35., 70., 70.))
        self.assertIn("M104 S250.0", out)
        self.assertIn("M109 S250.0", out)
        self.assertNotIn("M140 S70.0", out)
        self.assertNotIn("M190 S70.0", out)
        self.assertEqual(out[-1], "RESUME_BASE")
        self.assertLess(out.index("M109 S250.0"), out.index("RESUME_BASE"))

    def test_both_heaters_off_both_come_back(self):
        out = render("RESUME", printer(0., 30., 0., 30.))
        self.assertIn("M140 S70.0", out)
        self.assertIn("M104 S250.0", out)
        self.assertIn("M190 S70.0", out)
        self.assertIn("M109 S250.0", out)
        # bed is asked for before the nozzle, so both heat together
        self.assertLess(out.index("M140 S70.0"), out.index("M104 S250.0"))

    def test_still_hot_resumes_straight_away(self):
        out = render("RESUME", printer(250., 249., 70., 70.))
        self.assertEqual(out, ["RESUME_BASE"])

    def test_your_own_lower_target_is_kept(self):
        # the user set 230 while paused: no override, just a wait
        out = render("RESUME", printer(230., 200., 70., 70.))
        self.assertNotIn("M104 S250.0", out)
        self.assertIn("M109 S230.0", out)

    def test_nothing_saved_nothing_heated(self):
        out = render("RESUME", printer(0., 30., 0., 30., 0., 0.))
        self.assertEqual(out, ["RESUME_BASE"])

    def test_pause_saves_the_targets(self):
        out = render("PAUSE", printer(250., 250., 70., 70.))
        self.assertIn("SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=extruder_target VALUE=250.0", out)
        self.assertIn("SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=bed_target VALUE=70.0", out)
        self.assertEqual(out[2], "PAUSE_BASE")


if __name__ == "__main__":
    unittest.main()
