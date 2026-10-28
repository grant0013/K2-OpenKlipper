"""K2 speed/acceleration envelope: long back-and-forth moves on X, Y and the
diagonal at a grid of speeds and accelerations, recording the closed-loop
motors' tracking error (opcode 0x15 snapshots), vibration from the nozzle
accelerometer (lis2dw_k2) and any motor fault after each run. Run on the
Klipper host with the printer homed and the bed clear.

  python3 speed_envelope.py <label> [--speeds 200,300,400,500,600]
      [--accels 5000,10000,15000,20000] [--axes X,Y,D] [--moves 4]

Writes ~/k2-tuning/<label>/envelope.json and prints a table: per run the
tracking error RMS/max (um at the belt), vibration above 35 Hz (mm/s^2,
RMS), its peak frequency, whether the speed was reached over the stroke, and
motor faults. Motor addresses: X 129, Y 130 (both turn on X, Y and diagonal
moves of the CoreXY).
"""
import argparse, json, math, os, re, struct, time, urllib.parse, urllib.request
import numpy as np

MR = os.environ.get("K2_MOONRAKER", "http://127.0.0.1:7126")
MM_PER_RAD = 40.0 / (2 * math.pi)       # 20T GT2 pulley
STROKES = {"X": ("X30 Y130", "X230 Y130", 200.),
           "Y": ("X130 Y30", "X130 Y230", 200.),
           "D": ("X40 Y40", "X220 Y220", 254.6)}
MOTORS = {"X": (129, 130), "Y": (129, 130), "D": (129, 130)}


def post(script, timeout=120):
    data = urllib.parse.urlencode({"script": script}).encode()
    urllib.request.urlopen(MR + "/printer/gcode/script", data=data,
                           timeout=timeout).read()


def store(n=3000):
    with urllib.request.urlopen(MR + "/server/gcode_store?count=%d" % n,
                                timeout=10) as r:
        return json.load(r)["result"]["gcode_store"]


def motor_errors(snaps):
    """Tracking error (target - feedback) in um from 0x15 snapshots taken
    while the target was moving."""
    errs = []
    for hexs in snaps:
        b = bytes.fromhex(hexs.replace(" ", ""))
        if len(b) < 7:
            continue
        n = b[5]
        if len(b) < 6 + 16 * n:
            continue
        recs = np.array([struct.unpack("<4f", b[6 + 16 * i:22 + 16 * i])
                         for i in range(n)])
        if np.ptp(recs[:, 0]) < 1e-4:
            continue
        errs.extend((recs[:, 0] - recs[:, 1]).tolist())
    e = np.abs(np.array(errs)) * MM_PER_RAD * 1000.
    if not len(e):
        return None, None, 0
    return float(np.sqrt(np.mean(e ** 2))), float(e.max()), len(e)


