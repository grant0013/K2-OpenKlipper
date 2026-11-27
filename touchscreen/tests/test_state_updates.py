"""Event-driven publication retains fallback, expiry and per-client baselines."""
import copy
import json
import select
import socket
import threading
import time
import unittest
from unittest import mock

import test_service
from state_updates import WakeChannel
from snapshot_codec import SnapshotEncoder


class StateUpdates(unittest.TestCase):
    def setUp(self):
        fixture = test_service.ServiceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.s, self.mr = fixture.s, fixture.mr

    def connect(self):
        server, peer = socket.socketpair()
        peer.settimeout(2)
        reader = peer.makefile('rb')
        thread = threading.Thread(target=self.s.client, args=(server,), daemon=True)
        thread.start()
        def close():
            reader.close()
            peer.close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        baseline = json.loads(reader.readline())
        self.assertNotIn('_screen_patch', baseline)
        return peer, reader, baseline

    def quiet(self, peer, seconds=.3):
        self.assertFalse(select.select([peer], [], [], seconds)[0])

    def test_quiet_client_does_not_construct_snapshots_and_duplicate_updates_stay_quiet(self):
        self.s.status['extruder'] = {'temperature':20, 'target':0}
        with mock.patch.object(self.s, 'snapshot', wraps=self.s.snapshot) as build:
            peer, reader, baseline = self.connect()
            self.quiet(peer, .45)
            self.assertEqual(build.call_count, 1)
            self.s.merge_status({'extruder':{'temperature':21}}, 100)
            patch = json.loads(reader.readline())
            self.assertEqual(patch['status_set']['extruder']['temperature'],21)
            self.assertEqual(build.call_count, 2)
            previous = self.s.status['extruder']
            revision = self.s.state_revision
            self.s.merge_status({'extruder':{'temperature':21}}, 101)
            self.assertIs(self.s.status['extruder'], previous)
            self.assertEqual(self.s.state_revision, revision)
            self.assertEqual(self.s.status_times['extruder'],101)
            self.quiet(peer)
            self.assertEqual(build.call_count, 2)

    def test_burst_coalesces_without_losing_latest_temperature(self):
        with mock.patch.object(self.s, 'snapshot', wraps=self.s.snapshot) as build:
            peer, reader, baseline = self.connect()
            for value in range(20,40):
                self.s.merge_status({'extruder':{'temperature':value}}, value)
            patch = json.loads(reader.readline())
            self.assertEqual(patch['status_set']['extruder']['temperature'],39)
            self.quiet(peer)
            self.assertEqual(build.call_count,2)

    def test_requested_value_expires_without_any_new_status_notification(self):
        self.s.status['extruder'] = {'temperature':20, 'target':0}
        self.s.requested_values[('temperature','extruder')] = (210,time.monotonic()+.35)
        peer, reader, baseline = self.connect()
        self.assertEqual(baseline['heaters'][0]['target'],210)
        patch = json.loads(reader.readline())
        self.assertEqual(patch['set']['heaters'][0]['target'],0)
        self.assertEqual(self.s.requested_values,{})
        self.quiet(peer)

    def test_two_clients_share_builds_but_keep_independent_baselines(self):
        with mock.patch.object(self.s, 'snapshot', wraps=self.s.snapshot) as build:
            first, reader1, baseline1 = self.connect()
            second, reader2, baseline2 = self.connect()
            self.assertEqual(baseline1,baseline2)
            self.assertEqual(build.call_count,1)
            self.s.merge_status({'extruder':{'temperature':42}},100)
            for reader in (reader1,reader2):
                patch=json.loads(reader.readline())
                self.assertEqual(patch['status_set']['extruder']['temperature'],42)
            self.assertEqual(build.call_count,2)
            self.quiet(first)
            self.quiet(second)

    def test_wifi_worker_completion_wakes_without_printer_traffic(self):
        peer, reader, baseline = self.connect()
        self.s.wifi.networks=[('Example',-40,True)]
        self.s.wifi.message='1 networks found'
        self.s.wifi.on_change()
        patch=json.loads(reader.readline())
        self.assertEqual(patch['set']['wifi']['networks'],[['Example',-40,True]])

    def test_http_fallback_response_reaches_renderer_and_stream_recovers(self):
        peer, reader, baseline = self.connect()
        self.mr.request.return_value={}
        self.mr.status_snapshot.side_effect=None
        self.mr.status_snapshot.return_value={'status':dict(self.s.status,extruder={'temperature':150,'target':160}),'eventtime':100}
        self.s.refresh_platform=mock.Mock()
        self.s.refresh_network=mock.Mock()
        self.s.preload_files=mock.Mock()
        self.s.read_policy.update=mock.Mock()
        with mock.patch('service.time.sleep',side_effect=StopIteration):
            with self.assertRaises(StopIteration):self.s.poll()
        self.mr.status_snapshot.assert_called_once()
        patch=json.loads(reader.readline())
        self.assertEqual(patch['status_set']['extruder']['temperature'],150)
        self.s.set_stream_connected(True)
        patch=json.loads(reader.readline())
        self.assertTrue(patch['set']['stream'])
        self.mr.status_snapshot.reset_mock()
        self.s.cfs_initializing=False
        with mock.patch('service.time.sleep',side_effect=StopIteration):
            with self.assertRaises(StopIteration):self.s.poll()
        self.mr.status_snapshot.assert_not_called()

    def test_fault_and_file_notification_wake_without_periodic_snapshot_tick(self):
        peer, reader, baseline=self.connect()
        self.s.event('Fault','Example fault',fault=True)
        patch=json.loads(reader.readline())
        self.assertEqual(patch['set']['notices'][0]['detail'],'Example fault')
        self.s.filelist_changed({'action':'create_file','item':{'root':'gcodes','path':'new.gcode','modified':10,'size':12}})
        patch=json.loads(reader.readline())
        self.assertEqual(patch['set']['files'][0]['path'],'new.gcode')

    def test_revisioned_lists_are_skipped_until_their_owner_changes_revision(self):
        encoder=SnapshotEncoder()
        files=[{'path':'a.gcode'}]
        value={'session':'test','files':files,'status':{}}
        encoder.encode(value,revisions={'files':1})
        with mock.patch('snapshot_codec.same_value',wraps=__import__('snapshot_codec').same_value) as compare:
            value['status']={'extruder':{'temperature':20}}
            patch=json.loads(encoder.encode(value,revisions={'files':1}))
            self.assertNotIn('files',patch['set'])
            self.assertFalse(any(call.args[1] is files for call in compare.call_args_list))
        value['files']=[{'path':'b.gcode'}]
        patch=json.loads(encoder.encode(value,revisions={'files':2}))
        self.assertEqual(patch['set']['files'],value['files'])

    def test_removing_revision_tracking_does_not_leave_stale_revision_cache(self):
        encoder=SnapshotEncoder()
        encoder.encode({'files':['a']},revisions={'files':1})
        encoder.encode({'files':['b']})
        patch=json.loads(encoder.encode({'files':['c']},revisions={'files':1}))
        self.assertEqual(patch['set']['files'],['c'])

    def test_wakeup_coalesces_and_rearms_without_losing_notification(self):
        channel=WakeChannel()
        self.addCleanup(channel.close)
        for _ in range(1000):channel.signal()
        self.assertTrue(select.select([channel.reader],[],[],0)[0])
        channel.drain()
        self.assertFalse(select.select([channel.reader],[],[],0)[0])
        channel.signal()
        self.assertTrue(select.select([channel.reader],[],[],0)[0])

    def test_disconnect_and_resubscription_wake_and_reestablish_live_state(self):
        self.s.stream_connected=True
        peer, reader, baseline=self.connect()
        with self.assertRaises(EOFError):
            self.s.stream_message({'method':'notify_klippy_disconnected'})
        lost=json.loads(reader.readline())
        self.assertFalse(lost['set']['connected'])
        self.assertFalse(lost['set']['stream'])
        state=copy.deepcopy(self.s.status)
        state['extruder']={'temperature':180,'target':190}
        self.s.stream_message({'id':1,'result':{'status':state,'eventtime':101}})
        recovered=json.loads(reader.readline())
        self.assertTrue(recovered['set']['connected'])
        self.assertTrue(recovered['set']['stream'])
        self.assertEqual(recovered['status_set']['extruder']['temperature'],180)

    def test_expiry_is_broadcast_to_all_clients_sharing_the_presentation(self):
        self.s.status['extruder']={'temperature':20,'target':0}
        self.s.requested_values[('temperature','extruder')]=(210,time.monotonic()+.35)
        first, reader1, baseline1=self.connect()
        second, reader2, baseline2=self.connect()
        self.assertEqual(baseline1['heaters'][0]['target'],210)
        self.assertEqual(baseline2['heaters'][0]['target'],210)
        for reader in (reader1,reader2):
            patch=json.loads(reader.readline())
            self.assertEqual(patch['set']['heaters'][0]['target'],0)
        self.quiet(first)
        self.quiet(second)

    def test_live_sections_use_dependency_revisions_without_copying_static_inputs(self):
        self.s.last_state='standby'
        self.s.status['extruder']={'temperature':20,'target':0}
        first=self.s.screen_snapshot()
        with mock.patch('service.deepcopy',wraps=copy.deepcopy) as copies:
            self.s.merge_status({'extruder':{'temperature':21}},101)
            second=self.s.screen_snapshot()
            self.assertEqual(copies.call_count,0)
        self.assertEqual(second['heaters'][0]['current'],21)
        for key in ('slots','axes','availability','prefs','refill','wifi'):
            self.assertIs(first[key],second[key])

    def test_local_settings_and_preferences_invalidate_dependency_caches(self):
        self.s.screen_snapshot()
        with self.s.lock:
            self.s.settings['extruder']['max_temp']=250
            self.s.prefs['brightness']=42
            self.s.notify_state()
        value=self.s.screen_snapshot()
        self.assertEqual(value['heaters'][0]['limits']['maximum'],250)
        self.assertEqual(value['prefs']['brightness'],42)

    def test_new_epoch_removes_cached_cfs_sections(self):
        self.assertTrue(self.s.screen_snapshot()['slots'])
        state={'webhooks':{'state':'ready'},'print_stats':{'state':'standby'}}
        self.s.merge_status(state,101,full=True,new_epoch=True)
        value=self.s.screen_snapshot()
        self.assertEqual(value['slots'],[])
        self.assertEqual(value['cfs_units'],0)
