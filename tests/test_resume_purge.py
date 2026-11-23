import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class Cmd:
    def __init__(self, params):
        self.params = params

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))


def make_cfs(fans):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    scripts = []

    class G:
        def run_script_from_command(self, script):
            scripts.append(script)
    cfs.gcode = G()
    cfs.resume_purge_length = 80.
    cfs.purge_extrude_speed = 6.
    cfs.purge_cool_fan = 1.
    cfs.purge_cool_time = 5.
    for name in ('_require_hot_extruder', '_raise_to_gate',
                 '_ensure_purge_position'):
        setattr(cfs, name, lambda *a, n=name: scripts.append(n))
    cfs._fan_speeds = lambda: dict(fans)
    cfs._set_fans = lambda s: scripts.append(('fans', s))
    return cfs, scripts


class ResumePurgeTest(unittest.TestCase):
    def test_purges_fan_off_then_cools_wipes_and_restores_fans(self):
        cfs, scripts = make_cfs({'fan': .4})
        cfs.cmd_RESUME_PURGE(Cmd({}))
        self.assertEqual(scripts[:3], ['_require_hot_extruder',
                                       '_raise_to_gate',
                                       '_ensure_purge_position'])
        purge = scripts[3]
        self.assertLess(purge.index('M106 S0'), purge.index('G1 E80.000'))
        self.assertIn('M106 S255\nG4 P5000', purge)
        self.assertEqual(scripts[4], 'NOZZLE_WIPE CONFIRM=1')
        self.assertEqual(scripts[5], ('fans', {'fan': .4}))

    def test_length_zero_does_nothing(self):
        cfs, scripts = make_cfs({})
        cfs.cmd_RESUME_PURGE(Cmd({'LENGTH': 0}))
        self.assertEqual(scripts, [])


if __name__ == '__main__':
    unittest.main()
