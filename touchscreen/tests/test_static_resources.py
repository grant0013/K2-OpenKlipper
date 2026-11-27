"""Static resources are atomic local files, outside the telemetry stream."""
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from static_resources import ResourceFile
from service import Moonraker


class StaticResources(unittest.TestCase):
    def test_publish_only_replaces_changed_complete_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'catalogue.json'
            resource = ResourceFile(p)
            first = resource.publish([{'name': 'PLA'}])
            self.assertEqual(json.loads(p.read_text()), [{'name': 'PLA'}])
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            stamp = p.stat().st_mtime_ns
            self.assertIs(resource.publish([{'name': 'PLA'}]), first)
            self.assertEqual(p.stat().st_mtime_ns, stamp)
            second = resource.publish([{'name': 'PETG'}])
            self.assertNotEqual(first['revision'], second['revision'])
            self.assertEqual(json.loads(p.read_text()), [{'name': 'PETG'}])
            self.assertFalse(p.with_suffix('.json.new').exists())

    def test_live_query_excludes_material_catalogue(self):
        moonraker = Moonraker('http://127.0.0.1:7126')
        with mock.patch.object(moonraker, 'request', return_value={}) as request:
            moonraker.status_snapshot()
        path = request.call_args.args[0]
        fields = path.split('hark_cfs_slots=', 1)[1].split('&', 1)[0]
        self.assertIn('slots', fields.split(','))
        self.assertIn('revision', fields.split(','))
        self.assertNotIn('materials', fields.split(','))
        self.assertNotIn('library', fields.split(','))
