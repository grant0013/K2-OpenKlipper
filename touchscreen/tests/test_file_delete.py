"""Confirmed print-file deletion, UI routing and Moonraker error handling."""

import copy
import unittest
from unittest import mock

import test_native_ui
import test_service


class FileDeleteServiceTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_confirmed_delete_clears_only_matching_file_and_metadata(self):
        self.s.files = [{"path": "part.gcode"}, {"path": "keep.gcode"}]
        self.s.selected_file = "part.gcode"
        self.s.metadata["part.gcode"] = {"test": 1}
        self.s.execute({"action": "delete_file", "path": "part.gcode", "confirm": True})
        self.mr.request.assert_called_once_with(
            "/server/files/gcodes/part.gcode", method="DELETE"
        )
        self.assertEqual(self.s.files, [{"path": "keep.gcode"}])
        self.assertEqual(self.s.selected_file, "")
        self.assertNotIn("part.gcode", self.s.metadata)

    def test_no_confirmation_or_invalid_path_never_deletes(self):
        for path in (
            "../part.gcode",
            "/part.gcode",
            "a/../part.gcode",
            "a//part.gcode",
            "printer.cfg",
            "",
            "part\ngcode",
        ):
            with self.assertRaises(ValueError):
                self.s.execute({"action": "delete_file", "path": path, "confirm": True})
        with self.assertRaises(ValueError):
            self.s.execute({"action": "delete_file", "path": "part.gcode"})
        self.mr.request.assert_not_called()

    def test_delete_rechecks_active_print_and_pause(self):
        for state in ("printing", "paused"):
            self.state["print_stats"]["state"] = state
            with self.assertRaises(RuntimeError):
                self.s.execute(
                    {"action": "delete_file", "path": "part.gcode", "confirm": True}
                )
        self.mr.request.assert_not_called()

    def test_url_encoding_and_failed_delete_retains_file(self):
        path = "parts/a #1%.gcode"
        self.s.files = [{"path": path}]
        self.mr.request.side_effect = RuntimeError("Read-only storage")
        with self.assertRaisesRegex(RuntimeError, "Read-only"):
            self.s.execute({"action": "delete_file", "path": path, "confirm": True})
        self.mr.request.assert_called_once_with(
            "/server/files/gcodes/parts/a%20%231%25.gcode", method="DELETE"
        )
        self.assertEqual(self.s.files, [{"path": path}])

    def test_http_delete_method_is_used(self):
        from service import Moonraker

        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"result": {}}'
        with mock.patch(
            "service.urllib.request.urlopen", return_value=response
        ) as urlopen:
            Moonraker("http://localhost").request(
                "/server/files/gcodes/part.gcode", method="DELETE"
            )
        self.assertEqual(urlopen.call_args.args[0].get_method(), "DELETE")

    def test_recording_delete_uses_only_published_video_root(self):
        self.s.platform["recordings"] = [{"path": "part.mp4"}, {"path": "keep.mp4"}]
        with mock.patch(
            "service.storage.list_recordings", return_value=[{"path": "part.mp4"}]
        ):
            self.s.execute(
                {"action": "delete_recording", "path": "part.mp4", "confirm": True}
            )
        self.mr.request.assert_called_once_with(
            "/server/files/timelapse/part.mp4", method="DELETE"
        )
        self.assertEqual(self.s.platform["recordings"], [{"path": "keep.mp4"}])
        with self.assertRaises(ValueError):
            self.s.execute(
                {"action": "delete_recording", "path": "part.gcode", "confirm": True}
            )
        with (
            mock.patch("service.storage.list_recordings", return_value=[]),
            self.assertRaises(ValueError),
        ):
            self.s.execute(
                {
                    "action": "delete_recording",
                    "path": "unpublished.mp4",
                    "confirm": True,
                }
            )


class FileDeleteUITests(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def test_delete_confirmation_cancel_and_ok(self):
        items, _ = self.render("files")
        delete = next(
            i["action"] for i in items if i["action"].startswith("file_delete:")
        )
        items, commands = self.render("files", [delete])
        self.assertTrue(any(i["action"] == "confirmed" for i in items))
        self.assertTrue(all(c["action"] == "files" for c in commands))
        _, commands = self.render("files", [delete, "close"])
        self.assertTrue(all(c["action"] == "files" for c in commands))
        _, commands = self.render("files", [delete, "confirmed"])
        self.assertIn(
            {"action": "delete_file", "path": "part.gcode", "confirm": True}, commands
        )
        items, commands = self.render("files", [{"tap": [735, 115]}])
        self.assertTrue(any(i["action"] == "confirmed" for i in items))
        self.assertFalse(any(c["action"] == "file" for c in commands))
        items, _ = self.render("file")
        self.assertFalse(any(i["action"].startswith("file_delete:") for i in items))

    def test_recording_list_has_same_confirmed_delete_flow(self):
        data = copy.deepcopy(self.data)
        data["platform"]["recordings"] = [{"path": "part.mp4"}, {"path": "keep.mp4"}]
        items, _ = self.render("recordings", data=data)
        delete = next(
            i["action"] for i in items if i["action"].startswith("file_delete:")
        )
        _, commands = self.render("recordings", [delete, "confirmed"], data=data)
        self.assertEqual(
            commands,
            [{"action": "delete_recording", "path": "part.mp4", "confirm": True}],
        )

    def test_delete_result_returns_to_files_without_hijacking_other_tabs(self):
        done = copy.deepcopy(self.data)
        done["files"] = []
        done["file"] = ""
        done["results"] = [{"id": 17, "action": "delete_file", "success": True}]
        items, _ = self.render("file", [{"snapshot": done}])
        self.assertTrue(any(i["action"] == "refresh_files" for i in items))
        items, _ = self.render("heat", [{"snapshot": done}])
        self.assertTrue(any(i["action"] == "temperature:extruder" for i in items))
