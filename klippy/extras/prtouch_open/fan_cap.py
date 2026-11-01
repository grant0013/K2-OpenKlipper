# Hotend fans down while the nozzle strain gauge is armed (stock
# prtouch_v3 prth_min_fans: SET_FANS / bak_fans)
#
# Fan vibration reaches the gauge: on the K2 Plus (two hotend fans) a tap
# with the fans on fired with nothing touching, even with the nozzle at
# 60 C, and was clean with them off (10-31/11-01). Stock firmware slows
# the hotend fans to prth_min_fans while it probes, on every K2 model.
#
# acquire() at each tap caps every [heater_fan] that serves the extruder
# and, if that changed a running fan, waits settle_time for it to slow
# (8 s: on the K2 Plus 3 s left the first two taps of PROBE_ACCURACY false
# while the fans were still slowing; 8 s gave five taps within 0.0025 mm).
# The cap holds while a tap runs and while taps keep coming, and is lifted
# hold_time after the last one ends, so a whole mesh pays one spin-down.
# (The countdown runs only between taps: started at acquire() it ran out
# during a slow 5 s probe on 11-01 and the fans came back up mid-tap.)
# Fans of other heaters (the K2 Plus chamber heater's fan) are left alone.
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging


class ProbeFanCap:
    def __init__(self, printer, cap, settle_time=8., hold_time=5.):
        self.printer = printer
        self.cap = cap
        self.settle_time = settle_time
        self.hold_time = hold_time
        self.saved = None
        self.timer = None

    def enabled(self):
        return 0. < self.cap < 1.

    def _extruder_fans(self):
        fans = []
        for name, obj in self.printer.lookup_objects('heater_fan'):
            heaters = getattr(obj, 'heater_names', ())
            if any(h.startswith('extruder') for h in heaters):
                fans.append((name, obj))
        return fans

    def acquire(self):
        if not self.enabled():
            return
        reactor = self.printer.get_reactor()
        if self.saved is None:
            self.saved = {}
            slowed = []
            for name, fan in self._extruder_fans():
                self.saved[name] = (fan, fan.fan_speed)
                if fan.fan_speed > self.cap:
                    fan.fan_speed = self.cap
                    if getattr(fan, 'last_speed', 0.) > self.cap:
                        # heater_fan only applies fan_speed on its next
                        # timer tick: apply it now
                        fan.callback(reactor.monotonic())
                        slowed.append(name)
            if slowed:
                logging.info("PRTouch: %s capped at %.0f%% for probing",
                             ", ".join(slowed), self.cap * 100.)
                if self.settle_time:
                    reactor.pause(reactor.monotonic() + self.settle_time)
        # A tap is running: no restore until it ends
        self._set_restore(reactor.NEVER)

    def release(self):
        if self.saved is not None:
            reactor = self.printer.get_reactor()
            self._set_restore(reactor.monotonic() + self.hold_time)

    def _set_restore(self, when):
        reactor = self.printer.get_reactor()
        if self.timer is None:
            self.timer = reactor.register_timer(self._restore, when)
        else:
            reactor.update_timer(self.timer, when)

    def _restore(self, eventtime):
        saved, self.saved = self.saved or {}, None
        for name, (fan, speed) in saved.items():
            fan.fan_speed = speed
            try:
                fan.callback(eventtime)
            except Exception:
                logging.exception("PRTouch: restoring %s failed", name)
        return self.printer.get_reactor().NEVER
