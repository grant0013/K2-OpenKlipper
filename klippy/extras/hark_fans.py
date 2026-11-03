# Fan health from the tachometers, and the lowest speed a fan keeps turning
# at. The K2's fans are powered through their PWM, so the tachometer only
# reads true at full speed (part fan ~7500, hotend fan ~8000 rpm, 11-03);
# at part speed the count is an artefact, but 0 still means stopped.
#
#   FAN_CHECK [FAN=part|hotend|all] [SPIN=1]
#       Read each fan's full-speed rpm and keep it in hark_fans.json. SPIN=1
#       runs the part fan at 100% for spin_time first (and puts it back);
#       the hotend fan is read only while it runs at full (nozzle hot and
#       not capped for probing). A reading more than drop_warn percent
#       under the fan's usual rpm (the median of its first readings) is
#       reported with '!!' so it shows on the touchscreen during a print.
#   FAN_HEALTH
#       Show the records.
#   FAN_MIN_CAL [FAN=part] [START=30] [STEP=2] [HOLD=2.5]
#       Step the part fan's duty down from START percent until the
#       tachometer reads 0, then save the lowest duty that kept it turning
#       plus min_margin as the fan's minimum; M106 raises any lower
#       request to it (a fan told to run must turn).
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import os

HISTORY = 20


def median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    mid = n // 2
    return values[mid] if n % 2 else 0.5 * (values[mid - 1] + values[mid])


def usual_rpm(readings, baseline_count):
    """A fan's usual full-speed rpm: the median of its first readings (the
    fan when it was new to us), or of what there is."""
    good = [r for r in readings if r > 0]
    if not good:
        return None
    return median(good[:baseline_count])


def judge(rpm, usual, drop_warn):
    """(verdict, percent under usual): 'stopped', 'slow', 'ok' or 'new'."""
    if rpm <= 0:
        return 'stopped', None
    if usual is None or usual <= 0:
        return 'new', None
    under = 100. * (usual - rpm) / usual
    return ('slow' if under > drop_warn else 'ok'), round(under, 1)


def lowest_turning(steps):
    """From [(duty, rpm)] stepped downward: the lowest duty with rpm > 0
    before the first 0, or None when it never stopped / never turned."""
    last = None
    for duty, rpm in steps:
        if rpm <= 0:
            return last
        last = duty
    return None


