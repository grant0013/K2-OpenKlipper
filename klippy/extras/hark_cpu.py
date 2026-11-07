# Spread klippy's stepper threads over the CPUs of a pinned host
#
# On the K2's two slow cores klippy is started with `taskset 0x2`, so its
# threads inherit CPU1 and the one step generation thread per stepper queue up
# behind each other while Python waits for them. Measured on the T113, letting
# klippy's threads use both cores cut a batch run by 13%. Python and the serial
# threads stay where they are: only the work that runs in parallel moves.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import os


class HarkCPU:
    def __init__(self, config):
        self.printer = config.get_printer()
        # Above Moonraker (nice 0) when they meet on the other core
        self.step_thread_nice = config.getint(
            'step_thread_nice', -5, minval=-20, maxval=19)
        self.moved = 0
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)

    def _handle_ready(self):
        cpus = set(range(os.cpu_count() or 1))
        pinned = os.sched_getaffinity(0)
        if pinned >= cpus:
            # Not pinned (a host PC): the scheduler already has every core
            return
        motion = self.printer.lookup_object('motion_queuing')
        names = {name.split()[-1][:15]
                 for name in motion.syncemitter_to_name.values()}
        moved = 0
        for tid, comm in self._threads():
            if comm in names and self._spread(tid, cpus, comm):
                moved += 1
        self.moved = moved
        logging.info("hark_cpu: %d stepper threads on CPUs %s, klippy on %s",
                     moved, sorted(cpus), sorted(pinned))

    def _threads(self):
        for name in os.listdir('/proc/self/task'):
            try:
                with open('/proc/self/task/%s/comm' % name) as f:
                    yield int(name), f.read().strip()
            except (OSError, ValueError):
                continue

    def _spread(self, tid, cpus, comm):
        try:
            os.sched_setaffinity(tid, cpus)
        except OSError as e:
            logging.info("hark_cpu: can't move %s: %s", comm, e)
            return False
        try:
            os.setpriority(os.PRIO_PROCESS, tid, self.step_thread_nice)
        except OSError as e:
            # Moved but not raised is still better than pinned
            logging.info("hark_cpu: can't renice %s: %s", comm, e)
        return True

    def get_status(self, eventtime):
        return {'stepper_threads_moved': self.moved}


def load_config(config):
    return HarkCPU(config)
