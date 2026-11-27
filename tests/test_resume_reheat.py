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


class MacroError(Exception):
    pass


def raise_error(msg):
    raise MacroError(msg)


def render(name, printer):
    # Klipper's template environment: single-brace expressions
    env = jinja2.Environment('{%', '%}', '{', '}')
    out = env.from_string(macro_gcode(name)).render(
        printer=printer, params={}, action_raise_error=raise_error)
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.strip().startswith('#')]


def printer(nozzle_target, nozzle_temp, bed_target, bed_temp,
            saved_nozzle=250., saved_bed=70., is_paused=True):
    return {
        'pause_resume': {'is_paused': is_paused},
        'extruder': {'target': nozzle_target, 'temperature': nozzle_temp},
        'heater_bed': {'target': bed_target, 'temperature': bed_temp},
        'gcode_macro PAUSE': {'extruder_target': saved_nozzle,
                              'bed_target': saved_bed},
        'toolhead': {'homed_axes': 'xyz', 'position': {'z': 10.},
                     'axis_maximum': {'z': 260.}},
        'gcode_move': {'gcode_position': {'x': 120., 'y': 80., 'z': 10.}},
    }


class ResumeReheatTest(unittest.TestCase):
    def test_hotend_off_after_the_idle_timeout_is_reheated_first(self):
        out = render("RESUME", printer(0., 35., 70., 70.))
        self.assertIn("M104 S250.0", out)
        self.assertIn("_HEAT_FAST S=250.0", out)
        self.assertNotIn("M140 S70.0", out)
        self.assertNotIn("M190 S70.0", out)
        self.assertEqual(out[-1], "_RESUME_GO")
        self.assertLess(out.index("_HEAT_FAST S=250.0"), out.index("_RESUME_GO"))

    def test_both_heaters_off_both_come_back(self):
        out = render("RESUME", printer(0., 30., 0., 30.))
        self.assertIn("M140 S70.0", out)
        self.assertIn("M104 S250.0", out)
        self.assertIn("M190 S70.0", out)
        self.assertIn("_HEAT_FAST S=250.0", out)
        # bed is asked for before the nozzle, so both heat together
        self.assertLess(out.index("M140 S70.0"), out.index("M104 S250.0"))

    def test_still_hot_resumes_straight_away(self):
        out = render("RESUME", printer(250., 249., 70., 70.))
        self.assertEqual(out, ["_RESUME_LOAD", "_RESUME_GO"])

    def test_your_own_lower_target_is_kept(self):
        # the user set 230 while paused: no override, just a wait
        out = render("RESUME", printer(230., 200., 70., 70.))
        self.assertNotIn("M104 S250.0", out)
        self.assertIn("_HEAT_FAST S=230.0", out)

    def test_nothing_saved_nothing_heated(self):
        out = render("RESUME", printer(0., 30., 0., 30., 0., 0.))
        self.assertEqual(out, ["_RESUME_LOAD", "_RESUME_GO"])

    def test_resume_with_nothing_paused_heats_nothing(self):
        out = render("RESUME", printer(0., 30., 0., 30., is_paused=False))
        self.assertEqual(out, ["_RESUME_LOAD", "_RESUME_GO"])

    def test_pausing_twice_does_not_lift_or_save_again(self):
        # the first pause's targets stay, and the head stays parked
        out = render("PAUSE", printer(0., 30., 70., 70.,
                                      is_paused=True))
        self.assertEqual(out, ["PAUSE_BASE"])

    def test_pause_saves_the_targets(self):
        p = printer(250., 250., 70., 70., is_paused=False)
        out = render("PAUSE", p)
        self.assertIn("SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=extruder_target VALUE=250.0", out)
        self.assertIn("SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=bed_target VALUE=70.0", out)
        # where the print was, for _RESUME_GO's way back over the part
        self.assertIn("SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=paused_z VALUE=10.0", out)
        self.assertLess(out.index("SET_GCODE_VARIABLE MACRO=PAUSE VARIABLE=paused_z VALUE=10.0"),
                        out.index("PAUSE_BASE"))


def paused(detected=False, enabled=True, state="paused", fitted=True,
           active=None, feed="B", last="B", change_pending=False,
           external=False, hand_load=0):
    return {
        'hark_cfs_slots': {'tool_map_external': external},
        'print_stats': {'state': state},
        'filament_switch_sensor filament_sensor': {
            'enabled': enabled, 'filament_detected': detected},
        'k2_cfs': {'fitted': fitted, 'active_slot': active, 'feed_slot': feed,
                   'last_loaded_slot': last,
                   'change_pending': change_pending},
        'gcode_macro _RUNOUT_REFILL': {'hand_load': hand_load},
    }


class PauseLatchTest(unittest.TestCase):
    def runout(self, state):
        return {
            'print_stats': {'state': state},
            'hark_cfs_slots': {'slots': {}, 'tool_map_external': False},
            'k2_cfs': {'fitted': True, 'online': True, 'active_slot': None,
                       'feed_slot': None, 'last_loaded_slot': None},
            'gcode_macro _RUNOUT_REFILL': {'enabled': 1},
        }

    def test_runout_with_no_print_clears_the_pause(self):
        for state in ("standby", "complete", "cancelled", "error"):
            self.assertIn("CLEAR_PAUSE",
                          render("_RUNOUT_REFILL", self.runout(state)))

    def test_runout_on_a_paused_print_keeps_it(self):
        self.assertNotIn("CLEAR_PAUSE",
                         render("_RUNOUT_REFILL", self.runout("paused")))

    def test_start_print_clears_a_latched_pause_first(self):
        first = [l for l in macro_gcode("START_PRINT").splitlines()
                 if not l.startswith('#')][0]
        self.assertEqual(first, "CLEAR_PAUSE")