class HarkFans:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.names = {'part': config.get('part_fan', 'fan'),
                      'hotend': config.get('hotend_fan',
                                           'heater_fan hotend_fan')}
        self.spin_time = config.getfloat('spin_time', 3., minval=1.)
        self.drop_warn = config.getfloat('drop_warn', 15., above=0.)
        self.baseline_count = config.getint('baseline_count', 3, minval=1)
        self.min_margin = config.getfloat('min_margin', 2., minval=0.)
        config_file = self.printer.get_start_args().get('config_file', '')
        self.path = config.get(
            'stats_path',
            os.path.join(os.path.dirname(config_file)
                         or '/mnt/UDISK/printer_data_mainline/config',
                         'hark_fans.json'))
        self.records = self._load()
        self.fans = {}
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.gcode.register_command(
            'FAN_CHECK', self.cmd_FAN_CHECK,
            desc="Read the fans' full-speed rpm and compare with usual")
        self.gcode.register_command(
            'FAN_HEALTH', self.cmd_FAN_HEALTH, desc="Show the fan records")
        self.gcode.register_command(
            'FAN_MIN_CAL', self.cmd_FAN_MIN_CAL,
            desc="Find the lowest speed the part fan keeps turning at")

    def _ready(self):
        for key, name in self.names.items():
            self.fans[key] = self.printer.lookup_object(name, None)

    # --- records ---
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def _save(self):
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.records, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            logging.exception("hark_fans: could not write %s", self.path)

    def _record(self, key):
        return self.records.setdefault(key, {'rpm': [], 'min_duty': None,
                                             'last': None})

    def get_status(self, eventtime):
        out = {}
        for key, rec in self.records.items():
            out[key] = {'usual': usual_rpm(rec.get('rpm', []),
                                           self.baseline_count),
                        'last': rec.get('last'),
                        'min_duty': rec.get('min_duty'),
                        'readings': len(rec.get('rpm', []))}
        out['min_duty'] = (self.records.get('part') or {}).get('min_duty')
        return out

    # --- reading ---
    def _rpm(self, key, eventtime):
        fan = self.fans.get(key)
        if fan is None:
            return None, None
        st = fan.get_status(eventtime)
        return st.get('rpm'), st.get('speed', 0.)

    def _note(self, key, rpm, gcmd):
        rec = self._record(key)
        usual = usual_rpm(rec['rpm'], self.baseline_count)
        verdict, under = judge(rpm, usual, self.drop_warn)
        rec['rpm'] = (rec['rpm'] + [round(rpm)])[-HISTORY:]
        rec['last'] = {'rpm': round(rpm), 'verdict': verdict,
                       'under': under,
                       'stamp': round(self.reactor.monotonic(), 1)}
        self._save()
        label = "%s fan" % (key,)
        if verdict == 'stopped':
            msg = "!! %s: no tachometer pulses at full speed" % (
                label.capitalize(),)
            self.gcode.respond_raw(msg)
        elif verdict == 'slow':
            msg = "!! %s: %.0f rpm, %.0f%% under its usual %.0f" % (
                label.capitalize(), rpm, under, usual)
            self.gcode.respond_raw(msg)
        elif verdict == 'new':
            gcmd.respond_info("%s: %.0f rpm at full speed (first readings "
                              "set its usual)" % (label.capitalize(), rpm))
        else:
            gcmd.respond_info("%s: %.0f rpm at full speed (usual %.0f)"
                              % (label.capitalize(), rpm, usual))
        logging.info("hark_fans: %s %s rpm=%.0f usual=%s", key, verdict,
                     rpm, usual)

    def cmd_FAN_CHECK(self, gcmd):
        which = gcmd.get('FAN', 'all').lower()
        spin = gcmd.get_int('SPIN', 0, minval=0, maxval=1)
        keys = ['part', 'hotend'] if which == 'all' else [which]
        for key in keys:
            if key not in self.names:
                raise gcmd.error("FAN must be part, hotend or all")
        for key in keys:
            fan = self.fans.get(key)
            if fan is None:
                continue
            now = self.reactor.monotonic()
            rpm, speed = self._rpm(key, now)
            if rpm is None:
                gcmd.respond_info("%s fan has no tachometer" % (key,))
                continue
            if key == 'part' and spin:
                was = speed
                self.gcode.run_script_from_command("M106.1 S255")
                self.reactor.pause(self.reactor.monotonic() + self.spin_time)
                rpm, speed = self._rpm(key, self.reactor.monotonic())
                self.gcode.run_script_from_command(
                    "M106.1 S%d" % (round(was * 255.),))
            if speed < 0.99:
                gcmd.respond_info("%s fan is at %.0f%%: its rpm is only "
                                  "read at full speed" % (key, speed * 100.))
                continue
            self._note(key, rpm, gcmd)

    def cmd_FAN_HEALTH(self, gcmd):
        lines = []
        for key in ('part', 'hotend'):
            rec = self.records.get(key)
            if not rec or not rec.get('rpm'):
                lines.append("%s fan: no readings yet (FAN_CHECK)" % (key,))
                continue
            usual = usual_rpm(rec['rpm'], self.baseline_count)
            last = rec.get('last') or {}
            under = last.get('under')
            diff = ""
            if under:
                diff = ", %.0f%% %s usual" % (abs(under),
                                             "under" if under > 0 else "over")
            lines.append("%s fan: usual %s rpm, last %s (%s%s), %d readings%s"
                         % (key, "%.0f" % usual if usual else "?",
                            last.get('rpm'), last.get('verdict'), diff,
                            len(rec['rpm']),
                            (", minimum %.0f%%" % (rec['min_duty'] * 100.))
                            if rec.get('min_duty') else ""))
        gcmd.respond_info("Fan health:\n" + "\n".join(lines))

    # --- minimum duty ---
    def cmd_FAN_MIN_CAL(self, gcmd):
        key = gcmd.get('FAN', 'part').lower()
        if key != 'part':
            raise gcmd.error("FAN_MIN_CAL is for the part fan")
        start = gcmd.get_float('START', 30., above=0., maxval=100.)
        step = gcmd.get_float('STEP', 2., above=0., maxval=20.)
        hold = gcmd.get_float('HOLD', 2.5, minval=1., maxval=10.)
        fan = self.fans.get('part')
        if fan is None:
            raise gcmd.error("No part fan")
        now = self.reactor.monotonic()
        rpm, was = self._rpm('part', now)
        if rpm is None:
            raise gcmd.error("The part fan has no tachometer")
        steps = []
        duty = start
        try:
            # From spinning: a running fan keeps turning lower than one
            # starting from rest, which is why min_margin is added
            self.gcode.run_script_from_command("M106.1 S255")
            self.reactor.pause(self.reactor.monotonic() + 2.)
            while duty > 0.:
                self.gcode.run_script_from_command(
                    "M106.1 S%d" % (round(duty * 2.55),))
                self.reactor.pause(self.reactor.monotonic() + hold)
                rpm, speed = self._rpm('part', self.reactor.monotonic())
                steps.append((duty, rpm or 0.))
                gcmd.respond_info("  %3.0f%%: %s" % (
                    duty, "turning (%.0f)" % rpm if rpm else "stopped"))
                if not rpm:
                    break
                duty -= step
        finally:
            self.gcode.run_script_from_command(
                "M106.1 S%d" % (round(was * 255.),))
        lowest = lowest_turning(steps)
        rec = self._record('part')
        if lowest is None:
            if steps and steps[-1][1] > 0:
                msg = ("the part fan still turned at %.0f%%; its minimum "
                       "stays %s" % (steps[-1][0],
                                     "%.0f%%" % (rec['min_duty'] * 100.)
                                     if rec.get('min_duty') else "unset"))
            else:
                msg = ("the part fan did not turn at %.0f%%: start higher "
                       "(START=)" % (start,))
            gcmd.respond_info("Fan minimum: %s" % (msg,))
            return
        min_duty = min(1., (lowest + self.min_margin) / 100.)
        rec['min_duty'] = round(min_duty, 3)
        self._save()
        gcmd.respond_info("Fan minimum: the part fan stopped below %.0f%%; "
                          "M106 now raises anything under %.0f%% to it"
                          % (lowest, min_duty * 100.))


def load_config(config):
    return HarkFans(config)
