# Max volumetric flow from the nozzle strain gauge, nozzle still over the
# chute: step the flow up, read the steady gauge force at each step and stop
# where the force leaves the straight line of the low flows. Up to that knee
# the hotend melts what the extruder pushes; past it pressure climbs faster
# than flow and inner walls come out with gaps (K2 CR-PETG 250 C: +8% at 14,
# +12% at 16 mm^3/s, 11-03). The cap is the last flow still on the line
# less a margin, and APPLY=1 sets it for the print (SET_MAX_FLOW; START_PRINT
# and END_PRINT clear it).
#
#   FLOW_CAL_RUN [FLOWS=6,8,...] [STEP_TIME=2] [KNEE=8] [MARGIN=1] [APPLY=0]
#
# Needs the feed armed and the nozzle at print temperature (the
# FLOW_CAL_STATIC / _FLOW_AUTO macros do that part).
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import math


def median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    mid = n // 2
    if n % 2:
        return values[mid]
    return 0.5 * (values[mid - 1] + values[mid])


def line_fit(points):
    """Least-squares a + b*x through (x, y) points (2 or more)."""
    n = len(points)
    mx = sum(x for x, y in points) / n
    my = sum(y for x, y in points) / n
    den = sum((x - mx) ** 2 for x, y in points)
    b = (sum((x - mx) * (y - my) for x, y in points) / den) if den else 0.
    return my - b * mx, b


def excess_percent(points, fit_max):
    """Each point's force above the line through the EARLIER points at or
    below fit_max, in percent: [(flow, force, excess%)]. The first two
    points have nothing to be judged against and read 0."""
    out = []
    for i, (f, v) in enumerate(points):
        low = [p for p in points[:i] if p[0] <= fit_max]
        if len(low) < 2:
            out.append((f, v, 0.))
            continue
        a, b = line_fit(low)
        trend = a + b * f
        out.append((f, v, (100. * (v - trend) / trend) if trend else 0.))
    return out


def pick_cap(points, knee_pct=8., margin=1., fit_max=12., min_cap=4.):
    """From [(flow, force)] in ramp order: (cap, knee_flow, rows).

    knee_flow is the first flow more than knee_pct above the low-flow line
    (None when the ramp never bent); cap is the last flow still on the line
    less margin, or None without a knee (no limit found in the ramp)."""
    rows = excess_percent(points, fit_max)
    knee = None
    last_ok = None
    for f, v, ex in rows:
        if ex > knee_pct:
            knee = f
            break
        last_ok = f
    if knee is None or last_ok is None:
        return None, knee, rows
    cap = max(min_cap, last_ok - margin)
    return round(cap * 2.) / 2., knee, rows


