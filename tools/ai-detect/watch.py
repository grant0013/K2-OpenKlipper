#!/opt/obico-venv/bin/python
"""Creality K2 print-failure watcher (Obico model, CPU) with a local vision-model second opinion.

Every INTERVAL s while Moonraker says printing, score one camera frame. Smooth the score
(EWM) and compare it with a slow per-print baseline so a constant false positive doesn't
build up. Warn once, then pause via Moonraker when the score stays high. Pauses at most
once per print; if you resume, the rest of that print is notify-only.

Second opinion (10-27, on Dell-AI): a local vision model (vlm.py, Qwen3-VL on
llama-server) is asked to confirm a warn before it is sent, so Obico false alarms
on first layers etc. are dropped, and it looks at a frame every VLM_EVERY s to catch
failures Obico doesn't see (detached part, blob, layer shift). If the model is down
or slow, warnings go out on Obico alone as before. It never pauses on its own.

Auto-pause: K2_AUTOPAUSE=1 in /etc/default/k2-detect (default 0 = notify only).
The K2 Moonraker reports a bogus print_duration (~uptime), so print time is
measured here from when this watcher first sees the file printing.
Logs: $STATE_DIRECTORY/scores.csv, hit frames in hits/ (raw copies in hits/raw/),
model verdicts in vlm.csv with their frames in vlm/.
  watch.py               run the service loop
  watch.py --simulate    replay a scripted run through the logic, no camera/pause/push
"""
import sys, os, time, json, csv, datetime, subprocess, urllib.request, threading, queue
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vlm

CAM = os.environ.get("K2_CAM", "http://127.0.0.1:8080/snapshot")
MR = os.environ.get("K2_MR", "http://127.0.0.1:7126")
ALERT_SSH = os.environ.get("K2_ALERT_SSH", "")
ALERT_KNOWN_HOSTS = os.environ.get("K2_ALERT_KNOWN_HOSTS", "/etc/k2-detect/known_hosts")
OUT = os.environ.get("STATE_DIRECTORY", "/var/lib/k2-detect")
KEY = os.path.join(os.environ.get("CREDENTIALS_DIRECTORY", "/etc/k2-detect"), "alert_key")
AUTOPAUSE = os.environ.get("K2_AUTOPAUSE", "0") == "1"
NAME = "K2"

INTERVAL = 10       # s between frames
THRESH = 0.08       # per-box threshold (same as Obico ml_api)
ALPHA = 0.3         # fast EWM of the frame score
BASE_ALPHA = 0.02   # slow per-print baseline (~8 min time constant)
SKIP_FIRST = 120    # s of print time ignored (purge line, first moves)
WARN_AT, WARN_N = 0.35, 3            # notify once when EWM stays above this
PAUSE_AT, PAUSE_N, PAUSE_X = 0.6, 6, 2.5  # pause: EWM >= 0.6 and >= 2.5x baseline for 6 frames (~1 min)
BLIND_N = 60        # ~10 min of failed frames while printing -> one "watcher blind" alert
VLM_EVERY = 180     # s between periodic vision-model looks while printing
VLM_CONFIRM = 0.6   # model confidence needed to confirm an Obico warn
VLM_ALONE, VLM_ALONE_N = 0.7, 2   # model alone: this confidence on N looks in a row
VLM_WAIT = 300      # s to wait for a confirmation before warning on Obico alone
QUIET = ("none", "nothing_printing")  # verdicts that never alert: flat dark first layers read as an empty bed


