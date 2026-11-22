"""Material mismatches cannot be suggested, selected or printed."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import model
import test_native_ui
import test_service
from PIL import Image


class MaterialServiceTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_material_identity_preserves_additives_and_accepts_case_whitespace(self):
        self.assertTrue(model.material_matches(" pla ", "PLA"))
        self.assertTrue(model.material_matches("PETG\t-CF", "petg-cf"))
        for required, actual in [
            ("PLA", "PETG"),
            ("PLA", "PLA+"),
            ("PETG", "PETG-CF"),
            ("PA6", "PA12"),
        ]:
            self.assertFalse(model.material_matches(required, actual))
        self.assertTrue(model.material_matches(None, "PLA"))

    def test_suggestions_never_fall_back_to_wrong_material(self):
        self.assertEqual(
            model.suggest_mapping([{"tool": 0, "type": "ASA"}], self.state), {}
        )
        self.assertIn(
            model.suggest_mapping([{"tool": 0, "type": " pla "}], self.state)["0"],
            ("A", "B"),
        )

    def test_start_rejects_stale_mismatch_before_any_gcode(self):
        self.s.metadata["part.gcode"] = {"filaments": [{"tool": 0, "type": "PETG"}]}
        with self.assertRaisesRegex(ValueError, "requires PETG"):
            self.s.execute(
                {"action": "start", "path": "part.gcode", "mapping": {"0": "A"}}
            )
        self.mr.run_gcode.assert_not_called()


class MaterialUITests(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def mismatch(self):
        data = copy.deepcopy(self.data)
        for slot in data["slots"]:
            if slot["slot"] == "D":
                slot["type"] = "PETG"
        return data

    def test_picker_disables_wrong_material_and_ignores_direct_selection(self):
        data = self.mismatch()
        items, commands = self.render(
            "file", ["review_mapping", "mapping_pick:0"], data=data
        )
        self.assertFalse(
            next(i for i in items if i["action"] == "mapping_set:D")["enabled"]
        )
        self.assertTrue(
            next(i for i in items if i["action"] == "mapping_set:A")["enabled"]
        )
        items, commands = self.render(
            "file", ["review_mapping", "mapping_pick:0", "mapping_set:D"], data=data
        )
        self.assertTrue(any(i["action"] == "mapping_set:A" for i in items))
        self.assertEqual(commands, [])

    def test_stale_mapping_is_red_and_start_is_disabled(self):
        data = self.mismatch()
        items, commands = self.render(
            "file", ["review_mapping", "start_print"], data=data
        )
        self.assertFalse(any(i["action"] == "start_print" for i in items))
        self.assertEqual(commands, [])
        image = Image.open(self.path / "screen.ppm").convert("RGB")
        self.assertEqual(image.getpixel((500, 94)), (227, 93, 106))

    def test_matching_material_different_color_still_prints(self):
        data = self.mismatch()
        items, commands = self.render(
            "file",
            ["review_mapping", "mapping_pick:0", "mapping_set:A", "start_print"],
            data=data,
        )
        self.assertEqual(commands[-1]["action"], "start")
        self.assertEqual(commands[-1]["mapping"], {"0": "A"})

    def test_native_material_normalization_matches_service_rule(self):
        for required, actual in [
            (" pla ", "PLA"),
            ("PETG\t-CF", "petg-cf"),
            ("PLA", "PLA+"),
            ("PA6", "PA12"),
        ]:
            data = copy.deepcopy(self.data)
            data["metadata"]["filaments"][0]["type"] = required
            for slot in data["slots"]:
                if slot["slot"] == "D":
                    slot["type"] = actual
            items, _ = self.render(
                "file", ["review_mapping", "mapping_pick:0"], data=data
            )
            allowed = next(i for i in items if i["action"] == "mapping_set:D")[
                "enabled"
            ]
            self.assertEqual(
                allowed, model.material_matches(required, actual), (required, actual)
            )

    def test_missing_slicer_type_preserves_manual_mapping(self):
        data = self.mismatch()
        data["metadata"]["filaments"][0].pop("type")
        items, _ = self.render("file", ["review_mapping", "mapping_pick:0"], data=data)
        self.assertTrue(
            next(i for i in items if i["action"] == "mapping_set:D")["enabled"]
        )
