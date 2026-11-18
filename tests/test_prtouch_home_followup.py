"""PRTOUCH_HOME_Z: follow-up taps start home_followup_start above the
contact already measured, and the home still ends retracted at START_Z."""
import importlib
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
pm = importlib.import_module("extras.prtouch_mainline")


class FakeToolhead:
    def __init__(self):
        self.pos = [130., 130., 5., 0.]
        self.moves = []
        self.homed = None

    def get_position(self):
        return list(self.pos)

    def manual_move(self, pos, speed):
        self.pos = list(pos)
        self.moves.append(round(pos[2], 4))

    def wait_moves(self):
        pass

    def set_position(self, pos, homing_axes=""):
        self.pos = list(pos)
        self.homed = homing_axes

    def get_kinematics(self):
        return types.SimpleNamespace(clear_homing_state=lambda axes: None)


class Cmd:
    def __init__(self, **p):
        self.p = p
        self.info = []

    def get_int(self, k, d, **kw):
        return int(self.p.get(k, d))

    def get_float(self, k, d, **kw):
        return float(self.p.get(k, d))

    def respond_info(self, m):
        self.info.append(m)

    def error(self, m):
        return RuntimeError(m)


def make(followup, contacts):
    th = FakeToolhead()
    feed = iter(contacts)
    starts = []

    def probing_move(movepos, speed, start_z, gcmd):
        starts.append(round(th.pos[2], 4))
        z = next(feed)
        th.pos[2] = z
        return z

    objs = {"toolhead": th,
            "gcode": types.SimpleNamespace(Coord=lambda c: c)}
    reactor = types.SimpleNamespace(pause=lambda t: None, monotonic=lambda: 0.)
    printer = types.SimpleNamespace(
        lookup_object=lambda n, d=None: objs.get(n, d),
        get_reactor=lambda: reactor, command_error=RuntimeError)
    self = types.SimpleNamespace(
        printer=printer, config=None,
        param_helper=types.SimpleNamespace(get_probe_params=lambda g: {
            "probe_speed": 1., "lift_speed": 5.}),
        home_samples=3, home_start_z=5., home_tolerance=0.03,
        home_max_touches=5, home_allowed_noisy=1, home_settle_time=0.,
        home_followup_guard=0.5, home_followup_start=followup,
        probe_minimum_z=-1.5, contact_plane_frame=None,
        _z_frame=lambda: "frame", probing_move=probing_move,
        cmd_helper=types.SimpleNamespace())
    return self, th, starts


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self._lmz = pm.probe_helpers.lookup_minimum_z
        pm.probe_helpers.lookup_minimum_z = lambda cfg: -1.5

    def tearDown(self):
        pm.probe_helpers.lookup_minimum_z = self._lmz

    def run_home(self, followup, contacts):
        self_, th, starts = make(followup, contacts)
        gcmd = Cmd()
        pm.PRTouchMainline.cmd_PRTOUCH_HOME_Z(self_, gcmd) \
            if hasattr(pm, "PRTouchMainline") else \
            [c for c in vars(pm).values() if isinstance(c, type)
             and hasattr(c, "cmd_PRTOUCH_HOME_Z")][0].cmd_PRTOUCH_HOME_Z(
                self_, gcmd)
        return th, starts, gcmd

    def test_followups_start_near_the_first_contact(self):
        th, starts, gcmd = self.run_home(1.0, [-0.40, -0.41, -0.40])
        self.assertEqual(starts, [5.0, 0.6, 0.595])
        # ends retracted at START_Z, Z=0 at the median contact
        self.assertAlmostEqual(th.pos[2], 5.0 + 0.40)
        self.assertEqual(th.homed, "z")

    def test_off_keeps_start_z(self):
        th, starts, gcmd = self.run_home(0.0, [-0.40, -0.41, -0.40])
        self.assertEqual(starts, [5.0, 5.0, 5.0])
        self.assertAlmostEqual(th.pos[2], 5.40)

    def test_never_starts_above_start_z(self):
        th, starts, gcmd = self.run_home(5.0, [4.2, 4.21, 4.2])
        self.assertEqual(starts, [5.0, 5.0, 5.0])


if __name__ == "__main__":
    unittest.main()
