"""Render real widgets to distinguish filament presence, selection and targets."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

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
        self.assertEqual(a.getpixel((600, 400)), b.getpixel((600, 400)))
        self.assertIsNotNone(
            ImageChops.difference(a, b).crop((620, 410, 655, 445)).getbbox()
        )

    def test_current_readings_are_centered_for_different_digit_counts(self):
        data = copy.deepcopy(self.data)
        for h, current in zip(data["heaters"], (200, 58, 9)):
            h.update(current=current, target=0)
        image = self.frame("home", data)
        bounds = []
        for y in (43, 133, 223):
            crop = image.crop((637, y, 769, y + 32))
            bounds.append(
                crop.convert("L").point(lambda v: 255 if v > 100 else 0).getbbox()
            )
        for bounds_item in bounds:
            self.assertLessEqual(abs((bounds_item[0] + bounds_item[2]) / 2 - 66), 1)
        self.assertLessEqual(max(b[1] for b in bounds) - min(b[1] for b in bounds), 1)

    def test_target_is_only_shown_while_approaching_it(self):
        data = copy.deepcopy(self.data)
        for h in data["heaters"]:
            h.update(current=200, target=0)
        off = self.frame("home", data)
        for h in data["heaters"]:
            h["target"] = 200
        holding = self.frame("home", data)
        for y in (43, 133, 223):
            region = (637, y, 769, y + 32)
            self.assertIsNone(
                ImageChops.difference(off, holding).crop(region).getbbox()
            )
        for target in (210, 180):
            for h in data["heaters"]:
                h["target"] = target
            approaching = self.frame("home", data)
            # Both heating and cooling retain the destination, on one line.
            for y in (43, 133, 223):
                region = (637, y, 769, y + 32)
                self.assertIsNotNone(
                    ImageChops.difference(holding, approaching).crop(region).getbbox()
                )
                self.assertIsNone(
                    ImageChops.difference(holding, approaching)
                    .crop((637, y + 29, 769, y + 32))
                    .getbbox()
                )

    def test_print_name_hides_gcode_suffix_and_directory_only_in_display(self):
        data = copy.deepcopy(self.data)
        data["status"]["print_stats"].update(state="printing", filename="Tray")
        baseline = self.frame("home", data)
        for filename in ("folder/Tray.gcode", "Tray.GCODE", "Tray.gco", "Tray.g"):
            data["status"]["print_stats"]["filename"] = filename
            image = self.frame("home", data)
            self.assertIsNone(ImageChops.difference(baseline, image).getbbox())
            self.assertEqual(data["status"]["print_stats"]["filename"], filename)
