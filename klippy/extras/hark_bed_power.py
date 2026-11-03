# Bed heater power for 110 V and 220 V mains (K2)
#
# The K2's bed is a mains heater. Creality's config carries two PID sets
# and two power limits for it (pid_220_* / pid_110_*, power 0.5 / 1.0) and
# picks between them with BED_HEADER_POWER_CALIBRATION: heat the bed 30 C
# at half power and fit the slope; at or under 0.35 C/s the printer is on
# 110 V and gets full power with the 110 V PID. Our config ships the 220 V
# half: on 110 V that is a quarter of the power the bed should have.
#
# [hark_bed_power] voltage: auto (default), 110 or 220. On auto the result
# of the last calibration (hark_bed_power.json next to the config) is
# applied at start; with none, START_PRINT runs the calibration once
# before the first print's bed heat-up (_BED_POWER_AUTO), about two
# minutes from a cold bed.
#
#   BED_POWER_CALIBRATE [SAVE=1]   heat 30 C at test power, decide, apply
#   BED_POWER_STATUS               what is applied and why
#   BED_POWER_SET VOLTAGE=110|220  apply a set by hand (and save it)
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import os

PID_PARAM_BASE = 255.


def slope_of(samples):
    """Least-squares slope (deg/s) of [(time, temp)], or None under 3."""
    n = len(samples)
    if n < 3:
        return None
    mt = sum(t for t, v in samples) / n
    mv = sum(v for t, v in samples) / n
    den = sum((t - mt) ** 2 for t, v in samples)
    if den <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in samples) / den


def decide(slope, divide):
    """'110' when the half-power slope is at or under divide (stock's
    0.35 C/s), else '220'."""
    return '110' if slope <= divide else '220'


