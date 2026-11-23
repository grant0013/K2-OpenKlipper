"""Native service behavior: guard actual printer actions and retire operation state."""

import copy
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from service import Service


def status():
    return {
        "webhooks": {"state": "ready"},
        "print_stats": {"state": "standby"},
        "toolhead": {"homed_axes": "xyz"},
        "idle_timeout": {"state": "Idle"},
        "gcode_move": {"position": [100, 100, 10], "gcode_position": [100, 100, 10]},
        "k2_cfs": {"fitted": True, "online": True, "active_slot": "A"},
        "filament_switch_sensor filament_sensor": {"filament_detected": True},
        "hark_cfs_slots": {
            "map_tools": ["T0", "T1", "T2", "T3"],
            "map_targets": ["A", "B", "C", "D", "E"],
            "slots": {
                "A": {"loaded": True, "type": "PLA", "color": "123456", "name": "PLA"},
                "B": {"loaded": True, "type": "PLA", "color": "123456", "name": "PLA"},
                "E": {
                    "loaded": True,
                    "type": "PETG",
                    "color": "FFFFFF",
                    "name": "PETG",
                },
            },
        },
    }


def settings():
    return {
        "extruder": {"min_temp": -30, "max_temp": 350, "min_extrude_temp": 170},
        "heater_bed": {"min_temp": 0, "max_temp": 120},
        "heater_generic chamber_heater": {"min_temp": 0, "max_temp": 80},
        "hark_chamber": {"enable": True, "max_target": 60},
        "stepper_x": {"position_min": -9, "position_max": 352.5},
        "stepper_y": {"position_min": 0, "position_max": 350},
        "stepper_z": {"position_min": -10, "position_max": 360},
        "gcode_macro START_PRINT": {"variable_screen_full_mesh": 0},
        "gcode_macro _RUNOUT_REFILL": {"variable_enabled": 1},
        "fan": {},
        "heater_fan hotend_fan": {},
        "output_pin chamber_led": {},
        "z_tilt": {},
    }


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = Path(self.tmp.name) / "native.cfg"
        config.write_text("[brand]\nmodel: Test model\n[screen]\n")
        self.mr = mock.Mock()
        self.state = status()
        self.config = settings()
        self.mr.query.side_effect = lambda: copy.deepcopy(self.state)
        self.mr.status_snapshot.side_effect = lambda: {"status": self.mr.query(), "eventtime": time.monotonic()}
        self.mr.settings.side_effect = lambda: copy.deepcopy(self.config)
        self.s = Service(config, self.mr)
        self.s.status = copy.deepcopy(self.state)
        self.s.settings = copy.deepcopy(self.config)
        self.s.connected = True
        self.s.materials = [{"name": "PLA", "type": "PLA", "nozzle_temp": 220}]

    def run_action(self, action, **kw):
        return self.s.execute(dict(action=action, **kw))

    def test_bundled_printer_artwork_switches_with_cfs_presence(self):
        self.s.prepare_artwork()
        with_cfs = self.s.platform["printer_image"]
        self.assertTrue(Path(with_cfs).is_file())
        self.assertEqual(Path(self.s.artwork_key[0]).name, "printer-with-cfs.png")
        self.s.status["k2_cfs"]["fitted"] = False
        self.s.prepare_artwork()
        self.assertNotEqual(self.s.platform["printer_image"], with_cfs)
        self.assertEqual(Path(self.s.artwork_key[0]).name, "printer-without-cfs.png")
        self.s.status["k2_cfs"]["fitted"] = True
        self.s.prepare_artwork()
        self.assertEqual(self.s.platform["printer_image"], with_cfs)

    def test_legacy_image_override_does_not_change_automatic_artwork(self):
        self.s.config.set("brand", "status_image", "/missing/obsolete-printer.png")
        self.s.prepare_artwork()
        self.assertEqual(Path(self.s.artwork_key[0]).name, "printer-with-cfs.png")

    def test_timelapse_can_be_toggled_during_print(self):
        self.state["print_stats"]["state"] = "printing"
        self.s.platform["timelapse"] = {"enabled": False}
        for value in (True, False):
            self.run_action("timelapse", enabled=value)
            self.mr.request.assert_called_with("/machine/timelapse/settings", {"enabled": value})
            self.assertEqual(self.s.platform["timelapse"]["enabled"], value)

    def test_limits_change_without_model_assumptions(self):
        self.run_action("temperature", heater="extruder", value=350)
        self.mr.run_gcode.assert_called_with("M104 S350")
        self.config["extruder"]["max_temp"] = 285
        with self.assertRaises(ValueError):
            self.run_action("temperature", heater="extruder", value=300)
        self.run_action("temperature", heater="chamber", value=60)
        with self.assertRaises(ValueError):
            self.run_action("temperature", heater="chamber", value=61)
        self.config.pop("extruder")
        with self.assertRaises(ValueError):
            self.run_action("temperature", heater="extruder", value=200)

    def test_transparent_thumbnail_padding_is_trimmed_without_stretching(self):
        import io
        import storage
        from PIL import Image

        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        image.paste((255, 0, 0, 255), (20, 40, 80, 60))
        source = io.BytesIO()
        image.save(source, format="PNG")
        ready = storage.prepare_image(
            source, Path(self.tmp.name) / "images", "test", (120, 60), trim=True
        )
        with Image.open(ready) as fitted:
            self.assertEqual(fitted.size, (120, 60))
            self.assertEqual(fitted.getchannel("A").getbbox(), (0, 10, 120, 50))

    def test_unreadable_changed_thumbnail_clears_old_preview_and_can_retry(self):
        self.s.platform["file_thumbnails"] = {"part.gcode": "/tmp/old.png"}
        self.s.prepare_thumbnail = mock.Mock(side_effect=OSError("Unreadable preview"))
        self.mr.metadata.return_value = {}
        self.s.file_thumbnails([{"path": "part.gcode", "modified": 2, "size": 100}])
        self.assertNotIn("part.gcode", self.s.platform["file_thumbnails"])
        self.assertNotIn("part.gcode", self.s.file_signatures)

    def test_changed_file_without_thumbnail_clears_stale_preview(self):
        self.s.platform["file_thumbnails"] = {"part.gcode": "/tmp/old.png"}
        self.s.prepare_thumbnail = mock.Mock(return_value=None)
        self.mr.metadata.return_value = {}
        self.s.file_thumbnails([{"path": "part.gcode", "modified": 2, "size": 100}])
        self.assertNotIn("part.gcode", self.s.platform["file_thumbnails"])

    def test_old_thumbnail_worker_cannot_overwrite_new_generation(self):
        self.s.file_generation = 1
        self.s.platform["file_thumbnails"] = {"part.gcode": "/tmp/new.png"}

        def metadata(path):
            self.s.file_generation = 2
            return {}

        self.mr.metadata.side_effect = metadata
        self.s.prepare_thumbnail = mock.Mock(return_value="/tmp/old.png")
        self.s.file_thumbnails([{"path": "part.gcode", "modified": 1, "size": 100}], 1)
        self.assertEqual(
            self.s.platform["file_thumbnails"]["part.gcode"], "/tmp/new.png"
        )
        self.assertNotIn("part.gcode", self.s.file_signatures)

    def test_nan_and_unknown_heaters_never_reach_gcode(self):
        for heater, value in [
            ("extruder", float("nan")),
            ("extruder", float("inf")),
            ("unknown", 100),
        ]:
            with self.assertRaises(ValueError):
                self.run_action("temperature", heater=heater, value=value)
        self.mr.run_gcode.assert_not_called()

    def test_move_uses_fresh_limits_offsets_and_homing(self):
        self.state["gcode_move"]["position"][2] = 9.95
        self.run_action("position", axis="Z", value=360.05)
        self.assertIn("G1 Z360.0500", self.mr.run_gcode.call_args.args[0])
        with self.assertRaises(ValueError):
            self.run_action("position", axis="X", value=-10)
        self.state["toolhead"]["homed_axes"] = "xy"
        with self.assertRaises(ValueError):
            self.run_action("jog", axis="Z", value=1)

    def test_xy_homing_does_not_request_z(self):
        self.run_action("home", axis="XY")
        self.mr.run_gcode.assert_called_once_with("G28 X Y")
        with self.assertRaises(ValueError):
            self.run_action("home", axis="E")

    def test_manual_extrusion_preserves_gcode_state_and_configured_speed(self):
        self.state["extruder"] = {"temperature": 220, "can_extrude": True}
        self.config["extruder"].update(
            max_extrude_only_distance=20, max_extrude_only_velocity=2
        )
        self.run_action("extrude", value=-10)
        command = self.mr.run_gcode.call_args.args[0]
        self.assertIn("M83\nG1 E-10.0000 F120.0", command)
        self.assertTrue(command.endswith("RESTORE_GCODE_STATE NAME=NATIVE_EXTRUDE"))
        self.assertNotIn("CFS_", command)

    def test_manual_extrusion_rechecks_cold_and_configured_distance_limits(self):
        self.config["extruder"].update(
            min_extrude_temp=230, max_extrude_only_distance=8
        )
        self.state["extruder"] = {"temperature": 220, "can_extrude": True}
        with self.assertRaises(ValueError):
            self.run_action("extrude", value=1)
        self.state["extruder"]["temperature"] = 240
        for value in (9, -9, 0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.run_action("extrude", value=value)
        self.state["extruder"]["can_extrude"] = False
        with self.assertRaises(ValueError):
            self.run_action("extrude", value=1)
        self.mr.run_gcode.assert_not_called()

    def test_manual_extrusion_waits_for_cfs_filament_change(self):
        self.state["extruder"] = {"temperature": 220, "can_extrude": True}
        self.state["k2_cfs"]["change_pending"] = True
        with self.assertRaises(RuntimeError):
            self.run_action("extrude", value=1)
        self.mr.run_gcode.assert_not_called()

    def test_unavailable_during_printing_and_paused(self):
        for state in ("printing", "paused"):
            self.state["print_stats"]["state"] = state
            for action, kw in [
                ("home", {}),
                ("motors", {}),
                ("extrude", {"value": 1}),
                ("load", {"slot": "A"}),
                ("unload", {}),
                ("heaters_off", {}),
                ("calibrate", {"kind": "home", "confirm": True}),
            ]:
                with (
                    self.subTest(state=state, action=action),
                    self.assertRaises(RuntimeError),
                ):
                    self.run_action(action, **kw)
        self.mr.run_gcode.assert_not_called()

    def test_no_automatic_heater_fan_mutation(self):
        with self.assertRaises(KeyError):
            self.run_action("fan", fan="hotend", value=50)
        with self.assertRaises(ValueError):
            self.run_action("fan", fan="part", value=101)
        self.run_action("fan", fan="part", value=50)
        self.mr.run_gcode.assert_called_with("M106 P0 S127")

    def test_load_unload_delegate_to_existing_firmware_macros(self):
        self.run_action("load", slot="B")
        self.mr.run_gcode.assert_called_with("CFS_LOAD_SLOT SLOT=B CONFIRM=1")
        self.run_action("unload")
        self.mr.run_gcode.assert_called_with("CFS_UNLOAD_ACTIVE CONFIRM=1")
        self.state["k2_cfs"]["active_slot"] = None
        self.run_action("load", slot="E")
        self.mr.run_gcode.assert_called_with("LOAD_FILAMENT")
        self.run_action("unload")
        self.mr.run_gcode.assert_called_with("UNLOAD_FILAMENT")

    def test_files_always_use_configured_printer_root(self):
        self.mr.files.return_value = [
            {"path": "folder/old.gcode", "modified": 1},
            {"path": "new.gcode", "modified": 2},
        ]
        with mock.patch("service.threading.Thread"):
            self.run_action("files", source="usb")
        self.mr.files.assert_called_once_with()
        self.assertEqual(self.s.files_source, "gcodes")
        self.assertEqual(self.s.files[0]["path"], "new.gcode")

    def test_file_refresh_is_background_coalesced_and_preserves_foreground_operation(
        self,
    ):
        self.s.files = [{"path": "cached.gcode"}]
        self.mr.files.return_value = [{"path": "new.gcode", "modified": 2}]
        with mock.patch("service.threading.Thread") as thread:
            self.s.submit({"action": "files"})
            self.s.submit({"action": "files"})
            self.assertEqual(thread.call_count, 1)
            self.assertTrue(self.s.files_refreshing)
            self.assertIsNone(self.s.operation)
            self.assertEqual(self.s.files[0]["path"], "cached.gcode")
            self.s.submit({"action": "home"})
            operation = self.s.operation
            self.s.refresh_files()
            self.assertIs(self.s.operation, operation)
        self.assertFalse(self.s.files_refreshing)
        self.assertEqual(self.s.files[0]["path"], "new.gcode")
        self.assertIsNone(self.s.result)
        self.mr.run_gcode.assert_not_called()

    def test_failed_background_refresh_retains_cache_without_modal_notice(self):
        self.s.files = [{"path": "cached.gcode"}]
        self.mr.files.side_effect = RuntimeError("Storage is unavailable")
        self.s.files_refreshing = True
        self.s.refresh_files()
        self.assertFalse(self.s.files_refreshing)
        self.assertEqual(self.s.files[0]["path"], "cached.gcode")
        self.assertEqual(
            self.s.snapshot()["files_refresh"]["error"], "Storage is unavailable"
        )
        self.assertEqual(self.s.notices, [])
        self.assertIsNone(self.s.operation)

    def test_previews_cover_all_files_and_cache_unchanged_entries(self):
        files = [
            {"path": "part%d.gcode" % i, "modified": i, "size": 10} for i in range(17)
        ]
        self.s.prepare_thumbnail = mock.Mock(return_value="/tmp/test-preview.png")
        self.s.file_thumbnails(files)
        self.assertEqual(self.mr.metadata.call_count, 17)
        self.assertEqual(len(self.s.platform["file_thumbnails"]), 17)
        self.s.file_thumbnails(files)
        self.assertEqual(self.mr.metadata.call_count, 17)

    def test_timezone_presets_read_retained_firmware_mapping(self):
        import storage

        source = Path(self.tmp.name) / "timezone.sh"
        source.write_text("#!/bin/sh\nUTC-05:00;EST\nUTC+05:30;Asia/Kolkata\n")
        with mock.patch("storage.timezones", return_value=["EST", "Asia/Kolkata"]):
            choices = storage.timezone_presets(str(source))
        self.assertEqual([item["id"] for item in choices], ["EST", "Asia/Kolkata"])
        self.assertEqual(choices[0]["name"], "Eastern Standard Time (EST)")
        self.assertEqual(choices[1]["cities"], "Kolkata")
        self.assertEqual(choices[1]["label"], "UTC+05:30")

    def test_restart_invalidates_subscription_and_accepts_fresh_status(self):
        self.s.stream_connected = True
        with self.assertRaises(EOFError):
            self.s.stream_message({"method": "notify_klippy_disconnected"})
        self.assertFalse(self.s.stream_connected)
        self.assertFalse(self.s.connected)
        self.s.stream_message(
            {
                "result": {
                    "eventtime": 12,
                    "status": {
                        "k2_cfs": {"online": True, "temperature": 24, "humidity": 25}
                    },
                }
            }
        )
        self.assertTrue(self.s.connected)
        self.assertTrue(self.s.stream_connected)
        self.assertEqual(self.s.status["k2_cfs"]["temperature"], 24)

    def test_full_refresh_fills_dynamic_cfs_fields_without_rewinding_status(self):
        self.s.merge_status({"k2_cfs": {"online": None}}, 10, full=True)
        self.s.merge_status(
            {"k2_cfs": {"online": True, "temperature": 24, "humidity": 25}},
            12,
            full=True,
        )
        self.s.merge_status({"k2_cfs": {"online": None}}, 11, full=True)
        self.assertTrue(self.s.status["k2_cfs"]["online"])
        self.assertEqual(self.s.status["k2_cfs"]["humidity"], 25)
        with self.assertRaises(RuntimeError):
            self.s.stream_message({"error": {"message": "Klipper not ready"}})

    def test_startup_is_not_presented_as_a_printer_fault(self):
        self.s.status["webhooks"] = {
            "state": "startup",
            "state_message": "Printer is not ready",
        }
        self.assertEqual(self.s.snapshot()["error"], "")
        self.s.status["webhooks"]["state"] = "error"
        self.assertEqual(self.s.snapshot()["error"], "Printer is not ready")

    def test_external_handoff_interlocks(self):
        with self.assertRaises(RuntimeError):
            self.run_action("load", slot="E", inserted=True)
        self.state["k2_cfs"]["active_slot"] = None
        with self.assertRaises(RuntimeError):
            self.run_action("load", slot="A")
        self.run_action("load", slot="E")
        self.mr.run_gcode.assert_called_with("LOAD_FILAMENT")

    def test_slot_temperature_uses_driver_parameter_and_limits(self):
        self.run_action(
            "slot_save",
            slot="A",
            record={"name": "PLA", "color": "ABCDEF", "nozzle_temp": 250},
        )
        self.assertIn("NOZZLE=250", self.mr.run_gcode.call_args.args[0])
        with self.assertRaises(ValueError):
            self.run_action(
                "slot_save",
                slot="A",
                record={"name": "PLA", "color": "ABCDEF", "nozzle_temp": 351},
            )

    def test_print_start_validates_mapping_and_rolls_back(self):
        self.s.metadata["part.gcode"] = {"filaments": [{"tool": 0}]}
        self.mr.request.side_effect = RuntimeError("cannot start")
        with self.assertRaises(RuntimeError):
            self.run_action(
                "start", path="part.gcode", mapping={"0": "A"}, calibrate=True
            )
        calls = [c.args[0] for c in self.mr.run_gcode.call_args_list]
        self.assertIn("CFS_TOOL_MAP RESET=1 QUIET=1", calls)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=screen_full_mesh VALUE=0",
            calls,
        )

    def test_multifilament_external_and_unloaded_mapping_refused(self):
        self.s.metadata["part.gcode"] = {"filaments": [{"tool": 0}, {"tool": 1}]}
        with self.assertRaises(ValueError):
            self.run_action("start", path="part.gcode", mapping={"0": "E", "1": "E"})
        self.state["hark_cfs_slots"]["slots"]["A"]["loaded"] = False
        with self.assertRaises(ValueError):
            self.run_action("start", path="part.gcode", mapping={"0": "A", "1": "B"})
        self.mr.run_gcode.assert_not_called()

    def test_stop_restart_exclude_require_confirmation(self):
        self.state["print_stats"]["state"] = "printing"
        for action in ("cancel", "restart", "firmware_restart"):
            with self.assertRaises(ValueError):
                self.run_action(action)
        self.state["exclude_object"] = {"objects": [{"name": "part"}]}
        with self.assertRaises(ValueError):
            self.run_action("exclude", names=["other"], confirm=True)
        self.run_action("exclude", names=["part"], confirm=True)
        self.mr.run_gcode.assert_called_with('EXCLUDE_OBJECT NAME="part"')

    def test_unknown_action_cannot_execute_arbitrary_gcode(self):
        with self.assertRaises(ValueError):
            self.run_action("gcode", script="G28")
        self.mr.run_gcode.assert_not_called()

    def test_ack_does_not_resume_and_old_notice_stays_acknowledged(self):
        e = self.s.event("Unloading failed", "test", fault=True)
        self.s.submit({"action": "ack", "id": e["id"]})
        self.assertEqual(self.s.notices, [])
        self.assertEqual(len(self.s.events), 1)
        self.s.merge_status({"gcode_macro _STAGE": {"text": "Unloading filament"}})
        self.assertEqual(self.s.notices, [])
        self.mr.run_gcode.assert_not_called()

    def test_failed_operation_retires_before_next_home(self):
        self.mr.run_gcode.side_effect = RuntimeError("unload failed")
        with mock.patch("service.threading.Thread"):
            self.s.submit({"action": "unload"})
            token = self.s.operation["id"]
            self.s.run_operation({"action": "unload"}, token)
            self.assertIsNone(self.s.operation)
            self.assertEqual(self.s.result["action"], "unload")
            self.assertFalse(self.s.result["success"])
            self.s.submit({"action": "home"})
            self.assertEqual(self.s.operation["label"], "Homing")

    def test_shutdown_can_restart_but_cannot_move(self):
        self.state["webhooks"]["state"] = "shutdown"
        with self.assertRaises(RuntimeError):
            self.run_action("home")
        self.run_action("restart", confirm=True)
        self.mr.request.assert_called_with("/printer/restart", {})

    def test_refill_and_rfid_preferences_independent(self):
        self.run_action("preference", key="cfs_startup_read", value=True)
        self.mr.run_gcode.assert_not_called()
        self.run_action("preference", key="cfs_auto_refill", value=False)
        self.mr.run_gcode.assert_called_with(
            "SET_GCODE_VARIABLE MACRO=_RUNOUT_REFILL VARIABLE=enabled VALUE=0"
        )
        self.assertTrue(self.s.prefs["cfs_startup_read"])
        self.assertFalse(self.s.prefs["cfs_auto_refill"])

    def test_missing_hardware_capabilities_do_not_invent_heaters(self):
        self.s.status = {"print_stats": {"state": "standby"}}
        self.s.settings = {}
        snap = self.s.snapshot()
        self.assertEqual(snap["model"], "Test model")
        self.assertEqual(snap["heaters"], [])


if __name__ == "__main__":
    unittest.main()
