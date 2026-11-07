import importlib.util
import pathlib
import socket
import tempfile
import threading
import time
import subprocess
import unittest
import urllib.error
import urllib.request

PATH = pathlib.Path(__file__).parents[1] / "harkcam" / "harkcam.py"
spec = importlib.util.spec_from_file_location("harkcam", PATH)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

FRAME = b"\xff\xd8" + b"x" * 85000 + b"\xff\xd9"   # 720p, chamber light on


class SettingsTest(unittest.TestCase):
    def test_defaults_are_10_fps_two_streams(self):
        self.assertEqual(hc.settings(["harkcam.py"], {}),
                         (8080, "/dev/video0", "1280x720", 10, 2))

    def test_start_sh_webrtc_steps(self):
        start = (PATH.parent / "start.sh").read_text()
        subprocess.run(["sh", "-n", str(PATH.parent / "start.sh")], check=True)
        self.assertNotIn("MediaMTX", start)
        self.assertIn('"WebRTC (go2rtc)"', start)
        self.assertIn("-t 0 -w 1280 -h 720 -f 15 -c", start)
        self.assertLess(start.index('p=$(pidof ffmpeg)'),
                        start.index("    tries=0"))

    def test_start_sh_line_no_longer_forces_15_fps(self):
        start = (PATH.parent / "start.sh").read_text()
        self.assertIn("harkcam.py 8080 /dev/video0 >", start)

    def test_file_and_command_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg") as f:
            f.write("# comment\nsize: 640x480\nfps: 5  # slow\n"
                    "max_streams: 1\nunknown: 3\n")
            f.flush()
            cfg = hc.read_cfg(f.name)
        self.assertEqual(cfg, {"size": "640x480", "fps": "5",
                               "max_streams": "1"})
        self.assertEqual(hc.settings(["x", "8080", "/dev/video0"], cfg)[2:],
                         ("640x480", 5, 1))
        # command line wins over the file
        self.assertEqual(hc.settings(["x", "8080", "/dev/video0",
                                      "1280x720", "8"], cfg)[2:4],
                         ("1280x720", 8))

    def test_bad_values_fall_back(self):
        self.assertEqual(hc.settings(["x"], {"size": "4k", "fps": "fast",
                                             "max_streams": "0"})[2:],
                         ("1280x720", 10, 1))
        self.assertEqual(hc.settings(["x"], {"fps": "60",
                                             "max_streams": "9"})[3:],
                         (15, 4))
        self.assertEqual(hc.read_cfg("/nonexistent/harkcam.cfg"), {})


class PacerTest(unittest.TestCase):
    def test_slots_are_shared(self):
        now = [100.0]
        p = hc.Pacer(10, clock=lambda: now[0])
        self.assertEqual(p.reserve(), 0.0)
        self.assertAlmostEqual(p.reserve(), 0.1)    # second viewer waits
        self.assertAlmostEqual(p.reserve(), 0.2)
        p.cancel()
        self.assertAlmostEqual(p.reserve(), 0.2)
        now[0] = 200.0                              # idle: no backlog
        self.assertEqual(p.reserve(), 0.0)


class StreamsTest(unittest.TestCase):
    def test_new_stream_closes_oldest(self):
        s = hc.Streams(2)
        a, b = s.add(), s.add()
        self.assertFalse(a["stop"] or b["stop"])
        c = s.add()
        self.assertTrue(a["stop"])
        self.assertFalse(b["stop"] or c["stop"])
        s.remove(b)
        s.add()
        self.assertFalse(c["stop"])


class FakeCamera(threading.Thread):
    """15 fps of 85 KB frames, like the K2 camera with the light on."""
    def __init__(self):
        super().__init__(daemon=True)
        self.frame, self.count = None, 0
        self.cond = threading.Condition()

    def fresh(self):
        return self.frame is not None

    def run(self):
        while True:
            with self.cond:
                self.frame = FRAME
                self.count += 1
                self.cond.notify_all()
            time.sleep(1 / 15)


class DeviceHoldersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.video = self.root / "video0"
        self.video.touch()
        self.other = self.root / "video1"
        self.other.touch()

    def process(self, pid, comm, target):
        d = self.root / "proc" / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "comm").write_text(comm + "\n")
        (d / "fd" / "3").symlink_to(target)

    def holders(self):
        return hc.device_holders(str(self.video),
                                 proc=str(self.root / "proc"))

    def test_finds_cam_sub_app_on_our_camera(self):
        self.process(1298, "cam_sub_app", self.video)
        self.assertEqual(self.holders(), [1298])

    def test_leaves_a_second_camera_alone(self):
        self.process(1298, "cam_sub_app", self.other)
        self.assertEqual(self.holders(), [])

    def test_only_touches_creality_camera_apps(self):
        self.process(55, "ffmpeg", self.video)
        self.assertEqual(self.holders(), [])

    def test_follows_a_by_id_link(self):
        self.process(1298, "cam_sub_app", self.video)
        link = self.root / "sub-video0"
        link.symlink_to(self.video)
        self.assertEqual(hc.device_holders(str(link),
                                           proc=str(self.root / "proc")),
                         [1298])


