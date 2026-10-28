# Max volumetric flow test for the K2 (run on the Klipper host): parks over
# the purge chute, steps the flow up 3 s at a time and logs the nozzle gauge.
#   python3 flow_test.py <SLOT> <TEMP> <name> [first flow] [last flow]
# then: python3 flow_analyse.py <pa_logs dir>/<name>
# This file may be distributed under the terms of the GNU GPLv3 license.
#   python3 flow_test.py <SLOT> <TEMP> <name>
import json, sys, time, urllib.request, urllib.parse
SLOT, TEMP, NAME = sys.argv[1], float(sys.argv[2]), sys.argv[3]
F0 = int(sys.argv[4]) if len(sys.argv) > 4 else 4
F1 = int(sys.argv[5]) if len(sys.argv) > 5 else 24
AREA = 2.405
def g(s, to=900):
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:7126/printer/gcode/script?script=" + urllib.parse.quote(s),
        method="POST"), timeout=to)
def wheel():
    g("CFS_QUERY CMD=MEASURING_WHEEL")
    d = json.load(urllib.request.urlopen("http://127.0.0.1:7126/server/gcode_store?count=3", timeout=5))["result"]["gcode_store"]
    for x in reversed(d):
        if "wheel_mm" in x["message"]:
            return float(x["message"].split("'wheel_mm': ")[1].split(",")[0])
    return None
def extrude(mm, flow):
    v = flow / AREA
    g("G1 E%.3f F%.1f\nM400\nG4 P400" % (mm, v * 60.))
try:
    homed = json.load(urllib.request.urlopen(
        "http://127.0.0.1:7126/printer/objects/query?toolhead",
        timeout=5))["result"]["status"]["toolhead"]["homed_axes"]
    if "z" not in homed:
        g("KZ_ALIGN_BOTTOM CONFIRM=1")
    if "x" not in homed or "y" not in homed:
        g("G28 X Y")
    g("G90\nM83\nM106 S0")
    g("_CFS_SAFE_Z\nCFS_PURGE_POSITION CONFIRM=1")
    g("M109 S%.0f" % TEMP)
    g("CFS_FEED_START SLOT=%s CONFIRM=1" % SLOT)
    extrude(15, 5)
    print("FLOW RAMP (3 s per step):")
    g("PA_LOG_START")
    for flow in range(F0, F1 + 1, 2):
        mm = flow / AREA * 3.
        g("PA_LOG_MARK LABEL=f%d" % flow)
        extrude(mm, flow)
        print("  %2d mm3/s: commanded %.1f mm" % (flow, mm))
    g("PA_LOG_STOP NAME=%s" % NAME)
finally:
    for s in ("M104 S0", "CFS_FEED_STOP", "CFS_PRELOAD ENABLE=1", "CFS_SLOT_APPLY SLOT=%s" % SLOT,
              "M106 S255", "NOZZLE_WIPE CONFIRM=1", "TEMPERATURE_WAIT SENSOR=extruder MAXIMUM=180",
              "NOZZLE_WIPE CONFIRM=1", "M107"):
        try: g(s)
        except Exception as e: print("cleanup", s, "->", str(e)[:120])
