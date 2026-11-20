#!/usr/bin/env python3
"""Vibration during a print, by speed: find the speeds that shake the Hi.

  print_vibration.py log    [--socket SOCK] [--chips y] [--out FILE.csv]
                            [--any-state]  (bench tests: log when not printing)
  print_vibration.py report FILE.csv [--png FILE.png]

log: connects to Klipper's API socket and streams two things at once: the
accelerometer samples (lis2dw/dump_lis2dw) and the toolhead's planned moves
(motion_report/dump_trapq). Every --window seconds (0.25) of printing it
writes one CSV row: toolhead speed and direction from the moves, how much of
the window was spent accelerating, and per accelerometer the vibration RMS
of each axis (linear trend removed, so the commanded acceleration does not
count) plus the strongest frequency 10-170 Hz. Rows go to the CSV as they
are made; Ctrl-C or the end of the print stops it.

  Chip y is the BED accelerometer (bed_mcu, its own link): safe during any
  print. Chip x is the TOOLHEAD accelerometer on the nozzle MCU, whose
  230400-baud link also carries the extruder: 1600 samples/s would use
  about half of it, so only add it on a test print (--chips y,x), never on a
  customer print.

report: groups the (mostly) constant-speed windows by direction (mostly X, mostly Y,
diagonal) and speed (5 mm/s bins), prints the median vibration per bin,
flags bins well above their neighbours, and checks whether the strongest
frequency follows the speed (frequency / speed = 1 / a fixed distance):
2 mm is the GT2 belt tooth, 40 mm one pulley turn, ~53 mm one 17 mm bed
wheel turn, ~8.8 mm the bed roughness found on 17 Nov 2026.

Runs with Klipper's python (numpy; matplotlib only for --png).

Copyright (C) 2026  Hark Tech
This file may be distributed under the terms of the GNU GPLv3 license.
"""
import argparse
import bisect
import collections
import csv
import json
import math
import os
import signal
import socket
import sys
import time

DEFAULT_SOCKET = os.path.expanduser("~/printer_data_hi/comms/klippy.sock")
FMIN, FMAX = 10., 170.
FIELDS = ["t", "layer", "speed", "vx", "vy", "vz", "accel_frac"]
SPATIAL = (("GT2 belt tooth", 2.), ("bed roughness 8.8 mm", 8.8),
           ("pulley turn", 40.), ("bed wheel turn", 53.4))


# --- moves -----------------------------------------------------------------
class Moves:
    """Toolhead moves from motion_report/dump_trapq rows:
    (time, duration, start_velocity, acceleration, start_pos, direction)."""
    def __init__(self):
        self.starts, self.moves = [], []

    def add(self, rows):
        for r in rows:
            t, dur, v0, acc, pos, d = r[:6]
            if self.starts and t < self.starts[-1]:
                continue
            self.starts.append(t)
            self.moves.append((t, dur, v0, acc, d))

    def covered_until(self):
        if not self.moves:
            return -1.
        t, dur = self.moves[-1][:2]
        return t + dur

    def prune(self, before):
        i = bisect.bisect_left(self.starts, before) - 1
        if i > 0:
            del self.starts[:i], self.moves[:i]

    def sample(self, t):
        """(speed, (vx, vy, vz), accelerating) at print time t, or None."""
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0:
            return None
        t0, dur, v0, acc, d = self.moves[i]
        if t > t0 + dur:
            return 0., (0., 0., 0.), False
        v = v0 + acc * (t - t0)
        return v, (v * d[0], v * d[1], v * d[2]), abs(acc) > 1e-6


def window_motion(moves, t0, t1, n=20):
    speeds, vecs, acc = [], [], 0
    for k in range(n):
        s = moves.sample(t0 + (k + .5) * (t1 - t0) / n)
        if s is None:
            return None
        speeds.append(s[0])
        vecs.append(s[1])
        acc += s[2]
    mean = lambda vals: sum(vals) / len(vals)
    return (mean(speeds), [mean([v[a] for v in vecs]) for a in range(3)],
            acc / n)


