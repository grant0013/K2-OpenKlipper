# K2 Plus PTC chamber heater supervisor
#
# The K2 Plus has a PTC chamber heater (PC12) with its own fan (PB14, power
# PB2, tachometer fan0 = PC6). Stock Creality firmware drives it from patched
# heaters.py / heater_fan.py code; on the open stack the heater is a plain
# [heater_generic chamber_heater] and this module adds stock's rules around it:
#   - only targets above min_heat_target heat (stock M141/M191: S40 and below
#     only set the exhaust fan target), capped at max_target,
#   - never while the bed is heating up (the bed comes first, then the
#     chamber), so the two never draw full power together,
#   - soft_start_power for the first soft_start_time of each heat-up,
#   - the heater is switched off when its fan is meant to run but the
#     tachometer reads zero for fan_fail_time, or the chamber runs
#     overtemp_margin over the target for overtemp_time.
# A fault switches the heater off and says why; the print carries on (as
# stock). It stays off until the next CHAMBER_HEAT / M141 / M191 above
# min_heat_target. Nothing heats until [hark_chamber] enable: True.
#
#   CHAMBER_HEAT TARGET=<C> [WAIT=1] [TIMEOUT=<s>]   0 = off
#   CHAMBER_STATUS
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

TICK = 1.


class HarkChamber:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.enabled = config.getboolean('enable', False)
        self.heater_name = config.get('heater', 'chamber_heater')
        self.fan_name = config.get('fan', 'heater_fan ptc_fan')
        # The fan's tachometer is its own tachometer_pin (Klipper's rpm);
        # `tach` named a fan_feedback key before 11-03 and is ignored
        config.get('tach', None)
        self.min_heat_target = config.getfloat('min_heat_target', 40.)
        self.max_target = config.getfloat(
            'max_target', 60., above=self.min_heat_target)
        self.bed_margin = config.getfloat('bed_margin', 2., minval=0.)
        self.soft_start_time = config.getfloat(
            'soft_start_time', 60., minval=0.)
        self.soft_start_power = config.getfloat(
            'soft_start_power', .5, above=0., maxval=1.)
        self.fan_spinup_time = config.getfloat(
            'fan_spinup_time', 10., minval=0.)
        self.fan_fail_time = config.getfloat('fan_fail_time', 15., above=2.)
        self.overtemp_margin = config.getfloat(
            'overtemp_margin', 10., above=0.)
        self.overtemp_time = config.getfloat('overtemp_time', 30., minval=0.)
        self.wait_margin = config.getfloat('wait_margin', 1., minval=0.)
        self.wait_timeout = config.getfloat('wait_timeout', 1200., above=0.)
        self.heater = self.bed = self.fan = self.tach = None
        self.full_power = None
        self.requested = 0.
        self.applied = 0.
        self.fault = None
        self.held_for_bed = False
        self.heat_since = self.power_since = None
        self.zero_since = self.over_since = None
        self.gcode.register_command(
            'CHAMBER_HEAT', self.cmd_CHAMBER_HEAT,
            desc="K2 Plus chamber heater target (0 = off), WAIT=1 to wait")
        self.gcode.register_command(
            'CHAMBER_STATUS', self.cmd_CHAMBER_STATUS,
            desc="Report the K2 Plus chamber heater supervisor")
        self.printer.register_event_handler('klippy:ready', self._ready)

    def _ready(self):
        pheaters = self.printer.lookup_object('heaters')
        self.heater = pheaters.lookup_heater(self.heater_name)
        if self.printer.lookup_object('heater_bed', None) is not None:
            self.bed = pheaters.lookup_heater('heater_bed')
        self.fan = self.printer.lookup_object(self.fan_name, None)
        self.tach = self.fan
        self.full_power = self.heater.control.heater_max_power
        # See every other way the heater target is set (TURN_OFF_HEATERS
        # at the end of a print or on idle, SET_HEATER_TEMPERATURE), also
        # while the heater is held at 0 for the bed.
        self._set_temp = self.heater.set_temp
        self.heater.set_temp = self._external_set_temp
        self.reactor.register_timer(self._tick, self.reactor.NOW)

    # --- requests ------------------------------------------------------
    def _request(self, target, source):
        """Set the wanted chamber temperature. Returns a message for the
        console, or None when there is nothing to say."""
        if target <= self.min_heat_target:
            self.requested = 0.
            return None
        if not self.enabled:
            self.requested = 0.
            return ("Chamber heater not enabled (%s asked for %.0fC): set "
                    "[hark_chamber] enable: True after the supervised test"
                    % (source, target))
        msg = None
        if target > self.max_target:
            msg = ("Chamber target %.0fC capped at %.0fC (max_target)"
                   % (target, self.max_target))
            target = self.max_target
        self.fault = None
        self.requested = target
        return msg

    def _apply(self, target):
        if target != self.applied:
            self.applied = target
            self._set_temp(target)

    def _external_set_temp(self, degrees):
        # Range check first, as the heater does
        self._set_temp(degrees)
        self.applied = degrees
        if degrees <= 0.:
            self.requested = 0.
            self.held_for_bed = False
            return
        msg = self._request(degrees, "SET_HEATER_TEMPERATURE")
        if msg:
            self.gcode.respond_info(msg)
        self._update_target(self.reactor.monotonic())

    def _update_target(self, eventtime):
        self.held_for_bed = self.requested > 0. and self._bed_heating(
            eventtime)
        self._apply(0. if self.held_for_bed else self.requested)

    def _trip(self, why):
        self.fault = why
        self.requested = 0.
        self.power_since = self.zero_since = self.over_since = None
        self._apply(0.)
        logging.warning("hark_chamber: %s", why)
        self.gcode.respond_raw("!! Chamber heater off: %s" % (why,))

    # --- supervision ---------------------------------------------------
    def _bed_heating(self, eventtime):
        if self.bed is None:
            return False
        temp, target = self.bed.get_temp(eventtime)
        return target > 0. and temp < target - self.bed_margin

    def _tick(self, eventtime):
        try:
            self._supervise(eventtime)
        except Exception:
            logging.exception("hark_chamber: supervise failed")
            try:
                self.requested = 0.
                self.applied = None
                self._apply(0.)
            except Exception:
                logging.exception("hark_chamber: could not switch off")
        return eventtime + TICK

    def _supervise(self, eventtime):
        temp = self.heater.get_temp(eventtime)[0]
        self._update_target(eventtime)
        status = self.heater.get_status(eventtime)
        power = status.get('power', 0.)
        # Soft start: limited power for the first part of each heat-up
        if self.applied > 0.:
            if self.heat_since is None:
                self.heat_since = eventtime
            soft = eventtime - self.heat_since < self.soft_start_time
        else:
            self.heat_since = None
            soft = False
        self.heater.control.heater_max_power = (
            self.full_power * self.soft_start_power if soft
            else self.full_power)
        # Fan check: while the heater has power its fan must turn
        speed = self._fan_rpm(eventtime)
        if power > 0. and speed is not None:
            if self.power_since is None:
                self.power_since = eventtime
            if (eventtime - self.power_since < self.fan_spinup_time
                    or speed > 0):
                self.zero_since = None
            else:
                if self.zero_since is None:
                    self.zero_since = eventtime
                if eventtime - self.zero_since >= self.fan_fail_time:
                    self._trip("the heater fan has not turned for %.0f s"
                               % (eventtime - self.zero_since,))
                    return
        else:
            self.power_since = self.zero_since = None
        # Over-temperature: the chamber well over what was asked for
        limit = max(self.applied, self.min_heat_target) + self.overtemp_margin
        # On temperature alone: watermark control has cut the power long
        # before this, so a power test could never fire for a stuck heater
        if self.applied > 0. and temp > limit:
            if self.over_since is None:
                self.over_since = eventtime
            if eventtime - self.over_since >= self.overtemp_time:
                self._trip("chamber %.1fC, over the %.0fC limit"
                           % (temp, limit))
        else:
            self.over_since = None

    # --- commands ------------------------------------------------------
    def cmd_CHAMBER_HEAT(self, gcmd):
        target = gcmd.get_float('TARGET', minval=0.)
        msg = self._request(target, "CHAMBER_HEAT")
        if msg:
            gcmd.respond_info(msg)
        if self.heater is not None:
            self._update_target(self.reactor.monotonic())
        if self.requested > 0. and gcmd.get_int('WAIT', 0, minval=0,
                                                maxval=1):
            self._wait(gcmd, gcmd.get_float('TIMEOUT', self.wait_timeout,
                                            above=0.))

    def _wait(self, gcmd, timeout):
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.get_last_move_time()
        eventtime = self.reactor.monotonic()
        deadline = eventtime + timeout
        last_report = 0.
        while not self.printer.is_shutdown():
            if self.requested <= 0.:
                gcmd.respond_info("Chamber heating stopped: not waiting")
                return
            temp = self.heater.get_temp(eventtime)[0]
            if temp >= self.requested - self.wait_margin:
                gcmd.respond_info("Chamber at %.1fC" % (temp,))
                return
            if eventtime >= deadline:
                gcmd.respond_info(
                    "Chamber %.1fC after %.0f min, target %.0fC: carrying "
                    "on (it keeps heating)" % (temp, timeout / 60.,
                                               self.requested))
                return
            if eventtime - last_report >= 30.:
                last_report = eventtime
                gcmd.respond_info(
                    "Chamber %.1fC / %.0fC%s" % (
                        temp, self.requested,
                        " (waiting for the bed)" if self.held_for_bed
                        else ""))
            eventtime = self.reactor.pause(eventtime + TICK)

    def _fan_rpm(self, eventtime):
        """The PTC fan's rpm, or None without a fan or a tachometer (then
        the fan is not checked)."""
        if self.fan is None:
            return None
        return self.fan.get_status(eventtime).get('rpm')

    def cmd_CHAMBER_STATUS(self, gcmd):
        eventtime = self.reactor.monotonic()
        status = self.get_status(eventtime)
        tach = self._fan_rpm(eventtime)
        gcmd.respond_info(
            "Chamber heater: %s, requested %.0fC, heater target %.0fC, "
            "chamber %.1fC, power %.2f%s%s, fan tach %s, fault %s" % (
                "enabled" if self.enabled else "NOT enabled",
                status['requested'], status['applied'], status['temperature'],
                status['power'], " (held for the bed)" if self.held_for_bed
                else "", " (soft start)" if status['soft_start'] else "",
                tach, self.fault))

    def get_status(self, eventtime):
        temp = power = 0.
        soft = False
        if self.heater is not None:
            temp = self.heater.get_temp(eventtime)[0]
            power = self.heater.get_status(eventtime).get('power', 0.)
            soft = (self.full_power is not None
                    and self.heater.control.heater_max_power
                    < self.full_power)
        return {'enabled': self.enabled, 'requested': self.requested,
                'applied': self.applied or 0., 'held_for_bed':
                self.held_for_bed, 'soft_start': soft, 'fault': self.fault,
                'temperature': temp, 'power': power,
                'min_heat_target': self.min_heat_target,
                'max_target': self.max_target}


def load_config(config):
    return HarkChamber(config)
