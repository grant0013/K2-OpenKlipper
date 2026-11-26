"""Touchscreen PA sweep and "PA before print" (26 Nov 2026): the slot
page's PA sweep, the Print calibration page's PA rows, the file page's
"Measure PA first" and the sweep's result popup, against the fake Moonraker
in test_harkscreen_files. HARKSCREEN_SHOTS saves the rendered screens."""
import os
import tempfile

import test_harkscreen_files as t

hs = t.hs


def pa_status(pa_auto=0, pa_next=0, method="step"):
    st = t.status()
    st["k2_cfs"] = {"fitted": False, "active_slot": None}
    st["hark_cfs_slots"] = {"slots": {"E": {
        "loaded": True, "name": "Black PETG", "type": "PETG",
        "nozzle_temp": 250, "pressure_advance": 0.0627}},
        "tool_map_set": {}, "tool_map_file": ""}
    st["filament_switch_sensor filament_sensor"] = {"filament_detected": True}
    st["hark_flow_cal"] = {"auto": 1, "pa_auto": pa_auto, "last": {}}
    st["gcode_macro _PA_AUTO"] = {"on_load": 0, "on_print_start": pa_auto,
                                  "method": method}
    st["hark_print_cal"] = {"pa_on_load": 0, "pa_on_print_start": pa_auto,
                            "mesh": 1, "pa_method": method,
                            "pa_next": pa_next}
    return st


