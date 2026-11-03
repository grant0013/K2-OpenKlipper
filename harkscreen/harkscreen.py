#!/usr/bin/env python3
"""Hark Screen: a themeable touchscreen UI for the Creality K2 on mainline Klipper.

Draws with Pillow straight to the 480x800 framebuffer, reads the Goodix touch
panel from evdev, and gets its data from Moonraker. Look and feel come from
harkscreen.cfg in the printer config folder (colours, font, logo, pages,
sleep), which is re-read whenever it changes.

Usage: harkscreen.py [config path]
"""
import configparser, hashlib, json, os, re, struct, subprocess, sys, threading
import time, urllib.parse, urllib.request
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

CONFIG_DIR = "/mnt/UDISK/printer_data_mainline/config"
GCODE_DIR = "/mnt/UDISK/printer_data_mainline/gcodes"
CFG_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    CONFIG_DIR, "harkscreen.cfg")
FB = "/dev/fb0"
TOUCH = "/dev/input/event0"
# Panel framebuffer is portrait; the panel is mounted landscape, so the UI
# draws at W x H and show() rotates it (screen.rotate).
FB_W, FB_H = 480, 800
W, H = 800, 480
HEADER_H, NAV_W = 64, 120

DEFAULTS = {
    "brand": {"name": "K2 OpenKlipper", "logo": "klipper-logo.png",
              "idle_logo": "yes"},
    "theme": {"background": "#12171d", "panel": "#1b232c",
              "panel_edge": "#3c4b5a", "accent": "#d9434a",
              "text": "#eef2f6", "muted": "#8a97a6", "ok": "#2ecc71",
              "warn": "#f5a623", "error": "#ff6b6b",
              "font": "/etc/sysConfig/defData/SourceHanSansCN-Normal.otf",
              "font_scale": "1.0"},
    "screen": {"moonraker": "http://127.0.0.1:7126", "refresh": "1.0",
               "sleep_after": "600", "pages": "status,cfs,controls,machine,wifi",
               "touch_max_x": "480", "touch_max_y": "800",
               "touch_swap_xy": "no", "touch_invert_x": "no",
               "touch_invert_y": "no", "rotate": "90",
               "timezone": "auto"},
}

OBJECTS = {
    "print_stats": ["state", "filename", "message"],
    "virtual_sdcard": ["progress", "file_position", "is_active"],
    "display_status": ["progress", "message"],
    "extruder": ["temperature", "target"],
    "heater_bed": ["temperature", "target"],
    "temperature_sensor chamber_temperature": ["temperature"],
    "fan": ["speed", "rpm"],
    "heater_fan hotend_fan": ["speed", "rpm"],
    "hark_fans": ["part", "hotend", "min_duty"],
    "fan_generic auxiliary_fan": ["speed"],
    "fan_generic exhaust_fan": ["speed"],
    "gcode_macro _CHAMBER_FAN": ["manual"],
    "hark_chamber": ["enabled", "requested"],
    "toolhead": ["homed_axes"],
    "gcode_move": ["gcode_position", "homing_origin"],
    "gcode_macro START_PRINT": ["z_offset"],
    "k2_cfs": ["fitted", "online", "active_slot", "feed_slot", "running_out",
               "runout_next",
               "temperature", "humidity", "slot_stats", "change_pending"],
    "hark_cfs_slots": ["slots"],
    "hark_plr": ["available", "filename", "z"],
    "filament_switch_sensor filament_sensor": ["filament_detected"],
    "k2_motor_bus": ["motors_ready"],
    "output_pin chamber_led": ["value"],
    "webhooks": ["state", "state_message"],
    "idle_timeout": ["state", "idle_timeout"],
    "gcode_macro _LIGHT_IDLE": ["off_after"],
    "pa_pressure_log": ["last"],
    "hark_calib": ["pending"],
    "gcode_macro _PA_STATIC_SAVE": ["last"],
    "gcode_macro _PA_AUTO": ["on_load"],
    "gcode_macro _STAGE": ["text"],
    "hark_update": ["installed", "latest", "update_available", "updating",
                    "notes"],
}

# On a K2 driven by a separate PC (moonraker: http://<pc>:7126) an update
# lands on the PC only. This program runs on the K2 board, so it brings
# itself in step: while idle it fetches the release the PC runs (sync()).
SELF = os.path.realpath(__file__)
VERSION_FILE = os.path.join(os.path.dirname(SELF), "VERSION")
# Holds the version we updated from until the new copy has drawn a frame
UPDATED_MARK = os.path.join(os.path.dirname(SELF), ".just_updated")
# A release whose screen failed to start: not fetched again
BAD_VERSION_FILE = os.path.join(os.path.dirname(SELF), ".bad_version")
SYNC_URL = ("https://raw.githubusercontent.com/grant0013/K2-OpenKlipper/"
            "v%s/harkscreen/harkscreen.py")

# Choices made on the Settings page, kept beside harkscreen.cfg (updates
# keep the config folder). Klipper's own settings are sent again whenever
# Klipper (re)starts, so no config file needs editing.
PREFS_FILE = os.path.join(os.path.dirname(os.path.abspath(CFG_PATH)),
                          "harkscreen-settings.json")
BRIGHTNESS_SH = "/usr/bin/brightness.sh"   # Creality's backlight control
BRIGHTNESS = (25, 50, 75, 100)
# Temperature box presets (degrees C), as stock's screen offered
TEMP_PRESETS = {
    "extruder": (("Off", 0), ("PLA", 220), ("PETG", 250), ("ABS", 260),
                 ("TPU", 230)),
    "heater_bed": (("Off", 0), ("PLA", 60), ("PETG", 80), ("ABS", 100),
                   ("TPU", 50)),
    "chamber": (("Off", 0), ("45°", 45), ("50°", 50), ("55°", 55),
                ("60°", 60)),
}
TEMP_NAMES = {"extruder": "Nozzle", "heater_bed": "Bed",
              "chamber": "Chamber heater"}
# (label, M106 fan index, status object)
FANS = (("Part fan", 0, "fan"), ("Side fan", 2, "fan_generic auxiliary_fan"),
        ("Exhaust fan", 1, "fan_generic exhaust_fan"))
FAN_STEPS = (0, 25, 50, 75, 100)
SLEEP_AFTER = ((120, "2 min"), (300, "5 min"), (600, "10 min"), (0, "Never"))
# G-code storage: Klipper and Moonraker read GCODE_LINK; it points at the
# internal folder or at a folder on the USB drive the K2 mounts itself
GCODE_LINK = "/mnt/UDISK/printer_data_mainline/gcodes"
GCODE_INTERNAL = "/mnt/UDISK/printer_data/gcodes"
USB_MOUNT = "/mnt/exUDISK"
GCODE_USB = USB_MOUNT + "/gcodes"
# Time-lapse videos follow the same choice (logs too, at the next restart:
# system/storage-links.sh at start-up)
TIMELAPSE_LINK = "/mnt/UDISK/printer_data_mainline/timelapse"
TIMELAPSE_INTERNAL = "/mnt/UDISK/printer_data/timelapse"
TIMELAPSE_USB = USB_MOUNT + "/timelapse"
LOGS_LINK = "/mnt/UDISK/printer_data_mainline/logs"
# The K2 mounts its USB port's drive (sda, or sda1) at USB_MOUNT; its own
# memory is mmcblk0, so only sdX can ever be formatted here
USB_DEV = "sda"
USB_DEV_RE = r"sd[a-z]$"
USB_LABEL = "K2PRINTER"
LIGHT_OFF = ((0, "Never"), (300, "5 min"), (600, "10 min"), (1800, "30 min"))
# 99999999 s is stock's "never"
IDLE_OFF = ((900, "15 min"), (1800, "30 min"), (3600, "1 hour"),
            (99999999, "Never"))
ACCENTS = (("", "Theme"), ("#d9434a", "Red"), ("#f39c12", "Orange"),
           ("#2ecc71", "Green"), ("#3d8bfd", "Blue"), ("#9b59b6", "Purple"))
ZONES = (("UK", "Europe/London"), ("Ireland", "Europe/Dublin"),
         ("Central Europe", "Europe/Berlin"),
         ("Eastern Europe", "Europe/Helsinki"),
         ("US Eastern", "America/New_York"),
         ("US Central", "America/Chicago"),
         ("US Mountain", "America/Denver"),
         ("US Pacific", "America/Los_Angeles"),
         ("Canada Atlantic", "America/Halifax"),
         ("Brazil", "America/Sao_Paulo"), ("India", "Asia/Kolkata"),
         ("China", "Asia/Shanghai"), ("Japan", "Asia/Tokyo"),
         ("Australia East", "Australia/Sydney"),
         ("Australia West", "Australia/Perth"),
         ("New Zealand", "Pacific/Auckland"), ("UTC", "UTC"))
ZONEINFO = "/usr/share/zoneinfo"

# The last PA calibration result the user has dismissed (its stamp)
PA_ACK_FILE = "/mnt/UDISK/harkscreen/.pa_result_ack"
# An update offer put off with Later: {"version": tag, "until": epoch}
UPDATE_SNOOZE_FILE = "/mnt/UDISK/harkscreen/.update_snooze"
UPDATE_SNOOZE = 24 * 3600
# A PA result nobody dismissed goes away after this (seconds)
PA_RESULT_TIMEOUT = 600
# Error lines shown while idle; while printing or paused every one is shown
IDLE_ALERTS = ("Safety watch", "Chamber heater", "safety watch")
# Notices that wait their turn (no waking the screen, not over other boxes)
QUIET_NOTICES = ("update", "plr", "usb_new", "usb_unreadable", "usb_busy",
                 "usb_done")


class Config:
    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.reload()

    def reload(self):
        cp = configparser.ConfigParser()
        cp.read_dict(DEFAULTS)
        try:
            self.mtime = os.path.getmtime(self.path)
            cp.read(self.path)
        except OSError:
            self.mtime = None
        except configparser.Error as e:
            # A broken edit (e.g. a line with no "key: value") keeps the last
            # good settings, or the defaults, rather than stopping the screen.
            print("harkscreen.cfg not loaded:", e, flush=True)
            if getattr(self, "cp", None) is not None:
                return
            cp = configparser.ConfigParser()
            cp.read_dict(DEFAULTS)
        self.cp = cp

    def changed(self):
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return self.mtime is not None

    def get(self, section, key):
        return self.cp.get(section, key)

    def flag(self, section, key):
        try:
            return self.cp.getboolean(section, key)
        except ValueError:
            return DEFAULTS[section][key] == "yes"

    def num(self, section, key):
        try:
            return self.cp.getfloat(section, key)
        except ValueError:
            return float(DEFAULTS[section][key])


class Wifi:
    """wpa_supplicant over its control socket (the K2 has no wpa_cli)."""
    CTRL = "/etc/wifi/wpa_supplicant/sockets/wlan0"

    def __init__(self):
        self.networks = []           # (ssid, dbm, secured), strongest first
        self.busy = False
        self.message = ""
        self.lock = threading.Lock()

    def cmd(self, command, timeout=5):
        import socket
        local = "/tmp/harkscreen_wpa_%d_%d" % (os.getpid(),
                                               threading.get_ident())
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            if os.path.exists(local):
                os.unlink(local)
            s.bind(local)
            s.settimeout(timeout)
            s.sendto(command.encode(), self.CTRL)
            return s.recv(16384).decode(errors="replace")
        finally:
            s.close()
            try:
                os.unlink(local)
            except OSError:
                pass

    def status(self):
        try:
            st = dict(l.split("=", 1) for l in self.cmd("STATUS").splitlines()
                      if "=" in l)
            sig = dict(l.split("=", 1)
                       for l in self.cmd("SIGNAL_POLL").splitlines()
                       if "=" in l)
            st["rssi"] = sig.get("RSSI", "")
            return st
        except Exception:
            return {}

    def scan(self):
        def run():
            self.busy, self.message = True, "Scanning..."
            try:
                self.cmd("SCAN")
                time.sleep(4)
                best = {}
                for line in self.cmd("SCAN_RESULTS").splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) < 5 or not parts[4]:
                        continue
                    ssid, dbm = parts[4], int(parts[2])
                    secured = any(k in parts[3] for k in ("WPA", "WEP",
                                                          "SAE"))
                    if ssid not in best or dbm > best[ssid][1]:
                        best[ssid] = (ssid, dbm, secured)
                self.networks = sorted(best.values(), key=lambda n: -n[1])
                self.message = "%d networks found" % len(self.networks)
            except Exception as e:
                self.message = "Scan failed: %s" % e
            self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def connect(self, ssid, password, on_done=None):
        """Add and switch to ssid; roll back to the old network on failure."""
        def run():
            self.busy, self.message = True, "Connecting to %s..." % ssid
            old = [l.split("\t")[0]
                   for l in self.cmd("LIST_NETWORKS").splitlines()[1:]]
            nid = self.cmd("ADD_NETWORK").strip()
            ok = False
            try:
                self.cmd("SET_NETWORK %s ssid %s" % (nid,
                                                     ssid.encode().hex()))
                if password:
                    self.cmd('SET_NETWORK %s psk "%s"' % (nid, password))
                else:
                    self.cmd("SET_NETWORK %s key_mgmt NONE" % nid)
                self.cmd("SELECT_NETWORK %s" % nid)
                deadline = time.time() + 25
                while time.time() < deadline:
                    time.sleep(1)
                    st = self.status()
                    if (st.get("wpa_state") == "COMPLETED"
                            and st.get("ssid") == ssid):
                        subprocess.call(["killall", "-USR1", "udhcpc"])
                        for _ in range(10):
                            time.sleep(1)
                            if self.status().get("ip_address"):
                                ok = True
                                break
                        break
            except Exception as e:
                self.message = "Error: %s" % e
            if ok:
                # Keep the old networks as fallbacks; drop an older copy of
                # this ssid.
                for line in self.cmd("LIST_NETWORKS").splitlines()[1:]:
                    parts = line.split("\t")
                    if parts[0] != nid and len(parts) > 1 and parts[1] == ssid:
                        self.cmd("REMOVE_NETWORK %s" % parts[0])
                self.cmd("ENABLE_NETWORK all")
                self.cmd("SAVE_CONFIG")
                self.message = "Connected to %s (%s)" % (
                    ssid, self.status().get("ip_address", "?"))
            else:
                self.cmd("REMOVE_NETWORK %s" % nid)
                self.cmd("ENABLE_NETWORK all")
                self.cmd("REASSOCIATE")
                subprocess.call(["killall", "-USR1", "udhcpc"])
                self.message = ("Could not join %s; back on the previous "
                                "network" % ssid)
            self.busy = False
            if on_done:
                on_done(ok)
        threading.Thread(target=run, daemon=True).start()


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, IOError):
        return ""


def own_version():
    return read_text(VERSION_FILE).lstrip("vV")


def version_key(version):
    """'0.4.11-alpha.1' -> ((0, 4, 11), (0, 1)); a final release sorts
    after its alpha/beta/rc (as hark_update)."""
    v = (version or "").strip().lstrip("vV")
    main, _, pre = v.partition("-")
    nums = tuple(int(n) for n in re.findall(r"\d+", main)[:3])
    nums += (0,) * (3 - len(nums))
    if not pre:
        return nums, (9, 0)
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get(
        re.sub(r"[^a-z]", "", pre.lower()), 0)
    num = re.findall(r"\d+", pre)
    return nums, (rank, int(num[0]) if num else 0)


def remote_moonraker(url):
    host = urllib.parse.urlparse(url).hostname or ""
    return host not in ("", "127.0.0.1", "localhost", "::1")


def fetch_screen(version):
    """This program as published in release version, checked to be one."""
    with urllib.request.urlopen(SYNC_URL % version, timeout=30) as r:
        code = r.read()
    if len(code) < 50000 or b"class Screen" not in code:
        raise ValueError("download is not the screen program")
    compile(code, "harkscreen.py", "exec")
    return code


def install_screen(code, version):
    """Swap in the new copy, keeping this one as harkscreen.py.prev."""
    import shutil
    shutil.copy2(SELF, SELF + ".prev")
    tmp = SELF + ".new"
    with open(tmp, "wb") as f:
        f.write(code)
    os.chmod(tmp, 0o755)
    with open(UPDATED_MARK, "w") as f:
        f.write(own_version())
    os.replace(tmp, SELF)
    with open(VERSION_FILE, "w") as f:
        f.write(version + "\n")


def load_prefs():
    try:
        with open(PREFS_FILE) as f:
            prefs = json.load(f)
        return prefs if isinstance(prefs, dict) else {}
    except Exception:
        return {}


def save_prefs(prefs):
    tmp = PREFS_FILE + ".new"
    with open(tmp, "w") as f:
        json.dump(prefs, f, indent=1, sort_keys=True)
    os.replace(tmp, PREFS_FILE)


def set_brightness(level):
    if os.path.exists(BRIGHTNESS_SH):
        try:
            subprocess.call(["sh", BRIGHTNESS_SH, str(int(level))],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=5)
        except Exception as e:
            print("brightness: %s" % e, flush=True)