class CameraStateTest(unittest.TestCase):
    def camera(self):
        self.now = 100.0
        return hc.Camera("/dev/video0", "1280x720", 10,
                         clock=lambda: self.now)

    def test_no_frame_is_not_fresh(self):
        self.assertFalse(self.camera().fresh())

    def test_frame_goes_stale(self):
        cam = self.camera()
        cam.frame, cam.last = FRAME, self.now
        self.assertTrue(cam.fresh())
        self.now += hc.STALE_AFTER + 1
        self.assertFalse(cam.fresh())

    def test_silent_ffmpeg_counts_as_stuck(self):
        cam = self.camera()
        cam.proc = type("P", (), {"poll": lambda s: None})()
        cam.started = self.now
        self.assertFalse(cam.capture_stuck())
        self.now += hc.CAPTURE_STUCK + 1
        self.assertTrue(cam.capture_stuck())
        cam.last = self.now
        self.assertFalse(cam.capture_stuck())

    def test_exited_ffmpeg_is_not_stuck(self):
        cam = self.camera()
        cam.proc = type("P", (), {"poll": lambda s: 1})()
        self.now += 1000
        self.assertFalse(cam.capture_stuck())


class StoppedCameraTest(unittest.TestCase):
    """The camera stopped: never pass the last frame off as live."""
    @classmethod
    def setUpClass(cls):
        cls.saved = hc.CAM, hc.PACER, hc.STREAMS, hc.NO_FRAME_WAIT
        cam = FakeCamera()
        cam.frame, cam.count = FRAME, 1
        cam.fresh = lambda: False
        hc.CAM, hc.PACER, hc.STREAMS = cam, hc.Pacer(10), hc.Streams(2)
        hc.NO_FRAME_WAIT = 0.1
        cls.srv = hc.Server(("127.0.0.1", 0), hc.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        hc.CAM, hc.PACER, hc.STREAMS, hc.NO_FRAME_WAIT = cls.saved

    def status(self, action):
        url = "http://127.0.0.1:%d/?action=%s" % (self.port, action)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(url, timeout=5)
        return caught.exception.code

    def test_snapshot_says_503(self):
        self.assertEqual(self.status("snapshot"), 503)

    def test_new_stream_says_503(self):
        self.assertEqual(self.status("stream"), 503)


class LiveServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hc.CAM, hc.PACER, hc.STREAMS = FakeCamera(), hc.Pacer(10), \
            hc.Streams(2)
        hc.CAM.start()
        cls.srv = hc.Server(("127.0.0.1", 0), hc.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def read_stream(self, seconds, out, key):
        s = socket.create_connection(("127.0.0.1", self.port))
        s.sendall(b"GET /?action=stream HTTP/1.0\r\n\r\n")
        s.settimeout(0.5)
        n, end = 0, time.monotonic() + seconds
        try:
            while time.monotonic() < end:
                try:
                    d = s.recv(65536)
                except socket.timeout:
                    continue
                if not d:
                    break
                n += len(d)
        finally:
            s.close()
        out[key] = n

    def streams(self, count, seconds=3.0):
        out, ts = {}, []
        for i in range(count):
            t = threading.Thread(target=self.read_stream,
                                 args=(seconds, out, i))
            t.start()
            ts.append(t)
            time.sleep(0.05)
        for t in ts:
            t.join()
        time.sleep(0.3)
        return out

    def test_one_viewer_gets_10_fps(self):
        kbs = sum(self.streams(1).values()) / 3.0 / 1000
        print("\n  1 viewer: %.0f KB/s" % kbs)
        self.assertLess(kbs, 85 * 11)       # was ~85 * 15 = 1275
        self.assertGreater(kbs, 85 * 8)

    def test_two_viewers_share_one_budget(self):
        out = self.streams(2)
        kbs = sum(out.values()) / 3.0 / 1000
        print("\n  2 viewers: %.0f KB/s total (%s)" % (
            kbs, ", ".join("%.0f" % (v / 3000) for v in out.values())))
        self.assertLess(kbs, 85 * 11)       # not 2 x 10 fps
        self.assertTrue(all(v > 85000 * 9 for v in out.values()))

    def test_third_viewer_closes_the_oldest(self):
        out = self.streams(3, seconds=2.0)
        # stream 0 was ended almost at once; 1 and 2 kept going
        self.assertLess(out[0], out[1] / 3)
        # budget 10 fps, plus a frame or two the closed stream had queued
        self.assertLess(sum(out.values()) / 2.0 / 1000, 85 * 12.5)

    def test_snapshots_count_against_the_same_budget(self):
        url = "http://127.0.0.1:%d/?action=snapshot" % self.port
        got, end = 0, time.monotonic() + 2.0
        def poll():
            nonlocal got
            while time.monotonic() < end:
                with urllib.request.urlopen(url) as r:
                    got += len(r.read())
        ts = [threading.Thread(target=poll) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        kbs = got / 2.0 / 1000
        print("\n  4 snapshot pollers: %.0f KB/s total" % kbs)
        # budget 10 fps; each poller may finish one booked slot after the
        # window closes (4 x 85 KB over 2 s)
        self.assertLess(kbs, 85 * 10 + 4 * 85 / 2.0 + 20)
        self.assertGreater(kbs, 85 * 8)


if __name__ == "__main__":
    unittest.main()