class HarkFlowCal:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        # From 8: the first auto run (11-03) read 6 low because the prime's
        # pressure had not relaxed, and a 2 s step is short for 6 mm^3/s
        self.flows = config.getfloatlist(
            'flows', (8., 10., 12., 14., 16., 18., 20., 22., 24.))
        self.step_time = config.getfloat('step_time', 2., minval=0.8,
                                         maxval=10.)
        # Gauge window: from settle after the step starts to just before it
        # ends
        self.settle = config.getfloat('settle', 0.9, minval=0.2)
        # Pause before the rest level is read (the prime that comes first
        # leaves pressure in the nozzle), and again after the ramp
        self.relax = config.getfloat('relax', 3., minval=0.6)
        self.knee_pct = config.getfloat('knee_percent', 7., above=0.)
        self.margin = config.getfloat('margin', 1., minval=0.)
        self.fit_max = config.getfloat('fit_max_flow', 12., above=0.)
        self.min_cap = config.getfloat('min_cap', 4., above=0.)
        # Extruder acceleration for the steps (mm/s^2 of filament)
        self.e_accel = config.getfloat('extrude_accel', 2000., above=0.)
        self.last = {}
        self.gcode.register_command(
            'FLOW_CAL_RUN', self.cmd_FLOW_CAL_RUN,
            desc="Step the flow up at the chute and find the melt limit")

    def get_status(self, eventtime):
        return {'last': dict(self.last)}

    # --- gauge samples ---
    def _collector(self):
        state = {'samples': [], 'on': True}

        def on_data(msg):
            if not state['on']:
                return False
            data = msg.get('data')
            if data:
                state['samples'].extend(data)
            return True
        return state, on_data

    def _wait_samples(self, state, until, timeout=1.5):
        deadline = self.reactor.monotonic() + timeout
        while self.reactor.monotonic() < deadline:
            if state['samples'] and state['samples'][-1][0] >= until:
                return True
            self.reactor.pause(self.reactor.monotonic() + 0.05)
        return False

    @staticmethod
    def _window(samples, start, end):
        return [v for t, v in samples if start <= t < end]

    def _rest_level(self, toolhead, state, relax):
        """Median gauge level over the last 0.6 s of a relax pause."""
        toolhead.wait_moves()
        self.reactor.pause(self.reactor.monotonic() + relax)
        end = toolhead.get_last_move_time()
        self._wait_samples(state, end - 0.1)
        return median(self._window(state['samples'], end - 0.7, end - 0.05))

    def cmd_FLOW_CAL_RUN(self, gcmd):
        flows_param = gcmd.get('FLOWS', None)
        flows = ([float(f) for f in flows_param.split(',')] if flows_param
                 else list(self.flows))
        step_time = gcmd.get_float('STEP_TIME', self.step_time, minval=0.8,
                                   maxval=10.)
        knee_pct = gcmd.get_float('KNEE', self.knee_pct, above=0.)
        margin = gcmd.get_float('MARGIN', self.margin, minval=0.)
        settle = gcmd.get_float('SETTLE', self.settle, minval=0.2)
        relax = gcmd.get_float('RELAX', self.relax, minval=0.6)
        apply = gcmd.get_int('APPLY', 0, minval=0, maxval=1)
        if settle >= step_time - 0.2:
            raise gcmd.error("SETTLE must leave at least 0.2 s of the step")
        gauge = self.printer.lookup_object('pa_pressure_log', None)
        if gauge is None:
            raise gcmd.error("FLOW_CAL_RUN needs [pa_pressure_log]")
        toolhead = self.printer.lookup_object('toolhead')
        extruder = toolhead.get_extruder()
        if not extruder.get_heater().can_extrude:
            raise gcmd.error("Nozzle too cold for FLOW_CAL_RUN")
        area = math.pi * (extruder.filament_diameter * .5) ** 2
        tpos = toolhead.get_position()
        X, Y, Z = tpos[:3]
        E = tpos[3]
        state, on_data = self._collector()
        gauge.get_load_cell().add_client(on_data)
        prev_accel = extruder.set_extrude_only_accel_limit(self.e_accel)
        points = []
        rest = rest_end = None
        stopped_early = False
        try:
            # Rest level once the prime's pressure has relaxed
            rest = self._rest_level(toolhead, state, relax)
            if rest is None:
                raise gcmd.error("No gauge samples: is the strain gauge "
                                 "streaming?")
            for flow in flows:
                velocity = flow / area
                start = toolhead.get_last_move_time()
                E += velocity * step_time
                toolhead.move([X, Y, Z, E], velocity)
                toolhead.wait_moves()
                end = toolhead.get_last_move_time()
                self._wait_samples(state, end)
                window = self._window(state['samples'], start + settle,
                                      end - 0.05)
                level = median(window)
                if level is None:
                    gcmd.respond_info("Flow %.0f: no gauge samples in the "
                                      "window, skipped" % (flow,))
                    continue
                points.append((flow, abs(level - rest)))
                cap, knee, rows = pick_cap(points, knee_pct, margin,
                                           self.fit_max, self.min_cap)
                if knee is not None:
                    stopped_early = flow != flows[-1]
                    break
            # Rest again: how far the hot gauge drifted over the ramp
            rest_end = self._rest_level(toolhead, state, relax)
        finally:
            state['on'] = False
            extruder.set_extrude_only_accel_limit(prev_accel)
            toolhead.wait_moves()
            toolhead.set_position(tpos)
        cap, knee, rows = pick_cap(points, knee_pct, margin, self.fit_max,
                                   self.min_cap)
        stamp = round(toolhead.get_last_move_time(), 1)
        lines = ["flow  force   above the %g-and-under line" % (self.fit_max,)]
        for f, v, ex in rows:
            lines.append("%4.0f  %7.0f  %+5.1f%%" % (f, v, ex))
        if knee is None:
            verdict = ("no melt limit found up to %.0f mm^3/s (the ramp "
                       "stayed within %.0f%% of a straight line)"
                       % (points[-1][0] if points else 0., knee_pct))
        else:
            verdict = ("knee at %.0f mm^3/s (%+.1f%% over the line); "
                       "max flow %.1f mm^3/s" % (
                           knee, [ex for f, v, ex in rows if f == knee][0],
                           cap))
        self.last = {'cap': cap, 'knee': knee, 'knee_percent': knee_pct,
                     'points': [(f, round(v)) for f, v in points],
                     'excess': [round(ex, 1) for f, v, ex in rows],
                     'rest': rest, 'rest_end': rest_end, 'applied': 0,
                     'stamp': stamp,
                     'temperature': round(
                         extruder.get_heater().get_status(
                             self.reactor.monotonic()).get('target', 0.))}
        drift = ""
        if rest is not None and rest_end is not None and points:
            drift = "\nrest level moved %+.0f over the ramp (%.0f%% of the" \
                    " last step's force)" % (
                        rest_end - rest,
                        100. * abs(rest_end - rest) / max(1., points[-1][1]))
        gcmd.respond_info("Flow calibration: %s\n%s%s%s" % (
            verdict, "\n".join(lines),
            "\n(stopped early at the knee)" if stopped_early else "", drift))
        if apply and cap is not None:
            self.gcode.run_script_from_command("SET_MAX_FLOW FLOW=%.1f"
                                               % (cap,))
            self.last['applied'] = 1
        logging.info("hark_flow_cal: %s points=%s", verdict, points)


def load_config(config):
    return HarkFlowCal(config)
