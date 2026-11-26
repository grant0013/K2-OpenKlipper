# Printed filament calibrations started from the printer: flow ratio pads, a
# retraction test, a pressure advance pattern and a temperature tower, then
# pick the best result and save it to the slot.
#
#   CALIBRATE_FLOW SLOT=x [BASE=0.95] [STEP=0.02]
#   CALIBRATE_RETRACTION SLOT=x [START=0.2] [STEP=0.2]
#   CALIBRATE_PA SLOT=x [CENTRE=slot PA] [STEP=0.006] [COUNT=11]
#   CALIBRATE_TEMP SLOT=x [START=hot end] [END=cool end] [STEP=5]
#   CALIB_SET KIND=flow|retraction|pa|temp SLOT=x VALUE=v    (the pick)
#   CALIB_SKIP                                       (keep the old value)
#
# The slot record is what every later print uses (hark_filament_override puts
# it over the sliced file's values), and the pick is also remembered for that
# filament profile and colour (hark_cfs_slots tuned). The test files opt out
# of the override themselves (FILAMENT_OVERRIDE PRINT=0): they set their own
# flow, retraction, PA and temperatures on purpose.
#
# Each command writes its test print into the G-code folder and starts it.
# The file's last line, CALIB_PICK, asks which result is best: a Mainsail
# prompt with one button per value, and get_status()['pending'] for the
# touchscreen and the Mainsail CFS card. The pick is saved with CFS_SLOT_SET
# (FLOW= or RETRACT=), so OrcaSlicer and the next print use it.
#
# [hark_calib] options (the defaults are the K2's):
#   start_style: k2 | hi   hi = the Hi's one-line START_PRINT (TOOL=,
#                          MATERIAL=, TOTAL_LAYER= so it KAMP-purges by the
#                          test's objects and measures PA for the slot)
#   fan: full | slot       slot = the slot's own max fan, as a print of that
#                          filament gets it (PETG 80%), sent through the
#                          printer's M106 like a slicer file
#   printer_name: K2 OpenKlipper   (the test files' first line)
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
         'retraction': ('RETRACT', 'retraction', 'pillar pair'),
         'pa': ('PA', 'pressure advance', 'chevron'),
         'temp': ('NOZZLE', 'nozzle temperature', 'tower block')}
HINTS = {
    'flow': "Look at the tops side-on: the lowest number where the lines "
            "have closed up, before ridges.",
    'retraction': "Look between each pair of pillars: the shortest "
                  "retraction with no strings.",
    'pa': "Look at the chevron tips (label = PA x1000): the sharpest, "
          "even-width corner. Too little bulges, too much leaves a gap.",
    'temp': "Blocks run bottom (hottest) to top. Pick the coolest block "
            "with clean walls and a firm overhang, before strings, gloss "
            "or sag get worse.",
}


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


def square(g, x0, y0, size, w, walls, e, wall_v, fill_v, along_x,
           size_y=None):
    """Square (or size x size_y rectangle) with walls and a zig-zag solid
    fill."""
    sx, sy = size, size if size_y is None else size_y
    s = spacing(w, H)
    for k in reversed(range(walls)):
        i = w / 2. + k * s
        g.path([(x0 + i, y0 + i), (x0 + sx - i, y0 + i),
                (x0 + sx - i, y0 + sy - i), (x0 + i, y0 + sy - i),
                (x0 + i, y0 + i)], e, wall_v)
    lo = w / 2. + walls * s
    hi_x, hi_y = sx - lo, sy - lo
    if hi_x - lo < s / 2. or hi_y - lo < s / 2.:
        return
    # lines run along one axis and step across the other
    across = hi_y if along_x else hi_x
    n = max(2, int((across - lo) / s) + 1)
    step = (across - lo) / (n - 1)
    pts = []
    for j in range(n):
        c = lo + j * step
        if along_x:
            a, b = (lo, hi_x) if j % 2 == 0 else (hi_x, lo)
            pts += [(x0 + a, y0 + c), (x0 + b, y0 + c)]
        else:
            a, b = (lo, hi_y) if j % 2 == 0 else (hi_y, lo)
            pts += [(x0 + c, y0 + a), (x0 + c, y0 + b)]
    g.path(pts, e, fill_v)