class HarkBedPower:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.voltage = config.getchoice('voltage', {'auto': 'auto',
                                                    '110': '110',
                                                    '220': '220'}, 'auto')
        self.power = {'220': config.getfloat('power_220', 0.5, above=0.,
                                             maxval=1.),
                      '110': config.getfloat('power_110', 1.0, above=0.,
                                             maxval=1.)}
        self.pid = {'220': config.getfloatlist(
                        'pid_220', (18.578, 0.152, 298.489), count=3),
                    '110': config.getfloatlist(
                        'pid_110', (52.98, 0.849, 826.491), count=3)}
        self.divide = config.getfloat('slope_divide', 0.35, above=0.)
        self.test_power = config.getfloat('test_power', 0.5, above=0.,
                                          maxval=1.)
        self.rise = config.getfloat('rise', 30., above=10.)
        self.start_offset = config.getfloat('sample_start_offset', 10.,
                                            minval=0.)
        self.end_offset = config.getfloat('sample_end_offset', 15.,
                                          minval=0.)
        self.max_start = config.getfloat('max_start_temp', 60., above=0.)
        self.max_time = config.getfloat('max_time', 180., above=30.)
        config_file = self.printer.get_start_args().get('config_file', '')
        self.path = config.get(
            'state_path',
            os.path.join(os.path.dirname(config_file)
                         or '/mnt/UDISK/printer_data_mainline/config',
                         'hark_bed_power.json'))
        self.state = self._load()
        self.applied = None
        self.heater = None
        self.printer.register_event_handler('klippy:ready', self._ready)
        for name, func, desc in (
                ('BED_POWER_CALIBRATE', self.cmd_BED_POWER_CALIBRATE,
                 "Heat the bed 30 C at half power and pick 110 V or 220 V"),
                ('BED_POWER_STATUS', self.cmd_BED_POWER_STATUS,
                 "Show the bed power and PID set in use"),
                ('BED_POWER_SET', self.cmd_BED_POWER_SET,
                 "Apply the 110 V or 220 V set by hand")):
            self.gcode.register_command(name, func, desc=desc)

    # --- state ---
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get('voltage') in ('110',
                                                                  '220'):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def _save(self):
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            logging.exception("hark_bed_power: could not write %s", self.path)

    def get_status(self, eventtime):
        return {'voltage': self.applied, 'setting': self.voltage,
                'calibrated': bool(self.state),
                'slope': self.state.get('slope'),
                'power': self.power.get(self.applied) if self.applied
                else None}

    # --- apply ---
    def _ready(self):
        pheaters = self.printer.lookup_object('heaters')
        try:
            self.heater = pheaters.lookup_heater('heater_bed')
        except Exception:
            logging.info("hark_bed_power: no heater_bed")
            return
        choice = None
        if self.voltage in ('110', '220'):
            choice = self.voltage
        elif self.state:
            choice = self.state['voltage']
        if choice is not None:
            self.apply(choice)
            logging.info("hark_bed_power: %s V set applied (%s)", choice,
                         "config" if self.voltage != 'auto' else "saved")

    def apply(self, voltage):
        """Max power and PID gains for the set, on the live heater."""
        heater = self.heater
        power = self.power[voltage]
        kp, ki, kd = self.pid[voltage]
        heater.max_power = power
        control = heater.control
        control.heater_max_power = power
        if hasattr(control, 'Kp'):
            control.Kp = kp / PID_PARAM_BASE
            control.Ki = ki / PID_PARAM_BASE
            control.Kd = kd / PID_PARAM_BASE
            if control.Ki:
                control.temp_integ_max = power / control.Ki
        self.applied = voltage

    # --- the test ---
    def _set(self, target, power, wait):
        self.heater.max_power = power
        self.heater.control.heater_max_power = power
        self.printer.lookup_object('heaters').set_temperature(
            self.heater, target, wait)

    def measure(self, gcmd):
        """Stock's if_power_is_220v: 30 C rise at test power from the
        current temperature (preheat to max_start first when hotter),
        slope over the middle of the rise. Returns the slope."""
        temp = self.heater.last_temp
        if temp <= self.max_start:
            start = temp
        else:
            start = self.max_start
            gcmd.respond_info("Bed at %.0f C: waiting for %.0f C first"
                              % (temp, start))
            self._set(start, 1., True)
        target = start + self.rise
        gcmd.respond_info("Bed power test: heating %.0f to %.0f C at %.0f%%"
                          " power, sampling %.0f to %.0f C" % (
                              start, target, self.test_power * 100.,
                              start + self.start_offset,
                              target - self.end_offset))
        self._set(target, self.test_power, False)
        samples = []
        t0 = self.reactor.monotonic()
        try:
            while True:
                now = self.reactor.monotonic()
                temp = self.heater.last_temp
                if temp >= target - self.end_offset:
                    break
                if temp > start + self.start_offset:
                    samples.append((now, temp))
                if now - t0 > self.max_time:
                    gcmd.respond_info("Bed power test: %.0f s without "
                                      "reaching %.0f C" % (
                                          self.max_time,
                                          target - self.end_offset))
                    break
                self.reactor.pause(now + 0.1)
        finally:
            self._set(0., self.test_power, False)
        slope = slope_of(samples[1:])
        if slope is None:
            raise gcmd.error("Bed power test: too few samples (%d); is the "
                             "bed heating?" % (len(samples),))
        return slope

    def cmd_BED_POWER_CALIBRATE(self, gcmd):
        if self.heater is None:
            raise gcmd.error("No heater_bed")
        ps = self.printer.lookup_object('print_stats').get_status(
            self.reactor.monotonic())
        if ps.get('state') in ('printing', 'paused'):
            raise gcmd.error("Not during a print")
        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        slope = self.measure(gcmd)
        voltage = decide(slope, self.divide)
        self.apply(voltage)
        gcmd.respond_info(
            "Bed heated at %.2f C/s at %.0f%% power: %s V mains "
            "(%s %.2f C/s); bed max power %.0f%%, PID %s" % (
                slope, self.test_power * 100., voltage,
                "at or under" if voltage == '110' else "over", self.divide,
                self.power[voltage] * 100.,
                "/".join("%g" % v for v in self.pid[voltage])))
        if save:
            self.state = {'voltage': voltage, 'slope': round(slope, 3),
                          'test_power': self.test_power,
                          'stamp': round(self.reactor.monotonic(), 1)}
            self._save()
        logging.info("hark_bed_power: slope %.3f -> %s V", slope, voltage)

    def cmd_BED_POWER_STATUS(self, gcmd):
        if self.applied is None:
            gcmd.respond_info(
                "Bed power: config values (voltage %s, not calibrated: "
                "BED_POWER_CALIBRATE, or it runs at the first print)"
                % (self.voltage,))
            return
        if self.voltage != 'auto':
            why = "set in the config"
        elif self.state.get('set_by_hand'):
            why = "set by hand (BED_POWER_SET)"
        elif self.state.get('slope') is not None:
            why = "from the calibration (%.2f C/s at %.0f%% power)" % (
                self.state['slope'],
                self.state.get('test_power', self.test_power) * 100.)
        else:
            why = "from the saved result"
        gcmd.respond_info("Bed power: %s V set, %s; max power %.0f%%, PID %s"
                          % (self.applied, why,
                             self.power[self.applied] * 100.,
                             "/".join("%g" % v
                                      for v in self.pid[self.applied])))

    def cmd_BED_POWER_SET(self, gcmd):
        voltage = gcmd.get('VOLTAGE')
        if voltage not in ('110', '220'):
            raise gcmd.error("VOLTAGE must be 110 or 220")
        if self.heater is None:
            raise gcmd.error("No heater_bed")
        self.apply(voltage)
        self.state = {'voltage': voltage, 'slope': None, 'set_by_hand': 1,
                      'stamp': round(self.reactor.monotonic(), 1)}
        self._save()
        gcmd.respond_info("Bed power: %s V set applied and saved" % voltage)


def load_config(config):
    return HarkBedPower(config)
