#!/usr/bin/env python3
"""Hark Screen for the Creality Hi's 2.8" panel (240x320 portrait, mounted
landscape: the UI draws at 320x240 and show() rotates it, as Creality's own
screen does).

A compact sibling of harkscreen.py (the K2's 800x480 screen): it reuses that
file's Moonraker client, touch reader, M73 ETA and config loader, with pages
sized for the small panel: status, print files, filament (the CFS slots:
load, unload, measure pressure advance, pick the filament profile and
colour; the spool holder when no CFS answers), controls (move, preheat,
nozzle clean) and machine (IP, brightness, automatic PA, restart, back to
stock). Draws with Pillow straight to /dev/fb0; talks to Moonraker, so it
works the same with Klipper on the printer or on an external host.

This file may be distributed under the terms of the GNU GPLv3 license.
"""
import os, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harkscreen as hs  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

W, H = 320, 240            # drawn layout (landscape)
FB_W, FB_H = 240, 320      # panel framebuffer (portrait)
HEAD_H, NAV_H = 28, 38
BODY = (0, HEAD_H, W, H - NAV_H)
OKL_DIR = "/mnt/UDISK/okl-hi"

hs.GCODE_DIR = "/mnt/UDISK/printer_data/gcodes"   # the Hi's print files (Eta)
hs.DEFAULTS = {
    "brand": {"name": "Hi OpenKlipper"},
    "theme": {"background": "#12171d", "panel": "#1b232c",
              "panel_edge": "#3c4b5a", "accent": "#d9434a",
              "text": "#eef2f6", "muted": "#8a97a6", "ok": "#2ecc71",
              "warn": "#f5a623", "error": "#ff6b6b",
              "font": "/etc/sysConfig/defData/SourceHanSansSC-Bold.otf",
              "font_scale": "1.0"},
    "screen": {"moonraker": "http://127.0.0.1:7125", "refresh": "1.0",
               "sleep_after": "600",
               # Bench 15 Nov 2026: raw touch = framebuffer pixels
               "touch_max_x": "240", "touch_max_y": "320",
               "touch_swap_xy": "no", "touch_invert_x": "no",
               "touch_invert_y": "no", "rotate": "90"},
    "materials": {"PLA": "220/60", "PETG": "250/80", "ABS": "260/100"},
}

OBJECTS = {
    "print_stats": ["state", "filename", "message", "print_duration",
                    "info"],
    "virtual_sdcard": ["progress", "file_position"],
    "display_status": ["progress", "message"],
    "extruder": ["temperature", "target", "pressure_advance"],
    "heater_bed": ["temperature", "target"],
    "fan": ["speed"],
    "toolhead": ["homed_axes", "position"],
    "gcode_move": ["speed_factor"],
    "webhooks": ["state", "state_message"],
    "k2_motor_bus": ["motors_ready"],
    "pause_resume": ["is_paused"],
    # CFS and automatic pressure advance (absent objects are just left out)
    "k2_cfs": ["fitted", "online", "active_slot", "feed_slot",
               "change_pending"],
    "hark_cfs_slots": ["slots"],
    "hark_flow_cal": ["pa_auto"],
    "gcode_macro _PA_STATIC_SAVE": ["last"],
    "gcode_macro _PA_AUTO": ["on_print_start"],
    "filament_switch_sensor filament_sensor": ["filament_detected"],
}

CFS_SLOTS = "ABCD"
# Same names and colours as the KlipperScreen CFS panel and hark_cfs_slots
COLOURS = (
    ("Black", "000000"), ("White", "FFFFFF"), ("Grey", "808080"),
    ("Silver", "C0C0C0"), ("Red", "E53935"), ("Orange", "FB8C00"),
    ("Yellow", "FDD835"), ("Gold", "FFC107"), ("Green", "43A047"),
    ("Lime", "C0CA33"), ("Cyan", "26EEEE"), ("Blue", "1E88E5"),
    ("Navy", "1A237E"), ("Purple", "8E24AA"), ("Pink", "EC407A"),
    ("Brown", "6D4C41"), ("Beige", "D7CCC8"),
)
TYPE_ORDER = ("PLA", "PETG", "ABS", "ASA", "TPU", "PLA-CF", "PETG-CF")
PROFILE_ROWS = 3           # filament profiles per page (2 columns)


def colour_rgb(hexval):
    h = (hexval or "").strip().lstrip("#")
    if len(h) < 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def colour_name(hexval):
    h = (hexval or "").strip().lstrip("#").upper()
    for name, v in COLOURS:
        if v == h:
            return name
    return ("#" + h) if h else ""


def filament_types(filaments):
    present = sorted({f.get("type") for f in filaments if f.get("type")})
    return [t for t in TYPE_ORDER if t in present] + \
        [t for t in present if t not in TYPE_ORDER]


def filaments_of_type(filaments, mtype):
    """Custom profiles first, then Creality's own, then generic ones."""
    found = [f for f in filaments if f.get("type") == mtype]
    found.sort(key=lambda f: (f.get("source") == "creality",
                              f.get("brand") != "Creality",
                              f.get("name", "")))
    return found


def pa_auto_on(st):
    """PA_CAL_AUTO's switch, else the print start's own default."""
    sw = st.get("hark_flow_cal", {}).get("pa_auto")
    if sw is None:
        sw = st.get("gcode_macro _PA_AUTO", {}).get("on_print_start", 0)
    return bool(sw)

TABS = (("status", "Status"), ("files", "Files"), ("filament", "Filament"),
        ("control", "Control"), ("machine", "Machine"))
BRIGHTNESS = (25, 50, 75, 100)
SLEEP_CHOICES = ((300, "5 min"), (600, "10 min"), (1800, "30 min"),
                 (0, "Never"))
ROWS = 4                   # files per page


def fmt_time(sec):
    sec = int(sec or 0)
    h, m = sec // 3600, (sec % 3600) // 60
    return "%dh %02dm" % (h, m) if h else "%dm" % m


