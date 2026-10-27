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
# Only an error code in the reply trips an action, named in the message.
# Warning codes (supply voltage low, controller MCU hot, comms) are reported
# once and the print carries on. A missing or invalid reply never trips.
#
#   SAFETY_STATUS [QUERY=1]     report the watch state (QUERY polls the motors)
#   SAFETY_TEST TYPE=FAN|XY|E   run that fault's action as if it happened
#
# Reply parsing and fault decoding (in k2_motor_proto.py): portions adapted
# from Jacob10383's K2 Plus custom firmware
# (https://github.com/Jacob10383/k2-plus-custom-firmware,
# extras/motor_control.py), GPLv3.
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

from . import k2_motor_proto as proto

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
        self.motor_warned = {}
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
                self.motor_bad.clear(); self.motor_warned.clear()
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
        """Decoded protection status, or None when the motor gives no valid
        reply (no answer, bad CRC, someone else's frame)."""
        try:
            if name == 'e':
                if self.bus.e_send is None:
                    return None
                return self.bus.query_protection('e', self.e_addr)
            return self.bus.query_protection(name, self.xy_addrs[name])
        except Exception as e:
            logging.info("hark_safety: %s status query failed: %s", name, e)
            return None

    def _note_status(self, name, detail):
        if detail is None:
            self.last['motors'][name] = None
            return
        self.last['motors'][name] = {
            'status': detail['status'], 'raw': detail['raw'],
            'errors': list(detail['errors']),
            'warnings': list(detail['warnings']),
            'summary': proto.describe_protection(detail)}

    def _check_motors(self, eventtime):
        if self.bus is None:
            return
        for name in ('x', 'y', 'e'):
            detail = self._status(name)
            self._note_status(name, detail)
            if detail is None:
                continue        # no answer: never trip on a missed reply
            if detail['has_error']:
                bad = self.motor_bad.get(name, 0) + 1
                self.motor_bad[name] = bad
                logging.info("hark_safety: %s motor status %s (%d)",
                             name, detail['raw'], bad)
                if bad >= self.motor_fail_count:
                    what = ", ".join(detail['errors'])
                    if name == 'e':
                        self._trip(self.e_action,
                                   "the extruder motor reports %s (%s): "
                                   "likely a clog or jam"
                                   % (what, detail['raw']))
                    else:
                        self._trip(self.xy_action,
                                   "the %s motor reports %s (%s): stall, "
                                   "collision or driver error"
                                   % (name.upper(), what, detail['raw']))
                    return
                continue
            self.motor_bad[name] = 0
            self._warn_motor(name, detail)

    def _warn_motor(self, name, detail):
        # Warnings (low supply voltage, controller MCU hot, comms) do not
        # stop the print: say so once when they appear or change. A status
        # latch with no error or warning code (a stall flag left by stall
        # homing, say) only goes to the log.
        flags = tuple(detail['warnings'])
        if not flags and detail['status']:
            flags = (proto.describe_protection(detail),)
        if flags == self.motor_warned.get(name, ()):
            return
        self.motor_warned[name] = flags
        if not flags:
            return
        label = 'extruder' if name == 'e' else name.upper()
        if not detail['warnings']:
            logging.info("hark_safety: %s motor %s (%s)", label, flags[0],
                         detail['raw'])
            return
        msg = ("Safety watch warning: %s motor reports %s (%s); the print "
               "carries on"
               % (label, ", ".join(flags), detail['raw']))
        logging.warning("hark_safety: %s", msg)
        self.gcode.respond_info(msg)

    def _clear_motor_errors(self):
        # Read, clear, read again (as Jacob10383's firmware does before
        # homing) instead of a blind clear. Every motor is still cleared, as
        # before. An error that survives the clear is reported here; the
        # watch trips on it once start_grace has passed.
        if self.bus is None:
            return
        names = ['x', 'y']
        if self.bus.e_send is not None:
            names.append('e')
        addrs = dict(self.xy_addrs, e=self.e_addr)
        try:
            before, after, persistent = self.bus.recover_faults(
                names, always_clear=True, addrs=addrs)
        except Exception:
            logging.exception("hark_safety: motor fault clear failed")
            return
        logging.info("hark_safety: print start motor faults before %s, "
                     "after %s", self.bus.describe_faults(before),
                     self.bus.describe_faults(after))
        if persistent:
            msg = ("motor error still latched after a clear at print "
                   "start: %s" % (self.bus.describe_faults(persistent),))
            self.last['event'] = msg
            logging.warning("hark_safety: %s", msg)
            self.gcode.respond_raw("!! Safety watch: %s" % (msg,))

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
                self._note_status(name, self._status(name))
        motors = ", ".join(
            "%s %s" % (name.upper(), "no reply" if m is None
                       else "%s [%s]" % (m['summary'], m['raw']))
            for name, m in sorted(self.last['motors'].items())) or "not read"
        gcmd.respond_info(
            "Safety watch: fans %s, motors %s, actions fan=%s xy=%s e=%s, "
            "last event %s" % (self.last['fans'], motors,
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
