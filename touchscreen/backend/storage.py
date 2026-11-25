"""Bounded host I/O for the framebuffer UI; no drawing or navigation."""

import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from functools import lru_cache
import re
from zoneinfo import available_timezones

USB_MOUNT = "/mnt/exUDISK"
_ZONES = None


def timezones():
    global _ZONES
    if _ZONES is None:
        _ZONES = sorted(
            zone
            for zone in available_timezones()
            if not zone.startswith(("posix/", "right/"))
        )
    return _ZONES


# UTC labels and public tzdata names: portable fallback when the retained
# Creality mapping script is unavailable (e.g. the desktop simulator).
_OFFSET_ZONES = (
    ("-12:00", "Etc/GMT+12"),
    ("-11:00", "Pacific/Pago_Pago"),
    ("-10:00", "Pacific/Honolulu"),
    ("-09:30", "Pacific/Marquesas"),
    ("-09:00", "America/Anchorage"),
    ("-08:00", "America/Los_Angeles"),
    ("-07:00", "America/Denver"),
    ("-06:00", "America/Chicago"),
    ("-05:00", "Etc/GMT+5"),
    ("-04:00", "America/Halifax"),
    ("-03:30", "America/St_Johns"),
    ("-03:00", "America/Sao_Paulo"),
    ("-02:00", "Etc/GMT+2"),
    ("-01:00", "Atlantic/Cape_Verde"),
    ("+00:00", "Europe/Lisbon"),
    ("+01:00", "Europe/Madrid"),
    ("+02:00", "Europe/Athens"),
    ("+03:00", "Europe/Moscow"),
    ("+03:30", "Asia/Tehran"),
    ("+04:00", "Etc/GMT-4"),
    ("+04:30", "Asia/Kabul"),
    ("+05:00", "Asia/Karachi"),
    ("+05:30", "Asia/Kolkata"),
    ("+05:45", "Asia/Kathmandu"),
    ("+06:00", "Asia/Dhaka"),
    ("+06:30", "Asia/Yangon"),
    ("+07:00", "Asia/Ho_Chi_Minh"),
    ("+08:00", "Asia/Shanghai"),
    ("+09:00", "Asia/Tokyo"),
    ("+09:30", "Australia/Darwin"),
    ("+10:00", "Australia/Melbourne"),
    ("+10:30", "Australia/Lord_Howe"),
    ("+11:00", "Pacific/Guadalcanal"),
    ("+12:00", "Pacific/Auckland"),
    ("+12:45", "Pacific/Chatham"),
    ("+13:00", "Pacific/Kanton"),
    ("+14:00", "Pacific/Kiritimati"),
)


# Human-readable display names for the stock offset choices. Keep these
# separate from tzdata identifiers and from the stock mapping itself.
_ZONE_LABELS = {
    "-12:00": ("International Date Line West", "Baker Island"),
    "-11:00": ("Samoa Standard Time", "Pago Pago"),
    "-10:00": ("Hawaii Standard Time", "Honolulu"),
    "-09:30": ("Marquesas Time", "Marquesas Islands"),
    "-09:00": ("Alaska Time", "Anchorage"),
    "-08:00": ("Pacific Time", "Los Angeles"),
    "-07:00": ("Mountain Time", "Denver"),
    "-06:00": ("Central Time", "Chicago"),
    "-05:00": ("Eastern Standard Time (EST)", ""),
    "-04:00": ("Atlantic Time", "Halifax"),
    "-03:30": ("Newfoundland Time", "St. John's"),
    "-03:00": ("Brasilia Time", "Sao Paulo"),
    "-02:00": ("Fernando de Noronha Time", "Fernando de Noronha"),
    "-01:00": ("Cape Verde Time", "Praia"),
    "+00:00": ("Western European Time", "Lisbon"),
    "+01:00": ("Central European Time", "Madrid"),
    "+02:00": ("Eastern European Time", "Athens"),
    "+03:00": ("Moscow Standard Time", "Moscow"),
    "+03:30": ("Iran Standard Time", "Tehran"),
    "+04:00": ("Gulf Standard Time", "Dubai"),
    "+04:30": ("Afghanistan Time", "Kabul"),
    "+05:00": ("Pakistan Standard Time", "Karachi"),
    "+05:30": ("India Standard Time", "Kolkata"),
    "+05:45": ("Nepal Time", "Kathmandu"),
    "+06:00": ("Bangladesh Standard Time", "Dhaka"),
    "+06:30": ("Myanmar Time", "Yangon"),
    "+07:00": ("Indochina Time", "Ho Chi Minh City"),
    "+08:00": ("China Standard Time", "Shanghai"),
    "+09:00": ("Japan Standard Time", "Tokyo"),
    "+09:30": ("Australian Central Standard Time", "Darwin"),
    "+10:00": ("Australian Eastern Time", "Melbourne"),
    "+10:30": ("Lord Howe Time", "Lord Howe Island"),
    "+11:00": ("Solomon Islands Time", "Honiara"),
    "+12:00": ("New Zealand Time", "Auckland"),
    "+12:45": ("Chatham Islands Time", "Chatham Islands"),
    "+13:00": ("Phoenix Islands Time", "Kanton"),
    "+14:00": ("Line Islands Time", "Kiritimati"),
}