def ago(ts):
    d = time.time() - ts
    if d < 3600:
        return "%dm ago" % max(1, d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    return "%dd ago" % (d // 86400)


class Mr(hs.Moonraker):
    def query(self):
        import json, urllib.parse, urllib.request
        q = "&".join(urllib.parse.quote(n) + "=" + ",".join(f)
                     for n, f in OBJECTS.items())
        with urllib.request.urlopen(
                self.base + "/printer/objects/query?" + q, timeout=3) as r:
            return json.load(r)["result"]["status"]

    def library(self):
        """Filament profiles (hark_cfs_slots: library + Creality's)."""
        import json, urllib.request
        with urllib.request.urlopen(
                self.base + "/printer/objects/query?hark_cfs_slots="
                "library,materials", timeout=5) as r:
            h = json.load(r)["result"]["status"].get("hark_cfs_slots", {})
        return list(h.get("library") or []) + list(h.get("materials") or [])


class Thumbs(threading.Thread):
    """Fetches print-file thumbnails and metadata off the draw loop."""
    def __init__(self, screen):
        super().__init__(daemon=True)
        self.screen = screen
        self.cache = {}            # (file, size) -> Image or None
        self.meta = {}             # file -> metadata dict
        self.want = []
        self.lock = threading.Lock()
        self.event = threading.Event()

    def get(self, filename, size):
        key = (filename, size)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            if key not in self.want:
                self.want.append(key)
        self.event.set()
        return None

    def metadata(self, filename):
        with self.lock:
            if filename in self.meta:
                return self.meta[filename]
            if (filename, 0) not in self.want:
                self.want.append((filename, 0))
        self.event.set()
        return None

    def run(self):
        while True:
            self.event.wait(2)
            self.event.clear()
            while True:
                with self.lock:
                    if not self.want:
                        break
                    filename, size = self.want.pop(0)
                mr = Mr(self.screen.cfg.get("screen", "moonraker"))
                try:
                    meta = self.meta.get(filename)
                    if meta is None:
                        meta = mr.metadata(filename)
                        with self.lock:
                            self.meta[filename] = meta
                    img = None
                    if size:
                        full = mr.thumb_image(filename, meta)
                        if full is not None:
                            full.thumbnail((size, size))
                            img = full
                except Exception as e:
                    print("thumb %s: %s" % (filename, e), flush=True)
                    meta, img = {}, None
                    with self.lock:
                        self.meta.setdefault(filename, {})
                with self.lock:
                    if size:
                        self.cache[(filename, size)] = img
                self.screen.dirty = True


class Data(threading.Thread):
    def __init__(self, screen):
        super().__init__(daemon=True)
        self.screen = screen
        self.st, self.err = {}, "Starting"
        self.lib_at = 0.

    def run(self):
        files_at = 0.
        while True:
            mr = Mr(self.screen.cfg.get("screen", "moonraker"))
            st, err = {}, None
            try:
                st = mr.query()
                wh = st.get("webhooks", {})
                if wh.get("state") != "ready":
                    err = wh.get("state_message") or "Klipper %s" % wh.get(
                        "state", "?")
                if (self.screen.page == "files"
                        and time.time() - files_at > 10):
                    self.screen.files = mr.files()
                    self.screen.dirty = True
                    files_at = time.time()
                if (self.screen.page == "filament"
                        and time.time() - self.lib_at > 60):
                    self.screen.library = mr.library()
                    self.screen.dirty = True
                    self.lib_at = time.time()
            except Exception:
                try:
                    info = mr.info()
                    err = info.get("state_message") or "Klipper " + info.get(
                        "state", "starting")
                except Exception:
                    err = "Waiting for Moonraker"
            if self.screen.page != "files":
                files_at = 0.
            if (st, err) != (self.st, self.err):
                self.st, self.err = st, err
                self.screen.dirty = True
            time.sleep(self.screen.cfg.num("screen", "refresh"))


class Screen:
    def __init__(self, cfg):
        self.cfg = cfg
        self.page = "status"
        self.sub = None            # "move" on control, file path on files
        self.confirm = None        # (question, callback)
        self.notice = None         # (text, colour, until)
        self.files = []
        self.library = []          # filament profiles for the slot editor
        self.file_page = 0
        self.step = 10.
        self.prefs = hs.load_prefs()
        self.material = self.prefs.get("material", "PLA")
        self.buttons = []
        self.dirty = True
        self.asleep = False
        self.flash = None
        self.ip = "?"
        self.fb = os.open(hs.FB, os.O_RDWR)
        self.thumbs = Thumbs(self)
        self.eta = hs.Eta()
        self.apply_config()

    # --- look ------------------------------------------------------------
    def apply_config(self):
        c = self.cfg
        self.col = {k: hs.hex_rgb(c.get("theme", k)) for k in (
            "background", "panel", "panel_edge", "accent", "text", "muted",
            "ok", "warn", "error")}
        scale = c.num("theme", "font_scale")
        self.fonts = {}
        for name, size in (("s", 12), ("m", 15), ("l", 20), ("xl", 30)):
            try:
                self.fonts[name] = ImageFont.truetype(
                    c.get("theme", "font"), int(size * scale))
            except OSError:
                self.fonts[name] = ImageFont.load_default()
        self.dirty = True

    def materials(self):
        out = {}
        for name, value in self.cfg.cp.items("materials"):
            try:
                n, b = value.split("/")
                out[name.upper()] = (int(n), int(b))
            except ValueError:
                pass
        return out or {"PLA": (220, 60)}

    def fit(self, s, font, width):
        f = self.fonts[font]
        if f.getlength(s) <= width:
            return s
        while s and f.getlength(s + "...") > width:
            s = s[:-1]
        return s + "..."

    def text(self, d, xy, s, font="m", color="text", anchor="la",
             width=None):
        if width:
            s = self.fit(s, font, width)
        d.text(xy, s, font=self.fonts[font],
               fill=self.col.get(color, color), anchor=anchor)

    def button(self, d, rect, label, action, kind="panel", font="m",
               enabled=True):
        x0, y0, x1, y1 = rect
        fill = {"panel": self.col["panel"], "accent": self.col["accent"],
                "ok": self.col["ok"], "warn": self.col["warn"]}[kind]
        if not enabled:
            fill = hs.mix(self.col["panel"], self.col["background"], .5)
        if self.flash and self.flash[0] == rect \
                and time.time() - self.flash[1] < .25:
            fill = hs.mix(fill, (255, 255, 255), .35)
        d.rounded_rectangle(rect, 8, fill=fill,
                            outline=self.col["panel_edge"]
                            if kind == "panel" else None)
        if font == "m" and self.fonts["m"].getlength(label) > x1 - x0 - 8:
            font = "s"     # long labels on narrow buttons
        self.text(d, ((x0 + x1) // 2, (y0 + y1) // 2), label, font,
                  "text" if enabled else "muted", "mm", x1 - x0 - 8)
        if enabled:
            self.buttons.append((rect, action))

    def grid(self, d, labels, cols, rows, top, bottom, gap=6):
        """Lay out (label, action, kind, enabled) buttons in a grid."""
        bw = (W - gap * (cols + 1)) // cols
        bh = (bottom - top - gap * (rows - 1)) // rows
        for i, item in enumerate(labels):
            if item is None:
                continue
            label, action = item[0], item[1]
            kind = item[2] if len(item) > 2 else "panel"
            enabled = item[3] if len(item) > 3 else True
            c, r = i % cols, i // cols
            x0 = gap + c * (bw + gap)
            y0 = top + r * (bh + gap)
            self.button(d, (x0, y0, x0 + bw, y0 + bh), label, action, kind,
                        "m", enabled)

    # --- state helpers ---------------------------------------------------
    def state(self, st):
        return st.get("print_stats", {}).get("state") or "standby"

    def printing(self, st):
        return self.state(st) in ("printing", "paused")

    def progress(self, st):
        p = st.get("display_status", {}).get("progress")
        if not p:
            p = st.get("virtual_sdcard", {}).get("progress") or 0.
        return float(p or 0.)

    def remaining(self, st):
        ps = st.get("print_stats", {})
        rem = self.eta.remaining_min(
            ps.get("filename") or "",
            st.get("virtual_sdcard", {}).get("file_position") or 0)
        if rem is not None:
            return rem * 60
        p, dur = self.progress(st), ps.get("print_duration") or 0
        if p > .02 and dur:
            return dur * (1 - p) / p
        return None

    def say(self, text, colour="text", secs=4.):
        self.notice = (text, colour, time.time() + secs)
        self.dirty = True

    def run(self, script, done=None, timeout=1800):
        """Run G-code in the background; errors come back as a notice."""
        def go():
            mr = Mr(self.cfg.get("screen", "moonraker"))
            try:
                mr.run_gcode(script, timeout=timeout)
                if done:
                    self.say(done, "ok")
            except Exception as e:
                self.say(str(e).replace("\n", " "), "error", 8.)
        threading.Thread(target=go, daemon=True).start()

    # --- drawing ---------------------------------------------------------
    def frame(self, st, err):
        img = Image.new("RGB", (W, H), self.col["background"])
        self.img = img
        d = ImageDraw.Draw(img)
        self.buttons = []
        self.draw_header(d, st, err)
        if err and (not st or self.page == "status"):
            self.draw_error(d, err)
        elif self.page == "status":
            self.draw_status(d, st, err)
        elif self.page == "files":
            if self.sub:
                self.draw_file(d, st, self.sub)
            else:
                self.draw_files(d, st)
        elif self.page == "filament":
            sub = self.sub or ""
            if sub.startswith("slot:"):
                self.draw_slot(d, st, sub[5:])
            elif sub.startswith("types:"):
                self.draw_types(d, sub[6:])
            elif sub.startswith("prof:"):
                _, slot, mtype, page = sub.split(":", 3)
                self.draw_profiles(d, st, slot, mtype, int(page))
            elif sub.startswith("colour:"):
                self.draw_colours(d, st, sub[7:])
            else:
                self.draw_filament(d, st)
        elif self.page == "control":
            if self.sub == "move":
                self.draw_move(d, st)
            else:
                self.draw_control(d, st)
        elif self.page == "machine":
            self.draw_machine(d, st, err)
        self.draw_nav(d)
        if self.notice and time.time() < self.notice[2]:
            text, colour = self.notice[:2]
            d.rectangle((0, H - NAV_H - 24, W, H - NAV_H),
                        fill=self.col["panel"])
            self.text(d, (W // 2, H - NAV_H - 12), text, "s", colour, "mm",
                      W - 12)
        if self.confirm:
            self.draw_confirm(img)
        return img

    def draw_header(self, d, st, err):
        d.rectangle((0, 0, W, HEAD_H), fill=self.col["panel"])
        state = self.state(st)
        if err:
            label, colour = "Not ready", "error"
        elif state == "printing":
            label, colour = "Printing %d%%" % (self.progress(st) * 100), "ok"
        elif state == "paused":
            label, colour = "Paused", "warn"
        elif state == "error":
            label, colour = "Print error", "error"
        else:
            label, colour = self.cfg.get("brand", "name"), "text"
        self.text(d, (8, HEAD_H // 2), label, "m", colour, "lm", 150)
        e, b = st.get("extruder", {}), st.get("heater_bed", {})
        temps = "N %d  B %d" % (e.get("temperature") or 0,
                                b.get("temperature") or 0)
        self.text(d, (W - 8, HEAD_H // 2), temps, "m", "muted", "rm")

    def draw_nav(self, d):
        y0 = H - NAV_H
        d.rectangle((0, y0, W, H), fill=self.col["panel"])
        tw = W // len(TABS)
        # one size for every tab: the largest that fits them all
        font = "m" if all(self.fonts["m"].getlength(lb) <= tw - 6
                          for _, lb in TABS) else "s"
        for i, (page, label) in enumerate(TABS):
            rect = (i * tw, y0, (i + 1) * tw, H)
            on = page == self.page
            if on:
                d.rectangle((rect[0] + 6, y0, rect[2] - 6, y0 + 3),
                            fill=self.col["accent"])
            self.text(d, ((rect[0] + rect[2]) // 2, y0 + NAV_H // 2 + 1),
                      label, font, "text" if on else "muted", "mm", tw - 4)
            self.buttons.append((rect, ("page", page)))

    def draw_error(self, d, err):
        self.text(d, (W // 2, 70), "Printer not ready", "l", "error", "mm")
        lines = self.wrap(err, "s", W - 24)[:3]
        for i, line in enumerate(lines):
            self.text(d, (W // 2, 98 + i * 16), line, "s", "muted", "mm")
        self.button(d, (60, 150, 260, 190), "Restart firmware",
                    ("gcode_post", "/printer/firmware_restart"), "accent")

    def wrap(self, s, font, width):
        words, lines, cur = (s or "").split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if self.fonts[font].getlength(t) <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def temp_tile(self, d, rect, label, heater, preset):
        x0, y0, x1, y1 = rect
        d.rounded_rectangle(rect, 8, fill=self.col["panel"])
        cur, tgt = heater.get("temperature") or 0, heater.get("target") or 0
        self.text(d, (x0 + 10, y0 + 4), label, "s", "muted")
        self.text(d, (x0 + 10, y1 - 4), "%d" % round(cur), "xl",
                  "warn" if tgt and abs(cur - tgt) > 3 else "text", "ld")
        self.text(d, (x1 - 10, y1 - 10),
                  ("/ %d" % tgt) if tgt else "off", "m", "muted", "rd")

    def draw_status(self, d, st, err):
        top = HEAD_H + 6
        if self.printing(st):
            self.draw_printing(d, st, top)
            return
        e, b = st.get("extruder", {}), st.get("heater_bed", {})
        half = (W - 18) // 2
        self.temp_tile(d, (6, top, 6 + half, top + 58), "Nozzle", e, 0)
        self.temp_tile(d, (12 + half, top, W - 6, top + 58), "Bed", b, 0)
        y = top + 64
        state = self.state(st)
        ps = st.get("print_stats", {})
        if state == "complete":
            line, colour = "Finished: " + os.path.basename(
                ps.get("filename") or ""), "ok"
        elif state == "cancelled":
            line, colour = "Cancelled: " + os.path.basename(
                ps.get("filename") or ""), "muted"
        elif state == "error":
            line, colour = ps.get("message") or "Print error", "error"
        else:
            line, colour = "Ready", "ok"
        self.text(d, (8, y), line, "m", colour, width=W - 16)
        homed = st.get("toolhead", {}).get("homed_axes") or ""
        self.loaded_line(d, st, (8, y + 20), W - 16)
        self.text(d, (8, y + 36), "IP %s   Homed: %s" % (
            self.ip, homed.upper() or "no"), "s", "muted")
        self.button(d, (6, H - NAV_H - 44, W // 2 - 3, H - NAV_H - 6),
                    "Print a file", ("page", "files"), "accent")
        self.button(d, (W // 2 + 3, H - NAV_H - 44, W - 6, H - NAV_H - 6),
                    "Home", ("gcode", "G28", "Homed"))

    def draw_printing(self, d, st, top):
        ps = st.get("print_stats", {})
        fn = ps.get("filename") or ""
        size = 92
        thumb = self.thumbs.get(fn, size) if fn else None
        box = (6, top, 6 + size, top + size)
        d.rounded_rectangle(box, 8, fill=self.col["panel"])
        if thumb is not None:
            self.img.paste(thumb, (6 + (size - thumb.width) // 2,
                              top + (size - thumb.height) // 2),
                      thumb if thumb.mode == "RGBA" else None)
        x = 6 + size + 10
        name = os.path.splitext(os.path.basename(fn))[0]
        lines = self.wrap(name.replace("_", " "), "s", W - x - 6)[:2]
        for i, line in enumerate(lines):
            self.text(d, (x, top + i * 15), line, "s", "muted")
        p = self.progress(st)
        self.text(d, (x, top + 34), "%d%%" % (p * 100), "xl", "text")
        rem = self.remaining(st)
        info = ps.get("info") or {}
        sub = []
        if rem is not None:
            sub.append(fmt_time(rem) + " left")
        if info.get("current_layer") and info.get("total_layer"):
            sub.append("L %s/%s" % (info["current_layer"],
                                     info["total_layer"]))
        self.text(d, (x, top + 80), "   ".join(sub), "s", "muted")
        bar = (6, top + size + 6, W - 6, top + size + 13)
        d.rounded_rectangle(bar, 4, fill=self.col["panel"])
        if p > 0:
            d.rounded_rectangle((bar[0], bar[1], bar[0] + max(
                8, int((bar[2] - bar[0]) * p)), bar[3]), 4,
                fill=self.col["accent"])
        self.loaded_line(d, st, (8, top + size + 16), W - 16)
        y0, y1 = H - NAV_H - 40, H - NAV_H - 6
        paused = self.state(st) == "paused"
        third = (W - 24) // 3
        self.button(d, (6, y0, 6 + third, y1),
                    "Resume" if paused else "Pause",
                    ("gcode", "RESUME" if paused else "PAUSE", None),
                    "ok" if paused else "panel")
        self.button(d, (12 + third, y0, 12 + 2 * third, y1), "Cancel",
                    ("confirm", "Cancel this print?",
                     ("gcode", "CANCEL_PRINT", "Print cancelled")), "accent")
        e, b = st.get("extruder", {}), st.get("heater_bed", {})
        self.text(d, (W - 10, (y0 + y1) // 2 - 8), "%d/%d" % (
            e.get("temperature") or 0, e.get("target") or 0), "s", "muted",
            "rm")
        self.text(d, (W - 10, (y0 + y1) // 2 + 8), "%d/%d" % (
            b.get("temperature") or 0, b.get("target") or 0), "s", "muted",
            "rm")

    def draw_files(self, d, st):
        files = self.files
        pages = max(1, (len(files) + ROWS - 1) // ROWS)
        self.file_page = min(self.file_page, pages - 1)
        top = HEAD_H + 4
        rh = (H - NAV_H - top - 4) // ROWS
        lw = W - 50
        if not files:
            self.text(d, (W // 2, 110), "No print files", "m", "muted", "mm")
        for i, (path, mtime, _) in enumerate(
                files[self.file_page * ROWS:(self.file_page + 1) * ROWS]):
            y0 = top + i * rh
            rect = (4, y0, lw, y0 + rh - 4)
            d.rounded_rectangle(rect, 6, fill=self.col["panel"])
            th = rh - 10
            thumb = self.thumbs.get(path, th)
            if thumb is not None:
                self.img.paste(thumb, (8 + (th - thumb.width) // 2,
                                       y0 + 3 + (th - thumb.height) // 2),
                               thumb if thumb.mode == "RGBA" else None)
            name = os.path.splitext(os.path.basename(path))[0]
            self.text(d, (14 + th, y0 + 4), name, "s", "text",
                      width=lw - th - 20)
            meta = self.thumbs.metadata(path) or {}
            sub = [ago(mtime)]
            if meta.get("estimated_time"):
                sub.insert(0, fmt_time(meta["estimated_time"]))
            self.text(d, (14 + th, y0 + rh - 9), "  ".join(sub), "s",
                      "muted", "ld")
            self.buttons.append((rect, ("file", path)))
        x0 = lw + 6
        mid = (top + H - NAV_H) // 2
        self.button(d, (x0, top, W - 4, mid - 3), "^",
                    ("files_page", -1), enabled=self.file_page > 0)
        self.button(d, (x0, mid + 3, W - 4, H - NAV_H - 4), "v",
                    ("files_page", 1), enabled=self.file_page < pages - 1)

    def draw_file(self, d, st, path):
        top = HEAD_H + 6
        size = 110
        meta = self.thumbs.metadata(path) or {}
        thumb = self.thumbs.get(path, size)
        d.rounded_rectangle((6, top, 6 + size, top + size), 8,
                            fill=self.col["panel"])
        if thumb is not None:
            self.img.paste(thumb, (6 + (size - thumb.width) // 2,
                                   top + (size - thumb.height) // 2),
                           thumb if thumb.mode == "RGBA" else None)
        x = 6 + size + 10
        name = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        for i, line in enumerate(self.wrap(name, "s", W - x - 6)[:3]):
            self.text(d, (x, top + i * 15), line, "s", "text")
        rows = []
        if meta.get("estimated_time"):
            rows.append("Time  " + fmt_time(meta["estimated_time"]))
        if meta.get("filament_weight_total"):
            rows.append("Filament  %.0f g" % meta["filament_weight_total"])
        if meta.get("filament_type"):
            rows.append("Material  %s" % str(meta["filament_type"]).split(
                ";")[0])
        if meta.get("layer_height"):
            rows.append("Layer  %.2f mm" % meta["layer_height"])
        for i, row in enumerate(rows):
            self.text(d, (x, top + 52 + i * 16), row, "s", "muted")
        y0, y1 = H - NAV_H - 44, H - NAV_H - 6
        self.button(d, (6, y0, 106, y1), "Back", ("sub", None))
        busy = self.printing(st)
        self.button(d, (114, y0, W - 6, y1),
                    "Printing..." if busy else "Print",
                    ("confirm", "Bed clear? Start this print?",
                     ("start", path)), "accent", enabled=not busy)

    def draw_control(self, d, st):
        busy = self.printing(st)
        mats = self.materials()
        if self.material not in mats:
            self.material = list(mats)[0]
        n, b = mats[self.material]
        m = self.material
        items = [
            ("Home", ("gcode", "G28", "Homed"), "panel", not busy),
            ("Move", ("sub", "move"), "panel", not busy),
            ("Clean nozzle", ("confirm", "Clean the nozzle on the strip?",
                              ("gcode", "NOZZLE_CLEAR\nG28 Z",
                               "Nozzle cleaned")), "panel", not busy),
            ("%s %d/%d" % (m, n, b), ("material",), "warn", True),
            ("Preheat", ("gcode", "M104 S%d\nM140 S%d" % (n, b),
                         "Heating for %s" % m), "panel", True),
            ("Cool down", ("gcode", "TURN_OFF_HEATERS", "Heaters off"),
             "panel", True),
            ("Filament", ("page", "filament"), "panel", True),
            ("Unload", ("confirm", "Unload the filament?",
                        ("gcode", "CFS_UNLOAD_ACTIVE", "Unloaded")
                        if self.cfs_ok(st) else
                        ("gcode", "UNLOAD_FILAMENT TEMP=%d" % n,
                         "Pull the filament out now")), "panel",
             not busy and (not self.cfs_ok(st)
                           or bool(st.get("k2_cfs", {}).get("active_slot")))),
            ("Motors off", ("gcode", "M84", "Motors off"), "panel",
             not busy),
        ]
        self.grid(d, items, 3, 3, HEAD_H + 6, H - NAV_H - 6)

    def draw_move(self, d, st):
        top, bottom = HEAD_H + 6, H - NAV_H - 6
        homed = st.get("toolhead", {}).get("homed_axes") or ""
        pos = st.get("toolhead", {}).get("position") or [0, 0, 0]
        s = self.step
        def jog(axis, sign):
            return ("jog", axis, sign)
        cw, gap = 58, 6
        rh = (bottom - top - 2 * gap) // 3
        def cell(c, r, label, action, enabled=True, kind="panel"):
            x0 = gap + c * (cw + gap)
            y0 = top + r * (rh + gap)
            self.button(d, (x0, y0, x0 + cw, y0 + rh), label, action, kind,
                        "m", enabled)
        okxy = "x" in homed and "y" in homed
        cell(1, 0, "Y+", jog("Y", 1), okxy)
        cell(0, 1, "X-", jog("X", -1), okxy)
        cell(2, 1, "X+", jog("X", 1), okxy)
        cell(1, 2, "Y-", jog("Y", -1), okxy)
        cell(1, 1, "Home", ("gcode", "G28", "Homed"))
        cell(3, 0, "Z+", jog("Z", 1), "z" in homed)
        cell(3, 2, "Z-", jog("Z", -1), "z" in homed)
        cell(3, 1, "%g mm" % s, ("step",), True, "warn")
        x = gap + 4 * (cw + gap)
        self.text(d, (x, top + 4), "X %.1f" % pos[0], "s", "muted")
        self.text(d, (x, top + 20), "Y %.1f" % pos[1], "s", "muted")
        self.text(d, (x, top + 36), "Z %.2f" % pos[2], "s", "muted")
        self.button(d, (x, bottom - 40, W - gap, bottom), "Back",
                    ("sub", None))

    def draw_machine(self, d, st, err):
        top = HEAD_H + 6
        ver = hs.read_text(os.path.join(OKL_DIR, "VERSION")) or "?"
        wh = st.get("webhooks", {})
        rows = [("IP", self.ip), ("Firmware", "OpenKlipper " + ver),
                ("Klipper", wh.get("state") or "?"),
                ("Motors", "ready" if st.get("k2_motor_bus", {}).get(
                    "motors_ready") else "not ready")]
        for i, (k, v) in enumerate(rows):
            self.text(d, (8, top + i * 18), k, "s", "muted")
            self.text(d, (84, top + i * 18), v, "s", "text", width=W - 92)
        bri = self.prefs.get("brightness", 100)
        slp = int(self.prefs.get("sleep_after",
                                 self.cfg.num("screen", "sleep_after")))
        slabel = dict(SLEEP_CHOICES).get(slp, "%ds" % slp)
        items = [
            ("Bright %d%%" % bri, ("brightness",)),
            ("Sleep %s" % slabel, ("sleep",)),
            ("Motor init", ("gcode", "MOTOR_INIT", "Motors initialised")),
            ("Restart", ("confirm", "Restart Klipper (firmware)?",
                         ("gcode_post", "/printer/firmware_restart"))),
            ("Back to stock", ("confirm",
                               "Switch to Creality's firmware?",
                               ("stock",)), "accent",
             not self.printing(st)),
            ("Auto PA %s" % ("on" if pa_auto_on(st) else "off"),
             ("gcode", "PA_CAL_AUTO ENABLE=%d" % (0 if pa_auto_on(st) else 1),
              "Automatic pressure advance %s" % (
                  "off" if pa_auto_on(st) else "on")),
             "ok" if pa_auto_on(st) else "panel"),
        ]
        self.grid(d, items, 3, 2, top + 78, H - NAV_H - 6)

    # --- filament (CFS slots / spool holder) -------------------------------
    def cfs_ok(self, st):
        c = st.get("k2_cfs", {})
        return bool(c.get("fitted")) and bool(c.get("online"))

    def swatch(self, d, rect, hexval, radius=4):
        rgb = colour_rgb(hexval)
        if rgb is None:
            d.rounded_rectangle(rect, radius, fill=self.col["background"],
                                outline=self.col["panel_edge"])
            return
        d.rounded_rectangle(rect, radius, fill=rgb,
                            outline=self.col["panel_edge"])

    def loaded_line(self, d, st, xy, width):
        """'In the nozzle: B  Hyper PETG  PA 0.066' with its colour."""
        if not self.cfs_ok(st):
            return
        c = st.get("k2_cfs", {})
        slot = c.get("active_slot") or c.get("feed_slot")
        x, y = xy
        if not slot:
            self.text(d, (x, y), "Nozzle empty (no CFS slot loaded)", "s",
                      "muted", width=width)
            return
        rec = st.get("hark_cfs_slots", {}).get("slots", {}).get(slot, {})
        self.swatch(d, (x, y + 1, x + 14, y + 14), rec.get("color"), 3)
        pa = st.get("extruder", {}).get("pressure_advance")
        label = "%s  %s" % (slot, rec.get("name") or rec.get("type") or "")
        if pa:
            label += "   PA %.3f" % pa
        self.text(d, (x + 20, y), label, "s", "text", width=width - 20)

    def draw_filament(self, d, st):
        top = HEAD_H + 6
        busy = self.printing(st)
        if not self.cfs_ok(st):
            self.draw_spool(d, st, top)
            return
        c = st.get("k2_cfs", {})
        slots = st.get("hark_cfs_slots", {}).get("slots", {})
        active = c.get("active_slot") or c.get("feed_slot")
        gap = 6
        tw = (W - gap * 5) // 4
        th = 96
        for i, slot in enumerate(CFS_SLOTS):
            rec = slots.get(slot, {})
            x0 = gap + i * (tw + gap)
            rect = (x0, top, x0 + tw, top + th)
            on = slot == active
            fill = self.col["panel"]
            if self.flash and self.flash[0] == rect \
                    and time.time() - self.flash[1] < .25:
                fill = hs.mix(fill, (255, 255, 255), .35)
            d.rounded_rectangle(rect, 8, fill=fill,
                                outline=self.col["accent"] if on
                                else self.col["panel_edge"],
                                width=2 if on else 1)
            loaded = rec.get("loaded")
            self.swatch(d, (x0 + 6, top + 6, x0 + tw - 6, top + 24),
                        rec.get("color") if loaded else "")
            self.text(d, (x0 + 8, top + 30), slot, "l", "text")
            if on:
                self.text(d, (x0 + tw - 7, top + 33), "IN", "s", "accent",
                          "ra")
            if loaded:
                self.text(d, (x0 + 8, top + 54), rec.get("type") or "?",
                          "s", "text", width=tw - 12)
                self.text(d, (x0 + 8, top + 68), rec.get("name") or "",
                          "s", "muted", width=tw - 12)
                pa = rec.get("pressure_advance") or 0
                self.text(d, (x0 + 8, top + 82),
                          ("PA %.3f" % pa) if pa else "", "s", "muted")
            else:
                self.text(d, (x0 + 8, top + 58), "Empty", "s", "muted")
            self.buttons.append((rect, ("sub", "slot:" + slot)))
        last = st.get("gcode_macro _PA_STATIC_SAVE", {}).get("last") or {}
        y = top + th + 6
        if last.get("slot"):
            if last.get("saved"):
                line = "Last PA test: slot %s %.4f saved" % (
                    last["slot"], last.get("pa") or 0)
                colour = "ok"
            else:
                line = "Last PA test: slot %s not saved (%s)" % (
                    last["slot"], last.get("reason") or "?")
                colour = "warn"
            self.text(d, (8, y), line, "s", colour, width=W - 16)
        if c.get("change_pending"):
            self.text(d, (8, y), "Colour change %s failed: Resume retries"
                      % c["change_pending"], "s", "error", width=W - 16)
        y0, y1 = H - NAV_H - 44, H - NAV_H - 6
        half = (W - 18) // 2
        auto = pa_auto_on(st)
        self.button(d, (6, y0, 6 + half, y1),
                    "Auto PA %s" % ("on" if auto else "off"),
                    ("gcode", "PA_CAL_AUTO ENABLE=%d" % (0 if auto else 1),
                     "Automatic pressure advance %s" % (
                         "off" if auto else "on")),
                    "ok" if auto else "panel")
        self.button(d, (12 + half, y0, W - 6, y1),
                    "Unload %s" % active if active else "Unload",
                    ("confirm", "Cut and unload slot %s?" % active,
                     ("gcode", "CFS_UNLOAD_ACTIVE",
                      "Slot %s unloaded" % active)),
                    "warn", enabled=bool(active) and not busy)

    def draw_spool(self, d, st, top):
        """No CFS answering: the spool holder at the back."""
        busy = self.printing(st)
        mats = self.materials()
        if self.material not in mats:
            self.material = list(mats)[0]
        n, b = mats[self.material]
        has = st.get("filament_switch_sensor filament_sensor", {}).get(
            "filament_detected")
        self.text(d, (8, top), "Spool holder (no CFS answering)", "m",
                  "text", width=W - 16)
        self.text(d, (8, top + 20), "Filament in the toolhead: %s" % (
            "yes" if has else "no" if has is not None else "?"), "s",
            "muted")
        items = [
            ("%s %d/%d" % (self.material, n, b), ("material",), "warn"),
            ("Load", ("gcode", "LOAD_FILAMENT TEMP=%d" % n, "Loaded"),
             "panel", not busy),
            ("Unload", ("gcode", "UNLOAD_FILAMENT TEMP=%d" % n,
                        "Pull the filament out now"), "panel", not busy),
            ("Measure PA", ("confirm",
                            "Measure pressure advance at the chute "
                            "(about 3 min)?",
                            ("gcode", "PA_CAL_STATIC SLOT=E SAVE=1",
                             "Pressure advance measured")),
             "panel", not busy and bool(has)),
        ]
        self.grid(d, items, 2, 2, top + 44, H - NAV_H - 6)

    def draw_slot(self, d, st, slot):
        top = HEAD_H + 4
        busy = self.printing(st)
        c = st.get("k2_cfs", {})
        rec = st.get("hark_cfs_slots", {}).get("slots", {}).get(slot, {})
        active = (c.get("active_slot") or c.get("feed_slot")) == slot
        loaded = rec.get("loaded")
        self.swatch(d, (8, top + 4, 30, top + 26),
                    rec.get("color") if loaded else "")
        title = "Slot %s  %s" % (slot, rec.get("name") if loaded
                                 else "(empty)")
        self.text(d, (38, top + 4), title, "m", "text", width=W - 46)
        if loaded:
            bits = [rec.get("type") or "?"]
            if rec.get("nozzle_temp"):
                bits.append("%d/%d C" % (rec["nozzle_temp"],
                                         rec.get("bed_temp") or 0))
            if rec.get("pressure_advance"):
                bits.append("PA %.4f" % rec["pressure_advance"])
            bits.append(colour_name(rec.get("color")))
            self.text(d, (8, top + 32), "  ".join(b for b in bits if b),
                      "s", "muted", width=W - 16)
        if active:
            self.text(d, (8, top + 48), "In the nozzle now", "s", "accent")
        temp = rec.get("nozzle_temp") or 220
        if active:
            load = ("Unload", ("confirm", "Cut and unload slot %s?" % slot,
                               ("gcode", "CFS_UNLOAD_ACTIVE",
                                "Slot %s unloaded" % slot)), "warn",
                    not busy)
            measure = "PA_CAL_STATIC SLOT=%s SAVE=1" % slot
        else:
            load = ("Load", ("confirm",
                             "Load slot %s? Heats to %d C at the chute." % (
                                 slot, temp),
                             ("gcode", "CFS_LOAD_SLOT SLOT=%s" % slot,
                              "Slot %s loaded" % slot)), "accent",
                    not busy and bool(loaded))
            measure = ("CFS_LOAD_SLOT SLOT=%s PA=0 COOL=0\n"
                       "PA_CAL_STATIC SLOT=%s SAVE=1" % (slot, slot))
        edit_ok = not (busy and active)
        items = [
            load,
            ("Measure PA", ("confirm",
                            "Measure slot %s's pressure advance at the chute "
                            "(about 3 min%s)?" % (
                                slot, "" if active else ", loads it first"),
                            ("gcode", measure,
                             "Slot %s pressure advance measured" % slot)),
             "panel", not busy and bool(loaded)),
            ("Filament", ("sub", "types:" + slot), "panel", edit_ok),
            ("Colour", ("sub", "colour:" + slot), "panel", edit_ok),
            ("Mark empty", ("confirm", "Mark slot %s as empty?" % slot,
                            ("gcode_sub", "CFS_SLOT_CLEAR SLOT=%s" % slot,
                             "Slot %s marked empty" % slot, "slot:" + slot)),
             "panel", edit_ok and bool(loaded) and not active),
            ("Back", ("sub", None)),
        ]
        self.grid(d, items, 3, 2, top + 66, H - NAV_H - 6)

    def draw_types(self, d, slot):
        top = HEAD_H + 4
        self.text(d, (8, top + 2), "Slot %s: filament type" % slot, "m",
                  "text")
        types = filament_types(self.library)
        if not types:
            self.text(d, (8, top + 30), "Loading the filament list...",
                      "s", "muted")
        items = [(t, ("sub", "prof:%s:%s:0" % (slot, t))) for t in types[:8]]
        items += [None] * (8 - len(items))
        items.append(("Back", ("sub", "slot:" + slot)))
        self.grid(d, items, 3, 3, top + 26, H - NAV_H - 6)

    def draw_profiles(self, d, st, slot, mtype, page):
        top = HEAD_H + 4
        rec = st.get("hark_cfs_slots", {}).get("slots", {}).get(slot, {})
        found = filaments_of_type(self.library, mtype)
        per = PROFILE_ROWS * 2
        pages = max(1, (len(found) + per - 1) // per)
        page = min(page, pages - 1)
        self.text(d, (8, top + 2), "Slot %s: %s profile" % (slot, mtype),
                  "m", "text", width=W - 70)
        if pages > 1:
            self.text(d, (W - 8, top + 4), "%d/%d" % (page + 1, pages), "s",
                      "muted", "ra")
        colour = (rec.get("color") or "").strip().lstrip("#")
        items = []
        for f in found[page * per:(page + 1) * per]:
            line = "CFS_SLOT_SET SLOT=%s FILAMENT=%s" % (slot, f.get("id"))
            if colour:
                line += " COLOR=%s" % colour.upper()
            items.append((f.get("name") or f.get("id"),
                          ("gcode_sub", line,
                           "Slot %s: %s" % (slot, f.get("name")),
                           "slot:" + slot)))
        items += [None] * (per - len(items))
        bottom = H - NAV_H - 44
        self.grid(d, items, 2, PROFILE_ROWS, top + 24, bottom, gap=4)
        y0, y1 = bottom + 4, H - NAV_H - 6
        third = (W - 24) // 3
        self.button(d, (6, y0, 6 + third, y1), "<",
                    ("sub", "prof:%s:%s:%d" % (slot, mtype, page - 1)),
                    "panel", enabled=page > 0)
        self.button(d, (12 + third, y0, 12 + 2 * third, y1), "Back",
                    ("sub", "types:" + slot))
        self.button(d, (18 + 2 * third, y0, W - 6, y1), ">",
                    ("sub", "prof:%s:%s:%d" % (slot, mtype, page + 1)),
                    "panel", enabled=page + 1 < pages)

    def draw_colours(self, d, st, slot):
        top = HEAD_H + 4
        rec = st.get("hark_cfs_slots", {}).get("slots", {}).get(slot, {})
        cur = (rec.get("color") or "").strip().lstrip("#").upper()
        self.text(d, (8, top + 2), "Slot %s: colour (%s)" % (
            slot, colour_name(cur) or "none"), "m", "text", width=W - 16)
        cols, rows, gap = 6, 3, 6
        y_top, y_bot = top + 26, H - NAV_H - 6
        bw = (W - gap * (cols + 1)) // cols
        bh = (y_bot - y_top - gap * (rows - 1)) // rows
        cells = list(COLOURS) + [("Back", None)]
        for i, (name, hexval) in enumerate(cells):
            cx, cy = i % cols, i // cols
            x0 = gap + cx * (bw + gap)
            y0 = y_top + cy * (bh + gap)
            rect = (x0, y0, x0 + bw, y0 + bh)
            if hexval is None:
                self.button(d, rect, "Back", ("sub", "slot:" + slot))
                continue
            d.rounded_rectangle(rect, 6, fill=colour_rgb(hexval),
                                outline=self.col["accent"] if hexval == cur
                                else self.col["panel_edge"],
                                width=3 if hexval == cur else 1)
            self.buttons.append((rect, (
                "gcode_sub", "CFS_SLOT_SET SLOT=%s COLOR=%s" % (slot, hexval),
                "Slot %s: %s" % (slot, name), "slot:" + slot)))

    def draw_confirm(self, img):
        dim = Image.new("RGB", img.size, (0, 0, 0))
        img.paste(Image.blend(img, dim, .55))
        d = ImageDraw.Draw(img)
        box = (24, 50, W - 24, H - 50)
        d.rounded_rectangle(box, 10, fill=self.col["panel"],
                            outline=self.col["panel_edge"])
        q = self.confirm[0]
        for i, line in enumerate(self.wrap(q, "m", box[2] - box[0] - 20)[:3]):
            self.text(d, (W // 2, box[1] + 24 + i * 20), line, "m", "text",
                      "mm")
        self.buttons = []
        y0, y1 = box[3] - 50, box[3] - 12
        self.button(d, (box[0] + 12, y0, W // 2 - 6, y1), "No",
                    ("confirm_no",))
        self.button(d, (W // 2 + 6, y0, box[2] - 12, y1), "Yes",
                    ("confirm_yes",), "accent")

    # --- output ----------------------------------------------------------
    def show(self, img):
        rot = int(self.cfg.num("screen", "rotate")) % 360
        t = {90: Image.ROTATE_270, 270: Image.ROTATE_90,
             180: Image.ROTATE_180}.get(rot)
        if t is not None:
            img = img.transpose(t)
        os.pwrite(self.fb, img.convert("RGBA").tobytes("raw", "BGRA"), 0)

    def set_sleep(self, asleep):
        if asleep != self.asleep:
            subprocess.call(["/usr/bin/screen.sh", "off" if asleep else "on"])
            if not asleep and "brightness" in self.prefs:
                hs.set_brightness(self.prefs["brightness"])
            self.asleep = asleep
            self.dirty = True

    # --- input -----------------------------------------------------------
    def map_touch(self, x, y):
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

    def tap(self, x, y):
        x, y = self.map_touch(x, y)
        # Nearest button within a few pixels (fingers on a small panel)
        best = None
        for rect, action in self.buttons:
            x0, y0, x1, y1 = rect
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            dist = dx * dx + dy * dy
            if dist <= 64 and (best is None or dist < best[0]):
                best = (dist, rect, action)
        if best is None:
            return
        _, rect, action = best
        self.flash = (rect, time.time())
        self.dirty = True
        self.act(action)

    def act(self, action):
        kind = action[0]
        if kind == "page":
            self.page, self.sub, self.confirm = action[1], None, None
            if action[1] == "files":
                self.file_page = 0
        elif kind == "sub":
            self.sub = action[1]
        elif kind == "file":
            self.sub = action[1]
        elif kind == "files_page":
            self.file_page = max(0, self.file_page + action[1])
        elif kind == "confirm":
            self.confirm = (action[1], action[2])
        elif kind == "confirm_no":
            self.confirm = None
        elif kind == "confirm_yes":
            then, self.confirm = self.confirm[1], None
            self.act(then)
        elif kind == "gcode":
            self.say("Running: " + action[1].split("\n")[0], "muted", 2.)
            self.run(action[1], action[2])
        elif kind == "gcode_sub":
            # a quick setting (slot profile, colour): run it and step back
            self.sub = action[3]
            self.say("Saving...", "muted", 2.)
            self.run(action[1], action[2], timeout=30)
        elif kind == "gcode_post":
            mr = Mr(self.cfg.get("screen", "moonraker"))
            mr.post(action[1])
            self.say("Restarting firmware...", "warn", 6.)
        elif kind == "jog":
            axis, sign = action[1], action[2]
            feed = 600 if axis == "Z" else 6000
            self.run("G91\nG1 %s%.2f F%d\nG90" % (axis, sign * self.step,
                                                  feed))
        elif kind == "step":
            steps = (0.1, 1., 10., 50.)
            self.step = steps[(steps.index(self.step) + 1) % len(steps)] \
                if self.step in steps else 10.
        elif kind == "material":
            names = list(self.materials())
            i = names.index(self.material) if self.material in names else -1
            self.material = names[(i + 1) % len(names)]
            self.save_pref("material", self.material)
        elif kind == "brightness":
            cur = self.prefs.get("brightness", 100)
            nxt = BRIGHTNESS[(BRIGHTNESS.index(cur) + 1) % len(BRIGHTNESS)] \
                if cur in BRIGHTNESS else 100
            hs.set_brightness(nxt)
            self.save_pref("brightness", nxt)
        elif kind == "sleep":
            cur = int(self.prefs.get("sleep_after",
                                     self.cfg.num("screen", "sleep_after")))
            vals = [v for v, _ in SLEEP_CHOICES]
            nxt = vals[(vals.index(cur) + 1) % len(vals)] \
                if cur in vals else 600
            self.save_pref("sleep_after", nxt)
        elif kind == "start":
            path = action[1]
            def go():
                try:
                    Mr(self.cfg.get("screen", "moonraker")).start_print(path)
                    self.page, self.sub = "status", None
                    self.say("Print started", "ok")
                except Exception as e:
                    self.say(str(e), "error", 8.)
            threading.Thread(target=go, daemon=True).start()
        elif kind == "stock":
            self.say("Switching to Creality's firmware...", "warn", 30.)
            subprocess.Popen(
                ["sh", "-c", "sleep 1; sh %s/switch-to-stock.sh; "
                 "/etc/init.d/app restart" % OKL_DIR],
                start_new_session=True)

    def save_pref(self, key, value):
        self.prefs[key] = value
        try:
            hs.save_prefs(self.prefs)
        except Exception as e:
            print("prefs: %s" % e, flush=True)


def main():
    subprocess.call(["killall", "boot-play"], stderr=subprocess.DEVNULL)
    cfg = hs.Config(hs.CFG_PATH)
    screen = Screen(cfg)
    screen.ip = hs.local_ip()
    data = Data(screen)
    data.start()
    screen.thumbs.start()
    touch = hs.Touch()
    touch.start()
    screen.set_sleep(False)
    if "brightness" in screen.prefs:
        hs.set_brightness(screen.prefs["brightness"])
    last_draw, ip_at = 0., time.time()
    while True:
        now = time.time()
        if cfg.changed():
            cfg.reload()
            screen.apply_config()
        if now - ip_at > 30:
            ip, ip_at = hs.local_ip(), now
            if ip != screen.ip:
                screen.ip, screen.dirty = ip, True
        st, err = data.st, data.err
        printing = screen.printing(st)
        taps = touch.pop()
        if printing:
            touch.last_activity = now
        sleep_after = float(screen.prefs.get(
            "sleep_after", cfg.num("screen", "sleep_after")))
        if screen.asleep:
            if taps or printing or err:
                screen.set_sleep(False)
                touch.last_activity = now
            taps = []
        elif (sleep_after > 0 and not printing and not screen.confirm
              and now - touch.last_activity > sleep_after):
            screen.set_sleep(True)
        for x, y in taps:
            try:
                screen.tap(x, y)
            except Exception:
                import traceback
                traceback.print_exc()
        if screen.notice and now > screen.notice[2]:
            screen.notice, screen.dirty = None, True
        if screen.flash and now - screen.flash[1] > .25:
            screen.flash, screen.dirty = None, True
        if not screen.asleep and (screen.dirty or now - last_draw > 5):
            screen.dirty = False
            try:
                screen.show(screen.frame(st, err))
            except Exception:
                import traceback
                traceback.print_exc()
            last_draw = now
        time.sleep(0.05)


if __name__ == "__main__":
    main()
