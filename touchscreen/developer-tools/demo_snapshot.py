#!/usr/bin/env python3
"""Write synthetic preview data without contacting a printer or starting services."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import action_policy
from service import Service


def snapshot(printing=False):
    with tempfile.TemporaryDirectory(prefix="openklipper-demo-") as directory:
        config = Path(directory) / "screen.cfg"
        config.write_text("[brand]\nmodel: Demo 3D printer\n[screen]\n")
        service = Service(config)
        service.connected = True
        service.settings = {
            "extruder": {"min_temp": 0, "max_temp": 300, "min_extrude_temp": 170},
            "heater_bed": {"min_temp": 0, "max_temp": 110},
            "hark_chamber": {"max_target": 60},
            "heater_generic chamber_heater": {"min_temp": 0, "max_temp": 80},
            "stepper_x": {"position_min": 0, "position_max": 300},
            "stepper_y": {"position_min": 0, "position_max": 300},
            "stepper_z": {"position_min": 0, "position_max": 300},
            "bed_mesh": {"mesh_min": [10, 10], "mesh_max": [290, 290]},
            "z_tilt": {},
            "resonance_tester": {},
            "k2_belt_tension": {},
            "k2_cfs": {},
            "fan": {},
            "fan_generic auxiliary_fan": {},
            "fan_generic exhaust_fan": {},
            "heater_fan hotend_fan": {},
        }
        service.status = {
            "webhooks": {"state": "ready"},
            "print_stats": {
                "state": "printing" if printing else "standby",
                "filename": "Demo print.gcode" if printing else "",
                "print_duration": 1800 if printing else 0,
            },
            "virtual_sdcard": {"progress": 0.4 if printing else 0},
            "toolhead": {"homed_axes": "xyz"},
            "gcode_move": {
                "position": [150, 150, 10],
                "gcode_position": [150, 150, 10],
                "speed_factor": 1,
                "extrude_factor": 1,
                "homing_origin": [0, 0, 0],
            },
            "extruder": {"temperature": 29, "target": 0, "can_extrude": False},
            "heater_bed": {"temperature": 25, "target": 0},
            "temperature_sensor chamber_temperature": {"temperature": 24},
            "hark_chamber": {"requested": 0},
            "output_pin chamber_led": {"value": 1},
            "fan": {"speed": 0.5},
            "fan_generic auxiliary_fan": {"speed": 0.25},
            "fan_generic exhaust_fan": {"speed": 0},
            "heater_fan hotend_fan": {"speed": 0},
            "k2_cfs": {
                "fitted": True,
                "online": True,
                "active_slot": "A",
                "temperature": 24,
                "humidity": 25,
            },
            "hark_cfs_slots": {
                "map_targets": ["A", "B", "C", "D", "E"],
                "map_tools": ["T0", "T1", "T2", "T3"],
                "slots": {
                    letter: {
                        "loaded": True,
                        "type": "PLA",
                        "name": "Demo PLA",
                        "color": color,
                        "nozzle_temp": 220,
                    }
                    for letter, color in zip(
                        "ABCD", ["5035CE", "E34C55", "4F9F73", "EDCE52"]
                    )
                },
            },
        }
        service.materials = [{"name": "Demo PLA", "type": "PLA", "nozzle_temp": 220}]
        service.files = [
            {"path": "Demo print.gcode", "modified": 1700000000, "size": 10000}
        ]
        service.selected_file = "Demo print.gcode"
        service.metadata[service.selected_file] = {
            "estimated_time": 4500,
            "layer_count": 250,
            "filament_weight_total": 30,
            "filaments": [{"tool": 0, "type": "PLA", "colour": "5035CE"}],
            "suggested_mapping": {"0": "A"},
        }
        service.platform = {
            "timezone": "Etc/GMT+5",
            "serial": "DEMO",
            "free": "12 GB free",
            "usb": {"mounted": True, "model": "Demo USB drive"},
            "usb_free": "8 GB free",
            "timelapse": {"enabled": True},
            "recordings": [{"path": "Demo timelapse.mp4"}],
            "interfaces": {"ethernet": {"address": "192.0.2.20"}},
            "wifi": {
                "ssid": "Demo network",
                "ip_address": "192.0.2.21",
                "rssi": "-52 dBm",
            },
        }
        service.wifi.networks = [
            ["Demo network", -52, True],
            ["Guest network", -68, False],
        ]
        return service.snapshot()


SCENARIOS = (
    "idle",
    "heated",
    "heating",
    "empty-extruder",
    "printing",
    "error",
    "loading",
    "homing",
    "meshing",
    "load",
    "unload",
    "notice",
    "recovery",
    "disconnected",
)


def preview_scenario(name="idle", printing=False):
    data = snapshot(printing or name in ("printing", "heating"))
    data["platform"].update(
        thumbnail=str(ROOT / "developer-tools/fixtures/demo-part.png"),
        file_thumbnail=str(ROOT / "developer-tools/fixtures/demo-part.png"),
    )
    data["status"]["exclude_object"] = {
        "objects": [
            {
                "name": "Part_" + str(i + 1),
                "center": [x + 25, 150],
                "polygon": [[x, 75], [x + 50, 75], [x + 50, 225], [x, 225]],
            }
            for i, x in enumerate((40, 125, 210))
        ],
        "excluded_objects": [],
        "current_object": "Part_1",
    }
    if name in ("printing", "heating"):
        data["status"]["filament_switch_sensor filament_sensor"] = {
            "filament_detected": True
        }
        data["status"]["print_stats"]["info"] = {
            "current_layer": 100,
            "total_layer": 250,
        }
        data["print_stage"] = "Heating" if name == "heating" else "Printing"
        for heater, current in zip(data["heaters"], (180, 45, 35)):
            target = {"extruder": 210, "heater_bed": 60, "chamber": 55}[heater["key"]]
            current = current if name == "heating" else target
            heater.update(current=current, target=target)
            if heater["key"] != "chamber":
                data["status"][heater["key"]].update(temperature=current, target=target)
        data["status"]["hark_chamber"]["requested"] = 55
        data["status"]["extruder"]["can_extrude"] = True
        if name == "heating":
            data["status"]["print_stats"].update(
                print_duration=0, info={"current_layer": 0, "total_layer": 250}
            )
            data["status"]["virtual_sdcard"]["progress"] = 0
        data["availability"] = action_policy.availability(data["status"], True, [])
    operations = {
        "loading": ("file", "Reading the selected print file…"),
        "homing": ("home", "Homing X, Y and Z…"),
        "meshing": ("calibrate", "Measuring the bed mesh…"),
        "load": ("load", "Loading selected filament from CFS…"),
        "unload": ("unload", "Returning filament to CFS…"),
    }
    if name == "empty-extruder":
        data["status"]["k2_cfs"]["active_slot"] = None
        data["status"]["filament_switch_sensor filament_sensor"] = {
            "filament_detected": False
        }
        for record in data["slots"]:
            if record["slot"] == "E":
                record["loaded"] = False
        data["availability"] = action_policy.availability(data["status"], True, [])
    if name == "heated":
        for key, temperature in (
            ("extruder", 220),
            ("heater_bed", 60),
            ("chamber", 45),
        ):
            for heater in data["heaters"]:
                if heater["key"] == key:
                    heater.update(current=temperature, target=temperature)
            if key != "chamber":
                data["status"][key].update(temperature=temperature, target=temperature)
        data["status"]["extruder"]["can_extrude"] = True
        data["status"]["hark_chamber"]["requested"] = 45
    if name in operations:
        action, detail = operations[name]
        data["operation"] = {
            "id": 1,
            "action": action,
            "label": action_policy.describe({"action": action}).label,
            "detail": detail,
        }
    elif name == "error":
        data["error"] = (
            "No trigger on probe after full movement. Check the nozzle and bed before retrying."
        )
        data["status"]["webhooks"]["state"] = "error"
    elif name == "notice":
        data["notices"] = [
            {
                "id": 1,
                "title": "USB drive ejected",
                "detail": "You can remove the USB drive.",
                "fault": False,
            }
        ]
    elif name == "recovery":
        data["status"]["hark_plr"] = {"available": True}
    elif name == "disconnected":
        data["connected"] = False
    if data.get("operation"):
        operation = data["operation"]
        operation["resources"] = sorted(
            action_policy.describe({"action": operation["action"]}).resources
        )
        operation["interrupt"] = False
        data["operations"] = [operation]
        data["availability"] = action_policy.availability(
            data["status"], data["connected"], data["operations"]
        )
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--printing", action="store_true")
    parser.add_argument("--scenario", choices=SCENARIOS, default="idle")
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(preview_scenario(args.scenario, args.printing), indent=2) + "\n"
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
