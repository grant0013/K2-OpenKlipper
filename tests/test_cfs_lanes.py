import importlib
import json
import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
slots_mod = importlib.import_module("extras.hark_cfs_slots")


def rec(name, filament, loaded=True, colour='FB8C00'):
    r = dict(slots_mod.EMPTY)
    r.update({'loaded': loaded, 'filament': filament, 'name': name,
              'type': 'PLA', 'brand': 'Creality', 'color': colour})
    return r


def make_slots(records, library=None, fitted=True):
    s = slots_mod.HarkCfsSlots.__new__(slots_mod.HarkCfsSlots)
    s.orca_ids = slots_mod.load_orca_ids()
    s._orca_ids_lower = dict((k.lower(), v) for k, v in s.orca_ids.items())
    s.library = library or {}
    s.slots = dict((k, dict(slots_mod.EMPTY)) for k in slots_mod.SLOTS)
    s.slots.update(records)
    s.cfs_fitted = lambda: fitted
    s._lanes_pending = None
    s._lanes_lock = threading.Lock()
    s._lanes_thread = None
    s.moonraker_url = 'http://test'
    return s


class OrcaIdsTest(unittest.TestCase):
    def test_table_has_k2_presets(self):
        ids = slots_mod.load_orca_ids()
        self.assertEqual(ids['Hyper PLA'], 'OFCZsqXg')
        self.assertEqual(ids['CR-PETG'], 'OF3WQaKK')

    def test_creality_name_maps_to_orca_id(self):
        s = make_slots({'A': rec('Hyper PLA', 'cr-01001')})
        lane = s._lane_values()['lane1']
        self.assertEqual(lane['filament_id'], 'OFCZsqXg')
        self.assertEqual(lane['k2_filament'], 'cr-01001')
        self.assertEqual(lane['color'], '#FB8C00')

    def test_custom_filament_uses_its_creality_base(self):
        lib = {'u-my-pla': {'id': 'u-my-pla', 'name': 'My PLA',
                            'based_on': 'Hyper PLA'}}
        s = make_slots({'B': rec('My PLA', 'u-my-pla')}, lib)
        self.assertEqual(s._lane_values()['lane2']['filament_id'],
                         'OFCZsqXg')

    def test_unknown_filament_has_no_id(self):
        s = make_slots({'C': rec('Not A Real Filament', 'cr-99')})
        lane = s._lane_values()['lane3']
        self.assertEqual(lane['filament_id'], '')
        self.assertEqual(lane['material'], 'PLA')

    def test_empty_slot(self):
        s = make_slots({'D': rec('Hyper PLA', 'cr-01001', loaded=False)})
        lane = s._lane_values()['lane4']
        self.assertEqual((lane['filament_id'], lane['material']), ('', ''))

    def test_no_cfs_external_spool_is_lane_1(self):
        s = make_slots({'E': rec('CR-PETG', 'cr-06001')}, fitted=False)
        lanes = s._lane_values()
        self.assertEqual(lanes['lane1']['filament_id'], 'OF3WQaKK')
        self.assertEqual(lanes['lane2']['material'], '')


class PublishOrderTest(unittest.TestCase):
    def test_newest_lanes_win(self):
        posted = []
        slow = threading.Event()

        def fake_urlopen(req, timeout=0):
            body = json.loads(req.data)
            posted.append((body['key'], body['value']['filament_id']))
            slow.wait(0.05)

            class R:
                def read(self):
                    return b''
            return R()

        orig = slots_mod.urllib.request.urlopen
        slots_mod.urllib.request.urlopen = fake_urlopen
        try:
            s = make_slots({})                      # empty first
            s._publish_lanes()
            s.slots['A'] = rec('Hyper PLA', 'cr-01001')
            s._publish_lanes()                      # newer, while posting
            for i in range(100):
                if s._lanes_thread is None:
                    break
                time.sleep(0.02)
        finally:
            slots_mod.urllib.request.urlopen = orig
        last = {}
        for key, fid in posted:
            last[key] = fid
        self.assertEqual(last['lane1'], 'OFCZsqXg')
        self.assertIsNone(s._lanes_thread)


if __name__ == '__main__':
    unittest.main()
