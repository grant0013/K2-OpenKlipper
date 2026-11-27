"""Resource ownership, independent actions, and stale/double tap protection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import threading
import time
import unittest
from unittest import mock

import action_policy
import test_service


class PolicyTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    # Inherit fixture setup only; do not rerun the ServiceTests methods here.
    def test_contract_covers_controls_and_has_required_fields(self):
        for request in action_policy.controls().values():
            spec = action_policy.describe(request)
            self.assertTrue(spec.label)
            self.assertIsInstance(spec.resources, frozenset)
            self.assertIsInstance(spec.idle_only, bool)
        with self.assertRaises(ValueError):
            action_policy.describe({"action": "raw_gcode"})

    def test_busy_machine_rejects_conflicts_but_accepts_independent_controls(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def run(command):
            if command.startswith("G28"):
                started.set()
                release.wait(3)
                finished.set()

        self.mr.run_gcode.side_effect = run
        try:
            self.s.submit({"action": "home", "axis": "XY"})
            self.assertTrue(started.wait(1))
            availability = self.s.snapshot()["availability"]
            self.assertFalse(availability["jog"]["enabled"])
            self.assertFalse(availability["temperature:extruder"]["enabled"])
            self.assertFalse(availability["fan:part"]["enabled"])
            self.assertTrue(availability["temperature:heater_bed"]["enabled"])
            self.assertTrue(availability["fan:exhaust"]["enabled"])
            with self.assertRaises(action_policy.ActionBusy):
                self.s.submit({"action": "load", "slot": "B"})
            with self.assertRaises(action_policy.ActionBusy):
                self.s.submit({"action": "jog", "axis": "X", "value": 1})
            self.s.submit(
                {"action": "temperature", "heater": "heater_bed", "value": 40}
            )
            deadline = time.monotonic() + 1
            while not self.s.results and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.s.results)
            self.assertEqual(self.s.operation["action"], "home")
            self.s.submit({"action": "restart", "confirm": True})
            deadline = time.monotonic() + 1
            while not self.mr.request.called and time.monotonic() < deadline:
                time.sleep(0.01)
            self.mr.request.assert_called_with("/printer/restart", {})
        finally:
            release.set()
            finished.wait(1)
            deadline = time.monotonic() + 1
            while self.s.operations and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertFalse(self.s.operations)
        results = list(self.s.results)
        self.assertEqual(
            {r["action"] for r in results}, {"home", "temperature", "restart"}
        )
        self.assertEqual(len({r["id"] for r in results}), 3)
        self.assertTrue(self.s.snapshot()["availability"]["jog"]["enabled"])

    def test_filament_owns_only_needed_resources(self):
        with mock.patch("service.threading.Thread"):
            self.s.submit({"action": "load", "slot": "B"})
            available = self.s.snapshot()["availability"]
            for key in (
                "load",
                "unload",
                "home",
                "slot_save",
                "start",
                "temperature:extruder",
                "fan:part",
                "preference:cfs_auto_refill",
            ):
                self.assertFalse(available[key]["enabled"], key)
            for key in (
                "light",
                "temperature:heater_bed",
                "temperature:chamber",
                "fan:side",
                "fan:exhaust",
                "preference:accent",
                "wifi_scan",
            ):
                self.assertTrue(available[key]["enabled"], key)
            self.s.submit({"action": "preference", "key": "accent", "value": "#3d8bfd"})
            with self.assertRaises(action_policy.ActionBusy):
                self.s.submit({"action": "timezone", "name": "Europe/Athens"})

    def test_bounded_admission_reserves_an_interrupt_slot(self):
        with mock.patch("service.threading.Thread"):
            for heater in ("extruder", "heater_bed", "chamber"):
                self.s.submit({"action": "temperature", "heater": heater, "value": 30})
            for fan in ("part", "side", "exhaust"):
                self.s.submit({"action": "fan", "fan": fan, "value": 10})
            with self.assertRaises(action_policy.ActionBusy):
                self.s.submit({"action": "light"})
            self.assertFalse(self.s.snapshot()["availability"]["light"]["enabled"])
            self.s.submit({"action": "restart", "confirm": True})
            self.assertEqual(len(self.s.operations), 7)
            with self.assertRaises(action_policy.ActionBusy):
                self.s.submit({"action": "firmware_restart", "confirm": True})

    def test_unload_is_disabled_only_when_empty_is_known(self):
        self.state["k2_cfs"]["active_slot"] = None
        self.state["filament_switch_sensor filament_sensor"]["filament_detected"] = (
            False
        )
        available = action_policy.availability(self.state, True, [])
        self.assertFalse(available["unload"]["enabled"])
        self.assertTrue(available["load"]["enabled"])
        self.state["k2_cfs"]["active_slot"] = "B"
        self.assertTrue(
            action_policy.availability(self.state, True, [])["unload"]["enabled"]
        )

    def test_old_stage_text_does_not_become_an_unrelated_action_progress(self):
        with mock.patch("service.threading.Thread"):
            self.s.submit({"action": "light"})
            self.s.merge_status(
                {"gcode_macro _STAGE": {"text": "Unloading filament"}}, 10
            )
            self.assertNotIn("detail", self.s.operation)
            token = self.s.operation["id"]
            self.s.run_operation({"action": "light"}, token)
            self.s.submit({"action": "home"})
            self.assertEqual(self.s.operation["label"], "Homing")
            self.assertNotIn("detail", self.s.operation)

    def test_start_failure_releases_reservation(self):
        with mock.patch("service.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError(
                "Could not start worker"
            )
            with self.assertRaises(RuntimeError):
                self.s.submit({"action": "home"})
        self.assertFalse(self.s.operations)