def vibration(path, axis):
    rows = np.loadtxt(path, delimiter=",", comments="#")
    t = rows[:, 0]
    acc = (rows[:, 1] if axis == "X" else rows[:, 2] if axis == "Y"
           else np.hypot(rows[:, 1], rows[:, 2]))
    dt = np.median(np.diff(t))
    sp = np.fft.rfft(acc - acc.mean())
    f = np.fft.rfftfreq(len(acc), dt)
    hp = sp.copy()
    hp[f < 35] = 0
    vib = np.fft.irfft(hp, len(acc))
    band = (f > 35) & (f < 0.45 / dt)
    peak = f[band][np.argmax(np.abs(sp[band]))] if band.any() else 0.
    return float(np.sqrt(np.mean(vib ** 2))), float(peak)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--speeds", default="200,300,400,500,600")
    ap.add_argument("--accels", default="5000,10000,15000,20000")
    ap.add_argument("--axes", default="X,Y,D")
    ap.add_argument("--moves", type=int, default=4)
    a = ap.parse_args()
    out = os.path.expanduser("~/k2-tuning/" + a.label)
    os.makedirs(out, exist_ok=True)
    speeds = [float(s) for s in a.speeds.split(",")]
    accels = [float(s) for s in a.accels.split(",")]
    post("G90\nG1 Z10 F600\nG1 X130 Y130 F12000\nM400")
    post("MOTOR_CLEAR_FAULTS")
    results = []
    for axis in a.axes.split(","):
        start, end, length = STROKES[axis]
        for v in speeds:
            for acc in accels:
                name = "%s_v%d_a%d" % (axis, v, acc)
                # Reached cruise speed if the stroke covers accel + decel
                reached = length >= v * v / acc
                post("SET_VELOCITY_LIMIT VELOCITY=%d ACCEL=%d "
                     "MINIMUM_CRUISE_RATIO=0\nG1 %s F12000\nM400"
                     % (max(v, 300), acc, start))
                post("ACCELEROMETER_MEASURE CHIP=lis2dw_k2 NAME=%s_%s"
                     % (a.label, name))
                t0 = time.time()
                moves = "\n".join("G1 %s F%d\nG4 P200"
                                  % ((end, start)[i % 2], v * 60)
                                  for i in range(a.moves))
                post(moves)
                ta = min(v / acc, math.sqrt(length / acc))
                est = a.moves * (2 * ta + max(0., length - v * ta) / v
                                 + 0.2) + 0.5
                while time.time() - t0 < est:
                    for addr in MOTORS[axis]:
                        post("MOTOR_BUS_SEND ADDR=%d PAYLOAD=0015 "
                             "READ_LEN=400 TIMEOUT=0.3" % addr)
                post("M400")
                post("ACCELEROMETER_MEASURE CHIP=lis2dw_k2 NAME=%s_%s"
                     % (a.label, name))
                post("MOTOR_FAULTS")
                log = [g for g in store() if g["time"] >= t0 - 1]
                row = {"name": name, "axis": axis, "v": v, "accel": acc,
                       "reached": reached}
                for addr in MOTORS[axis]:
                    snaps = [g["message"].split("resp ")[1].strip()
                             for g in log if "resp f7 %02x a4 00 15" % addr
                             in g["message"]]
                    rms, mx, n = motor_errors(snaps)
                    row["m%d" % addr] = {"rms_um": rms, "max_um": mx,
                                         "samples": n}
                # MOTOR_FAULTS prints one line per motor, "X: ... (status
                # 00, error 00000000, warning 00000000)"; any error bit is a
                # fault
                faults = []
                for g in log:
                    if "Motor protection" not in g["message"]:
                        continue
                    for line in g["message"].splitlines():
                        m_err = re.search(r"error ([0-9a-f]{8})", line)
                        if m_err and int(m_err.group(1), 16):
                            faults.append(line.strip("/ "))
                row["faults"] = faults[-3:]
                csv = "/tmp/lis2dw_k2-%s_%s.csv" % (a.label, name)
                try:
                    row["vib_rms"], row["vib_peak_hz"] = vibration(csv, axis)
                except Exception as e:
                    row["vib_rms"], row["vib_peak_hz"] = None, None
                    row["vib_error"] = str(e)[:80]
                results.append(row)
                m = [row["m%d" % ad] for ad in MOTORS[axis]]
                print("%-14s reach %-3s  track rms %5s / max %5s um  "
                      "vib %6s mm/s2 @ %5s Hz  %s" % (
                          name, "yes" if reached else "no",
                          "%.0f" % max(x["rms_um"] or 0 for x in m),
                          "%.0f" % max(x["max_um"] or 0 for x in m),
                          "%.0f" % (row["vib_rms"] or 0),
                          "%.1f" % (row["vib_peak_hz"] or 0),
                          ("FAULT " + faults[-1][:60]) if faults else ""),
                      flush=True)
                json.dump(results, open(out + "/envelope.json", "w"),
                          indent=1)
                if faults:
                    print("stopping at the first motor fault")
                    post("SET_VELOCITY_LIMIT VELOCITY=800 ACCEL=20000")
                    return
    post("SET_VELOCITY_LIMIT VELOCITY=800 ACCEL=20000\n"
         "G1 X130 Y130 F12000\nM400")
    print("saved", out)


if __name__ == "__main__":
    main()
