import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class FakeMacro:
    def __init__(self, variables):
        self.variables = variables


class FakeSlots:
    def __init__(self, types):
        self.types = types

    def get_status(self, eventtime):
        return {'slots': {s: {'type': t} for s, t in self.types.items()}}


class FakePrinter:
    def __init__(self, objects):
        self.objects = objects

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


MACRO = FakeMacro({'default': 30,
                   'material': {'PETG': 0, 'PCTG': 0, 'ABS': 0, 'ASA': 0,
                                'PC': 0}})
TYPES = {'A': 'PETG', 'B': 'PLA', 'C': 'Hyper PLA', 'D': 'ABS-GF', 'E': ''}


def make_cfs(macro=MACRO, types=TYPES):
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    objects = {'hark_cfs_slots': FakeSlots(types)}
    if macro is not None:
        objects['gcode_macro _PURGE_FAN'] = macro
    cfs.printer = FakePrinter(objects)
    return cfs


class PurgeFanTest(unittest.TestCase):
    def test_petg_flushes_without_fan(self):
        self.assertEqual(make_cfs()._purge_fan(['A']), 0)

    def test_pla_keeps_creality_fan(self):
        self.assertEqual(make_cfs()._purge_fan(['B']), 30)
        self.assertEqual(make_cfs()._purge_fan(['C']), 30)

    def test_change_uses_the_lower_of_old_and_new(self):
        # PETG coming out first, then PLA: no fan for the whole purge
        self.assertEqual(make_cfs()._purge_fan(['B', 'A']), 0)

    def test_prefix_match_and_unknown_type(self):
        self.assertEqual(make_cfs()._purge_fan(['D']), 0)
        self.assertEqual(make_cfs()._purge_fan(['E']), 30)

    def test_without_the_macro_stock_fan(self):
        self.assertEqual(make_cfs(macro=None)._purge_fan(['A']), 30)

    def test_user_default_and_clamp(self):
        cfs = make_cfs(FakeMacro({'default': 0, 'material': {'PLA': 999}}))
        self.assertEqual(cfs._purge_fan(['A']), 0)
        self.assertEqual(cfs._purge_fan(['B']), 255)


if __name__ == '__main__':
    unittest.main()
