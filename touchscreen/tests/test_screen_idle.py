"""Timers and physical wake requests without accessing printer hardware."""

import time
import unittest
from unittest import mock

import test_service


class ScreenIdleTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def sleep(self):
        self.s.asleep = True
        self.s.platform["asleep"] = True

    def test_first_activity_publishes_awake_before_power_command_finishes(self):
        self.sleep()
        with mock.patch("service.threading.Thread") as thread:
            self.s.submit({"action": "activity"})
            self.assertFalse(self.s.snapshot()["platform"]["asleep"])
            self.s.submit({"action": "activity"})
            self.assertEqual(thread.call_count, 1)
            thread.return_value.start.assert_called_once()

    def test_print_and_fault_wake_without_a_touch(self):
        for kind in ("printing", "paused", "fault", "shutdown"):
            self.sleep()
            with mock.patch("service.threading.Thread") as thread:
                if kind == "fault":
                    self.s.event("Printer error", "test", fault=True)
                else:
                    delta = (
                        {"print_stats": {"state": kind}}
                        if kind != "shutdown"
                        else {"webhooks": {"state": "shutdown"}}
                    )
                    self.s.merge_status(delta)
                self.assertFalse(self.s.asleep, kind)
                thread.return_value.start.assert_called_once()

    def test_idle_sleep_and_pending_notice_inhibit_sleep(self):
        self.s.prefs["sleep_after"] = 120
        self.s.last_activity = time.monotonic() - 121
        with mock.patch.object(self.s, "screen_power") as power:
            self.s.update_screen_idle()
            power.assert_called_once_with(False)
        self.s.asleep = False
        self.s.notices = [{"id": 1}]
        self.s.last_activity = time.monotonic() - 121
        with mock.patch.object(self.s, "screen_power") as power:
            self.s.update_screen_idle()
            power.assert_not_called()

    def test_never_screen_sleep_and_stale_sleep_request(self):
        self.s.prefs["sleep_after"] = 0
        self.s.last_activity = 0
        with mock.patch("service.subprocess.run") as run:
            self.s.update_screen_idle()
            self.s.screen_power(False)
            run.assert_not_called()

    def test_snapshot_uses_loaded_timer_values(self):
        self.s.status["idle_timeout"]["idle_timeout"] = 3600
        self.s.status["gcode_macro _LIGHT_IDLE"] = {"off_after": 600}
        self.s.prefs.update(idle=1800, light_off=300)
        prefs = self.s.snapshot()["prefs"]
        self.assertEqual(prefs["idle"], 3600)
        self.assertEqual(prefs["light_off"], 600)

    def test_light_preference_rearms_or_cancels_existing_countdown(self):
        settings = dict(self.s.settings, **{"gcode_macro _LIGHT_IDLE": {}})
        with mock.patch.object(self.s, "gcode") as gcode:
            self.s.preference({"key": "light_off", "value": 0}, self.s.status, settings)
            self.assertEqual(
                gcode.call_args_list,
                [
                    mock.call(
                        "SET_GCODE_VARIABLE MACRO=_LIGHT_IDLE VARIABLE=off_after VALUE=0"
                    ),
                    mock.call("_LIGHT_IDLE"),
                ],
            )

    def test_saved_never_does_not_override_actual_printer_timeout(self):
        self.s.prefs["idle"] = 0
        self.assertEqual(self.s.current_preferences()["idle"], 1800)
        self.s.status["idle_timeout"] = {"idle_timeout": 86400}
        self.assertEqual(self.s.current_preferences()["idle"], 86400)
        with mock.patch.object(self.s, "gcode") as gcode:
            self.s.preference(
                {"key": "idle", "value": 86400}, self.s.status, self.s.settings
            )
            gcode.assert_called_once_with("SET_IDLE_TIMEOUT TIMEOUT=86400")
