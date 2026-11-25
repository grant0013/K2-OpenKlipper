"""Contextual sidebar activity across printer and background operations."""

import copy
import unittest

import test_native_ui
from PIL import Image


class SidebarActivity(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def state(self, action=None):
        data = copy.deepcopy(self.data)
        data["operations"] = []
        data["operation"] = None
        if action:
            op = {"id": 1, "action": action, "label": "Test operation"}
            data["operations"] = [op]
            data["operation"] = op
        return data

    def badge(self, items):
        return any(i["action"] == "route:history" and i["rect"][0] < 10 for i in items)

    def test_all_real_operations_have_explicit_activity(self):
        actions = (
            "home",
            "jog",
            "position",
            "extrude",
            "load",
            "unload",
            "calibrate",
            "calibration_result",
            "save_config",
            "slot_save",
            "files",
            "file",
            "recordings",
            "delete_file",
            "delete_recording",
            "export",
            "usb_eject",
            "usb_format",
            "start",
            "pause",
            "resume",
            "cancel",
            "plr",
            "restart",
            "firmware_restart",
            "wifi_scan",
            "wifi_join",
        )
        for action in actions:
            with self.subTest(action=action):
                items, _ = self.render("heat", data=self.state(action))
                self.assertTrue(self.badge(items))

    def test_idle_instant_setters_and_unknown_operations_have_no_badge(self):
        for action in (
            None,
            "temperature",
            "fan",
            "light",
            "motors",
            "heaters_off",
            "tune",
            "exclude",
            "timelapse",
            "preference",
            "timezone",
            "home_files",
            "move_step",
            "unknown",
        ):
            with self.subTest(action=action):
                items, _ = self.render("heat", data=self.state(action))
                self.assertFalse(self.badge(items))

    def test_print_state_does_not_create_activity_and_home_owns_print_operations(self):
        data = self.state()
        for state in ("printing", "paused"):
            data["status"]["print_stats"]["state"] = state
            for page in ("home", "heat", "files"):
                items, _ = self.render(page, data=data)
                self.assertFalse(self.badge(items))
        data["status"]["print_stats"]["state"] = "printing"
        for action in ("start", "pause", "resume", "cancel", "plr"):
            op = {"id": 1, "action": action}
            data.update(operation=op, operations=[op])
            items, _ = self.render("home", data=data)
            self.assertFalse(self.badge(items))

    def test_background_file_refresh_appears_and_clears_on_any_page(self):
        idle = self.state()
        busy = self.state()
        busy["files_refresh"] = {"busy": True, "error": ""}
        for page in ("home", "heat", "files"):
            with self.subTest(page=page):
                items, _ = self.render(page, [{"snapshot": busy}], data=idle)
                self.assertTrue(self.badge(items))
                items, _ = self.render(page, [{"snapshot": idle}], data=busy)
                self.assertFalse(self.badge(items))
        busy["status"]["print_stats"]["state"] = "printing"
        items, _ = self.render("home", data=busy)
        self.assertTrue(self.badge(items))

    def test_network_progress_is_only_suppressed_on_its_own_progress_page(self):
        for action, page, message in (
            ("wifi_scan", "networks", "Scanning"),
            ("wifi_join", "wifi", "Connecting to Demo"),
        ):
            data = self.state(action)
            data["wifi"].update(busy=True, message=message)
            items, _ = self.render(page, data=data)
            self.assertFalse(self.badge(items))
            items, _ = self.render("network", data=data)
            self.assertTrue(self.badge(items))
            # The platform worker can outlive the request operation.
            data.update(operation=None, operations=[])
            items, _ = self.render("heat", data=data)
            self.assertTrue(self.badge(items))
            done = copy.deepcopy(data)
            done["wifi"]["busy"] = False
            items, _ = self.render("heat", [{"snapshot": done}], data=data)
            self.assertFalse(self.badge(items))

    def test_history_and_completed_results_do_not_duplicate_activity(self):
        data = self.state("load")
        items, _ = self.render("history", data=data)
        self.assertFalse(self.badge(items))
        data = self.state()
        data["results"] = [{"id": 1, "action": "delete_file", "success": True}]
        items, _ = self.render("files", data=data)
        self.assertFalse(self.badge(items))

    def test_hidden_latest_operation_cannot_mask_another_active_operation(self):
        data = self.state("load")
        items, _ = self.render("heat", data=data)
        first = Image.open(self.path / "screen.ppm").crop((4, 427, 80, 461)).copy()
        data["operations"].append({"id": 2, "action": "temperature"})
        data["operation"] = data["operations"][-1]
        items, _ = self.render("heat", data=data)
        later = Image.open(self.path / "screen.ppm").crop((4, 427, 80, 461))
        self.assertTrue(self.badge(items))
        self.assertEqual(first.tobytes(), later.tobytes())

    def test_files_page_prioritizes_refresh_over_unrelated_printer_work(self):
        data = self.state()
        data["files_refresh"] = {"busy": True, "error": ""}
        self.render("files", data=data)
        first = Image.open(self.path / "screen.ppm").crop((4, 427, 80, 461)).copy()
        op = {"id": 1, "action": "load"}
        data.update(operation=op, operations=[op])
        self.render("files", data=data)
        later = Image.open(self.path / "screen.ppm").crop((4, 427, 80, 461))
        self.assertEqual(first.tobytes(), later.tobytes())
