import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cfs_mod = importlib.import_module("extras.k2_cfs")


class FakeReactor:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def pause(self, until):
        self.now = max(self.now, until)


class FakeGcode:
    def __init__(self):
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def respond_info(self, msg):
        pass


class FakeGcmd:
    def __init__(self, params):
        self.params = params
        self.info = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, minval=None, maxval=None):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


class FakePrinter:
    def __init__(self, reactor):
        self.reactor = reactor

    def get_reactor(self):
        return self.reactor


def make_cfs(arrive_at, stage5_replies=()):
    """A CFS whose filament reaches the nozzle sensor at arrive_at s and
    whose stage-5 sends get stage5_replies in turn (None = no reply)."""
    cfs = cfs_mod.K2CFS.__new__(cfs_mod.K2CFS)
    reactor = FakeReactor()
    cfs.printer = FakePrinter(reactor)
    cfs.gcode = FakeGcode()
    cfs.load_timeout = 90.
    cfs.load_stage5_timeout = 1.
    cfs.load_poll_interval = 2.
    cfs.load_arrival_poll_interval = .1
    cfs.load_sensor_grace = 3.
    cfs.sensor_poll_interval = .1
    cfs.load_engage_length = 5.
    cfs.load_engage_speed = 5.
    cfs.load_extrude_length = 9.
    cfs.load_extrude_speed = 5.
    cfs.load_box_finish_length = 3
    cfs.load_bus_timeout = 15.
    cfs.stall_retry_motion = False
    cfs.last_resp = None
    replies = list(stage5_replies)
    sent = []
    cfs.sent = sent

    def run_actuation(name, payload, timeout=0.2):
        sent.append((reactor.now, name, timeout))
        if name == 'EXTRUDE STAGE 5':
            reply = replies.pop(0) if replies else None
            if reply is None:
                # what _run_actuation does when nothing comes back
                reactor.now += timeout
                cfs.last_resp = b''
                raise RuntimeError("%s failed: sent .. resp (none)" % name)
            if isinstance(reply, Exception):
                cfs.last_resp = b'\xf7'
                raise reply
        cfs.last_resp = b'\xf7\x01\x03\x00\x10\xcd'
        return b'', cfs.last_resp

    cfs._run_actuation = run_actuation
    cfs._load_stage = lambda name, mask, stage, value=0, report_error=True: \
        run_actuation(name, [0xff, 0x10, mask, stage, value])
    cfs._nozzle_has_filament = lambda: reactor.now >= arrive_at \
        if sent and any(s[1] == 'EXTRUDE STAGE 4' for s in sent) else False
    cfs._require_cfs = cfs._require_actuation = cfs._require_idle = \
        lambda *a: None
    cfs._require_safe_motion = lambda *a: (0., 0., 0.)
    cfs._require_position = lambda *a: None
    cfs._require_hot_extruder = lambda *a: None
    cfs.purge_x = cfs.purge_y = 0.
    cfs._run_query = lambda name: {'slots_loaded': [True] * 4,
                                   'box_mode_code': cfs.BOX_MODE_IDLE}
    # selected after stage 7 (mask 1 = slot A)
    cfs._measuring = lambda: {'active_mask': 1 if any(
        s[1] == 'EXTRUDE STAGE 7' for s in sent) else 0}
    cfs.status = {}
    cfs._set_preloading = lambda on: None
    cfs._clear_hub = lambda gcmd: None
    cfs._best_effort_stop = lambda mask: None
    cfs._restore_idle_preload = lambda: None
    return cfs, reactor


class LoadStage5Test(unittest.TestCase):
    def load(self, cfs):
        cfs.cmd_CFS_LOAD(FakeGcmd({'SLOT': 'A', 'CONFIRM': '1'}))

    def test_no_reply_keeps_feeding_and_moves_on_at_arrival(self):
        # The box never answers stage 5 (live 11-01); filament at 12 s
        cfs, reactor = make_cfs(12.)
        self.load(cfs)
        stage5 = [s for s in cfs.sent if s[1] == 'EXTRUDE STAGE 5']
        self.assertTrue(all(t == 1. for _, _, t in stage5))
        stage6 = [s[0] for s in cfs.sent if s[1] == 'EXTRUDE STAGE 6'][0]
        # stage 6 within one sensor poll of the arrival, not at 15 s+
        self.assertLess(stage6 - 12., 0.2)
        self.assertGreaterEqual(stage6, 12.)

    def test_late_arrival_is_not_a_failure(self):
        # Used to fail once one 15 s wait plus the 3 s grace ran out
        cfs, reactor = make_cfs(40.)
        self.load(cfs)
        self.assertTrue(any(s[1] == 'EXTRUDE STAGE 7' for s in cfs.sent))

    def test_no_filament_times_out(self):
        cfs, reactor = make_cfs(1e9)
        with self.assertRaises(RuntimeError) as ctx:
            self.load(cfs)
        self.assertIn("no filament at nozzle sensor", str(ctx.exception))

    def test_box_error_still_fails(self):
        err = cfs_mod.CFSStatusError("EXTRUDE STAGE 5 failed: box status x",
                                     0x0a)
        cfs, reactor = make_cfs(1e9, [err])
        with self.assertRaises(RuntimeError) as ctx:
            self.load(cfs)
        self.assertIn("box status", str(ctx.exception))

    def test_stage0_wheel_timeout_fails_the_load(self):
        cfs, reactor = make_cfs(5.)

        def stage(name, mask, stage, value=0, report_error=True):
            if stage == 0:
                raise cfs_mod.CFSStatusError(
                    "EXTRUDE STAGE 0 failed: box status 0x09", 0x09)
        cfs._load_stage = stage
        with self.assertRaises(RuntimeError):
            self.load(cfs)

    def test_err8_with_filament_at_sensor_is_success(self):
        err = cfs_mod.CFSStatusError("ERR8", cfs_mod.STATUS_ODOMETER_STALLED)
        cfs, reactor = make_cfs(0., [err])
        self.load(cfs)
        self.assertTrue(any(s[1] == 'EXTRUDE STAGE 6' for s in cfs.sent))


if __name__ == '__main__':
    unittest.main()
