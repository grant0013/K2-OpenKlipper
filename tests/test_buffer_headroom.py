import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
mq_mod = importlib.import_module("extras.motion_queuing")


class FakeMcu:
    def __init__(self, print_time):
        self.print_time = print_time

    def estimated_print_time(self, eventtime):
        return self.print_time


class FakeReactor:
    NEVER = 9999999999.


def make_queue(print_time, last_sg, need_sg, headroom):
    mq = mq_mod.PrinterMotionQueuing.__new__(mq_mod.PrinterMotionQueuing)
    mq.reactor = FakeReactor()
    mq.mcu = FakeMcu(print_time)
    mq.kin_flush_delay = mq_mod.SDS_CHECK_TIME
    mq.last_flush_time = mq.last_step_gen_time = last_sg
    mq.need_flush_time = mq.need_step_gen_time = need_sg
    mq.do_kick_flush_timer = False
    mq.sg_low_time = mq_mod.BGFLUSH_SG_LOW_TIME
    mq.sg_high_time = mq_mod.BGFLUSH_SG_HIGH_TIME
    mq.set_buffer_headroom(headroom)
    mq.flushes = flushes = []

    def advance(want_flush, want_sg=0.):
        flushes.append(want_sg)
        mq.last_step_gen_time = max(mq.last_step_gen_time, want_sg)
    mq._advance_flush_time = advance
    return mq


class HeadroomTest(unittest.TestCase):
    @staticmethod
    def settled(mq, now=100.):
        # Run the timer the way the reactor would until nothing is due
        for unused in range(50):
            if mq._flush_handler(now) > now:
                break
        return mq.last_step_gen_time - mq.mcu.print_time

    def test_default_matches_upstream(self):
        ahead = self.settled(make_queue(10., 10., 20., 0.))
        self.assertGreaterEqual(ahead, mq_mod.BGFLUSH_SG_LOW_TIME)
        self.assertLessEqual(ahead, mq_mod.BGFLUSH_SG_HIGH_TIME + 0.005)

    def test_headroom_generates_further_ahead(self):
        ahead = self.settled(make_queue(10., 10., 20., .5))
        self.assertGreaterEqual(ahead, mq_mod.BGFLUSH_SG_LOW_TIME + .5)
        self.assertLessEqual(ahead, mq_mod.BGFLUSH_SG_HIGH_TIME + .505)

    def test_headroom_wakes_earlier(self):
        # 0.8 s of steps left: fine at default, already due with headroom
        plain = make_queue(10., 10.8, 20., 0.)
        self.assertGreater(plain._flush_handler(100.), 100.)
        self.assertEqual(plain.flushes, [])
        deep = make_queue(10., 10.8, 20., .5)
        deep._flush_handler(100.)
        self.assertEqual(len(deep.flushes), 1)

    def test_batch_size_is_unchanged(self):
        deep = make_queue(10., 10., 20., .5)
        self.assertAlmostEqual(deep.sg_high_time - deep.sg_low_time,
                               mq_mod.BGFLUSH_SG_HIGH_TIME
                               - mq_mod.BGFLUSH_SG_LOW_TIME)


class ReaderDepthTest(unittest.TestCase):
    def test_reader_fills_past_the_step_window(self):
        # The G-code reader must keep more queued than steps are generated,
        # or the deeper step window has nothing to work on
        toolhead = (pathlib.Path(__file__).parents[1] / "klippy"
                    / "toolhead.py").read_text(encoding='utf-8')
        self.assertEqual(toolhead.count('- BUFFER_TIME_HIGH'), 0)
        self.assertIn('BUFFER_TIME_HIGH + self.buffer_headroom', toolhead)
        self.assertIn('set_buffer_headroom(self.buffer_headroom)', toolhead)


if __name__ == '__main__':
    unittest.main()
