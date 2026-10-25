"""K2 motor tracking/vibration test: back-and-forth moves while polling the
motor position snapshots (opcode 0x15) and recording the head accelerometer.
Usage: run_motor_tests.py <label>     -> ~/K2_backups/motor/<label>/
"""
import json, os, sys, time, urllib.parse, urllib.request
MR = os.environ.get("K2_MOONRAKER", "http://k2.local:7126")
LABEL = sys.argv[1]
OUT = os.path.expanduser("~/K2_backups/motor/" + LABEL)
os.makedirs(OUT, exist_ok=True)

def post(script, timeout=60):
    data = urllib.parse.urlencode({"script": script}).encode()
    urllib.request.urlopen(MR + "/printer/gcode/script", data=data,
                           timeout=timeout).read()

def store(n=1000):
    with urllib.request.urlopen(MR + "/server/gcode_store?count=%d" % n,
                                timeout=10) as r:
        return json.load(r)["result"]["gcode_store"]

CONFIGS = []
for axis, addr in (("X", 129), ("Y", 130)):
    for v in (200, 300):
        for a in (5000, 20000):
            CONFIGS.append((axis, addr, v, a))

post("G90\nG1 Z10 F600\nG1 X130 Y125 F12000\nM400")
results = {}
for axis, addr, v, a in CONFIGS:
    name = "%s_v%d_a%d" % (axis, v, a)
    if axis == "X":
        ends = ("X90 Y125", "X170 Y125")
    else:
        ends = ("X130 Y85", "X130 Y165")
    moves = "\n".join("G1 %s F%d\nG4 P250" % (ends[i % 2], v * 60)
                      for i in range(6))
    post("SET_VELOCITY_LIMIT ACCEL=%d\nG1 %s F12000\nM400" % (a, ends[1]))
    post("ACCELEROMETER_MEASURE CHIP=lis2dw_k2 NAME=%s_%s" % (LABEL, name))
    t0 = time.time()
    post(moves)                       # queues the motion and returns
    est = 6 * (80.0 / v + v / a + 0.25) + 0.5
    n = 0
    while time.time() - t0 < est:
        post("MOTOR_BUS_SEND ADDR=%d PAYLOAD=0015 READ_LEN=400 TIMEOUT=0.3"
             % addr)
        n += 1
    post("M400")
    post("ACCELEROMETER_MEASURE CHIP=lis2dw_k2 NAME=%s_%s" % (LABEL, name))
    snaps = [(g["time"], g["message"].split("resp ")[1].strip())
             for g in store() if g["time"] >= t0 - 1
             and "resp f7 %02x a4 00 15" % addr in g["message"]]
    results[name] = {"t0": t0, "polls": n, "snaps": snaps}
    print(name, "polls", n, "snapshots", len(snaps), flush=True)
post("SET_VELOCITY_LIMIT ACCEL=20000\nG1 X130 Y125 F12000\nM400")
json.dump(results, open(OUT + "/motor_snaps.json", "w"))
print("saved", OUT)
