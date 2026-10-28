#!/usr/bin/env python3
# Offline look at a PA_CAL_STATIC capture (PA_CAL_STATIC ... CAPTURE=<name>
# writes <name>_pressure.csv and <name>_extruder.csv to the pa_logs folder).
#
#   python3 pa_static_offline.py <dir>/<name> [--skip 0.005] [--fit 0.4]
#
# Reports the gauge's sample gaps, then fits pressure = A + B*exp(-t/tau) to
# every constant-flow segment on its own and prints tau (the pressure advance
# a step implies) for the rising (slow->fast) and falling (fast->slow) steps:
# mean, spread and fit error, so settings (raw vs processed gauge, window
# period, flows, segment time) can be compared without the printer. pa_tester
# itself fits all steps of one direction with a shared tau; a large spread
# here is why its joint fit fails.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse, csv, math, statistics


def load(base):
    samples = []
    with open(base + '_pressure.csv') as f:
        for row in csv.DictReader(f):
            samples.append((float(row['print_time']), float(row['raw'])))
    moves = []
    with open(base + '_extruder.csv') as f:
        for row in csv.DictReader(f):
            moves.append((float(row['print_time']), float(row['move_t']),
                          float(row['start_v']), float(row['accel'])))
    samples.sort()
    moves.sort()
    return samples, moves


def gap_report(samples):
    dts = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
    if not dts:
        return
    nominal = statistics.median(dts)
    gaps = [d for d in dts if d > 3 * nominal]
    span = samples[-1][0] - samples[0][0]
    print("samples %d over %.2f s: nominal %.3f ms, %d gaps (mean %.1f ms, "
          "max %.1f ms), %.1f%% of the time not sampled"
          % (len(samples), span, nominal * 1000., len(gaps),
             statistics.mean(gaps) * 1000. if gaps else 0.,
             max(gaps) * 1000. if gaps else 0.,
             100. * sum(gaps) / span if span else 0.))


def cv_segments(moves):
    """Constant-velocity extruder moves with the previous cruise velocity
    (Klipper stores accel, cruise and decel as separate pieces)."""
    segs = []
    prev_cruise = None
    for t, mt, v, a in moves:
        if a == 0. and mt > 0.05:
            segs.append((t, t + mt, v, prev_cruise))
            prev_cruise = v
    return segs


def fit_exp(ts, ys):
    """Least-squares A + B*exp(-C*t) over a grid of C, refined; returns
    (tau, normalized mse)."""
    def solve(C):
        e = [math.exp(-C * t) for t in ts]
        n = len(ts)
        se, see = sum(e), sum(x * x for x in e)
        sy, sey = sum(ys), sum(x * y for x, y in zip(e, ys))
        det = n * see - se * se
        if abs(det) < 1e-12:
            return None
        A = (see * sy - se * sey) / det
        B = (n * sey - se * sy) / det
        r = sum((y - A - B * x) ** 2 for x, y in zip(e, ys))
        return r, A, B
    rng = (max(ys) - min(ys)) or 1.
    best = None
    C = 1.
    while C < 1000.:
        s = solve(C)
        if s and (best is None or s[0] < best[0]):
            best = (s[0], C)
        C *= 1.05
    if best is None:
        return None, None
    lo, hi = best[1] / 1.05, best[1] * 1.05
    for _ in range(40):
        m1, m2 = lo + (hi - lo) / 3., hi - (hi - lo) / 3.
        if solve(m1)[0] < solve(m2)[0]:
            hi = m2
        else:
            lo = m1
    C = 0.5 * (lo + hi)
    r = solve(C)[0]
    return 1. / C, r / len(ts) / rng ** 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('base')
    ap.add_argument('--skip', type=float, default=0.005,
                    help="seconds skipped after each step (filter settle)")
    ap.add_argument('--fit', type=float, default=0.4,
                    help="seconds of each segment fitted")
    args = ap.parse_args()
    samples, moves = load(args.base)
    gap_report(samples)
    times = [s[0] for s in samples]
    results = {'rising': [], 'falling': []}
    import bisect
    for start, end, v, prev_v in cv_segments(moves)[1:]:
        if prev_v is None or abs(v - prev_v) < 0.2:
            continue
        kind = 'rising' if v > prev_v else 'falling'
        lo = bisect.bisect_left(times, start + args.skip)
        hi = bisect.bisect_right(times, min(end, start + args.fit))
        win = samples[lo:hi]
        if len(win) < 20:
            continue
        t0 = win[0][0]
        tau, mse = fit_exp([s[0] - t0 for s in win], [s[1] for s in win])
        if tau is not None:
            results[kind].append((tau, mse, len(win)))
    for kind, rows in results.items():
        if not rows:
            print("%s: no segments" % kind)
            continue
        taus = [r[0] for r in rows]
        print("%-8s n=%2d  tau mean %.4f  median %.4f  sd %.4f  "
              "(min %.4f max %.4f)  mse median %.1e"
              % (kind, len(rows), statistics.mean(taus),
                 statistics.median(taus),
                 statistics.pstdev(taus), min(taus), max(taus),
                 statistics.median(r[1] for r in rows)))


if __name__ == '__main__':
    main()