class PAScreenTest(t.Base):
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

    def gcodes(self):
        return [a[1] for a in self.actions() if a and a[0] == "gcode"]

    # --- Print calibration page ------------------------------------------
    def test_cal_page_before_print_and_method(self):
        st = pa_status(pa_auto=1)
        self.s.page, self.s.cal_page = "settings", True
        self.render(st, "pa-1-cal-page")
        g = self.gcodes()
        self.assertIn("PA_CAL_AUTO ENABLE=0\nPRINT_CAL PA_START=0\n"
                      "PRINT_CAL PA_NEXT=0", g)
        self.assertIn("PA_CAL_AUTO ENABLE=1\nPRINT_CAL PA_START=1\n"
                      "PRINT_CAL PA_NEXT=0", g)
        nxt = "PA_CAL_AUTO ENABLE=0\nPRINT_CAL PA_START=0\nPRINT_CAL PA_NEXT=1"
        self.assertIn(nxt, g)
        for m in ("step", "sweep", "blend"):
            self.assertIn("PRINT_CAL PA_METHOD=%s" % m, g)
        # the Back button is still there under five rows
        self.assertIn(("cal_back",), self.actions())
        self.tap(("gcode", nxt))
        self.assertTrue(t.wait_for(lambda: nxt in t.FakeMoonraker.scripts))

    def test_cal_page_without_method_firmware(self):
        st = pa_status()
        del st["hark_print_cal"]["pa_method"]
        del st["hark_print_cal"]["pa_next"]
        self.s.page, self.s.cal_page = "settings", True
        self.render(st, "x")
        g = self.gcodes()
        self.assertFalse([x for x in g if "PA_METHOD" in x or "PA_NEXT" in x])
        self.assertIn("PA_CAL_AUTO ENABLE=1\nPRINT_CAL PA_START=1", g)

    # --- slot page --------------------------------------------------------
    def test_rear_spool_page_offers_the_sweep(self):
        st = pa_status()
        self.s.slot_menu = {"slot": "E",
                            "rec": st["hark_cfs_slots"]["slots"]["E"]}
        self.render(st, "pa-2-slot-e")
        confirms = [a for a in self.actions() if a[0] == "confirm"]
        sweep = [a for a in confirms if "PA_SWEEP_CAL" in a[2]]
        self.assertEqual(len(sweep), 1)
        self.assertEqual(sweep[0][2], "PA_SWEEP_CAL SLOT=E SAVE=1")
        self.assertIn("250", sweep[0][1])
        self.assertIn(("slot_close",), self.actions())

    def test_loaded_cfs_slot_loads_then_sweeps(self):
        st = pa_status()
        st["k2_cfs"] = {"fitted": True, "active_slot": "A"}
        rec = {"loaded": True, "type": "PLA", "nozzle_temp": 220}
        st["hark_cfs_slots"]["slots"].update(A=dict(rec), B=dict(rec))
        self.s.slot_menu = {"slot": "B", "rec": rec}
        self.render(st, "x")
        sweep = [a for a in self.actions() if a[0] == "confirm"
                 and "PA_SWEEP_CAL" in a[2]]
        self.assertEqual(sweep[0][2], "CFS_LOAD_SLOT SLOT=B COOL=0\n"
                                      "PA_SWEEP_CAL SLOT=B SAVE=1")

    # --- file page: Measure PA first --------------------------------------
    def test_measure_pa_first_arms_the_next_print(self):
        st = pa_status()
        self.s.open_files(self.mr, "Benchy_PLA_0.2mm.gcode")
        self.assertTrue(t.wait_for(
            lambda: "Benchy_PLA_0.2mm.gcode" in self.s.file_info))
        self.render(st, "x")
        self.tap(("files_pa_first",))
        self.render(st, "pa-3-file-pa-first")
        self.assertTrue(self.s.files["pa_first"])
        self.tap(("files_print", "Benchy_PLA_0.2mm.gcode"))
        self.assertTrue(t.wait_for(
            lambda: t.FakeMoonraker.started == ["Benchy_PLA_0.2mm.gcode"]))
        sc = t.FakeMoonraker.scripts
        self.assertEqual(sc[0], "PRINT_CAL PA_NEXT=1")

    def test_measure_pa_first_off_by_default_and_hidden_when_every(self):
        st = pa_status()
        self.s.open_files(self.mr, "Benchy_PLA_0.2mm.gcode")
        self.assertTrue(t.wait_for(
            lambda: "Benchy_PLA_0.2mm.gcode" in self.s.file_info))
        self.render(st, "x")
        self.tap(("files_print", "Benchy_PLA_0.2mm.gcode"))
        self.assertTrue(t.wait_for(lambda: t.FakeMoonraker.started))
        self.assertNotIn("PRINT_CAL PA_NEXT=1", t.FakeMoonraker.scripts)
        st = pa_status(pa_auto=1)
        self.s.open_files(self.mr, "Benchy_PLA_0.2mm.gcode")
        self.assertTrue(t.wait_for(
            lambda: "Benchy_PLA_0.2mm.gcode" in self.s.file_info))
        self.render(st, "x")
        self.assertNotIn(("files_pa_first",), self.actions())

    def test_tapping_off_clears_a_next_print_set_elsewhere(self):
        st = pa_status(pa_next=1)
        self.s.open_files(self.mr, "Benchy_PLA_0.2mm.gcode")
        self.assertTrue(t.wait_for(
            lambda: "Benchy_PLA_0.2mm.gcode" in self.s.file_info))
        self.render(st, "x")
        self.tap(("files_pa_first",))
        self.assertTrue(t.wait_for(
            lambda: "PRINT_CAL PA_NEXT=0" in t.FakeMoonraker.scripts))
        self.assertFalse(self.s.files["pa_first"])

    # --- result popup -----------------------------------------------------
    def test_sweep_result_popup(self):
        st = pa_status()
        st["gcode_macro _PA_STATIC_SAVE"] = {"last": {
            "slot": "E", "saved": 1, "pa": 0.06, "stamp": 1.0}}
        self.render(st, "x")       # the first stamp seen is acknowledged
        st["gcode_macro _PA_STATIC_SAVE"] = {"last": {
            "slot": "E", "saved": 1, "pa": 0.0627, "measured": 0.0627,
            "scale": 1.0, "material": "PETG", "how": "sweep", "r2": 0.889,
            "stamp": 2.0}}
        res = self.s.pa_result(st)
        self.assertEqual((res["how"], res["fitted"], res["saved"]),
                         ("sweep", 0.0627, True))
        self.render(st, "pa-4-result")
        self.assertIn(("pa_static_ok", 2.0), self.actions())

    def test_sweep_not_saved_is_still_a_good_value(self):
        st = pa_status()
        st["gcode_macro _PA_STATIC_SAVE"] = {"last": {"stamp": 1.0}}
        self.render(st, "x")
        st["gcode_macro _PA_STATIC_SAVE"] = {"last": {
            "slot": "E", "saved": 0, "reason": "not_saved", "pa": 0.05,
            "how": "sweep", "r2": 0.9, "stamp": 3.0}}
        res = self.s.pa_result(st)
        self.assertTrue(res["reliable"])
        self.assertFalse(res["saved"])
        self.render(st, "x")


if __name__ == "__main__":
    import unittest
    unittest.main()