@lru_cache(maxsize=1)
def timezone_presets(script_path="/usr/bin/timezone.sh"):
    """Read the stock selector's data without executing its reload/time writes."""
    try:
        source = Path(script_path).read_text()
    except OSError:
        source = ""
    pairs = re.findall(r"^UTC([+-]\d{2}:\d{2});([A-Za-z0-9_+./-]+)$", source, re.M)
    if not pairs:
        pairs = _OFFSET_ZONES
    valid = set(timezones())
    choices = [
        {
            "label": "UTC" + offset,
            "id": zone,
            "name": _ZONE_LABELS.get(offset, ("UTC" + offset, ""))[0],
            "cities": _ZONE_LABELS.get(offset, ("", ""))[1],
        }
        for offset, zone in pairs
        if zone in valid
    ]
    return sorted(choices, key=lambda choice: (choice["name"].casefold(), choice["id"]))


def free_space(path):
    try:
        return "%.1f GB free" % (shutil.disk_usage(path).free / 1e9)
    except OSError:
        return "Unavailable"


def system_identity():
    result = {"interfaces": {}}
    try:
        run = subprocess.run(
            ["ip", "-j", "-4", "addr", "show"],
            timeout=3,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        for interface in json.loads(run.stdout):
            name = interface["ifname"]
            if name == "lo":
                continue
            address = next(
                (
                    entry["local"]
                    for entry in interface.get("addr_info", [])
                    if entry.get("scope") == "global"
                ),
                "",
            )
            if name.startswith(("eth", "en")):
                result["interfaces"]["ethernet"] = {"name": name, "address": address}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    script = shutil.which("get_sn_mac.sh")
    if script:
        try:
            value = (
                subprocess.run(
                    [script, "sn"],
                    timeout=3,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                .stdout.decode()
                .strip()
            )
            if value and len(value) < 100:
                result["serial"] = value
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def request_json(mr, path):
    with urllib.request.urlopen(mr.base + path, timeout=10) as response:
        return json.load(response)["result"]


def list_recordings(mr):
    rows = request_json(mr, "/server/files/list?root=timelapse")
    return sorted(
        (row for row in rows if row.get("path", "").lower().endswith((".mp4", ".mkv"))),
        key=lambda row: -row.get("modified", 0),
    )


def _export(mr, root, name):
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts:
        raise ValueError("Invalid export filename")
    mount = Path(USB_MOUNT)
    if not os.path.ismount(mount):
        raise RuntimeError("Connect a USB drive first.")
    device = mount.stat().st_dev
    destination = mount / ("recordings" if root == "timelapse" else "logs")
    destination.mkdir(exist_ok=True)
    target = destination / parts.name
    if target.exists():
        target = target.with_name(
            target.stem + "-" + time.strftime("%Y%m%d-%H%M%S") + target.suffix
        )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination, suffix=".partial", delete=False
        ) as out:
            temporary = Path(out.name)
            url = (
                mr.base
                + "/server/files/"
                + root
                + "/"
                + urllib.parse.quote(name, safe="/")
            )
            with urllib.request.urlopen(url, timeout=30) as response:
                while True:
                    if not os.path.ismount(mount) or mount.stat().st_dev != device:
                        raise RuntimeError("USB drive was removed during export.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(target)
        return target
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def export_recording(mr, name):
    return _export(mr, "timelapse", name)


def export_logs(mr):
    logs = request_json(mr, "/server/files/list?root=logs")
    paths = [
        row["path"]
        for row in logs
        if PurePosixPath(row.get("path", "")).name in ("klippy.log", "moonraker.log")
    ]
    if not paths:
        raise RuntimeError("No printer logs are available.")
    return [_export(mr, "logs", path) for path in paths]


class FilamentReadPolicy:
    """Opt-in cached RFID refresh, separate from runout sensing and motion.

    The CFS scans a newly inserted RFID spool itself. This policy reads its
    cached records; it never sends the forced-scan/spool-actuation command.
    Unknown tags preserve the existing manual material record.
    """

    def __init__(self):
        self.ready = False
        self.startup_done = False
        self.previous_loaded = None
        self.pending_at = None

    def update(self, screen, mr, status):
        ready = (status.get("webhooks") or {}).get("state") == "ready"
        if ready and not self.ready:
            self.startup_done = False
            self.previous_loaded = None
        self.ready = ready
        cfs = status.get("k2_cfs") or {}
        if not ready or not cfs.get("online"):
            return
        state = (status.get("print_stats") or {}).get("state")
        if state in ("printing", "paused") or screen.busy(status):
            return
        loaded = tuple(cfs.get("slots_loaded") or [])
        if not self.startup_done:
            self.startup_done = True
            if screen.prefs.get("cfs_startup_read", False):
                self.pending_at = time.monotonic() + 3
        elif (
            screen.prefs.get("cfs_insertion_read", True)
            and self.previous_loaded is not None
            and any(new and not old for old, new in zip(self.previous_loaded, loaded))
        ):
            self.pending_at = time.monotonic() + 3
        self.previous_loaded = loaded
        if self.pending_at is None or time.monotonic() < self.pending_at:
            return
        self.pending_at = None
        mr.run_gcode("CFS_RFID_READ", timeout=10)
        fresh = mr.query()
        for slot, tag in (fresh.get("k2_cfs") or {}).get("rfid", {}).items():
            if not isinstance(tag, dict):
                continue
            material = next(
                (
                    f
                    for f in screen.filaments
                    if str(f.get("id")) == str(tag.get("mat_id"))
                ),
                None,
            )
            colour = str(tag.get("color") or "").lstrip("#")[-6:]
            if not material or len(colour) != 6:
                continue
            try:
                int(colour, 16)
            except ValueError:
                continue
            # Quote material names exactly as the existing editor does.
            name = material["name"].replace('"', "")
            mr.run_gcode(
                'CFS_SLOT_SET SLOT=%s NAME="%s" COLOR=%s' % (slot, name, colour),
                timeout=10,
            )


def list_usb_files():
    root = Path(USB_MOUNT)
    if not os.path.ismount(root):
        raise RuntimeError("Connect a USB drive first.")
    result = []
    for directory, folders, names in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not name.startswith(".")]
        for name in names:
            path = Path(directory) / name
            if name.startswith(".") or path.suffix.lower() not in (
                ".gcode",
                ".g",
                ".gco",
            ):
                continue
            if path.is_symlink():
                continue
            stat = path.stat()
            result.append((str(path.relative_to(root)), stat.st_mtime, stat.st_size))
    return sorted(result, key=lambda row: -row[1])


def import_usb_file(mr, name):
    """Stream the selected file into Moonraker without changing its roots."""
    import http.client
    import uuid

    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid USB filename")
    root = Path(USB_MOUNT)
    if not os.path.ismount(root):
        raise RuntimeError("USB drive was removed.")
    source = root.joinpath(*path.parts)
    if root.resolve() not in source.resolve().parents or source.is_symlink():
        raise ValueError("Invalid USB path")
    basename = path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    boundary = "native-screen-" + uuid.uuid4().hex
    fields = []
    for key, value in (("root", "gcodes"), ("path", "usb_imports")):
        fields.append(
            (
                '--%s\r\nContent-Disposition: form-data; name="%s"'
                "\r\n\r\n%s\r\n" % (boundary, key, value)
            ).encode()
        )
    fields.append(
        (
            '--%s\r\nContent-Disposition: form-data; name="file"; '
            'filename="%s"\r\nContent-Type: application/octet-stream'
            "\r\n\r\n" % (boundary, basename)
        ).encode()
    )
    prefix = b"".join(fields)
    suffix = ("\r\n--%s--\r\n" % boundary).encode()
    url = urllib.parse.urlsplit(mr.base)
    connection_type = (
        http.client.HTTPSConnection
        if url.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(url.hostname, url.port, timeout=30)
    try:
        with source.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            connection.putrequest("POST", url.path.rstrip("/") + "/server/files/upload")
            connection.putheader(
                "Content-Type", "multipart/form-data; boundary=" + boundary
            )
            connection.putheader(
                "Content-Length", str(len(prefix) + size + len(suffix))
            )
            connection.endheaders()
            connection.send(prefix)
            remaining = size
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("USB file changed during import.")
                connection.send(chunk)
                remaining -= len(chunk)
            connection.send(suffix)
        response = connection.getresponse()
        payload = json.loads(response.read())
        if response.status >= 400:
            raise RuntimeError(
                payload.get("error", {}).get("message", "USB import failed")
            )
        return payload["result"]["item"]["path"]
    finally:
        connection.close()


def prepare_image(source, cache, name, bounds, lighten=False, trim=False):
    """Decode and scale once in a service worker, never in the UI frame loop."""
    from PIL import Image, ImageChops

    if isinstance(source, (str, Path)):
        original = Path(source)
        signature = (
            str(original.resolve())
            + ":"
            + str(original.stat().st_mtime_ns)
            + ":"
            + str(bounds)
            + ":"
            + str(lighten)
        )
    else:
        signature = (
            hashlib.sha256(source.getvalue()).hexdigest() + str(bounds) + str(lighten)
        )
    if trim:
        signature += ":trim-alpha"
    digest = hashlib.sha256(signature.encode()).hexdigest()[:16]
    target = Path(cache) / (name + "-" + digest + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, (str, Path)):
        original = Path(source)
        if target.exists() and target.stat().st_mtime >= original.stat().st_mtime:
            return str(target)
        image = Image.open(original)
    else:
        image = Image.open(source)
    with image:
        image = image.convert("RGBA")
        if trim:
            visible = image.getchannel("A").getbbox()
            if not visible:
                raise ValueError("Thumbnail contains no visible pixels")
            image = image.crop(visible)
        factor = min(bounds[0] / image.width, bounds[1] / image.height)
        image = image.resize(
            (max(1, round(image.width * factor)), max(1, round(image.height * factor))),
            Image.LANCZOS,
        )
        if lighten:
            image = ImageChops.lighter(
                image, Image.new("RGBA", image.size, (7, 9, 12, 0))
            )
        canvas = Image.new("RGBA", bounds, (0, 0, 0, 0))
        canvas.paste(
            image,
            ((bounds[0] - image.width) // 2, (bounds[1] - image.height) // 2),
            image,
        )
        temporary = target.with_suffix(".new.png")
        canvas.save(temporary)
        temporary.replace(target)
    return str(target)
