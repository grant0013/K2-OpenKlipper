# Maintenance counters and reminders, as Creality's screen keeps them
#
# Stock counts print hours, grams of filament through the nozzle and
# cutter uses against thresholds (gcode_macro MAINTENANCE_ITEM_PARAM) and
# reminds the owner on the screen. This keeps the same counters in
# hark_maintenance.json next to the config, with stock's thresholds:
#   hours: shaper calibration 500, belt tension 1000, motion lubrication
#          500, camera 500, fans 500, PTFE tube 1000, wipe strip 500, air
#          filter 500, CFS PTFE 1000, CFS desiccant 500
#   grams: nozzle 20000
#   cuts:  cutter 100000 (every CFS load and change cuts once)
#
#   MAINTENANCE_STATUS            every item, how far along, due or not
#   MAINTENANCE_DONE ITEM=name    reset one item (ITEM=ALL for all)
#
# Due items are listed in the console when a print finishes, and the
# touchscreen reads `due` from the status.
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import math
import os

# name: (unit, stock threshold, label)
ITEMS = {
    'shaper_calibrate': ('hours', 500., "Input shaper calibration"),
    'belt_tensioning': ('hours', 1000., "Belt tension"),
    'lubrication': ('hours', 500., "Motion lubrication"),
    'camera': ('hours', 500., "Camera lens"),
    'fans': ('hours', 500., "Fan inspection"),
    'nozzle': ('grams', 20000., "Nozzle"),
    'cutter': ('cuts', 100000., "Cutter blade"),
    'ptfe_tube': ('hours', 1000., "PTFE tube"),
    'wipe_strip': ('hours', 500., "Nozzle wipe strip"),
    'air_filter': ('hours', 500., "Air filter"),
    'cfs_ptfe': ('hours', 1000., "CFS PTFE tubes"),
    'cfs_desiccant': ('hours', 500., "CFS desiccant"),
}
UNIT_LABEL = {'hours': 'h', 'grams': 'g', 'cuts': 'cuts'}


def progress(totals, items, thresholds):
    """{name: {'used', 'threshold', 'unit', 'due', 'label'}} from the
    counters `totals` {unit: value} and the per-item marks `items`
    {name: value at last done}."""
    out = {}
    for name, (unit, default, label) in ITEMS.items():
        threshold = thresholds.get(name, default)
        used = max(0., totals.get(unit, 0.) - items.get(name, 0.))
        out[name] = {'used': round(used, 1), 'threshold': threshold,
                     'unit': unit, 'due': threshold > 0 and used >= threshold,
                     'label': label}
    return out


class HarkMaintenance:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.density = config.getfloat('filament_density', 1.24, above=0.)
        self.diameter = config.getfloat('filament_diameter', 1.75, above=0.)
        self.thresholds = {}
        for name, (unit, default, label) in ITEMS.items():
            self.thresholds[name] = config.getfloat(name, default, minval=0.)
        config_file = self.printer.get_start_args().get('config_file', '')
        self.path = config.get(
            'state_path',
            os.path.join(os.path.dirname(config_file)
                         or '/mnt/UDISK/printer_data_mainline/config',
                         'hark_maintenance.json'))
        self.state = self._load()
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.cfs = None
        self.last_duration = self.last_filament = 0.
        self.last_loads = None
        self.was_printing = False
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.gcode.register_command(
            'MAINTENANCE_STATUS', self.cmd_MAINTENANCE_STATUS,
            desc="Maintenance counters against their thresholds")
        self.gcode.register_command(
            'MAINTENANCE_DONE', self.cmd_MAINTENANCE_DONE,
            desc="Mark a maintenance item done (ITEM=name or ALL)")

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault('totals', {})
                data.setdefault('items', {})
                return data
        except (OSError, ValueError):
            pass
        return {'totals': {'hours': 0., 'grams': 0., 'cuts': 0.},
                'items': {}}

    def _save(self):
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            logging.exception("hark_maintenance: could not write %s",
                              self.path)

    def _ready(self):
        self.cfs = self.printer.lookup_object('k2_cfs', None)
        self.reactor.register_timer(self._poll,
                                    self.reactor.monotonic() + 30.)

    # --- counting ---
    def _loads(self):
        if self.cfs is None:
            return None
        stats = self.cfs.get_status(self.reactor.monotonic()).get(
            'slot_stats') or {}
        return sum(rec.get('loads', 0) for rec in stats.values())

    def _poll(self, eventtime):
        try:
            self._count(eventtime)
        except Exception:
            logging.exception("hark_maintenance: count failed")
        return eventtime + 30.

    def _count(self, eventtime):
        ps = self.print_stats.get_status(eventtime)
        printing = ps.get('state') == 'printing'
        duration = ps.get('print_duration', 0.) or 0.
        filament = ps.get('filament_used', 0.) or 0.
        totals = self.state['totals']
        changed = False
        if printing:
            if duration >= self.last_duration:
                totals['hours'] = totals.get('hours', 0.) + (
                    duration - self.last_duration) / 3600.
            if filament >= self.last_filament:
                area = math.pi * (self.diameter / 2.) ** 2
                totals['grams'] = totals.get('grams', 0.) + (
                    filament - self.last_filament) * area * self.density / 1000.
            changed = True
        self.last_duration, self.last_filament = duration, filament
        loads = self._loads()
        if loads is not None:
            if self.last_loads is not None and loads > self.last_loads:
                totals['cuts'] = totals.get('cuts', 0.) + (
                    loads - self.last_loads)
                changed = True
            self.last_loads = loads
        if changed:
            self._save()
        if self.was_printing and not printing and ps.get('state') in (
                'complete', 'cancelled', 'error'):
            due = [v['label'] for v in self.progress().values() if v['due']]
            if due:
                self.gcode.respond_info(
                    "Maintenance due: %s (MAINTENANCE_STATUS)"
                    % (", ".join(due),))
        self.was_printing = printing

    def progress(self):
        return progress(self.state['totals'], self.state['items'],
                        self.thresholds)

    def get_status(self, eventtime):
        p = self.progress()
        return {'totals': dict(self.state['totals']), 'items': p,
                'due': [name for name, v in p.items() if v['due']]}

    def cmd_MAINTENANCE_STATUS(self, gcmd):
        t = self.state['totals']
        lines = ["%.1f print hours, %.0f g through the nozzle, %.0f cuts"
                 % (t.get('hours', 0.), t.get('grams', 0.),
                    t.get('cuts', 0.))]
        for name, v in self.progress().items():
            lines.append("%s%s: %.0f of %.0f %s (%s)" % (
                "!! " if v['due'] else "   ", v['label'], v['used'],
                v['threshold'], UNIT_LABEL[v['unit']],
                "DUE, MAINTENANCE_DONE ITEM=%s" % name if v['due']
                else name))
        gcmd.respond_info("Maintenance:\n" + "\n".join(lines))

    def cmd_MAINTENANCE_DONE(self, gcmd):
        item = gcmd.get('ITEM').lower()
        names = list(ITEMS) if item == 'all' else [item]
        for name in names:
            if name not in ITEMS:
                raise gcmd.error("ITEM must be one of %s or ALL"
                                 % (", ".join(ITEMS),))
        for name in names:
            unit = ITEMS[name][0]
            self.state['items'][name] = self.state['totals'].get(unit, 0.)
        self._save()
        gcmd.respond_info("Maintenance done: %s" % (
            ", ".join(ITEMS[n][2] for n in names),))


def load_config(config):
    return HarkMaintenance(config)
