#!/usr/bin/env python3
"""Moonraker/platform service for the native UI. Never draws or handles touch.

The only mutation entry point is execute(): typed requests are validated against
fresh state/configuration. A shared resource policy prevents conflicting actions.
At most six independent actions plus one interrupt run concurrently; file-list
refreshes are coalesced background reads. Every operation releases its own state.
The Unix socket is private to the current user.
"""

import argparse
import base64
import configparser
import hashlib
import io
import json
import math
import os
import re
import select
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

import action_policy
import gcode_metadata
import model
import platform_services as platform
import storage

OBJECTS = (
    "webhooks",
    "print_stats",
    "virtual_sdcard",
    "display_status",
    "extruder",
    "heater_bed",
    "heater_generic chamber_heater",
    "hark_chamber",
    "temperature_sensor chamber_temperature",
    "fan",
    "fan_generic auxiliary_fan",
    "fan_generic exhaust_fan",
    "heater_fan hotend_fan",
    "output_pin chamber_led",
    "stepper_enable",
    "toolhead",
    "gcode_move",
    "exclude_object",
    "k2_cfs",
    "hark_cfs_slots",
    "gcode_macro START_PRINT",
    "gcode_macro _RUNOUT_REFILL",
    "gcode_macro _STAGE",
    "hark_plr",
    "hark_calib",
    "pa_pressure_log",
    "gcode_macro _PA_STATIC_SAVE",
    "filament_switch_sensor filament_sensor",
    "idle_timeout",
    "gcode_macro _LIGHT_IDLE",
    "k2_motor_bus",
)


class Moonraker:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def request(self, path, body=None, timeout=10, method=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path, data, {"Content-Type": "application/json"}, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)["result"]
        except urllib.error.HTTPError as error:
            try:
                message = json.load(error).get("error", {}).get("message", str(error))
            finally:
                error.close()
            raise RuntimeError(message) from None

    def status_snapshot(self):
        return self.request(
            "/printer/objects/query?"
            + "&".join(urllib.parse.quote(name) for name in OBJECTS)
        )

    def query(self):
        return self.status_snapshot()["status"]

    def settings(self):
        return self.request("/printer/objects/query?configfile=settings")["status"][
            "configfile"
        ]["settings"]

    def run_gcode(self, script, timeout=600):
        return self.request("/printer/gcode/script", {"script": script}, timeout)

    def files(self, root="gcodes"):
        return self.request("/server/files/list?root=" + root)

    def metadata(self, path):
        return self.request(
            "/server/files/metadata?filename=" + urllib.parse.quote(path, safe="")
        )


class WebSocket:
    """Small RFC6455 client for Moonraker's local JSON-RPC status stream.

    HTTP queries are retained for startup, reconnect and command validation.
    Fragmented messages and ping/close frames are handled; frame sizes bounded.
    """

    def __init__(self, base):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "http":
            raise ValueError(
                "WebSocket stream requires local HTTP; use bounded polling for HTTPS"
            )
        host, port = parsed.hostname, parsed.port or 80
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(3)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path.rstrip("/") + "/websocket"
        self.sock.sendall(
            (
                "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
            ).encode()
        )
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            self.buffer += self.sock.recv(4096)
            if len(self.buffer) > 16384:
                raise RuntimeError("Invalid WebSocket handshake")
        header, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        )
        if not header.startswith(b"HTTP/1.1 101") or accept not in header:
            raise RuntimeError("Moonraker rejected WebSocket upgrade")

    def send(self, value, opcode=1):
        payload = json.dumps(value).encode() if opcode == 1 else value
        mask = os.urandom(4)
        length = len(payload)
        head = bytes([0x80 | opcode, 0x80 | (length if length < 126 else 126)])
        if length >= 126:
            head += struct.pack("!H", length)
        self.sock.sendall(
            head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        )

    def read(self, count):
        idle_since = time.monotonic()
        while len(self.buffer) < count:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                if time.monotonic() - idle_since > 15:
                    raise TimeoutError("Moonraker status stream timed out")
                self.send(b"", 9)
                continue
            idle_since = time.monotonic()
            if not chunk:
                raise EOFError("Moonraker disconnected")
            self.buffer += chunk
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def receive(self):
        fragments = []
        while True:
            first, second = self.read(2)
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.read(8))[0]
            if length > 2 * 1024 * 1024:
                raise RuntimeError("Oversize status frame")
            if second & 128:
                raise RuntimeError("Masked server frame")
            payload = self.read(length)
            opcode = first & 15
            if opcode == 8:
                raise EOFError("Moonraker closed stream")
            if opcode == 9:
                self.send(payload, 10)
                continue
            if opcode == 10:
                continue
            fragments.append(payload)
            if sum(map(len, fragments)) > 2 * 1024 * 1024:
                raise RuntimeError("Oversize status message")
            if first & 128:
                return json.loads(b"".join(fragments))

    def close(self):
        self.sock.close()