def fan_value(rec, fan='full'):
    """M106 S value for the test layers: full, or the slot's max fan."""
    if fan != 'slot':
        return 255
    try:
        pct = float(rec.get('fan_max') or 100.)
    except (TypeError, ValueError):
        pct = 100.
    return int(round(255. * max(0., min(100., pct)) / 100.))


def start_lines(slot, temp, bed, header, objects, style='k2', material='',
                layers=0):
    tool = 0 if slot == 'E' else SLOTS.index(slot)
    # the slot's saved values must not replace the test's own
    lines = list(header) + ["FILAMENT_OVERRIDE PRINT=0"]
    for name, (x0, y0, x1, y1) in objects:
        lines.append(
            "EXCLUDE_OBJECT_DEFINE NAME=%s CENTER=%.1f,%.1f "
            "POLYGON=[[%.1f,%.1f],[%.1f,%.1f],[%.1f,%.1f],[%.1f,%.1f]]"
            % (name, (x0 + x1) / 2, (y0 + y1) / 2, x0, y0, x1, y0, x1, y1,
               x0, y1))
    if style == 'hi':
        # The Hi's one-line start: loads this tool's slot, measures its PA
        # and purges next to the objects above (KAMP), as an Orca file does
        start = ("START_PRINT EXTRUDER_TEMP=%d BED_TEMP=%d TOOL=%d "
                 "MATERIAL=%s TOTAL_LAYER=%d VOLUMETRIC=12"
                 % (temp, bed, tool, (material or 'PLA').upper(), layers))
    else:
        start = ("START_PRINT EXTRUDER_TEMP=%d BED_TEMP=%d TOOL=%d"
                 % (temp, bed, tool))
    lines += ["M106 S0", start,
              "T%d" % tool, "M83", "G90", "G1 Z0.6 F600",
              "M104 S%d" % temp, "G92 E0"]
    return lines


def flow_pads(slot, rec, base, step, style='k2', fan='full',
              name='K2 OpenKlipper'):
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
        ["; %s flow-ratio pads: slot %s, %s"
         % (name, slot, ", ".join("%.2f" % r for r in ratios))],
        [(n, (x, y, x + pad, y + pad)) for n, (x, y) in zip(names, origins)],
        style, rec.get('type'), layers + 2)
    for layer in range(layers):
        g.z = H * (layer + 1)
        first, top = layer == 0, layer == layers - 1
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S%d" % fan_value(rec, fan))
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


def retraction_test(slot, rec, start, step, count=7, style='k2', fan='full',
                    name='K2 OpenKlipper'):
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
    layers = int(round(height / H))
    g.out = start_lines(
        slot, temp, bed,
        ["; %s retraction test: slot %s, %s mm at %.0f mm/s"
         % (name, slot, ", ".join("%.1f" % r for r in lengths), speed)],
        objects, style, rec.get('type'), layers)
    for layer in range(layers):
        g.z = H * (layer + 1)
        first = layer == 0
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S%d" % fan_value(rec, fan))
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


