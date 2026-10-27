# Print safety watch for the K2 open stack
#
# Stock Creality firmware stops a print when a fan dies or a closed-loop
# motor faults: master-server polled the fan tachometers and paused, and the
# closed motor_control module ran MOTOR_CANCEL_PRINT. Both are gone on the
# open stack, so while a print runs this module
#   - pauses when the hotend fan or the part fan is commanded on but its
#     tachometer (fan_feedback) reads zero for fan_fail_time,
#   - polls the X/Y motor controllers' status (00 0c 0b, as stock does) and
#     cancels the print on a fault (stall, tracking error, overcurrent ...),
#   - polls the extruder controller's status and pauses on a fault: an
#     extruder that cannot follow (clog, jam) trips its tracking protection.
#
#   SAFETY_STATUS [QUERY=1]     report the watch state (QUERY polls the motors)
#   SAFETY_TEST TYPE=FAN|XY|E   run that fault's action as if it happened
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

STATUS = (0x00, 0x0c, 0x0b)
CLEAR_ERRORS = (0x00, 0x0c, 0x05)
ACTIONS = {'cancel': 'cancel', 'pause': 'pause', 'log': 'log'}


class HarkSafety:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.poll_interval = config.getfloat('poll_interval', 3., above=.5)
        self.fan_fail_time = config.getfloat('fan_fail_time', 15., above=3.)
        self.fan_spinup_time = config.getfloat('fan_spinup_time', 10., minval=0.)
        self.part_fan_min = config.getfloat('part_fan_min', .3, minval=0., maxval=1.)
        self.motor_fail_count = config.getint('motor_fail_count', 2, minval=1)
        self.start_grace = config.getfloat('start_grace', 10., minval=0.)
        self.fan_action = config.getchoice('fan_action', ACTIONS, 'pause')
        self.xy_action = config.getchoice('xy_action', ACTIONS, 'cancel')
        self.e_action = config.getchoice('e_action', ACTIONS, 'pause')
        self.xy_addrs = {'x': 0x81, 'y': 0x82}
        self.e_addr = config.getint('e_addr', 0x81)
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.bus = self.fans = self.part_fan = self.hotend_fan = None
        self.was_printing = False
        self.watch_from = 0.
        self.tripped = False
        self.fan_on_since = {}
        self.fan_zero_since = {}
        self.motor_bad = {}
        self.last = {'fans': {}, 'motors': {}, 'event': None}
        self.gcode.register_command('SAFETY_STATUS', self.cmd_SAFETY_STATUS,
                                    desc="Report the print safety watch")
        self.gcode.register_command('SAFETY_TEST', self.cmd_SAFETY_TEST,
                                    desc="Run a safety action: TYPE=FAN|XY|E")
        self.printer.register_event_handler('klippy:ready', self._ready)

    def _ready(self):
        self.bus = self.printer.lookup_object('k2_motor_bus', None)
        self.fans = self.printer.lookup_object('fan_feedback', None)
        self.part_fan = self.printer.lookup_object('fan', None)
        self.hotend_fan = self.printer.lookup_object('heater_fan hotend_fan',
                                                     None)
        self.reactor.register_timer(self._poll, self.reactor.NOW)

    # --- polling -------------------------------------------------------
    def _poll(self, eventtime):
        try:
            state = self.print_stats.get_status(eventtime).get('state')
            printing = state == 'printing'
            if printing and not self.was_printing:
                # New print or resume: stale fault bits must not trip us
                self._clear_motor_errors()
                self.watch_from = eventtime + self.start_grace
                self.tripped = False
                self.fan_on_since.clear(); self.fan_zero_since.clear()
                self.motor_bad.clear()
            self.was_printing = printing
            if printing and not self.tripped and eventtime >= self.watch_from:
                self._check_fans(eventtime)
                if not self.tripped:
                    self._check_motors(eventtime)
        except Exception:
            logging.exception("hark_safety: poll failed")
        return eventtime + self.poll_interval

    def _check_fans(self, eventtime):
        if self.fans is None:
            return
        tach = self.fans.get_status(eventtime)
        expect = {}
        if self.hotend_fan is not None:
            expect['hotend fan'] = (
                self.hotend_fan.get_status(eventtime).get('speed', 0.) > 0.,
                tach.get('fan1_speed', 0))
        if self.part_fan is not None:
            expect['part fan'] = (
                self.part_fan.get_status(eventtime).get('speed', 0.)
                >= self.part_fan_min, tach.get('fan2_speed', 0))
        self.last['fans'] = dict((k, {'on': on, 'tach': t})
                                 for k, (on, t) in expect.items())
        for name, (on, speed) in expect.items():
            if not on:
                self.fan_on_since.pop(name, None)
                self.fan_zero_since.pop(name, None)
                continue
            since = self.fan_on_since.setdefault(name, eventtime)
            if eventtime - since < self.fan_spinup_time or speed > 0:
                self.fan_zero_since.pop(name, None)
                continue
            zero = self.fan_zero_since.setdefault(name, eventtime)
            if eventtime - zero >= self.fan_fail_time:
                self._trip(self.fan_action,
                           "the %s is switched on but has not turned for "
                           "%.0f s" % (name, eventtime - zero))
                return

    def _status(self, name):
        try:
            if name == 'e':
                if self.bus.e_send is None:
                    return None
                frame, resp = self.bus.e_xfer(self.e_addr, STATUS)
            else:
                frame, resp = self.bus.xfer(self.xy_addrs[name], STATUS)
        except Exception as e:
            logging.info("hark_safety: %s status query failed: %s", name, e)
            return None
        if not resp:
            return None
        resp = bytes(resp)
        i = resp.find(bytes(STATUS[:2]))
        if i < 0:
            return None
        return resp[i + 2:i + 10]

    def _check_motors(self, eventtime):
        if self.bus is None:
            return
        for name in ('x', 'y', 'e'):
            data = self._status(name)
            self.last['motors'][name] = data.hex(' ') if data else None
            if data is None:
                continue        # no answer: never trip on a missed reply
            if any(data):
                bad = self.motor_bad.get(name, 0) + 1
                self.motor_bad[name] = bad
                logging.info("hark_safety: %s motor status %s (%d)",
                             name, data.hex(' '), bad)
                if bad >= self.motor_fail_count:
                    if name == 'e':
                        self._trip(self.e_action,
                                   "the extruder motor reports a fault (%s): "
                                   "likely a clog or jam"
                                   % data.hex(' '))
                    else:
                        self._trip(self.xy_action,
                                   "the %s motor reports a fault (%s): stall, "
                                   "collision or driver error"
                                   % (name.upper(), data.hex(' ')))
                    return
            else:
                self.motor_bad[name] = 0

    def _clear_motor_errors(self):
        if self.bus is None:
            return
        for name, addr in self.xy_addrs.items():
            try:
                self.bus.xfer(addr, CLEAR_ERRORS, timeout=.05)
            except Exception:
                pass
        try:
            if self.bus.e_send is not None:
                self.bus.e_xfer(self.e_addr, CLEAR_ERRORS)
        except Exception:
            pass

    # --- actions -------------------------------------------------------
    def _trip(self, action, why):
        self.last['event'] = "%s: %s" % (action, why)
        logging.warning("hark_safety: %s -> %s", why, action)
        if action == 'log':
            self.gcode.respond_info("Safety watch (log only): %s" % why)
            return
        self.tripped = True
        if action == 'pause':
            msg = "Print paused by the safety watch: %s" % why
            script = "PAUSE"
        else:
            msg = "Print cancelled by the safety watch: %s" % why
            script = "CANCEL_PRINT\nTURN_OFF_HEATERS"
        self.gcode.respond_raw("!! %s" % msg)
        try:
            self.gcode.run_script(script)
        except Exception:
            logging.exception("hark_safety: %s failed", script)

    # --- commands ------------------------------------------------------
    def cmd_SAFETY_STATUS(self, gcmd):
        if gcmd.get_int('QUERY', 0, minval=0, maxval=1) and self.bus:
            for name in ('x', 'y', 'e'):
                data = self._status(name)
                self.last['motors'][name] = data.hex(' ') if data else None
        gcmd.respond_info(
            "Safety watch: fans %s, motors %s, actions fan=%s xy=%s e=%s, "
            "last event %s" % (self.last['fans'], self.last['motors'],
                               self.fan_action, self.xy_action, self.e_action,
                               self.last['event']))

    def cmd_SAFETY_TEST(self, gcmd):
        kind = gcmd.get('TYPE').upper()
        actions = {'FAN': self.fan_action, 'XY': self.xy_action,
                   'E': self.e_action}
        if kind not in actions:
            raise gcmd.error("TYPE must be FAN, XY or E")
        self._trip(actions[kind], "SAFETY_TEST %s (simulated)" % kind)

    def get_status(self, eventtime):
        return {'tripped': self.tripped, 'last_event': self.last['event'],
                'fans': self.last['fans'], 'motors': self.last['motors']}


def load_config(config):
    return HarkSafety(config)
