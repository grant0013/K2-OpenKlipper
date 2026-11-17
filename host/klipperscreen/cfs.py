# KlipperScreen panel for the Creality CFS on Hark Tech OpenKlipper (K2 and Hi).
#
# Shows the four CFS slots (colour, filament, what is feeding the nozzle),
# the box temperature and humidity, and offers Load / Unload when the
# firmware on this printer can move filament (the CFS_LOAD_SLOT and
# CFS_UNLOAD_ACTIVE macros exist and [k2_cfs] allow_actuation is on).
#
# Edit sets what a slot holds: material type, filament profile (Creality's
# list plus the custom ones saved with CFS_FILAMENT_SAVE), spool colour, and
# the slot's own nozzle / bed temperature, pressure advance and flow. It saves
# with CFS_SLOT_SET, the same record Mainsail's CFS_FILAMENTS dialog and
# OrcaSlicer's filament sync use.
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
# Same order and colours as klippy/extras/hark_cfs_slots.py
TYPE_ORDER = ("PLA", "PETG", "ABS", "ASA", "TPU", "PLA-CF", "PETG-CF")
COLOURS = (
    ("Black", "000000"), ("White", "FFFFFF"), ("Grey", "808080"),
    ("Silver", "C0C0C0"), ("Red", "E53935"), ("Orange", "FB8C00"),
    ("Yellow", "FDD835"), ("Gold", "FFC107"), ("Green", "43A047"),
    ("Lime", "C0CA33"), ("Cyan", "26EEEE"), ("Blue", "1E88E5"),
    ("Navy", "1A237E"), ("Purple", "8E24AA"), ("Pink", "EC407A"),
    ("Brown", "6D4C41"), ("Beige", "D7CCC8"),
)
# The slot settings the editor can nudge:
# (record key, CFS_SLOT_SET parameter, label, step, low, high, format)
ADJUST = (
    ("nozzle_temp", "NOZZLE", "Nozzle", 5, 170, 320, "%d °C"),
    ("bed_temp", "BED", "Bed", 5, 0, 120, "%d °C"),
    ("pressure_advance", "PA", "Pressure advance", .002, 0., .3, "%.3f"),
    ("flow_ratio", "FLOW", "Flow", .01, .5, 1.5, "%.2f"),
)
PROFILE_KEYS = ("nozzle_temp", "nozzle_first", "min_temp", "max_temp",
                "bed_temp", "pressure_advance", "flow_ratio")


def hex_to_rgb(text):
    text = (text or "").strip().lstrip("#")
    if len(text) < 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) / 255. for i in (0, 2, 4))
    except ValueError:
        return None


def colour_name(hexval):
    hexval = (hexval or "").strip().lstrip("#").upper()
    if not hexval:
        return ""
    for name, h in COLOURS:
        if h == hexval:
            return name
    return "#" + hexval


def filament_types(filaments):
    present = sorted({f.get("type") for f in filaments if f.get("type")})
    ordered = [t for t in TYPE_ORDER if t in present]
    return ordered + [t for t in present if t not in ordered]


def filaments_of_type(filaments, mtype):
    """Custom profiles first, then Creality's own, then the generic ones."""
    found = [f for f in filaments if f.get("type") == mtype]
    found.sort(key=lambda f: (f.get("source") == "creality",
                              f.get("brand") != "Creality",
                              f.get("name", "")))
    return found


def profile_summary(fil):
    bits = []
    if fil.get("nozzle_temp"):
        bits.append("%d °C" % fil["nozzle_temp"])
    if fil.get("bed_temp"):
        bits.append("bed %d °C" % fil["bed_temp"])
    if fil.get("pressure_advance"):
        bits.append("PA %.3f" % fil["pressure_advance"])
    return "  ·  ".join(bits)


