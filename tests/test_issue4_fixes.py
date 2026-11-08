"""Fixes from issue #4: a mesh retry at probing temperature, go2rtc's web UI
behind nginx, the touchscreen's light icon and the Moonraker webcam patch."""
import importlib.util
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "harkscreen", ROOT / "harkscreen" / "harkscreen.py")
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)

WEBCAM = '''class WebCam:
    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}

    async def get_stream_url(self):
        return ""
'''


def macro(cfg, name):
    text = (ROOT / "config" / cfg).read_text()
    m = re.search(r"^\[gcode_macro %s\]\n(.*?)^\[" % name, text, re.S | re.M)
    return m.group(1)


class CoolWipe(unittest.TestCase):
    def test_retract_needs_a_hot_nozzle(self):
        for cfg in ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg"):
            body = macro(cfg, "_COOL_WIPE")
            self.assertRegex(
                body, r"if printer\.extruder\.can_extrude %\}\n\s+G1 E-3 ")


class MeshAttempt(unittest.TestCase):
    def test_waits_after_the_mesh_and_the_check(self):
        for cfg in ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg"):
            body = macro(cfg, "_MESH_ATTEMPT")
            for cmd in ("BED_MESH_CALIBRATE", "BED_MESH_CHECK"):
                self.assertRegex(
                    body, cmd + r" [^\n]*\n(\s*#[^\n]*\n)*\s+M400\n")


class CamRoute(unittest.TestCase):
    def test_only_video_reaches_go2rtc(self):
        text = (ROOT / "system-nginx.conf").read_text()
        self.assertNotIn("location /cam/", text)
        routes = re.findall(r"location ~ (\^/cam/\S+)", text)
        self.assertEqual(len(routes), 2)
        for good in ("/cam/api/ws", "/cam/api/frame.jpeg",
                     "/cam/api/stream.mp4"):
            self.assertTrue(re.match(routes[0], good), good)
        for bad in ("/cam", "/cam/", "/cam/editor.html", "/cam/api/config",
                    "/cam/api/streams", "/cam/api/exit", "/cam/api/log"):
            self.assertIsNone(re.match(routes[0], bad), bad)


class LightIcon(unittest.TestCase):
    def test_on_and_off_differ(self):
        screen = hs.Screen.__new__(hs.Screen)
        shots = []
        for name in ("light", "light_on"):
            im = hs.Image.new("RGB", (80, 80))
            screen.icon(hs.ImageDraw.Draw(im), name, 40, 40, 34,
                        (255, 255, 255))
            shots.append(im.tobytes())
        self.assertNotEqual(shots[0], shots[1])


class WebcamPatch(unittest.TestCase):
    def run_patch(self, source):
        text = (ROOT / "install.sh").read_text()
        code = text.split('python3 - "$WC" <<\'PY\'\n')[1].split("\nPY\n")[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "webcam.py")
            path.write_text(source)
            subprocess.run([sys.executable, "-c", code, str(path)],
                           check=True, capture_output=True)
            return path.read_text()

    def test_adds_the_defaults(self):
        out = self.run_patch(WEBCAM)
        scope = {}
        exec(out, scope)
        cam = scope["WebCam"]()
        cam.name = "chamber"
        cam._hidden = 1
        self.assertEqual(cam.as_dict(), {
            "name": "chamber", "enabled": True, "icon": "mdiWebcam",
            "aspect_ratio": "16:9", "target_fps_idle": 5, "extra_data": {}})

    def test_runs_twice_safely(self):
        once = self.run_patch(WEBCAM)
        self.assertEqual(self.run_patch(once), once)


if __name__ == "__main__":
    unittest.main()
