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
                thumbnail = data["platform"].get("file_thumbnail")
                if thumbnail:
                    self.assertTrue(Path(thumbnail).is_file())
                else:
                    self.assertIn(name, ("file-reading", "file-no-preview"))
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

    def test_mesh_cases_include_realistic_flat_empty_and_busy_states(self):
        data = demo.preview_case("mesh")
        mesh = data["status"]["bed_mesh"]
        self.assertEqual(data["preview"]["page"], "mesh")
        self.assertEqual(len(mesh["probed_matrix"]), 9)
        self.assertTrue(all(len(row) == 9 for row in mesh["probed_matrix"]))
        values = [value for row in mesh["probed_matrix"] for value in row]
        self.assertLess(min(values), 0)
        self.assertGreater(max(values), 0)
        self.assertEqual(
            demo.preview_case("mesh-empty")["status"]["bed_mesh"]["probed_matrix"], []
        )
        unloaded = demo.preview_case("mesh-unloaded")["status"]["bed_mesh"]
        self.assertEqual(unloaded["probed_matrix"], [])
        self.assertEqual(unloaded["profile_name"], "")
        self.assertEqual(len(unloaded["profiles"]), 3)
        flat = demo.preview_case("mesh-flat")["status"]["bed_mesh"]["probed_matrix"]
        self.assertEqual(len({value for row in flat for value in row}), 1)
        busy = demo.preview_case("meshing")
        self.assertEqual(busy["operations"][0]["kind"], "mesh")
        self.assertFalse(
            demo.preview_case("mesh-printing")["availability"]["calibrate"]["enabled"]
        )

    def test_motor_cases_distinguish_enabled_and_unhomed_disabled_axes(self):
        on = demo.preview_case("motors-on")["status"]
        off = demo.preview_case("motors-off")["status"]
        self.assertTrue(all(on["stepper_enable"]["steppers"].values()))
        self.assertFalse(any(off["stepper_enable"]["steppers"].values()))
        self.assertEqual(off["toolhead"]["homed_axes"], "")

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
