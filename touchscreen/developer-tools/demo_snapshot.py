#!/usr/bin/env python3
"""Write synthetic preview data without contacting a printer or starting services."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CASES = FIXTURES / "cases"
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
            "gcode_macro START_PRINT": {"variable_screen_full_mesh": 0},
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
            {"path": path, "modified": 1700000000 - i * 3600, "size": 10000 + i * 1000}
            for i, path in enumerate(
                (
                    "Demo print.gcode",
                    "Storage tray.gcode",
                    "Cable clips.gcode",
                    "Tool stand.gcode",
                )
            )
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
            "recordings": [
                {"path": name}
                for name in ("Storage tray.mp4", "Cable clips.mp4", "Tool stand.mp4")
            ],
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
    "multi-cfs",
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
    "shutdown",
    "calibrating",
    "wifi-scanning",
    "wifi-connecting",
    "usb-ejected",
    "printing-long",
    "printing-extreme",
    "printing-status",
    "dark-preview",
    "wifi-connected",
    "wifi-failed",
)


def preview_scenario(name="idle", printing=False):
    data = snapshot(printing or name in ("printing", "heating"))
    data["platform"].update(
        estimated_time=4500,
        printer_image=str(FIXTURES / "assets/demo-printer-combo.png"),
        thumbnail=str(ROOT / "developer-tools/fixtures/assets/demo-part.png"),
        file_thumbnail=str(ROOT / "developer-tools/fixtures/assets/demo-part.png"),
        file_thumbnails={
            row["path"]: str(ROOT / "developer-tools/fixtures/assets/demo-part.png")
            for row in data["files"][:3]
        },
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
    if name == "multi-cfs":
        data = preview_scenario("heated", printing)
        palette = (
            ("5035CE", "E34C55", "4F9F73", "EDCE52"),
            ("1E88E5", "F5F5F5", "F39C12", "222222"),
            ("D15AA5", "00A6A6", "8D6E63", "ABB2BC"),
            ("FF7043", "7E57C2", "66BB6A", "90CAF9"),
        )
        records = []
        for unit, (kind, temperature) in enumerate(
            (("PLA", 220), ("PETG", 250), ("ASA", 260), ("ABS", 260)), 1
        ):
            for letter, color in zip("ABCD", palette[unit - 1]):
                records.append(
                    {
                        "slot": str(unit) + letter,
                        "loaded": True,
                        "type": kind,
                        "name": "Demo " + kind,
                        "color": color,
                        "nozzle_temp": temperature,
                    }
                )
        data["slots"] = records
        data["cfs_units"] = 4
        data["status"]["k2_cfs"]["active_slot"] = "1B"
        data["status"]["filament_switch_sensor filament_sensor"] = {
            "filament_detected": True
        }
        data["status"]["hark_cfs_slots"].update(
            slots={
                r["slot"]: {k: v for k, v in r.items() if k != "slot"} for r in records
            },
            map_targets=[r["slot"] for r in records] + ["E"],
            map_tools=["T" + str(i) for i in range(16)],
        )
        data["materials"] = [
            {"name": "Demo " + kind, "type": kind, "nozzle_temp": temperature}
            for kind, temperature in (
                ("PLA", 220),
                ("PETG", 250),
                ("ASA", 260),
                ("ABS", 260),
            )
        ]
        data["metadata"]["filaments"] = [
            {"tool": i, "type": kind, "colour": palette[i][i]}
            for i, kind in enumerate(("PLA", "PETG", "ASA"))
        ]
        data["metadata"]["suggested_mapping"] = {"0": "1A", "1": "2B", "2": "3C"}
        data["availability"] = action_policy.availability(data["status"], True, [])
    if name in ("printing-long", "printing-extreme", "printing-status", "dark-preview"):
        data = preview_scenario("printing")
        data["platform"]["estimated_time"] = (
            data["status"]["print_stats"]["print_duration"] + 8100
        )
        if name == "dark-preview":
            image = str(ROOT / "developer-tools/fixtures/assets/demo-part-dark.png")
            data["platform"].update(thumbnail=image, file_thumbnail=image)
    if name == "printing-extreme":
        data["platform"]["estimated_time"] = (
            data["status"]["print_stats"]["print_duration"] + 35999940
        )
        data["status"]["print_stats"]["info"] = {
            "current_layer": 999999,
            "total_layer": 999999,
        }
    if name == "printing-status":
        data["print_stage"] = (
            "Preparing the printer: waiting for the bed temperature to stabilize before the next operation begins"
        )
    if name == "calibrating":
        data["operation"] = {
            "id": 1,
            "action": "calibrate",
            "kind": "tilt",
            "label": "Calibrating",
            "detail": "Adjusting bed tilt…",
        }
    if name in ("wifi-scanning", "wifi-connecting"):
        data["platform"]["wifi"].update(
            busy=True,
            message="Scanning"
            if name == "wifi-scanning"
            else "Connecting to Demo network",
        )
        if name == "wifi-connecting":
            data["operation"] = {
                "id": 1,
                "action": "wifi_join",
                "ssid": "Demo network",
                "label": "Connecting Wi-Fi",
                "detail": "Connecting to Demo network",
            }
    if name == "usb-ejected":
        data["platform"]["usb"].update(mounted=False, ejected=True)
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
        data["status"]["webhooks"]["state"] = "ready"
    elif name == "shutdown":
        data["status"]["webhooks"].update(
            state="shutdown", state_message="Lost communication with the toolhead MCU."
        )
        data["error"] = "Lost communication with the toolhead MCU."
    elif name == "notice":
        data["notices"] = [
            {
                "id": 1,
                "title": "USB drive ejected",
                "detail": "You can remove the USB drive.",
                "fault": False,
            }
        ]
    elif name in ("wifi-connected", "wifi-failed"):
        data["notices"] = [
            {
                "id": 1,
                "title": "Wi-Fi connected"
                if name == "wifi-connected"
                else "Wi-Fi connection failed",
                "detail": "Connected to Demo network (192.0.2.21)"
                if name == "wifi-connected"
                else "Could not join the network. Check the password and try again.",
                "fault": name == "wifi-failed",
                "timed": name == "wifi-connected",
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


def available_cases():
    return sorted(path.stem for path in CASES.glob("*.json"))


def preview_case(name):
    """Materialize a portable UI case using the current snapshot schema."""
    if name not in available_cases():
        raise ValueError("Unknown preview case: " + name)
    recipe = json.loads((CASES / (name + ".json")).read_text())
    if recipe["scenario"] not in SCENARIOS:
        raise ValueError("Unknown scenario in case " + name)
    data = preview_scenario(recipe["scenario"])
    image = (FIXTURES / recipe["printer_image"]).resolve()
    if not image.is_relative_to(FIXTURES.resolve()) or not image.is_file():
        raise ValueError("Case artwork must be a bundled fixture asset")
    data["platform"]["printer_image"] = str(image)
    if recipe.get("model"):
        data["model"] = recipe["model"]
    if recipe.get("cfs") is False:
        data["status"]["k2_cfs"].update(fitted=False, active_slot=None)
        data["status"]["filament_switch_sensor filament_sensor"] = {
            "filament_detected": False
        }
        data["status"]["hark_cfs_slots"].update(
            slots={}, map_targets=["E"], map_tools=["T0"]
        )
        data["slots"] = []
        data["cfs_units"] = 0
        data["metadata"]["suggested_mapping"] = {}
        data["availability"] = action_policy.availability(
            data["status"], data["connected"], []
        )
    data["preview"] = {
        "case": name,
        "description": recipe["description"],
        "page": recipe.get("page", "home"),
        "actions": recipe.get("actions", []),
    }
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--scenario", choices=SCENARIOS)
    choice.add_argument("--case", choices=available_cases())
    parser.add_argument(
        "--printing", action="store_true", help="Mark a raw scenario as printing"
    )
    parser.add_argument(
        "--list", action="store_true", help="List bundled preview cases"
    )
    args = parser.parse_args()
    if args.list:
        for name in available_cases():
            recipe = json.loads((CASES / (name + ".json")).read_text())
            print(name + " — " + recipe["description"])
        return
    if args.output is None:
        parser.error("an output path is required unless --list is used")
    if args.case and args.printing:
        parser.error("--printing applies to --scenario, not --case")
    data = (
        preview_case(args.case)
        if args.case
        else preview_scenario(args.scenario or "idle", args.printing)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
