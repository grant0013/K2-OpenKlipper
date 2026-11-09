"""A bed with many abnormal points still gets a mesh (GitHub issue #3)."""
import importlib
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
mod = importlib.import_module("extras.k2_mesh_guard")


class Gcmd:
    def __init__(self):
        self.info = []

    def respond_info(self, msg):
        self.info.append(msg)


def result(z):
    return types.SimpleNamespace(bed_z=z)


def guard(max_points):
    g = mod.K2MeshGuard.__new__(mod.K2MeshGuard)
    points = [(x * 10., y * 10.) for y in range(5) for x in range(5)]
    g.bmc = types.SimpleNamespace(probe_mgr=types.SimpleNamespace(
        get_base_points=lambda: points))
    g.active_gcmd = Gcmd()
    g.threshold = .08
    g.max_points = max_points
    g.finalized = []
    g.original_finalize = lambda positions: g.finalized.append(positions)
    g.confirmed = []
    g._confirm_outliers = (
        lambda gcmd, base, pos, outliers: g.confirmed.extend(outliers))
    return g, points


def bumps(points, heights):
    return [result(heights.get(i, 0.)) for i in range(len(points))]


class TooManyPoints(unittest.TestCase):
    def test_carries_on_and_rechecks_the_worst(self):
        g, points = guard(2)
        heights = {6: .2, 8: .5, 16: .3, 18: .4}
        g._probe_finalize(bumps(points, heights))
        self.assertEqual(len(g.finalized), 1)
        self.assertEqual(sorted(o["index"] for o in g.confirmed), [8, 18])
        self.assertIn("carrying on", g.active_gcmd.info[0])

    def test_within_the_limit_rechecks_them_all(self):
        g, points = guard(6)
        g._probe_finalize(bumps(points, {6: .2, 18: .4}))
        self.assertEqual(len(g.confirmed), 2)
        self.assertEqual(len(g.finalized), 1)


if __name__ == "__main__":
    unittest.main()
