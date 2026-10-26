# Named PID profiles for a heater, switched without a restart
#
# Idea from Kalico's PID_PROFILE (KalicoCrew/kalico, GPLv3), written for
# mainline's heater code. Profiles live in a JSON file next to the config.
# "default" is always the [extruder] pid_Kp/Ki/Kd from the config.
#
#   PID_PROFILE LIST
#   PID_PROFILE LOAD=pla [HEATER=extruder]
#   PID_PROFILE SAVE=pla KP= KI= KD= [TARGET=]
#   PID_PROFILE CALIBRATE=pla TARGET=220 [FAN=1.0]
#   PID_PROFILE REMOVE=pla
#
# CFS_SLOT_APPLY loads the profile named after the slot's material (PLA ->
# "pla"), falling back to "default".
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os, time
from . import heaters, pid_calibrate


class _PIDConfig:
    # The three getfloat() calls mainline ControlPID makes on its config
    def __init__(self, kp, ki, kd):
        self.values = {'pid_Kp': kp, 'pid_Ki': ki, 'pid_Kd': kd}

    def getfloat(self, option, *args, **kwargs):
        return self.values[option]


class HarkPIDProfiles:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        config_dir = os.path.dirname(
            self.printer.get_start_args()['config_file'])
        self.path = config.get(
            'path', os.path.join(config_dir, 'hark_pid_profiles.json'))
        self.defaults = {}
        for name in config.getlist('heaters', ('extruder',)):
            section = config.getsection(name)
            self.defaults[name] = {
                'kp': section.getfloat('pid_Kp', note_valid=False),
                'ki': section.getfloat('pid_Ki', note_valid=False),
                'kd': section.getfloat('pid_Kd', note_valid=False),
                'note': 'config',
            }
        self.profiles = self._load()
        self.active = dict((name, 'default') for name in self.defaults)
        self.gcode.register_command('PID_PROFILE', self.cmd_PID_PROFILE,
                                    desc=self.cmd_PID_PROFILE_help)

    # --- storage -------------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except IOError:
            return dict((name, {}) for name in self.defaults)
        except ValueError:
            logging.exception("hark_pid_profiles: bad %s", self.path)
            return dict((name, {}) for name in self.defaults)
        return dict((name, dict(data.get(name, {})))
                    for name in self.defaults)

    def _save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.profiles, f, indent=2, sort_keys=True)
        os.rename(tmp, self.path)

    def _get(self, heater_name, profile):
        if profile == 'default':
            return self.defaults[heater_name]
        return self.profiles[heater_name].get(profile)

    # --- switching -----------------------------------------------------
    def _heater(self, gcmd):
        name = gcmd.get('HEATER', 'extruder')
        if name not in self.defaults:
            raise gcmd.error("PID_PROFILE: heater '%s' is not managed "
                             "(heaters: %s)"
                             % (name, ", ".join(sorted(self.defaults))))
        return name, self.printer.lookup_object('heaters').lookup_heater(
            name)

    def load(self, heater_name, profile):
        values = self._get(heater_name, profile)
        if values is None:
            raise self.printer.command_error(
                "PID_PROFILE: no profile '%s' for %s"
                % (profile, heater_name))
        heater = self.printer.lookup_object('heaters').lookup_heater(
            heater_name)
        new = heaters.ControlPID(
            heater, _PIDConfig(values['kp'], values['ki'], values['kd']))
        # Swap under the heater lock without heater.set_control(), which
        # zeroes the target. Carry the loop state across so the switch is
        # bumpless: same temperature history, and an integral rescaled so
        # Ki * integral (the steady heater power) is unchanged.
        with heater.lock:
            old = heater.control
            if isinstance(old, heaters.ControlPID):
                new.prev_temp = old.prev_temp
                new.prev_temp_time = old.prev_temp_time
                new.prev_temp_deriv = old.prev_temp_deriv
                if new.Ki:
                    new.prev_temp_integ = max(0., min(
                        new.temp_integ_max,
                        old.prev_temp_integ * old.Ki / new.Ki))
            heater.control = new
        self.active[heater_name] = profile
        logging.info("PID_PROFILE: %s now '%s' Kp=%.3f Ki=%.3f Kd=%.3f",
                     heater_name, profile, values['kp'], values['ki'],
                     values['kd'])
        return values

    def apply_material(self, material, heater_name='extruder'):
        # For CFS_SLOT_APPLY: returns a message when the profile changed
        if heater_name not in self.defaults:
            return None
        # "PLA" -> pla; a variant like "PLA-CF" falls back to its base
        material = (material or '').strip().lower()
        base = material.replace('-', ' ').split(' ')[0]
        wanted = 'default'
        for name in (material, base):
            if name and name in self.profiles[heater_name]:
                wanted = name
                break
        if self.active.get(heater_name) == wanted:
            return None
        values = self.load(heater_name, wanted)
        return "hotend PID '%s' (Kp %.2f Ki %.3f Kd %.2f)" % (
            wanted, values['kp'], values['ki'], values['kd'])

    # --- commands ------------------------------------------------------
    cmd_PID_PROFILE_help = "Named heater PID profiles: LIST, LOAD=, SAVE=," \
        " CALIBRATE=, REMOVE="

    def cmd_PID_PROFILE(self, gcmd):
        for key, handler in (('LOAD', self._cmd_load),
                             ('SAVE', self._cmd_save),
                             ('CALIBRATE', self._cmd_calibrate),
                             ('REMOVE', self._cmd_remove)):
            profile = gcmd.get(key, None)
            if profile is not None:
                profile = profile.strip().lower()
                if not profile:
                    raise gcmd.error("PID_PROFILE %s needs a name" % key)
                handler(gcmd, profile)
                return
        self._cmd_list(gcmd)

    def _cmd_list(self, gcmd):
        lines = []
        for name in sorted(self.defaults):
            lines.append("%s (active: %s)" % (name, self.active[name]))
            rows = [('default', self.defaults[name])]
            rows += sorted(self.profiles[name].items())
            for profile, v in rows:
                extra = []
                if v.get('target'):
                    extra.append("tuned at %.0fC" % v['target'])
                if v.get('fan') is not None:
                    extra.append("fan %.0f%%" % (v['fan'] * 100.))
                if v.get('note'):
                    extra.append(v['note'])
                lines.append("  %s: Kp=%.3f Ki=%.3f Kd=%.3f%s" % (
                    profile, v['kp'], v['ki'], v['kd'],
                    (" (%s)" % ", ".join(extra)) if extra else ""))
        gcmd.respond_info("\n".join(lines))

    def _cmd_load(self, gcmd, profile):
        heater_name, heater = self._heater(gcmd)
        try:
            v = self.load(heater_name, profile)
        except self.printer.command_error as e:
            raise gcmd.error(str(e))
        gcmd.respond_info("%s PID profile '%s': Kp=%.3f Ki=%.3f Kd=%.3f"
                          % (heater_name, profile, v['kp'], v['ki'],
                             v['kd']))

    def _store(self, heater_name, profile, kp, ki, kd, **extra):
        if profile == 'default':
            raise self.printer.command_error(
                "PID_PROFILE: 'default' is the config's PID; pick another"
                " name")
        rec = {'kp': round(kp, 3), 'ki': round(ki, 3), 'kd': round(kd, 3),
               'date': time.strftime('%Y-%m-%d %H:%M')}
        rec.update(dict((k, v) for k, v in extra.items() if v is not None))
        profiles = dict(self.profiles[heater_name])
        profiles[profile] = rec
        self.profiles[heater_name] = profiles
        self._save()
        return rec

    def _cmd_save(self, gcmd, profile):
        heater_name, heater = self._heater(gcmd)
        current = self._get(heater_name, self.active[heater_name])
        try:
            rec = self._store(
                heater_name, profile,
                gcmd.get_float('KP', current['kp'], above=0.),
                gcmd.get_float('KI', current['ki'], minval=0.),
                gcmd.get_float('KD', current['kd'], minval=0.),
                target=gcmd.get_float('TARGET', None),
                note=gcmd.get('NOTE', None))
        except self.printer.command_error as e:
            raise gcmd.error(str(e))
        gcmd.respond_info("Saved %s PID profile '%s': Kp=%.3f Ki=%.3f "
                          "Kd=%.3f" % (heater_name, profile, rec['kp'],
                                       rec['ki'], rec['kd']))

    def _cmd_remove(self, gcmd, profile):
        heater_name, heater = self._heater(gcmd)
        if profile not in self.profiles[heater_name]:
            raise gcmd.error("No %s PID profile '%s'" % (heater_name,
                                                          profile))
        if self.active[heater_name] == profile:
            self.load(heater_name, 'default')
        profiles = dict(self.profiles[heater_name])
        del profiles[profile]
        self.profiles[heater_name] = profiles
        self._save()
        gcmd.respond_info("Removed %s PID profile '%s'"
                          % (heater_name, profile))

    def _cmd_calibrate(self, gcmd, profile):
        # Mainline PID_CALIBRATE's tuning run, without its SAVE_CONFIG
        # side effect, saved straight into the named profile.
        heater_name, heater = self._heater(gcmd)
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None and print_stats.get_status(
                0.).get('state') in ('printing', 'paused'):
            raise gcmd.error("PID_PROFILE CALIBRATE is refused during a"
                             " print")
        target = gcmd.get_float('TARGET', above=50.)
        fan = gcmd.get_float('FAN', None, minval=0., maxval=1.)
        pheaters = self.printer.lookup_object('heaters')
        self.printer.lookup_object('toolhead').get_last_move_time()
        if fan is not None:
            self.gcode.run_script_from_command("M106 S%d"
                                               % round(fan * 255.))
        calibrate = pid_calibrate.ControlAutoTune(heater, target)
        old_control = heater.set_control(calibrate)
        try:
            pheaters.set_temperature(heater, target, True)
        finally:
            heater.set_control(old_control)
            if fan is not None:
                self.gcode.run_script_from_command("M107")
        if calibrate.check_busy(0., 0., 0.):
            raise gcmd.error("PID_PROFILE calibration interrupted")
        kp, ki, kd = calibrate.calc_final_pid()
        rec = self._store(heater_name, profile, kp, ki, kd, target=target,
                          fan=fan)
        if self.active[heater_name] == profile:
            self.load(heater_name, profile)
        gcmd.respond_info(
            "Saved %s PID profile '%s' (tuned at %.0fC): Kp=%.3f Ki=%.3f "
            "Kd=%.3f. The heater is now off."
            % (heater_name, profile, target, rec['kp'], rec['ki'],
               rec['kd']))

    def get_status(self, eventtime):
        return {'active': dict(self.active),
                'profiles': dict((name, sorted(p))
                                 for name, p in self.profiles.items())}


def load_config(config):
    return HarkPIDProfiles(config)
