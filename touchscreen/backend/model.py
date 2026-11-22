"""Pure, printer-independent transformations of Moonraker data.

No model-specific temperatures, travel limits, slot counts or printer names
belong here. Missing configuration disables the corresponding operation.
"""

import math
from dataclasses import dataclass

ACTIVE_STATES = frozenset(("printing", "paused"))


@dataclass(frozen=True)
class Limits:
    minimum: float
    maximum: float

    def contains(self, value):
        return math.isfinite(value) and self.minimum <= value <= self.maximum


def heater_limits(settings, heater, filament=False):
    name = "heater_generic chamber_heater" if heater == "chamber" else heater
    config = settings.get(name)
    if not config:
        return None
    try:
        minimum = max(0.0, float(config["min_temp"]))
        maximum = float(config["max_temp"])
        if filament:
            minimum = max(minimum, float(config["min_extrude_temp"]))
        if heater == "chamber":
            supervisor = settings.get("hark_chamber")
            if (
                not supervisor
                or "max_target" not in supervisor
                or str(supervisor.get("enable", True)).lower() in ("false", "0")
            ):
                return None
            maximum = min(maximum, float(supervisor["max_target"]))
        if not all(map(math.isfinite, (minimum, maximum))) or minimum > maximum:
            return None
        return Limits(minimum, maximum)
    except (KeyError, TypeError, ValueError):
        return None


def axis_limits(settings, status, axis):
    config = settings.get("stepper_" + axis.lower(), {})
    try:
        minimum = float(config.get("position_min", 0))
        maximum = float(config["position_max"])
        move = status.get("gcode_move", {})
        position = move["position"]
        gcode = move["gcode_position"]
        index = "xyz".index(axis.lower())
        # Config limits use machine coordinates; the input shows G-code
        # coordinates, including the active offset.
        offset = position[index] - gcode[index]
        if not all(map(math.isfinite, (minimum, maximum, offset))):
            return None
        minimum = math.ceil((minimum - offset) * 100 - 1e-7) / 100
        maximum = math.floor((maximum - offset) * 100 + 1e-7) / 100
        return Limits(minimum, maximum)
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def active(status):
    return status.get("print_stats", {}).get("state") in ACTIVE_STATES


def stopped(status):
    return status.get("webhooks", {}).get("state", "ready") != "ready"


def records(status):
    return (status.get("hark_cfs_slots") or {}).get("slots") or {}


def slots(status):
    """Slot identifiers come from the driver's advertised map capability."""
    info = status.get("hark_cfs_slots") or {}
    targets = info.get("map_targets")
    if targets:
        return [str(slot) for slot in targets if slot != "E"]
    # Older single-box drivers advertise their actual slot records instead.
    return sorted(key for key in records(status) if key != "E")


def fitted(status):
    return bool(status.get("k2_cfs")) and status["k2_cfs"].get("fitted") is not False


def loaded_slots(status, external=False):
    result = []
    for key, record in records(status).items():
        if key == "E" and not external:
            continue
        colour = str(record.get("color") or "").lstrip("#")
        if record.get("loaded") and record.get("type") and len(colour) == 6:
            try:
                int(colour, 16)
            except ValueError:
                continue
            result.append((key, record))
    return result


def slot_label(slot):
    if slot == "E":
        return "External spool"
    if len(slot) > 1:
        return "CFS %s · %s" % (slot[:-1], slot[-1])
    return slot


def refill_groups(status):
    groups = {}
    for key, record in loaded_slots(status):
        identity = (record["type"].upper(), record["color"].upper())
        groups.setdefault(identity, []).append(key)
    return [group for group in groups.values() if len(group) > 1]


def available_heaters(settings, status):
    result = []
    for key, label, icon in (
        ("extruder", "Nozzle", "nozzle"),
        ("heater_bed", "Bed", "bed"),
        ("chamber", "Chamber", "chamber"),
    ):
        if key == "chamber":
            present = (
                "temperature_sensor chamber_temperature" in status
                or "hark_chamber" in status
            )
        else:
            present = key in status or key in settings
        if present:
            result.append((key, label, icon))
    return result


def material_matches(required, actual):
    """Match declared material types, ignoring case/ASCII whitespace only.

    Missing slicer type retains manual mapping; additives and variants stay distinct.
    Keep the native material_matches() comparison in sync with these rules.
    """

    def key(value):
        return str(value or "").translate(str.maketrans("", "", " \t\r\n\f\v")).upper()

    expected = key(required)
    return not expected or expected == key(actual)


def suggest_mapping(filaments, status):
    """Prefer matching loaded material/colour without inventing physical slots."""
    candidates = dict(loaded_slots(status, external=True))
    cfs = status.get("k2_cfs") or {}
    external_present = (status.get("filament_switch_sensor filament_sensor") or {}).get(
        "filament_detected"
    ) and not cfs.get("active_slot")
    if not external_present:
        candidates.pop("E", None)
    if not fitted(status) or not cfs.get("online"):
        candidates = {k: v for k, v in candidates.items() if k == "E"}
    if len(filaments) > 1:
        candidates.pop("E", None)
    result = {}
    for filament in filaments:
        kind = filament.get("type") or ""
        colour = str(filament.get("colour") or "").lstrip("#")
        ranked = []
        for slot, rec in candidates.items():
            if not material_matches(kind, rec.get("type")):
                continue
            distance = 0
            if len(colour) == 6:
                try:
                    distance = sum(
                        (
                            int(colour[i : i + 2], 16)
                            - int(rec["color"].lstrip("#")[i : i + 2], 16)
                        )
                        ** 2
                        for i in (0, 2, 4)
                    )
                except ValueError:
                    distance = 0
            score = (
                distance,
                slot in result.values(),
                slot != cfs.get("active_slot"),
                slot,
            )
            ranked.append((score, slot))
        if ranked:
            result[str(filament["tool"])] = min(ranked)[1]
    return result
