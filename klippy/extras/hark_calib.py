# Printed filament calibrations started from the printer: flow ratio pads and
# a retraction test, then pick the best result and save it to the slot.
#
#   CALIBRATE_FLOW SLOT=x [BASE=0.95] [STEP=0.02]
#   CALIBRATE_RETRACTION SLOT=x [START=0.2] [STEP=0.2]
#   CALIB_SET KIND=flow|retraction SLOT=x VALUE=v    (the pick)
#   CALIB_SKIP                                       (keep the old value)
#
# Each command writes its test print into the G-code folder and starts it.
# The file's last line, CALIB_PICK, asks which result is best: a Mainsail
# prompt with one button per value, and get_status()['pending'] for the
# touchscreen and the Mainsail CFS card. The pick is saved with CFS_SLOT_SET
# (FLOW= or RETRACT=), so OrcaSlicer and the next print use it.
#
# Copyright (C) 2026 Grant Harkness (Hark Tech)
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, os

SLOTS = "ABCD"
FIL_AREA = math.pi * (1.75 / 2.) ** 2
H = 0.2
CENTRE = (130., 130.)
# 7-segment digits in a w x h box: a b c d e f g
DIGITS = {'0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc',
          '5': 'afgcd', '6': 'afgedc', '7': 'abc', '8': 'abcdefg',
          '9': 'gfabcd'}
KINDS = {'flow': ('FLOW', 'flow ratio', 'pad'),
         'retraction': ('RETRACT', 'retraction', 'pillar pair')}


def area(w, h):
    """Cross-section OrcaSlicer uses for a line of width w on a layer h."""
    return (w - h) * h + math.pi * (h / 2.) ** 2


def spacing(w, h):
    return w - h * (1. - math.pi / 4.)


class Path:
    """Relative-E G-code with retract + z-hop on longer travels."""

    def __init__(self, retract=1.0, retract_speed=40., hop=0.4):
        self.out, self.pos, self.z = [], None, 0.
        self.retract, self.retract_speed, self.hop = retract, retract_speed, hop

    def emit(self, line):
        self.out.append(line)

    def travel(self, x, y, retract=None, hop=None, speed=300.):
        if self.pos and math.hypot(x - self.pos[0], y - self.pos[1]) < 0.01:
            return
        far = self.pos is None or math.hypot(x - self.pos[0],
                                             y - self.pos[1]) > 2.
        r = self.retract if retract is None else retract
        hop = self.hop if hop is None else hop
        if far and r > 0:
            self.emit("G1 E-%.3f F%d" % (r, self.retract_speed * 60))
        if far and hop:
            self.emit("G1 Z%.3f F600" % (self.z + hop))
        self.emit("G1 X%.3f Y%.3f F%d" % (x, y, speed * 60))
        if far and hop:
            self.emit("G1 Z%.3f F600" % self.z)
        if far and r > 0:
            self.emit("G1 E%.3f F%d" % (r, self.retract_speed * 60))
        self.pos = (x, y)

    def line(self, x, y, e_per_mm, speed):
        d = math.hypot(x - self.pos[0], y - self.pos[1])
        self.emit("G1 X%.3f Y%.3f E%.5f F%d" % (x, y, d * e_per_mm,
                                                 speed * 60))
        self.pos = (x, y)

    def path(self, pts, e_per_mm, speed):
        self.travel(*pts[0])
        for p in pts[1:]:
            self.line(p[0], p[1], e_per_mm, speed)

    def label(self, x, y, text, e_per_mm, w=3.2, h=5.6, gap=1.6):
        seg = {'a': ((0, h), (w, h)), 'b': ((w, h), (w, h / 2)),
               'c': ((w, h / 2), (w, 0)), 'd': ((w, 0), (0, 0)),
               'e': ((0, 0), (0, h / 2)), 'f': ((0, h / 2), (0, h)),
               'g': ((0, h / 2), (w, h / 2))}
        for ch in text:
            for s in DIGITS.get(ch, ''):
                (ax, ay), (bx, by) = seg[s]
                self.travel(x + ax, y + ay)
                self.line(x + bx, y + by, e_per_mm, 20)
            x += w + gap