class Judge:
    """Pure decision logic: feed (state, file, print_duration, score), get actions back."""

    def __init__(self):
        self.reset(None)

    def reset(self, fname):
        self.file, self.ewm, self.base = fname, 0.0, 0.0
        self.warn_run = self.pause_run = 0
        self.warned = self.paused_once = self.muted = False
        self.was_paused = False

    def step(self, state, fname, duration, score):
        acts = []
        if fname != self.file:
            self.reset(fname)
        if state == "paused":
            self.was_paused = True
            return acts
        if state != "printing":
            return acts
        if self.was_paused:  # resumed (by you or the printer): do not fight it
            self.was_paused = False
            if self.paused_once:
                self.muted = True
            self.ewm = self.base = 0.0
            self.warn_run = self.pause_run = 0
        if duration < SKIP_FIRST or score is None:
            return acts
        self.ewm = ALPHA * score + (1 - ALPHA) * self.ewm
        alarm = self.ewm >= WARN_AT
        if not alarm:
            self.base = BASE_ALPHA * score + (1 - BASE_ALPHA) * self.base
        self.warn_run = self.warn_run + 1 if alarm else 0
        hot = self.ewm >= PAUSE_AT and self.ewm >= PAUSE_X * self.base
        self.pause_run = self.pause_run + 1 if hot else 0
        if self.warn_run >= WARN_N and not self.warned:
            self.warned = True
            acts.append("warn")
        if self.pause_run >= PAUSE_N and not self.paused_once and not self.muted:
            self.paused_once = True
            acts.append("pause")
        return acts


def http(url, data=None, timeout=10):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout).read()


def notify(title, body):
    """Send {"title", "body"} as JSON on stdin to K2_ALERT_SSH (user@host), whose
    authorized_keys entry should force the command that delivers the alert.
    Unset = alerts are only logged."""
    print(f"ALERT: {title} | {body}", flush=True)
    if not ALERT_SSH:
        return
    try:
        r = subprocess.run(
            ["ssh", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
             "-o", "UserKnownHostsFile=" + ALERT_KNOWN_HOSTS, "-o", "ConnectTimeout=10",
             "-i", KEY, ALERT_SSH],
            input=json.dumps({"title": title, "body": body}), text=True,
            capture_output=True, timeout=60)
        print(f"notify rc={r.returncode} {r.stdout.strip()} {r.stderr.strip()[-200:]}", flush=True)
    except Exception as e:
        print("notify error:", e, flush=True)


def pause():
    try:
        http(MR + "/printer/print/pause", data=b"")
        return True
    except Exception as e:
        print("pause error:", e, flush=True)
        return False


class Second:
    """Vision-model checks, one at a time on a worker thread; the main loop polls."""

    def __init__(self):
        self.jobs, self.done, self.busy = queue.Queue(), queue.Queue(), False
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, kind, jpeg, ctx):
        if self.busy:
            return False
        self.busy = True
        self.jobs.put((kind, jpeg, ctx))
        return True

    def _run(self):
        while True:
            kind, jpeg, ctx = self.jobs.get()
            try:
                v = vlm.check(jpeg)
            except Exception as e:
                v = {"error": str(e)[:200]}
            self.done.put((kind, jpeg, ctx, v))
            self.busy = False

    def results(self):
        out = []
        while not self.done.empty():
            out.append(self.done.get())
        return out


def log_verdict(kind, jpeg, ctx, v):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag = "error" if "error" in v else f"{v['status']}_{v['problem']}"
    os.makedirs(OUT + "/vlm", exist_ok=True)
    with open(f"{OUT}/vlm/{now.replace(' ', '_').replace(':', '')}_{kind}_{tag}.jpg", "wb") as f:
        f.write(jpeg)
    with open(OUT + "/vlm.csv", "a", newline="") as f:
        csv.writer(f).writerow([now, kind, ctx["file"], f"{ctx['prog']:.3f}", f"{ctx['ewm']:.2f}",
                                v.get("status", "error"), v.get("problem", ""),
                                f"{v.get('confidence', 0):.2f}", v.get("secs", ""),
                                v.get("reason", v.get("error", ""))])
    print(f"{now} vlm {kind}: {v}", flush=True)


