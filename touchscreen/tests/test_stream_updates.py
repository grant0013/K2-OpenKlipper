"""Exercise partial updates in the actual renderer, including retained state."""
import copy
import unittest
import test_native_ui as native


class StreamUpdates(unittest.TestCase):
    setUp = native.NativeUI.setUp
    render = native.NativeUI.render

    def patch(self, values, remove=(), session=None):
        return {'_screen_patch': True, 'session': session or self.data['session'],
                'set': values, 'remove': list(remove)}

    def pixels(self):
        return (self.path / 'screen.ppm').read_bytes()

    def test_partial_heater_update_matches_complete_state(self):
        changed = copy.deepcopy(self.data)
        changed['heaters'][0].update(current=187, target=210)
        self.render('heat', [{'snapshot': changed}])
        expected = self.pixels()
        self.render('heat', [{'snapshot': self.patch({'heaters': changed['heaters']})}])
        self.assertEqual(self.pixels(), expected)

    def test_sequential_updates_and_explicit_removal_match_full_snapshot(self):
        state = copy.deepcopy(self.data)
        state['platform']['starting_file'] = 'example.gcode'
        self.data = state
        changed = copy.deepcopy(state)
        del changed['platform']['starting_file']
        changed['heaters'][1]['target'] = 70
        self.render('heat', [{'snapshot': changed}])
        expected = self.pixels()
        self.render('heat', [{'snapshot': self.patch({'platform': changed['platform']})},
                             {'snapshot': self.patch({'heaters': changed['heaters']})}])
        self.assertEqual(self.pixels(), expected)

    def test_wrong_session_patch_does_not_change_rendered_values(self):
        self.render('heat')
        expected = self.pixels()
        self.render('heat', [{'snapshot': self.patch({'heaters': []}, session='stale')}])
        self.assertEqual(self.pixels(), expected)

    def test_full_reconnect_after_patch_and_reordered_config(self):
        changed = copy.deepcopy(self.data)
        changed['settings'] = dict(reversed(list(changed['settings'].items())))
        self.render('heat', [{'snapshot': changed}])
        expected = self.pixels()
        self.render('heat', [{'snapshot': self.patch({'heaters': []})}, {'snapshot': changed}])
        self.assertEqual(self.pixels(), expected)

    def test_core_resources_load_capabilities_and_reload_on_revision_change(self):
        import json
        resource = self.path / 'core.json'
        resource.write_text(json.dumps({k: self.data[k] for k in ('settings','zones','timezone_presets','tune_limits')}))
        original = copy.deepcopy(self.data)
        self.render('heat', data=original)
        expected = self.pixels()
        for k in ('settings','zones','timezone_presets','tune_limits'):
            self.data.pop(k)
        self.data['resources'] = {'path': str(resource), 'revision': 'first'}
        self.render('heat')
        self.assertEqual(self.pixels(), expected)
        del original['settings']['fan_generic auxiliary_fan']
        self.render('fans', data=original)
        expected = self.pixels()
        replacement = self.path / 'core-revised.json'
        replacement.write_text(json.dumps({'settings': original['settings']}))
        patch = self.patch({'resources': {'path': str(replacement), 'revision': 'second'}})
        self.render('fans', [{'snapshot': patch}])
        self.assertEqual(self.pixels(), expected)

    def test_catalogue_is_read_when_chooser_opens_and_bad_resource_blocks_controls(self):
        import json
        materials = self.data.pop('materials')
        catalogue = self.path / 'materials.json'
        catalogue.write_text(json.dumps(materials))
        self.data['catalogue'] = {'path': str(catalogue), 'revision': 'first'}
        items, _ = self.render('filament', ['edit_slot','route:materials'])
        self.assertTrue(any(i['action'].startswith('material:{') for i in items))
        self.data.pop('settings')
        self.data['resources'] = {'path': str(self.path/'missing.json'), 'revision': 'missing'}
        items, _ = self.render('move')
        self.assertFalse(any(i['action'].startswith('jog:') for i in items))
