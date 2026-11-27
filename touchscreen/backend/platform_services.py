"""Headless platform services adapted from the existing open-source screen.

No framebuffer, Pillow or UI imports. Long operations run only in service workers.
"""

import os, re, json, time, threading, subprocess, shutil

USB_DEV = "sda"
USB_DEV_RE = r"^sd[a-z]$"
USB_MOUNT = "/mnt/exUDISK"
USB_LABEL = "K2FILES"
GCODE_LINK = "/mnt/UDISK/printer_data_mainline/gcodes"
TIMELAPSE_LINK = "/mnt/UDISK/printer_data_mainline/timelapse"
LOGS_LINK = "/mnt/UDISK/printer_data_mainline/logs"
BRIGHTNESS_SH = "/usr/bin/brightness.sh"
ZONEINFO = "/usr/share/zoneinfo"


class Wifi:
    """wpa_supplicant over its control socket (the K2 has no wpa_cli)."""

    CTRL = "/etc/wifi/wpa_supplicant/sockets/wlan0"

    def __init__(self, on_change=None):
        self.on_change = on_change or (lambda: None)
        self.networks = []  # (ssid, dbm, secured), strongest first
        self.busy = False
        self.message = ""
        self.lock = threading.Lock()

    def cmd(self, command, timeout=5):
        import socket

        local = "/tmp/openklipper_wpa_%d_%d" % (os.getpid(), threading.get_ident())
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
            st = dict(
                l.split("=", 1) for l in self.cmd("STATUS").splitlines() if "=" in l
            )
            sig = dict(
                l.split("=", 1)
                for l in self.cmd("SIGNAL_POLL").splitlines()
                if "=" in l
            )
            st["rssi"] = sig.get("RSSI", "")
            return st
        except Exception:
            return {}

    def scan(self):
        self.busy, self.message = True, "Scanning..."
        self.on_change()

        def run():
            try:
                self.cmd("SCAN")
                time.sleep(4)
                best = {}
                for line in self.cmd("SCAN_RESULTS").splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) < 5 or not parts[4]:
                        continue
                    ssid, dbm = parts[4], int(parts[2])
                    secured = any(k in parts[3] for k in ("WPA", "WEP", "SAE"))
                    if ssid not in best or dbm > best[ssid][1]:
                        best[ssid] = (ssid, dbm, secured)
                self.networks = sorted(best.values(), key=lambda n: -n[1])
                self.message = "%d networks found" % len(self.networks)
            except Exception as e:
                self.message = "Scan failed: %s" % e
            self.busy = False
            self.on_change()

        threading.Thread(target=run, daemon=True).start()

    def connect(self, ssid, password, on_done=None):
        """Add and switch to ssid; roll back to the old network on failure."""

        def run():
            self.busy, self.message = True, "Connecting to %s..." % ssid
            self.on_change()
            old = [l.split("\t")[0] for l in self.cmd("LIST_NETWORKS").splitlines()[1:]]
            nid = self.cmd("ADD_NETWORK").strip()
            ok = False
            try:
                self.cmd("SET_NETWORK %s ssid %s" % (nid, ssid.encode().hex()))
                if password:
                    self.cmd("SET_NETWORK %s psk %s" % (nid, json.dumps(password)))
                else:
                    self.cmd("SET_NETWORK %s key_mgmt NONE" % nid)
                self.cmd("SELECT_NETWORK %s" % nid)
                deadline = time.time() + 25
                while time.time() < deadline:
                    time.sleep(1)
                    st = self.status()
                    if st.get("wpa_state") == "COMPLETED" and st.get("ssid") == ssid:
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
                    ssid,
                    self.status().get("ip_address", "?"),
                )
            else:
                self.cmd("REMOVE_NETWORK %s" % nid)
                self.cmd("ENABLE_NETWORK all")
                self.cmd("REASSOCIATE")
                subprocess.call(["killall", "-USR1", "udhcpc"])
                self.message = "Could not join %s; back on the previous network" % ssid
            self.busy = False
            self.on_change()
            if on_done:
                on_done(ok)

        threading.Thread(target=run, daemon=True).start()


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, IOError):
        return ""


