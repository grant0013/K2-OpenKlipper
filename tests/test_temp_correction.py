"""Creality's sensor-to-real temperature correction (temp_offset_flag),
ported from the stock Hi klippy for the bed."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import adc_temperature as at  # noqa: E402

HI_BED = at.TempCorrection([25, 40, 53, 69, 83, 108],
                           [25, 38, 50, 65, 80, 110])


def creality_stock(adc_temp, adc=(25, 40, 53, 69, 83, 108),
                   act=(25, 38, 50, 65, 80, 110)):
    """Creality's adc_callback branch logic, written out as shipped."""
    k = [(act[i + 1] - act[i]) / (adc[i + 1] - adc[i]) for i in range(5)]
    if adc[0] < adc_temp < adc[1]:
        return (adc_temp - adc[0]) * k[0] + act[0]
    for i in (1, 2, 3):
        if adc[i] <= adc_temp < adc[i + 1]:
            return (adc_temp - adc[i]) * k[i] + act[i]
    if adc_temp >= adc[4]:
        return (adc_temp - adc[4]) * k[4] + act[4]
    return adc_temp


class FakeConfig:
    error = ValueError

    def __init__(self, opts):
        self.opts = opts

    def get_name(self):
        return "heater_bed"

    def getboolean(self, name, default):
        return self.opts.get(name, default)

    def getfloatlist(self, name, sep=","):
        return [float(v) for v in self.opts[name].split(sep)]


class TempCorrectionTest(unittest.TestCase):
    def test_stock_points(self):
        for s, a in [(40, 38), (53, 50), (69, 65), (83, 80), (108, 110)]:
            self.assertAlmostEqual(HI_BED.correct(s), a)

    def test_matches_creality_code_everywhere(self):
        t = 25.5
        while t < 140:
            self.assertAlmostEqual(HI_BED.correct(t), creality_stock(t),
                                   msg="at %.1f" % t)
            t += 0.5

    def test_below_first_point_unchanged(self):
        self.assertEqual(HI_BED.correct(20.0), 20.0)
        self.assertEqual(HI_BED.correct(-5.0), -5.0)
        self.assertEqual(HI_BED.correct(25.0), 25.0)

    def test_target_80_means_sensor_83(self):
        self.assertAlmostEqual(HI_BED.uncorrect(80), 83)
        self.assertAlmostEqual(HI_BED.uncorrect(60), 53 + (60 - 50) * 16 / 15)

    def test_uncorrect_inverts_correct(self):
        for s in (10, 26, 47.3, 75, 99, 120):
            self.assertAlmostEqual(HI_BED.uncorrect(HI_BED.correct(s)), s)

    def test_off_by_default(self):
        self.assertIsNone(at.load_temp_correction(FakeConfig({})))

    def test_loads_from_config(self):
        c = at.load_temp_correction(FakeConfig({
            "temp_offset_flag": True,
            "adc_temp_list": "25,40,53,69,83,108",
            "actual_temp_list": "25,38,50,65,80,110"}))
        self.assertAlmostEqual(c.correct(83), 80)

    def test_bad_lists_rejected(self):
        for adc, act in [("25,40", "25"), ("25,40,30", "25,38,50"),
                         ("25,40,53", "25,38,30")]:
            with self.assertRaises(ValueError):
                at.load_temp_correction(FakeConfig({
                    "temp_offset_flag": True,
                    "adc_temp_list": adc, "actual_temp_list": act}))


if __name__ == "__main__":
    unittest.main()