def simulate():
    j = Judge()
    run = [("printing", 60, 0.9)] * 3 + [("printing", 200, 0.0)] * 30 + \
          [("printing", 500, 0.1)] * 20 + [("printing", 700, 0.8)] * 12 + \
          [("paused", 800, None)] * 3 + [("printing", 900, 0.8)] * 20
    for i, (st, dur, sc) in enumerate(run):
        acts = j.step(st, "sim.gcode", dur, sc)
        if acts or i % 10 == 0:
            print(f"{i:3d} {st:8s} dur={dur} score={sc} ewm={j.ewm:.2f} base={j.base:.2f} {acts}")


def main():
    sys.path.insert(0, "/opt/obico-server/ml_api")
    import cv2, numpy as np
    from lib.detection_model import load_net, detect
    md = "/opt/obico-server/ml_api/model/"
    net = load_net(md + "model.cfg", md + "model.meta", "/model_cache/ml_api/onnx/model-weights.onnx")
    os.makedirs(OUT + "/hits/raw", exist_ok=True)
    print(f"k2-detect up, autopause={'ON' if AUTOPAUSE else 'OFF'}, "
          f"vision model {'up' if vlm.healthy() else 'DOWN'} at {vlm.URL}", flush=True)
    j, blind, blind_sent = Judge(), 0, False
    seen_file, seen_start = None, time.time()
    sec = Second()
    v_file, pending, last_look, alone_run, alone_sent, last_v = None, None, 0.0, 0, set(), None

    while True:
        try:
            st = json.loads(http(MR + "/printer/objects/query?print_stats&virtual_sdcard"))["result"]["status"]
            ps = st["print_stats"]
            state, fname = ps["state"], ps["filename"]
            if fname != seen_file or state not in ("printing", "paused"):
                seen_file = fname if state in ("printing", "paused") else None
                seen_start = time.time()
            dur = time.time() - seen_start
            prog = st["virtual_sdcard"]["progress"]
        except Exception as e:
            print("moonraker error:", e, flush=True)
            time.sleep(30)
            continue
        if state not in ("printing", "paused"):
            j.step(state, fname, dur, None)
            blind, blind_sent = 0, False
            time.sleep(30)
            continue

        if fname != v_file:
            v_file, pending, last_look, alone_run, alone_sent, last_v = fname, None, 0.0, 0, set(), None

        score, dets, img, raw = None, [], None, None
        if state == "printing" and dur >= SKIP_FIRST:
            try:
                raw = http(CAM)
                img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                dets = detect(net, img, thresh=THRESH)
                score, blind = sum(d[1] for d in dets), 0
            except Exception as e:
                blind += 1
                print("camera/model error:", e, flush=True)
                if blind >= BLIND_N and not blind_sent:
                    blind_sent = True
                    notify(NAME + " watcher is blind",
                           f"No camera frames for ~{BLIND_N * INTERVAL // 60} min while printing {fname}. "
                           "Failure detection is not covering this print.")

        acts = j.step(state, fname, dur, score)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if score is not None:
            with open(OUT + "/scores.csv", "a", newline="") as f:
                csv.writer(f).writerow([now, state, fname, f"{prog:.3f}", len(dets), f"{score:.2f}",
                                        f"{max([d[1] for d in dets], default=0):.2f}",
                                        f"{j.ewm:.2f}", f"{j.base:.2f}", "+".join(acts)])
        hit = None
        if dets:
            for _, c, (x, y, w, h) in dets:
                cv2.rectangle(img, (int(x-w/2), int(y-h/2)), (int(x+w/2), int(y+h/2)), (0, 0, 255), 2)
                cv2.putText(img, f"{c:.2f}", (int(x-w/2), int(y-h/2)-4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            stamp = now.replace(" ", "_").replace(":", "")
            hit = f"{OUT}/hits/{stamp}_s{score:.2f}.jpg"
            cv2.imwrite(hit, img)
            with open(f"{OUT}/hits/raw/{stamp}_s{score:.2f}.jpg", "wb") as f:
                f.write(raw)

        where = f"{fname} at {prog * 100:.0f}%"
        ctx = {"file": fname, "prog": prog, "ewm": j.ewm, "where": where}
        obico_warn = (NAME + ": possible print problem",
                      f"{where}. AI score {j.ewm:.2f} and rising (pauses at {PAUSE_AT}). Worth a look in Mainsail.")

        # Vision-model verdicts that came back since the last frame
        for kind, jpeg, vctx, v in sec.results():
            log_verdict(kind, jpeg, vctx, v)
            if vctx["file"] != fname or state not in ("printing", "paused"):
                continue
            if "error" not in v:
                last_v = v
            if kind == "confirm" and pending:
                pending = None
                if "error" in v:
                    notify(*obico_warn)
                elif (v["status"] == "failure" and v["confidence"] >= VLM_CONFIRM
                      and v["problem"] not in QUIET):
                    notify(NAME + f": print problem ({v['problem'].replace('_', ' ')})",
                           f"{vctx['where']}. Obico score {vctx['ewm']:.2f} and the vision check agrees: "
                           f"{v['reason']} Check the camera in Mainsail.")
                elif v["status"] == "ok":
                    print(f"{now} warn suppressed: vision check says ok ({v['reason']})", flush=True)
                else:
                    notify(obico_warn[0], obico_warn[1] + f" Vision check unsure: {v['reason']}")
            elif kind == "look" and "error" not in v:
                bad = (v["status"] == "failure" and v["confidence"] >= VLM_ALONE
                       and v["problem"] not in QUIET)
                alone_run = alone_run + 1 if bad else 0
                if alone_run >= VLM_ALONE_N and v["problem"] not in alone_sent:
                    alone_sent.add(v["problem"])
                    notify(NAME + f": vision check sees a problem ({v['problem'].replace('_', ' ')})",
                           f"{vctx['where']}. {v['reason']} (seen on {alone_run} looks in a row; "
                           f"Obico score {vctx['ewm']:.2f}). Check the camera in Mainsail.")

        # Hold an Obico warn until the vision model has had a look (or give up waiting)
        if "warn" in acts:
            pending = {"t": time.time(), "sent": False, "jpeg": raw, "ctx": ctx}
            acts = [a for a in acts if a != "warn"]
        if pending and not pending["sent"] and pending["jpeg"]:
            pending["sent"] = sec.submit("confirm", pending["jpeg"], pending["ctx"])
        if pending and time.time() - pending["t"] > VLM_WAIT:
            pending = None
            notify(obico_warn[0], obico_warn[1] + " (vision check did not answer in time)")
        # Periodic look
        if raw and state == "printing" and time.time() - last_look >= VLM_EVERY and not pending:
            if sec.submit("look", raw, ctx):
                last_look = time.time()
        if "pause" in acts:
            pending = None  # the sustained alarm says it all
            seen = f" Last vision check: {last_v['status']}, {last_v['reason']}" if last_v else ""
            if AUTOPAUSE:
                ok = pause()
                notify(NAME + " PAUSED: possible print failure" if ok else NAME + " failure: PAUSE FAILED",
                       f"{where}. AI score {j.ewm:.2f} (pause level {PAUSE_AT}) for ~{PAUSE_N * INTERVAL}s. "
                       + ("Check the camera in Mainsail, then resume or cancel. If you resume, "
                          "it won't auto-pause again this print." if ok else
                          "Moonraker refused the pause. Check the printer now.") + seen)
            else:
                notify(NAME + ": likely print failure (auto-pause OFF)",
                       f"{where}. AI score {j.ewm:.2f} for ~{PAUSE_N * INTERVAL}s. Check the camera in Mainsail."
                       + seen)
        if score is not None:
            print(f"{now} {state} {len(dets)} boxes score={score:.2f} ewm={j.ewm:.2f} "
                  f"base={j.base:.2f} {acts or ''}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    simulate() if "--simulate" in sys.argv else main()
