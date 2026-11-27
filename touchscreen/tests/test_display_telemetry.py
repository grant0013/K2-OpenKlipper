"""Page/availability subscriptions keep hidden motion out of the screen pipeline."""
import copy
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import action_policy
import test_service
from service import Moonraker


class DisplayTelemetry(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def message(self, status, when=100, response=False):
        if response:
            self.s.stream_message({'id': 2, 'result': {'status': status, 'eventtime': when}})
        else:
            self.s.stream_message({'method': 'notify_status_update', 'params': [status, when]})

    def test_printing_home_ignores_motion_and_unused_counters_without_waking_screen(self):
        self.s.status['print_stats'] = {'state': 'printing', 'filename': 'test.gcode', 'print_duration': 10}
        self.s.stream_connected = True
        self.s.merge_status({}, 99)
        revision = self.s.state_revision
        self.message({'toolhead': {'position': [1, 2, 3]},
                      'gcode_move': {'position': [1, 2, 3], 'gcode_position': [1, 2, 3]},
                      'motion_report': {'live_position': [1, 2, 3]},
                      'print_stats': {'filament_used': 15, 'total_duration': 20},
                      'virtual_sdcard': {'file_position': 100}})
        self.assertEqual(self.s.state_revision, revision)
        self.assertEqual(self.s.status['print_stats']['print_duration'], 10)
        self.message({'print_stats': {'print_duration': 11, 'info': {'current_layer': 4}},
                      'extruder': {'temperature': 220}, 'k2_cfs': {'running_out': True}}, 101)
        self.assertGreater(self.s.state_revision, revision)
        self.assertEqual(self.s.status['print_stats']['info']['current_layer'], 4)
        self.assertTrue(self.s.status['k2_cfs']['running_out'])

    def test_move_waits_for_new_baseline_then_uses_fresh_offset_and_coordinates(self):
        self.s.submit({'action': 'view', 'page': 'move'})
        self.assertNotIn('gcode_move', self.s.status)
        self.assertFalse(self.s.snapshot()['availability']['position:X']['enabled'])
        self.assertIn('position', self.s.subscription_objects()['toolhead'])
        self.message({'gcode_move': {'position': [110, 100, 10], 'gcode_position': [100, 100, 10]},
                      'toolhead': {'homed_axes': 'xyz', 'position': [110, 100, 10]},
                      'motion_report': {'live_position': [110, 100, 10]}}, response=True)
        snapshot = self.s.snapshot()
        self.assertTrue(snapshot['availability']['position:X']['enabled'])
        self.assertEqual(snapshot['axes']['x']['maximum'], 342.5)
        self.assertEqual(snapshot['motion']['current'], [100, 100, 10])
        self.s.submit({'action': 'view', 'page': 'home'})
        self.assertNotIn('gcode_move', self.s.status)
        self.assertNotIn('motion_report', self.s.status)
        self.message({'gcode_move': {'gcode_position': [999, 999, 999]}}, 101)
        self.assertNotIn('gcode_move', self.s.status)

    def test_print_pause_and_cancel_renew_motion_using_control_availability(self):
        self.s.submit({'action': 'view', 'page': 'move'})
        for when, state in enumerate(('printing', 'paused', 'standby'), 100):
            self.s.subscription_refresh.clear()
            self.message({'print_stats': {'state': state}}, when)
            motion = 'motion_report' in self.s.subscription_objects()
            self.assertEqual(motion, state == 'standby')
            self.assertNotIn('gcode_move', self.s.status)
            if state != 'paused':self.assertTrue(self.s.subscription_refresh.is_set())
        # Homing/jog operation locks must not suppress their own visible progress.
        self.s.operations[1] = {'action': 'home', 'resources': ['motion'], 'label': 'Homing'}
        self.assertIn('motion_report', self.s.subscription_objects())

    def test_tune_subscribes_only_when_active_and_discards_previous_values(self):
        self.s.submit({'action': 'view', 'page': 'tune'})
        self.assertNotIn('gcode_move', self.s.subscription_objects())
        self.message({'print_stats': {'state': 'printing'}})
        self.assertEqual(set(self.s.subscription_objects()['gcode_move']), {'speed_factor', 'extrude_factor', 'homing_origin'})
        self.assertFalse(self.s.snapshot()['availability']['tune']['enabled'])
        self.message({'gcode_move': {'speed_factor': .9, 'extrude_factor': 1.05, 'homing_origin': [0, 0, .1]}}, 101, True)
        self.assertTrue(self.s.snapshot()['availability']['tune']['enabled'])
        self.s.submit({'action': 'view', 'page': 'home'})
        self.assertNotIn('gcode_move', self.s.status)
        self.s.submit({'action': 'view', 'page': 'tune'})
        self.assertFalse(self.s.snapshot()['availability']['tune']['enabled'])

    def test_faults_and_cfs_remain_subscribed_on_all_pages(self):
        for page in ('home', 'move', 'tune', 'heat', 'files', 'fans'):
            self.s.visible_page = page
            objects = self.s.subscription_objects()
            self.assertIsNone(objects['k2_cfs'])
            self.assertIn('state', objects['webhooks'])
            self.assertIn('temperature', objects['extruder'])
        self.message({'webhooks': {'state': 'shutdown', 'state_message': 'heater fault'}})
        self.assertEqual(self.s.snapshot()['error'], 'heater fault')

    def test_http_fallback_fields_match_subscription_but_action_reads_stay_full(self):
        mr = Moonraker('http://localhost')
        with mock.patch.object(mr, 'request', return_value={}) as request:
            mr.status_snapshot(objects=self.s.subscription_objects())
            path = request.call_args.args[0]
            self.assertNotIn('gcode_move', path)
            self.assertIn('toolhead=homed_axes&', path)
            self.assertIn('virtual_sdcard=progress', path)
            mr.status_snapshot()
            self.assertIn('&gcode_move&', request.call_args.args[0])
        self.s.fresh()
        self.mr.status_snapshot.assert_called_once_with()

    def test_reconnect_rediscovers_state_and_renews_motion_after_readiness_returns(self):
        self.s.visible_page = 'move'
        self.s.connected = False
        self.s.status = {}
        self.assertNotIn('motion_report', self.s.subscription_objects())
        self.s.stream_message({'id': 1, 'result': {'status': copy.deepcopy(self.state), 'eventtime': 100}})
        self.assertIn('motion_report', self.s.subscription_objects())
        self.assertTrue(self.s.subscription_refresh.is_set())
        self.assertNotIn('gcode_move', self.s.status)
        self.message({'gcode_move': self.state['gcode_move']}, 101, True)
        self.assertTrue(self.s.snapshot()['availability']['position:X']['enabled'])

    def test_elapsed_print_time_reuses_control_availability(self):
        self.s.status['print_stats'] = {'state': 'printing', 'print_duration': 10}
        self.s.merge_status({}, 99)
        first = self.s.screen_snapshot()['availability']
        self.s.merge_status({'print_stats': {'print_duration': 11}}, 100)
        self.assertIs(self.s.screen_snapshot()['availability'], first)

    def test_quiet_websocket_is_woken_without_losing_partial_frame_bytes(self):
        import socket
        import threading
        from service import WebSocket
        from state_updates import WakeChannel
        left, right = socket.socketpair()
        wake = WakeChannel()
        ws = WebSocket.__new__(WebSocket)
        ws.sock, ws.buffer, ws.wake = left, b'ab', wake
        renewed = threading.Event()
        ws.on_wake = renewed.set
        result = []
        worker = threading.Thread(target=lambda: result.append(ws.read(4)))
        try:
            worker.start()
            wake.signal()
            self.assertTrue(renewed.wait(1), 'Page demand did not wake quiet WebSocket')
            right.sendall(b'cd')
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [b'abcd'])
        finally:
            right.close()
            worker.join(1)
            left.close()
            wake.close()
