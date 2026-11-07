#!/usr/bin/env python3
"""Hark Cam: MJPEG stream of the K2 chamber camera for Mainsail / AI tools.

The K2's USB (UVC) camera compresses to MJPEG itself, so ffmpeg copies its
frames without re-encoding and this serves them mjpg-streamer style:
  /?action=stream    multipart MJPEG
  /?action=snapshot  latest JPEG
Usage: harkcam.py [port] [device] [WxH] [fps]

Light on the printer's WiFi: every viewer shares ONE frame budget (fps frames
a second in total, streams and snapshots together), so a second browser tab
halves each tab's frame rate instead of doubling the data sent. A 720p frame
with the chamber light on is about 85 KB, so 15 fps for one viewer was about
1.3 MB/s, which slowed the printer's network badly enough for Klipper's
toolhead board to miss its moves (GitHub issue #4). At most max_streams
streams stay open; a new one closes the oldest, so switching a page to its
fullscreen camera never leaves it blank.

Settings (all optional) in /mnt/UDISK/printer_data_mainline/config/harkcam.cfg,
"key: value" lines, read at start; the command line wins over the file:
  size: 1280x720     fps: 10     max_streams: 2     mode: mjpeg

mode: webrtc hands the camera to Creality's own apps instead (cam_app, which
has the camera compress to H.264, and webrtc_local, which sends it over
WebRTC: about 51 KB/s a viewer against 800+ for MJPEG, measured by nur00 in
issue #4). Mainsail and Fluidd cannot talk to webrtc_local, so this serves
their "WebRTC (go2rtc)" camera type (a WebSocket on /api/ws) and translates.
webrtc_local never connects without the browser's candidates in the offer,
and an offer without them leaves a session running with no video and nobody
watching, so the WHEP form, which the test page uses, refuses such an offer
(that is what Mainsail's "WebRTC (MediaMTX)" type sends):
  OPTIONS /whep        204, no ICE servers (the printer is on the LAN)
  POST /whep           browser offer (application/sdp) -> webrtc_local's
                       base64 JSON call -> answer, 201 with a Location
  PATCH /whep/<id>     204: webrtc_local takes no trickled candidates
  DELETE /whep/<id>    200
  /                    a test page that plays the stream
Every viewer is its own session in webrtc_local and the work multiplies, so
max_streams viewers at most; one over that is told no, not given the oldest
one's place (closing a viewer's WebSocket would not end its session).
webrtc_local only sends video, so other media in the offer (Mainsail asks
for audio too) are left out of what it sees and refused in the answer. The
camera does only one format at a time: no MJPEG snapshots in this mode.
"""
import base64, hashlib, http.server, json, os, socket, socketserver
import select, signal, struct, subprocess, sys, threading, time
import urllib.request
import uuid

CFG_PATH = os.environ.get(
    "HARKCAM_CFG", "/mnt/UDISK/printer_data_mainline/config/harkcam.cfg")
DEFAULTS = {"size": "1280x720", "fps": "10", "max_streams": "2",
            "mode": "mjpeg",
            "webrtc_url": "http://127.0.0.1:8000/call/webrtc_local"}
MODES = ("mjpeg", "webrtc")
SIZES = ("640x480", "800x600", "1280x720")
FPS_RANGE = (1, 15)
MAX_WAIT = 3.0          # a snapshot queued longer than this gets a 503
STALE_AFTER = 10.0      # no new frame for this long: the camera has stopped,
                        # so say so rather than serve the last frame forever
CAPTURE_STUCK = 15.0    # ffmpeg alive but silent this long: restart it
NO_FRAME_WAIT = 5.0     # a new viewer waits this long for a live frame
# Creality's apps start again by themselves when the camera drops off USB and
# comes back, and whichever holds the device locks ffmpeg out
CAMERA_APPS = ("cam_app", "cam_sub_app")


