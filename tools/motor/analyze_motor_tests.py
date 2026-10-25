"""Summarise a run_motor_tests.py run: motor tracking error from the 0x15
snapshots and head ringing after each stop from the accelerometer.
Usage: analyze_motor_tests.py <label> [<label2> ...]
"""
import csv, json, os, struct, sys
import numpy as np
MM_PER_RAD = 40.0 / (2 * np.pi)       # assumed 20T GT2 pulley

def motor_stats(snaps, t0):
    errs, moving = [], 0
    for t, hexs in snaps:
        if t < t0:
            continue
        b = bytes.fromhex(hexs.replace(" ", ""))
        n = b[5]
        recs = np.array([struct.unpack("<4f", b[6 + 16 * i:22 + 16 * i])
                         for i in range(n)])
        if np.ptp(recs[:, 0]) < 1e-4:            # target not moving
            continue
        moving += 1
        errs.extend((recs[:, 0] - recs[:, 1]).tolist())
    e = np.abs(np.array(errs)) * MM_PER_RAD * 1000   # microns
    if not len(e):
        return moving, float("nan"), float("nan")
    return moving, float(np.sqrt(np.mean(e ** 2))), float(e.max())

def ring_stats(path, axis, v, a):
    """Vibration above 35 Hz (frame/toolhead resonance, not the commanded
    acceleration): RMS over the whole set and the strongest frequency."""
    rows = np.loadtxt(path, delimiter=",", comments="#")
    t, acc = rows[:, 0], rows[:, 1 if axis == "X" else 2]
    dt = np.median(np.diff(t))
    sp = np.fft.rfft(acc - acc.mean())
    f = np.fft.rfftfreq(len(acc), dt)
    hp = sp.copy()
    hp[f < 35] = 0
    vib = np.fft.irfft(hp, len(acc))
    band = (f > 35) & (f < 0.45 / dt)
    peak = f[band][np.argmax(np.abs(sp[band]))]
    return float(np.sqrt(np.mean(vib ** 2))), float(peak)

for label in sys.argv[1:]:
    d = os.path.expanduser("~/K2_backups/motor/" + label)
    snaps = json.load(open(d + "/motor_snaps.json"))
    print("== %s" % label)
    print("%-15s %6s %10s %10s %12s %8s" % ("test", "snaps", "rms um",
                                           "max um", "vib>35Hz", "peak Hz"))
    for name, r in snaps.items():
        axis, v, a = name.split("_")[0], int(name.split("_v")[1].split("_")[0]), int(name.split("_a")[1])
        m, rms, mx = motor_stats(r["snaps"], r["t0"])
        ring, peak = ring_stats("%s/lis2dw_k2-%s_%s.csv" % (d, label, name),
                                axis, v, a)
        print("%-15s %6d %10.1f %10.1f %12.0f %8.1f" % (name, m, rms, mx,
                                                       ring, peak))
