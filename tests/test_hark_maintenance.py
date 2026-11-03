import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).parents[1] / "klippy" / "extras" / \
    "hark_maintenance.py"
spec = importlib.util.spec_from_file_location("hark_maintenance", PATH)
hm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hm)


class ProgressTest(unittest.TestCase):
    def test_due_when_used_reaches_the_threshold(self):
        totals = {'hours': 520., 'grams': 1500., 'cuts': 20.}
        p = hm.progress(totals, {}, {})
        self.assertTrue(p['shaper_calibrate']['due'])      # 500 h
        self.assertFalse(p['belt_tensioning']['due'])      # 1000 h
        self.assertFalse(p['nozzle']['due'])               # 20 kg
        self.assertEqual(p['nozzle']['used'], 1500.)

    def test_done_mark_restarts_the_count(self):
        totals = {'hours': 520.}
        p = hm.progress(totals, {'shaper_calibrate': 400.}, {})
        self.assertEqual(p['shaper_calibrate']['used'], 120.)
        self.assertFalse(p['shaper_calibrate']['due'])

    def test_threshold_zero_never_due(self):
        p = hm.progress({'hours': 9999.}, {}, {'air_filter': 0.})
        self.assertFalse(p['air_filter']['due'])

    def test_every_item_has_a_unit_label(self):
        for name, (unit, thr, label) in hm.ITEMS.items():
            self.assertIn(unit, hm.UNIT_LABEL)


if __name__ == "__main__":
    unittest.main()
