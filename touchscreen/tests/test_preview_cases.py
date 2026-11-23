"""Portable bundled cases and the documented desktop launcher, without a display."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "developer-tools"
spec = importlib.util.spec_from_file_location(
    "preview_case_data", TOOLS / "demo_snapshot.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class PreviewCaseTests(unittest.TestCase):
    def test_every_case_materializes_with_bundled_artwork_and_actions(self):
        for name in demo.available_cases():
            with self.subTest(case=name):
                data = demo.preview_case(name)
                self.assertEqual(data["preview"]["case"], name)
                self.assertIsInstance(data["preview"]["actions"], list)
                self.assertTrue(Path(data["platform"]["printer_image"]).is_file())
                self.assertTrue(Path(data["platform"]["file_thumbnail"]).is_file())
                recipe = json.loads((demo.CASES / (name + ".json")).read_text())
                self.assertFalse(Path(recipe["printer_image"]).is_absolute())

    def test_multi_cfs_mapping_and_no_cfs_cases_keep_distinct_capabilities(self):
        multi = demo.preview_case("mapping")
        self.assertEqual(multi["cfs_units"], 4)
        self.assertEqual(len(multi["slots"]), 16)
        self.assertEqual(len(multi["metadata"]["filaments"]), 3)
        self.assertEqual(multi["preview"]["actions"], ["review_mapping"])
        single = demo.preview_case("no-cfs")
        self.assertFalse(single["status"]["k2_cfs"]["fitted"])
        self.assertEqual(single["cfs_units"], 0)
        self.assertEqual(single["slots"], [])

    def test_unknown_case_is_rejected(self):
        with self.assertRaises(ValueError):
            demo.preview_case("../not-a-case")

    def test_case_listing_needs_no_display_or_host_binary(self):
        output = subprocess.check_output(
            ["sh", str(TOOLS / "preview.sh"), "--list"], text=True
        )
        for name in ("idle", "printing", "heating", "multi-cfs", "wifi-password"):
            self.assertIn(name + " — ", output)


@unittest.skipUnless(
    (ROOT / "out/host/openklipper-screen").exists(), "Build the host target first"
)
class PreviewLauncherTests(unittest.TestCase):
    def test_every_bundled_case_launches_headlessly_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for name in demo.available_cases():
                with self.subTest(case=name):
                    output = directory / "previews" / (name + ".ppm")
                    result = subprocess.run(
                        [
                            "sh",
                            str(TOOLS / "preview.sh"),
                            "--case",
                            name,
                            "--snapshot",
                            str(output),
                        ],
                        check=False,
                        cwd=directory,
                        env=dict(os.environ, DISPLAY=""),
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertTrue(
                        output.read_bytes().startswith(b"P6\n800 480\n255\n")
                    )

    def test_raw_scenario_and_exported_snapshot_commands_still_work(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            snapshot = directory / "idle.json"
            subprocess.run(
                [
                    "python3",
                    str(TOOLS / "demo_snapshot.py"),
                    str(snapshot),
                    "--case",
                    "idle",
                ],
                check=True,
                capture_output=True,
            )
            for arguments in (
                ["--scenario", "heated", "--page", "move"],
                [str(snapshot)],
                ["--case", "idle", "--page", "fans"],
            ):
                output = directory / "screen.ppm"
                subprocess.run(
                    [
                        "sh",
                        str(TOOLS / "preview.sh"),
                        *arguments,
                        "--snapshot",
                        str(output),
                    ],
                    cwd=directory,
                    env=dict(os.environ, DISPLAY=""),
                    check=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertTrue(output.is_file())
