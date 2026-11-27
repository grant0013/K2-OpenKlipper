"""Driver-reported filament phases stay balanced through nesting and failures."""

import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
phase = importlib.import_module("extras.k2_cfs")._filament_activity


class Command:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class Driver:
    def __init__(self):
        self.status = {"active_slot": "A", "filament_activity": None}
        self.observed = []

    @phase("load")
    def load(self, gcmd, fail=False, nested=False):
        self.observed.append(self.status["filament_activity"])
        if nested:
            try:
                self.unload(Command(SLOT="A"), fail=True)
            except RuntimeError:
                self.observed.append(self.status["filament_activity"])
        if fail:
            raise RuntimeError("failed load")
        return 42

    @phase("unload")
    def unload(self, gcmd, fail=False):
        self.observed.append(self.status["filament_activity"])
        if fail:
            raise RuntimeError("failed unload")


class ActivityTests(unittest.TestCase):
    def test_success_publishes_slot_and_clears_on_completion(self):
        driver = Driver()
        self.assertEqual(driver.load(Command(SLOT="b")), 42)
        self.assertEqual(driver.observed, [{"action": "load", "slot": "B"}])
        self.assertIsNone(driver.status["filament_activity"])

    def test_failure_preserves_exception_and_clears_busy_state(self):
        driver = Driver()
        with self.assertRaisesRegex(RuntimeError, "failed unload"):
            driver.unload(Command(), fail=True)
        self.assertEqual(driver.observed, [{"action": "unload", "slot": "A"}])
        self.assertIsNone(driver.status["filament_activity"])

    def test_nested_phase_restores_outer_operation_before_finishing(self):
        driver = Driver()
        driver.load(Command(SLOT="B"), nested=True)
        self.assertEqual(
            driver.observed,
            [
                {"action": "load", "slot": "B"},
                {"action": "unload", "slot": "A"},
                {"action": "load", "slot": "B"},
            ],
        )
        self.assertIsNone(driver.status["filament_activity"])
