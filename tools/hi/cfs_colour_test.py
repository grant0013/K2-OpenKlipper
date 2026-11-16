#!/usr/bin/env python3
# Quick multi-colour bench print for the Creality Hi's CFS on K2 OpenKlipper:
# a stepped tile, three layers per colour, so every colour stays visible from
# above as its own stripe, with a small tower beside it where the nozzle is
# primed after each colour change. It exercises START_PRINT's CFS load, the
# T<n> tool macros and CFS_TOOLCHANGE (cut, rewind, load, purge) mid-print.
#
#   python3 cfs_colour_test.py --slots B,C,A --temp 250 --bed 80 > test.gcode
#
# --slots is the print order (light colours first need the least purging).
# The file selects tools by the default tool map (T0=A .. T3=D).
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse, math

FIL_AREA = math.pi * (1.75 / 2.) ** 2
W, H = 0.45, 0.2
SPACING = W - H * (1. - math.pi / 4.)
RETRACT = 0.8
# CFS_PURGE leaves its own retract (purge_retract_length) in the nozzle
CHANGE_PRIME = RETRACT + 1.5
LAYERS_PER_COLOUR = 3
TILE_W, TILE_D = 45., 25.          # full footprint (first colour)
TOWER = 6.
GAP = 6.                           # tower to tile
CENTRE = (130., 130.)


def e_per_mm(flow):
    return ((W - H) * H + math.pi * (H / 2.) ** 2) / FIL_AREA * flow


class Out:
    def __init__(self, flow):
        self.lines, self.pos, self.z = [], None, 0.
        self.epm = e_per_mm(flow)
        self.retracted = False

    def emit(self, line):
        self.lines.append(line)

    def retract(self):
        if not self.retracted:
            self.emit("G1 E-%.2f F2400" % RETRACT)
            self.retracted = True

    def unretract(self, amount=RETRACT):
        if self.retracted:
            self.emit("G1 E%.2f F1800" % amount)
            self.retracted = False

    def travel(self, x, y):
        far = self.pos is None or math.hypot(x - self.pos[0],
                                             y - self.pos[1]) > 1.5
        if far:
            self.retract()
            self.emit("G1 Z%.3f F900" % (self.z + 0.4))
        self.emit("G1 X%.3f Y%.3f F12000" % (x, y))
        if far:
            self.emit("G1 Z%.3f F900" % self.z)
            self.unretract()
        self.pos = (x, y)

    def line(self, x, y, speed):
        d = math.hypot(x - self.pos[0], y - self.pos[1])
        if d < 1e-6:
            return
        self.emit("G1 X%.3f Y%.3f E%.5f F%d" % (x, y, d * self.epm,
                                                speed * 60))
        self.pos = (x, y)

    def loop(self, x0, y0, x1, y1, speed):
        self.travel(x0, y0)
        for x, y in ((x1, y0), (x1, y1), (x0, y1), (x0, y0)):
            self.line(x, y, speed)

    def rect(self, x0, y0, x1, y1, speed, along_x, walls=2):
        # walls from the outside in, then a zigzag fill
        for i in range(walls):
            o = W / 2. + i * SPACING
            self.loop(x0 + o, y0 + o, x1 - o, y1 - o, speed)
        o = W / 2. + walls * SPACING - 0.1          # slight overlap
        fx0, fy0, fx1, fy1 = x0 + o, y0 + o, x1 - o, y1 - o
        if fx1 - fx0 < SPACING or fy1 - fy0 < SPACING:
            return
        if along_x:
            n = max(1, int(round((fy1 - fy0) / SPACING)))
            step = (fy1 - fy0) / n
            self.travel(fx0, fy0)
            for i in range(n + 1):
                y = fy0 + i * step
                if i:
                    self.line(self.pos[0], y, speed)
                self.line(fx1 if i % 2 == 0 else fx0, y, speed)
        else:
            n = max(1, int(round((fx1 - fx0) / SPACING)))
            step = (fx1 - fx0) / n
            self.travel(fx0, fy0)
            for i in range(n + 1):
                x = fx0 + i * step
                if i:
                    self.line(x, self.pos[1], speed)
                self.line(x, fy1 if i % 2 == 0 else fy0, speed)


