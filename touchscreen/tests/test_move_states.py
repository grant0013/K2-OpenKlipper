"""Homing and explicit motor actions remain distinct across UI and service."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import action_policy
import test_native_ui
import test_service


class MoveStateServiceTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_partial_xy_is_not_enough_for_xy_movement(self):
        for homed in ("", "x", "y", "z", "xz", "yz"):
            self.state["toolhead"]["homed_axes"] = homed
            for axis in "XY":
                with self.assertRaises(ValueError):
                    self.s.execute({"action": "jog", "axis": axis, "value": 1})
        self.mr.run_gcode.assert_not_called()

    def test_lock_does_not_home_and_unlock_requires_confirmation(self):
        self.config.update({"stepper_x": {}, "stepper_y": {}, "stepper_z": {}})
        self.state["toolhead"]["homed_axes"] = ""
        self.s.execute({"action": "motors", "enable": True})
        command = self.mr.run_gcode.call_args.args[0]
        self.assertIn("SET_STEPPER_ENABLE STEPPER=stepper_x ENABLE=1", command)
        self.assertNotIn("G28", command)
        with self.assertRaises(ValueError):
            self.s.execute({"action": "motors", "enable": False})
        self.s.execute({"action": "motors", "enable": False, "confirm": True})
        self.assertEqual(self.mr.run_gcode.call_args.args[0], "M84")
        self.assertEqual(self.state["toolhead"]["homed_axes"], "")


class MoveStateUITests(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def state(self, homed):
        data = copy.deepcopy(self.data)
        data["status"]["toolhead"]["homed_axes"] = homed
        data["availability"] = action_policy.availability(data["status"], True, [])
        return data

    def test_unhomed_controls_disabled_but_homing_available(self):
        items, commands = self.render("move", data=self.state(""))
        self.assertFalse(
            any(
                i["enabled"] and i["action"].startswith(("jog:", "position:"))
                for i in items
            )
        )
        self.assertTrue(any('"home"' in i["action"] and i["enabled"] for i in items))
        self.assertEqual(commands, [])

    def test_partial_and_updated_homing_rebuilds_movement_controls(self):
        items, _ = self.render("move", data=self.state("z"))
        self.assertTrue(any(i["action"] == "jog:Z:1" and i["enabled"] for i in items))
        self.assertFalse(any(i["action"] == "jog:X:1" and i["enabled"] for i in items))
        items, _ = self.render(
            "move", [{"snapshot": self.state("xyz")}], data=self.state("")
        )
        self.assertTrue(any(i["action"] == "jog:X:1" and i["enabled"] for i in items))
        items, _ = self.render(
            "move", [{"snapshot": self.state("")}], data=self.state("xyz")
        )
        self.assertFalse(any(i["action"] == "jog:X:1" and i["enabled"] for i in items))

    def test_open_position_editor_disables_set_if_homing_is_lost(self):
        items, commands = self.render(
            "move", ["position:X", {"snapshot": self.state("")}, "number:Set"]
        )
        self.assertFalse(
            next(i for i in items if i["action"] == "number:Set")["enabled"]
        )
        self.assertEqual(commands, [])

    def test_motor_unlock_confirm_and_lock_are_explicit(self):
        items, commands = self.render("move", ["motor_toggle"])
        self.assertTrue(any(i["action"] == "confirmed" for i in items))
        self.assertEqual(commands, [])
        _, commands = self.render("move", ["motor_toggle", "confirmed"])
        self.assertEqual(
            commands, [{"action": "motors", "enable": False, "confirm": True}]
        )
        data = self.state("")
        data["status"]["stepper_enable"]["steppers"] = {"stepper_x": False}
        _, commands = self.render("move", ["motor_toggle"], data=data)
        self.assertEqual(commands, [{"action": "motors", "enable": True}])
