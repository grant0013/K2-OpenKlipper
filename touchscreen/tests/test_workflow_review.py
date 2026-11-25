"""Regression checks for live controls, input workflows and operation feedback."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from unittest import mock

import action_policy
import test_native_ui
import test_service
from PIL import Image


class BackendReview(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp
    run_action = test_service.ServiceTests.run_action

    def test_temperature_targets_stay_editable_and_latest_pending_target_wins(self):
        first = {"action": "temperature", "heater": "heater_bed", "value": 30}
        with mock.patch("service.threading.Thread"):
            self.s.submit(first)
            self.assertTrue(
                self.s.snapshot()["availability"]["temperature:heater_bed"]["enabled"]
            )
            self.s.submit(dict(first, value=40))
            self.s.submit(dict(first, value=60))
            self.assertEqual(len(self.s.operations), 1)
            with mock.patch.object(self.s, "execute") as execute:
                self.s.run_operation(first, 1)
                self.s.run_operation(dict(first, value=60), 2)
            self.assertEqual(
                [call.args[0]["value"] for call in execute.call_args_list], [30, 60]
            )
        self.assertFalse(self.s.operations)
        self.assertFalse(self.s.pending_temperatures)

    def test_heater_interlocks_still_protect_calibration(self):
        op = {
            "action": "calibrate",
            "label": "Calibrating",
            "resources": ["bed", "chamber", "nozzle"],
        }
        available = action_policy.availability(self.state, True, [op])
        for key in (
            "temperature:heater_bed",
            "temperature:chamber",
            "temperature:extruder",
        ):
            self.assertFalse(available[key]["enabled"])

    def test_home_file_shortcut_blocks_calibration_but_not_homing(self):
        for kind in ("tilt", "mesh", "shaper", "belt", "cutter"):
            op = {
                "action": "calibrate",
                "kind": kind,
                "label": "Calibrating",
                "resources": ["motion"],
            }
            self.assertFalse(
                action_policy.availability(self.state, True, [op])["home_files"][
                    "enabled"
                ]
            )
        for action, kind in (("home", ""), ("calibrate", "home")):
            op = {
                "action": action,
                "kind": kind,
                "label": "Homing",
                "resources": ["motion"],
            }
            self.assertTrue(
                action_policy.availability(self.state, True, [op])["home_files"][
                    "enabled"
                ]
            )

    def test_error_during_calibration_creates_immediate_notice_without_duplicates(self):
        self.s.operations[1] = {
            "action": "calibrate",
            "label": "Calibrating",
            "resources": ["motion"],
        }
        self.s.stream_message(
            {"method": "notify_gcode_response", "params": ["!! Must home axes first"]}
        )
        self.s.event("Calibrating failed", "Must home axes first", fault=True)
        self.assertEqual(len(self.s.notices), 1)
        self.assertTrue(self.s.notices[0]["fault"])
        self.assertEqual(self.s.notices[0]["detail"], "Must home axes first")

    def test_repeated_error_after_acknowledgment_is_not_suppressed(self):
        self.s.event("Calibration failed", "Must home axes first", fault=True)
        first = self.s.notices[0]["id"]
        self.s.submit({"action": "ack", "id": first})
        self.s.event("Calibration failed", "Must home axes first", fault=True)
        self.assertEqual(len(self.s.notices), 1)
        self.assertNotEqual(self.s.notices[0]["id"], first)

    def test_z_tilt_homes_if_needed_and_checks_capability(self):
        self.state["toolhead"]["homed_axes"] = ""
        self.run_action("calibrate", kind="tilt", confirm=True)
        self.mr.run_gcode.assert_called_with("G28\nZ_TILT_ADJUST")
        self.state["toolhead"]["homed_axes"] = "xyz"
        self.run_action("calibrate", kind="tilt", confirm=True)
        self.mr.run_gcode.assert_called_with("Z_TILT_ADJUST")
        self.config.pop("z_tilt")
        with self.assertRaises(ValueError):
            self.run_action("calibrate", kind="tilt", confirm=True)

    def test_lowercase_macro_settings_advertise_print_calibration(self):
        self.s.settings["gcode_macro start_print"] = self.s.settings.pop(
            "gcode_macro START_PRINT"
        )
        self.assertTrue(self.s.snapshot()["calibrate_before_print"])

    def test_preload_runs_once_after_ready_connection(self):
        with mock.patch.object(self.s, "submit") as submit:
            self.s.connected = False
            self.s.preload_files()
            submit.assert_not_called()
            self.s.connected = True
            self.s.preload_files()
            self.s.preload_files()
            submit.assert_called_once_with(
                {"action": "files", "source": "gcodes", "automatic": True}
            )

    def test_ejection_immediately_marks_physically_present_drive_unmounted(self):
        with (
            mock.patch(
                "service.platform.usb_disk",
                return_value={"mounted": True, "id": "demo"},
            ),
            mock.patch("service.platform.usb_in_use", return_value=[]),
            mock.patch("service.platform.unmount_usb", return_value=True),
        ):
            self.run_action("usb_eject")
        self.assertFalse(self.s.platform["usb"]["mounted"])
        self.assertEqual(self.s.notices[-1]["title"], "USB ejected")

    def test_tune_speed_and_absolute_z_entry_keep_bounds(self):
        self.state["print_stats"]["state"] = "printing"
        self.run_action("tune", key="speed", value=200)
        self.mr.run_gcode.assert_called_with("M220 S200")
        with self.assertRaises(ValueError):
            self.run_action("tune", key="speed", value=201)
        self.run_action("tune", key="z_offset", value=0.05)
        self.mr.run_gcode.assert_called_with("SET_GCODE_OFFSET Z=0.05 MOVE=1")
        with self.assertRaises(ValueError):
            self.run_action("tune", key="z_offset", value=1)

    def test_restarts_remain_available_during_print_and_pending_pause(self):
        self.state["print_stats"]["state"] = "printing"
        available = action_policy.availability(
            self.state,
            True,
            [
                {
                    "action": "pause",
                    "label": "Pausing",
                    "resources": ["interrupt"],
                    "interrupt": True,
                }
            ],
        )
        self.assertTrue(available["restart"]["enabled"])
        self.assertTrue(available["firmware_restart"]["enabled"])


class UIReview(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def policy(self, data, ops=()):
        data["availability"] = action_policy.availability(
            data["status"], data["connected"], ops
        )
        data["operations"] = list(ops)
        data["operation"] = ops[0] if ops else None
        return data

    def test_documented_progress_cases_use_live_states(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "developer-tools/demo_snapshot.py"
        spec = importlib.util.spec_from_file_location("workflow_preview", path)
        preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preview)
        calibration = preview.preview_scenario("calibrating")
        self.assertFalse(calibration["availability"]["home_files"]["enabled"])
        self.assertEqual(calibration["operation"]["kind"], "tilt")
        printing = preview.preview_scenario("printing-long")
        self.assertEqual(printing["status"]["print_stats"]["state"], "printing")
        self.assertEqual(
            printing["platform"]["estimated_time"]
            - printing["status"]["print_stats"]["print_duration"],
            8100,
        )
        self.assertTrue(
            preview.preview_scenario("wifi-scanning")["platform"]["wifi"]["busy"]
        )
        self.assertEqual(
            preview.preview_scenario("wifi-connecting")["operation"]["action"],
            "wifi_join",
        )
        self.assertTrue(
            preview.preview_scenario("usb-ejected")["platform"]["usb"]["ejected"]
        )

    def test_printing_history_is_in_centered_status_area(self):
        data = copy.deepcopy(self.data)
        data["status"]["print_stats"]["state"] = "printing"
        for stage in (
            "Printing",
            "Waiting for the heated bed to settle before continuing with the print preparation sequence",
        ):
            data["print_stage"] = stage
            items, _ = self.render("home", data=data)
            history = [i for i in items if i["action"] == "route:history"]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["rect"], [103, 289, 470, 80])
            image = Image.open(self.path / "screen.ppm").convert("RGB")
            rows = [
                y
                for y in range(289, 369)
                if any(
                    all(v > 175 for v in image.getpixel((x, y)))
                    for x in range(103, 515)
                )
            ]
            self.assertTrue(rows)
            self.assertLessEqual(abs((min(rows) + max(rows)) / 2 - 329), 3)
            if stage != "Printing":
                self.assertGreater(max(rows) - min(rows), 30)
        items, _ = self.render("home", [{"tap": [310, 330]}], data=data)
        self.assertFalse(any(i["action"] == "cancel_print" for i in items))

    def test_steps_disabled_when_unhomed_or_homing(self):
        data = copy.deepcopy(self.data)
        data["status"]["toolhead"]["homed_axes"] = ""
        items, _ = self.render("move", data=self.policy(data))
        self.assertFalse(
            any(i["enabled"] for i in items if i["action"].startswith("step:"))
        )
        data["status"]["toolhead"]["homed_axes"] = "xyz"
        op = {"action": "home", "label": "Homing", "resources": ["motion"]}
        items, _ = self.render("move", data=self.policy(data, [op]))
        self.assertFalse(
            any(i["enabled"] for i in items if i["action"].startswith("step:"))
        )
        items, _ = self.render("move", data=self.policy(data))
        self.assertTrue(
            all(i["enabled"] for i in items if i["action"].startswith("step:"))
        )

    def test_calibration_error_popup_and_home_interlock(self):
        data = copy.deepcopy(self.data)
        op = {
            "action": "calibrate",
            "kind": "tilt",
            "label": "Calibrating",
            "resources": ["motion"],
        }
        data = self.policy(data, [op])
        items, _ = self.render(data=data)
        self.assertFalse(
            next(i for i in items if i["action"] == "open_files")["enabled"]
        )
        data["notices"] = [
            {
                "id": 1,
                "fault": True,
                "title": "Calibration failed",
                "detail": "Must home axes first",
            }
        ]
        items, _ = self.render("calibration", data=data)
        self.assertTrue(any(i["action"] == "notice_ack:1" for i in items))

    def test_z_tilt_present_only_for_capable_printers(self):
        items, _ = self.render("calibration")
        self.assertTrue(any(i["action"] == "calibrate:tilt" for i in items))
        data = copy.deepcopy(self.data)
        data["settings"].pop("z_tilt")
        items, _ = self.render("calibration", data=data)
        self.assertFalse(any(i["action"] == "calibrate:tilt" for i in items))

    def test_temperature_digit_hysteresis(self):
        data = copy.deepcopy(self.data)
        data["heaters"][2]["current"] = 19.49
        self.render(data=data)
        before = (
            Image.open(self.path / "screen.ppm").crop((635, 200, 780, 278)).tobytes()
        )
        changed = copy.deepcopy(data)
        changed["heaters"][2]["current"] = 19.51
        self.render(actions=[{"snapshot": changed}], data=data)
        after = (
            Image.open(self.path / "screen.ppm").crop((635, 200, 780, 278)).tobytes()
        )
        self.assertTrue(before == after)
        changed["heaters"][2]["current"] = 20.01
        self.render(actions=[{"snapshot": changed}], data=data)
        self.assertTrue(
            before
            != Image.open(self.path / "screen.ppm").crop((635, 200, 780, 278)).tobytes()
        )

    def test_lowercase_print_macro_shows_calibration_checkbox(self):
        data = copy.deepcopy(self.data)
        data["settings"]["gcode_macro start_print"] = data["settings"].pop(
            "gcode_macro START_PRINT"
        )
        _items, commands = self.render(
            "file", ["calibrate_before", "review_mapping", "start_print"], data
        )
        self.assertTrue(commands[-1]["calibrate"])

    def test_filament_picker_does_not_expose_a_partial_fourth_row(self):
        items, _ = self.render(
            "file", ["review_mapping", "mapping_pick:0", {"tap": [500, 410]}]
        )
        self.assertTrue(any(i["action"].startswith("mapping_set:") for i in items))

    def test_mapping_overview_clips_the_fourth_requirement_cleanly(self):
        data = copy.deepcopy(self.data)
        data["metadata"]["filaments"] = [
            {"tool": i, "type": "PLA", "colour": "5035CE"} for i in range(4)
        ]
        items, _ = self.render("file", ["review_mapping", {"tap": [600, 386]}], data)
        self.assertTrue(any(i["action"].startswith("mapping_pick:") for i in items))
        self.assertFalse(any(i["action"].startswith("mapping_set:") for i in items))

    def test_usb_ejected_does_not_keep_storage_actions_enabled(self):
        data = copy.deepcopy(self.data)
        data["platform"]["usb"] = {"mounted": False, "model": "Demo drive"}
        items, _ = self.render("storage", data=data)
        self.assertFalse(any(i["action"] == "route:usb" for i in items))
        items, _ = self.render("usb", data=data)
        self.assertFalse(
            any(
                "usb_eject" in i["action"] or i["action"] == "usb_format" for i in items
            )
        )

    def test_password_caret_blinks_without_covering_placeholder(self):
        initial = ['wifi_select:{"ssid":"Demo","secured":true}']
        self.render("networks", initial)
        first = Image.open(self.path / "screen.ppm").convert("RGB")
        # The caret is at x=33; the first hint letter begins after the gap.
        self.assertTrue(any(first.getpixel((33, y))[0] > 180 for y in range(89, 125)))
        self.assertTrue(all(first.getpixel((x, 108))[0] < 80 for x in range(37, 47)))
        self.render("networks", initial + [{"snapshot": self.data} for _ in range(30)])
        later = Image.open(self.path / "screen.ppm").convert("RGB")
        self.assertNotEqual(
            first.crop((32, 88, 36, 126)).tobytes(),
            later.crop((32, 88, 36, 126)).tobytes(),
        )
        self.render("networks", initial + ["key:q"])
        typed = Image.open(self.path / "screen.ppm").convert("RGB")
        self.assertNotEqual(
            first.crop((48, 90, 210, 125)).tobytes(),
            typed.crop((48, 90, 210, 125)).tobytes(),
        )

    def test_shift_is_one_shot_and_double_tap_locks_capitals(self):
        initial = ['wifi_select:{"ssid":"Demo","secured":true}']
        items, _ = self.render("networks", initial + ["key_shift", "key:Q"])
        self.assertTrue(any(i["action"] == "key:q" for i in items))
        items, _ = self.render(
            "networks", initial + ["key_shift", "key_shift", "key:Q"]
        )
        self.assertTrue(any(i["action"] == "key:Q" for i in items))
        items, _ = self.render(
            "networks", initial + ["key_shift", "key_shift", "key:Q", "key_shift"]
        )
        self.assertTrue(any(i["action"] == "key:q" for i in items))

    def test_tune_direct_numeric_speed_and_offset_entry(self):
        data = copy.deepcopy(self.data)
        data["status"]["print_stats"]["state"] = "printing"
        _items, commands = self.render(
            "tune",
            ["tune_input:speed", "number:2", "number:0", "number:0", "number:Set"],
            data,
        )
        self.assertEqual(commands, [{"action": "tune", "key": "speed", "value": 200}])
        _items, commands = self.render(
            "tune",
            [
                "tune_input:z_offset",
                "number:0",
                "number:.",
                "number:0",
                "number:5",
                "number:Set",
            ],
            data,
        )
        self.assertEqual(
            commands, [{"action": "tune", "key": "z_offset", "value": 0.05}]
        )

    def test_restart_buttons_are_enabled_with_real_printing_policy(self):
        data = copy.deepcopy(self.data)
        data["status"]["print_stats"]["state"] = "printing"
        data = self.policy(data)
        items, _ = self.render("restart", data=data)
        self.assertTrue(
            all(i["enabled"] for i in items if i["action"].startswith("restart:"))
        )


if __name__ == "__main__":
    unittest.main()
