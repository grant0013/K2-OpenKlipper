import json, os, struct, sys, time, urllib.request, urllib.parse
MR = os.environ.get("K2_MOONRAKER", "http://k2.local:7126")
def post(script):
    data = urllib.parse.urlencode({"script": script}).encode()
    urllib.request.urlopen(MR + "/printer/gcode/script", data=data, timeout=30).read()
def store(n):
    with urllib.request.urlopen(MR + "/server/gcode_store?count=%d" % n, timeout=10) as r:
        return json.load(r)["result"]["gcode_store"]
out = {}
for addr, name in ((129, "X"), (130, "Y")):
    res = {}
    for base in range(0, 256, 16):
        for i in range(base, base + 16):
            post("MOTOR_BUS_SEND ADDR=%d PAYLOAD=000602%02x" % (addr, i))
        msgs = [g["message"] for g in store(40) if "// sent" in g["message"]]
        msgs = msgs[-16:]
        for m in msgs:
            sent, resp = m.split("|")[0] if "|" in m else m, m
            lines = m.split("\n")
            s = lines[0].split("sent ")[1].split()
            pid = int(s[6], 16)
            r = lines[1].split("resp ")[1].strip()
            res[pid] = r
    out[name] = res
    post("MOTOR_BUS_SEND ADDR=%d PAYLOAD=000603" % addr)
json.dump(out, open(sys.argv[1], "w"), indent=1)
for name, res in out.items():
    good = 0
    for pid in sorted(res):
        b = bytes.fromhex(res[pid].replace(" ", ""))
        if len(b) == 10 and b[3:5] == b"\x00\x06":
            v = struct.unpack("<f", b[5:9])[0]; good += 1
    print(name, "responses", len(res), "float replies", good)
