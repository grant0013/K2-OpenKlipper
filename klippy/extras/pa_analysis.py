#!/usr/bin/env python3
"""Pressure advance from a PA_CAL_RUN log (nozzle strain gauge).

Shared by the printer (pa_pressure_log runs it as a subprocess so Klipper's
reactor never stalls) and the Mac (analyze_pa.py adds plots).
Usage: pa_analysis.py <dir> <name>   -> prints a JSON result

The K2 gauge arrives in 50 ms windows with gaps between them, so each PA
value repeats its slow->fast and fast->slow flow steps several times and
the samples around every step are overlaid by time since the step
(equivalent-time sampling). With PA right, nozzle pressure (outflow) tracks
the commanded flow; too little lags, too much overshoots and undershoots
(CNC Kitchen PATuner). PA is picked where the net volume error after the
up and down steps balances, from a line through every PA value tested.
Quality checks decide whether the result is safe to save automatically.
"""
import csv, json, sys
import numpy as np

STEP_WIN = 0.45       # seconds scored after each flow step
SETTLE = 0.02         # s after a step before over/undershoot is judged
FOLD_BIN = 0.005      # s: bin width for the overlaid step response
REVERSAL_FILL = 0.02  # s: bridge the commanded-rate dips at wiggle reversals
OUTLIER = 30000       # counts from the local median: decode glitches
MIN_SNR = 8.0         # step size / resting noise needed to trust a result
MAX_DRY = 0.25        # dry-wiggle disturbance as a fraction of the step
MIN_STEP = 20000      # counts: a real flow step on the K2 is ~250k
MIN_FILL = 0.3        # fraction of step-response bins that must have data


def _rows(path):
    with open(path) as f:
        return list(csv.reader(f))[1:]


def load(directory, name):
    p = np.array([[float(a), float(b)] for a, b in
                  _rows("%s/%s_pressure.csv" % (directory, name))])
    moves = [tuple(float(x) for x in r) for r in
             _rows("%s/%s_extruder.csv" % (directory, name))]
    marks = [(float(t), lab) for t, lab in
             _rows("%s/%s_marks.csv" % (directory, name))]
    return p, moves, marks


def commanded_rate(t, moves):
    """Extruder velocity (mm/s of filament) at each time in t."""
    v = np.zeros_like(t)
    for pt, mt, sv, ac, _ in moves:
        i0, i1 = np.searchsorted(t, [pt, pt + mt])
        v[i0:i1] = sv + ac * (t[i0:i1] - pt)
    return v


