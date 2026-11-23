"""Wi-Fi form routing and connection results, without touching a real network."""

import unittest
from unittest import mock

import test_native_ui
import test_service


class WifiServiceTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_success_updates_address_and_shows_a_timed_confirmation(self):
        self.s.wifi.message = "Connected to Demo network (192.0.2.21)"
        self.s.wifi.status = mock.Mock(
            return_value={"ssid": "Demo network", "ip_address": "192.0.2.21"}
        )
        self.s.wifi.connect = mock.Mock(
            side_effect=lambda ssid, password, done: done(True)
        )
        self.s.run_operation(
            {"action": "wifi_join", "ssid": "Demo network", "password": "testpass"}, 10
        )
        self.assertTrue(self.s.result["success"])
        self.assertEqual(self.s.platform["wifi"]["ip_address"], "192.0.2.21")
        self.assertEqual(self.s.notices[0]["title"], "Wi-Fi connected")
        self.assertTrue(self.s.notices[0]["timed"])
        self.mr.run_gcode.assert_not_called()

    def test_failed_connection_shows_error_without_success_confirmation(self):
        self.s.wifi.message = "Could not join the network"
        self.s.wifi.connect = mock.Mock(
            side_effect=lambda ssid, password, done: done(False)
        )
        self.s.run_operation(
            {"action": "wifi_join", "ssid": "Demo network", "password": "testpass"}, 11
        )
        self.assertFalse(self.s.result["success"])
        self.assertEqual(self.s.notices[0]["title"], "Wi-Fi connection failed")
        self.assertTrue(self.s.notices[0]["fault"])
        self.assertNotIn("Wi-Fi connected", [n["title"] for n in self.s.notices])


class WifiUITests(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def test_secured_network_opens_password_form_and_submits_to_connect(self):
        select = 'wifi_select:{"ssid":"Demo network","secured":true}'
        items, commands = self.render("networks", [select])
        self.assertTrue(any(i["action"] == "key_eye" for i in items))
        self.assertEqual(commands, [])
        actions = [select] + ["key:" + letter for letter in "testpass"] + ["key_done"]
        items, commands = self.render("networks", actions)
        self.assertEqual(
            commands,
            [{"action": "wifi_join", "ssid": "Demo network", "password": "testpass"}],
        )
        self.assertTrue(any(i["action"] == "scan_wifi" for i in items))