def read_cfg(path):
    """key: value lines; # comments; unknown keys ignored."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    if k in DEFAULTS:
                        out[k] = v.strip()
    except OSError:
        pass
    return out


def settings(argv, cfg):
    """Port, device, size, fps, max_streams; bad values fall back to safe."""
    s = dict(DEFAULTS)
    s.update(cfg)
    if len(argv) > 3:
        s["size"] = argv[3]
    if len(argv) > 4:
        s["fps"] = argv[4]
    port = int(argv[1]) if len(argv) > 1 else 8080
    device = argv[2] if len(argv) > 2 else "/dev/video0"
    size = s["size"] if s["size"] in SIZES else DEFAULTS["size"]
    try:
        fps = min(max(int(s["fps"]), FPS_RANGE[0]), FPS_RANGE[1])
    except ValueError:
        fps = int(DEFAULTS["fps"])
    try:
        max_streams = min(max(int(s["max_streams"]), 1), 4)
    except ValueError:
        max_streams = int(DEFAULTS["max_streams"])
    return port, device, size, fps, max_streams


def mode_settings(cfg):
    """(mode, webrtc_local URL) from the settings file; unknown -> mjpeg."""
    mode = cfg.get("mode", DEFAULTS["mode"]).strip().lower()
    return (mode if mode in MODES else "mjpeg",
            cfg.get("webrtc_url", DEFAULTS["webrtc_url"]).strip())


# --- SDP translation for webrtc_local ------------------------------------
def split_sdp(sdp):
    """Session lines and one list of lines per media section."""
    lines = [l for l in sdp.replace("\r\n", "\n").split("\n") if l]
    session, media = [], []
    for line in lines:
        if line.startswith("m="):
            media.append([line])
        elif media:
            media[-1].append(line)
        else:
            session.append(line)
    return session, media


def join_sdp(session, media):
    return "\r\n".join(session + [l for m in media for l in m]) + "\r\n"


def media_mid(section):
    for line in section:
        if line.startswith("a=mid:"):
            return line[6:].strip()
    return None


def fix_candidate(line, client_ip):
    """One ICE candidate as webrtc_local can use it, or None. Browsers hide
    their LAN address behind a random .local name, which webrtc_local
    cannot look up: the address the browser reached us from is put in its
    place. Only UDP over IPv4 is kept."""
    c = line.strip()
    if c.startswith("a="):
        c = c[2:]
    parts = c.split()
    if len(parts) < 8 or not parts[0].startswith("candidate:") \
            or parts[2].lower() != "udp":
        return None
    if parts[4].lower().endswith(".local"):
        if not client_ip or ":" in client_ip:
            return None
        parts[4] = client_ip
    elif ":" in parts[4]:
        return None
    return "a=" + " ".join(parts)


def offer_for_creality(offer, extra_candidates=(), client_ip=None):
    """The offer webrtc_local can answer: the first video section only,
    with the browser's candidates in it (webrtc_local takes none later and
    never connects without them). Returns (sdp, plan); plan is each offered
    section, in order, as ("keep", mid) or ("refuse", m-line, mid, codec
    lines)."""
    session, media = split_sdp(offer)
    kept, plan = [], []
    for section in media:
        mid = media_mid(section)
        kind = section[0][2:].split()[0]
        if kind == "video" and not kept:
            kept.append(section)
            plan.append(("keep", mid))
        else:
            parts = section[0][2:].split()
            pt = parts[3] if len(parts) > 3 else "0"
            # that payload type's codec lines, as browsers repeat them
            codec = [l for l in section if l.startswith((
                "a=rtpmap:%s " % pt, "a=fmtp:%s " % pt))]
            plan.append(("refuse", "%s 0 %s %s" % (parts[0], parts[2], pt),
                         mid, codec))
    if not kept:
        raise ValueError("the offer has no video")
    video = kept[0]
    found = [l for l in video if l.startswith("a=candidate:")]
    rest = [l for l in video if not l.startswith((
        "a=candidate:", "a=end-of-candidates"))]
    cands = []
    for c in found + ["a=" + c if not c.startswith("a=") else c
                      for c in extra_candidates]:
        fixed = fix_candidate(c, client_ip)
        if fixed and fixed not in cands:
            cands.append(fixed)
    kept[0] = rest + cands + (["a=end-of-candidates"] if cands else [])
    keep_mids = [p[1] for p in plan if p[0] == "keep" and p[1] is not None]
    session = [("a=group:BUNDLE " + " ".join(keep_mids))
               if l.startswith("a=group:BUNDLE") else l for l in session]
    return join_sdp(session, kept), plan


def answer_for_browser(answer, plan):
    """webrtc_local's answer with every offered section back in its place:
    the video section under the offer's mid, the others refused (port 0),
    so the browser accepts it."""
    session, media = split_sdp(answer)
    if not media:
        raise ValueError("webrtc_local's answer has no media")
    video = media[0]
    out = []
    for step in plan:
        if step[0] == "keep":
            mid = step[1]
            section = [l for l in video if not l.startswith("a=mid:")]
            if mid is not None:
                # a=mid goes after the m=/c= lines like browsers write it
                at = next((i for i, l in enumerate(section)
                           if l.startswith("a=")), len(section))
                section.insert(at, "a=mid:" + mid)
            out.append(section)
        else:
            section = ["m=" + step[1], "c=IN IP4 0.0.0.0"]
            # the bundle's ICE and DTLS lines, as browsers write them in a
            # refused section (strict stacks will not take one without)
            section += [l for l in video if l.startswith((
                "a=ice-ufrag:", "a=ice-pwd:", "a=fingerprint:", "a=setup:",
                "a=rtcp-mux"))]
            if step[2] is not None:
                section.append("a=mid:" + step[2])
            section.append("a=inactive")
            section += step[3] if len(step) > 3 else []
            out.append(section)
    keep_mids = [s[1] for s in plan if s[0] == "keep" and s[1] is not None]
    session = [l for l in session if not l.startswith("a=group:BUNDLE")]
    if keep_mids:
        at = next((i for i, l in enumerate(session) if l.startswith("t=")),
                  len(session) - 1) + 1
        session.insert(at, "a=group:BUNDLE " + " ".join(keep_mids))
    return join_sdp(session, out)


def call_creality(url, offer_sdp, timeout=10):
    """webrtc_local's own signalling: base64 of {"type": "offer", "sdp"}
    in, base64 of the answer out."""
    body = base64.b64encode(json.dumps(
        {"type": "offer", "sdp": offer_sdp}).encode())
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "plain/text"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        reply = json.loads(base64.b64decode(r.read().strip()))
    if reply.get("type") != "answer" or not reply.get("sdp"):
        raise ValueError("webrtc_local did not answer: %r" % (reply,))
    return reply["sdp"]


TEST_PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>K2 camera</title>
<style>body{margin:0;background:#000;color:#aaa;font:14px sans-serif}
video{width:100%;max-height:100vh}#s{position:fixed;top:4px;left:6px}</style>
</head><body><div id="s">connecting</div>
<video id="v" autoplay muted playsinline></video><script>
const s = document.getElementById("s"), v = document.getElementById("v");
const pc = new RTCPeerConnection();
pc.addTransceiver("video", {direction: "recvonly"});
pc.ontrack = e => { v.srcObject = e.streams[0] || new MediaStream([e.track]); };
pc.oniceconnectionstatechange = () => { s.textContent = pc.iceConnectionState; };
// webrtc_local needs the candidates in the offer: wait for them
pc.createOffer().then(o => pc.setLocalDescription(o)).then(() =>
  new Promise(r => { if (pc.iceGatheringState === "complete") r();
    pc.onicegatheringstatechange = () => {
      if (pc.iceGatheringState === "complete") r(); }; }))
  .then(() => fetch("whep", {method: "POST",
                 headers: {"Content-Type": "application/sdp"},
                 body: pc.localDescription.sdp}))
  .then(r => { if (r.status !== 201) throw new Error("status " + r.status);
               return r.text(); })
  .then(sdp => pc.setRemoteDescription({type: "answer", sdp}))
  .catch(e => { s.textContent = "failed: " + e; });
</script></body></html>"""


