# Print-start calibration switches that survive a restart: automatic
# pressure advance (when a slot loads, at print start) and the bed mesh at
# print start. The touchscreen's Print calibration page and Mainsail set
# them with
#
#   PRINT_CAL [PA_LOAD=0|1] [PA_START=0|1] [MESH=0|1]
#             [PA_METHOD=step|sweep|blend] [PA_NEXT=0|1]
#
# (no parameters: report). The choice is saved in hark_print_cal.json beside
# the config and applied again at every start: the PA switches are the
# _PA_AUTO macro's on_load / on_print_start variables (until now they went
# back to the config's values at each restart), MESH is read by START_PRINT
# (0 = no bed mesh, the Z tap at the bed centre only). PA_METHOD is the
# _PA_AUTO macro's method: step (the stationary step test), sweep (the
# Creality Hi's moving sweep, PA_SWEEP_CAL) or blend (both). PA_NEXT=1 asks
# for a PA check before the next print only: START_PRINT runs it and sets it
# back to 0 (the touchscreen's "Next print" and "Measure PA first"). A
# switch never set here keeps the config's value. Flow calibration at print start has its
# own saved switch, FLOW_CAL_AUTO ENABLE= (hark_flow_cal).
#
# Copyright (C) 2026 Grant Harkness (Hark Tech)
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os

PA_VARS = (('PA_LOAD', 'pa_on_load', 'on_load'),
           ('PA_START', 'pa_on_print_start', 'on_print_start'))
PA_METHODS = ('step', 'sweep', 'blend')
SWITCHES = ('pa_on_load', 'pa_on_print_start', 'mesh', 'pa_next')


class HarkPrintCal:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        cfg_dir = os.path.dirname(
            self.printer.get_start_args()['config_file'])
        self.path = os.path.join(cfg_dir, 'hark_print_cal.json')
        self.saved = self._load()
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.gcode.register_command(
            'PRINT_CAL', self.cmd_PRINT_CAL,
            desc="Print-start calibration switches: PA_LOAD=, PA_START= "
                 "(automatic pressure advance), MESH= (bed mesh); saved")

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            out = {k: int(bool(v)) for k, v in data.items()
                   if k in SWITCHES}
            if data.get('pa_method') in PA_METHODS:
                out['pa_method'] = data['pa_method']
            return out
        except (IOError, OSError, ValueError):
            return {}

    def _save(self):
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(self.saved, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except (IOError, OSError):
            logging.exception("hark_print_cal: could not save %s", self.path)

    def _macro(self):
        return self.printer.lookup_object('gcode_macro _PA_AUTO', None)

    def _apply(self):
        macro = self._macro()
        if macro is None:
            return
        v = dict(macro.variables)
        for _, key, var in PA_VARS:
            if key in self.saved:
                v[var] = self.saved[key]
        if 'pa_method' in self.saved and 'method' in v:
            v['method'] = self.saved['pa_method']
        macro.variables = v

    def _ready(self):
        self._apply()

    def _current(self):
        macro = self._macro()
        mv = macro.variables if macro is not None else {}
        return {'pa_on_load': int(bool(mv.get('on_load', 0))),
                'pa_on_print_start': int(bool(mv.get('on_print_start', 0))),
                'mesh': self.saved.get('mesh', 1),
                # None: firmware whose _PA_AUTO has no method (step only)
                'pa_method': mv.get('method'),
                'pa_next': self.saved.get('pa_next', 0)}

    def get_status(self, eventtime):
        return self._current()

    def cmd_PRINT_CAL(self, gcmd):
        changed = False
        for param, key, _ in PA_VARS:
            val = gcmd.get_int(param, None, minval=0, maxval=1)
            if val is not None:
                self.saved[key] = val
                changed = True
        mesh = gcmd.get_int('MESH', None, minval=0, maxval=1)
        if mesh is not None:
            self.saved['mesh'] = mesh
            changed = True
        nxt = gcmd.get_int('PA_NEXT', None, minval=0, maxval=1)
        if nxt is not None:
            self.saved['pa_next'] = nxt
            changed = True
        method = gcmd.get('PA_METHOD', None)
        if method is not None:
            method = method.lower()
            if method not in PA_METHODS:
                raise gcmd.error("PA_METHOD must be one of %s"
                                 % (", ".join(PA_METHODS),))
            macro = self._macro()
            if macro is None or 'method' not in macro.variables:
                raise gcmd.error("PA_METHOD needs a _PA_AUTO with a method "
                                 "(the PA sweep firmware)")
            self.saved['pa_method'] = method
            changed = True
        if changed:
            self._apply()
            self._save()
        c = self._current()
        onoff = lambda v: "on" if v else "off"
        gcmd.respond_info(
            "Print calibration: automatic PA when a slot loads %s, at print "
            "start %s%s%s; bed mesh at print start %s%s"
            % (onoff(c['pa_on_load']), onoff(c['pa_on_print_start']),
               ", next print only" if c['pa_next'] else "",
               " (method %s)" % c['pa_method'] if c['pa_method'] else "",
               onoff(c['mesh']), " (saved)" if changed else ""))


def load_config(config):
    return HarkPrintCal(config)
