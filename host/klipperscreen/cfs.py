# KlipperScreen panel for the Creality CFS on K2 OpenKlipper (K2 and Hi).
#
# Shows the four CFS slots (colour, filament, what is feeding the nozzle),
# the box temperature and humidity, and offers Load / Unload when the
# firmware on this printer can move filament (the CFS_LOAD_SLOT and
# CFS_UNLOAD_ACTIVE macros exist and [k2_cfs] allow_actuation is on).
#
# Install: link or copy this file to ~/KlipperScreen/panels/cfs.py and add to
# KlipperScreen.conf:
#   [menu __main cfs]
#   name: CFS
#   icon: filament
#   panel: cfs
#
# KlipperScreen does not subscribe to k2_cfs / hark_cfs_slots, so the panel
# asks Moonraker for them every POLL_SECONDS while it is on screen.
import logging
from math import pi

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from ks_includes.screen_panel import ScreenPanel

SLOTS = "ABCD"
POLL_SECONDS = 2
TRUE_WORDS = ("true", "1", "yes", "on")


def hex_to_rgb(text):
    text = (text or "").strip().lstrip("#")
    if len(text) < 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) / 255. for i in (0, 2, 4))
    except ValueError:
        return None


class Swatch(Gtk.DrawingArea):
    """Rounded block in the filament colour; an outline when the slot is empty."""

    def __init__(self, size):
        super().__init__(width_request=size, height_request=size,
                         hexpand=True, vexpand=True)
        self.rgb = None
        self.connect("draw", self.on_draw)

    def set_rgb(self, rgb):
        if rgb != self.rgb:
            self.rgb = rgb
            self.queue_draw()

    def on_draw(self, da, ctx):
        w, h = da.get_allocated_width(), da.get_allocated_height()
        pad = 4
        x, y, w, h = pad, pad, w - 2 * pad, h - 2 * pad
        r = min(w, h) * .14
        ctx.new_sub_path()
        ctx.arc(x + w - r, y + r, r, -pi / 2, 0)
        ctx.arc(x + w - r, y + h - r, r, 0, pi / 2)
        ctx.arc(x + r, y + h - r, r, pi / 2, pi)
        ctx.arc(x + r, y + r, r, pi, 3 * pi / 2)
        ctx.close_path()
        if self.rgb is None:
            ctx.set_source_rgba(1, 1, 1, .25)
            ctx.set_line_width(2)
            ctx.set_dash([8, 6])
            ctx.stroke()
            return
        ctx.set_source_rgb(*self.rgb)
        ctx.fill_preserve()
        # thin edge so black and very dark filaments still show on the theme
        ctx.set_source_rgba(1, 1, 1, .35)
        ctx.set_line_width(1.5)
        ctx.stroke()


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        title = title or "CFS"
        super().__init__(screen, title)
        self.cfs = {}
        self.slots = {}
        self.tool_map = {}
        self.selected = None
        self.poll = None
        self.asked_details = False
        self.cards = {}

        self.header = Gtk.Label(hexpand=True, halign=Gtk.Align.START,
                                ellipsize=Pango.EllipsizeMode.END)
        self.header.set_margin_start(10)
        self.header.set_margin_top(6)
        self.header.set_margin_bottom(6)

        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                        hexpand=True, vexpand=True)
        size = self._gtk.img_scale * 2
        for i, slot in enumerate(SLOTS):
            card = self._make_card(slot, size)
            grid.attach(card["button"], i, 0, 1, 1)
            self.cards[slot] = card

        self.note = Gtk.Label(hexpand=True, wrap=True, halign=Gtk.Align.START)
        self.note.set_margin_start(10)
        self.note.set_margin_top(4)
        self.note.set_margin_bottom(4)

        self.btn_load = self._gtk.Button("load", _("Load"), "color3")
        self.btn_load.connect("clicked", self.load_selected)
        self.btn_unload = self._gtk.Button("retract", _("Unload"), "color2")
        self.btn_unload.connect("clicked", self.unload_active)
        self.btn_tags = self._gtk.Button("refresh", "Read tags", "color4")
        self.btn_tags.connect("clicked", self.read_tags)
        actions = Gtk.Grid(column_homogeneous=True, hexpand=True)
        actions.attach(self.btn_load, 0, 0, 1, 1)
        actions.attach(self.btn_unload, 1, 0, 1, 1)
        actions.attach(self.btn_tags, 2, 0, 1, 1)
        actions.set_vexpand(False)

        self.content.add(self.header)
        self.content.add(grid)
        self.content.add(self.note)
        self.content.add(actions)
        self._render()

    # --- widgets -------------------------------------------------------
    def _make_card(self, slot, size):
        button = Gtk.Button(hexpand=True, vexpand=True)
        button.get_style_context().add_class("frame-item")
        button.connect("clicked", self.select_slot, slot)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Label()
        swatch = Swatch(size)
        name = Gtk.Label(ellipsize=Pango.EllipsizeMode.END)
        detail = Gtk.Label(ellipsize=Pango.EllipsizeMode.END)
        state = Gtk.Label(ellipsize=Pango.EllipsizeMode.END)
        for w in (head, swatch, name, detail, state):
            box.add(w)
        button.add(box)
        return {"button": button, "head": head, "swatch": swatch,
                "name": name, "detail": detail, "state": state}

    # --- lifecycle -----------------------------------------------------
    def activate(self):
        self.refresh()
        if self.poll is None:
            self.poll = GLib.timeout_add_seconds(POLL_SECONDS, self.refresh)

    def deactivate(self):
        if self.poll is not None:
            GLib.source_remove(self.poll)
            self.poll = None

    def refresh(self, *args):
        try:
            self._screen._ws.api.query_objects(
                {"k2_cfs": None, "hark_cfs_slots": ["slots", "tool_map"]},
                self._on_data)
        except Exception:
            logging.exception("cfs panel: query failed")
        return True

    def _on_data(self, data, method=None, params=None):
        if not isinstance(data, dict) or "result" not in data:
            self.cfs, self.slots = {}, {}
            self._render(error=True)
            return
        status = data["result"].get("status", {})
        self.cfs = status.get("k2_cfs") or {}
        slots = status.get("hark_cfs_slots") or {}
        self.slots = slots.get("slots") or {}
        self.tool_map = slots.get("tool_map") or {}
        # firmware / per-slot presence / remaining come from one CFS_STATUS
        if (self.cfs.get("online") and "slots_loaded" not in self.cfs
                and not self.asked_details and not self._busy_printing()):
            self.asked_details = True
            self._screen._ws.api.gcode_script("CFS_STATUS")
        self._render()

    # --- state helpers -------------------------------------------------
    def _busy_printing(self):
        return self._printer.state in ("printing", "paused")

    def _can_move_filament(self):
        cfg = self._printer.get_config_section("k2_cfs") or {}
        allowed = str(cfg.get("allow_actuation", "false")).strip().lower()
        sections = self._printer.get_config_section_list("gcode_macro ")
        names = {s[12:].strip().upper() for s in sections}
        return (allowed in TRUE_WORDS and "CFS_LOAD_SLOT" in names
                and "CFS_UNLOAD_ACTIVE" in names)

    def _feeding_slot(self):
        return self.cfs.get("active_slot") or self.cfs.get("feed_slot")

    def _slot_present(self, slot):
        loaded = self.cfs.get("slots_loaded")
        i = SLOTS.index(slot)
        if isinstance(loaded, list) and i < len(loaded):
            return bool(loaded[i])
        return bool((self.slots.get(slot) or {}).get("loaded"))

    # --- drawing -------------------------------------------------------
    def _header_text(self, error):
        if error:
            return "CFS: no answer from the printer"
        cfs = self.cfs
        if not cfs:
            return "No CFS support in this printer's firmware"
        if not cfs.get("fitted", True):
            return "No CFS found: printing from the external spool"
        if cfs.get("online") is False:
            return "CFS is not answering: check its cable and power"
        parts = ["CFS"]
        if cfs.get("temperature") is not None:
            parts.append("%d °C" % cfs["temperature"])
        if cfs.get("humidity") is not None:
            parts.append("%d %% humidity" % cfs["humidity"])
        mode = cfs.get("box_state_name") or cfs.get("box_mode")
        if mode:
            parts.append(str(mode))
        if cfs.get("buffer"):
            parts.append("buffer %s" % cfs["buffer"])
        if cfs.get("firmware"):
            parts.append("firmware %s" % cfs["firmware"])
        return "  ·  ".join(parts)

    def _render(self, error=False):
        self.header.set_markup(
            "<b>%s</b>" % GLib.markup_escape_text(self._header_text(error)))
        feeding = self._feeding_slot()
        tools = {v: k for k, v in self.tool_map.items()}
        remain = self.cfs.get("remain")
        for slot, card in self.cards.items():
            info = self.slots.get(slot) or {}
            present = self._slot_present(slot)
            tool = tools.get(slot, "")
            card["head"].set_markup(
                "<big><b>%s</b></big>%s" % (
                    slot, "  <small>%s</small>" % tool if tool else ""))
            known = bool(info.get("type") or info.get("name"))
            card["swatch"].set_rgb(
                hex_to_rgb(info.get("color")) if present and known else None)
            if present and known:
                card["name"].set_markup("<b>%s</b>" % GLib.markup_escape_text(
                    info.get("name") or info.get("type") or ""))
                bits = []
                if info.get("type") and info.get("type") != info.get("name"):
                    bits.append(str(info["type"]))
                if info.get("nozzle_temp"):
                    bits.append("%d °C" % info["nozzle_temp"])
                i = SLOTS.index(slot)
                if (isinstance(remain, list) and i < len(remain)
                        and 0 <= remain[i] <= 100):
                    bits.append("%d %% left" % remain[i])
                card["detail"].set_text("  ·  ".join(bits))
            elif present:
                card["name"].set_markup("<b>Unknown filament</b>")
                card["detail"].set_text("")
            else:
                card["name"].set_text("Empty")
                card["detail"].set_text("")
            if slot == feeding:
                card["state"].set_markup("<b>In the nozzle</b>")
            elif present:
                card["state"].set_text("Ready")
            else:
                card["state"].set_text("")
            ctx = card["button"].get_style_context()
            if slot == self.selected:
                ctx.add_class("button_active")
            else:
                ctx.remove_class("button_active")

        can_move = self._can_move_filament() and bool(self.cfs.get("online"))
        idle = not self._busy_printing()
        sel = self.selected
        self.btn_load.set_sensitive(
            can_move and idle and sel is not None
            and self._slot_present(sel) and sel != feeding)
        self.btn_unload.set_sensitive(can_move and idle and bool(feeding))
        self.btn_tags.set_sensitive(bool(self.cfs.get("online")) and idle)
        if not self.cfs or error:
            note = ""
        elif not self._can_move_filament():
            note = ("Load and unload are not switched on for this printer "
                    "yet: the CFS is read-only here.")
        elif not idle:
            note = "Load and unload are for when nothing is printing."
        elif self.cfs.get("last_change_error"):
            note = str(self.cfs["last_change_error"])
        elif sel is None:
            note = "Tap a slot to choose it."
        else:
            note = ""
        self.note.set_text(note)
        self.note.set_visible(bool(note))

    # --- actions -------------------------------------------------------
    def select_slot(self, widget, slot):
        self.selected = None if self.selected == slot else slot
        self._render()

    def load_selected(self, widget):
        slot = self.selected
        if slot is None:
            return
        info = self.slots.get(slot) or {}
        what = info.get("name") or info.get("type") or "filament"
        self._screen._confirm_send_action(
            widget,
            "Load slot %s (%s)?\n\nThe printer homes if needed, heats, "
            "and purges at the chute." % (slot, what),
            "printer.gcode.script", {"script": "CFS_LOAD_SLOT SLOT=%s" % slot})

    def unload_active(self, widget):
        slot = self._feeding_slot()
        self._screen._confirm_send_action(
            widget,
            "Unload slot %s?\n\nThe printer heats, cuts the filament and "
            "the CFS winds it back." % (slot or "?"),
            "printer.gcode.script", {"script": "CFS_UNLOAD_ACTIVE"})

    def read_tags(self, widget):
        self.asked_details = True
        self._screen._send_action(
            widget, "printer.gcode.script",
            {"script": "CFS_RFID_READ\nCFS_STATUS"})