def system_zone():
    """The printer's time zone as set in Creality's settings (uci)."""
    try:
        out = subprocess.check_output(
            ["uci", "-q", "get", "system.@system[0].zonename"],
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        if out:
            return out
    except Exception:
        pass
    return time.strftime("%Z")


def set_system_zone(name):
    """Set the printer's own time zone, as Creality's settings screen did
    (/usr/bin/timezone.sh): the uci zonename for the next start, and
    /tmp/localtime (/etc/localtime links there) for now. No service
    reload: nothing else needs it, and it also resets the clock chip."""
    t0 = time.time()
    for cmd in (["uci", "set", "system.@system[0].zonename=%s" % name],
                ["uci", "commit", "system"]):
        try:
            subprocess.call(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=10)
        except Exception as e:
            print("time zone: %s failed: %s" % (cmd[0], e), flush=True)
    path = os.path.join(ZONEINFO, name)
    if os.path.isfile(path):
        try:
            tmp = "/tmp/localtime.new"
            if os.path.lexists(tmp):
                os.remove(tmp)
            os.symlink(path, tmp)
            os.replace(tmp, "/tmp/localtime")
        except OSError as e:
            print("time zone: %s" % e, flush=True)
    time.tzset()
    print("time zone: %s (%.1f s)" % (name, time.time() - t0), flush=True)


def klipper_prefs(prefs, idle):
    """G-code that applies the Settings page's Klipper choices."""
    lines = []
    if "light_off" in prefs:
        lines.append("SET_GCODE_VARIABLE MACRO=_LIGHT_IDLE VARIABLE=off_after"
                     " VALUE=%d" % int(prefs["light_off"]))
        if idle:
            # restart the countdown with the new time
            lines.append("_LIGHT_IDLE")
    if "idle" in prefs:
        lines.append("SET_IDLE_TIMEOUT TIMEOUT=%d" % int(prefs["idle"]))
    if "z_offset" in prefs:
        lines.append("SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=z_offset"
                     " VALUE=%.3f" % float(prefs["z_offset"]))
    return "\n".join(lines)


def free_gb(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return None


def storage_state():
    """Where G-codes go now, and whether a USB drive is there."""
    try:
        target = os.readlink(GCODE_LINK)
    except OSError:
        target = None
    return {"on": "usb" if target == GCODE_USB else
            ("internal" if target == GCODE_INTERNAL else None),
            "usb": os.path.ismount(USB_MOUNT),
            "internal_free": free_gb("/mnt/UDISK"),
            "usb_free": free_gb(USB_MOUNT) if os.path.ismount(USB_MOUNT)
            else None}


def point_link(link, target):
    os.makedirs(target, exist_ok=True)
    tmp = link + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)


def point_gcodes(where):
    """Point the G-code and time-lapse folders at the USB drive or back at
    internal storage. Moonraker must restart to read them."""
    if where == "usb" and not os.path.ismount(USB_MOUNT):
        raise OSError("no USB drive")
    usb = where == "usb"
    point_link(GCODE_LINK, GCODE_USB if usb else GCODE_INTERNAL)
    # time-lapse: only once start-up has made it a link
    if os.path.islink(TIMELAPSE_LINK):
        point_link(TIMELAPSE_LINK, TIMELAPSE_USB if usb
                   else TIMELAPSE_INTERNAL)


def usb_disk():
    """The drive in the K2's USB port, or None: device, size, a name for
    it (vendor model), an id to remember it by and whether it is mounted."""
    base = "/sys/block/" + USB_DEV
    try:
        size = int(read_text(base + "/size") or 0) * 512
    except ValueError:
        size = 0
    if size <= 0:
        return None
    model = " ".join(x for x in (read_text(base + "/device/vendor"),
                                 read_text(base + "/device/model")) if x)
    return {"dev": USB_DEV, "bytes": size, "gb": size / 1e9,
            "model": model or "USB drive",
            "id": "%s-%d" % (model, size),
            "mounted": os.path.ismount(USB_MOUNT)}


def usb_in_use():
    """G-code, time-lapse or log folders on the USB drive right now."""
    return [os.path.basename(link) for link in
            (GCODE_LINK, TIMELAPSE_LINK, LOGS_LINK)
            if os.path.realpath(link).startswith(USB_MOUNT + "/")]


def format_usb(dev=USB_DEV, mount=USB_MOUNT):
    """Erase the USB drive and make it one FAT32 volume (readable on
    Windows and macOS too; FAT32 uses the kernel's own fast driver, unlike
    NTFS's FUSE one). The whole drive, no partition table: the K2 mounts
    sda itself when it has no sda1. Refused for anything but sdX, odd
    sizes, or a drive holding the folders in use."""
    if not re.match(USB_DEV_RE, dev):
        raise ValueError("not a USB drive: %s" % dev)
    path = "/dev/" + dev
    size = int(read_text("/sys/block/%s/size" % dev) or 0) * 512
    if not 256e6 <= size <= 2.2e12:
        raise ValueError("drive size %.1f GB not supported" % (size / 1e9))
    busy = usb_in_use()
    if busy:
        raise OSError("in use for %s" % ", ".join(busy))
    with open("/proc/mounts") as f:
        mounted = [line.split()[1] for line in f
                   if line.split()[0].startswith(path)]
    for point in mounted:
        subprocess.check_call(["umount", point])
    subprocess.check_call(["mkfs.fat", "-F", "32", "-I", "-n", USB_LABEL,
                           path], stdout=subprocess.DEVNULL,
                          stderr=subprocess.STDOUT)
    try:
        # Forget the old partitions (sda1 ...) now that sda is one volume
        import fcntl
        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.ioctl(fd, 0x125F)    # BLKRRPART
        finally:
            os.close(fd)
    except OSError:
        pass
    os.makedirs(mount, exist_ok=True)
    subprocess.check_call(["mount", "-t", "vfat", "-o", "rw,async", path,
                           mount])


def mainline_start_cmd():
    """The printer's own start command (script, config) from its boot
    hook in /etc/rc.local, or None."""
    m = re.search(r"sh (\S+/switch-to-mainline\.sh) (\S+\.cfg)",
                  read_text("/etc/rc.local"))
    return (m.group(1), m.group(2)) if m else None


def unmount_usb(mount=USB_MOUNT, tries=20):
    """Flush and unmount the USB drive; True when it is safe to remove."""
    subprocess.call(["sync"])
    for _ in range(tries):
        if not os.path.ismount(mount):
            return True
        if subprocess.call(["umount", mount], stderr=subprocess.DEVNULL) \
                == 0:
            return True
        time.sleep(2)
    return not os.path.ismount(mount)


def restart():
    os.execv(sys.executable, [sys.executable] + sys.argv)


class Moonraker:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def query(self):
        q = "&".join(urllib.parse.quote(name) + "=" + ",".join(fields)
                     for name, fields in OBJECTS.items())
        with urllib.request.urlopen(
                self.base + "/printer/objects/query?" + q, timeout=3) as r:
            return json.load(r)["result"]["status"]

    def settings(self):
        with urllib.request.urlopen(
                self.base + "/printer/objects/query?configfile=settings",
                timeout=5) as r:
            return json.load(r)["result"]["status"]["configfile"]["settings"]

    def filaments(self):
        with urllib.request.urlopen(
                self.base + "/printer/objects/query?hark_cfs_slots="
                "library,materials", timeout=5) as r:
            h = json.load(r)["result"]["status"]["hark_cfs_slots"]
        return list(h.get("library") or []) + list(h.get("materials") or [])

    def console(self, count=12):
        """The latest console lines, newest last, without temperature
        reports and echoed commands."""
        with urllib.request.urlopen(
                self.base + "/server/gcode_store?count=%d" % count,
                timeout=3) as r:
            items = json.load(r)["result"]["gcode_store"]
        out = []
        for m in items:
            line = (m.get("message") or "").strip()
            if (m.get("type") != "response" or not line
                    or line.startswith(("B:", "T0:", "ok"))
                    or "action:" in line):
                continue
            out.append(line.lstrip("/! ").replace("echo: ", ""))
        return out

    def alerts(self, count=40):
        """Recent error lines ("!! ..."): the safety watch, runouts, print
        errors. Oldest first, as (time, text)."""
        with urllib.request.urlopen(
                self.base + "/server/gcode_store?count=%d" % count,
                timeout=3) as r:
            items = json.load(r)["result"]["gcode_store"]
        out = []
        for m in items:
            line = (m.get("message") or "").strip()
            if m.get("type") == "response" and line.startswith("!!"):
                out.append((float(m.get("time") or 0.), line[2:].strip()))
        return out

    def thumbnail(self, filename):
        """The slicer's largest thumbnail (up to 400 px) of a print file as
        an RGBA image, and the model height; (None, 0) without one."""
        q = urllib.parse.quote(filename)
        with urllib.request.urlopen(
                self.base + "/server/files/metadata?filename=" + q,
                timeout=5) as r:
            meta = json.load(r)["result"]
        height = float(meta.get("object_height") or 0)
        thumbs = [t for t in meta.get("thumbnails") or []
                  if t.get("relative_path") and t.get("width", 0) <= 400]
        if not thumbs:
            return None, height
        best = max(thumbs, key=lambda t: t.get("width", 0))
        folder = os.path.dirname(filename)
        path = "/".join(p for p in (folder, best["relative_path"]) if p)
        import io
        with urllib.request.urlopen(
                self.base + "/server/files/gcodes/" + urllib.parse.quote(path),
                timeout=5) as r:
            img = Image.open(io.BytesIO(r.read()))
            img.load()
        return img.convert("RGBA"), height

    def timelapse(self):
        """moonraker-timelapse settings, or None without the add-on."""
        try:
            with urllib.request.urlopen(
                    self.base + "/machine/timelapse/settings",
                    timeout=3) as r:
                return json.load(r)["result"]
        except Exception:
            return None

    def info(self):
        with urllib.request.urlopen(self.base + "/printer/info",
                                    timeout=3) as r:
            return json.load(r)["result"]

    def gcode(self, script):
        # Long commands (homing, cleaning) outlive the UI's patience; fire
        # them from a thread so the screen keeps drawing.
        def run():
            data = json.dumps({"script": script}).encode()
            req = urllib.request.Request(
                self.base + "/printer/gcode/script", data=data,
                headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=1800).read()
            except Exception as e:
                print("gcode %r failed: %s" % (script, e), flush=True)
        threading.Thread(target=run, daemon=True).start()

    def post(self, path):
        req = urllib.request.Request(self.base + path, data=b"")
        threading.Thread(
            target=lambda: urllib.request.urlopen(req, timeout=10).read(),
            daemon=True).start()


class Eta:
    """Slicer M73 remaining-time markers, indexed by file position."""
    def __init__(self):
        self.filename = None
        self.marks = []

    def remaining_min(self, filename, file_position):
        if filename != self.filename:
            self.filename, self.marks = filename, []
            try:
                with open(os.path.join(GCODE_DIR, filename), "rb") as f:
                    data = f.read()
                self.marks = [(m.start(), int(m.group(1)))
                              for m in re.finditer(rb"^M73 P\d+ R(\d+)",
                                                   data, re.M)]
            except OSError:
                pass
        best = None
        for pos, rem in self.marks:
            if pos > file_position:
                break
            best = rem
        return best


class Touch(threading.Thread):
    """Collects taps (finger lifted) from the Goodix evdev device."""
    EV_KEY, EV_ABS, BTN_TOUCH = 1, 3, 0x14a
    ABS_X, ABS_Y, MT_X, MT_Y = 0x00, 0x01, 0x35, 0x36

    def __init__(self):
        super().__init__(daemon=True)
        self.taps = []
        self.lock = threading.Lock()
        self.x = self.y = 0
        self.last_activity = time.time()

    def run(self):
        fmt = "llHHi"
        size = struct.calcsize(fmt)
        with open(TOUCH, "rb") as f:
            while True:
                data = f.read(size)
                if len(data) < size:
                    continue
                _, _, etype, code, value = struct.unpack(fmt, data)
                if etype == self.EV_ABS and code in (self.ABS_X, self.MT_X):
                    self.x = value
                elif etype == self.EV_ABS and code in (self.ABS_Y, self.MT_Y):
                    self.y = value
                elif (etype == self.EV_KEY and code == self.BTN_TOUCH
                      and value == 0):
                    with self.lock:
                        self.taps.append((self.x, self.y))
                    self.last_activity = time.time()

    def pop(self):
        with self.lock:
            taps, self.taps = self.taps, []
        return taps


def hex_rgb(value, fallback=(255, 255, 255)):
    value = (value or "").strip().lstrip("#")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback



def local_time(ts, zone):
    """UTC timestamp to struct_time in `zone`: "auto" (the printer's own
    time zone, as set in Creality's settings), "uk" (GMT/BST) or hours."""
    zone = zone.strip().lower()
    if zone in ("", "auto"):
        return time.localtime(ts)
    if zone != "uk":
        try:
            return time.gmtime(ts + float(zone) * 3600)
        except ValueError:
            return time.gmtime(ts)
    year = time.gmtime(ts).tm_year
    def last_sunday_0100(month):
        # Last Sunday of the month, 01:00 UTC
        day = 31
        while True:
            t = time.mktime((year, month, day, 1, 0, 0, 0, 0, 0)) - time.timezone
            if time.gmtime(t).tm_mon == month and time.gmtime(t).tm_wday == 6:
                return t
            day -= 1
    bst = last_sunday_0100(3) <= ts < last_sunday_0100(10)
    return time.gmtime(ts + (3600 if bst else 0))


def local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 1))
        return s.getsockname()[0]
    except OSError:
        return "?"
    finally:
        s.close()




def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def outline(d, pts, color, width):
    """Closed polygon outline; Pillow 8.2 polygon() has no width."""
    d.line(list(pts) + [pts[0]], fill=color, width=width, joint="curve")


def ease_out(t):
    return 1 - (1 - t) ** 3


class DataThread(threading.Thread):
    """Polls Moonraker so drawing never waits on the network."""
    def __init__(self, cfg, screen):
        super().__init__(daemon=True)
        self.cfg, self.screen = cfg, screen
        self.st, self.err, self.version = {}, "Starting", 0

    def run(self):
        settings_at = wifi_at = alerts_at = 0
        thumb_file, thumb_tries, thumb_at = None, 0, 0.
        sync_at = 0.
        was_ready = False
        storage_at = 0.
        while True:
            mr = Moonraker(self.cfg.get("screen", "moonraker"))
            st, err = {}, None
            try:
                st = mr.query()
                if st.get("webhooks", {}).get("state") != "ready":
                    err = "Klipper " + st.get("webhooks", {}).get("state",
                                                                   "?")
                if time.time() - settings_at > 30:
                    self.screen.timelapse = mr.timelapse()
                    self.screen.settings = mr.settings()
                    self.screen.filaments = mr.filaments()
                    settings_at = time.time()
                # While the printer reports a stage, follow the console too
                if (st.get("gcode_macro _STAGE") or {}).get("text"):
                    self.screen.console = mr.console()
                # The print's thumbnail. Moonraker may still be reading a
                # file the slicer has just uploaded and started, so a miss
                # is retried every 15 s for the first 5 minutes.
                fn = st.get("print_stats", {}).get("filename") or ""
                if fn != thumb_file:
                    thumb_file, thumb_tries, thumb_at = fn, 0, 0.
                if (fn and (not self.screen.thumb
                            or self.screen.thumb[0] != fn
                            or self.screen.thumb[1] is None)
                        and thumb_tries < 20
                        and time.time() - thumb_at > 15):
                    thumb_tries += 1
                    thumb_at = time.time()
                    try:
                        img, height = mr.thumbnail(fn)
                        if img is None:
                            print("thumbnail: %s has none (try %d)"
                                  % (fn, thumb_tries), flush=True)
                    except Exception as e:
                        img, height = None, 0.
                        print("thumbnail: %s: %s (try %d)"
                              % (fn, e, thumb_tries), flush=True)
                    self.screen.thumb = (fn, img, height)
                    self.screen.preview_cache = {}
                ready = st.get("webhooks", {}).get("state") == "ready"
                if ready and not was_ready:
                    # Klipper (re)started: its settings went back to the
                    # config, so send the Settings page's choices again
                    self.screen.prefs_pending = True
                was_ready = ready
                usb = usb_disk()
                if usb and not self.screen.usb:
                    self.screen.usb_since = time.time()
                if not usb and self.screen.prefs.get("usb_ejected") \
                        and not self.screen.prefs.get("usb_eject_pending"):
                    # unplugged after an eject: the next drive is used again
                    self.screen.prefs["usb_ejected"] = False
                    try:
                        save_prefs(self.screen.prefs)
                    except Exception:
                        pass
                self.screen.usb = usb
                if time.time() - storage_at > 10:
                    storage_at = time.time()
                    self.keep_storage(mr, st)
                if time.time() - sync_at > 600:
                    if self.sync(mr, st):
                        sync_at = time.time()
                # Error lines: every refresh while a print runs, else every 5 s
                ps = st.get("print_stats", {}).get("state")
                if (ps in ("printing", "paused", "error")
                        or time.time() - alerts_at > 5):
                    self.screen.alerts = mr.alerts()
                    alerts_at = time.time()
            except Exception:
                try:
                    err = "Klipper " + mr.info().get("state", "starting")
                except Exception:
                    err = "Waiting for Moonraker"
            if self.screen.page == "wifi" and time.time() - wifi_at > 5:
                self.screen.wifi_status = self.screen.wifi.status()
                wifi_at = time.time()
            self.st, self.err = st, err
            self.version += 1
            time.sleep(self.cfg.num("screen", "refresh"))


    def keep_storage(self, mr, st):
        """USB storage chosen: follow the drive. Unplugged -> back to
        internal storage; plugged in again -> USB. Only while idle, on a K2
        whose Moonraker runs on the printer, and Moonraker restarts."""
        if remote_moonraker(mr.base) or not os.path.lexists(GCODE_LINK):
            return
        if st.get("print_stats", {}).get("state") in ("printing", "paused"):
            return
        want = self.screen.prefs.get("gcode_storage", "internal")
        if self.screen.prefs.get("usb_ejected"):
            want = "internal"
        state = storage_state()
        where = "usb" if want == "usb" and state["usb"] else "internal"
        if state["on"] == where:
            return
        try:
            point_gcodes(where)
        except OSError as e:
            print("storage: %s" % e, flush=True)
            return
        print("storage: G-codes now on %s" % where, flush=True)
        mr.post("/server/restart")

    def sync(self, mr, st):
        """K2 board driven by a separate PC: fetch the screen of the release
        the PC runs, while idle. Returns True when it tried (rate limit)."""
        want = ((st.get("hark_update") or {}).get("installed")
                or "").lstrip("vV")
        state = st.get("print_stats", {}).get("state")
        # Forward only: a release from before this sync would leave the
        # screen unable to follow later ones
        if (not remote_moonraker(mr.base) or not want
                or (own_version()
                    and version_key(want) <= version_key(own_version()))
                or want == read_text(BAD_VERSION_FILE)
                or st.get("webhooks", {}).get("state") != "ready"
                or state in ("printing", "paused")
                or self.screen.busy(st)):
            return False
        try:
            code = fetch_screen(want)
            install_screen(code, want)
        except Exception as e:
            print("screen sync to %s failed: %s" % (want, e), flush=True)
            return True
        print("screen updated %s -> %s, restarting"
              % (read_text(UPDATED_MARK) or "?", want), flush=True)
        self.screen.restart_pending = True
        return True