# --- vibration ---------------------------------------------------------------
def window_vibration(samples):
    """samples: [(t, ax, ay, az)] -> (rms_x, rms_y, rms_z, peak_hz, peak_amp)."""
    import numpy as np
    a = np.array(samples, dtype=float)
    t = a[:, 0] - a[0, 0]
    out = []
    det = []
    for c in (1, 2, 3):
        y = a[:, c]
        if len(t) > 2 and t[-1] > 0:
            p = np.polyfit(t, y, 1)
            y = y - np.polyval(p, t)
        else:
            y = y - y.mean()
        det.append(y)
        out.append(float(np.sqrt((y ** 2).mean())))
    peak_hz = peak_amp = 0.
    if len(t) >= 16 and t[-1] > 0:
        fs = (len(t) - 1) / t[-1]
        win = np.hanning(len(t))
        power = sum(np.abs(np.fft.rfft(y * win)) ** 2 for y in det)
        f = np.fft.rfftfreq(len(t), 1. / fs)
        band = (f >= FMIN) & (f <= min(FMAX, fs / 2. * .9))
        if band.any():
            k = int(np.argmax(np.where(band, power, 0)))
            peak_hz = float(f[k])
            peak_amp = float(math.sqrt(power[k]) * 2. / win.sum())
    return out + [peak_hz, peak_amp]


# --- Klipper API -------------------------------------------------------------
class Api:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.s.setblocking(False)
        self.buf = b""
        self.next_id = 1

    def send(self, method, params):
        msg = {"id": self.next_id, "method": method, "params": params}
        self.next_id += 1
        self.s.sendall(json.dumps(msg).encode() + b"\x03")

    def read(self, timeout=.2):
        import select
        r, _, _ = select.select([self.s], [], [], timeout)
        if r:
            data = self.s.recv(1 << 20)
            if not data:
                raise ConnectionError("Klipper closed the API socket")
            self.buf += data
        out = []
        while b"\x03" in self.buf:
            raw, self.buf = self.buf.split(b"\x03", 1)
            try:
                out.append(json.loads(raw))
            except ValueError:
                pass
        return out


def cmd_log(args):
    chips = [c.strip() for c in args.chips.split(",") if c.strip()]
    if "x" in chips and not args.i_know_x_is_heavy:
        sys.exit("Chip x (toolhead) shares the nozzle MCU link with the "
                 "extruder: only on a test print, add --i-know-x-is-heavy")
    api = Api(args.socket)
    for c in chips:
        api.send("lis2dw/dump_lis2dw",
                 {"sensor": c, "response_template": {"q": "acc", "c": c}})
    api.send("motion_report/dump_trapq",
             {"name": "toolhead", "response_template": {"q": "trapq"}})
    out = open(args.out, "w", newline="")
    fields = list(FIELDS)
    for c in chips:
        fields += ["%s_%s" % (c, k) for k in
                   ("rms_x", "rms_y", "rms_z", "peak_hz", "peak_amp")]
    w = csv.writer(out)
    w.writerow(fields)
    moves = Moves()
    acc = {c: collections.deque() for c in chips}
    layer, state, seen_printing = 0, "", False
    next_t = None
    last_query = 0.
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(now=True))
    rows = 0
    print("logging %s to %s (Ctrl-C to stop)" % (",".join(chips), args.out),
          flush=True)
    try:
        while not stop["now"]:
            now = time.time()
            if now - last_query > 2.:
                api.send("objects/query", {"objects": {
                    "print_stats": ["state", "info"]},
                    "response_template": {"q": "ps"}})
                last_query = now
            for m in api.read():
                q = m.get("q")
                p = m.get("params") or {}
                if q == "acc":
                    acc[m["c"]].extend(p.get("data") or [])
                elif q == "trapq":
                    moves.add(p.get("data") or [])
                elif q == "ps" or "result" in m:
                    st = ((m.get("result") or p).get("status") or {}).get(
                        "print_stats") or {}
                    if st:
                        state = st.get("state", state)
                        layer = (st.get("info") or {}).get(
                            "current_layer") or layer
                        if state == "printing":
                            seen_printing = True
            if args.until_done and seen_printing and state not in (
                    "printing", "paused"):
                print("print over (%s)" % state, flush=True)
                break
            # windows that both streams cover
            ready = min([moves.covered_until()] +
                        [acc[c][-1][0] if acc[c] else -1. for c in chips])
            if next_t is None and ready > 0:
                next_t = ready - 1.
            while next_t is not None and next_t + args.window <= ready:
                t0, t1 = next_t, next_t + args.window
                next_t = t1
                mot = window_motion(moves, t0, t1)
                if mot is None or (state != "printing"
                                   and not args.any_state):
                    continue
                row = [round(t0, 3), layer, round(mot[0], 2)] + \
                    [round(v, 2) for v in mot[1]] + [round(mot[2], 2)]
                ok = True
                for c in chips:
                    d = acc[c]
                    while d and d[0][0] < t0:
                        d.popleft()
                    win = [s for s in d if s[0] < t1]
                    if len(win) < 16:
                        ok = False
                        break
                    row += [round(v, 1) for v in window_vibration(win)]
                if ok:
                    w.writerow(row)
                    rows += 1
                    if rows % 400 == 0:
                        out.flush()
                        print("%d windows, layer %s" % (rows, layer),
                              flush=True)
            if next_t is not None:
                moves.prune(next_t - 2.)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
        print("wrote %d windows to %s" % (rows, args.out), flush=True)


