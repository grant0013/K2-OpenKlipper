#!/opt/obico-venv/bin/python
"""Second opinion on a K2 camera frame from a local vision-language model.

The model (Qwen3-VL, llama.cpp llama-server on 127.0.0.1:8090, CPU) looks at one
chamber frame and answers in fixed JSON: is the print healthy, and if not, what
kind of failure. It is slow (tens of seconds a frame) so the watcher only asks
it to confirm an Obico alarm, plus a periodic look for failures Obico can't see.

  vlm.py image.jpg [...]   classify saved frames and print the verdicts
"""
import base64, json, os, sys, time, urllib.request

URL = os.environ.get("K2_VLM", "http://127.0.0.1:8090")
TIMEOUT = 180

PROBLEMS = ["none", "spaghetti", "detached_part", "blob_on_nozzle", "layer_shift",
            "warping", "knocked_over", "nothing_printing", "other"]

PROMPT = """This is a frame from the chamber camera of a Creality K2 3D printer, \
looking across the textured print bed (front-left corner, with the CREALITY logo, \
nearest the camera) towards the gantry. The print head with orange-red parts is \
usually at the top right or moving over the bed.

Judge only what you can see. Signs of a FAILED print: a tangle or nest of loose \
filament strands (spaghetti); a part that has come loose, tipped over or been \
dragged across the bed; a large blob of plastic stuck around the nozzle; layers \
visibly shifted sideways; corners peeling up from the bed; filament being \
extruded into the air with no part under it. A HEALTHY print looks like solid \
objects with clean layers sitting flat on the bed, or flat first-layer shapes. \
Small strings, a purge line, wipe marks, reflections, the printed logo and the \
texture of the bed are normal. If the bed is empty or the view is too dark or \
blurred to judge, say so.

Reply with JSON only. Keep "reason" to one short sentence (at most 15 words)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "failure", "unclear"]},
        "problem": {"type": "string", "enum": PROBLEMS},
        "confidence": {"type": "number"},
        "reason": {"type": "string", "maxLength": 120},
    },
    "required": ["status", "problem", "confidence", "reason"],
}


def check(jpeg, timeout=TIMEOUT):
    """Return {"status", "problem", "confidence", "reason", "secs"} or raise."""
    body = {
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": PROMPT},
        ]}],
        "temperature": 0,
        "max_tokens": 120,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "verdict", "schema": SCHEMA}},
    }
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t = time.time()
    resp = json.load(urllib.request.urlopen(req, timeout=timeout))
    out = json.loads(resp["choices"][0]["message"]["content"])
    out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0))))
    out["reason"] = str(out.get("reason", ""))[:200]
    out["secs"] = round(time.time() - t, 1)
    return out


def healthy():
    try:
        return json.load(urllib.request.urlopen(URL + "/health", timeout=5)).get("status") == "ok"
    except Exception:
        return False


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, "rb") as f:
            try:
                v = check(f.read())
            except Exception as e:
                print(f"{os.path.basename(path)}: ERROR {e}", flush=True)
                continue
        print(f"{os.path.basename(path)}: {v['status']} {v['problem']} {v['confidence']:.2f} "
              f"{v['secs']}s | {v['reason']}", flush=True)
