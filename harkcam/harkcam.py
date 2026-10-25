#!/usr/bin/env python3
"""Hark Cam: MJPEG stream of the K2 chamber camera for Mainsail / AI tools.

The K2's USB (UVC) camera compresses to MJPEG itself, so ffmpeg copies its
frames without re-encoding and this serves them mjpg-streamer style:
  /?action=stream    multipart MJPEG
  /?action=snapshot  latest JPEG
Usage: harkcam.py [port] [device] [WxH] [fps]
"""
import http.server, socketserver, subprocess, sys, threading, time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
DEVICE = sys.argv[2] if len(sys.argv) > 2 else "/dev/video0"
SIZE = sys.argv[3] if len(sys.argv) > 3 else "1280x720"
FPS = sys.argv[4] if len(sys.argv) > 4 else "15"
BOUNDARY = b"harkcamframe"


class Camera(threading.Thread):
    """Runs ffmpeg and keeps the newest complete JPEG."""
    def __init__(self):
        super().__init__(daemon=True)
        self.frame = None
        self.count = 0
        self.cond = threading.Condition()

    def run(self):
        while True:
            proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
                 "-input_format", "mjpeg", "-video_size", SIZE,
                 "-framerate", FPS, "-i", DEVICE, "-c:v", "copy",
                 "-f", "mjpeg", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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


CAM = Camera()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if "action=stream" in self.path:
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=%s"
                             % BOUNDARY.decode())
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with CAM.cond:
                        CAM.cond.wait_for(lambda: CAM.count != seen,
                                          timeout=5)
                        frame, seen = CAM.frame, CAM.count
                    if frame is None:
                        continue
                    self.wfile.write(b"--" + BOUNDARY + b"\r\n"
                                     b"Content-Type: image/jpeg\r\n"
                                     b"Content-Length: %d\r\n\r\n"
                                     % len(frame) + frame + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return
        elif "action=snapshot" in self.path:
            frame = CAM.frame
            if frame is None:
                self.send_error(503, "No frame yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(frame)
        else:
            body = (b"<html><body style='margin:0;background:#000'>"
                    b"<img src='/?action=stream' style='width:100%'>"
                    b"</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    CAM.start()
    Server(("", PORT), Handler).serve_forever()
