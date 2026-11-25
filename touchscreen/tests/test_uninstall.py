"""Exercise HarkScreen restoration without touching a printer or running installers."""

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "installation/uninstall.py"
spec = importlib.util.spec_from_file_location("native_uninstall", MODULE)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.screen = self.root / "native"
        self.screen.mkdir()
        (self.screen / "start.sh").write_text("#!/bin/sh\n")
        self.conf = self.root / "config"
        self.conf.mkdir()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(restore, "SCREEN", self.screen).start()
        mock.patch.object(restore, "CONF", self.conf).start()
        mock.patch.object(restore.os, "geteuid", return_value=0).start()

    def test_idle_guard_rejects_printing_paused_and_motion(self):
        for stats, busy in (("printing", False), ("paused", False), ("standby", True)):
            data = {
                "result": {
                    "status": {
                        "print_stats": {"state": stats},
                        "idle_timeout": {"state": "Printing" if busy else "Idle"},
                    }
                }
            }
            with (
                self.subTest(state=stats, busy=busy),
                mock.patch.object(
                    restore,
                    "urlopen",
                    return_value=io.BytesIO(json.dumps(data).encode()),
                ),
                self.assertRaises(RuntimeError),
            ):
                restore.ensure_idle()

    def test_download_rejects_version_that_starts_native_ui(self):
        responses = [
            io.BytesIO(b"#!/bin/sh\n"),
            io.BytesIO(b"#!/bin/sh\n"),
            io.BytesIO(b"sh /mnt/UDISK/openklipper-screen/start.sh\n"),
        ]
        with (
            mock.patch.object(restore, "urlopen", side_effect=responses),
            self.assertRaises(RuntimeError),
        ):
            restore.download_installer("test-tag", self.root)

    def test_check_does_not_stop_or_remove_anything(self):
        with (
            mock.patch.object(restore, "ensure_idle"),
            mock.patch.object(
                restore, "download_installer", return_value=self.root / "install.sh"
            ),
            mock.patch.object(restore.subprocess, "run") as run,
            mock.patch.object(restore, "stop_native") as stop,
            mock.patch.object(restore, "remove_native") as remove,
        ):
            restore.restore("test-tag", check=True)
        self.assertEqual(run.call_args.args[0][-1], "--check")
        stop.assert_not_called()
        remove.assert_not_called()
        self.assertTrue(self.screen.exists())

    def test_full_reinstall_verifies_replacement_before_removal(self):
        order = []
        with (
            mock.patch.object(restore, "ensure_idle"),
            mock.patch.object(
                restore, "download_installer", return_value=self.root / "install.sh"
            ),
            mock.patch.object(
                restore.subprocess,
                "run",
                side_effect=lambda *a, **k: order.append(
                    "preflight" if "--check" in a[0] else "install"
                ),
            ),
            mock.patch.object(
                restore, "stop_native", side_effect=lambda: order.append("stop")
            ),
            mock.patch.object(
                restore, "verify_hark", side_effect=lambda: order.append("verify")
            ),
            mock.patch.object(
                restore, "remove_native", side_effect=lambda: order.append("remove")
            ),
        ):
            restore.restore("test-tag")
        self.assertEqual(order, ["preflight", "stop", "install", "verify", "remove"])

    def test_failed_reinstall_retains_native_files(self):
        with (
            mock.patch.object(restore, "ensure_idle"),
            mock.patch.object(
                restore, "download_installer", return_value=self.root / "install.sh"
            ),
            mock.patch.object(
                restore.subprocess,
                "run",
                side_effect=[None, subprocess.CalledProcessError(1, "install"), None],
            ) as run,
            mock.patch.object(restore, "stop_native"),
            mock.patch.object(restore, "hark_running", return_value=False),
            mock.patch.object(restore, "remove_native") as remove,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            restore.restore("test-tag")
        remove.assert_not_called()
        self.assertTrue(self.screen.exists())
        self.assertEqual(run.call_args.args[0][1], str(self.screen / "start.sh"))

    def test_removal_keeps_printer_configuration(self):
        for name in (
            "native-screen.cfg",
            "native-screen-settings.json",
            "printer-overrides.cfg",
        ):
            (self.conf / name).write_text("original")
        with (
            mock.patch.object(restore, "stop_native"),
            mock.patch.object(restore, "PIDFILES", []),
            mock.patch.object(restore, "Path", return_value=self.root / "socket"),
        ):
            restore.remove_native()
        self.assertFalse(self.screen.exists())
        self.assertFalse((self.conf / "native-screen.cfg").exists())
        self.assertEqual((self.conf / "printer-overrides.cfg").read_text(), "original")

    def test_pid_identity_rejects_unrelated_process(self):
        with mock.patch.object(
            Path, "read_bytes", return_value=b"/usr/bin/python3\0other-service.py\0"
        ):
            self.assertFalse(restore.belongs_to(999999, self.screen))
        with mock.patch.object(
            Path,
            "read_bytes",
            return_value=(str(self.screen / "backend/service.py") + "\0").encode(),
        ):
            self.assertTrue(restore.belongs_to(999999, self.screen))


if __name__ == "__main__":
    unittest.main()
