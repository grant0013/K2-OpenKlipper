#!/usr/bin/env python3
# Flow-ratio pads for the K2: nine 30 mm pads in a 3x3 grid at the bed
# centre, every layer above the first printed at that pad's flow ratio and
# labelled with it (x100) in raised digits along the front edge.
#
#   python3 flow_pads.py --slot C [--base 0.95] [--step 0.02] [-o file]
#
# Print it, look at the top surfaces in a low light from the side, and pick
# the pad where the lines just close up with no gaps and no ridges (going up
# in flow, gaps disappear first, then the surface starts to ridge and the
# edges bulge). Save it to the slot, which Orca reads back for that slot:
#   CFS_SLOT_SET SLOT=C FLOW=0.97
# For a finer pick, run again with --base <best> --step 0.01.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse, json, math, urllib.request

SLOTS = "ABCD"
FIL_AREA = math.pi * (1.75 / 2.) ** 2
W = 0.42                    # line width
FIRST_W = 0.5
H = 0.2                     # layer height
LAYERS = 5                  # 1 first + 3 solid + 1 top
WALLS = 2
PAD = 30.
PITCH = 40.
CENTRE = (130., 130.)
RETRACT = 1.0
TRAVEL_F = 18000
# 7-segment strokes in a 3.2 x 5.6 mm box: a b c d e f g
DW, DH = 3.2, 5.6
SEG = {'a': ((0, DH), (DW, DH)), 'b': ((DW, DH), (DW, DH / 2)),
       'c': ((DW, DH / 2), (DW, 0)), 'd': ((DW, 0), (0, 0)),
       'e': ((0, 0), (0, DH / 2)), 'f': ((0, DH / 2), (0, DH)),
       'g': ((0, DH / 2), (DW, DH / 2))}
DIGITS = {'0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc',
          '5': 'afgcd', '6': 'afgedc', '7': 'abc', '8': 'abcdefg',
          '9': 'gfabcd'}


def area(w, h):
    """Cross-section Orca uses for a line of width w on a layer h high."""
    return (w - h) * h + math.pi * (h / 2.) ** 2


def spacing(w, h):
    return w - h * (1. - math.pi / 4.)


class Gcode:
    def __init__(self):
        self.out = []
        self.pos = None
        self.z = 0.

    def emit(self, line):
        self.out.append(line)

    def travel(self, x, y):
        if self.pos and math.hypot(x - self.pos[0], y - self.pos[1]) < 0.01:
            return
        far = self.pos is None or math.hypot(x - self.pos[0],
                                             y - self.pos[1]) > 2.
        if far:
            self.emit("G1 E-%.2f F2400" % RETRACT)
            self.emit("G1 Z%.3f F600" % (self.z + 0.4))
        self.emit("G1 X%.3f Y%.3f F%d" % (x, y, TRAVEL_F))
        if far:
            self.emit("G1 Z%.3f F600" % self.z)
            self.emit("G1 E%.2f F2400" % RETRACT)
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


def pad_layer(g, x0, y0, w, e_per_mm, wall_v, fill_v, along_x):
    s = spacing(w, H)
    for k in reversed(range(WALLS)):
        i = w / 2. + k * s
        g.path([(x0 + i, y0 + i), (x0 + PAD - i, y0 + i),
                (x0 + PAD - i, y0 + PAD - i), (x0 + i, y0 + PAD - i),
                (x0 + i, y0 + i)], e_per_mm, wall_v)
    lo = w / 2. + WALLS * s
    hi = PAD - lo
    n = int((hi - lo) / s) + 1
    step = (hi - lo) / (n - 1)
    pts = []
    for j in range(n):
        c = lo + j * step
        a, b = (lo, hi) if j % 2 == 0 else (hi, lo)
        if along_x:
            pts += [(x0 + a, y0 + c), (x0 + b, y0 + c)]
        else:
            pts += [(x0 + c, y0 + a), (x0 + c, y0 + b)]
    g.path(pts, e_per_mm, fill_v)


def label(g, x0, y0, text, e_per_mm):
    x = x0 + 3.
    for ch in text:
        strokes = [SEG[s] for s in DIGITS[ch]]
        for (ax, ay), (bx, by) in strokes:
            g.travel(x + ax, y0 + 3. + ay)
            g.line(x + bx, y0 + 3. + by, e_per_mm, 20)
        x += DW + 1.6