def build(slots, temp, bed, material, pa, flow, fan):
    colours = len(slots)
    layers = colours * LAYERS_PER_COLOUR
    total_w = TOWER + GAP + TILE_W
    x_left = CENTRE[0] - total_w / 2.
    y0 = CENTRE[1] - TILE_D / 2.
    tower = (x_left, y0, x_left + TOWER, y0 + TOWER)
    tile_x0 = x_left + TOWER + GAP
    tools = ["ABCD".index(s) for s in slots]
    o = Out(flow)
    poly = [[x_left - 3, y0 - 3], [tile_x0 + TILE_W + 3, y0 - 3],
            [tile_x0 + TILE_W + 3, y0 + TILE_D + 3], [x_left - 3,
                                                       y0 + TILE_D + 3]]
    o.emit("; Creality Hi CFS colour test: slots %s (%s), %d layers,"
           " tools/hi/cfs_colour_test.py" % (",".join(slots), material,
                                              layers))
    o.emit("EXCLUDE_OBJECT_DEFINE NAME=cfs_test CENTER=%.1f,%.1f POLYGON=%s"
           % (CENTRE[0], CENTRE[1],
              "[" + ",".join("[%.1f,%.1f]" % tuple(p) for p in poly) + "]"))
    o.emit("START_PRINT EXTRUDER_TEMP=%d BED_TEMP=%d MATERIAL=%s"
           " TOTAL_LAYER=%d VOLUMETRIC=8 SLOT=%s"
           % (temp, bed, material, layers, slots[0]))
    o.emit("T%d" % tools[0])
    # START_PRINT primes at its park temperature and leaves the nozzle
    # heating: wait for it so the first layer bonds
    o.emit("TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%d" % (temp - 4))
    o.emit("SET_PRESSURE_ADVANCE ADVANCE=%.3f" % pa)
    o.emit("EXCLUDE_OBJECT_START NAME=cfs_test")
    o.emit("G90")
    o.emit("M83")
    o.emit("G92 E0")
    for n in range(1, layers + 1):
        colour = (n - 1) // LAYERS_PER_COLOUR
        o.z = n * H
        speed = 30 if n == 1 else 80
        o.emit("; layer %d, slot %s" % (n, slots[colour]))
        o.emit("SET_PRINT_STATS_INFO CURRENT_LAYER=%d" % n)
        if n > 1 and (n - 1) % LAYERS_PER_COLOUR == 0:
            # colour change: park over the tower, change, prime onto it
            o.retract()
            o.emit("G1 Z%.3f F900" % (o.z + 0.4))
            o.emit("G1 X%.3f Y%.3f F12000" % (tower[0] + W / 2.,
                                              tower[1] + W / 2.))
            o.emit("T%d" % tools[colour])
            o.emit("G90")
            o.emit("M83")
            o.emit("G1 X%.3f Y%.3f Z%.3f F6000" % (tower[0] + W / 2.,
                                                  tower[1] + W / 2., o.z))
            o.pos = (tower[0] + W / 2., tower[1] + W / 2.)
            o.retracted = True
            o.unretract(CHANGE_PRIME)
        else:
            o.emit("G1 Z%.3f F900" % o.z)
        if n == 2 and fan:
            o.emit("M106 S%d" % fan)
        if n == 1:
            # one skirt loop takes the first ooze
            o.loop(x_left - 2., y0 - 2., tile_x0 + TILE_W + 2.,
                   y0 + TILE_D + 2., speed)
        o.rect(tower[0], tower[1], tower[2], tower[3], min(speed, 40),
               along_x=n % 2 == 0, walls=3)
        width = TILE_W * (colours - colour) / colours
        o.rect(tile_x0, y0, tile_x0 + width, y0 + TILE_D, speed,
               along_x=n % 2 == 0)
    o.retract()
    o.emit("EXCLUDE_OBJECT_END NAME=cfs_test")
    o.emit("END_PRINT")
    return "\n".join(o.lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slots', default='B,C,A')
    ap.add_argument('--temp', type=int, default=250)
    ap.add_argument('--bed', type=int, default=80)
    ap.add_argument('--material', default='PETG')
    ap.add_argument('--pa', type=float, default=0.06)
    ap.add_argument('--flow', type=float, default=0.95)
    # the Hi halves M106 with a 20 % floor: 102 = 20 % on the part
    ap.add_argument('--fan', type=int, default=102)
    a = ap.parse_args()
    slots = [s.strip().upper() for s in a.slots.split(',') if s.strip()]
    if (not 2 <= len(slots) <= 4 or len(set(slots)) != len(slots)
            or any(s not in "ABCD" for s in slots)):
        ap.error("--slots needs 2 to 4 different slots from A-D")
    print(build(slots, a.temp, a.bed, a.material, a.pa, a.flow, a.fan),
          end="")


if __name__ == '__main__':
    main()