def pa_pattern(slot, rec, pas, centre, style='k2', fan='full',
               name='K2 OpenKlipper'):
    """Andrew Ellis style chevrons (OrcaSlicer's PA pattern), each at its
    own pressure advance, labelled x1000 under the bottom leg."""
    w, layers, walls, leg, pitch, cy = 0.45, 4, 3, 30., 10., CENTRE[1]
    temp = int(rec.get('nozzle_temp') or 220)
    bed = int(rec.get('bed_temp') or 55)
    dx = leg * math.cos(math.radians(45))
    dy = leg * math.sin(math.radians(45))
    x0 = CENTRE[0] - (pitch * (len(pas) - 1) + dx) / 2.
    xs = [x0 + i * pitch for i in range(len(pas))]
    sp = spacing(w, H)
    e = area(w, H) / FIL_AREA * (rec.get('flow_ratio') or 1.)
    g = Path(rec.get('retract_length') or 1.0)
    g.out = start_lines(
        slot, temp, bed,
        ["; %s pressure advance pattern: slot %s, %s (label = PA x1000)"
         % (name, slot, ", ".join("%.3f" % p for p in pas))],
        [('pa_pattern', (xs[0] - 2, cy - dy - 8, xs[-1] + dx + 4,
                         cy + dy + 2))],
        style, rec.get('type'), layers)
    # the print start may hold a measured PA over the file's: release it,
    # each chevron prints at its own value
    g.emit("PA_HOLD_MEASURED")
    g.emit("EXCLUDE_OBJECT_START NAME=pa_pattern")
    for layer in range(layers):
        g.z = H * (layer + 1)
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S%d" % fan_value(rec, fan))
        speed = 30. if layer == 0 else 120.
        g.emit("SET_VELOCITY_LIMIT ACCEL=%d" % (1000 if layer == 0 else 5000))
        g.emit("G1 Z%.3f F600" % g.z)
        for x, pa in zip(xs, pas):
            g.emit("SET_PRESSURE_ADVANCE ADVANCE=%.4f" % pa)
            for k in range(walls):
                off = k * sp / math.sin(math.radians(45))
                g.travel(x + off + dx, cy + dy)
                g.line(x + off, cy, e, speed)
                g.line(x + off + dx, cy - dy, e, speed)
        if layer < 2:
            g.emit("SET_PRESSURE_ADVANCE ADVANCE=%.4f" % centre)
            for x, pa in zip(xs, pas):
                text = "%d" % round(pa * 1000)
                g.label(x + dx - len(text) * 2.0 + 2., cy - dy - 7., text,
                        area(0.4, H) / FIL_AREA, w=2.4, h=4.2, gap=0.9)
    g.emit("EXCLUDE_OBJECT_END NAME=pa_pattern")
    g.emit("SET_PRESSURE_ADVANCE ADVANCE=%.4f" % centre)
    g.emit("G1 E-%.2f F2400" % g.retract)
    g.emit("G1 Z10 F600")
    return g.out


def temp_tower(slot, rec, temps, style='k2', fan='full',
               name='K2 OpenKlipper', block_h=6.):
    """One block per temperature, hottest at the bottom. Each block is two
    10 mm pillars 15 mm apart (strings show in the gap; the travel retracts
    by the slot's length) and the right pillar's outer side leans out at
    45 degrees (overhang droop). A 0.6 mm step in at the foot of each block
    marks where the next temperature starts."""
    w, size, gap, walls = 0.42, 10., 15., 2
    bed = int(rec.get('bed_temp') or 55)
    flow = rec.get('flow_ratio') or 1.
    e = area(w, H) / FIL_AREA * flow
    per = int(round(block_h / H))
    x_a = CENTRE[0] - (2 * size + gap + block_h) / 2.
    x_b = x_a + size + gap
    y0 = CENTRE[1] - size / 2.
    height = per * len(temps) * H
    g = Path(rec.get('retract_length') or 0.8,
             float(rec.get('retract_speed') or 40.), hop=0)
    g.out = start_lines(
        slot, temps[0], bed,
        ["; %s temperature tower: slot %s, bottom to top %s C, %.0f mm "
         "blocks" % (name, slot, ", ".join("%d" % t for t in temps),
                     block_h)],
        [('temp_tower', (x_a - 1, y0 - 9, x_b + size + block_h + 1,
                         y0 + size + 1))],
        style, rec.get('type'), per * len(temps))
    g.emit("EXCLUDE_OBJECT_START NAME=temp_tower")
    for layer in range(per * len(temps)):
        block, inner = divmod(layer, per)
        temp = temps[block]
        g.z = H * (layer + 1)
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S%d" % fan_value(rec, fan))
        if inner == 0 and block > 0:
            # off the part to change temperature, so the wait oozes there
            g.emit("G1 E-%.2f F2400" % g.retract)
            g.emit("G1 Z%.3f F600" % (g.z + 1.))
            g.emit("G1 X%.3f Y%.3f F12000" % (x_b + size + block_h + 8.,
                                               y0 - 8.))
            g.emit("M117 Temperature tower %d C" % temp)
            g.emit("M104 S%d" % temp)
            g.emit("TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%d MAXIMUM=%d"
                   % (temp - 2, temp + 2))
            g.emit("RESPOND MSG=\"Temperature tower: block %d of %d at %d C\""
                   % (block + 1, len(temps), temp))
            g.emit("G1 E%.2f F2400" % g.retract)
            g.pos = None
        g.emit("SET_VELOCITY_LIMIT ACCEL=%d" % (2000 if layer == 0 else 5000))
        g.emit("G1 Z%.3f F600" % g.z)
        v = 25 if layer == 0 else 80
        notch = 0.6 if (block > 0 and inner < 3) else 0.
        # 45 degrees: out one layer height per layer, after the notch
        lean = max(0, inner - 2) * H
        along = layer % 2 == 0
        square(g, x_a + notch, y0 + notch, size - 2 * notch, w, walls, e,
               v, v + 20, along)
        # the travel across the gap retracts by the slot's length
        square(g, x_b + notch, y0 + notch, size - 2 * notch + lean, w,
               walls, e, v, v + 20, along, size_y=size - 2 * notch)
        if layer == 0:
            # bottom temperature under the left pillar
            g.label(x_a, y0 - 8., "%d" % temps[0], e)
    g.emit("EXCLUDE_OBJECT_END NAME=temp_tower")
    g.emit("G1 E-%.2f F2400" % g.retract)
    g.emit("G1 Z%.3f F600" % (height + 5))
    return g.out


