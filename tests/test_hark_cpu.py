import importlib
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
cpu_mod = importlib.import_module("extras.hark_cpu")


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def getint(self, name, default, **kw):
        return default


class FakeMotion:
    syncemitter_to_name = {1: 'stepper_x', 2: 'stepper_y', 3: 'extruder',
                           4: 'manual_extruder_stepper a_very_long_name_x'}


class FakePrinter:
    def __init__(self):
        self.handlers = {}

    def register_event_handler(self, event, callback):
        self.handlers[event] = callback

    def lookup_object(self, name, default=None):
        return FakeMotion() if name == 'motion_queuing' else default


THREADS = [(10, 'python'), (11, 'stepper_x'), (12, 'stepper_y'),
           (13, 'extruder'), (14, 'serialq mcu'),
           (15, 'a_very_long_nam')]


class SpreadTest(unittest.TestCase):
    def ready(self, affinity, cpu_count=2, set_error=None, nice_error=None):
        printer = FakePrinter()
        mod = cpu_mod.HarkCPU(FakeConfig(printer))
        mod._threads = lambda: iter(THREADS)
        self.moved, self.niced = [], []

        def setaffinity(tid, cpus):
            if set_error:
                raise set_error
            self.moved.append((tid, set(cpus)))

        def setpriority(which, tid, nice):
            if nice_error:
                raise nice_error
            self.niced.append((tid, nice))
        with mock.patch.object(cpu_mod.os, 'cpu_count',
                               lambda: cpu_count, create=True), \
                mock.patch.object(cpu_mod.os, 'sched_getaffinity',
                                  lambda pid: set(affinity), create=True), \
                mock.patch.object(cpu_mod.os, 'sched_setaffinity',
                                  setaffinity, create=True), \
                mock.patch.object(cpu_mod.os, 'setpriority',
                                  setpriority, create=True), \
                mock.patch.object(cpu_mod.os, 'PRIO_PROCESS', 0, create=True):
            printer.handlers['klippy:ready']()
        return mod

    def test_pinned_klippy_frees_the_stepper_threads_only(self):
        mod = self.ready({1})
        self.assertEqual([t for t, c in self.moved], [11, 12, 13, 15])
        self.assertTrue(all(c == {0, 1} for t, c in self.moved))
        self.assertEqual(self.niced, [(t, -5) for t in (11, 12, 13, 15)])
        self.assertEqual(mod.get_status(0.)['stepper_threads_moved'], 4)

    def test_python_and_serial_threads_stay_pinned(self):
        self.ready({1})
        tids = [t for t, c in self.moved]
        self.assertNotIn(10, tids)
        self.assertNotIn(14, tids)

    def test_nothing_happens_on_an_unpinned_host(self):
        mod = self.ready({0, 1, 2, 3}, cpu_count=4)
        self.assertEqual(self.moved, [])
        self.assertEqual(mod.moved, 0)

    def test_a_refused_move_is_not_fatal(self):
        mod = self.ready({1}, set_error=OSError(1, 'not permitted'))
        self.assertEqual(mod.moved, 0)

    def test_a_refused_renice_still_counts_as_moved(self):
        mod = self.ready({1}, nice_error=OSError(13, 'denied'))
        self.assertEqual(mod.moved, 4)


if __name__ == '__main__':
    unittest.main()
