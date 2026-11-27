"""Move-page telemetry is interpolated, offset-aware and subscribed on demand."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import model
import test_service


class MotionStateTests(unittest.TestCase):
    def test_live_position_uses_gcode_frame_and_converges_to_target(self):
        status = {'toolhead': {'homed_axes': 'xyz', 'position': [310, 200, 10.2]},
                  'gcode_move': {'gcode_position': [300, 200, 10]},
                  'motion_report': {'live_position': [110, 200, 10.2]}}
        motion = model.motion_state(status)
        self.assertEqual(motion['current'], [100, 200, 10])
        self.assertEqual(motion['target'], [300, 200, 10])
        self.assertEqual(motion['moving'], [True, False, False])
        status['motion_report']['live_position'] = [310, 200, 10.2]
        self.assertEqual(model.motion_state(status)['moving'], [False]*3)

    def test_missing_or_invalid_live_position_falls_back_to_commanded(self):
        for live in (None, [], [float('nan'), 0, 0], ['bad', 0, 0]):
            status = {'gcode_move': {'gcode_position': [100, 200, 10]},
                      'toolhead': {'position': [100, 200, 10]},
                      'motion_report': {'live_position': live}}
            self.assertEqual(model.motion_state(status)['current'], [100, 200, 10])
        self.assertEqual(model.motion_state({}), {})


class MotionSubscriptionTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp

    def test_subscribe_only_on_move_page_and_drop_old_sample_on_return(self):
        self.assertNotIn('motion_report', self.s.subscription_objects())
        self.s.submit({'action': 'view', 'page': 'move'})
        self.assertEqual(self.s.subscription_objects()['motion_report'], ['live_position'])
        self.assertTrue(self.s.subscription_refresh.is_set())
        self.s.status['motion_report'] = {'live_position': [1,2,3]}
        self.s.submit({'action': 'view', 'page': 'heat'})
        self.assertNotIn('motion_report', self.s.subscription_objects())
        self.assertNotIn('motion_report', self.s.status)
        self.assertEqual(self.s.snapshot()['motion'], {})

    def test_disconnected_stream_does_not_freeze_motion_during_http_fallback(self):
        self.s.visible_page = 'move'
        self.s.stream_connected = True
        self.s.status.update({
            'toolhead': {'homed_axes': 'xyz', 'position': [300, 200, 10]},
            'gcode_move': {'gcode_position': [300, 200, 10]},
            'motion_report': {'live_position': [100, 200, 10]},
        })
        self.assertEqual(self.s.snapshot()['motion']['current'], [100, 200, 10])
        self.assertTrue(self.s.snapshot()['motion']['moving'][0])
        self.s.stream_connected = False
        # HTTP snapshots intentionally omit motion_report and preserve other objects.
        self.s.merge_status({'gcode_move': {'gcode_position': [300, 200, 10]}}, full=True)
        motion = self.s.snapshot()['motion']
        self.assertEqual(motion['current'], [300, 200, 10])
        self.assertEqual(motion['moving'], [False]*3)
        self.s.stream_message({'id': 1, 'result': {'status': {
            'toolhead': {'homed_axes': 'xyz', 'position': [300, 200, 10]},
            'gcode_move': {'gcode_position': [300, 200, 10]},
            'motion_report': {'live_position': [250, 200, 10]},
        }, 'eventtime': 100}})
        self.assertEqual(self.s.snapshot()['motion']['current'], [250, 200, 10])

    def test_stream_failure_discards_cached_motion_sample(self):
        self.s.status['motion_report'] = {'live_position': [1, 2, 3]}
        self.s.stream_connected = True
        with mock.patch('service.WebSocket') as websocket, mock.patch('service.time.sleep', side_effect=RuntimeError('stop retry')):
            websocket.return_value.receive.side_effect = OSError('connection lost')
            with self.assertRaisesRegex(RuntimeError, 'stop retry'):
                self.s.stream()
        self.assertFalse(self.s.stream_connected)
        self.assertNotIn('motion_report', self.s.status)

    def test_native_accent_is_preserved_and_new_default_is_blue(self):
        self.assertEqual(self.s.current_preferences()['accent'], '#3d8bfd')
        self.s.prefs['accent'] = '#d9434a'
        self.assertEqual(self.s.current_preferences()['accent'], '#d9434a')
