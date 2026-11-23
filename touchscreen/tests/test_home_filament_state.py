"""Render real widgets to distinguish filament presence, selection and targets."""

import copy
import unittest

import action_policy
import test_native_ui
from PIL import Image, ImageChops


class HomeFilamentStateTests(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def frame(self, page, data):
        self.render(page, data=data)
        return Image.open(self.path / "screen.ppm").copy()

    def test_nozzle_marker_follows_sensor_not_selected_spool(self):
        loaded = copy.deepcopy(self.data)
        loaded["status"]["k2_cfs"]["active_slot"] = "D"
        empty = copy.deepcopy(loaded)
        empty["status"]["filament_switch_sensor filament_sensor"][
            "filament_detected"
        ] = False
        before = self.frame("filament", empty)
        after = self.frame("filament", loaded)
        # D feeds the nozzle even when a different spool is selected for editing.
        self.assertIsNotNone(
            ImageChops.difference(before, after).crop((497, 278, 617, 308)).getbbox()
        )
        self.assertIsNone(
            ImageChops.difference(before, after).crop((104, 278, 224, 308)).getbbox()
        )

    def test_filament_overview_remains_visible_during_print(self):
        data = copy.deepcopy(self.data)
        data["status"]["print_stats"]["state"] = "printing"
        data["availability"] = action_policy.availability(data["status"], True, [])
        items, _ = self.render("filament", data=data)
        self.assertTrue(any(i["action"] == "slot:D" for i in items))
        for action in ("load", "unload", "edit_slot"):
            self.assertFalse(next(i for i in items if i["action"] == action)["enabled"])

    def test_light_state_changes_icon_without_selected_background(self):
        on = copy.deepcopy(self.data)
        off = copy.deepcopy(on)
        off["status"]["output_pin chamber_led"]["value"] = 0
        a = self.frame("home", on)
        b = self.frame("home", off)
        self.assertEqual(a.getpixel((650, 320)), b.getpixel((650, 320)))
        self.assertIsNotNone(
            ImageChops.difference(a, b).crop((660, 325, 695, 360)).getbbox()
        )

    def test_three_digit_current_and_target_share_right_edges_and_baseline(self):
        data = copy.deepcopy(self.data)
        for h in data["heaters"]:
            h.update(current=350, target=350)
        image = self.frame("home", data)
        # Compare identical glyphs in both columns for every heater.
        for y in (67, 158, 249):
            current = image.crop((651, y, 703, y + 30))
            target = image.crop((719, y, 771, y + 30))

            def ink_bounds(crop):
                mask = crop.convert("L").point(lambda v: 255 if v > 100 else 0)
                return mask.getbbox()

            self.assertEqual(ink_bounds(current), ink_bounds(target))