# --- report ------------------------------------------------------------------
def median(vals):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return 0.
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.


def classify(vx, vy, speed):
    if speed <= 0:
        return None
    if abs(vx) >= .9 * speed:
        return "X"
    if abs(vy) >= .9 * speed:
        return "Y"
    return "diagonal"


def cmd_report(args):
    rows = list(csv.DictReader(open(args.file)))
    if not rows:
        sys.exit("no windows in %s" % args.file)
    chips = sorted({k.split("_")[0] for k in rows[0] if k.endswith("_rms_x")})
    print("%s: %d windows (%.0f s of printing), chips %s" % (
        os.path.basename(args.file), len(rows),
        len(rows) * args.window, ",".join(chips)))
    cruise = [r for r in rows if float(r["accel_frac"]) <= args.max_accel
              and float(r["speed"]) >= 5.]
    print("%d mostly constant-speed windows used (at most %d%% of the "
          "window accelerating)\n" % (len(cruise), args.max_accel * 100))
    groups = collections.defaultdict(list)
    for r in cruise:
        cls = classify(float(r["vx"]), float(r["vy"]), float(r["speed"]))
        if cls:
            groups[cls].append(r)
    flagged = []
    for chip in chips:
        name = {"y": "bed", "x": "toolhead"}.get(chip, chip)
        for cls in ("X", "Y", "diagonal"):
            g = groups.get(cls) or []
            if len(g) < 20:
                continue
            bins = collections.defaultdict(list)
            for r in g:
                b = int(float(r["speed"]) // args.bin) * args.bin
                tot = math.sqrt(sum(float(r["%s_rms_%s" % (chip, a)]) ** 2
                                    for a in "xyz"))
                bins[b].append((tot, float(r["%s_rms_z" % chip]),
                                float(r["%s_peak_hz" % chip])))
            keys = sorted(k for k in bins if len(bins[k]) >= args.min_count)
            if not keys:
                continue
            meds = {k: median([v[0] for v in bins[k]]) for k in keys}
            overall = median(list(meds.values()))
            print("== %s accelerometer, moves along %s (%d windows)" % (
                name, cls, len(g)))
            print("   speed mm/s   windows   vibration RMS   vertical   "
                  "main freq")
            for k in keys:
                vals = bins[k]
                around = [meds[j] for j in keys if j != k and
                          abs(j - k) <= 2 * args.bin]
                ref = median(around) if around else overall
                flag = ""
                if meds[k] > args.flag * max(ref, 1.) and \
                        meds[k] > overall:
                    flag = "  <-- %.1fx its neighbours" % (meds[k] / ref)
                    flagged.append((name, cls, k, meds[k] / ref,
                                    median([v[2] for v in vals])))
                print("   %3d-%-3d    %7d   %13.0f   %8.0f   %6.0f Hz%s" % (
                    k, k + args.bin, len(vals), meds[k],
                    median([v[1] for v in vals]),
                    median([v[2] for v in vals]), flag))
            # does the main frequency follow the speed?
            ratios = [float(r["speed"]) / float(r["%s_peak_hz" % chip])
                      for r in g if float(r["%s_peak_hz" % chip]) > 0]
            if ratios:
                hits = []
                for label, dist in SPATIAL:
                    near = sum(1 for x in ratios if abs(x - dist) < .08 * dist)
                    hits.append((near / len(ratios), label, dist))
                hits.sort(reverse=True)
                share, label, dist = hits[0]
                if share > .15:
                    print("   %.0f%% of windows vibrate once per %.1f mm of "
                          "travel (%s)" % (share * 100, dist, label))
            print()
    if flagged:
        print("Problem speeds (vibration well above the speeds around them):")
        for name, cls, k, ratio, hz in sorted(flagged, key=lambda f: -f[3]):
            print("   %s, along %s, %d-%d mm/s: %.1fx, main %.0f Hz" % (
                name, cls, k, k + args.bin, ratio, hz))
    else:
        print("No speed bin stands out from its neighbours by %.1fx." %
              args.flag)
    if args.png:
        plot(rows, chips, args)


def plot(rows, chips, args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(chips), 1, figsize=(9, 4 * len(chips)),
                             squeeze=False)
    for ax, chip in zip(axes[:, 0], chips):
        for cls, col in (("X", "tab:blue"), ("Y", "tab:red"),
                         ("diagonal", "tab:grey")):
            pts = [(float(r["speed"]), math.sqrt(sum(
                float(r["%s_rms_%s" % (chip, a)]) ** 2 for a in "xyz")))
                for r in rows if float(r["accel_frac"]) <= args.max_accel
                and classify(float(r["vx"]), float(r["vy"]),
                             float(r["speed"])) == cls]
            if pts:
                ax.scatter(*zip(*pts), s=3, alpha=.35, color=col, label=cls)
        ax.set_title("%s accelerometer: vibration at constant speed" % (
            {"y": "Bed", "x": "Toolhead"}.get(chip, chip)))
        ax.set_xlabel("speed (mm/s)")
        ax.set_ylabel("RMS (mm/s²)")
        ax.legend(markerscale=4)
    fig.tight_layout()
    fig.savefig(args.png, dpi=110)
    print("plot: %s" % args.png)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log")
    lg.add_argument("--socket", default=DEFAULT_SOCKET)
    lg.add_argument("--chips", default="y")
    lg.add_argument("--i-know-x-is-heavy", action="store_true")
    lg.add_argument("--window", type=float, default=.25)
    lg.add_argument("--any-state", action="store_true",
                    help="log moves outside a print too (bench sweeps)")
    lg.add_argument("--until-done", action="store_true",
                    help="stop when the print running now ends")
    lg.add_argument("--out", default="print_vibration_%s.csv"
                    % time.strftime("%Y%m%d-%H%M%S"))
    rp = sub.add_parser("report")
    rp.add_argument("file")
    rp.add_argument("--window", type=float, default=.25)
    rp.add_argument("--bin", type=int, default=5)
    rp.add_argument("--min-count", type=int, default=12)
    rp.add_argument("--flag", type=float, default=1.4)
    rp.add_argument("--max-accel", type=float, default=.3,
                    help="share of a window allowed to be accelerating")
    rp.add_argument("--png")
    args = ap.parse_args()
    (cmd_log if args.cmd == "log" else cmd_report)(args)


if __name__ == "__main__":
    main()
