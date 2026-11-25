"""Filament activity is shared, slot-specific and tied to real operation state."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import action_policy
import model
import test_service


class ActivityTests(unittest.TestCase):
    def test_explicit_load_slot_takes_priority_over_old_active_slot(self):
        status = test_service.status()
        result = model.filament_activity(status, [{"action": "load", "slot": "B"}])
        self.assertEqual(result, {"action": "load", "slot": "B"})
        self.assertIsNone(model.filament_activity(status, []))

    def test_driver_phases_are_busy_but_operating_modes_and_failed_change_are_not(self):
        status = test_service.status()
        for mode, action in (("preloading", "load"), ("wrappering", "unload")):
            status["k2_cfs"]["filament_activity"] = {"action": action, "slot": "A"}
            self.assertEqual(
                model.filament_activity(status), {"action": action, "slot": "A"}
            )
            for operation in (
                "load",
                "unload",
                "slot_save",
                "slot_clear",
                "home_files",
            ):
                self.assertTrue(
                    action_policy.reason({"action": operation}, status, True, [])
                )
            self.assertFalse(
                action_policy.reason({"action": "light"}, status, True, [])
            )
        for mode in ("idle", "printing", "error", "preloading", "wrappering"):
            status["k2_cfs"].update(
                box_mode=mode, change_pending="A>B", filament_activity=None
            )
            self.assertIsNone(model.filament_activity(status))

    def test_driver_phase_overrides_aggregate_load_then_restores_it(self):
        status = test_service.status()
        operations = [{"action": "load", "slot": "B"}]
        status["k2_cfs"]["filament_activity"] = {"action": "unload", "slot": "A"}
        self.assertEqual(
            model.filament_activity(status, operations),
            {"action": "unload", "slot": "A"},
        )
        status["k2_cfs"]["filament_activity"] = None
        self.assertEqual(
            model.filament_activity(status, operations), {"action": "load", "slot": "B"}
        )
        self.assertIsNone(model.filament_activity(status))

    def test_unknown_slot_never_marks_an_arbitrary_spool(self):
        status = test_service.status()
        self.assertEqual(
            model.filament_activity(status, [{"action": "load", "slot": "99Z"}]),
            {"action": "load", "slot": ""},
        )
        status["k2_cfs"].update(
            filament_activity={"action": "load", "slot": ""}, active_slot=None
        )
        self.assertEqual(
            model.filament_activity(status), {"action": "load", "slot": ""}
        )


class OperationLifecycleTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_unload_retains_original_slot_after_driver_clears_selection(self):
        with mock.patch("service.threading.Thread"):
            self.s.submit({"action": "unload"})
        self.s.status["k2_cfs"]["active_slot"] = None
        self.s.status["filament_switch_sensor filament_sensor"]["filament_detected"] = (
            False
        )
        self.assertEqual(
            self.s.snapshot()["filament_activity"], {"action": "unload", "slot": "A"}
        )
        self.s.operations.clear()
        self.assertIsNone(self.s.snapshot()["filament_activity"])

    def test_failed_load_releases_busy_state_and_reports_error(self):
        with mock.patch("service.threading.Thread"):
            self.s.submit({"action": "load", "slot": "B"})
        token = next(iter(self.s.operations))
        with mock.patch.object(
            self.s, "execute", side_effect=RuntimeError("CFS jammed")
        ):
            self.s.run_operation({"action": "load", "slot": "B"}, token)
        snapshot = self.s.snapshot()
        self.assertIsNone(snapshot["filament_activity"])
        self.assertTrue(
            any(
                notice["fault"] and "CFS jammed" in notice["detail"]
                for notice in snapshot["notices"]
            )
        )