def set_brightness(level):
    if os.path.exists(BRIGHTNESS_SH):
        try:
            subprocess.call(
                ["sh", BRIGHTNESS_SH, str(int(level))],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception as e:
            print("brightness: %s" % e, flush=True)


def system_zone():
    """The printer's time zone as set in Creality's settings (uci)."""
    try:
        out = (
            subprocess.check_output(
                ["uci", "-q", "get", "system.@system[0].zonename"],
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            .decode()
            .strip()
        )
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
    for cmd in (
        ["uci", "set", "system.@system[0].zonename=%s" % name],
        ["uci", "commit", "system"],
    ):
        try:
            subprocess.call(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
            )
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
    # Creality's Python omits tzset; the zonefile still updates system time.
    refresh = getattr(time, "tzset", None)
    if refresh is not None:
        refresh()
    print("time zone: %s (%.1f s)" % (name, time.time() - t0), flush=True)


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
    model = " ".join(
        x
        for x in (read_text(base + "/device/vendor"), read_text(base + "/device/model"))
        if x
    )
    return {
        "dev": USB_DEV,
        "bytes": size,
        "gb": size / 1e9,
        "model": model or "USB drive",
        "id": "%s-%d" % (model, size),
        "mounted": os.path.ismount(USB_MOUNT),
    }


def usb_in_use():
    """G-code, time-lapse or log folders on the USB drive right now."""
    return [
        os.path.basename(link)
        for link in (GCODE_LINK, TIMELAPSE_LINK, LOGS_LINK)
        if os.path.realpath(link).startswith(USB_MOUNT + "/")
    ]


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
        mounted = [line.split()[1] for line in f if line.split()[0].startswith(path)]
    for point in mounted:
        subprocess.check_call(["umount", point])
    subprocess.check_call(
        ["mkfs.fat", "-F", "32", "-I", "-n", USB_LABEL, path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        # Forget the old partitions (sda1 ...) now that sda is one volume
        import fcntl

        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.ioctl(fd, 0x125F)  # BLKRRPART
        finally:
            os.close(fd)
    except OSError:
        pass
    os.makedirs(mount, exist_ok=True)
    subprocess.check_call(["mount", "-t", "vfat", "-o", "rw,async", path, mount])


def unmount_usb(mount=USB_MOUNT, tries=20):
    """Flush and unmount the USB drive; True when it is safe to remove."""
    subprocess.call(["sync"])
    for _ in range(tries):
        if not os.path.ismount(mount):
            return True
        if subprocess.call(["umount", mount], stderr=subprocess.DEVNULL) == 0:
            return True
        time.sleep(2)
    return not os.path.ismount(mount)


def file_filaments(meta):
    """The filaments a print file uses, from Moonraker's metadata, as
    [{"tool": n, "colour": "RRGGBB" or "", "type": "PLA" or ""}]. OrcaSlicer
    lists every filament of the project; the ones with no weight in
    filament_weights are not printed and left out."""

    def as_list(value):
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        value = str(value or "").strip()
        if value.startswith("["):
            try:
                return [str(v) for v in json.loads(value)]
            except ValueError:
                value = value.strip("[]")
        return [v.strip(" \"'") for v in re.split(r"[;,]", value)]

    colours = [
        c.strip().lstrip("#")[:6].upper()
        for c in as_list(meta.get("filament_colors") or meta.get("extruder_colors"))
    ]
    colours = [c if re.fullmatch(r"[0-9A-F]{6}", c) else "" for c in colours]
    kinds = [k.strip().upper() for k in as_list(meta.get("filament_type"))]
    while colours and not colours[-1]:
        colours.pop()
    while kinds and not kinds[-1]:
        kinds.pop()
    count = max(len(colours), len(kinds))
    weights = meta.get("filament_weights")
    used = list(range(count))
    if isinstance(weights, (list, tuple)) and len(weights) == count:
        try:
            picked = [i for i in used if float(weights[i] or 0) > 0]
        except (TypeError, ValueError):
            picked = []
        used = picked or used
    return [
        {
            "tool": i,
            "colour": colours[i] if i < len(colours) else "",
            "type": kinds[i] if i < len(kinds) else "",
        }
        for i in used
    ]


def tool_map_script(path, mapping):
    """CFS_TOOL_MAP for the print about to start from the screen."""
    line = 'CFS_TOOL_MAP RESET=1 FILE="%s"' % path.replace('"', "")
    for t in sorted(mapping):
        line += " T%d=%s" % (t, mapping[t])
    return line