class Pacer:
    """One frame budget shared by every viewer: fps frames a second."""
    def __init__(self, fps, clock=time.monotonic):
        self.interval = 1.0 / fps
        self.clock = clock
        self.next = 0.0
        self.lock = threading.Lock()

    def reserve(self):
        """Book the next send slot; returns how long to wait for it."""
        with self.lock:
            now = self.clock()
            slot = max(now, self.next)
            self.next = slot + self.interval
            return slot - now

    def cancel(self):
        """Give back the slot just booked but not used."""
        with self.lock:
            self.next = max(self.clock(), self.next - self.interval)


class Streams:
    """Open streams, oldest first; a new one past the cap ends the oldest."""
    def __init__(self, cap):
        self.cap = cap
        self.open = []
        self.lock = threading.Lock()

    def add(self):
        token = {"stop": False}
        with self.lock:
            self.open.append(token)
            while len(self.open) > self.cap:
                self.open.pop(0)["stop"] = True
        return token

    def remove(self, token):
        with self.lock:
            if token in self.open:
                self.open.remove(token)


class Viewers:
    """WebRTC viewers, cap at most. A viewer leaving a page and opening the
    next one overlap, so a new one waits a moment for a place."""
    def __init__(self, cap):
        self.cap = cap
        self.count = 0
        self.cond = threading.Condition()

    def enter(self, wait):
        with self.cond:
            if not self.cond.wait_for(lambda: self.count < self.cap, wait):
                return False
            self.count += 1
            return True

    def leave(self):
        with self.cond:
            self.count -= 1
            self.cond.notify()


