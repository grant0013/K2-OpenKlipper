"""Real LVGL renders: artwork colours follow slot data, not selection/accent."""
import copy
import sys
import unittest
from pathlib import Path
from PIL import Image, ImageChops
import test_native_ui

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'developer-tools'))
from demo_snapshot import preview_case


class PrinterSpoolColours(unittest.TestCase):
    setUp = test_native_ui.NativeUI.setUp
    render = test_native_ui.NativeUI.render

    def frame(self, data, updates=None):
        self.render('home', updates, data)
        return Image.open(self.path / 'screen.ppm').copy()

    def colour_count(self, image, rgb):
        return sum(pixel[:3] == rgb for pixel in image.crop((265, 75, 410, 130)).get_flattened_data())

    def test_neutral_base_preserves_printer_colours_outside_spool_region(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
        from storage import prepare_image
        root = Path(__file__).resolve().parents[1]
        reference = Image.open(prepare_image(root/'assets/printer-with-cfs.png', self.path,
                                             'reference', (400,286), lighten=True)).convert('RGBA')
        neutral = Image.open(root/'assets/generated/printer-with-cfs-base.png').convert('RGBA')
        self.assertEqual(reference.getpixel((270,150)), neutral.getpixel((270,150)))
        r,g,b,_ = neutral.getpixel((162,55))
        self.assertEqual((r,g,b), (r,r,r))

    def test_black_white_and_bright_colours_render_in_the_illustration(self):
        data = preview_case('idle')
        for slot, colour in zip(data['slots'], ('00FFFF','000000','FFFFFF','FF6A00')):
            slot['color'] = colour
        image = self.frame(data)
        for rgb in ((0,255,255),(0,0,0),(255,255,255),(255,106,0)):
            self.assertGreater(self.colour_count(image, rgb), 25)

    def test_empty_unknown_and_offline_slots_do_not_keep_previous_colours(self):
        data = preview_case('idle')
        for slot in data['slots']:
            slot['loaded'] = False
        empty = self.frame(data)
        self.assertGreater(self.colour_count(empty, (39,49,62)), 100)
        for slot in data['slots']:
            slot.update(loaded=True, color='invalid')
        # No purple/red/green/yellow fringe from the original static bitmap.
        for pixel in empty.crop((281,86,383,123)).get_flattened_data():
            self.assertLessEqual(max(pixel[:3])-min(pixel[:3]), 35)
        unknown = self.frame(data)
        self.assertGreater(self.colour_count(unknown, (118,129,141)), 100)
        data['status']['k2_cfs']['online'] = False
        offline = self.frame(data)
        self.assertEqual(empty.crop((265,75,410,130)).tobytes(), offline.crop((265,75,410,130)).tobytes())

    def test_live_colour_patch_matches_fresh_render_including_revision(self):
        data = preview_case('idle')
        changed = copy.deepcopy(data)
        for slot in changed['slots']:
            slot['color'] = '00FFFF'
        changed['status']['hark_cfs_slots']['revision'] = 99
        expected = self.frame(changed)
        patch = {'_screen_patch': True, 'session': data['session'],
                 'set': {'slots': changed['slots'], 'status': changed['status']},
                 'remove': []}
        updated = self.frame(data, [{'snapshot': patch}])
        self.assertIsNone(ImageChops.difference(expected, updated).getbbox())

    def test_external_spool_colour_and_material_edits_refresh_home(self):
        data = preview_case('no-cfs')
        data['slots'] = [{'slot':'E', 'loaded':True, 'type':'PLA', 'color':'00FFFF'}]
        data['status']['filament_switch_sensor filament_sensor']['filament_detected'] = True
        for field, value in (('color','FF6A00'), ('type','PETG')):
            with self.subTest(field=field):
                changed = copy.deepcopy(data)
                changed['slots'][0][field] = value
                expected = self.frame(changed)
                self.assertIsNotNone(ImageChops.difference(expected, self.frame(data)).getbbox())
                patch = {'_screen_patch':True, 'session':data['session'],
                         'set':{'slots':changed['slots']}, 'remove':[]}
                updated = self.frame(data, [{'snapshot':patch}])
                self.assertIsNone(ImageChops.difference(expected, updated).getbbox())

    def test_active_unit_drives_artwork_and_sidebar_together(self):
        data = preview_case('multi-cfs')
        for slot in data['slots']:
            slot['color'] = '00FFFF' if slot['slot'].startswith('1') else 'FF6A00'
        first = self.frame(data)
        data['status']['k2_cfs']['active_slot'] = '2A'
        second = self.frame(data)
        self.assertGreater(self.colour_count(first, (0,255,255)), 100)
        self.assertGreater(self.colour_count(second, (255,106,0)), 100)

    def test_print_preview_is_not_tinted(self):
        data = preview_case('printing')
        with_layers = self.frame(data)
        data['platform']['printer_spools'] = False
        self.assertEqual(with_layers.tobytes(), self.frame(data).tobytes())
