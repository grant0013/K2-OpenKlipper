"""K2 filament profiles: the loaded slot's record overrides the file's
temperatures and part fan, and the K2 warms its chamber for ASA/ABS/PC."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).parents[1]
CFGS = ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg")


def section(cfg, name):
    text = (ROOT / "config" / cfg).read_text()
    m = re.search(r"^\[%s\]\n(.*?)(?=^\[)" % re.escape(name), text,
                  re.M | re.S)
    assert m, "%s missing from %s" % (name, cfg)
    return m.group(1)


class FilamentProfile(unittest.TestCase):
    def test_wrappers_route_through_the_profile(self):
        for cfg in CFGS:
            for cmd, base in (("M140", "M140.1"), ("M190", "M190.1"),
                              ("M104", "M104.1"), ("M109", "M109.1")):
                body = section(cfg, "gcode_macro %s" % cmd)
                self.assertIn("rename_existing: %s" % base, body)
            for cmd in ("M140", "M190"):
                self.assertIn('gcode_macro _FILAMENT_PROFILE',
                              section(cfg, "gcode_macro %s" % cmd))
            for cmd in ("M104", "M109"):
                self.assertIn("_PROFILE_NOZZLE",
                              section(cfg, "gcode_macro %s" % cmd))

    def test_overrides_only_while_printing(self):
        for cfg in CFGS:
            for name in ("gcode_macro M140", "gcode_macro M190",
                         "gcode_macro _PROFILE_NOZZLE", "gcode_macro M106"):
                self.assertIn('printer.print_stats.state in ("printing", '
                              '"paused")', section(cfg, name), name)

    def test_armed_after_start_and_cleared_at_end(self):
        for cfg in CFGS:
            sp = section(cfg, "gcode_macro START_PRINT")
            arm = sp.index("VARIABLE=active VALUE=1")
            self.assertGreater(arm, sp.index("_CREALITY_PRIME_LINE"))
            for name in ("gcode_macro END_PRINT", "gcode_macro CANCEL_PRINT"):
                self.assertIn("MACRO=_FILAMENT_PROFILE VARIABLE=active "
                              "VALUE=0", section(cfg, name))

    def test_tool_change_hands_the_nozzle_back(self):
        for cfg in CFGS:
            body = section(cfg, "gcode_macro _HARK_TOOL")
            self.assertLess(body.index("VARIABLE=multi VALUE=1"),
                            body.index("_CFS_TC SLOT="))

    def test_chamber_warmup_defaults(self):
        self.assertIn("variable_chamber_soak: {'ASA': 50, 'ABS': 50, 'PC': 50}",
                      section(CFGS[0], "gcode_macro _FILAMENT_PROFILE"))
        # The K2 Plus has a chamber heater of its own
        self.assertIn("variable_chamber_soak: {}",
                      section(CFGS[1], "gcode_macro _FILAMENT_PROFILE"))

    def test_new_macro_names_parse_as_extended_commands(self):
        # Klipper reads a name like _K2_X as the command "_K2": no letter
        # followed by a digit in a macro name
        for cfg in CFGS:
            text = (ROOT / "config" / cfg).read_text()
            for name in ("_FILAMENT_PROFILE", "_PROFILE_NOZZLE",
                         "_CHAMBER_SOAK_STEP", "_CHAMBER_SOAK_END"):
                self.assertIn("[gcode_macro %s]" % name, text)
                self.assertIsNone(re.search(r"[A-Z][0-9]", name))


if __name__ == "__main__":
    unittest.main()