def device_holders(device, names=CAMERA_APPS, proc="/proc"):
    """Pids of the named programs that have this video device open."""
    target = os.path.realpath(device)
    pids = []
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            with open(os.path.join(proc, pid, "comm")) as f:
                if f.read().strip() not in names:
                    continue
            fds = os.path.join(proc, pid, "fd")
            for fd in os.listdir(fds):
                if os.path.realpath(os.path.join(fds, fd)) == target:
                    pids.append(int(pid))
                    break
        except OSError:
            pass            # it exited, or isn't ours to read
    return pids


class Camera(threading.Thread):
    """Runs ffmpeg and keeps the newest complete JPEG."""
    def __init__(self, device, size, fps, clock=time.monotonic):
        super().__init__(daemon=True)
        self.device = device
        self.clock = clock
        self.last = None        # when the newest frame arrived
        self.proc = None
        self.started = 0.0
        self.cmd = ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
                    "-input_format", "mjpeg", "-video_size", size,
                    "-framerate", str(fps), "-i", device, "-c:v", "copy",
                    "-f", "mjpeg", "pipe:1"]
        self.frame = None
        self.count = 0
        self.cond = threading.Condition()

    def fresh(self):
        """True while frames are arriving."""
        return (self.frame is not None and self.last is not None
                and self.clock() - self.last <= STALE_AFTER)

    def capture_stuck(self):
        """True when a running ffmpeg has sent nothing for CAPTURE_STUCK."""
        if self.proc is None or self.proc.poll() is not None:
            return False
        since = max(self.started, self.last or 0.0)
        return self.clock() - since > CAPTURE_STUCK

    def free_device(self):
        """Stop a Creality app that took the camera. Returns its pids."""
        pids = device_holders(self.device)
        for pid in pids:
            print("harkcam: camera held by pid %d, stopping it" % pid,
                  flush=True)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        return pids

    def watch(self):
        while True:
            time.sleep(5)
            if self.capture_stuck():
                print("harkcam: no frames from ffmpeg, restarting it",
                      flush=True)
                self.proc.kill()

    def run(self):
        threading.Thread(target=self.watch, daemon=True).start()
        while True:
            self.started = self.clock()
            self.proc = proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            buf = b""
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                # Split on JPEG start/end markers
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2)
                    if start < 0 or end < 0:
                        break
                    jpeg = buf[start:end + 2]
                    buf = buf[end + 2:]
                    with self.cond:
                        self.frame = jpeg
                        self.last = self.clock()
                        self.count += 1
                        self.cond.notify_all()
                if len(buf) > 4 << 20:
                    buf = b""
            proc.wait()
            # Camera unplugged or busy: retry. Opened again too soon after
            # one of Creality's apps stops, the camera can stick until a reboot.
            time.sleep(5 if self.free_device() else 2)


BOUNDARY = b"harkcamframe"
CAM = PACER = STREAMS = None


MODE, WEBRTC_URL = "mjpeg", DEFAULTS["webrtc_url"]
WS_GATHER, WS_GATHER_MAX = 0.6, 3.0
SESSIONS = {}           # WHEP session id -> start time (for the log)
VIEWERS = Viewers(int(DEFAULTS["max_streams"]))
VIEWER_WAIT = 3.0       # seconds a new WebRTC viewer waits for a place


