"""homing_override: a manual G28 must not raise the bed into a finished
part (27 Nov 2026). Rendered with Klipper's template delimiters."""
import pathlib
import re
import unittest

import jinja2

CFG = pathlib.Path(__file__).parents[1] / "config" / "mainline_stage2.cfg"


class MacroError(Exception):
    pass


def raise_error(msg):
    raise MacroError(msg)


def homing(params, homed="xyz", state="complete", paused=False):
    text = CFG.read_text()
    m = re.search(r"^\[homing_override\]\n(.*?)(?=^\[)", text, re.S | re.M)
    body = m.group(1).split("gcode:\n", 1)[1]
    env = jinja2.Environment('{%', '%}', '{', '}')
    printer = {
        'toolhead': {'homed_axes': homed, 'position': {'z': 135.}},
        'print_stats': {'state': state},
        'pause_resume': {'is_paused': paused},
        'k2_motor_bus': {'motors_ready': True},
        'filament_switch_sensor filament_sensor': {'filament_detected': False},
        'gcode_macro START_PRINT': {'skip_home_wipe': 0},
    }
    out = env.from_string(body).render(params=params, printer=printer,
                                       action_raise_error=raise_error)
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.strip().startswith('#')]


def bed_moves(out):
    return [l for l in out if l.startswith(("G1 Z", "G28 Z", "PRTOUCH",
                                            "FORCE_MOVE"))]


def xy_homes(out):
    # G28 X / G28 Y, or through RUN_SAFE ... AXIS=X on newer configs
    axes = []
    for l in out:
        m = re.match(r"G28 ([XY])$", l) or re.search(r" AXIS=([XY])$", l)
        if m:
            axes.append(m.group(1))
    return axes


class KeepZTest(unittest.TestCase):
    def test_home_after_a_print_keeps_z(self):
        out = homing({})
        self.assertEqual(bed_moves(out), [])
        self.assertEqual(xy_homes(out), ["Y", "X"])

    def test_z_only_after_a_print_does_nothing(self):
        out = homing({'Z': ''}, state="cancelled")
        self.assertEqual(bed_moves(out), [])
        self.assertEqual(xy_homes(out), [])

    def test_force_measures_z_again(self):
        out = homing({'Z': '', 'FORCE': '1'}, state="standby")
        self.assertIn("PRTOUCH_HOME_Z PROBE_SPEED=1", out)

    def test_start_print_still_taps(self):
        out = homing({'Z': ''}, state="printing")
        self.assertIn("PRTOUCH_HOME_Z PROBE_SPEED=1", out)

    def test_never_z_while_paused(self):
        with self.assertRaisesRegex(MacroError, "paused"):
            homing({}, state="paused", paused=True)
        self.assertEqual(xy_homes(homing({'X': '', 'Y': ''}, state="paused",
                                         paused=True)), ["Y", "X"])

    def test_unhomed_z_is_measured(self):
        out = homing({}, homed="", state="standby")
        self.assertIn("G28 Z", out)
        self.assertIn("PRTOUCH_HOME_Z PROBE_SPEED=1", out)


if __name__ == "__main__":
    unittest.main()
