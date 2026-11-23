"""Metadata isolation, responsive setters and consistent touch feedback."""

import base64
import copy
import unittest
from pathlib import Path
from unittest import mock

import action_policy
import gcode_metadata
import platform_services
import test_native_ui
import test_service
from PIL import Image


class BackendFeedback(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_slow_query_cannot_overwrite_newer_homing_subscription(self):
        self.s.merge_status({"toolhead": {"homed_axes": "xyz"}}, 20)
        stale = copy.deepcopy(self.state)
        stale["toolhead"]["homed_axes"] = ""
        self.mr.status_snapshot.side_effect = None
        self.mr.status_snapshot.return_value = {"status": stale, "eventtime": 10}
        status, _ = self.s.fresh()
        self.assertEqual(status["toolhead"]["homed_axes"], "xyz")

    def test_spool_events_prompt_once_cancel_on_removal_and_hide_old_details(self):
        self.s.wake_screen = mock.Mock()
        self.s.merge_status({"k2_cfs": {"slot_event_history": []}})
        history = [{"id": 1, "slot": "A", "event": "inserted"}]
        self.s.merge_status({"k2_cfs": {"slot_event_history": history}})
        self.assertEqual(self.s.notices[-1]["filament_slot"], "A")
        count = self.s.history_id
        self.s.merge_status({"k2_cfs": {"slot_event_history": history}})
        self.assertEqual(self.s.history_id, count)
        history = history + [{"id": 2, "slot": "A", "event": "removed"}]
        self.s.merge_status({"k2_cfs": {"slot_event_history": history}})
        self.assertFalse(self.s.notices)
        self.s.status["hark_cfs_slots"]["slots"]["A"]["loaded"] = False
        record = next(r for r in self.s.snapshot()["slots"] if r["slot"] == "A")
        self.assertNotIn("type", record)
        self.assertNotIn("color", record)
        self.assertEqual(self.s.status["hark_cfs_slots"]["slots"]["A"]["type"], "PLA")
        history += [{"id": 3, "slot": "A", "event": "inserted"}]
        self.s.merge_status({"k2_cfs": {"slot_event_history": history}})
        self.assertEqual(len(self.s.notices), 1)
        self.assertEqual(self.s.notices[0]["filament_slot"], "A")

    def test_startup_does_not_replay_old_spool_notices(self):
        self.s.wake_screen = mock.Mock()
        self.s.merge_status(
            {
                "k2_cfs": {
                    "slot_event_history": [{"id": 8, "slot": "A", "event": "inserted"}]
                }
            }
        )
        self.assertFalse(self.s.notices)
        self.assertEqual(self.s.cfs_event_id, 8)

    def test_klipper_print_state_not_idle_timer_controls_print_locks(self):
        self.state["idle_timeout"]["state"] = "Printing"
        available = action_policy.availability(self.state, True, [])
        for key in ("slot_save", "home", "jog:X", "load"):
            self.assertTrue(available[key]["enabled"], key)
        self.s.execute({"action": "home"})
        self.state["print_stats"]["state"] = "printing"
        self.assertFalse(
            action_policy.availability(self.state, True, [])["load"]["enabled"]
        )
        op = {
            "action": "calibrate",
            "label": "Calibrating",
            "resources": ["motion", "filament"],
        }
        self.assertFalse(
            action_policy.availability(self.s.status, True, [op])["load"]["enabled"]
        )

    def test_temperature_echo_is_immediate_and_failed_request_rolls_back(self):
        self.s.status["heater_bed"] = {"temperature": 20, "target": 0}
        r = {"action": "temperature", "heater": "heater_bed", "value": 35}
        with mock.patch("service.threading.Thread"):
            self.s.submit(r)
        self.assertEqual(self.s.status["heater_bed"]["target"], 0)
        self.assertEqual(
            next(h for h in self.s.snapshot()["heaters"] if h["key"] == "heater_bed")[
                "target"
            ],
            35,
        )
        with mock.patch.object(self.s, "execute", side_effect=RuntimeError("rejected")):
            self.s.run_operation(r, 1)
        self.assertEqual(
            next(h for h in self.s.snapshot()["heaters"] if h["key"] == "heater_bed")[
                "target"
            ],
            0,
        )
        self.assertTrue(self.s.notices)

    def test_shutdown_discards_unconfirmed_setters(self):
        with mock.patch("service.threading.Thread"):
            self.s.submit(
                {"action": "temperature", "heater": "heater_bed", "value": 35}
            )
            self.s.submit(
                {"action": "temperature", "heater": "heater_bed", "value": 40}
            )
            self.s.merge_status({"webhooks": {"state": "shutdown"}})
        self.assertFalse(self.s.requested_values)
        self.assertFalse(self.s.pending_temperatures)
        self.assertFalse(self.s.pending_fans)

    def test_rejected_temperature_does_not_create_a_target_echo(self):
        with self.assertRaises(ValueError):
            self.s.submit(
                {"action": "temperature", "heater": "heater_bed", "value": 9999}
            )
        self.assertFalse(self.s.requested_values)

    def test_fan_stays_editable_and_only_latest_repeat_is_queued(self):
        self.s.status["fan"] = {"speed": 0}
        r = {"action": "fan", "fan": "part", "value": 40}
        with mock.patch("service.threading.Thread"):
            self.s.submit(r)
            self.s.submit(dict(r, value=80))
            snap = self.s.snapshot()
            self.assertTrue(snap["availability"]["fan:part"]["enabled"])
            self.assertEqual(snap["status"]["fan"]["speed"], 0.8)
            self.assertEqual(len(self.s.operations), 1)
            with mock.patch.object(self.s, "execute") as execute:
                self.s.run_operation(r, 1)
                self.s.run_operation(dict(r, value=80), 2)
            self.assertEqual(
                [c.args[0]["value"] for c in execute.call_args_list], [40, 80]
            )

    def test_new_file_clears_preview_on_admission_and_late_old_result_is_ignored(self):
        self.s.platform["file_thumbnail"] = "old.png"
        with mock.patch("service.threading.Thread") as thread:
            self.s.submit({"action": "file", "path": "first.gcode"})
            self.s.submit({"action": "file", "path": "second.gcode"})
        self.assertNotIn("file_thumbnail", self.s.platform)
        self.assertTrue(self.s.snapshot()["file_read"]["busy"])
        requests = [c.kwargs["args"] for c in thread.call_args_list]
        with (
            mock.patch.object(
                self.s, "read_metadata", return_value=({"filament_type": "PLA"}, None)
            ),
            mock.patch.object(
                self.s,
                "prepare_thumbnail",
                side_effect=lambda path, *args, **kw: path + ".png",
            ),
        ):
            self.s.run_operation(*requests[1])
            self.s.run_operation(*requests[0])
        self.assertEqual(self.s.selected_file, "second.gcode")
        self.assertEqual(self.s.platform["file_thumbnail"], "second.gcode.png")
        self.assertFalse(self.s.file_read["busy"])

    def test_missing_thumbnail_clears_its_previous_image(self):
        self.s.platform["file_thumbnail"] = "old.png"
        self.assertIsNone(
            self.s.prepare_thumbnail("part.gcode", {}, "file_thumbnail", (100, 100))
        )
        self.assertNotIn("file_thumbnail", self.s.platform)

    def test_slot_reset_is_metadata_only_and_rejects_unknown_slots(self):
        self.s.execute({"action": "slot_clear", "slot": "E", "confirm": True})
        self.mr.run_gcode.assert_called_once_with("CFS_SLOT_CLEAR SLOT=E")
        self.s.execute({"action": "slot_clear", "slot": "A", "confirm": True})
        self.assertEqual(self.mr.run_gcode.call_args.args[0], "CFS_SLOT_CLEAR SLOT=A")
        for slot, confirmed in (("Z", True), ("E", False)):
            with self.assertRaises(ValueError):
                self.s.execute(
                    {"action": "slot_clear", "slot": slot, "confirm": confirmed}
                )

    def test_print_file_change_clears_old_preview_and_handles_missing_image(self):
        self.s.platform["thumbnail"] = "old.png"
        self.s.merge_status({"print_stats": {"filename": "new.gcode"}})
        self.assertNotIn("thumbnail", self.s.platform)
        self.assertTrue(self.s.platform["print_preview_busy"])
        with mock.patch.object(self.s, "read_metadata", return_value=({}, None)):
            self.s.refresh_print_preview("new.gcode")
        self.assertFalse(self.s.platform["print_preview_busy"])
        self.assertNotIn("thumbnail", self.s.platform)


class GcodeMetadata(unittest.TestCase):
    def test_creality_footer_preserves_materials_colors_weights_and_time(self):
        text = """; generated by Creality_Print V6.0
; total layer number: 200
; filament used [g] = 3.01, 1.70, 5.36, 4.01
; estimated printing time (normal mode) = 34m 1s
; filament_colour = #FFFFFF;#000000;#0080FF;#ff1e1e
; filament_type = PLA;PLA;PLA;PLA
"""
        meta, image = gcode_metadata.parse(text)
        filaments = platform_services.file_filaments(meta)
        self.assertEqual([f["type"] for f in filaments], ["PLA"] * 4)
        self.assertEqual(filaments[3]["colour"], "FF1E1E")
        self.assertEqual(meta["estimated_time"], 2041)
        self.assertEqual(meta["layer_count"], 200)
        self.assertAlmostEqual(meta["filament_weight_total"], 14.08)
        self.assertIsNone(image)

    def test_embedded_png_and_missing_or_corrupt_thumbnail(self):
        png = (
            Path(__file__).resolve().parents[1]
            / "developer-tools/fixtures/assets/demo-part.png"
        ).read_bytes()
        text = (
            "; thumbnail begin 300x300 123\n; "
            + base64.b64encode(png).decode()
            + "\n; thumbnail end"
        )
        self.assertEqual(gcode_metadata.parse(text)[1], png)
        self.assertIsNone(
            gcode_metadata.parse(
                "; thumbnail begin 300x300 1\n; !!!!\n; thumbnail end"
            )[1]
        )
        self.assertEqual(gcode_metadata.parse("; no metadata"), ({}, None))

    def test_remote_reader_bounds_even_when_server_ignores_range(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"; filament_type = PLA\n"
        with mock.patch("gcode_metadata.urllib.request.urlopen", return_value=response):
            result, _ = gcode_metadata.read("http://example.invalid", "part.gcode")
        self.assertEqual(result["filament_type"], "PLA")
        self.assertTrue(
            all(
                call.args == (gcode_metadata.WINDOW,)
                for call in response.read.call_args_list
            )
        )
        with self.assertRaises(ValueError):
            gcode_metadata.read("http://example.invalid", "../config.cfg")


class NativeFeedback(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def test_mapping_buttons_align_with_their_columns(self):
        items, _ = self.render("file", ["review_mapping"])
        back = next(i for i in items if i["action"] == "back")
        start = next(i for i in items if i["action"] == "start_print")
        pick = next(i for i in items if i["action"].startswith("mapping_pick:"))
        self.assertEqual(back["rect"][2], 286)
        self.assertEqual(start["rect"][0], pick["rect"][0])
        self.assertEqual(start["rect"][2], pick["rect"][2])
        self.assertEqual(start["rect"][1:], [402, 320, 64])

    def test_reset_is_available_in_external_and_cfs_editors(self):
        items, _ = self.render("filament", ["slot:E", "edit_slot"])
        self.assertTrue(any(i["action"] == "reset_spool" for i in items))
        _, commands = self.render(
            "filament", ["slot:E", "edit_slot", "reset_spool", "confirmed"]
        )
        self.assertEqual(
            commands[-1], {"action": "slot_clear", "slot": "E", "confirm": True}
        )
        items, _ = self.render("filament", ["slot:A", "edit_slot"])
        self.assertTrue(any(i["action"] == "reset_spool" for i in items))
        _, commands = self.render(
            "filament", ["slot:A", "edit_slot", "reset_spool", "confirmed"]
        )
        self.assertEqual(
            commands[-1], {"action": "slot_clear", "slot": "A", "confirm": True}
        )

    def test_preview_waits_for_new_file_instead_of_accepting_an_old_snapshot(self):
        data = copy.deepcopy(self.data)
        data["platform"]["file_thumbnail"] = str(
            Path(__file__).resolve().parents[1]
            / "developer-tools/fixtures/assets/demo-part.png"
        )
        items, _ = self.render(
            "files",
            ['file_open:{"action":"file","path":"next.gcode"}', {"snapshot": data}],
            data,
        )
        self.assertFalse(
            next(i for i in items if i["action"] == "review_mapping")["enabled"]
        )
        image = Image.open(self.path / "screen.ppm").convert("RGB")
        purple = sum(
            1
            for r, g, b in image.crop((120, 50, 430, 360)).get_flattened_data()
            if b > r * 1.1 and r > g * 1.1
        )
        self.assertLess(purple, 100)

    def test_shared_dialog_messages_are_centered_in_the_body_area(self):
        cases = [
            ("move", ["motor_toggle"], self.data),
            ("files", ['file_delete:{"action":"file","path":"part.gcode"}'], self.data),
        ]
        failed = copy.deepcopy(self.data)
        failed["notices"] = [
            {
                "id": 1,
                "title": "Wi-Fi connection failed",
                "detail": "Could not connect. Check the password and try again.",
                "fault": True,
            }
        ]
        cases.append(("wifi", [], failed))
        for page, actions, data in cases:
            with self.subTest(page=page):
                self.render(page, actions, data)
                image = Image.open(self.path / "screen.ppm").convert("RGB")
                pixels = [
                    (x, y)
                    for y in range(138, 326)
                    for x in range(148, 652)
                    if all(v > 190 for v in image.getpixel((x, y)))
                ]
                self.assertTrue(pixels)
                self.assertLessEqual(
                    abs(
                        (min(x for x, y in pixels) + max(x for x, y in pixels)) / 2
                        - 400
                    ),
                    4,
                )
                self.assertLessEqual(
                    abs(
                        (min(y for x, y in pixels) + max(y for x, y in pixels)) / 2
                        - 232
                    ),
                    4,
                )

    def test_fan_release_keeps_its_position_through_pending_and_stale_snapshots(self):
        self.render("fans", [{"tap": [500, 150]}])
        first = Image.open(self.path / "screen.ppm").crop((120, 140, 760, 170)).copy()
        data = copy.deepcopy(self.data)
        op = {
            "id": 1,
            "action": "fan",
            "fan": "part",
            "label": "Setting fan",
            "resources": ["fan:part"],
        }
        data.update(operation=op, operations=[op])
        data["availability"] = action_policy.availability(data["status"], True, [op])
        _, commands = self.render("fans", [{"tap": [500, 150]}, {"snapshot": data}])
        later = Image.open(self.path / "screen.ppm").crop((120, 140, 760, 170))
        self.assertEqual(first.tobytes(), later.tobytes())
        self.assertEqual(len(commands), 1)

    def test_long_error_scrolls_without_moving_header_or_buttons(self):
        data = copy.deepcopy(self.data)
        data["notices"] = [
            {
                "id": 1,
                "title": "Printer error",
                "detail": "\n".join(
                    f"Line {i:02d}: diagnostic information" for i in range(30)
                ),
                "fault": True,
            }
        ]
        self.render("home", data=data)
        first = Image.open(self.path / "screen.ppm").copy()
        steps = [
            {"queue_touch": [400, 300, True]},
            {"queue_touch": [400, 230, True]},
            {"queue_touch": [400, 160, True]},
            {"queue_touch": [400, 150, False]},
        ]
        self.render("home", steps, data)
        later = Image.open(self.path / "screen.ppm")
        self.assertNotEqual(
            first.crop((150, 136, 650, 326)).tobytes(),
            later.crop((150, 136, 650, 326)).tobytes(),
        )
        self.assertEqual(
            first.crop((140, 75, 660, 120)).tobytes(),
            later.crop((140, 75, 660, 120)).tobytes(),
        )
        self.assertEqual(
            first.crop((280, 342, 520, 402)).tobytes(),
            later.crop((280, 342, 520, 402)).tobytes(),
        )

    def test_filament_notice_ok_selects_the_affected_slot_and_cfs_tab(self):
        data = copy.deepcopy(self.data)
        data["notices"] = [
            {
                "id": 1,
                "title": "Filament changed",
                "detail": "Enter filament details for slot D.",
                "fault": False,
                "filament_slot": "D",
            }
        ]
        items, _ = self.render("home", data=data)
        action = next(
            i["action"] for i in items if i["action"].startswith("filament_notice:")
        )
        items, commands = self.render(
            "home", [action, "edit_slot", "reset_spool", "confirmed"], data
        )
        self.assertEqual(
            commands[-1], {"action": "slot_clear", "slot": "D", "confirm": True}
        )
        self.assertTrue(any(c == {"action": "ack", "id": 1} for c in commands))

    def test_removal_retracts_an_obsolete_insert_notice(self):
        data = copy.deepcopy(self.data)
        data["notices"] = [
            {
                "id": 1,
                "title": "Filament changed",
                "detail": "Enter filament details.",
                "fault": False,
                "filament_slot": "A",
            }
        ]
        removed = copy.deepcopy(data)
        removed["notices"] = []
        items, _ = self.render("home", [{"snapshot": removed}], data)
        self.assertFalse(any(i["action"].startswith("filament_notice:") for i in items))
        self.assertTrue(any(i["action"] == "open_files" for i in items))

    def test_spool_notice_preserves_unsaved_editor_slot_until_discard_is_confirmed(
        self,
    ):
        notice = copy.deepcopy(self.data)
        notice["notices"] = [
            {
                "id": 1,
                "title": "Filament changed",
                "detail": "Enter details for slot D.",
                "fault": False,
                "filament_slot": "D",
            }
        ]
        initial = [
            "slot:A",
            "edit_slot",
            "color:FF0000",
            {"snapshot": notice},
            'filament_notice:{"id":1,"slot":"D"}',
        ]
        _, commands = self.render("filament", initial + ["close", "save_slot"])
        self.assertEqual(commands[-1]["slot"], "A")
        _, commands = self.render(
            "filament", initial + ["confirmed", "edit_slot", "reset_spool", "confirmed"]
        )
        self.assertEqual(
            commands[-1], {"action": "slot_clear", "slot": "D", "confirm": True}
        )
