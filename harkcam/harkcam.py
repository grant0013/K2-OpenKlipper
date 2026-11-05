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
  size: 1280x720     fps: 10     max_streams: 2
"""
import http.server, os, socketserver, subprocess, sys, threading, time

CFG_PATH = os.environ.get(
    "HARKCAM_CFG", "/mnt/UDISK/printer_data_mainline/config/harkcam.cfg")
DEFAULTS = {"size": "1280x720", "fps": "10", "max_streams": "2"}
SIZES = ("640x480", "800x600", "1280x720")
FPS_RANGE = (1, 15)
MAX_WAIT = 3.0          # a snapshot queued longer than this gets a 503


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


class Camera(threading.Thread):
    """Runs ffmpeg and keeps the newest complete JPEG."""
    def __init__(self, device, size, fps):
        super().__init__(daemon=True)
        self.cmd = ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
                    "-input_format", "mjpeg", "-video_size", size,
                    "-framerate", str(fps), "-i", device, "-c:v", "copy",
                    "-f", "mjpeg", "pipe:1"]
        self.frame = None
        self.count = 0
        self.cond = threading.Condition()

    def run(self):
        while True:
            proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
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
                        self.count += 1
                        self.cond.notify_all()
                if len(buf) > 4 << 20:
                    buf = b""
            proc.wait()
            time.sleep(2)   # camera unplugged or busy: retry


BOUNDARY = b"harkcamframe"
CAM = PACER = STREAMS = None


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
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
        if frame is None:
            self.send_error(503, "No frame yet")
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
    global CAM, PACER, STREAMS
    port, device, size, fps, max_streams = settings(argv, read_cfg(CFG_PATH))
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
