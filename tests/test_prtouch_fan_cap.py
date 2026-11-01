import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras" / "prtouch_open"
    / "fan_cap.py"
)
SPEC = importlib.util.spec_from_file_location("fan_cap", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.now = 100.
        self.paused = []
        self.timers = {}

    def monotonic(self):
        return self.now

    def pause(self, until):
        self.paused.append(until - self.now)
        self.now = until

    def register_timer(self, cb, when):
        handle = object()
        self.timers[handle] = [cb, when]
        return handle

    def update_timer(self, handle, when):
        self.timers[handle][1] = when

    def run_until(self, t):
        self.now = t
        for handle, (cb, when) in list(self.timers.items()):
            if when <= t:
                self.timers[handle][1] = cb(t)


class FakeHeaterFan:
    """heater_fan: runs at fan_speed while its heater is hot."""

    def __init__(self, heater, speed=1., hot=True):
        self.heater_names = [heater]
        self.fan_speed = speed
        self.hot = hot
        self.last_speed = speed if hot else 0.
        self.calls = 0

    def callback(self, eventtime):
        self.calls += 1
        self.last_speed = self.fan_speed if self.hot else 0.
        return eventtime + 1.


class FakePrinter:
    def __init__(self, fans):
        self.reactor = FakeReactor()
        self.fans = fans

    def get_reactor(self):
        return self.reactor

    def lookup_objects(self, module):
        return list(self.fans.items())


def make(cap=0.6, **fans):
    printer = FakePrinter(
        dict(("heater_fan %s" % k, v) for k, v in fans.items()))
    return MODULE.ProbeFanCap(printer, cap, settle_time=2., hold_time=5.), \
        printer.reactor


class FanCapTest(unittest.TestCase):
    def test_hot_fans_capped_then_restored(self):
        hotend = FakeHeaterFan("extruder")
        cap, reactor = make(hotend_fan=hotend)
        cap.acquire()
        self.assertEqual(hotend.fan_speed, 0.6)
        self.assertEqual(hotend.last_speed, 0.6)
        self.assertEqual(reactor.paused, [2.])
        cap.release()
        reactor.run_until(reactor.now + 5.1)
        self.assertEqual(hotend.fan_speed, 1.)
        self.assertEqual(hotend.last_speed, 1.)

    def test_one_spin_down_for_many_taps(self):
        hotend = FakeHeaterFan("extruder")
        cap, reactor = make(hotend_fan=hotend)
        for _ in range(20):
            cap.acquire()
            reactor.now += 1.5
            cap.release()
        self.assertEqual(reactor.paused, [2.])
        self.assertEqual(hotend.fan_speed, 0.6)
        reactor.run_until(reactor.now + 5.1)
        self.assertEqual(hotend.fan_speed, 1.)

    def test_cold_fan_capped_without_waiting(self):
        hotend = FakeHeaterFan("extruder", hot=False)
        cap, reactor = make(hotend_fan=hotend)
        cap.acquire()
        self.assertEqual(hotend.fan_speed, 0.6)
        self.assertEqual(reactor.paused, [])

    def test_chamber_heater_fan_left_alone(self):
        hotend = FakeHeaterFan("extruder")
        ptc = FakeHeaterFan("chamber_heater")
        cap, reactor = make(hotend_fan=hotend, ptc_fan=ptc)
        cap.acquire()
        self.assertEqual(ptc.fan_speed, 1.)
        self.assertEqual(hotend.fan_speed, 0.6)

    def test_cap_of_zero_or_one_does_nothing(self):
        for value in (0., 1.):
            hotend = FakeHeaterFan("extruder")
            cap, reactor = make(cap=value, hotend_fan=hotend)
            cap.acquire()
            self.assertEqual(hotend.fan_speed, 1.)
            self.assertEqual(reactor.paused, [])

    def test_slower_user_setting_kept(self):
        hotend = FakeHeaterFan("extruder", speed=0.4)
        cap, reactor = make(hotend_fan=hotend)
        cap.acquire()
        self.assertEqual(hotend.fan_speed, 0.4)
        self.assertEqual(reactor.paused, [])
        reactor.run_until(reactor.now + 5.1)
        self.assertEqual(hotend.fan_speed, 0.4)


if __name__ == "__main__":
    unittest.main()