def clean(t, raw):
    """Drop decode glitches: samples far from the local median."""
    n = 31
    if len(raw) <= n:
        return t, raw
    padded = np.pad(raw, n // 2, mode="edge")
    med = np.median(
        np.lib.stride_tricks.sliding_window_view(padded, n), axis=1)
    ok = np.abs(raw - med) < OUTLIER
    return t[ok], raw[ok]


def fold(rel, vals):
    """Mean of vals in FOLD_BIN bins of rel over (0, STEP_WIN)."""
    edges = np.arange(0., STEP_WIN + 1e-9, FOLD_BIN)
    idx = np.digitize(rel, edges) - 1
    out = np.full(len(edges) - 1, np.nan)
    for b in range(len(out)):
        sel = idx == b
        if sel.any():
            out[b] = vals[sel].mean()
    return edges[:-1] + FOLD_BIN / 2, out


def analyse(p, moves, marks):
    order = np.argsort(p[:, 0])
    t, raw = clean(p[order, 0], p[order, 1])

    blocks = []
    ends = {lab[3:]: tm for tm, lab in marks if lab.startswith("end")}
    for t0, lab in marks:
        if not lab.startswith("k") or lab[1:] not in ends:
            continue
        k, t1 = lab[1:], ends[lab[1:]]
        base_sel = (t > t0 - 1.2) & (t < t0 - 0.1)
        post_sel = (t > t1 + 0.5) & (t < t1 + 1.4)
        sel = (t >= t0 - 0.3) & (t <= t1 + 0.8)
        if base_sel.sum() < 10 or sel.sum() < 100:
            continue
        tb = t[sel]
        # Gauge drift: straight-line baseline between the rests either side
        b0 = np.median(raw[base_sel])
        if post_sel.sum() >= 10:
            b1 = np.median(raw[post_sel])
            tm0, tm1 = np.median(t[base_sel]), np.median(t[post_sel])
            pb = raw[sel] - (b0 + (b1 - b0) * (tb - tm0) / (tm1 - tm0))
        else:
            pb = raw[sel] - b0

        # Commanded rate on a dense grid (the moves are known exactly),
        # with the reversal dips filled, then read at the gauge samples
        dt = 0.0005
        td = np.arange(t0 - 0.3, t1 + 0.8, dt)
        vd = commanded_rate(td, moves)
        nfill = max(3, int(REVERSAL_FILL / dt) | 1)
        win = np.lib.stride_tricks.sliding_window_view
        # Closing (max then min) fills the dips without widening the
        # fast sections
        vfd = win(np.pad(vd, nfill // 2, mode="edge"), nfill).max(axis=1)
        vfd = win(np.pad(vfd, nfill // 2, mode="edge"), nfill).min(axis=1)
        vmax = vfd.max()
        if vmax <= 0:
            continue
        # Flow steps: where the filled rate crosses midway between levels
        slow = np.median(vfd[(vfd > 0.05 * vmax) & (vfd < 0.6 * vmax)])
        above = vfd > 0.5 * (slow + vmax)
        ups = list(td[1:][~above[:-1] & above[1:]])
        downs = list(td[1:][above[:-1] & ~above[1:]])
        vf = np.interp(tb, td, vfd)
        moving = vf > 0.05 * vmax
        if moving.sum() < 50 or not ups or not downs:
            continue
        if np.corrcoef(pb[moving], vf[moving])[0, 1] < 0:
            pb = -pb
        steady = moving.copy()
        for e in ups + downs:
            steady &= ~((tb > e - 0.05) & (tb < e + STEP_WIN))
        # Melt pressure is not proportional to flow (PETG at the slow rate
        # already reads ~70% of a line through zero), so take each settled
        # level as measured and join them with a straight line
        mid = 0.5 * (slow + vmax)
        fast_sel, slow_sel = steady & (vf > mid), steady & (vf <= mid)
        if fast_sel.sum() < 20 or slow_sel.sum() < 20:
            continue
        p_fast = float(np.median(pb[fast_sel]))
        p_slow = float(np.median(pb[slow_sel]))
        g = (p_fast - p_slow) / (vmax - slow)
        model = p_slow + g * (vf - slow)
        step = p_fast - p_slow
        resid = pb - model

        def overlay(events):
            rel, val = [], []
            for e in events:
                w = (tb > e) & (tb < e + STEP_WIN)
                rel.append(tb[w] - e)
                val.append(resid[w])
            return fold(np.concatenate(rel), np.concatenate(val))

        def window_mean(events):
            w = np.zeros(len(tb), bool)
            for e in events:
                w |= (tb > e) & (tb < e + STEP_WIN)
            return float(resid[w].mean()) if w.any() else float("nan")

        # Net volume error after the steps: a lagging nozzle leaves the up
        # steps short and the down steps long. For a first-order nozzle
        # this is exactly linear in PA and zero at the right value.
        balance = (window_mean(ups) - window_mean(downs)) / max(step, 1e-9)
        bt, up_r = overlay(ups)
        _, dn_r = overlay(downs)
        both = np.concatenate([up_r, dn_r])
        have = ~np.isnan(both)
        late = bt > SETTLE
        over = np.nanmax(up_r[late]) if np.any(~np.isnan(up_r[late])) \
            else np.nan
        under = -np.nanmin(dn_r[late]) if np.any(~np.isnan(dn_r[late])) \
            else np.nan
        noise = float(np.std(pb[(tb >= t0 - 0.3) & (tb < t0)])) \
            if ((tb >= t0 - 0.3) & (tb < t0)).sum() > 5 else float("nan")
        blocks.append(dict(
            k=float(k), balance=balance,
            err=float(np.sqrt(np.mean(both[have] ** 2)) / max(step, 1e-9))
            if have.any() else float("nan"),
            under=float(max(0., under) / step), over=float(max(0., over)
                                                         / step),
            gain=g, step=float(step), noise=noise,
            p_slow=p_slow, p_fast=p_fast,
            snr=float(step / max(noise, 1e-9)),
            fill=float(have.mean()), steps=len(ups) + len(downs),
            t=tb - t0, p=pb, model=model, bt=bt, up_r=up_r, dn_r=dn_r))

    # Dry wiggle (motion, no extrusion): how much the head's own motion
    # shows on the gauge beyond resting noise
    dry = None
    for t0, lab in marks:
        if lab == "dry":
            sel = (t > t0) & (t < t0 + 2.2)
            base = (t > t0 - 1.2) & (t < t0 - 0.1)
            if sel.sum() > 30 and base.sum() > 10:
                d = raw[sel] - np.median(raw[base])
                excess = np.var(d) - np.var(raw[base])
                dry = float(np.sqrt(max(0., excess)))
    return blocks, dry


def recommend(blocks, dry):
    blocks = sorted(blocks, key=lambda b: b["k"])
    # A block whose settled fast pressure is far off the rest was logged
    # before the melt had settled (usually the first): leave it out
    pf = np.median([b["p_fast"] for b in blocks])
    settled = [b for b in blocks if abs(b["p_fast"] - pf) < 0.15 * abs(pf)]
    skipped = [b["k"] for b in blocks if b not in settled]
    if len(settled) >= 3:
        blocks = settled
    ks = np.array([b["k"] for b in blocks])
    bal = np.array([b["balance"] for b in blocks])
    problems = []
    fitted = r2 = None
    if len(blocks) >= 3:
        slope, icept = np.polyfit(ks, bal, 1)
        fit = slope * ks + icept
        r2 = 1. - np.sum((bal - fit) ** 2) / max(
            np.sum((bal - bal.mean()) ** 2), 1e-12)
        if slope <= 0:
            problems.append("step balance does not rise with PA")
        else:
            fitted = float(-icept / slope)
        if r2 < 0.9:
            problems.append("step balance is noisy (R2 %.2f)" % r2)
    else:
        problems.append("too few PA values logged")
    if fitted is not None and not ks[0] <= fitted <= ks[-1]:
        problems.append("best value is outside the tested range")
    best = float(ks[np.argmin(np.abs(ks - fitted))]) if fitted is not None \
        else None
    ok = [b for b in blocks if b["under"] < 0.05]
    no_under = ok[-1]["k"] if ok else None
    snr = float(np.median([b["snr"] for b in blocks]))
    step = float(np.median([b["step"] for b in blocks]))
    fill = float(np.median([b["fill"] for b in blocks]))
    if not step >= MIN_STEP:
        problems.append("no flow step on the gauge (%.0f counts): is the "
                        "filament feeding?" % step)
    if not snr >= MIN_SNR:
        problems.append("gauge noise too high (step/noise %.1f < %.0f)"
                        % (snr, MIN_SNR))
    if dry is not None and dry / max(step, 1e-9) > MAX_DRY:
        problems.append("head motion shows on the gauge (%.0f%% of a step)"
                        % (100 * dry / step))
    if fill < MIN_FILL:
        problems.append("too few gauge samples around the steps (%.0f%%)"
                        % (100 * fill))
    return dict(best=best, skipped=skipped,
                fitted=None if fitted is None else round(fitted, 4),
                r2=None if r2 is None else round(float(r2), 3),
                no_undershoot=no_under,
                snr=round(snr, 1), step=round(step), fill=round(fill, 2),
                dry_fraction=None if dry is None else round(dry / step, 3),
                reliable=not problems, problems=problems,
                table=[dict(k=b["k"], balance=round(b["balance"], 4),
                            err=round(b["err"], 4),
                            over=round(b["over"], 3),
                            under=round(b["under"], 3),
                            snr=round(b["snr"], 1),
                            fill=round(b["fill"], 2)) for b in blocks])


def run(directory, name):
    p, moves, marks = load(directory, name)
    blocks, dry = analyse(p, moves, marks)
    if not blocks:
        return dict(reliable=False, problems=["no PA blocks found in log"])
    return recommend(blocks, dry)


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2])))
