"""Touchscreen "Print a file": Moonraker calls, the list and detail views,
and starting a print, against a fake Moonraker. Set HARKSCREEN_SHOTS to a
folder to save the rendered screens; HARKSCREEN_FONT to draw them with the
K2's own font."""
import http.server
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.parse

PATH = pathlib.Path(__file__).parents[1] / "harkscreen" / "harkscreen.py"
spec = importlib.util.spec_from_file_location("harkscreen", PATH)
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)
Image, ImageDraw = hs.Image, hs.ImageDraw

NOW = time.time()
FILES = [
    {"path": "Benchy_PLA_0.2mm.gcode", "modified": NOW - 3600,
     "size": 2400000},
    {"path": "old/Bracket_PETG.gcode", "modified": NOW - 5 * 86400,
     "size": 900000},
    {"path": ".thumbs/Benchy_PLA_0.2mm-300x300.png", "modified": NOW,
     "size": 1000},
    {"path": "notes.txt", "modified": NOW, "size": 10},
] + [{"path": "Part_%02d.gcode" % i, "modified": NOW - 86400 - i * 60,
      "size": 100000} for i in range(5)]


def thumb_png(color):
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(im).polygon([(40, 260), (260, 260), (220, 90), (90, 60)],
                               fill=color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


THUMB = thumb_png((230, 120, 40, 255))


class FakeMoonraker(http.server.BaseHTTPRequestHandler):
    started = []
    refuse = None

    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/server/files/list":
            return self.reply(200, {"result": FILES})
        if url.path == "/server/files/metadata":
            name = q["filename"][0]
            if name.startswith("Part_"):
                return self.reply(404, {"error": {"message": "no meta"}})
            meta = {"filename": name, "estimated_time": 5025,
                    "filament_weight_total": 14.6,
                    "filament_type": "PLA;PLA;", "object_height": 48.0,
                    "layer_height": 0.2,
                    "thumbnails": [
                        {"width": 32, "height": 32,
                         "relative_path": ".thumbs/x-32x32.png"},
                        {"width": 300, "height": 300,
                         "relative_path": ".thumbs/x-300x300.png"}]}
            return self.reply(200, {"result": meta})
        if url.path.startswith("/server/files/gcodes/"):
            assert url.path.endswith("x-300x300.png"), url.path
            return self.reply(200, THUMB, "image/png")
        self.reply(404, {"error": {"message": "not found"}})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/printer/print/start":
            name = urllib.parse.parse_qs(url.query)["filename"][0]
            if FakeMoonraker.refuse:
                return self.reply(400, {"error": {
                    "message": FakeMoonraker.refuse}})
            FakeMoonraker.started.append(name)
            return self.reply(200, {"result": "ok"})
        self.reply(404, {"error": {"message": "not found"}})


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def status(state="standby", filename=""):
    return {"webhooks": {"state": "ready"},
            "print_stats": {"state": state, "filename": filename},
            "idle_timeout": {"state": "Idle"},
            "extruder": {"temperature": 24, "target": 0},
            "heater_bed": {"temperature": 23, "target": 0},
            "k2_cfs": {"fitted": True}, "hark_cfs_slots": {"slots": {}},
            "fan": {"speed": 0}}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                  FakeMoonraker)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.mr = hs.Moonraker("http://127.0.0.1:%d" % cls.srv.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeMoonraker.started, FakeMoonraker.refuse = [], None


class MoonrakerTest(Base):
    def test_files_newest_first_without_hidden_or_other_files(self):
        paths = [f[0] for f in self.mr.files()]
        self.assertEqual(paths[:2], ["Benchy_PLA_0.2mm.gcode",
                                     "old/Bracket_PETG.gcode"][:1] +
                         ["Part_00.gcode"])
        self.assertNotIn(".thumbs/Benchy_PLA_0.2mm-300x300.png", paths)
        self.assertNotIn("notes.txt", paths)
        self.assertEqual(len(paths), 7)
        self.assertEqual(paths[-1], "old/Bracket_PETG.gcode")

    def test_metadata_and_largest_thumbnail(self):
        meta = self.mr.metadata("Benchy_PLA_0.2mm.gcode")
        img = self.mr.thumb_image("Benchy_PLA_0.2mm.gcode", meta)
        self.assertEqual(img.size, (300, 300))
        self.assertEqual(img.mode, "RGBA")
        img, height = self.mr.thumbnail("Benchy_PLA_0.2mm.gcode")
        self.assertEqual((img.size, height), ((300, 300), 48.0))

    def test_start_print_ok_and_refused(self):
        self.mr.start_print("old/Bracket_PETG.gcode")
        self.assertEqual(FakeMoonraker.started, ["old/Bracket_PETG.gcode"])
        FakeMoonraker.refuse = "Printer is busy"
        with self.assertRaises(RuntimeError) as cm:
            self.mr.start_print("Benchy_PLA_0.2mm.gcode")
        self.assertIn("Printer is busy", str(cm.exception))


class FactsTest(unittest.TestCase):
    def test_facts(self):
        f = dict(hs.Screen.file_facts({
            "estimated_time": 5025, "filament_weight_total": 14.6,
            "filament_type": "PLA;PETG;PLA", "object_height": 48,
            "layer_height": 0.2}))
        self.assertEqual(f, {"Print time": "1h 24m",
                             "Filament": "15 g PLA/PETG",
                             "Height": "48.0 mm", "Layers": "0.20 mm"})
        # OrcaSlicer's list form, as read from Grant's host 11-05
        self.assertEqual(dict(hs.Screen.file_facts({
            "filament_weight_total": 27.53,
            "filament_type": '["PLA", "PETG", "PLA", "PLA"]'}))["Filament"],
            "28 g PLA/PETG")
        self.assertEqual(dict(hs.Screen.file_facts({
            "filament_type": "PETG"}))["Filament"], "PETG")
        self.assertEqual(hs.Screen.file_facts({"estimated_time": 20}),
                         [("Print time", "1 min")])
        self.assertEqual(hs.Screen.file_facts({}), [])


class ScreenTest(Base):
    shots = os.environ.get("HARKSCREEN_SHOTS")

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(self.tmp.name, "harkscreen.cfg")
        with open(cfg, "w") as f:
            f.write("[theme]\nfont: %s\n" % os.environ.get(
                "HARKSCREEN_FONT", "/nonexistent.otf"))
        hs.FB = os.path.join(self.tmp.name, "fb")
        open(hs.FB, "wb").close()
        self.s = hs.Screen(hs.Config(cfg))
        self.s.map_touch = lambda x, y: (x, y)

    def tearDown(self):
        os.close(self.s.fb)
        self.tmp.cleanup()

    def render(self, st, name):
        img = self.s.render(st, None)
        if self.shots:
            os.makedirs(self.shots, exist_ok=True)
            img.save(os.path.join(self.shots, name + ".png"))
        return img

    def actions(self):
        return [b[4] for b in self.s.buttons]

    def tap(self, action):
        for x0, y0, x1, y1, a in self.s.buttons:
            if a == action:
                return self.s.handle_tap((x0 + x1) // 2, (y0 + y1) // 2,
                                         self.mr)
        self.fail("no button %r in %r" % (action, self.actions()))

    def test_idle_status_has_print_a_file(self):
        self.render(status(), "1-status-idle")
        self.assertIn(("files_open",), self.actions())

    def test_no_button_while_printing_or_busy(self):
        self.render(status("printing", "Benchy_PLA_0.2mm.gcode"), "x")
        self.assertFalse([a for a in self.actions()
                          if a[0] == "files_open"])
        st = status()
        st["idle_timeout"]["state"] = "Printing"     # homing, cleaning
        self.render(st, "x")
        self.assertNotIn(("files_open",), self.actions())

    def test_finished_print_offers_again_and_other(self):
        self.render(status("complete", "Benchy_PLA_0.2mm.gcode"),
                    "2-status-complete")
        self.assertIn(("files_open", "Benchy_PLA_0.2mm.gcode"),
                      self.actions())
        self.assertIn(("files_open",), self.actions())

    def test_list_detail_and_print(self):
        st = status()
        self.render(st, "x")
        self.tap(("files_open",))
        self.assertTrue(wait_for(lambda: self.s.files["list"] is not None
                                 and len(self.s.file_info) >= 4))
        self.render(st, "3-files-list")
        picks = [a[1] for a in self.actions() if a[0] == "files_pick"]
        self.assertEqual(picks, ["Benchy_PLA_0.2mm.gcode", "Part_00.gcode",
                                 "Part_01.gcode", "Part_02.gcode"])
        # second page: the rest, Back on, More off
        self.tap(("files_page", 1))
        self.render(st, "4-files-page2")
        picks = [a[1] for a in self.actions() if a[0] == "files_pick"]
        self.assertEqual(picks, ["Part_03.gcode", "Part_04.gcode",
                                 "old/Bracket_PETG.gcode"])
        self.assertNotIn(("files_page", 1), self.actions())
        self.tap(("files_page", -1))
        self.render(st, "x")
        self.tap(("files_pick", "Benchy_PLA_0.2mm.gcode"))
        self.render(st, "5-file-detail")
        self.tap(("files_print", "Benchy_PLA_0.2mm.gcode"))
        self.assertTrue(wait_for(lambda: self.s.files is None))
        self.assertEqual(FakeMoonraker.started, ["Benchy_PLA_0.2mm.gcode"])
        self.assertEqual(self.s.running[0], "Starting print")

    def test_refused_start_shows_why(self):
        FakeMoonraker.refuse = "File not found"
        st = status()
        self.render(st, "x")
        self.tap(("files_open",))
        self.assertTrue(wait_for(lambda: self.s.files["list"] is not None))
        self.render(st, "x")
        self.tap(("files_pick", "Part_00.gcode"))
        self.assertTrue(wait_for(lambda: "Part_00.gcode" in
                                 self.s.file_info))
        self.render(st, "x")
        self.tap(("files_print", "Part_00.gcode"))
        self.assertTrue(wait_for(lambda: self.s.files.get("error")))
        self.render(st, "6-file-refused")
        self.assertIn("File not found", self.s.files["error"])
        self.assertIn(("files_print", "Part_00.gcode"), self.actions())

    def test_print_again_opens_that_file(self):
        st = status("complete", "Benchy_PLA_0.2mm.gcode")
        self.render(st, "x")
        self.tap(("files_open", "Benchy_PLA_0.2mm.gcode"))
        self.assertEqual(self.s.files["pick"], "Benchy_PLA_0.2mm.gcode")
        self.assertTrue(wait_for(lambda: "Benchy_PLA_0.2mm.gcode" in
                                 self.s.file_info))
        self.render(st, "x")
        self.tap(("files_back",))
        self.render(st, "x")
        self.assertIn(("files_close",), self.actions())

    def test_print_disabled_while_busy_and_closed_by_a_print(self):
        st = status()
        self.s.open_files(self.mr, "Benchy_PLA_0.2mm.gcode")
        st["idle_timeout"]["state"] = "Printing"
        self.render(st, "x")
        self.assertNotIn(("files_print", "Benchy_PLA_0.2mm.gcode"),
                         self.actions())
        self.render(status("printing", "Benchy_PLA_0.2mm.gcode"), "x")
        self.assertIsNone(self.s.files)

    def test_nav_closes_the_list(self):
        self.render(status(), "x")
        self.tap(("files_open",))
        self.render(status(), "x")
        self.tap(("page", "controls"))
        self.assertIsNone(self.s.files)

    def test_transitions_setting(self):
        self.assertEqual(self.s.transition_frames(), (3, 1))      # fast
        self.s.cfg.cp.set("screen", "transitions", "off")
        self.assertEqual(self.s.transition_frames(), (0, 0))
        self.s.cfg.cp.set("screen", "transitions", "smooth")
        self.assertEqual(self.s.transition_frames(), (7, 5))
        self.s.cfg.cp.set("screen", "transitions", "nonsense")
        self.assertEqual(self.s.transition_frames(), (3, 1))
        a = self.render(status(), "x")
        self.s.page = "controls"
        b = self.render(status(), "x")
        for mode in ("off", "fast", "smooth"):
            self.s.cfg.cp.set("screen", "transitions", mode)
            self.s.slide(a, b, 1)
            self.s.fade(a, b)
        # the panel ends on the new frame
        with open(hs.FB, "rb") as f:
            panel = f.read()
        self.assertEqual(panel, self.s.to_fb(b).convert("RGBA").tobytes(
            "raw", "BGRA"))

    def test_next_page_loading_does_not_redraw(self):
        self.render(status(), "x")
        self.tap(("files_open",))
        self.assertTrue(wait_for(lambda: len(self.s.file_info) >= 7))
        key = self.s.files_key()
        self.s.file_info.pop("Part_04.gcode", None)     # on page 2
        self.assertEqual(self.s.files_key(), key)
        self.s.file_info.pop("Part_00.gcode", None)     # on view
        self.assertNotEqual(self.s.files_key(), key)

    def test_page_buttons_say_previous_and_next(self):
        self.render(status(), "x")
        self.tap(("files_open",))
        self.assertTrue(wait_for(lambda: self.s.files["list"] is not None))
        self.render(status(), "x")
        labels = []
        orig = self.s.button
        self.s.button = lambda d, box, label, *a, **k: (
            labels.append(label), orig(d, box, label, *a, **k))
        self.render(status(), "x")
        self.s.button = orig
        self.assertIn("Previous", labels)
        self.assertIn("Next", labels)
        self.assertNotIn("More", labels)

    def test_empty_and_unreadable_lists(self):
        global FILES
        saved = FILES
        try:
            FILES = []
            self.render(status(), "x")
            self.tap(("files_open",))
            self.assertTrue(wait_for(lambda: self.s.files["list"] == []))
            self.render(status(), "7-files-empty")
            self.assertEqual([a for a in self.actions()
                              if a[0] == "files_pick"], [])
        finally:
            FILES = saved
        self.s.open_files(hs.Moonraker("http://127.0.0.1:9"))
        self.assertTrue(wait_for(lambda: self.s.files["error"]))
        self.render(status(), "x")


if __name__ == "__main__":
    unittest.main()
