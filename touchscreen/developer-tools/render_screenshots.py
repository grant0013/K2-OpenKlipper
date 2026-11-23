#!/usr/bin/env python3
"""Render the user guide's screenshots with native widgets and synthetic data."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from demo_snapshot import preview_case, preview_scenario

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=ROOT / "out/host/openklipper-screen"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "screenshots")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-screen-docs-") as directory:
        directory = Path(directory)
        preview = directory / "part.png"
        subprocess.run(
            [
                "convert",
                "-background",
                "none",
                str(Path(__file__).parent / "fixtures/assets/demo-part.svg"),
                "-trim",
                "+repage",
                "PNG32:" + str(preview),
            ],
            check=True,
        )
        cases = [
            ("home", "home", "printing", []),
            ("move", "move", "heated", []),
            ("filament", "filament", "idle", []),
            ("mapping", "file", "idle", ["review_mapping", "mapping_pick:0"]),
            ("files", "files", "idle", []),
            ("file", "file", "idle", []),
            ("timers", "timers", "idle", []),
            ("idle", "home", "idle", []),
            ("heating", "home", "heating", []),
            ("temperature", "home", "idle", ["temperature:extruder"]),
            ("executing", "home", "homing", []),
            ("thermals", "heat", "heating", []),
            ("fans", "fans", "idle", []),
            ("no-cfs", "filament", "no-cfs", []),
            ("multi-cfs", "filament", "multi-cfs", []),
            ("mapping-review", "file", "multi-cfs", ["review_mapping"]),
            ("tune", "tune", "printing", []),
            ("calibrate", "calibration", "idle", []),
            ("general", "settings", "idle", []),
            ("network", "network", "idle", []),
            ("timelapse", "timelapse", "idle", []),
            ("storage", "storage", "idle", []),
            ("system", "system", "idle", []),
            ("load", "filament", "load", []),
            ("unload", "filament", "unload", []),
            ("two-cfs", "filament", "multi-cfs", []),
            ("three-cfs", "filament", "multi-cfs", []),
            ("edit", "filament", "idle", ["edit_slot"]),
            ("materials", "filament", "idle", ["edit_slot", "route:materials"]),
            ("colors", "filament", "idle", ["edit_slot", "route:colors"]),
            ("numeric", "home", "idle", ["temperature:extruder", "temp_input"]),
            ("bed-temperature", "home", "idle", ["temperature:heater_bed"]),
            ("chamber-temperature", "home", "idle", ["temperature:chamber"]),
            ("objects", "objects", "printing", []),
            ("history", "history", "idle", []),
            ("errors", "errors", "idle", []),
            ("error", "home", "error", []),
            ("notice", "home", "notice", []),
            ("loading", "home", "loading", []),
            ("display", "display", "idle", []),
            ("refill", "refill", "idle", []),
            ("refill-sequence", "refill_sequence", "idle", []),
            ("ethernet", "ethernet", "idle", []),
            ("wifi", "wifi", "idle", []),
            ("networks", "networks", "idle", []),
            (
                "wifi-password",
                "networks",
                "idle",
                ['wifi_select:{"ssid":"Demo network","secured":true}'],
            ),
            ("wifi-connected", "wifi", "wifi-connected", []),
            ("wifi-failed", "wifi", "wifi-failed", []),
            ("recordings", "recordings", "idle", []),
            ("usb", "usb", "idle", []),
            ("machine", "machine", "idle", []),
            ("software", "software", "idle", []),
            ("timezone", "timezone", "idle", []),
            ("help", "help", "idle", []),
            ("restart", "restart", "idle", []),
            ("restart-klipper", "restart", "idle", ["restart:klipper"]),
            ("restart-firmware", "restart", "idle", ["restart:firmware"]),
            ("save", "save", "idle", []),
            ("calibrate-printing", "calibration", "printing", []),
            (
                "delete-file",
                "files",
                "idle",
                ['file_delete:{"action":"file","path":"Demo print.gcode"}'],
            ),
            ("stop-print", "home", "printing", ["cancel_print"]),
            ("unlock-motors", "move", "heated", ["motor_toggle"]),
            ("startup", "home", "disconnected", []),
            ("connection-lost", "filament", "idle", []),
            ("shutdown", "home", "shutdown", []),
            ("service-lost", "filament", "idle", [{"transport_lost": True}]),
        ]
        for name, page, scenario, actions in cases:
            data = (
                preview_case(scenario)
                if scenario == "no-cfs"
                else preview_scenario(scenario)
            )
            data["status"]["stepper_enable"] = {
                "steppers": {"stepper_x": True, "stepper_y": True, "stepper_z": True}
            }
            if name == "home":
                data["status"]["print_stats"].update(
                    info={"current_layer": 100, "total_layer": 250}
                )
                data["platform"].update(estimated_time=4500, thumbnail=str(preview))
                for key, value in (("extruder", 210), ("heater_bed", 60)):
                    data["status"][key].update(temperature=value, target=value)
                    for heater in data["heaters"]:
                        if heater["key"] == key:
                            heater.update(current=value, target=value)
            if name == "mapping":
                for record in data["slots"]:
                    if record["slot"] == "B":
                        record["type"] = "PETG"
                    if record["slot"] == "C":
                        record["type"] = "ASA"
            if name == "files":
                data["files"] = [
                    {"path": path, "modified": 1795387354 - i * 3600, "size": 10000}
                    for i, path in enumerate(
                        (
                            "Storage tray_PLA.gcode",
                            "Cable holder_PETG.gcode",
                            "Tool stand_ASA.gcode",
                        )
                    )
                ]
            if name in ("two-cfs", "three-cfs"):
                units = 2 if name == "two-cfs" else 3
                data["slots"] = [
                    r for r in data["slots"] if int(r["slot"][:-1]) <= units
                ]
                data["cfs_units"] = units
            if name == "files":
                data["platform"]["file_thumbnails"] = {
                    data["files"][0]["path"]: str(preview),
                    data["files"][1]["path"]: str(preview),
                }
            if name in ("history", "errors"):
                data["events"] = [
                    {
                        "id": 1,
                        "time": 1795387354,
                        "title": "Homing complete",
                        "detail": "X, Y and Z homed.",
                        "fault": False,
                    },
                    {
                        "id": 2,
                        "time": 1795387414,
                        "title": "Filament operation failed",
                        "detail": "Check the filament path before trying again.",
                        "fault": True,
                    },
                    {
                        "id": 3,
                        "time": 1795387474,
                        "title": "Printer ready",
                        "detail": "Ready for the next operation.",
                        "fault": False,
                    },
                ]
            if name == "connection-lost":
                actions = [{"snapshot": preview_scenario("disconnected")}]
            fixture = directory / "snapshot.json"
            fixture.write_text(json.dumps(data))
            steps = directory / "actions.json"
            steps.write_text(json.dumps(actions))
            snapshot = directory / "screen.ppm"
            subprocess.run(
                [
                    str(args.binary.resolve()),
                    "--assets",
                    str(ROOT / "assets/generated"),
                    "--fixture",
                    str(fixture),
                    "--page",
                    page,
                    "--actions",
                    str(steps),
                    "--snapshot",
                    str(snapshot),
                ],
                check=True,
            )
            subprocess.run(
                ["convert", str(snapshot), str(args.output / (name + ".png"))],
                check=True,
            )
    print(args.output)


if __name__ == "__main__":
    main()