class HarkCalib:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.pending = None
        self.style = config.getchoice('start_style',
                                      {'k2': 'k2', 'hi': 'hi'}, 'k2')
        self.fan = config.getchoice('fan', {'full': 'full', 'slot': 'slot'},
                                    'full')
        self.name = config.get('printer_name', 'K2 OpenKlipper')
        for cmd, desc in (
                ('CALIBRATE_FLOW', "Print flow-ratio pads for SLOT=, then "
                                   "pick the best"),
                ('CALIBRATE_RETRACTION', "Print a retraction test for SLOT=,"
                                         " then pick the best"),
                ('CALIBRATE_PA', "Print a pressure advance pattern for "
                                 "SLOT=, then pick the best"),
                ('CALIBRATE_TEMP', "Print a temperature tower for SLOT=, "
                                   "then pick the best"),
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
        state = stats.get_status(
            self.printer.get_reactor().monotonic()).get('state')
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
        if kind == 'flow':
            return "%.2f" % value
        if kind == 'pa':
            return "%.4f" % value
        if kind == 'temp':
            return "%d" % round(value)
        return "%.1f" % value

    @staticmethod
    def _label(kind, value):
        if kind == 'pa':
            return "%d" % round(value * 1000)
        if kind == 'temp':
            return "%d C" % round(value)
        return HarkCalib._fmt(kind, value)

    # --- commands ------------------------------------------------------
    def cmd_CALIBRATE_FLOW(self, gcmd):
        self._require_idle(gcmd)
        slot, rec = self._slot_record(gcmd)
        base = gcmd.get_float('BASE', rec.get('flow_ratio') or 0.95,
                              minval=0.5, maxval=1.5)
        step = gcmd.get_float('STEP', 0.02, minval=0.005, maxval=0.1)
        lines, ratios = flow_pads(slot, rec, base, step, self.style,
                                  self.fan, self.name)
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
        lines, lengths = retraction_test(slot, rec, start, step,
                                         style=self.style, fan=self.fan,
                                         name=self.name)
        lines += ["END_PRINT", "CALIB_PICK KIND=retraction SLOT=%s VALUES=%s"
                  % (slot, ",".join("%.2f" % r for r in lengths))]
        self._start(gcmd, "calib_retraction_slot%s.gcode" % slot, lines)

    def cmd_CALIBRATE_PA(self, gcmd):
        self._require_idle(gcmd)
        slot, rec = self._slot_record(gcmd)
        centre = gcmd.get_float('CENTRE', rec.get('pressure_advance') or
                                (0.06 if 'PETG' in str(rec.get('type', ''))
                                 .upper() else 0.045),
                                minval=0., maxval=1.)
        step = gcmd.get_float('STEP', 0.006, minval=0.001, maxval=0.05)
        count = gcmd.get_int('COUNT', 11, minval=3, maxval=15)
        start = max(0., round(centre - step * (count // 2), 4))
        pas = [round(start + i * step, 4) for i in range(count)]
        lines = pa_pattern(slot, rec, pas, centre, self.style, self.fan,
                           self.name)
        lines += ["END_PRINT", "CALIB_PICK KIND=pa SLOT=%s VALUES=%s"
                  % (slot, ",".join("%.4f" % p for p in pas))]
        self._start(gcmd, "calib_pa_slot%s.gcode" % slot, lines)

    def cmd_CALIBRATE_TEMP(self, gcmd):
        self._require_idle(gcmd)
        slot, rec = self._slot_record(gcmd)
        nozzle = int(rec.get('nozzle_temp') or 220)
        lo = int(rec.get('min_temp') or nozzle - 20)
        hi = int(rec.get('max_temp') or nozzle + 20)
        step = gcmd.get_int('STEP', 5, minval=2, maxval=20)
        start = gcmd.get_int('START', min(hi, nozzle + 3 * step),
                             minval=170, maxval=320)
        end = gcmd.get_int('END', max(lo, nozzle - 3 * step),
                           minval=170, maxval=320)
        if end >= start:
            raise gcmd.error("START (%d) must be hotter than END (%d)"
                             % (start, end))
        temps = list(range(start, end - 1, -step))
        if len(temps) > 10:
            raise gcmd.error("%d blocks is too tall: use a bigger STEP or a "
                             "narrower START/END (10 at most)" % len(temps))
        lines = temp_tower(slot, rec, temps, self.style, self.fan,
                           self.name)
        lines += ["END_PRINT", "CALIB_PICK KIND=temp SLOT=%s VALUES=%s"
                  % (slot, ",".join("%d" % t for t in temps))]
        self._start(gcmd, "calib_temp_slot%s.gcode" % slot, lines)

    def cmd_CALIB_PICK(self, gcmd):
        kind = gcmd.get('KIND').lower()
        if kind not in KINDS:
            raise gcmd.error("KIND must be flow, retraction, pa or temp")
        slot = gcmd.get('SLOT').upper()
        try:
            values = [float(v) for v in gcmd.get('VALUES').split(',') if v]
        except ValueError:
            raise gcmd.error("VALUES must be numbers separated by commas")
        _, what, item = KINDS[kind]
        hint = HINTS[kind]
        self.pending = {'kind': kind, 'slot': slot, 'values': values,
                        'labels': [self._label(kind, v) for v in values],
                        'what': what, 'hint': hint,
                        'label': "Pick the best %s for slot %s"
                                 % (item, slot)}
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
                       'VALUE=%s|primary' % (self._label(kind, v), kind,
                                             slot, v))
        out += ['action:prompt_button_group_end',
                'action:prompt_footer_button Keep the old value|CALIB_SKIP|'
                'secondary', 'action:prompt_show']
        for line in out:
            self.gcode.respond_raw('// ' + line)

    def cmd_CALIB_SET(self, gcmd):
        kind = gcmd.get('KIND').lower()
        if kind not in KINDS:
            raise gcmd.error("KIND must be flow, retraction, pa or temp")
        slot = gcmd.get('SLOT').upper()
        value = gcmd.get_float('VALUE', minval=0.)
        param, what, _ = KINDS[kind]
        extra = ""
        if kind == 'temp':
            # the first layer keeps its distance from the printing
            # temperature (often +5)
            slots = self.printer.lookup_object('hark_cfs_slots')
            rec = slots.slots.get(slot) or {}
            old = int(rec.get('nozzle_temp') or 0)
            first = int(rec.get('nozzle_first') or old)
            lift = max(0, first - old) if old else 0
            extra = " NOZZLE_FIRST=%d" % (round(value) + lift)
        self.gcode.run_script_from_command(
            "CFS_SLOT_SET SLOT=%s %s=%s%s" % (slot, param,
                                              self._fmt(kind, value), extra))
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
