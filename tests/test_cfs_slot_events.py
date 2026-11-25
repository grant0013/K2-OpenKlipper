"""CFS inventory events preserve the old tail's refill/purge identity."""

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "klippy"))
slots_mod = importlib.import_module("extras.hark_cfs_slots")
cfs_mod = importlib.import_module("extras.k2_cfs")


class SlotEvents(unittest.TestCase):
    def test_removal_empties_inventory_without_losing_in_flight_material(self):
        slots = slots_mod.HarkCfsSlots.__new__(slots_mod.HarkCfsSlots)
        old = dict(
            slots_mod.EMPTY,
            loaded=True,
            type="PETG-CF",
            color="123456",
            nozzle_temp=270,
        )
        slots.slots = {
            "A": old,
            "B": dict(old),
            "E": dict(slots_mod.EMPTY, loaded=True),
        }
        slots._save = mock.Mock()
        slots.handle_slot_event("A", "removed")
        self.assertFalse(slots.slots["A"]["loaded"])
        self.assertTrue(slots.slots["B"]["loaded"])
        self.assertEqual(slots.slots["A"]["type"], "PETG-CF")
        self.assertEqual(slots.slots["A"]["nozzle_temp"], 270)
        slots._save.assert_called_once_with()
        # Existing runout and temperature lookups must still see the old tail.
        cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
        cfs.printer = mock.Mock()
        source = mock.Mock()
        source.get_status.return_value = {"slots": slots.slots}
        cfs.printer.lookup_object.return_value = source
        self.assertEqual(cfs._matching_slot("A", [False, True, False, False]), "B")
        self.assertEqual(cfs._slot_nozzle_temp("A"), 270)

    def test_insertion_does_not_invent_a_material_or_move_filament(self):
        slots = slots_mod.HarkCfsSlots.__new__(slots_mod.HarkCfsSlots)
        slots.slots = {
            "A": dict(slots_mod.EMPTY),
            "E": dict(slots_mod.EMPTY, loaded=True),
        }
        slots._save = mock.Mock()
        slots.handle_slot_event("A", "inserted")
        slots.handle_slot_event("A", "rfid_ready")
        slots.handle_slot_event("E", "removed")
        self.assertFalse(slots.slots["A"]["loaded"])
        self.assertTrue(slots.slots["E"]["loaded"])
        slots._save.assert_not_called()

    def test_measuring_preserves_every_event_between_snapshots(self):
        cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
        cfs.status = {}
        cfs.printer = mock.Mock()
        cfs._run_query = mock.Mock(
            side_effect=[
                {"slot_events": {"A": "removed"}},
                {"slot_events": {"A": "inserted"}},
                {},
            ]
        )
        cfs._measuring()
        self.assertEqual(
            cfs.status["slot_event_history"],
            [
                {"id": 1, "slot": "A", "event": "removed"},
                {"id": 2, "slot": "A", "event": "inserted"},
            ],
        )
        self.assertEqual(
            cfs.printer.send_event.call_args_list,
            [
                mock.call("k2_cfs:slot_event", "A", "removed"),
                mock.call("k2_cfs:slot_event", "A", "inserted"),
            ],
        )
        previous = cfs.status["slot_event_history"]
        cfs._run_query.side_effect = [{"slot_events": {"A": "inserted"}}, {}]
        cfs._measuring()
        self.assertEqual(len(previous), 2)
        self.assertEqual(cfs.status["slot_event_history"][-1]["id"], 3)


if __name__ == "__main__":
    unittest.main()
