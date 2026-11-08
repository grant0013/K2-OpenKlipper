"""A probe that runs out of travel says what to check."""
import importlib
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
mod = importlib.import_module("extras.prtouch_mainline")


class Err(Exception):
    pass


def probe(message):
    class Phoming:
        def probing_move(self, *a, **k):
            raise Err(message)

    objects = {"toolhead": object(), "homing": Phoming()}
    printer = types.SimpleNamespace(
        command_error=Err, is_shutdown=lambda: False,
        lookup_object=lambda name, default=None: objects[name])
    p = mod.PRTouchMainlineProbe.__new__(mod.PRTouchMainlineProbe)
    p.printer = printer
    p.param_helper = types.SimpleNamespace(
        get_probe_params=lambda gcmd: {"lift_speed": 5.})
    p.driver = types.SimpleNamespace(last_no_trigger=True,
                                     last_cleanup_ok=True)
    p.no_trigger_retries = 0
    p.no_trigger_retry_max_distance = 2.
    return p


class Gcmd:
    def __init__(self):
        self.info = []

    def respond_info(self, msg):
        self.info.append(msg)


class Hint(unittest.TestCase):
    def test_no_trigger_gets_the_hint(self):
        gcmd = Gcmd()
        with self.assertRaises(Err):
            probe(mod._NO_TRIGGER_ERROR).probing_move([0, 0, -1], 5, 5, gcmd)
        self.assertIn("KZ_ALIGN_MEASURE", gcmd.info[0])

    def test_other_errors_do_not(self):
        gcmd = Gcmd()
        with self.assertRaises(Err):
            probe("something else").probing_move([0, 0, -1], 5, 5, gcmd)
        self.assertEqual(gcmd.info, [])


if __name__ == "__main__":
    unittest.main()