class LoadTemperatureTest(unittest.TestCase):
    def temp(self, state, target, saved=250., rec=220):
        lines = macro_gcode("LOAD_FILAMENT").splitlines()
        wanted = [l for l in lines if l.startswith(
            ("{% set saved_t", "{% set temp ="))]
        self.assertEqual(len(wanted), 2)
        env = jinja2.Environment('{%', '%}', '{', '}')
        return float(env.from_string(
            "{% set target = t %}{% set rec_t = r %}" + "".join(wanted)
            + "{temp}").render(
                t=target, r=rec, params={}, printer={
                    'print_stats': {'state': state},
                    'gcode_macro PAUSE': {'extruder_target': saved}}))

    def test_after_the_idle_timeout_loads_at_the_prints_temperature(self):
        self.assertEqual(self.temp("paused", 0.), 250.)

    def test_a_target_already_set_is_used(self):
        self.assertEqual(self.temp("paused", 240.), 240.)

    def test_outside_a_pause_uses_the_slot_record(self):
        self.assertEqual(self.temp("standby", 0.), 220.)


class ResumeLoadTest(unittest.TestCase):
    def test_empty_toolhead_loads_the_slot_before_resuming(self):
        out = render("_RESUME_LOAD", paused())
        self.assertIn("CFS_RUNOUT_REFILL SLOT=B CONFIRM=1 RESUME=0", out)

    def test_order_in_resume(self):
        # the nozzle has to be hot before the load, the load before the print
        p = printer(0., 35., 70., 70.)
        p.update(paused())
        out = render("RESUME", p)
        self.assertLess(out.index("_HEAT_FAST S=250.0"), out.index("_RESUME_LOAD"))
        self.assertLess(out.index("_RESUME_LOAD"), out.index("_RESUME_GO"))

    def test_filament_present_loads_nothing(self):
        self.assertEqual(render("_RESUME_LOAD", paused(detected=True)), [])

    def test_a_disabled_sensor_is_not_believed(self):
        self.assertEqual(render("_RESUME_LOAD", paused(enabled=False)), [])

    def test_only_from_a_pause(self):
        self.assertEqual(render("_RESUME_LOAD", paused(state="printing")), [])

    def test_a_failed_colour_change_retries_instead(self):
        p = printer(250., 250., 70., 70.)
        p.update(paused(change_pending=True))
        out = render("RESUME", p)
        self.assertIn("CFS_CHANGE_RETRY", out)
        self.assertNotIn("_RESUME_LOAD", out)

    def test_no_cfs_stays_paused(self):
        with self.assertRaisesRegex(MacroError, "rear holder"):
            render("_RESUME_LOAD", paused(fitted=False))

    def test_a_rear_spool_print_stays_paused(self):
        # the CFS must not load a slot into a print mapped to the rear spool
        with self.assertRaisesRegex(MacroError, "rear holder"):
            render("_RESUME_LOAD", paused(external=True))

    def test_hand_fed_after_a_runout_loads_and_purges(self):
        # 27 Nov: filament pushed in by hand after a rear-spool runout; the
        # sensor saw it, so RESUME printed without loading it
        for fitted, external, load in ((False, False, "LOAD_FILAMENT"),
                                       (True, True, "LOAD_FILAMENT SLOT=E")):
            out = render("_RESUME_LOAD", paused(
                detected=True, fitted=fitted, external=external,
                hand_load=1))
            self.assertIn(load, out)
            self.assertLess(out.index(load), out.index("_RESUME_LOAD_DONE"))

    def test_hand_fed_into_a_cfs_slot_print_stays_paused(self):
        with self.assertRaisesRegex(MacroError, "by hand"):
            render("_RESUME_LOAD", paused(detected=True, hand_load=1))


class ResumeGoTest(unittest.TestCase):
    def go(self, hold=0, wipe=0, paused=True, z_now=10., paused_z=126.4):
        return render("_RESUME_GO", {
            'gcode_macro _RESUME_GO': {'hold': hold, 'wipe': wipe},
            'gcode_macro PAUSE': {'paused_x': 191.7, 'paused_y': 239.6,
                                  'paused_z': paused_z},
            'pause_resume': {'is_paused': paused},
            'toolhead': {'homed_axes': 'xyz'},
            'gcode_move': {'gcode_position': {'z': z_now}}})

    def test_hold_stops_before_the_print(self):
        out = self.go(hold=1)
        self.assertNotIn("RESUME_BASE", out)
        self.assertIn("SET_GCODE_VARIABLE MACRO=_RESUME_GO VARIABLE=hold VALUE=0", out)

    def test_back_over_the_part_before_dropping_in(self):
        # the bed came up while paused (Z 10): over the part at 2 mm above
        # the print first, never one straight line through it
        out = self.go(z_now=10.)
        self.assertEqual(out[-3:], ["G1 Z128.400 F600",
                                    "G1 X191.700 Y239.600 F12000",
                                    "RESUME_BASE"])

    def test_already_higher_stays_higher(self):
        self.assertIn("G1 Z200.000 F600", self.go(z_now=200.))

    def test_wipe_after_a_hand_reload(self):
        out = self.go(wipe=1)
        self.assertLess(out.index("NOZZLE_WIPE CONFIRM=1"),
                        out.index("RESUME_BASE"))

    def test_hold_needs_a_pause(self):
        out = self.go(hold=1, paused=False)
        self.assertEqual(out[-1], "RESUME_BASE")
        self.assertNotIn("G1 Z128.400 F600", out)

    def test_no_slot_known_stays_paused(self):
        with self.assertRaisesRegex(MacroError, "no slot"):
            render("_RESUME_LOAD", paused(feed=None, last=None))


if __name__ == "__main__":
    unittest.main()