def draft_from_filament(fil, colour=""):
    """A slot draft holding a filament profile's settings."""
    draft = {"filament": fil.get("id", ""), "name": fil.get("name", ""),
             "type": fil.get("type", ""), "brand": fil.get("brand", ""),
             "color": colour or fil.get("color", "")}
    for key in PROFILE_KEYS:
        draft[key] = fil.get(key) or 0
    return draft


def adjust_limits(draft, key, low, high):
    # the profile's own printing range for the nozzle, when it has one
    if key == "nozzle_temp":
        low = draft.get("min_temp") or low
        high = draft.get("max_temp") or high
    return low, high


def slot_set_script(slot, draft):
    """The CFS_SLOT_SET line that stores a draft in a slot."""
    parts = ["CFS_SLOT_SET", "SLOT=%s" % slot]
    if draft.get("filament"):
        parts.append("FILAMENT=%s" % draft["filament"])
    else:
        parts.append('NAME="%s"' % str(draft.get("name", "")).replace('"', ""))
        parts.append('TYPE="%s"' % str(draft.get("type", "")).replace('"', ""))
    colour = (draft.get("color") or "").strip().lstrip("#")
    if colour:
        parts.append("COLOR=%s" % colour.upper())
    for key, param, _l, step, _lo, _hi, _f in ADJUST:
        value = draft.get(key) or 0
        if isinstance(step, int):
            parts.append("%s=%d" % (param, round(value)))
        else:
            parts.append("%s=%.3f" % (param, value))
    # the first layer keeps its distance from the nozzle temperature
    if draft.get("nozzle_first"):
        parts.append("NOZZLE_FIRST=%d" % round(draft["nozzle_first"]))
    return " ".join(parts)


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
        # the slot editor: filament profiles, the slot being edited, its draft
        self.materials = []
        self.library = []
        self.view = "main"
        self.edit_slot = None
        self.draft = {}
        self.pick_type = None
        self.edit_widgets = {}

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

        self.note = Gtk.Label(hexpand=True, wrap=True, halign=Gtk.Align.FILL,
                              xalign=0, max_width_chars=40)
        self.note.set_margin_start(10)
        self.note.set_margin_top(4)
        self.note.set_margin_bottom(4)

        self.btn_load = self._gtk.Button("load", _("Load"), "color3")
        self.btn_load.connect("clicked", self.load_selected)
        self.btn_unload = self._gtk.Button("retract", _("Unload"), "color2")
        self.btn_unload.connect("clicked", self.unload_active)
        self.btn_edit = self._gtk.Button("fine-tune", "Edit", "color1")
        self.btn_edit.connect("clicked", self.edit_selected)
        self.btn_tags = self._gtk.Button("refresh", "Read tags", "color4")
        self.btn_tags.connect("clicked", self.read_tags)
        actions = Gtk.Grid(column_homogeneous=True, hexpand=True)
        actions.attach(self.btn_load, 0, 0, 1, 1)
        actions.attach(self.btn_unload, 1, 0, 1, 1)
        actions.attach(self.btn_edit, 2, 0, 1, 1)
        actions.attach(self.btn_tags, 3, 0, 1, 1)
        actions.set_vexpand(False)

        self.main_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 hexpand=True, vexpand=True)
        self.main_view.add(self.header)
        self.main_view.add(grid)
        self.main_view.add(self.note)
        self.main_view.add(actions)
        self.content.add(self.main_view)
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
        self._ask_profiles()
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

    def _ask_profiles(self):
        # the profile lists are long and rarely change: asked for when the
        # page opens and when the editor does, not on every poll
        try:
            self._screen._ws.api.query_objects(
                {"hark_cfs_slots": ["materials", "library"]},
                self._on_profiles)
        except Exception:
            logging.exception("cfs panel: profile query failed")

    def _on_profiles(self, data, method=None, params=None):
        if not isinstance(data, dict) or "result" not in data:
            return
        slots = data["result"].get("status", {}).get("hark_cfs_slots") or {}
        self.materials = slots.get("materials") or []
        self.library = slots.get("library") or []
        if self.view == "main":
            self._render()

    def _refresh_once(self, *args):
        self.refresh()
        return False

    # --- state helpers -------------------------------------------------
    def _filaments(self):
        return list(self.library) + list(self.materials)

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
        self.btn_edit.set_sensitive(
            sel is not None and bool(self._filaments()) and not error)
        self.btn_tags.set_sensitive(bool(self.cfs.get("online")) and idle)
        if not self.cfs or error:
            note = ""
        elif sel is not None and not self._filaments():
            note = ("No filament profiles from the printer yet: Edit needs "
                    "[hark_cfs_slots] in its config.")
        elif not self._can_move_filament():
            note = ("Load and unload are not switched on for this printer "
                    "yet: the CFS is read-only here.")
        elif not idle:
            note = "Load and unload are for when nothing is printing."
        elif self.cfs.get("last_change_error"):
            note = str(self.cfs["last_change_error"])
        elif sel is None:
            note = ("Tap a slot to choose it, then Edit to set its filament "
                    "type, profile and colour.")
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

    # --- slot editor ---------------------------------------------------
    def _show(self, view, widget=None, title=None):
        self.view = view
        for child in self.content.get_children():
            self.content.remove(child)
        self.content.add(self.main_view if view == "main" else widget)
        self.content.show_all()
        self._screen.base_panel.set_title(
            "%s | %s" % (self.title, title) if title else self.title)
        if view == "main":
            self._render()

    def back(self):
        if self.view == "main":
            return False
        if self.view == "filament":
            self._show_types()
        elif self.view in ("type", "colour") and self._draft_known():
            self._show_edit()
        else:
            self._show("main")
        return True

    def _draft_known(self):
        return bool(self.draft.get("name") and self.draft.get("type"))

    def _button(self, style=None):
        button = self._gtk.Button(style=style)
        button.set_vexpand(False)
        return button

    def _picker(self, heading, columns):
        """A page with a heading over a scrolling grid of choices."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       hexpand=True, vexpand=True)
        label = Gtk.Label(hexpand=True, halign=Gtk.Align.FILL, wrap=True,
                          xalign=0, max_width_chars=40)
        label.set_markup("<b>%s</b>" % GLib.markup_escape_text(heading))
        label.set_margin_start(10)
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        grid = Gtk.Grid(column_homogeneous=True, hexpand=True,
                        row_spacing=4, column_spacing=4)
        scroll = self._gtk.ScrolledWindow()
        scroll.add(grid)
        page.add(label)
        page.add(scroll)

        def add(button):
            n = len(grid.get_children())
            grid.attach(button, n % columns, n // columns, 1, 1)
        return page, add

    def edit_selected(self, widget):
        slot = self.selected
        if slot is None:
            return
        self._ask_profiles()
        self.edit_slot = slot
        info = self.slots.get(slot) or {}
        if info.get("name") and info.get("type"):
            self.draft = {k: info.get(k) or (0 if k in PROFILE_KEYS else "")
                          for k in ("filament", "name", "type", "brand",
                                    "color") + PROFILE_KEYS}
            self._show_edit()
        else:
            self.draft = {}
            self._show_types()

    # step 1: material type
    def _show_types(self, widget=None):
        slot = self.edit_slot
        page, add = self._picker(
            "Slot %s: what material is it?" % slot, 4)
        for i, mtype in enumerate(filament_types(self._filaments())):
            button = self._gtk.Button(None, mtype, "color%d" % (i % 4 + 1))
            button.set_vexpand(False)
            button.set_size_request(-1, self._gtk.img_scale * 2)
            button.connect("clicked", self._show_filaments, mtype)
            add(button)
        self._show("type", page, "Slot %s" % slot)

    # step 2: filament profile of that type
    def _show_filaments(self, widget, mtype):
        slot = self.edit_slot
        self.pick_type = mtype
        page, add = self._picker(
            "Slot %s: which %s filament?" % (slot, mtype), 2)
        for fil in filaments_of_type(self._filaments(), mtype):
            button = self._button(
                "color3" if fil.get("source") != "creality" else "color4")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(16)
            box.set_margin_bottom(16)
            name = Gtk.Label(ellipsize=Pango.EllipsizeMode.END)
            name.set_markup("<b>%s</b>" % GLib.markup_escape_text(
                fil.get("name", "")))
            detail = Gtk.Label(ellipsize=Pango.EllipsizeMode.END)
            who = fil.get("brand") or ""
            if fil.get("source") != "creality":
                who = "My profile"
            detail.set_markup("<small>%s</small>" % GLib.markup_escape_text(
                "  ·  ".join(b for b in (who, profile_summary(fil)) if b)))
            box.add(name)
            box.add(detail)
            button.add(box)
            button.connect("clicked", self._pick_filament, fil)
            add(button)
        self._show("filament", page, "Slot %s" % slot)

    def _pick_filament(self, widget, fil):
        had_colour = self.draft.get("color", "")
        self.draft = draft_from_filament(fil, had_colour)
        if self.draft["color"]:
            self._show_edit()
        else:
            self._show_colours()

    # step 3: spool colour
    def _show_colours(self, widget=None):
        slot = self.edit_slot
        page, add = self._picker(
            "Slot %s: what colour is the spool?" % slot, 6)
        choices = list(COLOURS)
        current = (self.draft.get("color") or "").upper()
        if current and current not in [h for _, h in COLOURS]:
            choices.insert(0, ("As it is now", current))
        size = self._gtk.img_scale
        for name, hexval in choices:
            button = self._button()
            button.get_style_context().add_class("frame-item")
            if hexval == current:
                button.get_style_context().add_class("button_active")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            swatch = Swatch(size)
            swatch.set_rgb(hex_to_rgb(hexval))
            box.add(swatch)
            box.add(Gtk.Label(label=name))
            button.add(box)
            button.connect("clicked", self._pick_colour, hexval)
            add(button)
        self._show("colour", page, "Slot %s" % slot)

    def _pick_colour(self, widget, hexval):
        self.draft["color"] = hexval
        self._show_edit()

    # the slot's summary: change filament / colour, nudge its settings, save
    def _show_edit(self, widget=None):
        slot = self.edit_slot
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       hexpand=True, vexpand=True)
        side = Gtk.PositionType.LEFT
        bts = self._gtk.bsidescale
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                        hexpand=True, vexpand=True,
                        row_spacing=4, column_spacing=4)
        swatch = Swatch(self._gtk.img_scale)
        name = Gtk.Label(halign=Gtk.Align.START,
                         ellipsize=Pango.EllipsizeMode.END)
        detail = Gtk.Label(halign=Gtk.Align.START,
                           ellipsize=Pango.EllipsizeMode.END)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       valign=Gtk.Align.CENTER)
        text.add(name)
        text.add(detail)
        btn_filament = self._gtk.Button("filament", "Change filament",
                                        "color3", bts, side, 2)
        btn_filament.connect("clicked", self._show_types)
        btn_colour = self._gtk.Button("fine-tune", "Change colour", "color4",
                                      bts, side, 2)
        btn_colour.connect("clicked", self._show_colours)
        grid.attach(swatch, 0, 0, 1, 1)
        grid.attach(text, 1, 0, 3, 1)
        grid.attach(btn_filament, 4, 0, 2, 1)
        grid.attach(btn_colour, 6, 0, 2, 1)

        self.edit_widgets = {"swatch": swatch, "name": name,
                             "detail": detail, "values": {}}
        for i, (key, _p, label, step, low, high, fmt) in enumerate(ADJUST):
            col, row = (i % 2) * 4, 1 + i // 2
            title = Gtk.Label(label=label, halign=Gtk.Align.END, wrap=True)
            value = Gtk.Label()
            minus = self._gtk.Button("decrease", None, "color1", bts)
            plus = self._gtk.Button("increase", None, "color2", bts)
            minus.connect("clicked", self._adjust, key, -1)
            plus.connect("clicked", self._adjust, key, 1)
            grid.attach(title, col, row, 1, 1)
            grid.attach(minus, col + 1, row, 1, 1)
            grid.attach(value, col + 2, row, 1, 1)
            grid.attach(plus, col + 3, row, 1, 1)
            self.edit_widgets["values"][key] = (value, fmt)

        note = Gtk.Label(hexpand=True, wrap=True, halign=Gtk.Align.FILL,
                         xalign=0, max_width_chars=40)
        note.set_margin_start(10)
        note.set_margin_top(4)
        note.set_margin_bottom(4)
        note.set_text(
            "Used for loading, purging and colour changes. A sliced file's "
            "own temperatures and pressure advance still win while it prints.")

        btn_empty = self._gtk.Button("delete", "Mark empty", "color2",
                                     bts, side, 1)
        btn_empty.connect("clicked", self._clear_slot)
        btn_cancel = self._gtk.Button("cancel", _("Cancel"), "color4",
                                      bts, side, 1)
        btn_cancel.connect("clicked", self._cancel_edit)
        btn_save = self._gtk.Button("complete", _("Save"), "color3",
                                    bts, side, 1)
        btn_save.connect("clicked", self._save_slot)
        actions = Gtk.Grid(column_homogeneous=True, hexpand=True)
        actions.attach(btn_empty, 0, 0, 1, 1)
        actions.attach(btn_cancel, 1, 0, 1, 1)
        actions.attach(btn_save, 2, 0, 1, 1)
        actions.set_vexpand(False)
        for button in (btn_empty, btn_cancel, btn_save):
            button.set_size_request(-1, int(self._gtk.img_scale * 1.4))

        page.add(grid)
        page.add(note)
        page.add(actions)
        self._show("edit", page, "Slot %s" % slot)
        self._render_edit()

    def _render_edit(self):
        draft, w = self.draft, self.edit_widgets
        w["swatch"].set_rgb(hex_to_rgb(draft.get("color")))
        w["name"].set_markup("<big><b>%s</b></big>" % GLib.markup_escape_text(
            draft.get("name") or ""))
        w["detail"].set_text("  ·  ".join(b for b in (
            draft.get("type"), draft.get("brand"),
            colour_name(draft.get("color"))) if b))
        for key, (label, fmt) in w["values"].items():
            label.set_markup("<big><b>%s</b></big>" % GLib.markup_escape_text(
                fmt % (draft.get(key) or 0)))

    def _adjust(self, widget, key, direction):
        for k, _p, _l, step, low, high, _f in ADJUST:
            if k != key:
                continue
            low, high = adjust_limits(self.draft, key, low, high)
            old = self.draft.get(key) or 0
            new = min(max(old + direction * step, low), high)
            new = int(round(new)) if isinstance(step, int) else round(new, 3)
            self.draft[key] = new
            if key == "nozzle_temp" and self.draft.get("nozzle_first"):
                self.draft["nozzle_first"] += new - old
        self._render_edit()

    def _cancel_edit(self, widget=None):
        self._show("main")

    def _save_slot(self, widget):
        script = slot_set_script(self.edit_slot, self.draft)
        logging.info("cfs panel: %s", script)
        self._screen._ws.api.gcode_script(script)
        self._show("main")
        GLib.timeout_add(700, self._refresh_once)

    def _clear_slot(self, widget):
        slot = self.edit_slot
        self._show("main")
        self._screen._confirm_send_action(
            widget,
            "Mark slot %s as empty?\n\nIts filament record is cleared; "
            "nothing moves." % slot,
            "printer.gcode.script", {"script": "CFS_SLOT_CLEAR SLOT=%s" % slot})
