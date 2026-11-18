"""Hi config changes from the 18 Nov 2026 cross-check: one probe speed,
3-tap Z home, fast squash touches, z_tilt travel height, hot Z per
material, fan tachometers (log only) and the Z motor cable check."""
import pathlib
import unittest

CFG = (pathlib.Path(__file__).parents[1] / "config"
       / "mainline_stage2_hi.cfg").read_text()


def section(name):
    start = CFG.index("[%s]" % name)
    end = CFG.find("\n[", start + 1)
    return CFG[start:end if end > 0 else None]


class ProbeTest(unittest.TestCase):
    def test_one_speed_and_three_tap_home(self):
        pr = section("prtouch_mainline")
        self.assertIn("\nspeed: 1.0", pr)
        self.assertIn("home_samples: 3", pr)
        self.assertIn("home_samples_tolerance: 0.03", pr)
        self.assertIn("home_followup_start: 1.0", pr)
        # the rough strip home stays one tap
        self.assertIn("START_Z=8 SAMPLES=1 MAX_TOUCHES=1",
                      section("gcode_macro NOZZLE_CLEAR"))

    def test_squash_touches_fast_rezero_normal(self):
        nc = section("gcode_macro NOZZLE_CLEAR")
        self.assertEqual(nc.count("PROBE PROBE_SPEED=5"), 2)
        scrub = section("gcode_macro _NOZZLE_CLEAR_SCRUB")
        self.assertNotIn("PROBE_SPEED", scrub)

    def test_z_tilt_travel(self):
        self.assertIn("horizontal_move_z: 3", section("z_tilt"))
        self.assertIn("retry_tolerance: 0.03", section("z_tilt"))


class StartPrintTest(unittest.TestCase):
    def test_hot_z_per_material(self):
        sp = section("gcode_macro START_PRINT")
        self.assertIn("variable_hot_z_material: {}", sp)
        self.assertIn("set offset = 0.0 - hz.v + sp.z_offset|float", sp)

    def test_z_driver_check_after_homing(self):
        sp = section("gcode_macro START_PRINT")
        call = sp.index("        HI_Z_DRIVER_CHECK\n")
        self.assertLess(sp.index("        G28\n"), call)
        self.assertLess(call, sp.index("NOZZLE_CLEAR MATERIAL"))
        self.assertIn("RESPOND TYPE=error",
                      section("gcode_macro _HI_Z_DRIVER_REPORT"))


class FanTest(unittest.TestCase):
    def test_tachometers_log_only(self):
        self.assertIn("tachometer_pin: ^nozzle_mcu:PA10", section("fan"))
        self.assertIn("tachometer_pin: ^nozzle_mcu:PA9",
                      section("heater_fan hotend_fan"))
        self.assertIn("fan_action: log", section("hark_safety"))


if __name__ == "__main__":
    unittest.main()
