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
               "timezone": "uk"},
}

OBJECTS = {
    "print_stats": ["state", "filename", "message"],
    "virtual_sdcard": ["progress", "file_position", "is_active"],
    "display_status": ["progress", "message"],
    "extruder": ["temperature", "target"],
    "heater_bed": ["temperature", "target"],
    "temperature_sensor chamber_temperature": ["temperature"],
    "fan": ["speed"],
    "toolhead": ["homed_axes"],
    "gcode_move": ["gcode_position"],
    "k2_cfs": ["fitted", "online", "active_slot", "feed_slot", "running_out",
               "runout_next",
               "temperature", "humidity"],
    "hark_cfs_slots": ["slots"],
    "filament_switch_sensor filament_sensor": ["filament_detected"],
    "k2_motor_bus": ["motors_ready"],
    "output_pin chamber_led": ["value"],
    "webhooks": ["state", "state_message"],
    "idle_timeout": ["state"],
    "pa_pressure_log": ["last"],
    "hark_update": ["installed", "latest", "update_available", "updating"],
}

# The last PA calibration result the user has dismissed (its stamp)
PA_ACK_FILE = "/mnt/UDISK/harkscreen/.pa_result_ack"


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
    """UTC timestamp to struct_time in `zone`: "uk" (GMT/BST) or hours."""
    zone = zone.strip().lower()
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
        settings_at = wifi_at = 0
        while True:
            mr = Moonraker(self.cfg.get("screen", "moonraker"))
            st, err = {}, None
            try:
                st = mr.query()
                if st.get("webhooks", {}).get("state") != "ready":
                    err = "Klipper " + st.get("webhooks", {}).get("state",
                                                                   "?")
                if time.time() - settings_at > 30:
                    self.screen.settings = mr.settings()
                    self.screen.filaments = mr.filaments()
                    settings_at = time.time()
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
        self.asleep = False
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
            if p in ("status", "cfs", "controls", "machine", "wifi")]
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

    def text(self, d, xy, s, font="m", color="text", anchor="la"):
        """Paste cached glyph masks; freetype is slow on the K2."""
        s = str(s)
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

    def wrap(self, text, font, width):
        """Split text into lines no wider than width pixels."""
        f = self.fonts[font]
        lines = []
        for para in text.split("\n"):
            line = ""
            for word in para.split():
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
        if icon and box[3] - box[1] > 90:
            self.icon(d, icon, cx, cy - 18, 34, tc)
            self.text(d, (cx, cy + 28), label, "s", tc, anchor="mm")
        else:
            self.text(d, (cx, cy), label, font, tc, anchor="mm")
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
        """A PA calibration result not yet dismissed with OK, or None."""
        last = (st.get("pa_pressure_log") or {}).get("last") or {}
        stamp = last.get("stamp")
        if stamp and stamp != self.pa_ack:
            return last
        return None

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
            round((st.get("display_status", {}).get("progress")
                   or vs.get("progress") or 0) * 100),
            round(st.get("fan", {}).get("speed", 0) * 100), round(z, 2), rem,
            st.get("k2_cfs"), st.get("hark_cfs_slots"),
            st.get("filament_switch_sensor filament_sensor"),
            st.get("k2_motor_bus"), st.get("output_pin chamber_led"),
            st.get("toolhead"), st.get("webhooks", {}).get("state"),
            self.wifi.busy, self.wifi.message, self.wifi.networks,
            self.wifi_status, len(self.settings), len(self.filaments),
            self.ip, bool(self.pa_result(st))], sort_keys=True, default=str)

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
            self.draw_offline(d, body, err)
        elif self.editor:
            self.draw_editor(d, body, st)
        else:
            getattr(self, "page_" + self.page)(d, body, st)
        self.draw_nav(d)
        if not err and self.busy(st) and not self.editor and not self.kb:
            self.draw_busy(d, body, st)
        if self.slot_menu and not self.confirm:
            self.draw_slot_menu(st)
        if self.confirm:
            self.draw_confirm(d)
        res = None if err else self.pa_result(st)
        if res and not self.confirm:
            self.draw_pa_result(res)
        return self.canvas

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
        if good:
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
                    ("pa_ok", res.get("stamp")), "accent", True, "m")

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
        self.text(d, (box[0] + 90, box[1] + 70), "%s · %s°C" % (
            rec.get("name", "Empty"), rec.get("nozzle_temp", "-"))
            if rec.get("loaded") else "Empty", "xs", "muted")
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
                                "About 5 min, heats to %d°" % temp,
                                "PA_CAL_RUN SLOT=E AUTO=1"), "accent"))
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
                            "About 5 min, heats to %d°" % (slot, temp),
                            "PA_CAL_RUN SLOT=%s AUTO=1" % slot), "accent"))
        elif rec.get("loaded"):
            q = ("Load slot %s and purge out %s?\nHeats to %d°"
                 % (slot, active, temp)) if active else \
                ("Load slot %s?\nHeats to %d°" % (slot, temp))
            actions.append(("Load & purge", ("confirm", q,
                            "CFS_LOAD_SLOT SLOT=%s" % slot), "accent"))
            # A new reel in any slot: load it, then calibrate it
            actions.append(("Calibrate PA", ("confirm",
                            "Load slot %s, then calibrate PA?\n"
                            "About 7 min, heats to %d°" % (slot, temp),
                            "CFS_LOAD_SLOT SLOT=%s COOL=0\nPA_CAL_RUN SLOT=%s AUTO=1"
                            % (slot, slot)), "accent"))
        actions.append(("Set filament", ("slot_set",), "accent"))
        actions.append(("Close", ("slot_close",), "muted"))
        bw = (box[2] - box[0] - 3 * 16) // 2
        for i, (label, action, color) in enumerate(actions):
            bx = box[0] + 16 + (i % 2) * (bw + 16)
            by = box[1] + 100 + (i // 2) * 92
            self.button(d, (bx, by, bx + bw, by + 78), label, action, color,
                        True, "s")

    def draw_busy(self, d, body, st):
        x0, y0, x1, y1 = body
        box = (x0 + 14, y1 - 62, x1 - 14, y1 - 10)
        self.panel(d, box, self.c["accent"], True,
                   mix(self.c["background"], self.c["accent"], 0.15))
        label = self.running[0] if self.running else "Klipper"
        detail = ""
        ex = st.get("extruder", {})
        if ex.get("target") and ex.get("temperature", 0) < ex["target"] - 3:
            detail = "  ·  nozzle %d → %d°" % (ex["temperature"], ex["target"])
        self.text(d, (box[0] + 50, (box[1] + box[3]) // 2),
                  "%s in progress%s" % (label, detail), "s", anchor="lm")
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

    def draw_offline(self, d, body, err):
        x0, y0, x1, y1 = body
        cx = (x0 + x1) // 2
        self.paste_logo((cx - 130, y0 + 20, cx + 130, y0 + 280))
        self.text(d, (cx, y0 + 320), "Klipper is not ready", "m", "warn",
                  anchor="mm")
        self.text(d, (cx, y0 + 360), err[:50], "xs", "muted", anchor="mm")
        self.anims.append({"kind": "spin", "center": (cx, y0 + 395), "r": 12,
                           "rect": (cx - 18, y0 + 377, cx + 18, y0 + 413)})

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
            self.text(d, (lx, ly + 16), name[:30], "xs", "muted")
            prog = st.get("display_status", {}).get("progress") or \
                st.get("virtual_sdcard", {}).get("progress", 0)
            prog = max(0., min(1., prog))
            self.text(d, (lx, ly + 44), "%d%%" % round(prog * 100), "xl")
            rem = self.eta.remaining_min(
                ps.get("filename", ""),
                st.get("virtual_sdcard", {}).get("file_position", 0))
            if state == "printing" and rem is not None:
                done = time.strftime("%H:%M", local_time(
                    time.time() + rem * 60, self.cfg.get("screen",
                                                          "timezone")))
                hrs = "%dh %02dm" % (rem // 60, rem % 60) if rem >= 60 \
                    else "%d min" % rem
                self.text(d, (left[2] - 18, ly + 58), hrs + " left", "m",
                          anchor="ra")
                self.text(d, (left[2] - 18, ly + 98), "done about " + done,
                          "xs", "muted", anchor="ra")
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
            z = st.get("gcode_move", {}).get("gcode_position", [0, 0, 0])[2]
            self.text(d, (lx, left[3] - 30), "Z %.2f mm" % z, "s", "muted",
                      anchor="lm")
        elif self.cfg.flag("brand", "idle_logo"):
            self.paste_logo((left[0], left[1] - 6, left[2], left[3] + 6))
        # Temperature gauges down the right
        tiles = [("Nozzle", "nozzle", st.get("extruder", {})),
                 ("Bed", "bed", st.get("heater_bed", {})),
                 ("Chamber", "chamber",
                  st.get("temperature_sensor chamber_temperature", {}))]
        tx0, tx1 = x0 + 434, x1 - pad
        th = (left[3] - left[1] - 2 * 10) // 3
        for i, (label, icon, h) in enumerate(tiles):
            ty = left[1] + i * (th + 10)
            self.gauge(d, (tx0, ty, tx1, ty + th), label, icon, h)
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
                "s", anchor="lm")
        elif warning:
            # Early runout warning from the CFS entry sensor
            nxt = cfs.get("runout_next")
            self.text(d, (sx + 28, fy + 27), "Slot %s running out · %s" % (
                warning, ("%s takes over" % nxt) if nxt else "will pause"),
                "s", color="accent", anchor="lm")
        else:
            self.text(d, (sx + 28, fy + 27), "Slot %s · %s" % (
                slot or "-", rec.get("name", "no filament") if slot else
                "no filament"), "s", anchor="lm")
        fx = x1 - pad - 140
        self.text(d, (x1 - pad - 18, fy + 27), "fan %d%%" % round(fan * 100),
                  "s", anchor="rm")
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
            self.text(d, (tx, sy - 52), rec.get("name", "?")[:22], "m")
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
                self.text(d, (tx, cy + 18), rec.get("name", "?")[:16], "s")
                self.text(d, (tx, cy + 56), "%s · %s°C" % (
                    rec.get("type", ""), rec.get("nozzle_temp", "?")), "xs",
                    "muted")
                extras = []
                if rec.get("pressure_advance"):
                    extras.append("PA %.3f" % rec["pressure_advance"])
                if rec.get("retract_length"):
                    extras.append("ret %.1f" % rec["retract_length"])
                self.text(d, (tx, cy + 86), "  ".join(extras), "xs", "accent")
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
             ("gcode", "SET_PIN PIN=chamber_led VALUE=%d" % (0 if light
                                                             else 1)),
             "accent", True, "light"),
            ("Motors off", ("gcode", "M84"), "accent", not busy, "motor"),
            ("Heaters off", ("gcode", "TURN_OFF_HEATERS"), "warn",
             ps not in ("printing", "paused"), "heat_off"),
            ("Restart", ("confirm", "Restart Klipper?", "@restart"),
             "error", ps not in ("printing", "paused"), "restart"),
        ]
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
                self.text(d, (bx + 14, by + ih // 2), m.get("name", "?")[:20],
                          "xs", anchor="lm")
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
                      "Slot %s: %s colour" % (slot, m.get("name", "")), "s")
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
                  "text" if ws.get("ssid") else "warn")
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
            self.text(d, (bx + 14, by + 18), ssid[:22], "xs")
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
        self.text(d, (x0 + pad, y0 + 6), "Password for %s" % kb["ssid"][:24],
                  "xs", "muted")
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
                 "wifi": "Wi-Fi"}
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
            r = 6 + 11 * s
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
            elif kind == "pa_ok":
                self.pa_ack = action[1] or 0.
                try:
                    with open(PA_ACK_FILE, "w") as f:
                        f.write(repr(self.pa_ack))
                except Exception:
                    pass
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
                elif script.startswith("@wifi:"):
                    self.wifi.connect(script[6:], "", self.after_wifi)
                else:
                    if script in label:
                        self.running = (label[script], time.time())
                    elif "PA_CAL_RUN" in script:
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
                    if script.startswith(("CFS_LOAD_SLOT", "CFS_UNLOAD",
                                          "PA_CAL_RUN", "LOAD_FILAMENT",
                                          "UNLOAD_FILAMENT")):
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
    seen, last_anim = -1, 0.0
    while True:
        now = time.time()
        if cfg.changed():
            cfg.reload()
            screen.apply_config()
        mr = Moonraker(cfg.get("screen", "moonraker"))
        st, err = data.st, data.err
        printing = st.get("print_stats", {}).get("state") in (
            "printing", "paused")
        taps = touch.pop()
        sleep_after = cfg.num("screen", "sleep_after")
        if screen.asleep:
            if taps or printing or screen.pa_result(st):
                screen.set_sleep(False)
                touch.last_activity = now
            taps = []
        elif (sleep_after > 0 and not printing and not screen.confirm
              and not screen.pa_result(st)
              and now - touch.last_activity > sleep_after):
            screen.set_sleep(True)
        for x, y in taps:
            screen.handle_tap(x, y, mr)
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
            screen.transition = None
            digest = hashlib.md5(screen.base.tobytes()).digest()
            if digest != screen.shown:
                screen.show(screen.base)
                screen.shown = digest
        moving = bool(screen.anims or screen.flash)
        if moving and now - last_anim >= 1.0 / screen.ANIM_FPS:
            screen.animate(now)
            last_anim = now
        time.sleep(0.03 if moving else 0.08)


if __name__ == "__main__":
    main()