def square(g, x0, y0, size, w, walls, e, wall_v, fill_v, along_x):
    """Square with walls and a zig-zag solid fill."""
    s = spacing(w, H)
    for k in reversed(range(walls)):
        i = w / 2. + k * s
        g.path([(x0 + i, y0 + i), (x0 + size - i, y0 + i),
                (x0 + size - i, y0 + size - i), (x0 + i, y0 + size - i),
                (x0 + i, y0 + i)], e, wall_v)
    lo = w / 2. + walls * s
    hi = size - lo
    if hi - lo < s / 2.:
        return
    n = max(2, int((hi - lo) / s) + 1)
    step = (hi - lo) / (n - 1)
    pts = []
    for j in range(n):
        c = lo + j * step
        a, b = (lo, hi) if j % 2 == 0 else (hi, lo)
        pts += ([(x0 + a, y0 + c), (x0 + b, y0 + c)] if along_x
                else [(x0 + c, y0 + a), (x0 + c, y0 + b)])
    g.path(pts, e, fill_v)


def start_lines(slot, temp, bed, header, objects):
    tool = 0 if slot == 'E' else SLOTS.index(slot)
    lines = list(header)
    for name, (x0, y0, x1, y1) in objects:
        lines.append(
            "EXCLUDE_OBJECT_DEFINE NAME=%s CENTER=%.1f,%.1f "
            "POLYGON=[[%.1f,%.1f],[%.1f,%.1f],[%.1f,%.1f],[%.1f,%.1f]]"
            % (name, (x0 + x1) / 2, (y0 + y1) / 2, x0, y0, x1, y0, x1, y1,
               x0, y1))
    lines += ["M106 S0",
              "START_PRINT EXTRUDER_TEMP=%d BED_TEMP=%d TOOL=%d"
              % (temp, bed, tool),
              "T%d" % tool, "M83", "G90", "G1 Z0.6 F600",
              "M104 S%d" % temp, "G92 E0"]
    return lines


