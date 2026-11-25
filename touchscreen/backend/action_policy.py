"""Small, explicit action contract shared by execution and UI availability.

No widget-specific locking rules or subclass hierarchy: add a declaration here,
keep argument/fresh-state validation in Service.execute(), and bind a control to
its action key. Accepted operations reserve resources before their thread starts.
"""

from dataclasses import dataclass, replace

MAX_ACTIONS = 6

MACHINE = frozenset({"motion", "filament", "nozzle"})
FANS = frozenset({"fan:part", "fan:side", "fan:exhaust"})


class ActionBusy(RuntimeError):
    """Expected backpressure, not a new printer fault or a queued movement."""


@dataclass(frozen=True)
class ActionSpec:
    label: str
    resources: frozenset
    idle_only: bool
    requires_ready: bool
    interrupt: bool
    report_completion: bool


def rule(label, resources=(), *, idle=False, ready=True, interrupt=False, report=False):
    return ActionSpec(label, frozenset(resources), idle, ready, interrupt, report)


ACTIONS = {
    "home": rule("Homing", MACHINE | {"fan:part"}, idle=True, report=True),
    "jog": rule("Moving", {"motion"}, idle=True),
    "position": rule("Moving", {"motion"}, idle=True),
    "home_files": rule("Choosing a print"),
    "move_step": rule("Selecting movement distance", {"motion"}, idle=True),
    "motors": rule("Changing motor state", {"motion", "filament"}, idle=True),
    "extrude": rule("Moving filament", MACHINE, idle=True),
    "load": rule("Loading filament", MACHINE | {"fan:part"}, idle=True, report=True),
    "unload": rule(
        "Unloading filament", MACHINE | {"fan:part"}, idle=True, report=True
    ),
    "calibrate": rule(
        "Calibrating", MACHINE | FANS | {"bed", "chamber"}, idle=True, report=True
    ),
    "calibration_result": rule("Saving calibration", MACHINE, idle=True),
    "start": rule(
        "Starting print",
        MACHINE | {"bed", "chamber", "fan:part", "file"},
        idle=True,
        report=True,
    ),
    "mesh_profile": rule("Updating mesh profile", MACHINE, idle=True, report=True),
    "save_config": rule("Saving configuration", {"*"}, idle=True, report=True),
    "slot_clear": rule("Resetting filament", {"filament"}, idle=True),
    "slot_save": rule("Saving filament", {"filament"}, idle=True),
    "temperature": rule("Setting temperature"),
    "fan": rule("Setting fan"),
    "light": rule("Changing light", {"light"}),
    "heaters_off": rule("Turning heaters off", {"nozzle", "bed", "chamber"}, idle=True),
    "tune": rule("Adjusting print", {"tuning"}),
    "exclude": rule("Skipping objects", {"objects"}),
    "files": rule("Reading files", ready=False),
    "delete_recording": rule(
        "Deleting recording", {"recordings", "storage"}, ready=False, report=True
    ),
    "delete_file": rule("Deleting file", {"storage"}, report=True),
    "file": rule("Reading file", ready=False),
    "recordings": rule("Reading recordings", {"recordings"}, ready=False),
    "export": rule("Exporting", {"storage"}, ready=False, report=True),
    "usb_format": rule(
        "Formatting USB", {"storage"}, idle=True, ready=False, report=True
    ),
    "usb_eject": rule("Ejecting USB", {"storage"}, idle=True, ready=False),
    "timelapse": rule("Setting time-lapse", {"timelapse"}, ready=False),
    "preference": rule("Saving settings", {"preferences"}, ready=False),
    "timezone": rule("Setting time zone", {"preferences"}, ready=False),
    "wifi_scan": rule("Scanning Wi-Fi", {"network"}, ready=False),
    "wifi_join": rule("Connecting to Wi-Fi", {"network"}, ready=False, report=True),
    "pause": rule("Pausing", {"interrupt"}, interrupt=True),
    "resume": rule("Resuming", {"interrupt"}, interrupt=True),
    "cancel": rule("Stopping print", {"interrupt"}, interrupt=True, report=True),
    "restart": rule(
        "Restarting printer control", {"interrupt"}, ready=False, interrupt=True
    ),
    "firmware_restart": rule(
        "Restarting controller firmware", {"interrupt"}, ready=False, interrupt=True
    ),
    "plr": rule("Recovering print", MACHINE | {"bed", "chamber"}, report=True),
}


