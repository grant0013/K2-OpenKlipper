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
        actions = (
            [select]
            + ["key:" + letter for letter in "testpass"]
            + ["key_eye", "key_done"]
        )
        items, commands = self.render("networks", actions)
        self.assertEqual(
            commands,
            [{"action": "wifi_join", "ssid": "Demo network", "password": "testpass"}],
        )
        self.assertFalse(any(i["action"] == "scan_wifi" for i in items))

    def test_single_symbols_page_covers_printable_password_characters_without_more(
        self,
    ):
        select = 'wifi_select:{"ssid":"Demo network","secured":true}'
        characters = set()
        for modifiers in ([], ["key_shift"], ["key_symbols"]):
            items, _ = self.render("networks", [select] + modifiers)
            characters.update(
                i["action"][4:] for i in items if i["action"].startswith("key:")
            )
            self.assertFalse(any(i["action"] == "key_more" for i in items))
            for item in items:
                self.assertGreaterEqual(min(item["rect"][2:]), 52)
        self.assertEqual(characters, {chr(code) for code in range(32, 127)})

    def test_password_length_stays_in_sync_with_the_field_limit(self):
        select = 'wifi_select:{"ssid":"Demo network","secured":true}'
        _, commands = self.render(
            "networks", [select] + ["key:x"] * 65 + ["key_back", "key:z", "key_done"]
        )
        self.assertEqual(
            commands,
            [
                {
                    "action": "wifi_join",
                    "ssid": "Demo network",
                    "password": "x" * 62 + "z",
                }
            ],
        )

    def test_password_field_keeps_its_height_and_centers_text_with_visibility_control(
        self,
    ):
        from PIL import Image

        items, _ = self.render(
            "networks", ['wifi_select:{"ssid":"Demo network","secured":true}']
        )
        eye = next(i["rect"] for i in items if i["action"] == "key_eye")
        image = Image.open(self.path / "screen.ppm").convert("RGB")
        accent = tuple(
            bytes.fromhex(self.data["prefs"].get("accent", "#3d8bfd").lstrip("#"))
        )
        borders = [y for y in range(60, 150) if image.getpixel((400, y)) == (89, 100, 113)]
        self.assertTrue(borders)
        top, bottom = min(borders), max(borders) + 1
        self.assertGreaterEqual(bottom - top, eye[3] + 8)
        self.assertLessEqual(abs((top + bottom) / 2 - (eye[1] + eye[3] / 2)), 1)
        ink = (
            image.crop((30, top + 2, 350, bottom - 2))
            .convert("L")
            .point(lambda v: 255 if v > 110 else 0)
            .getbbox()
        )
        self.assertIsNotNone(ink)
        self.assertLessEqual(
            abs(top + 2 + (ink[1] + ink[3]) / 2 - (eye[1] + eye[3] / 2)), 3
        )