def slot_temps(slot):
    try:
        url = ("http://192.168.3.64:7126/server/files/config/"
               "hark_cfs_slots.json")
        rec = json.load(urllib.request.urlopen(url, timeout=5))
        rec = rec['slots'][slot]
        return (rec['nozzle_temp'], rec['nozzle_first'], rec['bed_temp'],
                rec['flow_ratio'], rec['type'])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', default='C')
    ap.add_argument('--base', type=float)
    ap.add_argument('--step', type=float, default=0.02)
    ap.add_argument('--temp', type=int)
    ap.add_argument('--bed', type=int)
    ap.add_argument('-o')
    a = ap.parse_args()
    slot = a.slot.upper()
    rec = slot_temps(slot)
    temp = a.temp or (rec[0] if rec else 220)
    bed = a.bed or (rec[2] if rec else 55)
    base = a.base or (rec[3] if rec and rec[3] else 0.95)
    ratios = [round(base + (i - 4) * a.step, 3) for i in range(9)]
    names = ["fr_%03d" % round(r * 100) for r in ratios]
    origins = []
    for i in range(9):
        col, row = i % 3, i // 3
        cx = CENTRE[0] + (col - 1) * PITCH
        cy = CENTRE[1] + (row - 1) * PITCH
        origins.append((cx - PAD / 2., cy - PAD / 2.))

    g = Gcode()
    g.emit("; K2 flow-ratio pads (Hark Tech), generated by flow_pads.py")
    g.emit("; slot %s, %dC, bed %dC; flow ratios %s (front-left to "
           "back-right, label = ratio x100)" % (slot, temp, bed,
                                                ", ".join("%.2f" % r
                                                          for r in ratios)))
    g.emit("; pick the smoothest closed top, then: CFS_SLOT_SET SLOT=%s "
           "FLOW=<ratio>" % slot)
    for (x0, y0), n in zip(origins, names):
        g.emit("EXCLUDE_OBJECT_DEFINE NAME=%s CENTER=%.1f,%.1f "
               "POLYGON=[[%.1f,%.1f],[%.1f,%.1f],[%.1f,%.1f],[%.1f,%.1f]]"
               % (n, x0 + PAD / 2, y0 + PAD / 2, x0, y0, x0 + PAD, y0,
                  x0 + PAD, y0 + PAD, x0, y0 + PAD))
    g.emit("M106 S0")
    g.emit("START_PRINT EXTRUDER_TEMP=%d BED_TEMP=%d TOOL=%d"
           % (temp, bed, SLOTS.index(slot)))
    g.emit("T%d" % SLOTS.index(slot))
    g.emit("M83\nG90\nG1 Z0.6 F600\nM104 S%d\nG92 E0" % temp)
    for layer in range(LAYERS):
        g.z = H * (layer + 1)
        first = layer == 0
        top = layer == LAYERS - 1
        g.emit(";LAYER:%d" % layer)
        if layer == 1:
            g.emit("M106 S255")
        g.emit("SET_VELOCITY_LIMIT ACCEL=%d" % (2000 if first else
                                               5000 if top else 10000))
        g.emit("G1 Z%.3f F600" % g.z)
        for i, ((x0, y0), n, r) in enumerate(zip(origins, names, ratios)):
            w = FIRST_W if first else W
            # The first layer stays at the slot's ratio so every pad sticks
            # the same way; only the layers judged change.
            e = area(w, H) / FIL_AREA * (base if first else r)
            g.emit("EXCLUDE_OBJECT_START NAME=%s" % n)
            pad_layer(g, x0, y0, w, e, 40 if first else 120,
                      60 if first else 150 if not top else 100,
                      along_x=(layer % 2 == 0))
            g.emit("EXCLUDE_OBJECT_END NAME=%s" % n)
    g.emit("SET_VELOCITY_LIMIT ACCEL=2000")
    for k in range(2):
        g.z = H * (LAYERS + 1 + k)
        g.emit(";LAYER:%d" % (LAYERS + k))
        g.emit("G1 Z%.3f F600" % g.z)
        for (x0, y0), n, r in zip(origins, names, ratios):
            g.emit("EXCLUDE_OBJECT_START NAME=%s" % n)
            label(g, x0, y0, "%d" % round(r * 100),
                  area(W, H) / FIL_AREA * base)
            g.emit("EXCLUDE_OBJECT_END NAME=%s" % n)
    g.emit("G1 E-%.2f F2400\nG1 Z5 F600\nEND_PRINT" % RETRACT)
    out = a.o or "K2_flow_pads_slot%s_%03d-%03d.gcode" % (
        slot, round(ratios[0] * 100), round(ratios[-1] * 100))
    with open(out, 'w') as f:
        f.write("\n".join(g.out) + "\n")
    print("wrote %s: slot %s %dC bed %dC, ratios %s"
          % (out, slot, temp, bed, " ".join("%.2f" % r for r in ratios)))


if __name__ == '__main__':
    main()