def describe(request):
    action = request.get("action")
    if action not in ACTIONS:
        raise ValueError("Unknown touchscreen action")
    spec = ACTIONS[action]
    if action == "temperature":
        resources = {"extruder": "nozzle", "heater_bed": "bed", "chamber": "chamber"}
        if request.get("heater") not in resources:
            raise ValueError("Unknown heater")
        spec = replace(spec, resources=frozenset({resources[request["heater"]]}))
    elif action == "fan":
        if request.get("fan") not in ("part", "side", "exhaust"):
            raise KeyError("Unknown fan")
        spec = replace(spec, resources=frozenset({"fan:" + request["fan"]}))
    elif action == "preference" and str(request.get("key", "")).startswith("cfs_"):
        spec = replace(
            spec,
            resources=spec.resources | {"filament"},
            idle_only=True,
            requires_ready=True,
        )
    if action in ("start", "file", "delete_file") and request.get("path"):
        spec = replace(
            spec, resources=spec.resources | {"file:" + str(request["path"])}
        )
    return spec


def reason(request, status, connected, operations):
    spec = describe(request)
    operations = tuple(operations)
    if request.get("action") in ("restart", "firmware_restart"):
        return (
            "Restart in progress"
            if any(
                op.get("action") in ("restart", "firmware_restart") for op in operations
            )
            else ""
        )
    if request.get("action") in ("temperature", "fan"):
        operations = tuple(
            op
            for op in operations
            if not (
                op.get("action") == request.get("action")
                and (
                    (
                        op.get("heater") == request.get("heater")
                        if request.get("action") == "temperature"
                        else op.get("fan") == request.get("fan")
                    )
                    or set(op.get("resources", ())) == set(spec.resources)
                )
            )
        )
    if request.get("action") == "home_files":
        for op in operations:
            if (
                op.get("action") in ("calibrate", "calibration_result", "save_config")
                and op.get("kind") != "home"
            ):
                return "Calibration in progress"
            if op.get("action") in ("load", "unload", "start"):
                return op["label"] + " in progress"
    homed = set(status.get("toolhead", {}).get("homed_axes", ""))
    if request.get("action") == "move_step" and not (
        "z" in homed or {"x", "y"} <= homed
    ):
        return "Home the printer first"
    if (
        not spec.interrupt
        and sum(not op.get("interrupt", False) for op in operations) >= MAX_ACTIONS
    ):
        return "Wait for a pending action to finish"
    if spec.requires_ready and (
        not connected or status.get("webhooks", {}).get("state", "ready") != "ready"
    ):
        return "Printer is not ready"
    if request.get("action") in ("jog", "position") and request.get("axis"):
        axis = str(request["axis"]).lower()
        needed = "xy" if axis in ("x", "y") else "z"
        if not set(needed) <= set(status.get("toolhead", {}).get("homed_axes", "")):
            return "Home XY first" if needed == "xy" else "Home Z first"
    if request.get("action") == "unload":
        sensor = status.get("filament_switch_sensor filament_sensor", {}).get(
            "filament_detected"
        )
        if sensor is False and not status.get("k2_cfs", {}).get("active_slot"):
            return "No filament in the extruder"
    if (
        request.get("action") == "delete_file"
        and request.get("path")
        and status.get("print_stats", {}).get("state") in ("printing", "paused")
        and request["path"] == status.get("print_stats", {}).get("filename")
    ):
        return "Cannot delete the file being printed"
    if spec.idle_only:
        if status.get("print_stats", {}).get("state") in ("printing", "paused"):
            return "Unavailable during printing"
    if (
        status.get("k2_cfs", {}).get("change_pending")
        and spec.resources & MACHINE
        and not spec.interrupt
    ):
        return "Wait for the filament change to finish"
    for operation in operations:
        held = set(operation["resources"])
        # Explicit restart/print-interruption commands must not wait behind a
        # long-running G-code HTTP request. Fresh state/confirmation still apply.
        conflict = bool(spec.resources & held)
        if not spec.interrupt and ("*" in spec.resources or "*" in held):
            conflict = True
        if conflict:
            return operation["label"] + " in progress"
    return ""


def controls(status=None):
    """Stable keys consumed by native controls; values are validated requests."""
    result = {
        name: {"action": name}
        for name in ACTIONS
        if name not in ("temperature", "fan", "preference")
    }
    result["delete_file_current"] = {
        "action": "delete_file",
        "path": (status or {}).get("print_stats", {}).get("filename", ""),
    }
    result.update(
        {
            "temperature:" + h: {"action": "temperature", "heater": h}
            for h in ("extruder", "heater_bed", "chamber")
        }
    )
    result.update(
        {"fan:" + f: {"action": "fan", "fan": f} for f in ("part", "side", "exhaust")}
    )
    for action in ("jog", "position"):
        for axis in "XYZ":
            result[action + ":" + axis] = {"action": action, "axis": axis}
    for key in (
        "brightness",
        "accent",
        "sleep_after",
        "light_off",
        "idle",
        "cfs_startup_read",
        "cfs_insertion_read",
        "cfs_auto_refill",
    ):
        result["preference:" + key] = {"action": "preference", "key": key}
    return result


def availability(status, connected, operations):
    result = {}
    for key, request in controls(status).items():
        blocked = reason(request, status, connected, operations)
        result[key] = {"enabled": not bool(blocked), "reason": blocked}
    return result
