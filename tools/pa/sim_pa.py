#!/usr/bin/env python3
"""Synthetic PA_CAL_RUN logs with a known answer, to test analyze_pa.py.

Nozzle model: outflow lags inflow with time constant TAU (first order), and
the gauge reads pressure ~ outflow (sign flipped, offset, drift, noise).
Klipper PA adds K * (smoothed extruder velocity derivative) to the inflow,
so the ideal PA equals TAU.
Usage: sim_pa.py <outdir> <name> [tau]
"""
import os, sys
import numpy as np

out, name = sys.argv[1], sys.argv[2]
TAU = float(sys.argv[3]) if len(sys.argv) > 3 else 0.045
KS = [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]
SMOOTH = float(os.environ.get("SIM_SMOOTH", "0.038"))
DT = 0.00078125
V_XY, SEG, ACC = 40., 14., 20000.
SLOW, FAST = 2. / 2.405, 12. / 2.405       # filament mm/s
CYCLES = int(os.environ.get("SIM_CYCLES", "4"))

moves, marks = [], []
t = 1.0


def segment(t, ev):
    """XY move of SEG mm at V_XY, 180 degree reversal: accel, cruise, decel."""
    ta = V_XY / ACC
    da = 0.5 * ACC * ta * ta
    tc = (SEG - 2 * da) / V_XY
    r = ev / V_XY                       # filament per mm of XY
    out = [(t, ta, 0., ACC * r), (t + ta, tc, V_XY * r, 0.),
           (t + ta + tc, ta, V_XY * r, -ACC * r)]
    return out, t + 2 * ta + tc


block_k = []
marks.append((t, "dry"))
for _ in range(6):
    _, t = segment(t, 0.)
t += 1.5
for k in KS:
    t += 1.5
    marks.append((t, "k%g" % k))
    start = t
    for ev, n in [(SLOW, 4), (FAST, 4)] * CYCLES + [(SLOW, 4)]:
        for _ in range(n):
            seg, t = segment(t, ev)
            moves.extend(seg)
    marks.append((t, "end%g" % k))
    block_k.append((start, t, k))
t_end = t + 2.

ts = np.arange(0., t_end, DT)
v = np.zeros_like(ts)
for pt, mt, sv, ac in moves:
    sel = (ts >= pt) & (ts < pt + mt)
    v[sel] = sv + ac * (ts[sel] - pt)
# PA applied with Klipper-like smoothing of the velocity
n = max(1, int(SMOOTH / DT))
vs = np.convolve(v, np.ones(n) / n, mode="same")
kvec = np.zeros_like(ts)
for s, e, k in block_k:
    kvec[(ts >= s - 0.5) & (ts <= e + 1.0)] = k
q_in = v + kvec * np.gradient(vs, DT)
q_out = np.zeros_like(ts)
for i in range(1, len(ts)):
    q_out[i] = q_out[i - 1] + DT / TAU * (q_in[i - 1] - q_out[i - 1])
rng = np.random.default_rng(int(os.environ.get("SIM_SEED", "1")))
GAIN = float(os.environ.get("SIM_GAIN", "5000"))
raw = (-(GAIN * q_out) - 1600000 + 300 * ts + rng.normal(0, 400, len(ts))
       + 600 * np.sin(2 * np.pi * 208 * ts))
# The K2 delivers 64-sample (50 ms) windows, one every ~166 ms, first
# sample dropped, with the odd decode glitch
keep = np.zeros(len(ts), bool)
w = rng.uniform(0, 0.1)
while w < ts[-1]:
    i0 = int(w / DT)
    keep[i0 + 1:i0 + 64] = True
    w += 0.166 + rng.uniform(-0.01, 0.01)
glitch = rng.random(len(ts)) < 0.002
raw[glitch] = 2426879
ts, raw = ts[keep], raw[keep]

os.makedirs(out, exist_ok=True)
with open("%s/%s_pressure.csv" % (out, name), "w") as f:
    f.write("print_time,raw\n")
    for a, b in zip(ts, raw):
        f.write("%.6f,%d\n" % (a, b))
with open("%s/%s_extruder.csv" % (out, name), "w") as f:
    f.write("print_time,move_t,start_v,accel,start_e\n")
    for pt, mt, sv, ac in moves:
        f.write("%.6f,%.6f,%.6f,%.6f,0\n" % (pt, mt, sv, ac))
with open("%s/%s_marks.csv" % (out, name), "w") as f:
    f.write("print_time,label\n")
    for a, b in marks:
        f.write("%.6f,%s\n" % (a, b))
print("simulated tau=%.3f -> ideal PA %.3f" % (TAU, TAU))
