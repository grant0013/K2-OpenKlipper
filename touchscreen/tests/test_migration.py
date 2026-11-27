"""Validate migration without running a printer service or changing calibration."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_migration", ROOT / "installation/migrate.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTest(unittest.TestCase):
    def test_obsolete_image_paths_are_removed_without_rewriting_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "native-screen.cfg"
            target.write_text("[brand]\n# Keep this comment\nmodel: My printer\nstatus_image: old.png\nstatus_image_without_cfs = old-bare.png\n[screen]\nrotate: 90\n")
            migration.migrate(target, ROOT / "runtime/native-screen.cfg.example", Path(directory) / "missing.cfg")
            self.assertEqual(target.read_text(), "[brand]\n# Keep this comment\nmodel: My printer\n[screen]\nrotate: 90\n")

    def test_existing_native_accent_survives_new_config_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            prefs = path / "native-screen-settings.json"
            prefs.write_text('{"accent": "#d9434a"}')
            migration.migrate(path / "native-screen.cfg", ROOT / "runtime/native-screen.cfg.example", path / "missing.cfg")
            self.assertEqual(migration.json.loads(prefs.read_text())["accent"], "#d9434a")

    def test_timezone_auto_carries_over_and_repairs_existing_install(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                target = path / "native-screen.cfg"
                prefs = path / "native-screen-settings.json"
                (path / "harkscreen-settings.json").write_text('{"timezone": "auto"}')
                if existing:
                    target.write_text("[screen]\nrotate: 90\n")
                    prefs.write_text('{"brightness": 40}')
                migration.migrate(target, ROOT / "runtime/native-screen.cfg.example", path / "harkscreen.cfg")
                result = migration.json.loads(prefs.read_text())
                self.assertEqual(result["timezone"], "auto")
                if existing:
                    self.assertEqual(result["brightness"], 40)
                result["timezone"] = "America/Detroit"
                prefs.write_text(migration.json.dumps(result))
                migration.migrate(target, ROOT / "runtime/native-screen.cfg.example", path / "harkscreen.cfg")
                self.assertEqual(migration.json.loads(prefs.read_text())["timezone"], "America/Detroit")

    def test_active_usb_storage_selection_survives_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            legacy = path / "harkscreen.cfg"
            legacy.write_text("[screen]\nrotate: 90\n")
            (path / "harkscreen-settings.json").write_text(
                '{"gcode_storage": "usb", "usb_ejected": false}'
            )
            migration.migrate(path / "native-screen.cfg", ROOT / "runtime/native-screen.cfg.example", legacy)
            prefs = migration.json.loads((path / "native-screen-settings.json").read_text())
            self.assertEqual(prefs["gcode_storage"], "usb")
            self.assertIs(prefs["usb_ejected"], False)

    def test_display_settings_transfer_and_calibration_stays_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            legacy = path / "harkscreen.cfg"
            legacy.write_text(
                "[brand]\nmodel: Custom model\nstatus_image: obsolete.png\n[screen]\nrotate: 270\n"
                "cutter_calibration_method: STALL\n[theme]\naccent: #d9434a\n"
            )
            overrides = path / "printer-overrides.cfg"
            overrides.write_text("[extruder]\nmax_temp: 350\n")
            target = path / "native-screen.cfg"
            migration.migrate(
                target, ROOT / "runtime/native-screen.cfg.example", legacy, "Detected model"
            )
            config = migration.configparser.ConfigParser()
            config.read(target)
            self.assertFalse(config.has_option("brand", "status_image"))
            self.assertEqual(config.get("brand", "model"), "Custom model")
            self.assertEqual(config.getint("screen", "rotate"), 270)
            self.assertEqual(config.get("screen", "cutter_calibration_method"), "STALL")
            self.assertEqual(overrides.read_text(), "[extruder]\nmax_temp: 350\n")
            self.assertEqual(
                migration.json.loads(
                    target.with_name("native-screen-settings.json").read_text()
                )["accent"],
                "#3d8bfd",
            )
            target.write_text("[brand]\nmodel: Existing native settings\n")
            migration.migrate(target, ROOT / "runtime/native-screen.cfg.example", legacy)
            self.assertEqual(
                target.read_text(), "[brand]\nmodel: Existing native settings\n"
            )


if __name__ == "__main__":
    unittest.main()