def flow_pads(slot, rec, base, step):
    """Nine 30 mm pads, 3x3 at the bed centre, every layer above the first
    at that pad's flow ratio, labelled with it x100."""
    w, first_w, pad, pitch, layers, walls = 0.42, 0.5, 30., 40., 5, 2
    temp = int(rec.get('nozzle_temp') or 220)
    bed = int(rec.get('bed_temp') or 55)
    ratios = [round(base + (i - 4) * step, 3) for i in range(9)]
    names = ["fr_%03d" % round(r * 100) for r in ratios]
    origins = [(CENTRE[0] + (i % 3 - 1) * pitch - pad / 2.,
                CENTRE[1] + (i // 3 - 1) * pitch - pad / 2.)
               for i in range(9)]
    g = Path(rec.get('retract_length') or 1.0)
    g.out = start_lines(
        slot, temp, bed,
        ["; K2 OpenKlipper flow-ratio pads: slot %s, %s"
         % (slot, ", ".join("%.2f" % r for r in ratios))],
        [(n, (x, y, x + pad, y + pad)) for n, (x, y) in zip(names, origins)])
    for layer in range(layers):
        g.z = H * (layer + 1)
        first, top = layer == 0, layer == layers - 1
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S255")
        g.emit("SET_VELOCITY_LIMIT ACCEL=%d"
               % (2000 if first else 5000 if top else 10000))
        g.emit("G1 Z%.3f F600" % g.z)
        for (x0, y0), n, r in zip(origins, names, ratios):
            lw = first_w if first else w
            e = area(lw, H) / FIL_AREA * (base if first else r)
            g.emit("EXCLUDE_OBJECT_START NAME=%s" % n)
            square(g, x0, y0, pad, lw, walls, e, 40 if first else 120,
                   60 if first else 100 if top else 150, layer % 2 == 0)
            g.emit("EXCLUDE_OBJECT_END NAME=%s" % n)
    g.emit("SET_VELOCITY_LIMIT ACCEL=2000")
    for k in range(2):
        g.z = H * (layers + 1 + k)
        g.emit("G1 Z%.3f F600" % g.z)
        for (x0, y0), n, r in zip(origins, names, ratios):
            g.emit("EXCLUDE_OBJECT_START NAME=%s" % n)
            g.label(x0 + 3., y0 + 3., "%d" % round(r * 100),
                    area(w, H) / FIL_AREA * base)
            g.emit("EXCLUDE_OBJECT_END NAME=%s" % n)
    g.emit("G1 E-%.2f F2400" % g.retract)
    g.emit("G1 Z5 F600")
    return g.out, ratios


def retraction_test(slot, rec, start, step, count=7):
    """Pairs of 5 mm pillars 20 mm apart, 12 mm tall. The travel between the
    two pillars of a pair (front to back) retracts by that pair's length, so
    the pair that strings least has the best retraction. Labelled x10.
    Pairs are printed in a zig-zag, so the move to the next pair runs along
    a row, never front to back, and uses the largest retraction; no z-hop
    anywhere (a hop drew PETG strings between the rear pillars, 10-28)."""
    w, size, gap_y, pitch, height, walls = 0.42, 5., 20., 13., 12., 2
    temp = int(rec.get('nozzle_temp') or 220)
    bed = int(rec.get('bed_temp') or 55)
    speed = float(rec.get('retract_speed') or 40.)
    lengths = [round(start + i * step, 2) for i in range(count)]
    x_first = CENTRE[0] - pitch * (count - 1) / 2. - size / 2.
    y_a = CENTRE[1] - gap_y / 2. - size / 2.
    y_b = CENTRE[1] + gap_y / 2. - size / 2.
    flow = rec.get('flow_ratio') or 1.
    e = area(w, H) / FIL_AREA * flow
    objects = []
    for i, r in enumerate(lengths):
        x = x_first + i * pitch
        objects.append(("rt_%02d" % round(r * 10),
                        (x - 2, y_a - 9, x + size + 2, y_b + size)))
    g = Path(max(lengths) + 0.5, speed, hop=0)
    g.out = start_lines(
        slot, temp, bed,
        ["; K2 OpenKlipper retraction test: slot %s, %s mm at %.0f mm/s"
         % (slot, ", ".join("%.1f" % r for r in lengths), speed)],
        objects)
    layers = int(round(height / H))
    for layer in range(layers):
        g.z = H * (layer + 1)
        first = layer == 0
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S255")
        g.emit("SET_VELOCITY_LIMIT ACCEL=%d" % (2000 if first else 5000))
        g.emit("G1 Z%.3f F600" % g.z)
        v = 25 if first else 60
        # Odd layers run right to left (and flip each pair), so a layer
        # starts on the pillar the last one ended on
        back = layer % 2 == 1
        order = list(enumerate(lengths))
        for i, r in (reversed(order) if back else order):
            x = x_first + i * pitch
            name = objects[i][0]
            g.emit("EXCLUDE_OBJECT_START NAME=%s" % name)
            if first:
                g.label(x - 0.5, y_a - 8., "%d" % round(r * 10), e)
            # Front then back on even pairs, back then front on odd ones:
            # the test travel is front to back, the move to the next pair
            # stays in one row
            first_y, second_y = (y_a, y_b) if (i % 2 == 0) != back else (y_b, y_a)
            square(g, x, first_y, size, w, walls, e, v, v, layer % 2 == 0)
            g.travel(x + w, second_y + w, retract=r, speed=150)
            square(g, x, second_y, size, w, walls, e, v, v, layer % 2 == 0)
            g.emit("EXCLUDE_OBJECT_END NAME=%s" % name)
    g.emit("G1 E-%.2f F2400" % g.retract)
    g.emit("G1 Z%.3f F600" % (height + 5))
    return g.out, lengths


class HarkCalib:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.pending = None
        for cmd, desc in (
                ('CALIBRATE_FLOW', "Print flow-ratio pads for SLOT=, then "
                                   "pick the best"),
                ('CALIBRATE_RETRACTION', "Print a retraction test for SLOT=,"
                                         " then pick the best"),
                ('CALIB_PICK', "Ask which calibration result is best"),
                ('CALIB_SET', "Save the picked calibration result"),
                ('CALIB_SKIP', "Close the calibration pick, keep the old "
                               "value")):
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd),
                                        desc=desc)

    def get_status(self, eventtime):
        return {'pending': dict(self.pending) if self.pending else {}}

    # --- helpers -------------------------------------------------------
    def _slot_record(self, gcmd):
        slot = gcmd.get('SLOT').upper()
        slots = self.printer.lookup_object('hark_cfs_slots')
        if slot not in slots.slots:
            raise gcmd.error("SLOT must be A, B, C, D or E")
        rec = slots.slots[slot]
        if not rec.get('loaded'):
            raise gcmd.error("Slot %s has no filament set: set it first"
                             % slot)
        cfs = self.printer.lookup_object('k2_cfs', None)
        fitted = cfs is None or cfs.fitted
        if fitted and slot == 'E':
            raise gcmd.error("Slot E is the external spool, used without a "
                             "CFS")
        if not fitted and slot != 'E':
            raise gcmd.error("No CFS: calibrate the external spool, SLOT=E")
        return slot, rec

    def _require_idle(self, gcmd):
        stats = self.printer.lookup_object('print_stats')
        state = stats.get_status(0.).get('state')
        if state in ('printing', 'paused'):
            raise gcmd.error("A print is running")

    def _start(self, gcmd, name, lines):
        sd = self.printer.lookup_object('virtual_sdcard')
        path = os.path.join(sd.sdcard_dirname, name)
        with open(path, 'w') as f:
            f.write("\n".join(lines) + "\n")
        gcmd.respond_info("Starting %s" % name)
        self.gcode.run_script_from_command(
            "SDCARD_PRINT_FILE FILENAME=%s" % name)

    @staticmethod
    def _fmt(kind, value):
        return "%.2f" % value if kind == 'flow' else "%.1f" % value

    # --- commands ------------------------------------------------------
    def cmd_CALIBRATE_FLOW(self, gcmd):
        self._require_idle(gcmd)
        slot, rec = self._slot_record(gcmd)
        base = gcmd.get_float('BASE', rec.get('flow_ratio') or 0.95,
                              minval=0.5, maxval=1.5)
        step = gcmd.get_float('STEP', 0.02, minval=0.005, maxval=0.1)
        lines, ratios = flow_pads(slot, rec, base, step)
        lines += ["END_PRINT", "CALIB_PICK KIND=flow SLOT=%s VALUES=%s"
                  % (slot, ",".join("%.3f" % r for r in ratios))]
        self._start(gcmd, "calib_flow_slot%s.gcode" % slot, lines)

    def cmd_CALIBRATE_RETRACTION(self, gcmd):
        self._require_idle(gcmd)
        slot, rec = self._slot_record(gcmd)
        current = rec.get('retract_length') or 0.8
        step = gcmd.get_float('STEP', 0.2, minval=0.05, maxval=1.)
        start = gcmd.get_float('START', max(0.2, round(current - 3 * step, 2)),
                               minval=0., maxval=5.)
        lines, lengths = retraction_test(slot, rec, start, step)
        lines += ["END_PRINT", "CALIB_PICK KIND=retraction SLOT=%s VALUES=%s"
                  % (slot, ",".join("%.2f" % r for r in lengths))]
        self._start(gcmd, "calib_retraction_slot%s.gcode" % slot, lines)

    def cmd_CALIB_PICK(self, gcmd):
        kind = gcmd.get('KIND').lower()
        if kind not in KINDS:
            raise gcmd.error("KIND must be flow or retraction")
        slot = gcmd.get('SLOT').upper()
        values = [float(v) for v in gcmd.get('VALUES').split(',') if v]
        _, what, item = KINDS[kind]
        self.pending = {'kind': kind, 'slot': slot, 'values': values,
                        'label': "Pick the best %s for slot %s"
                                 % (item, slot)}
        if kind == 'flow':
            hint = ("Look at the tops side-on: the lowest number where the "
                    "lines have closed up, before ridges.")
        else:
            hint = ("Look between each pair of pillars: the shortest "
                    "retraction with no strings.")
        out = ['action:prompt_end',
               'action:prompt_begin Pick the best %s (slot %s)' % (item,
                                                                  slot),
               'action:prompt_text %s' % hint,
               'action:prompt_button_group_start']
        for i, v in enumerate(values):
            if i and i % 3 == 0:
                out += ['action:prompt_button_group_end',
                        'action:prompt_button_group_start']
            out.append('action:prompt_button %s|CALIB_SET KIND=%s SLOT=%s '
                       'VALUE=%s|primary' % (self._fmt(kind, v), kind, slot,
                                             v))
        out += ['action:prompt_button_group_end',
                'action:prompt_footer_button Keep the old value|CALIB_SKIP|'
                'secondary', 'action:prompt_show']
        for line in out:
            self.gcode.respond_raw('// ' + line)

    def cmd_CALIB_SET(self, gcmd):
        kind = gcmd.get('KIND').lower()
        if kind not in KINDS:
            raise gcmd.error("KIND must be flow or retraction")
        slot = gcmd.get('SLOT').upper()
        value = gcmd.get_float('VALUE', minval=0.)
        param, what, _ = KINDS[kind]
        self.gcode.run_script_from_command(
            "CFS_SLOT_SET SLOT=%s %s=%s" % (slot, param, self._fmt(kind,
                                                                  value)))
        self.pending = None
        self.gcode.respond_raw('// action:prompt_end')
        gcmd.respond_info("Slot %s %s set to %s"
                          % (slot, what, self._fmt(kind, value)))

    def cmd_CALIB_SKIP(self, gcmd):
        self.pending = None
        self.gcode.respond_raw('// action:prompt_end')
        gcmd.respond_info("Calibration pick closed; the slot keeps its "
                          "value")


def load_config(config):
    return HarkCalib(config)
