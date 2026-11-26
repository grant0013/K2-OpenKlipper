"""The cached encoder must retain complete state and notice in-place edits."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import snapshot_codec


class SnapshotCodecTests(unittest.TestCase):
    def test_cached_fields_are_not_reencoded_for_temperature_updates(self):
        encoder = snapshot_codec.SectionEncoder()
        config = {'extruder': {'max_temp': 350}, 'macro': {'gcode': 'G1 X10'}}
        library = [{'type': 'PLA', 'name': 'Example'}]
        value = {'settings': config, 'status': {'extruder': {'temperature': 20},
                 'hark_cfs_slots': {'library': library}}, 'operation': None}
        self.assertEqual(json.loads(encoder.encode(value)), value)
        with mock.patch.object(snapshot_codec, 'compact_json', wraps=snapshot_codec.compact_json) as dumps:
            value['status']['extruder']['temperature'] = 21
            self.assertEqual(json.loads(encoder.encode(value)), value)
            encoded = [c.args[0] for c in dumps.call_args_list]
            self.assertFalse(any(v is config or v is library or v is value['status']['hark_cfs_slots'] for v in encoded))
        unchanged = encoder.encode(value)
        with mock.patch.object(snapshot_codec, 'compact_json', side_effect=AssertionError('Reencoded unchanged state')):
            self.assertIs(encoder.encode(value), unchanged)

    def test_in_place_mutation_deletions_nulls_and_reconnection(self):
        encoder = snapshot_codec.SectionEncoder()
        value = {'settings': {'limit': 350}, 'files': ['a'], 'status': {'fan': {'speed': 0}}, 'result': None}
        encoder.encode(value)
        value['settings']['limit'] = 280
        value['files'].append('b')
        del value['status']['fan']
        del value['result']
        value['error'] = '温度'
        self.assertEqual(json.loads(encoder.encode(value)), value)
        value['status'] = None
        self.assertEqual(json.loads(encoder.encode(value)), value)
        value['status'] = {'fan': {'speed': 0.5}}
        self.assertEqual(json.loads(encoder.encode(value)), value)
        value['status'] = None
        self.assertEqual(json.loads(encoder.encode(value)), value)
        self.assertEqual(json.loads(snapshot_codec.SectionEncoder().encode(value)), value)

    def test_failed_encoding_can_be_retried(self):
        encoder = snapshot_codec.SectionEncoder()
        encoder.encode({'value': 1})
        with self.assertRaises(TypeError):
            encoder.encode({'value': object()})
        self.assertEqual(json.loads(encoder.encode({'value': 2})), {'value': 2})

class SnapshotPatchTests(unittest.TestCase):
    def test_patch_round_trip_removal_null_and_reconnect(self):
        encoder = snapshot_codec.SnapshotEncoder()
        state = {'session': 'first', 'settings': {'limit': 350}, 'status': {'fan': {'speed': 0}}, 'result': 1}
        assembled = json.loads(encoder.encode(state))
        state['status']['fan']['speed'] = .5
        state['result'] = None
        del state['settings']
        update = json.loads(encoder.encode(state))
        self.assertTrue(update['_screen_patch'])
        assembled.update(update['set'])
        for key in update['remove']:
            assembled.pop(key, None)
        self.assertEqual(assembled, state)
        self.assertIsNone(encoder.encode(state))
        self.assertFalse(json.loads(snapshot_codec.SnapshotEncoder().encode(state)).get('_screen_patch', False))

    def test_boolean_type_changes_are_not_suppressed(self):
        encoder = snapshot_codec.SnapshotEncoder()
        encoder.encode({'value': {'enabled': True}})
        result = json.loads(encoder.encode({'value': {'enabled': 1}}))
        self.assertIs(type(result['set']['value']['enabled']), int)

    def test_immutable_sections_are_reused_but_replacements_are_checked(self):
        encoder = snapshot_codec.SectionEncoder()
        value = {'settings': {'limit': 350}}
        encoder.encode(value, immutable=('settings',))
        with mock.patch.object(snapshot_codec, 'same_value', side_effect=AssertionError('Compared immutable section')):
            encoder.encode(value, immutable=('settings',))
        value['settings'] = {'limit': 280}
        self.assertEqual(json.loads(encoder.encode(value, immutable=('settings',))), value)


    def test_immutable_status_only_reencodes_replaced_objects(self):
        encoder = snapshot_codec.SnapshotEncoder(immutable_status=True)
        library = {"library": [{"type": "PLA", "name": "Example"}]}
        value = {"session": "test", "status": {"extruder": {"temperature": 20}, "hark_cfs_slots": library}}
        encoder.encode(value)
        value["status"] = dict(value["status"], extruder={"temperature": 21})
        with mock.patch.object(snapshot_codec, "same_value", wraps=snapshot_codec.same_value) as compare:
            update = json.loads(encoder.encode(value))
            self.assertEqual(update["set"]["status"]["extruder"]["temperature"], 21)
            self.assertFalse(any(c.args[1] is library for c in compare.call_args_list))


if __name__ == '__main__':
    unittest.main()
