"""End-to-end native widgets driven by the same backend action contract."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import copy

# Only reuse fixture/render helpers, not the base class's test methods.
import unittest

import action_policy
import test_native_ui
from PIL import Image


class FlowTests(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def active(self, action):
        data = copy.deepcopy(self.data)
        spec = action_policy.describe({"action": action})
        operation = {
            "id": 1,
            "action": action,
            "label": spec.label,
            "resources": sorted(spec.resources),
            "interrupt": False,
            "detail": "Current operation",
        }
        data["operation"] = operation
        data["operations"] = [operation]
        data["availability"] = action_policy.availability(
            data["status"], True, data["operations"]
        )
        return data

    def test_homing_keeps_motion_visible_disabled_and_navigation_live(self):
        data = self.active("home")
        items, commands = self.render("move", data=data)
        for item in items:
            if item["action"].startswith(("jog:", "position:")):
                self.assertFalse(item["enabled"])
            if item["action"].startswith("route:"):
                self.assertTrue(item["enabled"])
        self.assertTrue(
            any(i["action"] == "route:history" and i["rect"][1] >= 400 for i in items)
        )
        items, commands = self.render("move", [{"tap": [490, 48]}], data=data)
        self.assertTrue(any(i["action"] == "load" for i in items))
        self.assertFalse(next(i for i in items if i["action"] == "load")["enabled"])
        self.assertEqual(commands, [])
        items, commands = self.render("move", ["jog:X:1"], data=data)
        self.assertEqual(commands, [])

    def test_filament_operation_locks_nozzle_but_not_bed(self):
        data = self.active("load")
        items, _ = self.render("heat", data=data)
        by_action = {i["action"]: i for i in items}
        self.assertFalse(by_action["temperature:extruder"]["enabled"])
        self.assertTrue(by_action["temperature:heater_bed"]["enabled"])
        _, commands = self.render("fans", [{"tap": [500, 150]}], data=data)
        self.assertEqual(commands, [])
        _, commands = self.render("fans", [{"tap": [500, 276]}], data=data)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["fan"], "side")

    def test_completion_restores_controls_without_changing_current_tab(self):
        active = self.active("home")
        done = copy.deepcopy(self.data)
        done["operation"] = None
        done["operations"] = []
        done["availability"] = action_policy.availability(done["status"], True, [])
        items, commands = self.render("move", [{"snapshot": done}], data=active)
        self.assertTrue(next(i for i in items if i["action"] == "jog:X:1")["enabled"])
        self.assertFalse(
            any(i["action"] == "route:history" and i["rect"][1] >= 400 for i in items)
        )
        self.assertEqual(commands, [])
        done["results"] = [
            {"id": 2, "action": "temperature", "success": True},
            {"id": 1, "action": "slot_save", "success": True},
        ]
        items, commands = self.render("files", [{"snapshot": done}], data=active)
        self.assertTrue(any(i["action"].startswith("file_open:") for i in items))
        self.assertTrue(all(c["action"] == "files" for c in commands))

    def test_accepted_save_does_not_prompt_to_discard_while_navigating(self):
        active = self.active("slot_save")
        active["operation"]["slot"] = "D"
        actions = [
            "edit_slot",
            "route:colors",
            "color:5035ce",
            {"snapshot": active},
            "route:files",
        ]
        items, commands = self.render("filament", actions, data=self.data)
        self.assertFalse(any(i["action"] == "confirmed" for i in items))
        self.assertTrue(any(i["action"].startswith("file_open:") for i in items))
        self.assertTrue(all(c["action"] == "files" for c in commands))

    def test_activity_animation_survives_unchanged_live_snapshots(self):
        data = self.active("home")
        self.render("heat", data=data)
        first = Image.open(self.path / "screen.ppm").crop((20, 400, 68, 435)).copy()
        self.render("heat", [{"snapshot": data} for _ in range(15)], data=data)
        later = Image.open(self.path / "screen.ppm").crop((20, 400, 68, 435))
        self.assertNotEqual(first.tobytes(), later.tobytes())

    def test_open_temperature_dialog_tracks_resource_locks(self):
        active = self.active("load")
        items, _ = self.render(
            "home", ["temperature:extruder", {"snapshot": active}], data=self.data
        )
        self.assertFalse(next(i for i in items if i["action"] == "temp_set")["enabled"])
        self.assertFalse(
            next(i for i in items if i["action"] == "temp_input")["enabled"]
        )
        items, _ = self.render(
            "home",
            ["temperature:extruder", {"snapshot": active}, {"snapshot": self.data}],
            data=self.data,
        )
        self.assertTrue(next(i for i in items if i["action"] == "temp_set")["enabled"])

    def test_resource_unlock_does_not_enable_invalid_numeric_value(self):
        active = self.active("load")
        actions = [
            "temperature:extruder",
            "temp_input",
            "number:3",
            "number:5",
            "number:1",
            {"snapshot": active},
            {"snapshot": self.data},
        ]
        items, commands = self.render("home", actions, data=self.data)
        self.assertFalse(
            next(i for i in items if i["action"] == "number:Set")["enabled"]
        )
        self.assertEqual(commands, [])

    def test_footer_buttons_use_larger_fixed_geometry(self):
        items, _ = self.render("file")
        for action in ("back", "review_mapping"):
            footers = [i for i in items if i["action"] == action and i["rect"][1] > 380]
            self.assertEqual(len(footers), 1)
            self.assertGreaterEqual(footers[0]["rect"][2], 296)
            self.assertGreaterEqual(footers[0]["rect"][3], 64)

    def test_present_spools_and_empty_extruder_are_different_states(self):
        data = copy.deepcopy(self.data)
        data["status"]["k2_cfs"]["active_slot"] = None
        data["status"]["filament_switch_sensor filament_sensor"][
            "filament_detected"
        ] = False
        data["availability"] = action_policy.availability(data["status"], True, [])
        items, commands = self.render("filament", data=data)
        self.assertTrue(next(i for i in items if i["action"] == "load")["enabled"])
        self.assertFalse(next(i for i in items if i["action"] == "unload")["enabled"])
        _, commands = self.render("filament", ["load"], data=data)
        self.assertEqual(commands, [{"action": "load", "slot": "A"}])

    def test_spool_ring_touch_selects_the_slot_then_loads_that_slot(self):
        # Slot C's decorative ring used to intercept the card's click.
        _, commands = self.render("filament", [{"tap": [425, 195]}, "load"])
        self.assertEqual(commands, [{"action": "load", "slot": "C"}])
        data = copy.deepcopy(self.data)
        for record in data["slots"]:
            if record["slot"] == "C":
                record["loaded"] = False
        items, commands = self.render("filament", ["slot:C"], data=data)
        self.assertFalse(any(i["action"] == "load" for i in items))

    def test_mapping_compares_actual_colors_and_supports_multiple_boxes(self):
        data = copy.deepcopy(self.data)
        data["metadata"]["filaments"][0]["colour"] = "E34C55"
        self.render("file", ["review_mapping"], data=data)
        image = Image.open(self.path / "screen.ppm").convert("RGB")
        self.assertEqual(image.getpixel((135, 152)), (227, 76, 85))
        self.assertEqual(image.getpixel((484, 152)), (80, 53, 206))
        for unit in (2, 3):
            for letter in "ABCD":
                data["slots"].append(dict(data["slots"][0], slot=str(unit) + letter))
        data["cfs_units"] = 3
        items, _ = self.render("file", ["review_mapping", "mapping_pick:0"], data=data)
        choices = [i for i in items if i["action"].startswith("mapping_set:")]
        self.assertEqual(len(choices), 12)
        self.assertTrue(any(i["action"] == "mapping_set:3D" for i in choices))

    def test_long_mapping_list_scrolls_to_last_filament_and_preserves_footer(self):
        data = copy.deepcopy(self.data)
        data["metadata"]["filaments"] = [
            {"tool": i, "type": "PLA", "colour": "E34C55"} for i in range(24)
        ]
        data["metadata"]["suggested_mapping"] = {str(i): "D" for i in range(24)}
        actions = ["review_mapping"]
        # Actual touch drags exercise LVGL scrolling rather than a route shortcut.
        for _ in range(18):
            actions += [{"queue_touch": [400, y, True]} for y in range(360, 109, -25)]
            actions += [{"queue_touch": [400, 110, False]}, {}, {}]
        items, commands = self.render("file", actions, data=data)
        last = next(i for i in items if i["action"] == "mapping_pick:23")
        self.assertLess(last["rect"][1], 388)
        self.assertGreater(last["rect"][1] + last["rect"][3], 88)
        start = next(i for i in items if i["action"] == "start_print")
        self.assertGreaterEqual(start["rect"][1], 400)
        self.assertEqual(commands, [])

    def test_mapping_without_slicer_material_still_allows_tool_zero(self):
        data = copy.deepcopy(self.data)
        data["metadata"]["filaments"] = []
        items, _ = self.render("file", ["review_mapping"], data=data)
        self.assertTrue(any(i["action"] == "mapping_pick:0" for i in items))
        _, commands = self.render(
            "file",
            ["review_mapping", "mapping_pick:0", "mapping_set:A", "start_print"],
            data=data,
        )
        self.assertEqual(
            commands,
            [
                {
                    "action": "start",
                    "path": "part.gcode",
                    "mapping": {"0": "A"},
                    "calibrate": False,
                }
            ],
        )

    def test_refill_switches_are_centered_in_their_rows(self):
        items, _ = self.render("refill")
        import json

        keys = ("cfs_insertion_read", "cfs_startup_read", "cfs_auto_refill")
        for index, key in enumerate(keys):
            item = next(
                i
                for i in items
                if i["action"].startswith("{")
                and json.loads(i["action"]).get("key") == key
            )
            r = item["rect"]
            self.assertEqual(r[1] + r[3] / 2, 129 + index * 94)

    def test_cfs_switches_update_local_preview_like_timelapse(self):
        _, commands = self.render("refill", [{"tap": [700, 130]}, {"tap": [700, 130]}])
        self.assertEqual(
            commands,
            [
                {"action": "preference", "key": "cfs_insertion_read", "value": False},
                {"action": "preference", "key": "cfs_insertion_read", "value": True},
            ],
        )

    def test_existing_editor_locks_save_fields_while_filament_is_busy(self):
        active = self.active("load")
        items, commands = self.render(
            "filament", ["edit_slot", {"snapshot": active}], data=self.data
        )
        for action in (
            "route:materials",
            "route:colors",
            "draft_temperature",
            "save_slot",
        ):
            self.assertFalse(next(i for i in items if i["action"] == action)["enabled"])
        self.assertEqual(commands, [])

    def test_color_field_shows_swatch_and_language_is_not_a_dead_picker(self):
        self.render("filament", ["edit_slot"])
        image = Image.open(self.path / "screen.ppm").convert("RGB")
        # The Color row has a circular color sample beside its chevron.
        self.assertEqual(image.getpixel((665, 232)), (80, 53, 206))
        items, _ = self.render("system")
        self.assertFalse(any(i["action"] == "route:language" for i in items))