class Screen:
    ANIM_FPS = 12

    def __init__(self, cfg):
        self.cfg = cfg
        self.page = "status"
        self.confirm = None          # (question, script) awaiting a yes
        self.editor = None           # CFS slot editor state
        self.filaments = []
        self.settings = {}
        self.wifi = Wifi()
        self.wifi_status = {}
        self.wifi_page = 0
        self.kb = None               # on-screen keyboard for a password
        self.buttons = []            # (x0, y0, x1, y1, action)
        self.anims = []              # overlay animations on the current base
        self.flash = None            # (rect, start) of a pressed button
        self.transition = None       # page slide in progress
        # Unknown at start (an earlier copy may have switched the panel off):
        # main()'s set_sleep(False) then really switches it on
        self.asleep = True
        self.base = None             # last full landscape frame (no overlays)
        self.shown = None            # digest of the frame on the panel
        self.last_key = None         # view_key of the drawn base
        self.running = None          # (label, start) of a command we sent
        self.slot_menu = None        # slot sheet: load / purge / unload / set
        try:
            with open(PA_ACK_FILE) as f:
                self.pa_ack = float(f.read().strip() or 0)
        except Exception:
            self.pa_ack = 0.
        # Print-start / load / tool-change stages (_STAGE): [text, start]
        # entries, and the latest console lines while one runs
        self.stage_log = []
        self.console = []
        # Stationary PA results (PA_CAL_STATIC): set to the first stamp seen,
        # so only results after this screen started pop up
        self.pa_static_ack = None
        # When each PA result was first seen, for PA_RESULT_TIMEOUT
        self.pa_seen = {}
        self.box_kind = None
        # Notices (notice()): error lines and print errors are acknowledged
        # from the first ones seen, so only new ones pop up; an update offer
        # put off with Later stays away for UPDATE_SNOOZE
        self.alerts = []
        # The print's thumbnail: (filename, RGBA image or None, height)
        self.thumb = None
        self.preview_cache = {}
        self.restart_pending = False
        # Settings page: saved choices, the time zone picker, the zone name
        self.prefs = load_prefs()
        self.prefs_pending = False
        self.last_st = {}
        # moonraker-timelapse settings (None: not installed)
        self.timelapse = None
        # USB drive in the K2's port, since when, and a format in progress
        # or its outcome (shown as a notice)
        self.usb = None
        self.usb_since = 0.
        self.usb_job = None
        if self.prefs.get("usb_eject_pending"):
            # an eject restarted Klipper (and so this screen): finish it
            threading.Thread(target=self.finish_usb_eject,
                             daemon=True).start()
        # Temperature box (heater being set, the target shown) and fan box
        self.temp_pick = None
        self.temp_target = 0
        self.fans_open = False
        # Z offset steps made on the Controls page during this print
        self.z_adjust = 0.
        self.z_print = None
        self.zone_pick = False
        self.zone_name = None
        self.modal_on = False
        self.alert_ack = None
        self.error_ack = None
        try:
            with open(UPDATE_SNOOZE_FILE) as f:
                self.update_snooze = json.load(f)
        except Exception:
            self.update_snooze = {}
        self.dirty = True
        self.ip = "?"
        self.eta = Eta()
        self.fb = os.open(FB, os.O_RDWR)
        self.apply_config()

    def apply_config(self):
        c = self.cfg
        t = lambda k: hex_rgb(c.get("theme", k))
        self.c = {k: t(k) for k in ("background", "panel", "panel_edge",
                                    "accent", "text", "muted", "ok", "warn",
                                    "error")}
        self.c["white"] = (255, 255, 255)
        if self.prefs.get("accent"):
            self.c["accent"] = hex_rgb(self.prefs["accent"], self.c["accent"])
        scale = c.num("theme", "font_scale")
        path = c.get("theme", "font")
        self.fonts = {}
        for name, size in (("xxs", 15), ("xs", 18), ("s", 22), ("m", 28),
                           ("l", 38), ("xl", 60)):
            try:
                self.fonts[name] = ImageFont.truetype(path, int(size * scale))
            except OSError:
                self.fonts[name] = ImageFont.load_default()
        logo = c.get("brand", "logo")
        if not os.path.isabs(logo):
            logo = os.path.join(os.path.dirname(c.path), logo)
        try:
            self.logo = self.soft_logo(Image.open(logo))
        except OSError:
            self.logo = None
        pages = [p.strip() for p in c.get("screen", "pages").split(",")]
        self.pages = self.all_pages = [
            p for p in pages
            if p in ("status", "cfs", "controls", "machine", "wifi",
                     "settings")]
        # Settings is always reachable, also with a harkscreen.cfg written
        # before it existed
        if "settings" not in self.pages:
            self.pages.append("settings")
        if self.page not in self.pages and self.pages:
            self.page = self.pages[0]
        self.tcache, self.glow_cache, self.bg = {}, {}, None
        self.dirty = True

    def soft_logo(self, im):
        """Fade a square logo's edges so it sits on any background. A logo
        that already has a transparent background is used as it is."""
        im = im.convert("RGBA")
        lo, _ = im.split()[3].getextrema()
        if lo < 16:
            return im
        size = im.size
        mask = Image.new("L", size, 0)
        inset = min(size) * 0.03
        ImageDraw.Draw(mask).ellipse((inset, inset, size[0] - inset,
                                      size[1] - inset), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(min(size) * 0.07))
        alpha = ImageChops.multiply(mask, im.split()[3])
        im.putalpha(alpha)
        return im

    # --- drawing helpers ---------------------------------------------
    def colour(self, color):
        return self.c.get(color, color) if isinstance(color, str) else color

    def text(self, d, xy, s, font="m", color="text", anchor="la",
             width=None):
        """Paste cached glyph masks; freetype is slow on the K2. With width,
        text that would not fit is cut short with an ellipsis."""
        s = str(s)
        if width:
            s = self.fit(s, font, width)
        key = (font, s, anchor)
        ent = self.tcache.get(key)
        if ent is None:
            f = self.fonts[font]
            x0, y0, x1, y1 = f.getbbox(s, anchor=anchor)
            m = Image.new("L", (max(1, x1 - x0), max(1, y1 - y0)), 0)
            ImageDraw.Draw(m).text((-x0, -y0), s, font=f, fill=255,
                                   anchor=anchor)
            if len(self.tcache) > 1500:
                self.tcache.clear()
            ent = self.tcache[key] = (m, x0, y0)
        m, ox, oy = ent
        self.canvas.paste(self.colour(color),
                          (int(xy[0] + ox), int(xy[1] + oy)), m)

    def fit(self, s, font, width):
        """s, cut short with an ellipsis if wider than width pixels."""
        f = self.fonts[font]
        if f.getlength(s) <= width:
            return s
        while s and f.getlength(s + "…") > width:
            s = s[:-1]
        return s.rstrip() + "…"

    def lines(self, text, font, width, count):
        """wrap() limited to count lines, the last one ending in an
        ellipsis if text was left over."""
        out = self.wrap(text, font, width)
        if len(out) > count:
            out = out[:count]
            out[-1] = self.fit(out[-1] + "…", font, width)
        return out

    def wrap(self, text, font, width):
        """Split text into lines no wider than width pixels."""
        f = self.fonts[font]
        lines = []
        for para in text.split("\n"):
            line = ""
            words = []
            for word in para.split():
                # A word wider than the line (a long file name) is broken
                while f.getbbox(word)[2] > width and len(word) > 1:
                    cut = len(word) - 1
                    while cut > 1 and f.getbbox(word[:cut])[2] > width:
                        cut -= 1
                    words.append(word[:cut])
                    word = word[cut:]
                words.append(word)
            for word in words:
                trial = (line + " " + word).strip()
                if line and f.getbbox(trial)[2] > width:
                    lines.append(line)
                    line = word
                else:
                    line = trial
            lines.append(line)
        return lines

    def background(self):
        if self.bg is None:
            top = mix(self.c["background"], self.c["panel_edge"], 0.25)
            bot = self.c["background"]
            grad = Image.new("RGB", (1, H))
            for y in range(H):
                grad.putpixel((0, y), mix(top, bot, y / (H - 1)))
            self.bg = grad.resize((W, H))
        return self.bg.copy()

    def glow(self, box, color, radius=16):
        """Soft halo behind a panel, cached per size and colour."""
        x0, y0, x1, y1 = box
        key = (x1 - x0, y1 - y0, color, radius)
        g = self.glow_cache.get(key)
        if g is None:
            pad = 22
            g = Image.new("L", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), 0)
            ImageDraw.Draw(g).rounded_rectangle(
                (pad, pad, pad + x1 - x0, pad + y1 - y0), radius=radius,
                outline=150, width=6)
            g = g.filter(ImageFilter.GaussianBlur(9))
            self.glow_cache[key] = g
        self.canvas.paste(color, (x0 - 22, y0 - 22), g)

    def panel(self, d, box, edge=None, glow=False, fill=None):
        if glow and edge:
            self.glow(box, edge)
        d.rounded_rectangle(box, radius=16, fill=fill or self.c["panel"],
                            outline=edge or self.c["panel_edge"], width=2)
        # faint top highlight
        d.line((box[0] + 16, box[1] + 2, box[2] - 16, box[1] + 2),
               fill=mix(fill or self.c["panel"], self.c["white"], 0.06))

    def button(self, d, box, label, action, color="accent", enabled=True,
               font="m", icon=None):
        col = self.colour(color)
        if enabled:
            fill = mix(self.c["panel"], col, 0.10)
            self.panel(d, box, col, fill=fill)
        else:
            d.rounded_rectangle(box, radius=16, fill=self.c["background"],
                                outline=self.c["panel_edge"], width=2)
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        tc = col if enabled else self.c["muted"]
        room = box[2] - box[0] - 16
        if icon and box[3] - box[1] > 90:
            font = "s"
        sizes = ("xl", "l", "m", "s", "xs", "xxs")
        for f in sizes[sizes.index(font):]:
            font = f
            if self.fonts[f].getlength(label) <= room:
                break
        if icon and box[3] - box[1] > 90:
            self.icon(d, icon, cx, cy - 18, 34, tc)
            self.text(d, (cx, cy + 28), label, font, tc, anchor="mm",
                      width=room)
        else:
            self.text(d, (cx, cy), label, font, tc, anchor="mm", width=room)
        if enabled:
            self.buttons.append((*box, action))

    def paste_logo(self, box):
        if not self.logo:
            return
        x0, y0, x1, y1 = box
        logo = self.logo.copy()
        logo.thumbnail((x1 - x0, y1 - y0), Image.LANCZOS)
        self.canvas.paste(logo, (x0 + (x1 - x0 - logo.width) // 2,
                                 y0 + (y1 - y0 - logo.height) // 2), logo)

    def icon(self, d, name, cx, cy, s, color, angle=0.0):
        """Small vector icons, s = overall size in px."""
        h = s / 2.0
        w = max(2, int(s / 12))
        if name == "nozzle":
            d.rectangle((cx - h * .7, cy - h, cx + h * .7, cy - h * .1),
                        outline=color, width=w)
            d.polygon([(cx - h * .45, cy - h * .1), (cx + h * .45, cy - h * .1),
                       (cx, cy + h)], outline=color, fill=color)
        elif name == "bed":
            d.rectangle((cx - h, cy + h * .45, cx + h, cy + h * .75),
                        fill=color)
            for dx in (-h * .5, 0, h * .5):
                d.arc((cx + dx - h * .2, cy - h * .9, cx + dx + h * .2,
                       cy - h * .1), 90, 270, fill=color, width=w)
                d.arc((cx + dx - h * .2, cy - h * .2, cx + dx + h * .2,
                       cy + h * .3), 270, 450, fill=color, width=w)
        elif name == "chamber":
            d.rounded_rectangle((cx - h, cy - h, cx + h, cy + h), radius=4,
                                outline=color, width=w)
            d.line((cx, cy - h * .55, cx, cy + h * .25), fill=color, width=w)
            d.ellipse((cx - h * .25, cy + h * .15, cx + h * .25, cy + h * .6),
                      fill=color)
        elif name == "fan":
            import math
            d.ellipse((cx - h * .2, cy - h * .2, cx + h * .2, cy + h * .2),
                      fill=color)
            for k in range(3):
                a = math.radians(angle + k * 120)
                b = a + math.radians(55)
                pts = [(cx + math.cos(a) * h * .25, cy + math.sin(a) * h * .25),
                       (cx + math.cos(a) * h, cy + math.sin(a) * h),
                       (cx + math.cos(b) * h * .9, cy + math.sin(b) * h * .9)]
                d.polygon(pts, fill=color)
        elif name in ("spool", "cfs"):
            d.ellipse((cx - h, cy - h, cx + h, cy + h), outline=color,
                      width=w)
            d.ellipse((cx - h * .35, cy - h * .35, cx + h * .35,
                       cy + h * .35), outline=color, width=w)
        elif name == "wifi":
            for k, r in enumerate((h, h * .66, h * .33)):
                d.arc((cx - r, cy - r + h * .4, cx + r, cy + r + h * .4),
                      225, 315, fill=color, width=w)
            d.ellipse((cx - w, cy + h * .4 - w, cx + w, cy + h * .4 + w),
                      fill=color)
        elif name == "status":
            d.arc((cx - h, cy - h, cx + h, cy + h), 135, 405, fill=color,
                  width=w)
            d.line((cx, cy, cx + h * .55, cy - h * .45), fill=color,
                   width=w)
        elif name == "controls":
            for k, f in enumerate((.3, .7, .45)):
                y = cy - h * .6 + k * h * .6
                d.line((cx - h, y, cx + h, y), fill=color, width=w)
                x = cx - h + 2 * h * f
                d.ellipse((x - h * .18, y - h * .18, x + h * .18, y + h * .18),
                          fill=color)
        elif name == "machine":
            d.rectangle((cx - h * .6, cy - h * .6, cx + h * .6, cy + h * .6),
                        outline=color, width=w)
            for k in (-.3, 0, .3):
                for x0, x1 in ((-h, -h * .6), (h * .6, h)):
                    d.line((cx + x0, cy + h * k * 1.5, cx + x1,
                            cy + h * k * 1.5), fill=color, width=w)
        elif name == "pause":
            d.rectangle((cx - h * .6, cy - h * .7, cx - h * .2, cy + h * .7),
                        fill=color)
            d.rectangle((cx + h * .2, cy - h * .7, cx + h * .6, cy + h * .7),
                        fill=color)
        elif name == "play":
            d.polygon([(cx - h * .5, cy - h * .7), (cx + h * .7, cy),
                       (cx - h * .5, cy + h * .7)], fill=color)
        elif name == "stop":
            d.rounded_rectangle((cx - h * .6, cy - h * .6, cx + h * .6,
                                 cy + h * .6), radius=3, fill=color)
        elif name == "home":
            outline(d, [(cx - h, cy), (cx, cy - h), (cx + h, cy)], color, w)
            d.rectangle((cx - h * .65, cy, cx + h * .65, cy + h * .85),
                        outline=color, width=w)
        elif name == "clean":
            for dx, dy, r in ((0, 0, .55), (.55, -.5, .25), (-.55, .5, .2)):
                x, y, r = cx + dx * h, cy + dy * h, r * h
                d.polygon([(x, y - r * 1.6), (x + r * .45, y - r * .45),
                           (x + r * 1.6, y), (x + r * .45, y + r * .45),
                           (x, y + r * 1.6), (x - r * .45, y + r * .45),
                           (x - r * 1.6, y), (x - r * .45, y - r * .45)],
                          fill=color)
        elif name == "light":
            d.ellipse((cx - h * .55, cy - h * .8, cx + h * .55, cy + h * .3),
                      outline=color, width=w)
            d.rectangle((cx - h * .3, cy + h * .35, cx + h * .3, cy + h * .7),
                        fill=color)
        elif name == "motor":
            d.ellipse((cx - h, cy - h, cx + h, cy + h), outline=color,
                      width=w)
            d.polygon([(cx + h * .1, cy - h * .7), (cx - h * .4, cy + h * .1),
                       (cx, cy + h * .1), (cx - h * .1, cy + h * .7),
                       (cx + h * .4, cy - h * .1), (cx, cy - h * .1)],
                      fill=color)
        elif name == "heat_off":
            d.ellipse((cx - h * .6, cy - h * .3, cx + h * .6, cy + h * .9),
                      outline=color, width=w)
            outline(d, [(cx - h * .45, cy), (cx, cy - h), (cx + h * .45, cy)],
                    color, w)
            d.line((cx - h, cy + h, cx + h, cy - h), fill=color, width=w)
        elif name == "restart":
            d.arc((cx - h * .8, cy - h * .8, cx + h * .8, cy + h * .8), 40,
                  330, fill=color, width=w)
            d.polygon([(cx + h * .55, cy - h * .95), (cx + h * .95,
                       cy - h * .35), (cx + h * .25, cy - h * .3)],
                      fill=color)
        elif name == "settings":
            import math
            for k in range(8):
                a = k * math.pi / 4
                d.line((cx + math.cos(a) * h * .55, cy + math.sin(a) * h * .55,
                        cx + math.cos(a) * h, cy + math.sin(a) * h),
                       fill=color, width=w + 2)
            d.ellipse((cx - h * .7, cy - h * .7, cx + h * .7, cy + h * .7),
                      outline=color, width=w)
            d.ellipse((cx - h * .25, cy - h * .25, cx + h * .25, cy + h * .25),
                      outline=color, width=w)
        elif name == "warning":
            outline(d, [(cx, cy - h), (cx + h, cy + h * .8),
                        (cx - h, cy + h * .8), (cx, cy - h)], color, w)
            d.line((cx, cy - h * .35, cx, cy + h * .25), fill=color,
                   width=w + 1)
            d.ellipse((cx - w, cy + h * .45, cx + w, cy + h * .45 + 2 * w),
                      fill=color)
        elif name == "update":
            d.ellipse((cx - h, cy - h, cx + h, cy + h), outline=color,
                      width=w)
            d.line((cx, cy - h * .55, cx, cy + h * .35), fill=color,
                   width=w + 1)
            d.polygon([(cx - h * .4, cy + h * .05), (cx + h * .4,
                       cy + h * .05), (cx, cy + h * .55)], fill=color)

    def ring(self, d, cx, cy, r, frac, color, width=8):
        d.arc((cx - r, cy - r, cx + r, cy + r), 135, 405,
              fill=self.c["panel_edge"], width=width)
        frac = max(0.0, min(1.0, frac))
        if frac > 0.005:
            d.arc((cx - r, cy - r, cx + r, cy + r), 135, 135 + 270 * frac,
                  fill=color, width=width)

    def busy(self, st):
        """Klipper is executing G-code (homing, cleaning...) outside a print."""
        ps = st.get("print_stats", {}).get("state")
        working = (st.get("idle_timeout", {}).get("state") == "Printing"
                   and ps not in ("printing", "paused"))
        if self.running and not working and \
                time.time() - self.running[1] > 4:
            self.running = None
        return working or bool(self.running and
                               time.time() - self.running[1] <= 4)

    def pa_result(self, st):
        """A PA calibration result not yet dismissed with OK, or None:
        the stationary test's (PA_CAL_STATIC), else the moving test's. A
        print starting, or PA_RESULT_TIMEOUT, dismisses it: it must not sit
        over a print start (11-01)."""
        static = (st.get("gcode_macro _PA_STATIC_SAVE") or {}).get(
            "last") or {}
        stamp = static.get("stamp")
        if self.pa_static_ack is None and "gcode_macro _PA_STATIC_SAVE" in st:
            self.pa_static_ack = stamp or 0.
        last = (st.get("pa_pressure_log") or {}).get("last") or {}
        printing = st.get("print_stats", {}).get("state") in ("printing",
                                                              "paused")
        now = time.time()
        for key in (("static", stamp), ("moving", last.get("stamp"))):
            if key[1] and key not in self.pa_seen:
                self.pa_seen[key] = now
        if printing or any(now - t > PA_RESULT_TIMEOUT
                           for k, t in self.pa_seen.items()
                           if k in (("static", stamp),
                                    ("moving", last.get("stamp")))):
            if stamp:
                self.pa_static_ack = stamp
            if last.get("stamp") and last["stamp"] != self.pa_ack:
                self.ack_pa(last["stamp"])
            return None
        if stamp and stamp != self.pa_static_ack:
            return {"static": True, "slot": static.get("slot"),
                    "saved": bool(static.get("saved")),
                    "reliable": bool(static.get("saved")),
                    "fitted": static.get("pa"),
                    "measured": static.get("measured"),
                    "scale": static.get("scale"),
                    "material": static.get("material"),
                    "reason": static.get("reason"), "stamp": stamp}
        stamp = last.get("stamp")
        if stamp and stamp != self.pa_ack:
            return last
        return None

    def ack_pa(self, stamp):
        self.pa_ack = stamp or 0.
        try:
            with open(PA_ACK_FILE, "w") as f:
                f.write(repr(self.pa_ack))
        except Exception:
            pass

    def track_stage(self, st):
        """Log each stage with the time it started; a new print start
        (after a quiet spell) starts a fresh log. Returns the current text."""
        cur = (st.get("gcode_macro _STAGE") or {}).get("text") or ""
        last = self.stage_log[-1][0] if self.stage_log else ""
        now = time.time()
        if cur != last:
            if cur and (not self.stage_log or (
                    not last and now - self.stage_log[-1][1] > 60)):
                self.stage_log = []
            self.stage_log.append([cur, now])
            self.stage_log = self.stage_log[-12:]
        return cur

    def view_key(self, st, err):
        """What the page shows, rounded as drawn: redraw only on change."""
        def r(name, key):
            return round(st.get(name, {}).get(key) or 0)
        ps = st.get("print_stats", {})
        vs = st.get("virtual_sdcard", {})
        rem = None
        if ps.get("state") == "printing":
            rem = self.eta.remaining_min(ps.get("filename", ""),
                                         vs.get("file_position", 0))
        z = st.get("gcode_move", {}).get("gcode_position", [0, 0, 0])[2]
        return json.dumps([
            self.page, err, ps.get("state"), ps.get("filename"),
            self.busy(st), self.running and self.running[0],
            r("extruder", "temperature"), r("extruder", "target"),
            r("heater_bed", "temperature"), r("heater_bed", "target"),
            r("temperature_sensor chamber_temperature", "temperature"),
            st.get("hark_chamber"),
            round((st.get("display_status", {}).get("progress")
                   or vs.get("progress") or 0) * 100),
            round(st.get("fan", {}).get("speed", 0) * 100),
            round((st.get("fan", {}).get("rpm") or 0) / 50),
            round(((st.get("heater_fan hotend_fan") or {}).get("rpm") or 0)
                  / 50),
            (st.get("heater_fan hotend_fan") or {}).get("speed"),
            st.get("hark_fans"), round(z, 2), rem,
            st.get("k2_cfs"), st.get("hark_cfs_slots"),
            st.get("filament_switch_sensor filament_sensor"),
            st.get("k2_motor_bus"), st.get("output_pin chamber_led"),
            st.get("toolhead"), st.get("webhooks", {}).get("state"),
            self.wifi.busy, self.wifi.message, self.wifi.networks,
            self.wifi_status, len(self.settings), len(self.filaments),
            self.ip, bool(self.pa_result(st)),
            (st.get("hark_calib") or {}).get("pending"),
            self.track_stage(st), self.console[-2:],
            self.notice_key(st, err),
            bool(self.thumb and self.thumb[1] is not None),
            self.prefs, self.zone_pick, self.zone_name, self.z_adjust,
            self.temp_pick, self.temp_target, self.fans_open,
            self.usb, self.usb_job and self.usb_job.get("kind"),
            self.usb_job and self.usb_job.get("title"),
            self.prefs.get("usb_ejected"),
            [(st.get(o) or {}).get("speed") for _, _, o in FANS],
            (st.get("gcode_macro _CHAMBER_FAN") or {}).get("manual"),
            (st.get("gcode_move") or {}).get("homing_origin"),
            (st.get("gcode_macro START_PRINT") or {}).get("z_offset"),
            storage_state() if self.page == "settings" else None,
            (self.timelapse or {}).get("enabled") if self.timelapse
            is not None else None,
            (st.get("idle_timeout") or {}).get("idle_timeout"),
            (st.get("gcode_macro _LIGHT_IDLE") or {}).get("off_after"),
            # the Settings page shows the time
            time.strftime("%H:%M") if self.page == "settings" else "",
            # the stage timer ticks every 2 s while a stage runs
            int(time.time() / 2) if self.stage_log and self.stage_log[-1][0]
            else 0],
            sort_keys=True, default=str)

    # --- base frame (everything static) ---------------------------------
    def render(self, st, err):
        self.buttons, self.anims = [], []
        # No CFS (not found, unplugged, or cfs_fitted: False): the CFS page
        # becomes the External spool page until a CFS is connected
        self.no_cfs = st.get("k2_cfs", {}).get("fitted") is False
        self.canvas = img = self.background()
        d = ImageDraw.Draw(img)
        # Header: logo, name, state pill
        self.paste_logo((6, 2, 66, HEADER_H - 2))
        self.text(d, (76, HEADER_H // 2), self.cfg.get("brand", "name"), "m",
                  anchor="lm")
        state, color = self.machine_state(st, err)
        col = self.colour(color)
        pw = self.fonts["s"].getbbox(state)[2] + 56
        pill = (W - 16 - pw, 14, W - 16, HEADER_H - 14)
        d.rounded_rectangle(pill, radius=18, fill=mix(self.c["background"],
                                                      col, 0.18),
                            outline=col, width=2)
        dot = (pill[0] + 20, HEADER_H // 2)
        d.ellipse((dot[0] - 6, dot[1] - 6, dot[0] + 6, dot[1] + 6), fill=col)
        self.text(d, (pill[0] + 36, HEADER_H // 2), state, "s", col,
                  anchor="lm")
        if state in ("Printing", "Heating") or state.endswith("…"):
            self.anims.append({"kind": "pulse", "center": dot, "color": col,
                               "rect": (dot[0] - 18, dot[1] - 18, dot[0] + 18,
                                        dot[1] + 18)})
        d.line((0, HEADER_H - 1, W, HEADER_H - 1), fill=self.c["panel_edge"],
               width=2)
        body = (NAV_W, HEADER_H, W, H)
        if self.editor and st.get("print_stats", {}).get("state") in (
                "printing", "paused"):
            self.editor = None
        if err:
            self.draw_offline(d, body, err,
                              st.get("webhooks", {}).get("state_message", ""))
        elif self.editor:
            self.draw_editor(d, body, st)
        else:
            getattr(self, "page_" + self.page)(d, body, st)
        self.draw_nav(d)
        # The busy banner takes the Status page's footer; other pages fill
        # the body and show the work in the header pill instead
        if (not err and self.busy(st) and not self.editor and not self.kb
                and self.page == "status"):
            self.draw_busy(d, body, st)
        # Animations of the page under a box would draw over the box
        page_anims = len(self.anims)
        box = self.top_box(st, err)
        self.box_kind = box and box[0]
        if box:
            kind, arg = box
            if kind == "confirm":
                self.draw_confirm(d)
            elif kind == "notice":
                self.draw_notice(arg)
            elif kind == "pa":
                self.draw_pa_result(arg)
            elif kind == "calib":
                self.draw_calib_pick(arg)
            elif kind == "slot":
                self.draw_slot_menu(st)
            elif kind == "temp":
                self.draw_temp_box(arg, st)
            elif kind == "fans":
                self.draw_fans_box(st)
            # main() fades a box in and out; only the box animates
            self.anims = self.anims[page_anims:]
        self.modal_on = bool(box)
        # a box that cannot show (Klipper not ready) is closed
        if err:
            self.temp_pick, self.fans_open = None, False
        return self.canvas

    def top_box(self, st, err):
        """The one box shown over the page, most important first: a question
        the user is answering, an error or safety message, a calibration
        result or pick, the slot menu, then an update offer. Boxes never
        stack."""
        if self.confirm:
            return ("confirm", None)
        if err:
            return None
        note = self.notice(st, err)
        if note and note["kind"] not in QUIET_NOTICES:
            return ("notice", note)
        res = self.pa_result(st)
        if res:
            return ("pa", res)
        pick = self.calib_pick(st)
        if pick:
            return ("calib", pick)
        if self.temp_pick:
            return ("temp", self.temp_pick)
        if self.fans_open:
            return ("fans", None)
        if self.slot_menu:
            return ("slot", None)
        if note and not self.editor and not self.kb:
            return ("notice", note)
        return None

    # --- notices: update offers, print errors, safety messages ------------
    def notice(self, st, err):
        """The box to show, most urgent first, or None."""
        if err:
            return None
        ps = st.get("print_stats", {})
        state = ps.get("state", "")
        running = state in ("printing", "paused")
        alerts = self.alerts or []
        newest = alerts[-1][0] if alerts else 0.
        if self.alert_ack is None and "print_stats" in st:
            # Screen start: what is already in the console is old news
            self.alert_ack = newest
            self.error_ack = (ps.get("filename"), ps.get("message"))
        if self.alert_ack is None:
            return None
        if state == "error" and ps.get("message") and (
                ps.get("filename"), ps.get("message")) != self.error_ack:
            return {"kind": "error", "title": "Print stopped",
                    "lines": [ps["message"]], "color": "error",
                    "ack": ((ps.get("filename"), ps.get("message")), newest)}
        fresh = [m for t, m in alerts if t > self.alert_ack
                 and (running or state == "error"
                      or any(k in m for k in IDLE_ALERTS))]
        if not fresh and not running and state != "error":
            # Lines from while idle that are not shown (start-up messages,
            # console typos) must not pop up once a print starts
            self.alert_ack = max(self.alert_ack, newest)
        if fresh:
            title = {"paused": "Print paused",
                     "error": "Print stopped"}.get(state, "Printer message")
            return {"kind": "alert", "title": title, "lines": fresh[-3:],
                    "color": "warn" if state == "printing" else "error",
                    "ack": (self.error_ack, newest)}
        plr = st.get("hark_plr") or {}
        if plr.get("available") and not running and not self.busy(st):
            # A print a power cut stopped: resume or forget it
            name = os.path.basename(plr.get("filename") or "")
            return {"kind": "plr", "title": "Print interrupted",
                    "lines": ["%s stopped at Z %.2f mm when the power went."
                              % (name, plr.get("z") or 0),
                              "Resume heats up, finds Z from the bottom"
                              " switches, purges at the chute and carries on"
                              " from where it stopped. The part must still"
                              " be stuck down."],
                    "color": "warn"}
        upd = st.get("hark_update", {})
        latest = upd.get("latest")
        snooze = self.update_snooze or {}
        if (upd.get("update_available") and not upd.get("updating")
                and not running and not self.busy(st)
                and not (snooze.get("version") == latest
                         and time.time() < snooze.get("until", 0))):
            first = ""
            for line in (upd.get("notes") or "").splitlines():
                line = line.strip().lstrip("#*- ").strip()
                if line and not line.startswith("K2 OpenKlipper v"):
                    first = line
                    break
            lines = ["Version %s is ready to install (this printer has %s)."
                     % (latest, upd.get("installed") or "?"),
                     "Settings are kept. The printer restarts (about 2 min)."]
            if first:
                lines.append(first)
            return {"kind": "update", "title": "Update available",
                    "lines": lines, "color": "accent", "version": latest}
        return self.usb_notice(st)

    def usb_notice(self, st):
        """Formatting, its result, or a drive just plugged in."""
        if self.usb_job:
            return self.usb_job
        usb = self.usb
        if (not usb or self.prefs.get("usb_ejected")
                or remote_moonraker(self.cfg.get("screen", "moonraker"))
                or st.get("print_stats", {}).get("state") in ("printing",
                                                              "paused")
                or self.busy(st)
                or usb["id"] in (self.prefs.get("usb_asked") or [])):
            return None
        waited = time.time() - self.usb_since
        name = "%s (%.0f GB)" % (usb["model"], usb["gb"])
        if usb["mounted"] and waited > 3:
            if self.prefs.get("gcode_storage") == "usb":
                return None
            free = free_gb(USB_MOUNT)
            return {"kind": "usb_new", "title": "USB drive found",
                    "lines": ["%s, %.1f GB free." % (name, free or 0),
                              "Keep print files, time-lapses and logs on it?"
                              " It saves the printer's built-in memory from"
                              " wear. Change it any time in Settings."],
                    "color": "accent", "id": usb["id"]}
        if not usb["mounted"] and waited > 10:
            return {"kind": "usb_unreadable",
                    "title": "USB drive can't be read",
                    "lines": ["%s is not in a format the printer reads."
                              % name,
                              "Format it for the printer? Everything on it"
                              " will be erased. It will still work on"
                              " Windows and Mac computers."],
                    "color": "warn", "id": usb["id"]}
        return None

    def urgent(self, st, err):
        """An error notice is waiting: wake the screen and keep it on."""
        note = self.notice(st, err)
        return bool(note and note["kind"] not in QUIET_NOTICES)

    def notice_key(self, st, err):
        note = self.notice(st, err)
        return note and [note["kind"], note["lines"]]

    def draw_notice(self, note):
        dim = Image.new("RGB", (W, H), (0, 0, 0))
        self.canvas = Image.blend(self.canvas, dim, 0.65)
        d = ImageDraw.Draw(self.canvas)
        col = self.c[note["color"]]
        box = (110, 50, W - 110, 430)
        self.panel(d, box, col, True)
        icon = "update" if note["kind"] in QUIET_NOTICES else "warning"
        self.icon(d, icon, box[0] + 46, 96, 30, col)
        if note["kind"] not in QUIET_NOTICES:
            c = (box[0] + 46, 96)
            self.anims.append({"kind": "pulse", "center": c, "color": col,
                               "big": True,
                               "rect": (c[0] - 34, c[1] - 34, c[0] + 34,
                                        c[1] + 34)})
        self.text(d, (box[0] + 80, 96), note["title"], "m", col, anchor="lm")
        width = box[2] - box[0] - 60
        rows = []
        for i, msg in enumerate(note["lines"]):
            font = "s" if i == 0 else "xs"
            for line in self.wrap(msg, font, width)[:3]:
                rows.append((line, font, "text" if i == 0 else "muted"))
        y = 150
        for line, font, colour in rows[:7]:
            self.text(d, (box[0] + 30, y), line, font, colour, anchor="lm")
            y += 34 if font == "s" else 28
        self.buttons = []
        two = {"update": (("Later", ("notice_later", note.get("version"))),
                          ("Update now", ("notice_update",))),
               "usb_new": (("Not now", ("usb_later", note.get("id"))),
                           ("Use it", ("usb_use", note.get("id")))),
               "usb_unreadable": (("Not now", ("usb_later", note.get("id"))),
                                  ("Format", ("usb_format_ask",))),
               "plr": (("Discard", ("confirm",
                                    "Forget the interrupted print?",
                                    "PLR_DISCARD")),
                       ("Resume", ("confirm",
                                   "Resume the print?\nThe part must still"
                                   " be stuck down, the bed around it"
                                   " clear.", "PLR_RESUME CONFIRM=1")))}
        if note["kind"] in two:
            (l1, a1), (l2, a2) = two[note["kind"]]
            self.button(d, (box[0] + 30, 350, W // 2 - 10, 410), l1, a1,
                        "muted", True, "m")
            self.button(d, (W // 2 + 10, 350, box[2] - 30, 410), l2, a2,
                        "accent", True, "m")
        elif note["kind"] == "usb_busy":
            c = (W // 2, 380)
            self.anims.append({"kind": "spin", "center": c, "r": 16,
                               "rect": (c[0] - 22, c[1] - 22, c[0] + 22,
                                        c[1] + 22)})
        elif note["kind"] == "usb_done":
            self.button(d, (W // 2 - 110, 350, W // 2 + 110, 410), "OK",
                        ("usb_done_ok",), "accent", True, "m")
        else:
            self.button(d, (W // 2 - 110, 350, W // 2 + 110, 410), "OK",
                        ("notice_ok", note["ack"]), "accent", True, "m")

    def calib_pick(self, st):
        """A finished flow or retraction test waiting for its best result."""
        pick = (st.get("hark_calib") or {}).get("pending") or {}
        printing = st.get("print_stats", {}).get("state") in ("printing",
                                                              "paused")
        return pick if pick.get("values") and not printing else None

    def draw_calib_pick(self, pick):
        """One button per tested value; the pick is saved to the slot."""
        dim = Image.new("RGB", (W, H), (0, 0, 0))
        self.canvas = Image.blend(self.canvas, dim, 0.65)
        d = ImageDraw.Draw(self.canvas)
        kind, slot = pick.get("kind"), pick.get("slot", "?")
        box = (90, 40, W - 90, 450)
        self.panel(d, box, self.c["accent"], True)
        title = ("Best flow pad" if kind == "flow"
                 else "Best retraction") + (
            " · external spool" if slot == "E" else " · slot %s" % slot)
        self.text(d, (W // 2, 74), title, "m", anchor="mm")
        hint = ("Lowest number where the top lines have closed up"
                if kind == "flow" else
                "Shortest retraction with no strings between the pillars")
        self.text(d, (W // 2, 112), hint, "xs", "muted", anchor="mm")
        self.buttons = []
        values = pick.get("values") or []
        cols = 3
        bw = (box[2] - box[0] - (cols + 1) * 14) // cols
        for i, v in enumerate(values):
            label = ("%d" % round(v * 100)) if kind == "flow" else (
                "%.1f mm" % v)
            bx = box[0] + 14 + (i % cols) * (bw + 14)
            by = 140 + (i // cols) * 66
            self.button(d, (bx, by, bx + bw, by + 56), label,
                        ("gcode", "CALIB_SET KIND=%s SLOT=%s VALUE=%s"
                         % (kind, slot, v)), "accent", True, "s")
        self.button(d, (W // 2 - 130, 380, W // 2 + 130, 436),
                    "Keep the old value", ("gcode", "CALIB_SKIP"), "muted",
                    True, "s")

    def draw_pa_result(self, res):
        """Calibration outcome; stays up until OK is pressed."""
        dim = Image.new("RGB", (W, H), (0, 0, 0))
        self.canvas = Image.blend(self.canvas, dim, 0.65)
        d = ImageDraw.Draw(self.canvas)
        slot = res.get("slot") or "?"
        saved = res.get("saved")
        good = res.get("reliable")
        col = self.c["ok"] if saved else (self.c["accent"] if good
                                          else self.c["warn"])
        box = (130, 70, W - 130, 410)
        self.panel(d, box, col, True)
        self.text(d, (W // 2, 112), "PA calibration · slot %s" % slot, "m",
                  anchor="mm")
        fitted = res.get("fitted")
        if saved:
            head = "Saved PA %.3f to slot %s" % (fitted, slot)
        elif good and fitted is not None:
            head = "Measured PA %.3f (not saved)" % fitted
        else:
            head = "Not saved: result not reliable"
        self.text(d, (W // 2, 168), head, "s", col, anchor="mm")
        if res.get("static"):
            if saved and res.get("scale") and res.get("scale") != 1:
                notes = ["Measured %.4f x %.2f for %s" % (
                    res.get("measured") or 0, res.get("scale"),
                    res.get("material") or "this filament")]
            elif saved:
                notes = ["Measured with the nozzle still over the chute"]
            elif res.get("reason") == "implausible":
                notes = ["%.3f is outside the expected range" % (
                    res.get("fitted") or 0), "the slot keeps its value"]
            else:
                notes = ["No result from the gauge", "the slot keeps its value"]
        elif good:
            notes = ["Fit R² %.3f · signal %.0f" % (res.get("r2") or 0,
                                                    res.get("snr") or 0)]
            if not saved and not res.get("save_requested"):
                notes.append("Analysis only: save with CFS_SLOT_SET")
        else:
            notes = (res.get("problems") or ["no result"])[:2]
        y = 214
        for note in notes:
            for line in self.wrap(note, "xs", box[2] - box[0] - 60)[:2]:
                self.text(d, (W // 2, y), line, "xs", "muted", anchor="mm")
                y += 30
        self.buttons = []
        self.button(d, (W // 2 - 110, 320, W // 2 + 110, 390), "OK",
                    ("pa_static_ok" if res.get("static") else "pa_ok",
                     res.get("stamp")), "accent", True, "m")

    def draw_slot_menu(self, st):
        dim = Image.new("RGB", (W, H), (0, 0, 0))
        self.canvas = Image.blend(self.canvas, dim, 0.6)
        d = ImageDraw.Draw(self.canvas)
        slot = self.slot_menu["slot"]
        slots = st.get("hark_cfs_slots", {}).get("slots", {})
        rec = slots.get(slot, {})
        active = st.get("k2_cfs", {}).get("active_slot")
        box = (140, 50, W - 140, 440)
        self.panel(d, box, self.c["accent"], True)
        col = hex_rgb(rec.get("color"), self.c["muted"])
        self.icon(d, "spool", box[0] + 50, box[1] + 50, 46, col)
        self.text(d, (box[0] + 90, box[1] + 32),
                  "External spool" if slot == "E" else "Slot %s" % slot, "m")
        stats = (st.get("k2_cfs", {}).get("slot_stats") or {}).get(slot)
        if stats and stats.get("recent"):
            # The slot's record: a slot that keeps failing shows in warn
            bad = stats.get("recent_failures", 0)
            self.text(d, (box[2] - 24, box[1] + 32),
                      "%d loads · %d of last %d failed"
                      % (stats.get("loads", 0), bad, stats["recent"])
                      if bad else "%d loads, none failed lately"
                      % stats.get("loads", 0),
                      "xs", "warn" if bad >= 3 else "muted", anchor="rm")
        self.text(d, (box[0] + 90, box[1] + 70), "%s · %s°C" % (
            rec.get("name", "Empty"), rec.get("nozzle_temp", "-"))
            if rec.get("loaded") else "Empty", "xs", "muted",
            width=box[2] - box[0] - 110)
        self.buttons = []
        temp = max(int(rec.get("nozzle_temp") or 0),
                   int(slots.get(active, {}).get("nozzle_temp") or 0)
                   if active else 0, 220)
        actions = []
        if slot == "E":
            # Rear spool holder: the same functions through LOAD_FILAMENT /
            # UNLOAD_FILAMENT; pressure advance is saved to slot E
            temp = max(int(rec.get("nozzle_temp") or 0), 220)
            if st.get("filament_switch_sensor filament_sensor", {}).get(
                    "filament_detected"):
                actions.append(("Load & purge", ("confirm",
                                "Load and purge the spool?\nHeats to %d°"
                                % temp, "LOAD_FILAMENT"), "accent"))
                actions.append(("Unload", ("confirm",
                                "Cut and unload the spool?\nHeats to %d°"
                                % temp, "UNLOAD_FILAMENT"), "warn"))
                actions.append(("Calibrate PA", ("confirm",
                                "Calibrate PA for this spool?\n"
                                "About 3 min, heats to %d°" % temp,
                                "PA_CAL_STATIC SLOT=E SAVE=1"), "accent"))
            else:
                actions.append(("Load", ("confirm",
                                "Push the filament in at the back until it"
                                " stops, then load?\nHeats to %d°" % temp,
                                "LOAD_FILAMENT"), "accent"))
        elif rec.get("loaded") and active == slot:
            actions.append(("Purge", ("confirm",
                            "Purge slot %s?\nHeats to %d°" % (slot, temp),
                            "CFS_LOAD_SLOT SLOT=%s" % slot), "accent"))
            actions.append(("Unload", ("confirm",
                            "Unload slot %s?\nHeats to %d°" % (slot, temp),
                            "CFS_UNLOAD_ACTIVE"), "warn"))
            actions.append(("Calibrate PA", ("confirm",
                            "Calibrate PA for slot %s?\n"
                            "About 3 min, heats to %d°" % (slot, temp),
                            "PA_CAL_STATIC SLOT=%s SAVE=1" % slot),
                            "accent"))
        elif rec.get("loaded"):
            q = ("Load slot %s and purge out %s?\nHeats to %d°"
                 % (slot, active, temp)) if active else \
                ("Load slot %s?\nHeats to %d°" % (slot, temp))
            actions.append(("Load & purge", ("confirm", q,
                            "CFS_LOAD_SLOT SLOT=%s" % slot), "accent"))
            # A new reel in any slot: load it, then calibrate it
            # With auto PA on load the load measures it already
            auto = (st.get("gcode_macro _PA_AUTO") or {}).get("on_load")
            actions.append(("Calibrate PA", ("confirm",
                            "Load slot %s, then calibrate PA?\n"
                            "About 5 min, heats to %d°" % (slot, temp),
                            ("CFS_LOAD_SLOT SLOT=%s" % slot) if auto else
                            "CFS_LOAD_SLOT SLOT=%s COOL=0\nPA_CAL_STATIC SLOT=%s"
                            " SAVE=1" % (slot, slot)), "accent"))
        if rec.get("loaded"):
            # Printed tests: the printer prints them and asks for the best
            what = "the external spool" if slot == "E" else "slot %s" % slot
            actions.append(("Flow test", ("confirm",
                            "Print flow pads for %s?\nAbout 20 min. Is the"
                            " bed clear?" % what,
                            "CALIBRATE_FLOW SLOT=%s" % slot), "accent"))
            actions.append(("Retraction test", ("confirm",
                            "Print a retraction test for %s?\nAbout 25 min."
                            " Is the bed clear?" % what,
                            "CALIBRATE_RETRACTION SLOT=%s" % slot),
                            "accent"))
        actions.append(("Set filament", ("slot_set",), "accent"))
        actions.append(("Close", ("slot_close",), "muted"))
        cols = 3
        bw = (box[2] - box[0] - (cols + 1) * 14) // cols
        for i, (label, action, color) in enumerate(actions):
            bx = box[0] + 14 + (i % cols) * (bw + 14)
            by = box[1] + 100 + (i // cols) * 84
            self.button(d, (bx, by, bx + bw, by + 72), label, action, color,
                        True, "s")

    def draw_busy(self, d, body, st):
        x0, y0, x1, y1 = body
        box = (x0 + 14, y1 - 62, x1 - 14, y1 - 10)
        self.panel(d, box, self.c["accent"], True,
                   mix(self.c["background"], self.c["accent"], 0.15))
        label = self.running[0] if self.running else "Klipper"
        stage = self.stage_log[-1][0] if self.stage_log else ""
        if stage:
            label = stage
        detail = ""
        ex = st.get("extruder", {})
        if ex.get("target") and ex.get("temperature", 0) < ex["target"] - 3:
            detail = "  ·  nozzle %d → %d°" % (ex["temperature"], ex["target"])
        self.text(d, (box[0] + 50, (box[1] + box[3]) // 2),
                  ("%s%s" if stage else "%s in progress%s") % (label, detail),
                  "s", anchor="lm", width=box[2] - box[0] - 66)
        # The banner covers the footer: its animations (the fan) stop
        self.anims = [a for a in self.anims
                      if a["rect"][3] <= box[1] or a["rect"][1] >= box[3]]
        cx, cy = box[0] + 26, (box[1] + box[3]) // 2
        self.anims.append({"kind": "spin", "center": (cx, cy), "r": 11,
                           "rect": (cx - 16, cy - 16, cx + 16, cy + 16)})

    def machine_state(self, st, err):
        if err:
            return err[:24], "error"
        ps = st.get("print_stats", {}).get("state", "")
        if self.busy(st):
            return ((self.running[0] if self.running else "Working")
                    + "…", "accent")
        if ps not in ("printing", "paused"):
            ex = st.get("extruder", {})
            if ex.get("target") and ex.get("temperature", 0) < ex["target"] - 3:
                return "Heating", "warn"
        return {"printing": ("Printing", "accent"),
                "paused": ("Paused", "warn"),
                "complete": ("Complete", "ok"),
                "cancelled": ("Cancelled", "muted"),
                "error": ("Error", "error")}.get(ps, ("Ready", "ok"))

    def draw_offline(self, d, body, err, why=""):
        x0, y0, x1, y1 = body
        cx = (x0 + x1) // 2
        # Klipper's own reason (shutdown, config error): the first lines,
        # without its generic "Once the underlying issue is corrected" tail
        why = (why or "").split("Once the underlying issue")[0].strip()
        lines = []
        for para in why.splitlines():
            if para.strip():
                lines += self.wrap(para.strip(), "xs", x1 - x0 - 60)
        lines = lines[:4]
        logo = 260 - 30 * len(lines)
        self.paste_logo((cx - logo // 2, y0 + 20, cx + logo // 2,
                         y0 + 20 + logo))
        y = y0 + 40 + logo
        self.text(d, (cx, y), "Klipper is not ready", "m", "warn",
                  anchor="mm")
        self.text(d, (cx, y + 38), err[:50], "xs", "muted", anchor="mm")
        for i, line in enumerate(lines):
            self.text(d, (cx, y + 70 + i * 26), line, "xs", anchor="mm")
        sy = y + 70 + len(lines) * 26 + 10
        self.anims.append({"kind": "spin", "center": (cx, sy), "r": 12,
                           "rect": (cx - 18, sy - 18, cx + 18, sy + 18)})

    def gauge(self, d, box, label, icon, h):
        x0, y0, x1, y1 = box
        self.panel(d, box)
        cur, tgt = h.get("temperature", 0), h.get("target", 0)
        r = min(x1 - x0, y1 - y0) // 2 - 16
        cx, cy = x0 + 16 + r, (y0 + y1) // 2
        heating = tgt and abs(cur - tgt) > 3
        col = self.c["warn"] if heating else self.c["accent"]
        frac = cur / tgt if tgt else cur / 300.0
        self.ring(d, cx, cy, r, frac, col if tgt else self.c["muted"])
        self.icon(d, icon, cx, cy, r * 0.8, self.c["muted"])
        tx = cx + r + 18
        self.text(d, (tx, y0 + 16), label, "xs", "muted")
        self.text(d, (x1 - 14, cy + 4), "%d°" % round(cur), "l",
                  anchor="rm")
        if tgt:
            self.text(d, (x1 - 14, y1 - 14), "→ %d°" % round(tgt), "xs",
                      col, anchor="rs")
        if heating:
            self.anims.append({"kind": "heat", "center": (cx, cy), "r": r,
                               "frac": frac, "color": col,
                               "rect": (cx - r - 8, cy - r - 8, cx + r + 8,
                                        cy + r + 8)})

    def page_status(self, d, body, st):
        x0, y0, x1, y1 = body
        pad = 14
        left = (x0 + pad, y0 + pad, x0 + 420, y1 - pad - 64)
        ps = st.get("print_stats", {})
        state = ps.get("state", "")
        if state in ("printing", "paused") or (
                state == "complete" and ps.get("filename")):
            glow = state == "printing"
            self.panel(d, left, self.c["accent"] if glow else None, glow)
            lx, ly = left[0] + 18, left[1]
            name = os.path.splitext(os.path.basename(
                ps.get("filename", "")))[0]
            stage = self.stage_log[-1][0] if self.stage_log else ""
            # During a print start the step timer sits top right
            self.text(d, (lx, ly + 16), name, "xs", "muted",
                      width=left[2] - lx - (150 if stage else 18))
            if state in ("printing", "paused") and stage:
                self.draw_stage(d, left, st)
                return self.draw_status_side(d, body, st, left, pad)
            prog = st.get("display_status", {}).get("progress") or \
                st.get("virtual_sdcard", {}).get("progress", 0)
            prog = max(0., min(1., prog))
            rem = self.eta.remaining_min(
                ps.get("filename", ""),
                st.get("virtual_sdcard", {}).get("file_position", 0))
            z = st.get("gcode_move", {}).get("gcode_position", [0, 0, 0])[2]
            thumb = self.thumb if self.thumb and self.thumb[0] == ps.get(
                "filename") else None
            if thumb and thumb[1] is not None:
                # The model, filled in up to the printed height
                frac = 1. if state == "complete" else (
                    z / thumb[2] if thumb[2] > 0 else prog)
                pbox = (lx, ly + 42, lx + 172, ly + 214)
                self.draw_preview(d, pbox, thumb, max(0., min(1., frac)),
                                  state == "printing")
                tx = pbox[2] + 18
                self.text(d, (tx, ly + 38), "%d%%" % round(prog * 100), "xl")
                if state == "printing" and rem is not None:
                    done = time.strftime("%H:%M", local_time(
                        time.time() + rem * 60, self.zone()))
                    hrs = "%dh %02dm" % (rem // 60, rem % 60) \
                        if rem >= 60 else "%d min" % rem
                    self.text(d, (tx, ly + 124), hrs + " left", "m",
                              width=left[2] - 18 - tx)
                    self.text(d, (tx, ly + 166), "done about " + done, "xs",
                              "muted")
                bar = (lx, ly + 232, left[2] - 18, ly + 262)
            else:
                self.text(d, (lx, ly + 44), "%d%%" % round(prog * 100),
                          "xl")
                if state == "printing" and rem is not None:
                    done = time.strftime("%H:%M", local_time(
                        time.time() + rem * 60, self.zone()))
                    hrs = "%dh %02dm" % (rem // 60, rem % 60) \
                        if rem >= 60 else "%d min" % rem
                    self.text(d, (left[2] - 18, ly + 58), hrs + " left",
                              "m", anchor="ra")
                    self.text(d, (left[2] - 18, ly + 98),
                              "done about " + done, "xs", "muted",
                              anchor="ra")
                bar = (lx, ly + 150, left[2] - 18, ly + 180)
            d.rounded_rectangle(bar, radius=15, fill=self.c["background"],
                                outline=self.c["panel_edge"], width=2)
            fill_x = bar[0] + int((bar[2] - bar[0]) * prog)
            if fill_x - bar[0] > 30:
                fill = (bar[0], bar[1], fill_x, bar[3])
                d.rounded_rectangle(fill, radius=15, fill=self.c["accent"])
                d.rounded_rectangle((fill[0] + 6, fill[1] + 4, fill[2] - 6,
                                     fill[1] + 11), radius=4,
                                    fill=mix(self.c["accent"],
                                             self.c["white"], 0.35))
                if state == "printing":
                    self.anims.append({"kind": "sheen", "rect": fill})
            ztxt = "Z %.2f mm" % z
            if thumb and thumb[2] > 0:
                ztxt += " of %.1f" % thumb[2]
            self.text(d, (lx, left[3] - 30), ztxt, "s", "muted",
                      anchor="lm")
        elif self.cfg.flag("brand", "idle_logo"):
            self.paste_logo((left[0], left[1] - 6, left[2], left[3] + 6))
        self.draw_status_side(d, body, st, left, pad)

    def preview(self, img, size, frac):
        """The thumbnail fitted into size: printed part (below frac of the
        model's height) in colour, the rest a faint grey ghost. Returns
        (image, row of the print line or None, silhouette mask)."""
        key = (id(img), size, round(frac * 200))
        hit = self.preview_cache.get(key)
        if hit:
            return hit
        alpha = img.getchannel("A")
        bbox = alpha.getbbox() or (0, 0) + img.size
        im = img.crop(bbox)
        im.thumbnail(size, Image.LANCZOS)
        a = im.getchannel("A")
        h = im.size[1]
        line = int(round(h * (1. - frac)))
        ghost = im.convert("L").convert("RGBA")
        ghost = Image.blend(ghost, Image.new("RGBA", im.size,
                                             self.c["panel"] + (255,)), 0.72)
        ghost.putalpha(a.point(lambda v: v * 0.6))
        out = ghost
        if line < h:
            done = Image.new("L", im.size, 0)
            ImageDraw.Draw(done).rectangle((0, line, im.size[0], h), fill=255)
            out = Image.composite(im, ghost, done)
        hit = (out, line if 0 < line < h else None, a)
        if len(self.preview_cache) > 40:
            self.preview_cache.clear()
        self.preview_cache[key] = hit
        return hit

    def draw_preview(self, d, box, thumb, frac, live):
        x0, y0, x1, y1 = box
        img, line, mask = self.preview(thumb[1], (x1 - x0, y1 - y0), frac)
        px = x0 + (x1 - x0 - img.size[0]) // 2
        py = y0 + (y1 - y0 - img.size[1]) // 2
        self.canvas.paste(img, (px, py), img)
        if line is not None and live:
            # The layer being printed: a shimmer along the model's outline
            strip = mask.crop((0, max(0, line - 3), img.size[0],
                               min(img.size[1], line + 4)))
            if strip.getbbox():
                y = py + max(0, line - 3)
                self.anims.append({
                    "kind": "scan", "mask": strip,
                    "rect": (px, y, px + img.size[0], y + strip.size[1])})

    def draw_stage(self, d, left, st):
        """What the print start (or a tool change) is doing: the current
        step with its timer, the steps before it with their times, and the
        latest console line."""
        lx, ly = left[0] + 18, left[1]
        now = time.time()

        def mmss(s):
            s = max(0, int(s))
            return "%d:%02d" % (s // 60, s % 60)

        text, start = self.stage_log[-1]
        lines = self.lines(text, "m", left[2] - lx - 18, 2)
        for i, line in enumerate(lines):
            self.text(d, (lx, ly + 44 + i * 36), line, "m", "accent")
        self.text(d, (left[2] - 18, ly + 16), mmss(now - start), "s",
                  anchor="ra")
        cx, cy = left[2] - 120, ly + 26
        self.anims.append({"kind": "spin", "center": (cx, cy), "r": 9,
                           "rect": (cx - 14, cy - 14, cx + 14, cy + 14)})
        # Earlier steps of this start, newest first
        y = ly + 60 + len(lines) * 36
        done = [(self.stage_log[i][0],
                 self.stage_log[i + 1][1] - self.stage_log[i][1])
                for i in range(len(self.stage_log) - 1)
                if self.stage_log[i][0]]
        width = left[2] - lx - 90
        for name, secs in reversed(done[-4:]):
            parts = self.wrap(name, "xs", width)
            label = parts[0] + ("…" if len(parts) > 1 else "")
            self.text(d, (lx, y), "• " + label, "xs", "muted")
            self.text(d, (left[2] - 18, y), mmss(secs), "xs", "muted",
                      anchor="ra")
            y += 26
        total = now - next((s for n, s in self.stage_log if n),
                           self.stage_log[0][1])
        self.text(d, (lx, left[3] - 30), "set-up %s" % mmss(total), "xs",
                  "muted", anchor="lm")
        if self.console:
            parts = self.wrap(self.console[-1], "xxs", left[2] - lx - 150)
            self.text(d, (left[2] - 18, left[3] - 30),
                      parts[0] + ("…" if len(parts) > 1 else ""), "xxs",
                      "muted", anchor="rm")

    def draw_status_side(self, d, body, st, left, pad):
        x0, y0, x1, y1 = body
        # Temperature gauges down the right
        chamber = dict(st.get("temperature_sensor chamber_temperature", {}))
        hc = st.get("hark_chamber") or {}
        if hc.get("enabled"):
            # the chamber heater's target, as the nozzle and bed show theirs
            chamber["target"] = hc.get("requested") or 0
        tiles = [("Nozzle", "nozzle", st.get("extruder", {})),
                 ("Bed", "bed", st.get("heater_bed", {})),
                 ("Chamber", "chamber", chamber)]
        tx0, tx1 = x0 + 434, x1 - pad
        th = (left[3] - left[1] - 2 * 10) // 3
        chamber_heater = (st.get("hark_chamber") or {}).get("enabled")
        for i, (label, icon, h) in enumerate(tiles):
            ty = left[1] + i * (th + 10)
            self.gauge(d, (tx0, ty, tx1, ty + th), label, icon, h)
            # Tap a gauge to set its temperature (the chamber: its heater
            # when enabled, else the fans that vent it)
            action = {"nozzle": ("temp_pick", "extruder"),
                      "bed": ("temp_pick", "heater_bed")}.get(
                icon, ("temp_pick", "chamber") if chamber_heater
                else ("fans_pick",))
            self.buttons.append((tx0, ty, tx1, ty + th, action))
        # Footer: active CFS slot with its colour, part fan
        cfs = st.get("k2_cfs", {})
        slots = st.get("hark_cfs_slots", {}).get("slots", {})
        fan = st.get("fan", {}).get("speed", 0)
        fy = y1 - pad - 54
        self.panel(d, (x0 + pad, fy, x1 - pad, fy + 54))
        # The CFS deselects its slot while just following the extruder, so
        # mid-print the slot feeding the print is feed_slot
        printing = st.get("print_stats", {}).get("state") in (
            "printing", "paused")
        slot = cfs.get("active_slot") or (
            cfs.get("feed_slot") if printing else None)
        rec = slots.get(slot, {}) if slot else {}
        sx = x0 + pad + 32
        # Room up to the fan icon
        foot_w = (x1 - pad - 170) - (sx + 28)
        warning = cfs.get("running_out") if printing else None
        self.icon(d, "spool", sx, fy + 27, 30,
                  hex_rgb(rec.get("color"), self.c["muted"]))
        if cfs.get("fitted") is False:
            # Rear spool holder: slot E's record describes it when set
            rec = slots.get("E", {})
            loaded = st.get("filament_switch_sensor filament_sensor", {}).get(
                "filament_detected")
            self.icon(d, "spool", sx, fy + 27, 30,
                      hex_rgb(rec.get("color") if loaded else None,
                              self.c["muted"]))
            name = rec.get("name") if rec.get("loaded") else ""
            self.text(d, (sx + 28, fy + 27), "External spool · %s" % (
                (name or "loaded") if loaded else "no filament"),
                "s", anchor="lm", width=foot_w)
        elif warning:
            # Early runout warning from the CFS entry sensor
            nxt = cfs.get("runout_next")
            self.text(d, (sx + 28, fy + 27), "Slot %s running out · %s" % (
                warning, ("%s takes over" % nxt) if nxt else "will pause"),
                "s", color="accent", anchor="lm", width=foot_w)
        else:
            self.text(d, (sx + 28, fy + 27), "Slot %s · %s" % (
                slot or "-", rec.get("name", "no filament") if slot else
                "no filament"), "s", anchor="lm", width=foot_w)
        fx = x1 - pad - 140
        self.text(d, (x1 - pad - 18, fy + 27), "fan %d%%" % round(fan * 100),
                  "s", anchor="rm")
        self.buttons.append((fx - 30, fy, x1 - pad, fy + 54, ("fans_pick",)))
        if fan > 0:
            self.anims.append({"kind": "fan", "center": (fx, fy + 27),
                               "speed": fan, "size": 30,
                               "rect": (fx - 18, fy + 9, fx + 18, fy + 45)})
        else:
            self.icon(d, "fan", fx, fy + 27, 30, self.c["muted"])

    def page_external(self, d, body, st):
        """No CFS: the rear spool holder, slot E, as one large card."""
        x0, y0, x1, y1 = body
        pad = 14
        rec = st.get("hark_cfs_slots", {}).get("slots", {}).get("E", {})
        loaded = st.get("filament_switch_sensor filament_sensor", {}).get(
            "filament_detected")
        idle = st.get("print_stats", {}).get("state") not in ("printing",
                                                              "paused")
        self.text(d, (x0 + pad, y0 + 10), "External spool", "s", "text")
        self.text(d, (x1 - pad, y0 + 10),
                  "filament in" if loaded else "no filament", "s",
                  "ok" if loaded else "muted", anchor="ra")
        box = (x0 + pad, y0 + 54, x1 - pad, y1 - 70)
        self.panel(d, box, self.c["accent"] if loaded else None, bool(loaded))
        if idle:
            self.buttons.append((*box, ("edit_slot", "E", rec)))
        sw = hex_rgb(rec.get("color"), self.c["panel_edge"])
        r = min(70, (box[3] - box[1]) // 2 - 24)
        sx, sy = box[0] + 30 + r, (box[1] + box[3]) // 2
        if rec.get("loaded"):
            d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=sw,
                      outline=self.c["panel_edge"], width=2)
            d.ellipse((sx - r * .38, sy - r * .38, sx + r * .38,
                       sy + r * .38), fill=self.c["panel"],
                      outline=mix(sw, (0, 0, 0), 0.3), width=2)
        else:
            d.ellipse((sx - r, sy - r, sx + r, sy + r),
                      outline=self.c["panel_edge"], width=3)
        tx = sx + r + 28
        if rec.get("loaded"):
            self.text(d, (tx, sy - 52), rec.get("name", "?"), "m",
                      width=box[2] - tx - 18)
            self.text(d, (tx, sy - 6), "%s · %s°C · bed %s°C" % (
                rec.get("type", ""), rec.get("nozzle_temp", "?"),
                rec.get("bed_temp", "?")), "s", "muted")
            extras = []
            if rec.get("pressure_advance"):
                extras.append("PA %.3f" % rec["pressure_advance"])
            if rec.get("flow_ratio"):
                extras.append("flow %.2f" % rec["flow_ratio"])
            if rec.get("retract_length"):
                extras.append("ret %.1f" % rec["retract_length"])
            self.text(d, (tx, sy + 36), "  ".join(extras), "s", "accent")
        else:
            self.text(d, (tx, sy), "Filament not set", "m", "muted",
                      anchor="lm")
        self.text(d, ((x0 + x1) // 2, y1 - 40),
                  "tap to set the filament, load, unload or calibrate PA"
                  if idle else "rear spool holder", "xs", "muted",
                  anchor="mm")

    def page_cfs(self, d, body, st):
        if st.get("k2_cfs", {}).get("fitted") is False:
            return self.page_external(d, body, st)
        x0, y0, x1, y1 = body
        pad = 14
        cfs = st.get("k2_cfs", {})
        slots = st.get("hark_cfs_slots", {}).get("slots", {})
        online = cfs.get("online")
        idle = st.get("print_stats", {}).get("state") not in ("printing",
                                                              "paused")
        self.text(d, (x0 + pad, y0 + 10),
                  "CFS %s" % ("online" if online else "offline"), "s",
                  "ok" if online else "error")
        if idle:
            self.text(d, ((x0 + x1) // 2, y0 + 14), "tap a slot to set it",
                      "xs", "muted", anchor="ma")
        if cfs.get("humidity") is not None:
            self.text(d, (x1 - pad, y0 + 10), "%s°C  %s%% RH" % (
                cfs.get("temperature"), cfs.get("humidity")), "s", "muted",
                anchor="ra")
        top = y0 + 48
        cw = (x1 - x0 - 3 * pad) // 2
        ch = (y1 - top - 2 * pad) // 2
        for i, letter in enumerate("ABCD"):
            cx = x0 + pad + (i % 2) * (cw + pad)
            cy = top + (i // 2) * (ch + pad)
            rec = slots.get(letter, {})
            active = cfs.get("active_slot") == letter
            self.panel(d, (cx, cy, cx + cw, cy + ch),
                       self.c["accent"] if active else None, active)
            if idle:
                self.buttons.append((cx, cy, cx + cw, cy + ch,
                                     ("edit_slot", letter, rec)))
            sw = hex_rgb(rec.get("color"), self.c["panel_edge"])
            r = (ch - 40) // 2
            sx, sy = cx + 18 + r, cy + ch // 2
            if rec.get("loaded"):
                d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=sw,
                          outline=self.c["panel_edge"], width=2)
                d.ellipse((sx - r * .38, sy - r * .38, sx + r * .38,
                           sy + r * .38), fill=self.c["panel"],
                          outline=mix(sw, (0, 0, 0), 0.3), width=2)
            else:
                d.ellipse((sx - r, sy - r, sx + r, sy + r),
                          outline=self.c["panel_edge"], width=3)
            self.text(d, (sx, sy), letter, "s", "text", anchor="mm")
            tx = sx + r + 16
            if rec.get("loaded"):
                tw = cx + cw - tx - 12
                names = self.lines(rec.get("name", "?"), "s", tw, 2)
                for k, line in enumerate(names):
                    self.text(d, (tx, cy + 18 + k * 28), line, "s")
                down = 28 * (len(names) - 1)
                self.text(d, (tx, cy + 56 + down), "%s · %s°C" % (
                    rec.get("type", ""), rec.get("nozzle_temp", "?")), "xs",
                    "muted", width=tw)
                extras = []
                if rec.get("pressure_advance"):
                    extras.append("PA %.3f" % rec["pressure_advance"])
                if rec.get("retract_length"):
                    extras.append("ret %.1f" % rec["retract_length"])
                self.text(d, (tx, cy + 86 + down), "  ".join(extras), "xs",
                          "accent", width=tw)
                if active:
                    self.text(d, (cx + cw - 14, cy + ch - 14), "IN USE", "xxs",
                              "accent", anchor="rs")
            else:
                self.text(d, (tx, cy + ch // 2), "Empty", "m", "muted",
                          anchor="lm")

    def page_controls(self, d, body, st):
        x0, y0, x1, y1 = body
        pad = 14
        ps = st.get("print_stats", {}).get("state", "")
        busy = ps in ("printing", "paused") or self.busy(st)
        light = st.get("output_pin chamber_led", {}).get("value", 0)
        grid = [
            ("Resume" if ps == "paused" else "Pause",
             ("gcode", "RESUME" if ps == "paused" else "PAUSE"), "warn",
             busy, "play" if ps == "paused" else "pause"),
            ("Cancel", ("confirm", "Cancel the print?", "CANCEL_PRINT"),
             "error", busy, "stop"),
            ("Home", ("gcode", "G28"), "accent", not busy, "home"),
            ("Deep clean", ("confirm", "Heat and deep-clean the nozzle?",
                            "NOZZLE_DEEP_CLEAN"), "accent", not busy,
             "clean"),
            ("Light off" if light else "Light on",
             # switched on by hand: the idle switch-off countdown
             # (_LIGHT_IDLE off_after) starts again
             ("gcode", "SET_PIN PIN=chamber_led VALUE=0" if light
              else "SET_PIN PIN=chamber_led VALUE=1\n_LIGHT_IDLE"),
             "accent", True, "light"),
            ("Motors off", ("gcode", "M84"), "accent", not busy, "motor"),
            ("Heaters off", ("gcode", "TURN_OFF_HEATERS"), "warn",
             ps not in ("printing", "paused"), "heat_off"),
            ("Restart", ("confirm", "Restart Klipper?", "@restart"),
             "error", ps not in ("printing", "paused"), "restart"),
        ]
        if ps in ("printing", "paused"):
            # While printing, the second row is the Z offset (babystep)
            return self.draw_print_controls(d, body, st, grid[:2], light)
        cols, rows = 4, 2
        if self.busy(st):
            y1 -= 62
        bw = (x1 - x0 - (cols + 1) * pad) // cols
        bh = (y1 - y0 - (rows + 1) * pad) // rows
        for i, (label, action, color, enabled, icon) in enumerate(grid):
            bx = x0 + pad + (i % cols) * (bw + pad)
            by = y0 + pad + (i // cols) * (bh + pad)
            self.button(d, (bx, by, bx + bw, by + bh), label, action, color,
                        enabled, icon=icon)

    def draw_print_controls(self, d, body, st, grid, light):
        x0, y0, x1, y1 = body
        pad = 14
        sp = st.get("gcode_macro START_PRINT") or {}
        saved = float(sp.get("z_offset") or 0.)
        offset = float((st.get("gcode_move") or {}).get(
            "homing_origin", [0, 0, 0])[2])
        cols = 4
        bw = (x1 - x0 - (cols + 1) * pad) // cols
        bh = 150
        top = list(grid) + [
            ("Light off" if light else "Light on",
             ("gcode", "SET_PIN PIN=chamber_led VALUE=%d" % (0 if light
                                                             else 1)),
             "accent", True, "light"),
            ("Save Z %+.3f" % (saved + self.z_adjust), ("z_save",),
             "ok", abs(self.z_adjust) > 1e-6, None)]
        for i, (label, action, color, enabled, icon) in enumerate(top):
            bx = x0 + pad + i * (bw + pad)
            self.button(d, (bx, y0 + pad, bx + bw, y0 + pad + bh), label,
                        action, color, enabled, icon=icon)
        # Z offset strip
        zy = y0 + 2 * pad + bh
        zbox = (x0 + pad, zy, x1 - pad, y1 - pad)
        self.panel(d, zbox)
        self.text(d, (zbox[0] + 18, zy + 26),
                  "Z offset now %+.3f mm" % offset, "s", anchor="lm")
        note = ("this print %+.3f · saved %+.3f" % (self.z_adjust, saved)
                if self.z_adjust else "saved %+.3f, used by every print"
                % saved)
        self.text(d, (zbox[2] - 18, zy + 26), note, "xs", "muted",
                  anchor="rm", width=zbox[2] - zbox[0] - 300)
        self.text(d, (zbox[0] + 18, zy + 56),
                  "negative = nozzle closer to the bed", "xs", "muted",
                  anchor="lm")
        steps = (-0.05, -0.01, 0.01, 0.05)
        sw = (zbox[2] - zbox[0] - 2 * 18 - 3 * 12) // 4
        sy = zy + 80
        for i, step in enumerate(steps):
            bx = zbox[0] + 18 + i * (sw + 12)
            self.button(d, (bx, sy, bx + sw, zbox[3] - 14), "%+.2f" % step,
                        ("z_adjust", step), "accent", True, "m")

    def page_machine(self, d, body, st):
        x0, y0, x1, y1 = body
        pad = 14
        settings = self.settings
        sh = settings.get("input_shaper", {})
        ex = settings.get("extruder", {})
        pr = settings.get("printer", {})
        motors = st.get("k2_motor_bus", {}).get("motors_ready")
        cfs = st.get("k2_cfs", {}).get("online")
        homed = st.get("toolhead", {}).get("homed_axes") or ""
        rows = [
            ("Klipper", st.get("webhooks", {}).get("state", "?"),
             st.get("webhooks", {}).get("state") == "ready"),
            ("Motors", "ready" if motors else "NOT ready", motors),
            ("Homed", homed or "none", homed == "xyz"),
            ("CFS", "not fitted", None)
            if st.get("k2_cfs", {}).get("fitted") is False
            else ("CFS", "online" if cfs else "offline", cfs),
            ("Address", self.ip, None),
            ("Shaper X", "%s %.1f Hz" % (sh.get("shaper_type_x", "?"),
                                         sh.get("shaper_freq_x", 0)), None),
            ("Shaper Y", "%s %.1f Hz" % (sh.get("shaper_type_y", "?"),
                                         sh.get("shaper_freq_y", 0)), None),
            ("Nozzle PID", "%.2f/%.2f/%.1f" % (
                ex.get("pid_kp", 0), ex.get("pid_ki", 0),
                ex.get("pid_kd", 0)), None),
            ("Z limits", "%g mm/s · %g" % (
                pr.get("max_z_velocity", 0), pr.get("max_z_accel", 0)),
             None),
        ]
        upd = st.get("hark_update", {})
        update_ready = bool(upd.get("update_available")) and not upd.get("updating")
        if upd.get("updating"):
            rows.append(("Software", "updating...", None))
        elif update_ready:
            rows.append(("Update", "%s: tap" % upd.get("latest", "?"), False))
        elif upd.get("installed"):
            rows.append(("Software", upd.get("installed"), None))
        cw = (x1 - x0 - 3 * pad) // 2
        per_col = 5
        rh = (y1 - y0 - pad - per_col * 10) // per_col
        for i, (label, value, good) in enumerate(rows):
            cx = x0 + pad + (i // per_col) * (cw + pad)
            cy = y0 + pad + (i % per_col) * (rh + 10)
            self.panel(d, (cx, cy, cx + cw, cy + rh))
            tx = cx + 14
            if good is not None:
                col = self.c["ok"] if good else self.c["error"]
                d.ellipse((cx + 14, cy + rh // 2 - 5, cx + 24,
                           cy + rh // 2 + 5), fill=col)
                tx = cx + 34
            self.text(d, (tx, cy + rh // 2), label, "xs", "muted",
                      anchor="lm")
            self.text(d, (cx + cw - 14, cy + rh // 2), value, "xs",
                      anchor="rm")
            printing = st.get("print_stats", {}).get("state") in ("printing", "paused")
            if label == "Update" and update_ready and not printing:
                self.buttons.append((cx, cy, cx + cw, cy + rh, (
                    "confirm", "Update to %s? Settings are kept; the printer "
                    "restarts (about 2 min)." % upd.get("latest", "?"),
                    "OKL_UPDATE CONFIRM=1")))

    # --- CFS slot editor (idle only) -------------------------------------
    FILTERS = ("All", "PLA", "PETG", "ABS", "TPU", "Other")
    COLOURS = (
        ("Black", "000000"), ("White", "FFFFFF"), ("Grey", "808080"),
        ("Silver", "C0C0C0"), ("Red", "E53935"), ("Orange", "FB8C00"),
        ("Yellow", "FDD835"), ("Gold", "FFC107"), ("Green", "43A047"),
        ("Lime", "C0CA33"), ("Cyan", "26EEEE"), ("Blue", "1E88E5"),
        ("Navy", "1A237E"), ("Purple", "8E24AA"), ("Pink", "EC407A"),
        ("Brown", "6D4C41"), ("Beige", "D7CCC8"),
    )

    def filament_choices(self):
        f = self.editor["filter"]
        lib = self.filaments
        if f == "All":
            return lib
        if f == "Other":
            return [m for m in lib
                    if m.get("type") not in ("PLA", "PETG", "ABS", "TPU")]
        return [m for m in lib if m.get("type") == f]

    def draw_editor(self, d, body, st):
        x0, y0, x1, y1 = body
        pad = 14
        ed = self.editor
        slot = ed["slot"]
        if ed["step"] == "material":
            self.text(d, (x0 + pad, y0 + 8), "Slot %s: filament" % slot, "s")
            cw = (x1 - x0 - pad * 2 - 5 * 8) // 6
            for i, f in enumerate(self.FILTERS):
                bx = x0 + pad + i * (cw + 8)
                box = (bx, y0 + 44, bx + cw, y0 + 84)
                on = ed["filter"] == f
                d.rounded_rectangle(box, radius=14,
                                    fill=self.c["accent"] if on
                                    else self.c["panel"],
                                    outline=self.c["panel_edge"], width=2)
                self.text(d, ((box[0] + box[2]) // 2, y0 + 64), f, "xs",
                          self.c["background"] if on else "text",
                          anchor="mm")
                self.buttons.append((*box, ("ed_filter", f)))
            items = self.filament_choices()
            per = 8
            pages = max(1, (len(items) + per - 1) // per)
            ed["page"] = min(ed["page"], pages - 1)
            iw = (x1 - x0 - pad * 3) // 2
            ih = 54
            top = y0 + 96
            cur = ed.get("material") or {}
            for i, m in enumerate(items[ed["page"] * per:
                                        (ed["page"] + 1) * per]):
                bx = x0 + pad + (i % 2) * (iw + pad)
                by = top + (i // 2) * (ih + 8)
                box = (bx, by, bx + iw, by + ih)
                picked = (cur.get("id"), cur.get("name")) == (
                    m.get("id"), m.get("name"))
                self.panel(d, box, self.c["accent"] if picked else None,
                           picked)
                self.text(d, (bx + 14, by + ih // 2), m.get("name", "?"),
                          "xs", anchor="lm", width=iw - 80)
                self.text(d, (box[2] - 12, by + ih // 2),
                          m.get("type", ""), "xs", "muted", anchor="rm")
                self.buttons.append((*box, ("ed_pick", m)))
            by = y1 - 58
            bw = (x1 - x0 - pad * 6) // 5
            def bx(i):
                return x0 + pad + i * (bw + pad)
            self.button(d, (bx(0), by, bx(0) + bw, by + 48), "Back",
                        ("ed_page", -1), "muted", ed["page"] > 0, "s")
            self.button(d, (bx(1), by, bx(1) + bw, by + 48), "More",
                        ("ed_page", 1), "muted", ed["page"] < pages - 1, "s")
            self.button(d, (bx(2), by, bx(2) + bw, by + 48), "Empty",
                        ("confirm", "Mark slot %s empty?" % slot,
                         "CFS_SLOT_CLEAR SLOT=%s" % slot), "warn", True, "s")
            self.button(d, (bx(3), by, bx(3) + bw, by + 48), "Cancel",
                        ("ed_cancel",), "muted", True, "s")
            self.button(d, (bx(4), by, bx(4) + bw, by + 48), "Colour",
                        ("ed_step", "colour"), "accent",
                        bool(ed.get("material")), "s")
        else:
            m = ed["material"]
            self.text(d, (x0 + pad, y0 + 8),
                      "Slot %s: %s colour" % (slot, m.get("name", "")), "s",
                      width=x1 - x0 - 2 * pad)
            cols = 6
            sw = (x1 - x0 - pad * (cols + 1)) // cols
            sh = 70
            for i, (name, hexval) in enumerate(self.COLOURS):
                cx = x0 + pad + (i % cols) * (sw + pad)
                cy = y0 + 48 + (i // cols) * (sh + 12)
                rgb = hex_rgb(hexval)
                picked = ed.get("colour") == hexval
                if picked:
                    self.glow((cx, cy, cx + sw, cy + sh), self.c["accent"])
                d.rounded_rectangle((cx, cy, cx + sw, cy + sh), radius=14,
                                    fill=rgb,
                                    outline=self.c["accent"] if picked
                                    else self.c["panel_edge"],
                                    width=5 if picked else 2)
                self.text(d, (cx + sw // 2, cy + sh // 2), name, "xs",
                          (0, 0, 0) if sum(rgb) > 380 else (255, 255, 255),
                          anchor="mm")
                self.buttons.append((cx, cy, cx + sw, cy + sh,
                                     ("ed_colour", hexval)))
            by = y1 - 58
            bw = (x1 - x0 - pad * 4) // 3
            self.button(d, (x0 + pad, by, x0 + pad + bw, by + 48), "Back",
                        ("ed_step", "material"), "muted", True, "s")
            self.button(d, (x0 + 2 * pad + bw, by, x0 + 2 * pad + 2 * bw,
                            by + 48), "Cancel", ("ed_cancel",), "muted",
                        True, "s")
            self.button(d, (x0 + 3 * pad + 2 * bw, by, x1 - pad, by + 48),
                        "Save", ("ed_save",), "ok", bool(ed.get("colour")),
                        "s")

    # --- Wi-Fi ---------------------------------------------------------
    KB_LETTERS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")
    KB_SYMBOLS = ("1234567890", "!@#$%^&*()", "-_=+[]{};:", "'\\|,.<>/?~")

    # --- Settings page ----------------------------------------------------
    def zone(self):
        """A time zone picked on the Settings page wins over harkscreen.cfg."""
        return self.prefs.get("timezone") or self.cfg.get("screen",
                                                          "timezone")

    def current_prefs(self, st):
        """What is in force: the saved choice, else what Klipper reports."""
        light = self.prefs.get("light_off")
        if light is None:
            light = (st.get("gcode_macro _LIGHT_IDLE") or {}).get("off_after")
        idle = self.prefs.get("idle")
        if idle is None:
            idle = (st.get("idle_timeout") or {}).get("idle_timeout")
        return {"brightness": self.prefs.get("brightness", 100),
                "sleep_after": int(self.prefs.get(
                    "sleep_after", self.cfg.num("screen", "sleep_after"))),
                "light_off": int(light or 0), "idle": int(idle or 0),
                "accent": self.prefs.get("accent", "")}

    def page_settings(self, d, body, st):
        if self.zone_pick:
            return self.draw_zone_pick(d, body)
        x0, y0, x1, y1 = body
        pad = 14
        cur = self.current_prefs(st)
        if self.zone_name is None:
            self.zone_name = system_zone()
        label_w = 150
        rows = [
            ("Brightness", [("%d%%" % b, ("pref", "brightness", b),
                             cur["brightness"] == b) for b in BRIGHTNESS]),
            ("Screen off", [(n, ("pref", "sleep_after", v),
                             cur["sleep_after"] == v)
                            for v, n in SLEEP_AFTER]),
            ("Light off", [(n, ("pref", "light_off", v),
                            cur["light_off"] == v) for v, n in LIGHT_OFF]),
            ("Idle off", [(n, ("pref", "idle", v),
                           cur["idle"] == v) for v, n in IDLE_OFF]),
        ]
        rh, gap = 42, 7
        y = y0 + 8
        for label, opts in rows:
            self.text(d, (x0 + pad, y + rh // 2), label, "s", "muted",
                      anchor="lm")
            bx = x0 + pad + label_w
            bw = (x1 - pad - bx - (len(opts) - 1) * 10) // len(opts)
            for i, (text, action, on) in enumerate(opts):
                box = (bx + i * (bw + 10), y, bx + i * (bw + 10) + bw, y + rh)
                self.button(d, box, text, action,
                            "accent" if on else "muted", True, "s")
            y += rh + gap
        # Time zone: the printer's own, with a picker
        self.text(d, (x0 + pad, y + rh // 2), "Time zone", "s", "muted",
                  anchor="lm")
        bx = x0 + pad + label_w
        names = dict((z, n) for n, z in ZONES)
        zone = names.get(self.zone_name, self.zone_name)
        self.panel(d, (bx, y, x1 - pad - 170, y + rh))
        self.text(d, (bx + 16, y + rh // 2),
                  "%s · %s" % (zone, time.strftime("%H:%M", time.localtime())),
                  "s", anchor="lm", width=x1 - pad - 170 - bx - 32)
        self.button(d, (x1 - pad - 160, y, x1 - pad, y + rh), "Change",
                    ("zone_pick",), "accent", True, "s")
        y += rh + gap
        self.draw_storage_row(d, body, y, rh, st)
        y += rh + gap
        if self.timelapse is not None:
            on = bool(self.timelapse.get("enabled"))
            self.text(d, (x0 + pad, y + rh // 2), "Time-lapse", "s",
                      "muted", anchor="lm")
            bx = x0 + pad + label_w
            bw = (x1 - pad - bx - 10) // 2
            for i, (text, value) in enumerate((("Off", False),
                                               ("On (one frame a layer)",
                                                True))):
                box = (bx + i * (bw + 10), y, bx + i * (bw + 10) + bw,
                       y + rh)
                self.button(d, box, text, ("timelapse", value),
                            "accent" if on == value else "muted", True, "s")
            y += rh + gap
        # Highlight colour
        self.text(d, (x0 + pad, y + rh // 2), "Highlight", "s", "muted",
                  anchor="lm")
        n = len(ACCENTS)
        bw = (x1 - pad - bx - (n - 1) * 10) // n
        theme = hex_rgb(self.cfg.get("theme", "accent"))
        for i, (hexval, name) in enumerate(ACCENTS):
            box = (bx + i * (bw + 10), y, bx + i * (bw + 10) + bw, y + rh)
            col = hex_rgb(hexval, theme) if hexval else theme
            on = cur["accent"] == hexval
            d.rounded_rectangle(box, radius=14, fill=col,
                                outline=self.c["text"] if on else
                                self.c["panel_edge"], width=4 if on else 2)
            self.text(d, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2),
                      name, "xs", (255, 255, 255), anchor="mm",
                      width=bw - 8)
            self.buttons.append((*box, ("pref", "accent", hexval)))

    # --- temperature and fan boxes ---------------------------------------
    def temp_limit(self, heater):
        """Highest target the box offers: the config's max_temp less a
        margin (nozzle at most 300C); the chamber heater's own 60C."""
        if heater == "chamber":
            return 60
        cfgmax = float((self.settings.get(heater) or {}).get(
            "max_temp", 300 if heater == "extruder" else 110))
        if heater == "extruder":
            return int(min(300, cfgmax - 10))
        return int(cfgmax - 5)

    def heater_now(self, heater, st):
        if heater == "chamber":
            return ((st.get("temperature_sensor chamber_temperature") or {})
                    .get("temperature", 0),
                    (st.get("hark_chamber") or {}).get("requested", 0))
        h = st.get(heater) or {}
        return h.get("temperature", 0), h.get("target", 0)

    def set_temp(self, heater, value, mr):
        value = max(0, min(int(value), self.temp_limit(heater)))
        self.temp_target = value
        cmd = {"extruder": "M104 S%d", "heater_bed": "M140 S%d",
               "chamber": "CHAMBER_HEAT TARGET=%d"}[heater] % value
        mr.gcode(cmd)

    def modal_dim(self):
        dim = Image.new("RGB", (W, H), (0, 0, 0))
        self.canvas = Image.blend(self.canvas, dim, 0.65)
        return ImageDraw.Draw(self.canvas)

    def draw_temp_box(self, heater, st):
        d = self.modal_dim()
        box = (110, 40, W - 110, 440)
        self.panel(d, box, self.c["accent"], True)
        cur, tgt = self.heater_now(heater, st)
        self.text(d, (box[0] + 30, 80), TEMP_NAMES[heater], "m",
                  anchor="lm")
        self.text(d, (box[2] - 30, 80), "now %d°" % round(cur or 0), "s",
                  "muted", anchor="rm")
        self.buttons = []
        # target with steps either side
        cy = 160
        self.text(d, (W // 2, cy), ("%d°" % self.temp_target)
                  if self.temp_target else "Off", "xl",
                  "warn" if self.temp_target else "muted", anchor="mm")
        for i, step in enumerate((-10, -5, 5, 10)):
            bx = box[0] + 30 + i * 70 if step < 0 else \
                box[2] - 30 - (3 - i) * 70 - 60
            self.button(d, (bx, cy - 30, bx + 60, cy + 30), "%+d" % step,
                        ("temp_to", heater, self.temp_target + step),
                        "accent", True, "s")
        presets = TEMP_PRESETS[heater]
        n = len(presets)
        pw = (box[2] - box[0] - 60 - (n - 1) * 10) // n
        py = 230
        for i, (name, value) in enumerate(presets):
            bx = box[0] + 30 + i * (pw + 10)
            label = name if not value or heater == "chamber" else \
                "%s %d°" % (name, value)
            self.button(d, (bx, py, bx + pw, py + 64), label,
                        ("temp_to", heater, value),
                        "accent" if value == self.temp_target else "muted",
                        value <= self.temp_limit(heater), "xs")
        note = {"extruder": "max %d°" % self.temp_limit(heater),
                "heater_bed": "max %d°" % self.temp_limit(heater),
                "chamber": "heats only above 40°, max 60°"}[heater]
        self.text(d, (W // 2, 326), note, "xs", "muted", anchor="mm")
        self.button(d, (W // 2 - 110, 352, W // 2 + 110, 416), "Done",
                    ("box_close",), "accent", True, "m")

    def draw_fans_box(self, st):
        d = self.modal_dim()
        box = (90, 40, W - 90, 440)
        self.panel(d, box, self.c["accent"], True)
        self.text(d, (box[0] + 30, 80), "Fans", "m", anchor="lm")
        self.buttons = []
        auto = not (st.get("gcode_macro _CHAMBER_FAN") or {}).get("manual")
        health = st.get("hark_fans") or {}
        y = 112
        for label, index, obj in FANS:
            fan = st.get(obj) or {}
            speed = round(float(fan.get("speed", 0)) * 100)
            self.text(d, (box[0] + 30, y + 26), label, "s", "muted",
                      anchor="lm")
            if index == 0:
                # Tachometer: true at full speed only (the fan is powered
                # through its PWM), so the rpm shows then, else the health
                # record's usual
                text, color = self.fan_rpm_text(fan, health.get("part"))
                self.text(d, (box[0] + 30, y + 46), text, "xs", color,
                          anchor="lm", width=150)
            steps = list(FAN_STEPS)
            opts = [("%d%%" % v if v else "Off", ("fan_to", index, v),
                     abs(speed - v) < 13 and not (index == 1 and auto))
                    for v in steps]
            if index == 1:
                # the exhaust fan's automatic chamber control
                opts = [("Auto", ("fan_auto",), auto)] + [
                    o for o in opts if o[1][2] in (0, 25, 50, 100)]
            bx = box[0] + 190
            n = len(opts)
            bw = (box[2] - 30 - bx - (n - 1) * 8) // n
            for i, (text, action, on) in enumerate(opts):
                x = bx + i * (bw + 8)
                self.button(d, (x, y, x + bw, y + 52), text, action,
                            "accent" if on else "muted", True, "xs")
            y += 64
        # The hotend fan runs itself (on above 50C, slowed while probing):
        # its rpm and health only
        hot = st.get("heater_fan hotend_fan") or {}
        self.text(d, (box[0] + 30, y + 10), "Hotend fan", "s", "muted",
                  anchor="lm")
        hot_speed = float(hot.get("speed", 0) or 0)
        state = ("off" if hot_speed <= 0 else
                 "slowed for probing" if hot_speed < 0.99 else "running")
        text, color = self.fan_rpm_text(hot, health.get("hotend"))
        self.text(d, (box[0] + 190, y + 10), "%s · %s" % (state, text),
                  "xs", color, anchor="lm")
        y += 36
        self.text(d, (W // 2, y + 10), "The slicer may change them again"
                  " at its next fan command", "xs", "muted", anchor="mm")
        self.button(d, (W // 2 - 110, 352, W // 2 + 110, 416), "Done",
                    ("box_close",), "accent", True, "m")

    @staticmethod
    def fan_rpm_text(fan, record):
        """(text, colour): '7496 rpm' at full speed, else the usual from
        the health record; a slowed or stopped verdict from the last
        FAN_CHECK colours it."""
        rpm = fan.get("rpm")
        speed = float(fan.get("speed", 0) or 0)
        record = record or {}
        last = record.get("last") or {}
        usual = record.get("usual")
        if rpm is not None and speed >= 0.99:
            text = "%d rpm" % round(rpm)
        elif usual:
            text = "usually %d rpm" % round(usual)
        else:
            text = "no tachometer" if rpm is None else "rpm at full speed"
        if last.get("verdict") == "slow":
            return text + ", slow", "warn"
        if last.get("verdict") == "stopped":
            return text + ", not turning", "error"
        return text, "muted"

    def draw_storage_row(self, d, body, y, rh, st):
        x0, y0, x1, y1 = body
        pad = 14
        # G-code files, time-lapse videos and logs
        self.text(d, (x0 + pad, y + rh // 2), "Storage", "s", "muted",
                  anchor="lm")
        bx = x0 + pad + 150
        if remote_moonraker(self.cfg.get("screen", "moonraker")):
            self.panel(d, (bx, y, x1 - pad, y + rh))
            self.text(d, (bx + 16, y + rh // 2),
                      "Stored on the PC that runs Klipper", "s", "muted",
                      anchor="lm")
            return
        state = storage_state()
        busy = st.get("print_stats", {}).get("state") in ("printing",
                                                          "paused")
        want = self.prefs.get("gcode_storage", "internal")
        usb = self.usb
        ejected = self.prefs.get("usb_ejected")
        # small buttons: Eject (a mounted drive) and Format (any drive)
        small = []
        if usb and usb["mounted"] and not ejected:
            small.append(("Eject", ("usb_eject",), "accent", not busy))
        if usb and not ejected:
            small.append(("Format", ("usb_format_ask",), "warn",
                          not busy and not usb_in_use()))
        fw = 84
        sw = len(small) * (fw + 8)
        bw = (x1 - pad - bx - 10 - sw) // 2
        gb = lambda v: ("%.1f GB free" % v) if v is not None else ""
        opts = (("internal", "Printer · %s" % gb(state["internal_free"]),
                 True),
                ("usb", "USB drive (ejected)" if usb and ejected else
                 ("USB · %s" % gb(state["usb_free"]))
                 if state["usb"] else "USB drive (not inserted)",
                 state["usb"] or want == "usb"))
        for i, (where, label, ok) in enumerate(opts):
            box = (bx + i * (bw + 10), y, bx + i * (bw + 10) + bw, y + rh)
            on = want == where
            self.button(d, box, label, ("pref", "gcode_storage", where),
                        "accent" if on else "muted", ok and not busy, "xs")
        for i, (text, action, colour, enabled) in enumerate(small):
            fx = x1 - pad - (len(small) - i) * (fw + 8) + 8
            self.button(d, (fx, y, fx + fw, y + rh), text, action, colour,
                        enabled, "xs")

    def draw_zone_pick(self, d, body):
        x0, y0, x1, y1 = body
        pad = 14
        if self.zone_name is None:
            self.zone_name = system_zone()
        self.text(d, (x0 + pad, y0 + 10), "Time zone (now %s)"
                  % (self.zone_name or "?"), "s",
                  width=x1 - x0 - 2 * pad)
        zones = [(n, z) for n, z in ZONES
                 if not os.path.isdir(ZONEINFO)
                 or os.path.exists(os.path.join(ZONEINFO, z))]
        cols, rh, gap = 3, 50, 8
        bw = (x1 - x0 - 2 * pad - (cols - 1) * gap) // cols
        top = y0 + 48
        for i, (name, zone) in enumerate(zones[:17]):
            bx = x0 + pad + (i % cols) * (bw + gap)
            by = top + (i // cols) * (rh + gap)
            on = zone == self.zone_name
            self.button(d, (bx, by, bx + bw, by + rh), name,
                        ("zone_set", zone), "accent" if on else "muted",
                        True, "xs")
        i = 17
        bx = x0 + pad + (i % cols) * (bw + gap)
        by = top + (i // cols) * (rh + gap)
        self.button(d, (bx, by, bx + bw, by + rh), "Back", ("zone_back",),
                    "muted", True, "xs")

    def remember_usb(self, usb_id):
        asked = list(self.prefs.get("usb_asked") or [])
        if usb_id and usb_id not in asked:
            self.prefs["usb_asked"] = (asked + [usb_id])[-10:]
            try:
                save_prefs(self.prefs)
            except Exception:
                pass

    def start_usb_format(self):
        st = self.last_st
        if st.get("print_stats", {}).get("state") in ("printing", "paused"):
            self.usb_job = {"kind": "usb_done", "title": "Not formatted",
                            "lines": ["The printer is printing."],
                            "color": "warn"}
            return
        self.usb_job = {"kind": "usb_busy", "title": "Formatting",
                        "lines": ["Formatting the USB drive. This takes a few"
                                  " seconds; don't unplug it."],
                        "color": "accent"}

        def run():
            try:
                format_usb()
                job = {"kind": "usb_done", "title": "USB drive ready",
                       "lines": ["Formatted for the printer (FAT32, label %s)."
                                 % USB_LABEL,
                                 "Choose Storage: USB in Settings to keep"
                                 " files, time-lapses and logs on it."],
                       "color": "ok"}
            except Exception as e:
                msg = str(e)
                if "in use" in msg:
                    msg = ("It holds the printer's files or logs. Set Storage"
                           " to Printer in Settings, restart the printer,"
                           " then format.")
                job = {"kind": "usb_done", "title": "Not formatted",
                       "lines": [msg], "color": "warn"}
                print("usb format: %s" % e, flush=True)
            self.usb_job = job
            self.dirty = True
        threading.Thread(target=run, daemon=True).start()

    def start_usb_eject(self, mr):
        st = self.last_st
        if st.get("print_stats", {}).get("state") in ("printing", "paused"):
            self.usb_job = {"kind": "usb_done", "title": "Not ejected",
                            "lines": ["The printer is printing. Eject the"
                                      " drive when the print is over."],
                            "color": "warn"}
            return
        self.prefs["usb_ejected"] = True
        self.prefs["usb_eject_pending"] = True
        try:
            save_prefs(self.prefs)
        except Exception:
            pass
        busy = usb_in_use()
        self.usb_job = {"kind": "usb_busy", "title": "Ejecting",
                        "lines": ["Moving the printer's files off the USB"
                                  " drive. Don't unplug it yet."],
                        "color": "accent"}
        if "logs" in busy:
            # Klipper's log is open on the drive: restart Klipper the way
            # the printer starts it (storage-links.sh keeps everything
            # internal while usb_ejected is set). That restarts this
            # screen too; the new one finishes the eject.
            cmd = mainline_start_cmd()
            if cmd:
                self.usb_job["lines"] = [
                    "Restarting Klipper to release the USB drive (about a"
                    " minute). Don't unplug it yet."]
                subprocess.Popen(["sh", cmd[0], cmd[1]],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return
        if busy:
            try:
                point_gcodes("internal")
            except OSError as e:
                print("eject: %s" % e, flush=True)
            mr.post("/server/restart")
        threading.Thread(target=self.finish_usb_eject, daemon=True).start()

    def finish_usb_eject(self):
        # Moonraker (and Klipper) must have let go of the drive
        deadline = time.time() + 90
        while usb_in_use() and time.time() < deadline:
            time.sleep(2)
        time.sleep(5)
        ok = unmount_usb()
        self.prefs["usb_eject_pending"] = False
        try:
            save_prefs(self.prefs)
        except Exception:
            pass
        if ok:
            self.usb_job = {"kind": "usb_done", "title": "Safe to remove",
                            "lines": ["You can unplug the USB drive now.",
                                      "Files are kept on the printer until"
                                      " it is plugged in again."],
                            "color": "ok"}
        else:
            self.usb_job = {"kind": "usb_done", "title": "Not ejected",
                            "lines": ["The drive is still in use. Switch the"
                                      " printer off before unplugging it."],
                            "color": "warn"}
        self.dirty = True

    def set_pref(self, key, value, mr, st):
        self.prefs[key] = value
        if key == "gcode_storage" and value == "usb" \
                and os.path.ismount(USB_MOUNT) \
                and not self.prefs.get("usb_eject_pending"):
            # chosen again while still mounted: the eject is undone
            self.prefs["usb_ejected"] = False
        try:
            save_prefs(self.prefs)
        except Exception as e:
            print("settings: could not save: %s" % e, flush=True)
        if key == "brightness":
            set_brightness(value)
        elif key == "accent":
            self.apply_config()
        elif key in ("light_off", "idle", "z_offset"):
            idle = st.get("print_stats", {}).get("state") not in (
                "printing", "paused")
            mr.gcode(klipper_prefs({key: value}, idle))

    def page_wifi(self, d, body, st):
        x0, y0, x1, y1 = body
        pad = 14
        if self.kb:
            return self.draw_keyboard(d, body)
        ws = self.wifi_status
        self.panel(d, (x0 + pad, y0 + pad, x1 - pad, y0 + 96))
        self.icon(d, "wifi", x0 + pad + 34, y0 + 55, 34,
                  self.c["accent"] if ws.get("ssid") else self.c["muted"])
        self.text(d, (x0 + pad + 66, y0 + 26),
                  ws.get("ssid") or "Not connected", "m",
                  "text" if ws.get("ssid") else "warn",
                  width=(x1 - pad - 160) - (x0 + pad + 66))
        self.text(d, (x0 + pad + 66, y0 + 64), "%s   %s" % (
            ws.get("ip_address", ""),
            ("%s dBm" % ws["rssi"]) if ws.get("rssi") else ""), "xs",
                  "muted")
        self.button(d, (x1 - pad - 150, y0 + 26, x1 - 2 * pad, y0 + 84),
                    "Scan", ("wifi_scan",), "accent", not self.wifi.busy)
        self.text(d, (x0 + pad, y0 + 108), self.wifi.message, "xs", "muted")
        if self.wifi.busy:
            cx = x1 - pad - 190
            self.anims.append({"kind": "spin", "center": (cx, y0 + 55),
                               "r": 12, "rect": (cx - 18, y0 + 37, cx + 18,
                                                 y0 + 73)})
        nets = self.wifi.networks
        per = 6
        pages = max(1, (len(nets) + per - 1) // per)
        self.wifi_page = min(self.wifi_page, pages - 1)
        iw = (x1 - x0 - 3 * pad) // 2
        ih = 62
        top = y0 + 140
        for i, (ssid, dbm, secured) in enumerate(
                nets[self.wifi_page * per:(self.wifi_page + 1) * per]):
            bx = x0 + pad + (i % 2) * (iw + pad)
            by = top + (i // 2) * (ih + 10)
            current = ssid == ws.get("ssid")
            self.panel(d, (bx, by, bx + iw, by + ih),
                       self.c["accent"] if current else None, current)
            self.text(d, (bx + 14, by + 18), ssid, "xs", width=iw - 28)
            quality = ("strong" if dbm > -55 else "good" if dbm > -65
                       else "fair" if dbm > -75 else "weak")
            self.text(d, (bx + 14, by + 42), "%s · %d dBm · %s" % (
                quality, dbm, "secured" if secured else "open"), "xs",
                "muted")
            if not self.wifi.busy:
                self.buttons.append((bx, by, bx + iw, by + ih,
                                     ("wifi_pick", ssid, secured)))
        if pages > 1:
            self.button(d, (x0 + pad, y1 - 52, x0 + 150, y1 - 8), "Back",
                        ("wifi_page", -1), "muted", self.wifi_page > 0, "s")
            self.button(d, (x1 - 150, y1 - 52, x1 - pad, y1 - 8), "More",
                        ("wifi_page", 1), "muted",
                        self.wifi_page < pages - 1, "s")

    def draw_keyboard(self, d, body):
        x0, y0, x1, y1 = body
        pad = 10
        kb = self.kb
        self.text(d, (x0 + pad, y0 + 6), "Password for %s" % kb["ssid"],
                  "xs", "muted", width=x1 - x0 - 2 * pad)
        shown = kb["text"] if kb["show"] else "•" * len(kb["text"])
        self.panel(d, (x0 + pad, y0 + 32, x1 - pad, y0 + 76))
        self.text(d, (x0 + 2 * pad, y0 + 54), shown[-36:] + "|", "s",
                  anchor="lm")
        rows = self.KB_SYMBOLS if kb["symbols"] else self.KB_LETTERS
        kw, kh, gap = 60, 54, 5
        ky = y0 + 86
        for r, row in enumerate(rows):
            rx = x0 + pad + (10 - len(row)) * (kw + gap) // 2
            for i, ch in enumerate(row):
                if kb["shift"] and not kb["symbols"]:
                    ch = ch.upper()
                box = (rx + i * (kw + gap), ky, rx + i * (kw + gap) + kw,
                       ky + kh)
                d.rounded_rectangle(box, radius=10, fill=self.c["panel"],
                                    outline=self.c["panel_edge"], width=2)
                self.text(d, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2),
                          ch, "s", anchor="mm")
                self.buttons.append((*box, ("kb_key", ch)))
            ky += kh + gap
        fy = ky + 2
        specials = [("Shift", ("kb_shift",), 84), ("123" if not kb["symbols"]
                    else "abc", ("kb_symbols",), 72),
                    ("Space", ("kb_key", " "), 116), ("Del", ("kb_back",), 64),
                    ("Show" if not kb["show"] else "Hide", ("kb_show",), 76),
                    ("Cancel", ("kb_cancel",), 96),
                    ("Join", ("kb_join",), 96)]
        fx = x0 + pad
        for label, action, w in specials:
            color = "ok" if label == "Join" else "accent" if (
                label == "Shift" and kb["shift"]) else "muted"
            enabled = label != "Join" or len(kb["text"]) >= 8
            self.button(d, (fx, fy, fx + w, fy + kh), label, action, color,
                        enabled, "s")
            fx += w + gap

    def draw_nav(self, d):
        d.rectangle((0, HEADER_H, NAV_W - 2, H),
                    fill=mix(self.c["background"], self.c["panel"], 0.6))
        d.line((NAV_W - 1, HEADER_H, NAV_W - 1, H),
               fill=self.c["panel_edge"], width=2)
        names = {"status": "Status",
                 "cfs": "Spool" if getattr(self, "no_cfs", False) else "CFS",
                 "controls": "Controls", "machine": "Machine",
                 "wifi": "Wi-Fi", "settings": "Settings"}
        n = len(self.pages) or 1
        bh = (H - HEADER_H) // n
        for i, p in enumerate(self.pages):
            box = (0, HEADER_H + i * bh, NAV_W, HEADER_H + (i + 1) * bh)
            on = p == self.page and not self.editor
            col = self.c["accent"] if on else self.c["muted"]
            cy = (box[1] + box[3]) // 2
            if on:
                self.glow((10, box[1] + 8, NAV_W - 10, box[3] - 8),
                          self.c["accent"], 14)
                d.rounded_rectangle((10, box[1] + 8, NAV_W - 10, box[3] - 8),
                                    radius=14,
                                    fill=mix(self.c["panel"], col, 0.12),
                                    outline=col, width=2)
            self.icon(d, p, NAV_W // 2, cy - 12, 26, col)
            self.text(d, (NAV_W // 2, cy + 18), names[p], "xxs", col,
                      anchor="mm")
            self.buttons.append((*box, ("page", p)))

    def draw_confirm(self, d):
        dim = Image.new("RGB", (W, H), (0, 0, 0))
        self.canvas = Image.blend(self.canvas, dim, 0.65)
        d = ImageDraw.Draw(self.canvas)
        box = (150, 110, W - 150, 370)
        self.panel(d, box, self.c["warn"], True)
        # Wrap the question to the box; long ones drop a font size.
        width = box[2] - box[0] - 48
        for font in ("m", "s", "xs"):
            lines = self.wrap(self.confirm[0], font, width)
            if len(lines) <= 3:
                break
        step = {"m": 36, "s": 30, "xs": 26}[font]
        top = 190 - step * (len(lines) - 1) // 2
        for i, line in enumerate(lines):
            self.text(d, (W // 2, top + i * step), line, font, anchor="mm")
        self.buttons = []
        self.button(d, (190, 260, 380, 340), "No", ("cancel_confirm",),
                    "muted")
        self.button(d, (420, 260, W - 190, 340), "Yes", ("do_confirm",),
                    "error")

    # --- overlay animations ------------------------------------------------
    def draw_anim(self, a, t):
        """Draw one animation over a crop of the base; returns the crop."""
        import math
        rect = a["rect"]
        region = self.base.crop(rect)
        d = ImageDraw.Draw(region)
        ox, oy = rect[0], rect[1]
        kind = a["kind"]
        if kind == "pulse":
            s = (t * 1.2) % 1.0
            cx, cy = a["center"][0] - ox, a["center"][1] - oy
            r = (18 + 14 * s) if a.get("big") else 6 + 11 * s
            ring = region.copy()
            ImageDraw.Draw(ring).ellipse((cx - r, cy - r, cx + r, cy + r),
                                         outline=a["color"], width=3)
            region = Image.blend(region, ring, 1 - s)
        elif kind == "sheen":
            w, h = region.size
            band = 70
            pos = int(((t * 160) % (w + band)) - band)
            light = region.copy()
            ImageDraw.Draw(light).polygon(
                [(pos, 0), (pos + band * .6, 0), (pos + band, h),
                 (pos + band * .4, h)],
                fill=mix(self.c["accent"], self.c["white"], 0.75))
            mask = Image.new("L", region.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1),
                                                   radius=15, fill=110)
            region.paste(light, (0, 0), mask)
        elif kind == "heat":
            s = (math.sin(t * 3.2) + 1) / 2
            cx, cy, r = a["center"][0] - ox, a["center"][1] - oy, a["r"]
            col = mix(a["color"], self.c["white"], 0.45 * s)
            frac = max(0.0, min(1.0, a["frac"]))
            if frac > 0.005:
                d.arc((cx - r, cy - r, cx + r, cy + r), 135,
                      135 + 270 * frac, fill=col, width=8)
        elif kind == "fan":
            cx, cy = a["center"][0] - ox, a["center"][1] - oy
            d.rectangle((0, 0) + region.size, fill=self.c["panel"])
            angle = (t * 360 * 2.5 * a["speed"]) % 360
            self.icon(d, "fan", cx, cy, a["size"],
                      self.c["accent"] if a["speed"] else self.c["muted"],
                      angle)
        elif kind == "scan":
            import math
            w, h = region.size
            glow = Image.new("RGB", region.size, mix(self.c["accent"],
                                                     self.c["white"], 0.5))
            # a bright band runs along the line; the whole line breathes
            band = Image.new("L", region.size, 0)
            pos = (t * 90) % (w + 60) - 30
            bd = ImageDraw.Draw(band)
            for x in range(w):
                v = 150 + 50 * math.sin(t * 4)
                dist = abs(x - pos)
                if dist < 30:
                    v += 145 * (1 - dist / 30.)
                bd.line((x, 0, x, h), fill=int(min(255, v)))
            m = ImageChops.multiply(band, a["mask"])
            mid = Image.new("L", region.size, 0)
            ImageDraw.Draw(mid).rectangle((0, h // 2 - 1, w, h // 2 + 1),
                                          fill=255)
            m = ImageChops.multiply(m, mid.filter(ImageFilter.GaussianBlur(1.5)))
            region.paste(glow, (0, 0), m)
        elif kind == "spin":
            cx, cy, r = a["center"][0] - ox, a["center"][1] - oy, a["r"]
            start = (t * 360) % 360
            d.arc((cx - r, cy - r, cx + r, cy + r), start, start + 270,
                  fill=self.c["accent"], width=3)
        elif kind == "flash":
            s = min(1.0, (t - a["start"]) / 0.25)
            region = Image.blend(region, Image.new("RGB", region.size,
                                                   self.c["accent"]),
                                 0.45 * (1 - s))
        return region

    def animate(self, now):
        if self.flash:
            rect, start = self.flash
            if now - start > 0.25:
                self.flash = None
                self.blit(self.base.crop(rect), rect)
            else:
                self.blit(self.draw_anim({"kind": "flash", "rect": rect,
                                          "start": start}, now), rect)
        for a in self.anims:
            self.blit(self.draw_anim(a, now), a["rect"])

    # --- output ----------------------------------------------------------
    def to_fb(self, img):
        rot = int(self.cfg.num("screen", "rotate")) % 360
        if rot == 90:
            return img.transpose(Image.ROTATE_270)   # 90 deg clockwise
        if rot == 270:
            return img.transpose(Image.ROTATE_90)
        if rot == 180:
            return img.transpose(Image.ROTATE_180)
        return img

    def show(self, img):
        raw = self.to_fb(img).convert("RGBA").tobytes("raw", "BGRA")
        os.pwrite(self.fb, raw, 0)

    def blit(self, region, rect):
        """Write one landscape rectangle straight into the framebuffer."""
        rot = int(self.cfg.num("screen", "rotate")) % 360
        x0, y0, x1, y1 = rect
        patch = self.to_fb(region).convert("RGBA")
        if rot == 90:
            fx, fy = FB_W - y1, x0
        elif rot == 270:
            fx, fy = y0, FB_H - x1
        elif rot == 180:
            fx, fy = FB_W - x1, FB_H - y1
        else:
            fx, fy = x0, y0
        pw, ph = patch.size
        fx, fy = max(0, fx), max(0, fy)
        raw = patch.tobytes("raw", "BGRA")
        row = pw * 4
        for r in range(min(ph, FB_H - fy)):
            os.pwrite(self.fb, raw[r * row:(r + 1) * row],
                      ((fy + r) * FB_W + fx) * 4)

    def fade(self, old, new, frames=5):
        """Ease a box in over (or out of) the page."""
        for i in range(1, frames):
            self.show(Image.blend(old, new, ease_out(i / float(frames))))
        self.show(new)

    def slide(self, old, new, direction):
        """Slide the page body vertically from old to new frame."""
        bx0, by0 = NAV_W, HEADER_H
        bh = H - HEADER_H
        old_body = old.crop((bx0, by0, W, H))
        new_body = new.crop((bx0, by0, W, H))
        frames = 7
        for i in range(1, frames + 1):
            p = ease_out(i / float(frames))
            off = int(bh * p)
            frame = new.copy()
            body = Image.new("RGB", new_body.size)
            body.paste(old_body, (0, -direction * off))
            body.paste(new_body, (0, direction * (bh - off)))
            frame.paste(body, (bx0, by0))
            self.show(frame)

    def set_sleep(self, asleep):
        if asleep != self.asleep:
            subprocess.call(["/usr/bin/screen.sh", "off" if asleep else "on"])
            if not asleep and "brightness" in self.prefs:
                # the panel comes back at the driver's default
                set_brightness(self.prefs["brightness"])
            self.asleep = asleep
            self.dirty = True
            self.shown = None

    # --- input -----------------------------------------------------------
    def map_touch(self, x, y):
        """Raw panel coordinates (portrait pixels) to the drawn layout."""
        c = self.cfg
        if c.flag("screen", "touch_swap_xy"):
            x, y = y, x
        x = x * FB_W / c.num("screen", "touch_max_x")
        y = y * FB_H / c.num("screen", "touch_max_y")
        if c.flag("screen", "touch_invert_x"):
            x = FB_W - x
        if c.flag("screen", "touch_invert_y"):
            y = FB_H - y
        rot = int(c.num("screen", "rotate")) % 360
        if rot == 90:
            return y, FB_W - x
        if rot == 270:
            return FB_H - y, x
        if rot == 180:
            return FB_W - x, FB_H - y
        return x, y

    def handle_tap(self, x, y, mr):
        x, y = self.map_touch(x, y)
        for x0, y0, x1, y1, action in self.buttons:
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            kind = action[0]
            if kind != "page":
                self.flash = ((x0, y0, x1, y1), time.time())
            label = {"G28": "Home", "M84": "Motors off", "PAUSE": "Pause",
                     "RESUME": "Resume", "NOZZLE_DEEP_CLEAN": "Deep clean",
                     "CANCEL_PRINT": "Cancel"}
            self.dirty = True
            if kind == "page":
                self.zone_pick = False
                if action[1] == "settings":
                    self.zone_name = None    # read again when shown
                if action[1] != self.page or self.editor:
                    self.transition = (self.pages.index(self.page)
                                       if self.page in self.pages else 0)
                self.page = action[1]
                self.editor = None
                self.kb = None
            elif kind == "gcode":
                if action[1] in label:
                    self.running = (label[action[1]], time.time())
                mr.gcode(action[1])
            elif kind == "confirm":
                self.confirm = (action[1], action[2])
            elif kind == "temp_pick":
                self.temp_pick = action[1]
                self.temp_target = int(round(
                    self.heater_now(action[1], self.last_st)[1] or 0))
            elif kind == "temp_to":
                self.set_temp(action[1], action[2], mr)
            elif kind == "fans_pick":
                self.fans_open = True
            elif kind == "fan_to":
                index, pct = action[1], action[2]
                p = (" P%d" % index) if index else ""
                mr.gcode(("M106%s S%d" % (p, round(pct * 2.55))) if pct
                         else ("M107%s" % p))
            elif kind == "fan_auto":
                mr.gcode("SET_GCODE_VARIABLE MACRO=_CHAMBER_FAN "
                         "VARIABLE=manual VALUE=0")
            elif kind == "box_close":
                self.temp_pick, self.fans_open = None, False
            elif kind == "usb_use":
                self.remember_usb(action[1])
                self.set_pref("gcode_storage", "usb", mr, self.last_st)
            elif kind == "usb_later":
                self.remember_usb(action[1])
            elif kind == "usb_format_ask":
                usb = self.usb or {}
                self.confirm = ("Erase everything on %s (%.0f GB) and format"
                                " it for the printer?" % (
                                    usb.get("model", "the USB drive"),
                                    usb.get("gb", 0)), "@usbformat")
            elif kind == "usb_eject":
                self.start_usb_eject(mr)
            elif kind == "usb_done_ok":
                self.usb_job = None
            elif kind == "timelapse":
                mr.post("/machine/timelapse/settings?enabled=%s"
                        % ("true" if action[1] else "false"))
                if self.timelapse is not None:
                    self.timelapse["enabled"] = action[1]
            elif kind == "z_adjust":
                self.z_adjust = round(self.z_adjust + action[1], 3)
                mr.gcode("SET_GCODE_OFFSET Z_ADJUST=%.3f MOVE=1" % action[1])
            elif kind == "z_save":
                sp = self.last_st.get("gcode_macro START_PRINT") or {}
                new = round(float(sp.get("z_offset") or 0.) + self.z_adjust,
                            3)
                self.z_adjust = 0.
                self.set_pref("z_offset", new, mr, self.last_st)
            elif kind == "pref":
                self.set_pref(action[1], action[2], mr, self.last_st)
            elif kind == "zone_pick":
                self.zone_pick = True
            elif kind == "zone_back":
                self.zone_pick = False
            elif kind == "zone_set":
                # off the screen's loop: it never waits for the system
                threading.Thread(target=set_system_zone, args=(action[1],),
                                 daemon=True).start()
                self.zone_name = action[1]
                self.prefs["timezone"] = "auto"
                try:
                    save_prefs(self.prefs)
                except Exception:
                    pass
                self.zone_pick = False
            elif kind == "notice_ok":
                self.error_ack, self.alert_ack = action[1]
            elif kind == "notice_later":
                self.update_snooze = {"version": action[1],
                                      "until": time.time() + UPDATE_SNOOZE}
                try:
                    with open(UPDATE_SNOOZE_FILE, "w") as f:
                        json.dump(self.update_snooze, f)
                except Exception:
                    pass
            elif kind == "notice_update":
                self.running = ("Updating", time.time())
                mr.gcode("OKL_UPDATE CONFIRM=1")
            elif kind == "pa_static_ok":
                self.pa_static_ack = action[1]
            elif kind == "pa_ok":
                self.ack_pa(action[1])
            elif kind == "edit_slot":
                self.slot_menu = {"slot": action[1], "rec": action[2]}
            elif kind == "slot_close":
                self.slot_menu = None
            elif kind == "slot_set":
                slot, rec = self.slot_menu["slot"], self.slot_menu["rec"]
                self.slot_menu = None
                match = [m for m in self.filaments
                         if m.get("id") == rec.get("filament")
                         and m.get("name") == rec.get("name")]
                self.editor = {"slot": slot, "step": "material",
                               "filter": "All", "page": 0,
                               "material": match[0] if match else {},
                               "colour": (rec.get("color") or "").upper()}
            elif kind == "ed_filter":
                self.editor.update(filter=action[1], page=0)
            elif kind == "ed_page":
                self.editor["page"] = max(0, self.editor["page"] + action[1])
            elif kind == "ed_pick":
                self.editor["material"] = action[1]
                if not self.editor.get("colour"):
                    self.editor["colour"] = (action[1].get("color")
                                             or "").upper()
            elif kind == "ed_step":
                self.editor["step"] = action[1]
            elif kind == "ed_colour":
                self.editor["colour"] = action[1]
            elif kind == "ed_cancel":
                self.editor = None
            elif kind == "ed_save":
                ed = self.editor
                m = ed["material"]
                # Several profiles share a Creality id and lookup by id
                # returns the first, so pick shared ones by name.
                shared = sum(1 for f in self.filaments
                             if f.get("id") == m.get("id")) > 1
                which = ('NAME="%s"' % m["name"].replace('"', "")
                         if shared else "FILAMENT=%s" % m["id"])
                mr.gcode("CFS_SLOT_SET SLOT=%s %s COLOR=%s" % (
                    ed["slot"], which, ed["colour"]))
                self.editor = None
            elif kind == "wifi_scan":
                self.wifi.scan()
            elif kind == "wifi_page":
                self.wifi_page = max(0, self.wifi_page + action[1])
            elif kind == "wifi_pick":
                if action[2]:
                    self.kb = {"ssid": action[1], "text": "", "shift": False,
                               "symbols": False, "show": False}
                else:
                    self.confirm = ("Join open network %s?" % action[1][:20],
                                    "@wifi:" + action[1])
            elif kind == "kb_key":
                self.kb["text"] += action[1]
                if self.kb["shift"]:
                    self.kb["shift"] = False
            elif kind == "kb_back":
                self.kb["text"] = self.kb["text"][:-1]
            elif kind == "kb_shift":
                self.kb["shift"] = not self.kb["shift"]
            elif kind == "kb_symbols":
                self.kb["symbols"] = not self.kb["symbols"]
            elif kind == "kb_show":
                self.kb["show"] = not self.kb["show"]
            elif kind == "kb_cancel":
                self.kb = None
            elif kind == "kb_join":
                ssid, pw = self.kb["ssid"], self.kb["text"]
                self.kb = None
                self.wifi.connect(ssid, pw, self.after_wifi)
            elif kind == "cancel_confirm":
                self.confirm = None
            elif kind == "do_confirm":
                script = self.confirm[1]
                self.confirm = None
                if script.startswith("CFS_SLOT_CLEAR"):
                    self.editor = None
                if script == "@restart":
                    mr.post("/printer/restart")
                elif script == "@usbformat":
                    self.start_usb_format()
                elif script.startswith("@wifi:"):
                    self.wifi.connect(script[6:], "", self.after_wifi)
                else:
                    if script in label:
                        self.running = (label[script], time.time())
                    elif "PA_CAL_RUN" in script or "PA_CAL_STATIC" in script:
                        self.running = ("Calibrating PA", time.time())
                    elif script.startswith("CFS_LOAD_SLOT"):
                        self.running = ("Loading slot " + script[-1],
                                        time.time())
                    elif script == "CFS_UNLOAD_ACTIVE":
                        self.running = ("Unloading", time.time())
                    elif script == "LOAD_FILAMENT":
                        self.running = ("Loading", time.time())
                    elif script == "UNLOAD_FILAMENT":
                        self.running = ("Unloading", time.time())
                    elif script.startswith("PA_CAL_RUN"):
                        self.running = ("Calibrating PA", time.time())
                    elif script.startswith("CALIBRATE_"):
                        self.running = ("Starting test print", time.time())
                    if script.startswith(("CFS_LOAD_SLOT", "CFS_UNLOAD",
                                          "PA_CAL_RUN", "PA_CAL_STATIC",
                                          "LOAD_FILAMENT",
                                          "UNLOAD_FILAMENT", "CALIBRATE_")):
                        self.slot_menu = None
                    mr.gcode(script)
            return

    def after_wifi(self, ok):
        self.ip = local_ip()
        self.dirty = True


def main():
    # The boot animation keeps looping until something takes the panel.
    subprocess.call(["killall", "boot-play"], stderr=subprocess.DEVNULL)
    cfg = Config(CFG_PATH)
    screen = Screen(cfg)
    screen.ip = local_ip()
    data = DataThread(cfg, screen)
    data.start()
    touch = Touch()
    touch.start()
    screen.set_sleep(False)
    if "brightness" in screen.prefs:
        set_brightness(screen.prefs["brightness"])
    seen, last_anim = -1, 0.0
    while True:
        now = time.time()
        if cfg.changed():
            cfg.reload()
            screen.apply_config()
        mr = Moonraker(cfg.get("screen", "moonraker"))
        if screen.restart_pending:
            restart()
        st, err = data.st, data.err
        screen.last_st = st
        # A new print starts with no Z offset steps of its own
        ps_now = st.get("print_stats", {})
        key = (ps_now.get("filename"), ps_now.get("state") in ("printing",
                                                               "paused"))
        if key[1] and screen.z_print != key:
            screen.z_print, screen.z_adjust = key, 0.
        elif not key[1]:
            screen.z_print = None
        if screen.prefs_pending and not err:
            screen.prefs_pending = False
            script = klipper_prefs(screen.prefs, st.get("print_stats", {}).get(
                "state") not in ("printing", "paused"))
            if script:
                mr.gcode(script)
        printing = st.get("print_stats", {}).get("state") in (
            "printing", "paused")
        taps = touch.pop()
        sleep_after = float(screen.prefs.get(
            "sleep_after", cfg.num("screen", "sleep_after")))
        if screen.asleep:
            if (taps or printing or screen.pa_result(st)
                    or screen.calib_pick(st) or screen.urgent(st, err)):
                screen.set_sleep(False)
                touch.last_activity = now
            taps = []
        elif (sleep_after > 0 and not printing and not screen.confirm
              and not screen.pa_result(st) and not screen.calib_pick(st)
              and not screen.urgent(st, err)
              and now - touch.last_activity > sleep_after):
            screen.set_sleep(True)
        for x, y in taps:
            try:
                screen.handle_tap(x, y, mr)
            except Exception:
                # one bad button must not stop the screen
                import traceback
                traceback.print_exc()
                sys.stdout.flush()
                screen.dirty = True
        if screen.asleep:
            time.sleep(0.2)
            continue
        if data.version != seen:
            seen = data.version
            if screen.view_key(st, err) != screen.last_key:
                screen.dirty = True
        if screen.dirty:
            screen.dirty = False
            screen.last_key = screen.view_key(st, err)
            old = screen.base
            was_modal = screen.modal_on
            try:
                screen.base = screen.render(st, err)
            except Exception:
                import traceback
                traceback.print_exc()
                sys.stdout.flush()
                screen.page, screen.editor, screen.kb = "status", None, None
                screen.confirm = None
                screen.base = screen.render(st, err)
            if screen.transition is not None and old is not None:
                new = (screen.pages.index(screen.page)
                       if screen.page in screen.pages else 0)
                direction = 1 if new >= screen.transition else -1
                screen.slide(old, screen.base, direction)
                screen.shown = None
            elif old is not None and screen.modal_on != was_modal:
                screen.fade(old, screen.base)
                screen.shown = None
            screen.transition = None
            digest = hashlib.md5(screen.base.tobytes()).digest()
            if digest != screen.shown:
                screen.show(screen.base)
                screen.shown = digest
                if os.path.exists(UPDATED_MARK):
                    # A self-update drew its first frame: keep it
                    os.remove(UPDATED_MARK)
        moving = bool(screen.anims or screen.flash)
        if moving and now - last_anim >= 1.0 / screen.ANIM_FPS:
            screen.animate(now)
            last_anim = now
        time.sleep(0.03 if moving else 0.08)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A self-update that cannot start goes back to the copy before it,
        # and that release is not fetched again
        if (os.path.exists(UPDATED_MARK)
                and os.path.exists(SELF + ".prev")):
            import traceback
            traceback.print_exc()
            with open(BAD_VERSION_FILE, "w") as f:
                f.write(own_version())
            old = read_text(UPDATED_MARK)
            os.replace(SELF + ".prev", SELF)
            with open(VERSION_FILE, "w") as f:
                f.write(old + "\n")
            os.remove(UPDATED_MARK)
            print("screen update failed to start; back to %s" % (old or "?"),
                  flush=True)
            restart()
        raise