class Handler(http.server.BaseHTTPRequestHandler):
    # unbuffered reads, so select() on the socket sees everything waiting
    # (a timed-out buffered reader cannot be read again)
    rbufsize = 0

    def log_message(self, *args):
        pass

    def read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.rfile.read(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "OPTIONS, GET, POST, PATCH, DELETE")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, If-Match")
        self.send_header("Access-Control-Expose-Headers",
                         "Location, ETag, Link")

    def plain(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self):
        """The browser's address: nginx's X-Real-IP, else the peer."""
        ip = (self.headers.get("X-Real-IP")
              or (self.headers.get("X-Forwarded-For") or "").split(",")[0]
              or self.client_address[0]).strip()
        return ip[7:] if ip.startswith("::ffff:") else ip

    # --- go2rtc-style WebSocket (Mainsail's "WebRTC (go2rtc)") ----------
    def ws_send(self, obj):
        data = json.dumps(obj).encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else
                                struct.pack(">BH", 126, n) if n < 65536 else
                                struct.pack(">BQ", 127, n))
        self.wfile.write(head + data)
        self.wfile.flush()

    def ws_recv(self):
        """One text message, or None when the browser closes."""
        while True:
            head = self.read_exact(2)
            if len(head) < 2:
                return None
            op, n = head[0] & 0x0F, head[1] & 0x7F
            if n == 126:
                n = struct.unpack(">H", self.read_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self.read_exact(8))[0]
            mask = self.read_exact(4) if head[1] & 0x80 else b"\0\0\0\0"
            data = bytes(b ^ mask[i % 4] for i, b in
                         enumerate(self.read_exact(n)))
            if op == 8:
                return None
            if op == 9:                       # ping -> pong
                self.wfile.write(bytes([0x8A, len(data)]) + data)
                self.wfile.flush()
            elif op == 1:
                return data.decode()

    def go2rtc_ws(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode())
            .digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        ip = self.client_ip()
        offer, cands = None, []
        # The offer comes at once and the candidates one by one after it;
        # webrtc_local needs them all in the offer, so wait until they stop
        # (WS_GATHER s of quiet, WS_GATHER_MAX at most)
        start = time.time()
        while time.time() - start < WS_GATHER_MAX:
            ready = select.select([self.connection], [], [], WS_GATHER)[0]
            if not ready:
                if offer and cands:
                    break
                continue
            msg = self.ws_recv()
            if msg is None:
                return
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            if m.get("type") == "webrtc/offer":
                offer = m.get("value") or ""
            elif m.get("type") == "webrtc/candidate" and m.get("value"):
                cands.append(m["value"])
        if not offer:
            return
        if not VIEWERS.enter(VIEWER_WAIT):
            print("ws: %s refused, %d viewers already" % (ip, VIEWERS.cap),
                  flush=True)
            self.ws_send({"type": "error", "value":
                          "camera: %d viewers already (max_streams in "
                          "harkcam.cfg)" % VIEWERS.cap})
            return
        try:
            try:
                sdp, plan = offer_for_creality(offer, cands, ip)
                answer = answer_for_browser(call_creality(WEBRTC_URL, sdp),
                                            plan)
            except Exception as e:
                print("ws: %s" % e, flush=True)
                self.ws_send({"type": "error",
                              "value": "webrtc_local: %s" % e})
                return
            print("ws: %s, %d candidates" % (ip, sum(
                1 for l in sdp.split("\r\n")
                if l.startswith("a=candidate"))), flush=True)
            self.ws_send({"type": "webrtc/answer", "value": answer})
            # keep the socket open (the browser closes it); answer pings
            while self.ws_recv() is not None:
                pass
        finally:
            VIEWERS.leave()

    def whep(self):
        """The path is /whep or /whep/<id> (behind nginx's /webcam/)."""
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/whep":
            return ""
        if path.startswith("/whep/"):
            return path[6:]
        return None

    def do_OPTIONS(self):
        if self.whep() is None:
            return self.plain(404, "not found")
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_POST(self):
        if self.whep() != "":
            return self.plain(404, "not found")
        if MODE != "webrtc":
            return self.plain(409, "harkcam is in mode: mjpeg; set mode: "
                              "webrtc in harkcam.cfg for WebRTC")
        offer = self.read_exact(int(self.headers.get("Content-Length") or 0))
        if b"a=candidate" not in offer:
            return self.plain(400, "no ICE candidates in the offer: set the "
                              "camera type to WebRTC (go2rtc)")
        try:
            sdp, plan = offer_for_creality(offer.decode(),
                                           client_ip=self.client_ip())
            answer = answer_for_browser(
                call_creality(WEBRTC_URL, sdp), plan)
        except Exception as e:
            print("whep: %s" % e, flush=True)
            return self.plain(502, "webrtc_local: %s" % e)
        sid = uuid.uuid4().hex
        SESSIONS[sid] = time.time()
        while len(SESSIONS) > 32:
            SESSIONS.pop(next(iter(SESSIONS)))
        print("whep: session %s (%d kept)" % (sid[:8], len(SESSIONS)),
              flush=True)
        body = answer.encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/sdp")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Location", "whep/" + sid)
        self.send_header("ETag", '"%s"' % sid)
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_PATCH(self):
        if not self.whep():
            return self.plain(404, "not found")
        self.read_exact(int(self.headers.get("Content-Length") or 0))
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_DELETE(self):
        sid = self.whep()
        if not sid:
            return self.plain(404, "not found")
        SESSIONS.pop(sid, None)
        self.send_response(200)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if (self.headers.get("Upgrade", "").lower() == "websocket"
                and self.path.split("?")[0].rstrip("/").endswith("/api/ws")):
            if MODE != "webrtc":
                return self.plain(409, "harkcam is in mode: mjpeg")
            return self.go2rtc_ws()
        if MODE == "webrtc":
            if "action=" in self.path:
                return self.plain(503, "harkcam is in mode: webrtc (the "
                                  "camera is sending H.264): no MJPEG "
                                  "stream or snapshot. Set mode: mjpeg in "
                                  "harkcam.cfg for those.")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(TEST_PAGE)))
            self.end_headers()
            self.wfile.write(TEST_PAGE)
            return
        if "action=stream" in self.path:
            self.stream()
        elif "action=snapshot" in self.path:
            self.snapshot()
        else:
            body = (b"<html><body style='margin:0;background:#000'>"
                    b"<img src='/?action=stream' style='width:100%'>"
                    b"</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def stream(self):
        # A camera that has just started gets a moment for its first frame
        with CAM.cond:
            CAM.cond.wait_for(CAM.fresh, timeout=NO_FRAME_WAIT)
        if not CAM.fresh():
            self.send_error(503, "No frames from the camera")
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=%s"
                         % BOUNDARY.decode())
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        token = STREAMS.add()
        seen = -1
        try:
            while not token["stop"]:
                with CAM.cond:
                    CAM.cond.wait_for(lambda: CAM.count != seen, timeout=5)
                if token["stop"]:
                    break
                if CAM.count == seen:
                    # Nothing new: never resend the last frame as if live.
                    # Closing lets the viewer reconnect when it recovers.
                    if not CAM.fresh():
                        break
                    continue
                time.sleep(PACER.reserve())
                with CAM.cond:
                    frame, seen = CAM.frame, CAM.count
                if frame is None:
                    continue
                self.wfile.write(b"--" + BOUNDARY + b"\r\n"
                                 b"Content-Type: image/jpeg\r\n"
                                 b"Content-Length: %d\r\n\r\n"
                                 % len(frame) + frame + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            STREAMS.remove(token)

    def snapshot(self):
        wait = PACER.reserve()
        if wait > MAX_WAIT:
            PACER.cancel()
            self.send_error(503, "Camera busy")
            return
        time.sleep(wait)
        frame = CAM.frame
        if not CAM.fresh():
            self.send_error(503, "No frames from the camera")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(frame)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv):
    global CAM, PACER, STREAMS, VIEWERS, MODE, WEBRTC_URL
    cfg = read_cfg(CFG_PATH)
    port, device, size, fps, max_streams = settings(argv, cfg)
    MODE, WEBRTC_URL = mode_settings(cfg)
    if MODE == "webrtc":
        print("harkcam: mode webrtc, up to %d viewers, port %d -> %s"
              % (max_streams, port, WEBRTC_URL), flush=True)
        VIEWERS = Viewers(max_streams)
        Server(("", port), Handler).serve_forever()
        return
    print("harkcam: %s %s at %d fps shared, up to %d streams, port %d"
          % (device, size, fps, max_streams, port), flush=True)
    # The camera offers 5, 10 and 15 fps; capture at the nearest one at or
    # above the budget (the pacer drops the rest)
    capture = 5 if fps <= 5 else 10 if fps <= 10 else 15
    CAM = Camera(device, size, capture)
    PACER = Pacer(fps)
    STREAMS = Streams(max_streams)
    CAM.start()
    Server(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main(sys.argv)