class Service:
    def __init__(self, config, mr=None):
        self.config_path = Path(config)
        self.config = configparser.ConfigParser()
        self.config.read(config)
        self.mr = mr or Moonraker(
            self.config.get("screen", "moonraker", fallback="http://127.0.0.1:7126")
        )
        self.prefs_path = self.config_path.with_name("native-screen-settings.json")
        try:
            self.prefs = json.loads(self.prefs_path.read_text())
        except (OSError, ValueError):
            self.prefs = {}
        self.lock = threading.RLock()
        self.status, self.settings, self.materials = {}, {}, []
        self.events, self.notices, self.files, self.metadata = [], [], [], {}
        self.operations = {}
        self.pending_temperatures = {}
        self.pending_fans = {}
        self.requested_values = {}
        self.selection_id = 0
        self.cfs_event_id = None
        self.file_read = {"busy": False, "path": ""}
        self.files_preloaded = False
        self.files_ready = False
        self.results = deque(maxlen=32)
        self.result = None
        self.operation_id = 0
        self.history_id = 0
        self.wifi = platform.Wifi()
        self.platform = {}
        self.sequence = 0
        self.started = time.time()
        self.session = uuid.uuid4().hex
        self.last_alert = self.started
        self.usb_id = None
        self.usb_initialized = False
        self.connected = False
        self.stream_connected = False
        self.status_times = {}
        self.read_policy = storage.FilamentReadPolicy()
        self.filaments = []
        self.ready = False
        self.files_source = "gcodes"
        self.file_generation = 0
        self.file_lock = threading.RLock()
        self.files_refreshing = False
        self.files_error = ""
        self.file_signatures = {}
        self.selected_file = ""
        self.last_state = ""
        self.last_stage = ""
        self.print_stage = ""
        self.config_stamp = 0
        self.last_activity = time.monotonic()
        self.asleep = False
        self.power_lock = threading.Lock()
        self.cache_path = self.config_path.parent / "native-screen-cache"
        self.print_file = ""
        self.artwork_key = None

    def event(self, title, detail="", fault=False, timed=False, filament_slot=None):
        with self.lock:
            if fault and any(
                e.get("fault")
                and e.get("detail") == str(detail).strip().lstrip("!").strip()
                and time.time() - e["time"] < 3
                for e in self.notices
            ):
                return
            if fault:
                detail = str(detail).strip().lstrip("!").strip()
            self.history_id += 1
            event = {
                "id": self.history_id,
                "title": str(title),
                "detail": str(detail),
                "time": time.time(),
                "fault": fault,
                "timed": timed,
            }
            if filament_slot is not None:
                event["filament_slot"] = filament_slot
            self.events = (self.events + [event])[-200:]
            if fault or timed or filament_slot is not None:
                self.notices.append(event)
            self.sequence += 1
        if fault or filament_slot is not None:
            self.wake_screen()
        return event

    def busy(self, status):
        return bool(self.operation) or bool(
            status.get("k2_cfs", {}).get("change_pending")
        )

    def fresh(self, idle=False):
        snapshot = self.mr.status_snapshot()
        settings = self.mr.settings()
        # A slower HTTP response must not overwrite newer subscription state.
        self.merge_status(snapshot["status"], snapshot.get("eventtime"))
        with self.lock:
            status = {
                key: dict(value) if isinstance(value, dict) else value
                for key, value in self.status.items()
            }
            self.settings = settings
        if model.stopped(status):
            raise RuntimeError("Printer stopped. Resolve its error first.")
        if idle and model.active(status):
            raise RuntimeError(
                "Unavailable during printing. Finish or stop the print first."
            )
        return status, settings

    def save_preferences(self):
        temp = self.prefs_path.with_suffix(".json.new")
        temp.write_text(json.dumps(self.prefs, indent=2))
        os.chmod(temp, 0o600)
        temp.replace(self.prefs_path)

    def submit(self, request):
        action = request.get("action")
        if action == "files":
            # Browsing storage is a background read, not a printer operation.
            # Coalesce repeated navigation/refresh taps while the read is active.
            with self.lock:
                if self.files_refreshing:
                    return
                self.files_refreshing = True
                self.files_error = ""
                self.sequence += 1
            threading.Thread(target=self.refresh_files, daemon=True).start()
            return
        if action == "activity":
            self.wake_screen()
            return
        if action == "ack":
            with self.lock:
                self.notices = [n for n in self.notices if n["id"] != request.get("id")]
                self.sequence += 1
            return
        spec = action_policy.describe(request)
        with self.lock:
            blocked = action_policy.reason(
                request, self.status, self.connected, self.operations.values()
            )
            if blocked:
                raise action_policy.ActionBusy(blocked)
            if action == "file":
                self.selection_id += 1
                request = dict(request, selection_id=self.selection_id)
                self.selected_file = str(request["path"])
                self.file_read = {"busy": True, "path": self.selected_file}
                self.platform.pop("file_thumbnail", None)
                self.metadata.pop(self.selected_file, None)
            if action in ("temperature", "fan"):
                channel = (
                    request.get("heater")
                    if action == "temperature"
                    else request.get("fan")
                )
                if action == "temperature":
                    limits = model.heater_limits(self.settings, channel)
                    if not limits or not limits.contains(
                        float(request.get("value", float("nan")))
                    ):
                        raise ValueError("Temperature is outside configured limits")
                if action == "fan":
                    value = float(request.get("value", float("nan")))
                    if not math.isfinite(value) or not 0 <= value <= 100:
                        raise ValueError("Invalid fan speed")
                self.requested_values[(action, channel)] = (
                    float(request["value"]),
                    time.monotonic() + 8,
                )
            if action == "fan" and any(
                op.get("action") == "fan" and op.get("fan") == request["fan"]
                for op in self.operations.values()
            ):
                self.pending_fans[request["fan"]] = dict(request)
                self.sequence += 1
                return
            if action == "temperature":
                if any(
                    op.get("action") == "temperature"
                    and op.get("heater") == request["heater"]
                    for op in self.operations.values()
                ):
                    self.pending_temperatures[request["heater"]] = dict(request)
                    self.sequence += 1
                    return
            self.operation_id += 1
            token = self.operation_id
            self.operations[token] = {
                "id": token,
                "action": action,
                "slot": request.get("slot", ""),
                "heater": request.get("heater", ""),
                "fan": request.get("fan", ""),
                "kind": request.get("kind", ""),
                "ssid": request.get("ssid", ""),
                "label": spec.label,
                "resources": sorted(spec.resources),
                "interrupt": spec.interrupt,
                "started": time.time(),
            }
            self.sequence += 1
        try:
            threading.Thread(
                target=self.run_operation, args=(dict(request), token), daemon=True
            ).start()
        except Exception:
            with self.lock:
                self.operations.pop(token, None)
                if (
                    action == "file"
                    and request.get("selection_id") == self.selection_id
                ):
                    self.file_read["busy"] = False
                if action in ("temperature", "fan"):
                    self.requested_values.pop(
                        (
                            action,
                            request.get("heater")
                            if action == "temperature"
                            else request.get("fan"),
                        ),
                        None,
                    )
                self.sequence += 1
            raise

    @property
    def operation(self):
        # Prefer the machine's progress over an unrelated quick settings write.
        with self.lock:
            values = sorted(
                self.operations.values(),
                key=lambda op: ("motion" not in op["resources"], op["id"]),
            )
            return values[0] if values else None

    def preload_files(self):
        if (
            self.connected
            and self.status.get("webhooks", {}).get("state") == "ready"
            and not self.files_preloaded
        ):
            self.files_preloaded = True
            self.submit({"action": "files", "source": "gcodes"})

    def refresh_files(self):
        try:
            self.execute({"action": "files", "source": "gcodes"})
            self.files_ready = True
        except Exception as error:
            with self.lock:
                self.files_error = str(error)
        finally:
            with self.lock:
                self.files_refreshing = False
                self.sequence += 1

    @staticmethod
    def operation_label(action):
        return action_policy.ACTIONS[action].label

    def run_operation(self, request, token):
        action = request.get("action")
        spec = action_policy.describe(request)
        success, failure = False, ""
        try:
            self.execute(request)
            success = True
            if spec.report_completion:
                title = {
                    "delete_file": "File deleted",
                    "delete_recording": "Recording deleted",
                }.get(action, spec.label + " complete")
                self.event(
                    title,
                    request.get("path", "")
                    if action in ("delete_file", "delete_recording")
                    else "",
                )
        except Exception as error:
            failure = str(error)
            self.event(
                "Wi-Fi connection failed"
                if action == "wifi_join"
                else spec.label + " failed",
                failure,
                fault=True,
            )
        finally:
            with self.lock:
                result = {
                    "id": token,
                    "action": action,
                    "success": success,
                    "error": failure,
                    "slot": request.get("slot", ""),
                    "path": request.get("path", ""),
                }
                self.result = result
                self.results.append(result)
                self.operations.pop(token, None)
                self.sequence += 1
                if (
                    action == "file"
                    and request.get("selection_id") == self.selection_id
                ):
                    self.file_read["busy"] = False
                channel = (
                    request.get("heater")
                    if action == "temperature"
                    else request.get("fan")
                )
                if not success and action in ("temperature", "fan"):
                    current = self.requested_values.get((action, channel))
                    if current and current[0] == float(request["value"]):
                        self.requested_values.pop((action, channel), None)
                pending = (
                    self.pending_temperatures.pop(request.get("heater"), None)
                    if action == "temperature"
                    else self.pending_fans.pop(request.get("fan"), None)
                    if action == "fan"
                    else None
                )
                if pending is not None:
                    # Re-admit under the same RLock: a newer setter must not
                    # overtake this queued target. All interlocks are rechecked.
                    try:
                        self.submit(pending)
                    except (ValueError, action_policy.ActionBusy):
                        pass  # A newly active interlock wins over a queued target.

    def snapshot(self):
        with self.lock:
            status = {
                key: dict(value) if isinstance(value, dict) else value
                for key, value in self.status.items()
            }
            settings = self.settings
            for (action, channel), (value, expiry) in list(
                self.requested_values.items()
            ):
                if time.monotonic() >= expiry:
                    self.requested_values.pop((action, channel), None)
                    continue
                if action == "temperature":
                    obj, field, desired = (
                        ("hark_chamber", "requested", value)
                        if channel == "chamber"
                        else (channel, "target", value)
                    )
                else:
                    obj = {
                        "part": "fan",
                        "side": "fan_generic auxiliary_fan",
                        "exhaust": "fan_generic exhaust_fan",
                    }[channel]
                    field, desired = "speed", value / 100
                in_flight = any(
                    op.get("action") == action
                    and op.get("heater" if action == "temperature" else "fan")
                    == channel
                    for op in self.operations.values()
                )
                if (
                    abs(float(status.get(obj, {}).get(field, -999)) - desired) < 0.001
                    and not in_flight
                ):
                    self.requested_values.pop((action, channel), None)
                else:
                    status.setdefault(obj, {})[field] = desired
            slots = []
            for key, rec in model.records(status).items():
                # Removed spool data remains inside Klipper for the in-flight
                # tail/refill only, never as the new spool's editable details.
                visible = rec if rec.get("loaded") else {"loaded": False}
                slots.append(dict(visible, slot=key, label=model.slot_label(key)))
            heaters = []
            for key, label, icon in model.available_heaters(settings, status):
                limits = model.heater_limits(settings, key)
                now = (
                    status.get(key, {})
                    if key != "chamber"
                    else status.get("temperature_sensor chamber_temperature", {})
                )
                target = (
                    status.get(key, {}).get("target", 0)
                    if key != "chamber"
                    else status.get("hark_chamber", {}).get("requested", 0)
                )
                heaters.append(
                    {
                        "key": key,
                        "label": label,
                        "current": now.get("temperature", 0),
                        "target": target,
                        "limits": vars(limits) if limits else None,
                    }
                )
            name = self.config.get("brand", "model", fallback="") or self.config.get(
                "brand", "name", fallback="Printer"
            )
            axis = {
                a: (
                    vars(limit)
                    if (limit := model.axis_limits(settings, status, a))
                    else None
                )
                for a in "xyz"
            }
            error = "" if self.connected else "Waiting for Moonraker"
            if self.connected and status.get("webhooks", {}).get("state") in (
                "error",
                "shutdown",
            ):
                error = (
                    status.get("webhooks", {}).get("state_message") or "Printer stopped"
                )
            return {
                "session": self.session,
                "sequence": self.sequence,
                "connected": self.connected,
                "print_stage": self.print_stage,
                "stream": self.stream_connected,
                "status": status,
                "settings": settings,
                "slots": slots,
                "heaters": heaters,
                "axes": axis,
                "materials": self.materials,
                "prefs": self.current_preferences(),
                "events": self.events,
                "notices": self.notices,
                "operation": dict(self.operation) if self.operation else None,
                "operations": [dict(op) for op in self.operations.values()],
                "results": list(self.results),
                "availability": action_policy.availability(
                    status, self.connected, self.operations.values()
                ),
                "result": self.result,
                "platform": self.platform,
                "model": name,
                "cfs_units": len({slot[:-1] or "1" for slot in model.slots(status)})
                if model.fitted(status)
                else 0,
                "error": error,
                "files": self.files,
                "files_ready": self.files_ready,
                "tune_limits": model.TUNE_LIMITS,
                "calibrate_before_print": "variable_screen_full_mesh"
                in model.macro_options(settings, "START_PRINT"),
                "files_refresh": {
                    "busy": self.files_refreshing,
                    "error": self.files_error,
                },
                "file": self.selected_file,
                "file_read": dict(self.file_read),
                "metadata": self.metadata.get(self.selected_file, {}),
                "source": self.files_source,
                "wifi": dict(
                    self.platform.get("wifi", {}),
                    networks=self.wifi.networks,
                    busy=self.wifi.busy,
                    message=self.wifi.message,
                ),
                "refill": model.refill_groups(status),
                "zones": storage.timezones(),
                "timezone_presets": storage.timezone_presets(),
                "cutter_method": self.config.get(
                    "screen", "cutter_calibration_method", fallback="SENSOR"
                ),
            }

    def gcode(self, command):
        return self.mr.run_gcode(command)

    def execute(self, r):
        action = r.get("action")
        if action in ("restart", "firmware_restart"):
            if r.get("confirm") is not True:
                raise ValueError("Restart requires confirmation")
            self.mr.request(
                "/printer/firmware_restart"
                if action == "firmware_restart"
                else "/printer/restart",
                {},
            )
            return
        spec = action_policy.describe(r)
        local_actions = {
            "wifi_scan",
            "wifi_join",
            "timezone",
            "files",
            "file",
            "recordings",
            "delete_recording",
            "export",
            "usb_format",
            "usb_eject",
            "preference",
            "timelapse",
        }
        if action in local_actions and not spec.idle_only:
            status, settings = self.status, self.settings
        else:
            status, settings = self.fresh(spec.idle_only)
        if action == "home":
            axis = r.get("axis", "")
            if axis not in ("", "X", "Y", "Z", "XY"):
                raise ValueError("Unknown axis")
            self.gcode("G28 " + " ".join(axis))
        elif action in ("jog", "position"):
            axis = str(r["axis"]).lower()
            if (
                axis not in "xyz"
                or len(axis) != 1
                or not set("xy" if axis in "xy" else "z")
                <= set(status.get("toolhead", {}).get("homed_axes", ""))
            ):
                raise ValueError("Home this axis first")
            value = float(r["value"])
            if action == "jog":
                if not math.isfinite(value) or abs(value) > 100:
                    raise ValueError("Invalid movement distance")
                value += status["gcode_move"]["gcode_position"]["xyz".index(axis)]
            limit = model.axis_limits(settings, status, axis)
            if not limit or not limit.contains(value):
                raise ValueError("Position is outside configured travel")
            speed = (
                min(float(settings.get("printer", {}).get("max_velocity", 50)), 50) * 60
            )
            if axis == "z":
                speed = min(
                    speed,
                    float(settings.get("printer", {}).get("max_z_velocity", 5)) * 60,
                )
            self.gcode(
                "SAVE_GCODE_STATE NAME=NATIVE_MOVE\nG90\nG1 %s%.4f F%.1f\nRESTORE_GCODE_STATE NAME=NATIVE_MOVE"
                % (axis.upper(), value, speed)
            )
        elif action == "extrude":
            value = float(r["value"])
            config = settings.get("extruder")
            if not config:
                raise ValueError("Extruder unavailable")
            maximum = min(float(config.get("max_extrude_only_distance", 50)), 100)
            if (
                not math.isfinite(maximum)
                or maximum <= 0
                or not math.isfinite(value)
                or value == 0
                or abs(value) > maximum
            ):
                raise ValueError("Extrusion distance is outside the configured limit")
            extruder = status.get("extruder", {})
            minimum = float(config.get("min_extrude_temp", 170))
            if (
                not math.isfinite(minimum)
                or not extruder.get("can_extrude")
                or extruder.get("temperature", 0) < minimum
            ):
                raise ValueError(
                    "Heat the nozzle to at least %g°C before moving filament" % minimum
                )
            if status.get("k2_cfs", {}).get("change_pending"):
                raise RuntimeError("Wait for the filament change to finish")
            speed = min(float(config.get("max_extrude_only_velocity", 5)), 5)
            if not math.isfinite(speed) or speed <= 0:
                raise ValueError("Extrusion speed is unavailable")
            self.gcode(
                "SAVE_GCODE_STATE NAME=NATIVE_EXTRUDE\nM83\nG1 E%.4f F%.1f\n"
                "RESTORE_GCODE_STATE NAME=NATIVE_EXTRUDE" % (value, speed * 60)
            )
        elif action == "motors":
            enable = r.get("enable")
            if not isinstance(enable, bool):
                raise ValueError("Specify whether to lock or unlock motors")
            if not enable:
                if r.get("confirm") is not True:
                    raise ValueError("Unlocking motors requires confirmation")
                self.gcode("M84")
            else:
                commands = [
                    "SET_STEPPER_ENABLE STEPPER=%s ENABLE=1" % name
                    for name in settings
                    if re.fullmatch(r"stepper_[xyz]\d*", name)
                ]
                if not commands:
                    raise ValueError("Axis motors unavailable")
                self.gcode("\n".join(commands))
        elif action == "temperature":
            heater, value = r["heater"], float(r["value"])
            limit = model.heater_limits(settings, heater)
            if not limit or not limit.contains(value):
                raise ValueError("Temperature is outside configured limits")
            commands = {
                "extruder": "M104 S%g",
                "heater_bed": "M140 S%g",
                "chamber": "CHAMBER_HEAT TARGET=%g",
            }
            self.gcode(commands[heater] % value)
        elif action == "heaters_off":
            self.gcode("TURN_OFF_HEATERS")
        elif action == "light":
            if "output_pin chamber_led" not in settings:
                raise ValueError("No controllable light configured")
            on = not bool(status.get("output_pin chamber_led", {}).get("value"))
            self.gcode(
                "SET_PIN PIN=chamber_led VALUE=%d" % on
                + (
                    "\n_LIGHT_IDLE"
                    if on and "gcode_macro _LIGHT_IDLE" in settings
                    else ""
                )
            )
        elif action == "fan":
            names = {
                "part": ("fan", 0),
                "side": ("fan_generic auxiliary_fan", 2),
                "exhaust": ("fan_generic exhaust_fan", 1),
            }
            obj, index = names[r["fan"]]
            value = float(r["value"])
            if obj not in settings or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Fan control unavailable or invalid")
            self.gcode("M106 P%d S%d" % (index, round(value * 2.55)))
        elif action in ("pause", "resume", "cancel"):
            state = status.get("print_stats", {}).get("state")
            if action == "cancel" and r.get("confirm") is not True:
                raise ValueError("Stopping requires confirmation")
            if state not in ("printing", "paused") or (
                action == "resume" and state != "paused"
            ):
                raise ValueError("No matching print operation")
            self.gcode(
                {"pause": "PAUSE", "resume": "RESUME", "cancel": "CANCEL_PRINT"}[action]
            )
        elif action == "tune":
            if not model.active(status):
                raise ValueError("No print in progress")
            key, value = r["key"], float(r["value"])
            if key == "z_offset":
                current = status.get("gcode_move", {}).get("homing_origin", [0, 0, 0])[
                    2
                ]
                if not math.isfinite(value) or abs(value - current) > 0.100001:
                    raise ValueError(
                        "Z entry may change the current offset by at most 0.1 mm"
                    )
                self.gcode("SET_GCODE_OFFSET Z=%g MOVE=1" % value)
            elif key == "z":
                if not math.isfinite(value) or abs(value) > 0.1:
                    raise ValueError("Invalid Z adjustment")
                self.gcode("SET_GCODE_OFFSET Z_ADJUST=%g MOVE=1" % value)
            else:
                if not math.isfinite(value) or not model.TUNE_LIMITS.get(key, {}).get(
                    "minimum", 1
                ) <= value <= model.TUNE_LIMITS.get(key, {}).get("maximum", 0):
                    raise ValueError("Invalid ratio")
                self.gcode({"speed": "M220 S%g", "flow": "M221 S%g"}[key] % value)
        elif action in ("load", "unload"):
            self.filament_action(action, r, status)
        elif action == "slot_clear":
            slot = str(r.get("slot", ""))
            if r.get("confirm") is not True or (
                slot != "E" and slot not in model.slots(status)
            ):
                raise ValueError("Choose a known filament slot and confirm reset")
            self.gcode("CFS_SLOT_CLEAR SLOT=%s" % slot)
        elif action == "slot_save":
            self.save_slot(r, status, settings)
        elif action == "calibrate":
            commands = {
                "home": ("G28", None),
                "tilt": ("Z_TILT_ADJUST", "z_tilt"),
                "mesh": ("G28\nBED_MESH_CALIBRATE", "bed_mesh"),
                "shaper": ("SHAPER_CALIBRATE", "resonance_tester"),
                "belt": ("BELT_TENSION CONFIRM=1", "k2_belt_tension"),
                "cutter": (
                    "CFS_CUTTER_CALIBRATE METHOD=%s CONFIRM=1"
                    % self.config.get(
                        "screen", "cutter_calibration_method", fallback="SENSOR"
                    ),
                    "k2_cfs",
                ),
            }
            command, capability = commands[r["kind"]]
            if r.get("confirm") is not True or (
                capability and capability not in settings
            ):
                raise ValueError("Calibration unavailable or not confirmed")
            if r["kind"] == "tilt" and not set("xyz") <= set(
                status.get("toolhead", {}).get("homed_axes", "")
            ):
                command = "G28\n" + command
            self.gcode(command)
        elif action == "save_config":
            if r.get("confirm") is not True:
                raise ValueError("Save requires confirmation")
            self.gcode("SAVE_CONFIG")
        elif action == "exclude":
            known = {
                o["name"] for o in status.get("exclude_object", {}).get("objects", [])
            }
            names = r.get("names", [])
            if (
                not model.active(status)
                or not names
                or r.get("confirm") is not True
                or not set(names) <= known
            ):
                raise ValueError("Invalid object exclusion")
            self.gcode(
                "\n".join("EXCLUDE_OBJECT NAME=" + json.dumps(name) for name in names)
            )
        elif action == "files":
            # Files always browses Moonraker's configured G-code storage.
            # Attaching/removing USB cannot silently switch this listing.
            with self.file_lock:
                files = sorted(self.mr.files(), key=lambda f: -f.get("modified", 0))
                with self.lock:
                    self.files_source = "gcodes"
                    self.files = files
                    self.file_generation += 1
                    generation = self.file_generation
                threading.Thread(
                    target=self.file_thumbnails,
                    args=(list(files), generation),
                    daemon=True,
                ).start()
        elif action in ("delete_file", "delete_recording"):
            if r.get("confirm") is not True:
                raise ValueError("Deletion requires confirmation")
            path = r.get("path")
            recording = action == "delete_recording"
            if (
                not recording
                and model.active(status)
                and path == status.get("print_stats", {}).get("filename")
            ):
                raise RuntimeError("Cannot delete the file being printed")
            extensions = (".mp4", ".mkv") if recording else (".gcode", ".gco", ".g")
            root = "timelapse" if recording else "gcodes"
            if (
                not isinstance(path, str)
                or not path
                or path.startswith("/")
                or any(part in ("", ".", "..") for part in path.split("/"))
                or any(ord(ch) < 32 for ch in path)
                or not path.lower().endswith(extensions)
            ):
                raise ValueError("Invalid filename")
            with self.file_lock:
                if recording and not any(
                    row.get("path") == path for row in storage.list_recordings(self.mr)
                ):
                    raise ValueError("Recording is no longer available")
                self.mr.request(
                    "/server/files/" + root + "/" + urllib.parse.quote(path, safe="/"),
                    method="DELETE",
                )
                if recording:
                    with self.lock:
                        self.platform["recordings"] = [
                            row
                            for row in self.platform.get("recordings", [])
                            if row.get("path") != path
                        ]
                else:
                    with self.lock:
                        self.files = [f for f in self.files if f.get("path") != path]
                        self.file_generation += 1
                        self.metadata.pop(path, None)
                        self.file_signatures.pop(path, None)
                        self.platform.get("file_thumbnails", {}).pop(path, None)
                        if self.selected_file == path:
                            self.selected_file = ""
                            self.platform.pop("file_thumbnail", None)
        elif action == "file":
            path = str(r["path"])
            if self.files_source == "usb":
                path = storage.import_usb_file(self.mr, path)
            selection = r.get("selection_id", self.selection_id)
            if self.selection_id == selection:
                self.selected_file = path
                self.platform.pop("file_thumbnail", None)
            meta, embedded = self.read_metadata(path)
            meta["filaments"] = platform.file_filaments(meta)
            meta["suggested_mapping"] = model.suggest_mapping(
                meta["filaments"] or [{"tool": 0}], self.status
            )
            preview = None
            try:
                if embedded:
                    preview = storage.prepare_image(
                        io.BytesIO(embedded),
                        self.cache_path,
                        "selected-" + str(selection),
                        (328, 337),
                        trim=True,
                    )
                else:
                    preview = self.prepare_thumbnail(
                        path,
                        meta,
                        "selected-" + str(selection),
                        (328, 337),
                        publish=False,
                    )
            except (OSError, ValueError):
                pass
            with self.lock:
                self.metadata[path] = meta
                if self.selection_id == selection and self.selected_file == path:
                    self.platform.pop("file_thumbnail", None)
                    if preview:
                        self.platform["file_thumbnail"] = preview
                    self.file_read = {"busy": False, "path": path}
                    self.sequence += 1
        elif action == "start":
            self.start_print(r, status, settings)
        elif action == "preference":
            self.preference(r, status, settings)
        elif action == "wifi_scan":
            self.wifi.scan()
        elif action == "wifi_join":
            password = r.get("password", "")
            ssid = str(r["ssid"])
            if (
                not ssid
                or len(ssid.encode()) > 32
                or (password and not 8 <= len(password) <= 63)
            ):
                raise ValueError("Invalid network name or password")
            done = threading.Event()
            outcome = []

            def joined(ok):
                outcome.append(ok)
                done.set()

            self.wifi.connect(ssid, password, joined)
            if not done.wait(45) or not outcome or not outcome[0]:
                raise RuntimeError(self.wifi.message or "Wi-Fi connection timed out")
            self.platform["wifi"] = self.wifi.status()
            self.event(
                "Wi-Fi connected",
                self.wifi.message or "Connected to " + ssid,
                timed=True,
            )
        elif action == "timezone":
            name = r["name"]
            if name not in storage.timezones():
                raise ValueError("Unknown time zone")
            platform.set_system_zone(name)
            self.prefs["timezone"] = name
            self.save_preferences()
        elif action in ("usb_format", "usb_eject"):
            disk = platform.usb_disk()
            if not disk:
                raise RuntimeError("USB drive not connected")
            if action == "usb_format":
                if r.get("confirm") is not True:
                    raise ValueError("Formatting requires confirmation")
                platform.format_usb()
            elif platform.usb_in_use():
                raise RuntimeError("USB drive contains active printer directories")
            elif not platform.unmount_usb():
                raise RuntimeError("USB drive is busy")
            updated = platform.usb_disk()
            if action == "usb_eject" and updated:
                updated["mounted"] = False
                updated["ejected"] = True
            with self.lock:
                self.platform["usb"] = updated
                self.sequence += 1
            self.event(
                "USB formatted" if action == "usb_format" else "USB ejected", timed=True
            )
        elif action == "recordings":
            self.platform["recordings"] = storage.list_recordings(self.mr)
        elif action == "export":
            if r.get("kind") == "logs":
                storage.export_logs(self.mr)
            else:
                storage.export_recording(self.mr, r["path"])
            self.event("Export complete", timed=True)
        elif action == "timelapse":
            value = bool(r["enabled"])
            self.mr.request("/machine/timelapse/settings", {"enabled": value})
            self.platform["timelapse"]["enabled"] = value
        elif action == "plr":
            if r.get("confirm") is not True:
                raise ValueError("Recovery requires confirmation")
            self.gcode("PLR_RESUME CONFIRM=1" if r.get("resume") else "PLR_DISCARD")
        elif action == "calibration_result":
            if r.get("skip"):
                self.gcode("CALIB_SKIP")
            else:
                pending = status.get("hark_calib", {}).get("pending") or {}
                value = float(r["value"])
                if value not in pending.get("values", []):
                    raise ValueError("Unknown result")
                self.gcode(
                    "CALIB_SET KIND=%s SLOT=%s VALUE=%g"
                    % (pending["kind"], pending["slot"], value)
                )
        else:
            raise ValueError("Unknown touchscreen action")
        with self.lock:
            self.sequence += 1

    def filament_action(self, action, r, status):
        cfs = status.get("k2_cfs", {})
        active = cfs.get("active_slot")
        sensor = status.get("filament_switch_sensor filament_sensor", {}).get(
            "filament_detected"
        )
        if action == "unload":
            self.gcode("CFS_UNLOAD_ACTIVE CONFIRM=1" if active else "UNLOAD_FILAMENT")
            return
        slot = r["slot"]
        records = model.records(status)
        if slot == "E":
            if active:
                raise RuntimeError(
                    "Retract CFS filament before loading the external spool"
                )
            if not records.get("E", {}).get("type"):
                raise RuntimeError("Set external filament details first")
            if not sensor and r.get("inserted") is not True:
                raise RuntimeError("Insert the external filament first")
            self.gcode("LOAD_FILAMENT")
        else:
            if (
                not cfs.get("online")
                or slot not in model.slots(status)
                or not records.get(slot, {}).get("loaded")
            ):
                raise ValueError("CFS slot unavailable")
            if sensor and not active:
                raise RuntimeError(
                    "Retract and remove external filament before loading CFS"
                )
            self.gcode("CFS_LOAD_SLOT SLOT=%s CONFIRM=1" % slot)

    def save_slot(self, r, status, settings):
        slot = str(r["slot"])
        record = r["record"]
        if slot != "E" and slot not in model.slots(status):
            raise ValueError("Unknown slot")
        material = next(
            (m for m in self.materials if m.get("name") == record.get("name")), None
        )
        if not material:
            raise ValueError("Unknown material")
        colour = str(record["color"]).lstrip("#")
        if not re.fullmatch("[0-9a-fA-F]{6}", colour):
            raise ValueError("Invalid colour")
        temperature = float(record["nozzle_temp"])
        limit = model.heater_limits(settings, "extruder", True)
        if not limit or not limit.contains(temperature):
            raise ValueError("Filament temperature outside configured limits")
        name = material["name"].replace('"', "")
        self.gcode(
            'CFS_SLOT_SET SLOT=%s NAME="%s" COLOR=%s NOZZLE=%g'
            % (slot, name, colour, temperature)
        )

    def start_print(self, r, status, settings):
        path = str(r["path"])
        meta = self.metadata.get(path)
        if not meta:
            raise ValueError("Read this file before printing")
        filaments = meta.get("filaments") or [{"tool": 0}]
        mapping = {int(k): v for k, v in r.get("mapping", {}).items()}
        choices = dict(model.loaded_slots(status, external=True))
        cfs = status.get("k2_cfs", {})
        sensor = status.get("filament_switch_sensor filament_sensor", {}).get(
            "filament_detected"
        )
        if not sensor or cfs.get("active_slot"):
            choices.pop("E", None)
        if not model.fitted(status):
            choices = {k: v for k, v in choices.items() if k == "E"}
        elif not cfs.get("online"):
            choices = {k: v for k, v in choices.items() if k == "E"}
        if (
            sensor
            and not cfs.get("active_slot")
            and any(slot != "E" for slot in mapping.values())
        ):
            raise RuntimeError(
                "Retract and remove the external filament before printing from CFS"
            )
        tools = status.get("hark_cfs_slots", {}).get("map_tools", [])
        supported = {int(str(t).lstrip("T")) for t in tools}
        if any(f["tool"] not in supported for f in filaments):
            raise ValueError("File tool count exceeds installed driver capability")
        if any(mapping.get(f["tool"]) not in choices for f in filaments):
            raise ValueError("Map every file filament to a loaded known slot")
        for filament in filaments:
            record = choices[mapping[filament["tool"]]]
            if not model.material_matches(filament.get("type"), record.get("type")):
                raise ValueError(
                    "File filament %d requires %s; selected filament is %s"
                    % (
                        filament["tool"] + 1,
                        filament["type"],
                        record.get("type", "Unknown"),
                    )
                )
        if "E" in mapping.values() and len(filaments) > 1:
            raise ValueError(
                "External spool cannot switch file filaments automatically"
            )
        mesh = "variable_screen_full_mesh" in model.macro_options(
            settings, "START_PRINT"
        )
        with self.lock:
            self.platform["starting_file"] = path
            self.platform["preview_file"] = path
            self.platform["estimated_time"] = meta.get("estimated_time", 0)
            self.platform["layer_count"] = meta.get("layer_count", 0)
            self.platform.pop("thumbnail", None)
            if self.selected_file == path and self.platform.get("file_thumbnail"):
                self.platform["thumbnail"] = self.platform["file_thumbnail"]
            self.platform["print_preview_busy"] = False
            self.sequence += 1
        try:
            if mesh:
                self.gcode(
                    "SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=screen_full_mesh VALUE=%d"
                    % bool(r.get("calibrate"))
                )
            self.gcode(platform.tool_map_script(path, mapping))
            self.mr.request("/printer/print/start", {"filename": path})
        except Exception:
            with self.lock:
                self.platform.pop("starting_file", None)
                self.sequence += 1
            for command in ["CFS_TOOL_MAP RESET=1 QUIET=1"] + (
                [
                    "SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=screen_full_mesh VALUE=0"
                ]
                if mesh
                else []
            ):
                try:
                    self.gcode(command)
                except Exception:
                    pass
            raise

    def preference(self, r, status, settings):
        key, value = r["key"], r["value"]
        choices = {
            "brightness": (25, 50, 75, 100),
            "sleep_after": (0, 120, 300, 600),
            "light_off": (0, 300, 600, 1800),
            "idle": (0, 900, 1800, 3600, 86400),
        }
        if key in choices:
            if value not in choices[key]:
                raise ValueError("Invalid preference value")
            if key == "brightness":
                platform.set_brightness(value)
            elif key == "light_off" and "gcode_macro _LIGHT_IDLE" in settings:
                self.gcode(
                    "SET_GCODE_VARIABLE MACRO=_LIGHT_IDLE VARIABLE=off_after VALUE=%d"
                    % value
                )
                # Re-arm/cancel the existing countdown immediately while idle.
                if not model.active(status):
                    self.gcode("_LIGHT_IDLE")
            elif key == "idle":
                self.gcode("SET_IDLE_TIMEOUT TIMEOUT=%d" % (value or 86400))
        elif key == "accent":
            if not re.fullmatch("#[0-9a-fA-F]{6}", str(value)):
                raise ValueError("Invalid accent")
        elif key in ("cfs_startup_read", "cfs_insertion_read", "cfs_auto_refill"):
            if model.active(status):
                raise RuntimeError("Unavailable during printing")
            if not isinstance(value, bool):
                raise ValueError("Invalid switch value")
            if key == "cfs_auto_refill":
                if "gcode_macro _RUNOUT_REFILL" not in settings:
                    raise ValueError("Refill unavailable")
                self.gcode(
                    "SET_GCODE_VARIABLE MACRO=_RUNOUT_REFILL VARIABLE=enabled VALUE=%d"
                    % value
                )
        else:
            raise ValueError("Unknown preference")
        self.prefs[key] = value
        self.save_preferences()

    def cfs_slot_notices(self):
        history = self.status.get("k2_cfs", {}).get("slot_event_history", [])
        latest = max((e.get("id", 0) for e in history), default=0)
        if self.cfs_event_id is None or latest < self.cfs_event_id:
            self.cfs_event_id = (
                latest  # Initial/restarted driver: do not replay old prompts.
            )
            return
        for item in history:
            if item.get("id", 0) <= self.cfs_event_id:
                continue
            slot, kind = item.get("slot"), item.get("event")
            if slot not in model.slots(self.status):
                continue
            if kind in ("removed", "inserted"):
                self.notices = [
                    n for n in self.notices if n.get("filament_slot") != slot
                ]
            if kind == "inserted":
                self.event(
                    "Filament changed",
                    "Enter the material, color and nozzle temperature for %s."
                    % model.slot_label(slot),
                    filament_slot=slot,
                )
        self.cfs_event_id = latest

    def merge_status(self, delta, eventtime=None, full=False):
        with self.lock:
            initialized = bool(self.status)
            previous_file = self.status.get("print_stats", {}).get("filename", "")
            previous_state = self.status.get("print_stats", {}).get("state", "")
            for name, values in delta.items():
                if eventtime is not None:
                    if eventtime < self.status_times.get(name, float("-inf")):
                        continue
                    self.status_times[name] = eventtime
                if isinstance(values, dict) and not full:
                    self.status.setdefault(name, {}).update(values)
                else:
                    self.status[name] = values
            self.connected = True
            if self.status.get("webhooks", {}).get("state") != "ready":
                self.requested_values.clear()
                self.pending_temperatures.clear()
                self.pending_fans.clear()
                self.cfs_event_id = None
            elif "k2_cfs" in delta:
                self.cfs_slot_notices()
            filename = self.status.get("print_stats", {}).get("filename", "")
            if filename != previous_file and filename != self.platform.get(
                "preview_file"
            ):
                self.platform.pop("thumbnail", None)
                self.platform.pop("estimated_time", None)
                self.platform.pop("layer_count", None)
                self.platform["print_preview_busy"] = bool(filename)
            state = self.status.get("print_stats", {}).get("state", "")
            if (
                state in ("printing", "paused")
                or (state in ("error", "cancelled") and state != previous_state)
                or self.status.get("webhooks", {}).get("state") != "ready"
            ):
                self.platform.pop("starting_file", None)
            stage = self.status.get("gcode_macro _STAGE", {}).get("text", "")
            if state == "printing" and previous_state not in ("printing", "paused"):
                self.print_file = ""
            if state != self.last_state:
                self.print_stage = ""
                self.last_state = state
                self.event(state.title() or "Connected")
            if stage != self.last_stage:
                self.last_stage = stage
                if initialized:
                    if stage:
                        self.event(stage)
                    self.print_stage = stage if state in ("printing", "paused") else ""
                    operation = self.operation
                    if operation and set(operation["resources"]) & {
                        "motion",
                        "filament",
                    }:
                        operation["detail"] = stage
            self.sequence += 1
        if model.active(self.status) or self.status.get("webhooks", {}).get(
            "state"
        ) in ("error", "shutdown"):
            self.wake_screen()

    def stream_message(self, message):
        if "error" in message:
            raise RuntimeError(
                "Moonraker subscription rejected: " + str(message["error"])
            )
        method = message.get("method")
        if method == "notify_status_update":
            params = message["params"]
            self.merge_status(params[0], params[1] if len(params) > 1 else None)
            self.stream_connected = True
        elif method == "notify_gcode_response":
            line = message.get("params", [""])[0]
            if line.startswith("!!"):
                self.event("Printer error", line[2:].strip(), fault=True)
        elif "result" in message and "status" in message["result"]:
            result = message["result"]
            self.merge_status(result["status"], result.get("eventtime"), full=True)
            self.stream_connected = True
        elif method in (
            "notify_klippy_shutdown",
            "notify_klippy_disconnected",
            "notify_klippy_ready",
        ):
            # A subscription from the previous Klipper instance must not stop
            # HTTP polling or retain the old instance's partial CFS schema.
            self.stream_connected = False
            self.connected = False
            self.sequence += 1
            raise EOFError("Klipper state changed; renew the subscription")

    def stream(self):
        while True:
            ws = None
            try:
                ws = WebSocket(self.mr.base)
                ws.send(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "printer.objects.subscribe",
                        "params": {"objects": {name: None for name in OBJECTS}},
                    }
                )
                while True:
                    self.stream_message(ws.receive())
            except Exception:
                self.stream_connected = False
                time.sleep(2)
            finally:
                if ws:
                    ws.close()

    def current_preferences(self):
        prefs = dict(self.prefs)
        prefs["sleep_after"] = int(
            prefs.get(
                "sleep_after", self.config.get("screen", "sleep_after", fallback="600")
            )
        )
        prefs["light_off"] = int(
            self.status.get("gcode_macro _LIGHT_IDLE", {}).get(
                "off_after",
                prefs.get(
                    "light_off",
                    self.settings.get("gcode_macro _LIGHT_IDLE", {}).get(
                        "variable_off_after", 0
                    ),
                ),
            )
        )
        prefs["idle"] = int(
            self.status.get("idle_timeout", {}).get(
                "idle_timeout",
                prefs.get(
                    "idle", self.settings.get("idle_timeout", {}).get("timeout", 1800)
                ),
            )
            or 86400
        )
        return prefs

    def wake_screen(self):
        with self.lock:
            self.last_activity = time.monotonic()
            if not self.asleep:
                return
            self.asleep = False
            self.platform["asleep"] = False
            self.sequence += 1
        threading.Thread(target=self.screen_power, args=(True,), daemon=True).start()

    def update_screen_idle(self):
        active = model.active(self.status)
        urgent = bool(self.notices) or self.status.get("webhooks", {}).get("state") in (
            "error",
            "shutdown",
        )
        if active or urgent or self.operation:
            self.wake_screen()
            return
        timeout = self.current_preferences()["sleep_after"]
        with self.lock:
            sleep = (
                timeout > 0
                and not self.asleep
                and time.monotonic() - self.last_activity > timeout
            )
            if sleep:
                self.asleep = True
                self.platform["asleep"] = True
                self.sequence += 1
        if sleep:
            self.screen_power(False)

    def screen_power(self, on):
        # Serialize physical requests; a queued sleep must not override a wake.
        with self.power_lock:
            with self.lock:
                if not on and not self.asleep:
                    return
            try:
                subprocess.run(
                    ["/usr/bin/screen.sh", "on" if on else "off"],
                    timeout=5,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if on:
                    platform.set_brightness(self.prefs.get("brightness", 100))
            except (OSError, subprocess.SubprocessError):
                pass

    def prepare_icons(self):
        sizes = {
            "nozzle": (24, 24),
            "bed": (24, 24),
            "chamber": (24, 24),
            "lightbulb": (28, 28),
            "lightbulb-off": (28, 28),
            "home_fan": (30, 30),
            "pause": (24, 24),
            "play": (24, 24),
            "skip-object": (28, 28),
        }
        icons = {}
        for key, bounds in sizes.items():
            source = self.config.get("icons", key, fallback="")
            if not source:
                continue
            path = Path(source)
            if not path.is_absolute():
                path = self.config_path.parent / path
            try:
                icons[key] = storage.prepare_image(
                    path, self.cache_path, "icon-" + key, bounds
                )
            except (OSError, ValueError):
                pass
        self.platform["icons"] = icons

    def prepare_artwork(self):
        cfs = model.fitted(self.status)
        filename = "printer-with-cfs.png" if cfs else "printer-without-cfs.png"
        path = Path(__file__).resolve().parents[1] / "assets" / filename
        signature = (str(path), path.stat().st_mtime)
        if signature != self.artwork_key:
            self.platform["printer_image"] = storage.prepare_image(
                path, self.cache_path, "printer", (400, 286), lighten=True
            )
            self.artwork_key = signature

    def refresh_print_preview(self, filename):
        preview = None
        meta = {}
        try:
            meta, embedded = self.read_metadata(filename, retry_errors=True)
            if embedded:
                preview = storage.prepare_image(
                    io.BytesIO(embedded),
                    self.cache_path,
                    "print-preview",
                    (446, 236),
                    trim=True,
                )
            else:
                preview = self.prepare_thumbnail(
                    filename, meta, "print-preview", (446, 236), publish=False
                )
        except (OSError, ValueError, RuntimeError):
            with self.lock:
                if self.status.get("print_stats", {}).get("filename") == filename:
                    self.platform["print_preview_busy"] = False
                    self.sequence += 1
            return False
        with self.lock:
            if self.status.get("print_stats", {}).get("filename") != filename:
                return False
            self.platform["preview_file"] = filename
            self.platform["layer_count"] = meta.get("layer_count", 0)
            self.platform.pop("thumbnail", None)
            if preview:
                self.platform["thumbnail"] = preview
            self.platform["estimated_time"] = meta.get("estimated_time", 0)
            self.platform["print_preview_busy"] = False
            self.sequence += 1
        return True

    def read_metadata(self, path, retry_errors=False):
        meta = self.mr.metadata(path)
        embedded = None
        if (
            not meta.get("filament_type")
            or not (meta.get("filament_colors") or meta.get("extruder_colors"))
            or not meta.get("thumbnails")
        ):
            try:
                extra, embedded = gcode_metadata.read(self.mr.base, path)
                for key, value in extra.items():
                    if not meta.get(key):
                        meta[key] = value
            except (OSError, ValueError, TypeError):
                if retry_errors and not meta.get("thumbnails"):
                    raise
        return meta, embedded

    def file_thumbnails(self, files, generation=None):
        for item in files:
            if generation is not None and generation != self.file_generation:
                return
            path = item["path"]
            signature = (item.get("modified"), item.get("size"))
            if self.file_signatures.get(path) == signature:
                continue
            try:
                meta, embedded = self.read_metadata(path)
                key = "file-list-" + hashlib.sha256(path.encode()).hexdigest()[:12]
                if embedded:
                    ready = storage.prepare_image(
                        io.BytesIO(embedded), self.cache_path, key, (96, 60), trim=True
                    )
                else:
                    ready = self.prepare_thumbnail(path, meta, key, (96, 60))
                self.platform.pop(key, None)
                with self.lock:
                    if generation is not None and generation != self.file_generation:
                        return
                    previews = self.platform.setdefault("file_thumbnails", {})
                    if ready:
                        previews[path] = ready
                    else:
                        previews.pop(path, None)
                    self.file_signatures[path] = signature
                    self.sequence += 1
            except Exception:
                with self.lock:
                    if generation is not None and generation != self.file_generation:
                        return
                    self.platform.setdefault("file_thumbnails", {}).pop(path, None)
                    self.sequence += 1
                continue

    def prepare_thumbnail(self, path, meta, key, bounds, publish=True):
        thumbnails = meta.get("thumbnails") or []
        if not thumbnails:
            if publish:
                self.platform.pop(key, None)
            return
        selected = max(thumbnails, key=lambda t: t.get("width", 0) * t.get("height", 0))
        parent = Path(path).parent.as_posix()
        relative = selected["relative_path"]
        url = (
            self.mr.base
            + "/server/files/gcodes/"
            + urllib.parse.quote(
                (parent + "/" if parent != "." else "") + relative, safe="/"
            )
        )
        with urllib.request.urlopen(url, timeout=15) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Thumbnail is too large")
        ready = storage.prepare_image(
            io.BytesIO(data), self.cache_path, key, bounds, trim=True
        )
        if publish:
            self.platform[key] = ready
        return ready

    def refresh_platform(self):
        info = storage.system_identity()
        disk = platform.usb_disk()
        previous_disk = self.platform.get("usb") or {}
        if (
            disk
            and not disk.get("mounted")
            and previous_disk.get("ejected")
            and disk.get("id") == previous_disk.get("id")
        ):
            disk["ejected"] = True
        info["usb"] = disk
        info["free"] = storage.free_space(str(self.config_path.parent))
        info["wifi"] = self.wifi.status()
        info["timezone"] = platform.system_zone()
        try:
            info["stock_firmware"] = (
                subprocess.check_output(
                    ["fw_printenv", "version"], timeout=3, stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()
                .partition("=")[2]
            )
        except (OSError, subprocess.SubprocessError):
            pass
        version = Path(__file__).resolve().parents[1] / "VERSION"
        if version.exists():
            info["screen_version"] = version.read_text().strip()
        with self.lock:
            for key in (
                "recordings",
                "timelapse",
                "printer_image",
                "thumbnail",
                "file_thumbnail",
                "estimated_time",
                "screen_version",
                "version",
                "asleep",
                "icons",
                "file_thumbnails",
                "print_preview_busy",
                "starting_file",
                "preview_file",
                "layer_count",
                "stock_firmware",
            ):
                if key in self.platform:
                    info[key] = self.platform[key]
            if disk and disk.get("mounted"):
                info["usb_free"] = storage.free_space(platform.USB_MOUNT)
            uid = disk.get("id") if disk else None
            if self.usb_initialized and uid != self.usb_id:
                self.event(
                    "USB drive connected" if uid else "USB drive removed", timed=True
                )
                if not uid and self.files_source == "usb":
                    self.files = []
                    self.files_source = "gcodes"
            self.usb_initialized = True
            self.usb_id = uid
            self.platform = info
        try:
            self.prepare_artwork()
            self.prepare_icons()
        except (OSError, ValueError):
            pass

    def poll(self):
        config_at = platform_at = alerts_at = full_at = 0
        while True:
            try:
                now = time.monotonic()
                if not self.stream_connected or not self.status or now - full_at >= 5:
                    # CFS adds environment fields after its startup poll. Some
                    # Moonraker versions subscribe only to initially known keys.
                    snapshot = self.mr.status_snapshot()
                    self.merge_status(
                        snapshot["status"], snapshot.get("eventtime"), full=True
                    )
                    full_at = now
                if now - config_at > 30 or not self.settings:
                    self.settings = self.mr.settings()
                    data = self.status.get("hark_cfs_slots", {})
                    # Library fields need an explicit full object query on
                    # adapters which advertise a reduced subscription shape.
                    self.materials = list(data.get("library", [])) + list(
                        data.get("materials", [])
                    )
                    self.filaments = self.materials
                    config_at = now
                    try:
                        self.platform["timelapse"] = self.mr.request(
                            "/machine/timelapse/settings"
                        )
                    except Exception:
                        self.platform.pop("timelapse", None)
                self.update_screen_idle()
                ready = self.status.get("webhooks", {}).get("state") == "ready"
                self.preload_files()
                if ready and not self.ready:
                    for key in ("idle", "light_off", "cfs_auto_refill"):
                        if key in self.prefs:
                            try:
                                self.preference(
                                    {"key": key, "value": self.prefs[key]},
                                    self.status,
                                    self.settings,
                                )
                            except Exception:
                                pass
                self.ready = ready
                filename = self.status.get("print_stats", {}).get("filename", "")
                if filename and filename != self.print_file:
                    if self.refresh_print_preview(filename):
                        self.print_file = filename
                if now - platform_at > 5:
                    self.refresh_platform()
                    platform_at = now
                if not self.stream_connected and now - alerts_at > 5:
                    store = self.mr.request("/server/gcode_store?count=30").get(
                        "gcode_store", []
                    )
                    for row in store:
                        stamp = float(row.get("time", 0))
                        message = row.get("message", "")
                        if (
                            stamp > self.last_alert
                            and row.get("type") == "response"
                            and message.startswith("!!")
                        ):
                            self.event("Printer error", message[2:].strip(), fault=True)
                        self.last_alert = max(self.last_alert, stamp)
                    alerts_at = now
                # Cached RFID policy shares the existing command semantics;
                # this never forces a scan or performs filament motion.
                if not self.operation:
                    try:
                        self.read_policy.update(self, self.mr, self.status)
                    except Exception as error:
                        self.event("Filament information refresh failed", str(error))
            except Exception as error:
                self.connected = False
                self.sequence += 1
                self.platform["connection_error"] = str(error)
            time.sleep(3 if self.stream_connected else 1)

    def serve(self, path):
        target = Path(path)
        if target.exists():
            # Refuse to unlink a different running server's socket.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(path)
            except OSError:
                target.unlink()
            else:
                raise RuntimeError("Native touchscreen service is already running")
            finally:
                probe.close()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        os.chmod(path, 0o600)
        server.listen(2)
        for function in (self.poll, self.stream):
            threading.Thread(target=function, daemon=True).start()
        try:
            while True:
                client, _ = server.accept()
                threading.Thread(
                    target=self.client, args=(client,), daemon=True
                ).start()
        finally:
            server.close()
            target.unlink(missing_ok=True)

    def client(self, connection):
        connection.settimeout(2)
        buffer = b""
        previous = ""
        try:
            while True:
                readable, _, _ = select.select([connection], [], [], 0.1)
                if readable:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    if len(buffer) > 262144:
                        raise ValueError("Oversize UI request")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        try:
                            self.submit(json.loads(line))
                        except action_policy.ActionBusy:
                            # A rapid second tap is rejected, not queued or
                            # promoted into a modal printer error.
                            with self.lock:
                                self.sequence += 1
                        except Exception as error:
                            self.event("Request failed", str(error), fault=True)
                value = self.snapshot()
                # Stream large immutable configuration only when it changes;
                # do not send an empty operation with stale stage information.
                encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                if encoded != previous:
                    connection.sendall(encoded.encode() + b"\n")
                    previous = encoded
        except (OSError, ValueError):
            pass
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", default="/tmp/openklipper-screen.sock")
    args = parser.parse_args()
    service = Service(args.config)
    service.screen_power(True)
    service.serve(args.socket)


if __name__ == "__main__":
    main()
