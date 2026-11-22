#!/usr/bin/env python3
"""Render the user guide's screenshots with native widgets and synthetic data."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from demo_snapshot import preview_scenario

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=ROOT / "build-host/openklipper-screen"
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
                str(Path(__file__).parent / "fixtures/demo-part.svg"),
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
            ("timers", "timers", "idle", []),
        ]
        for name, page, scenario, actions in cases:
            data = preview_scenario(scenario)
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
