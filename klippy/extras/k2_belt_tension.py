# K2 Plus automatic belt tensioners, over the motor bus
#
# The K2 Plus has a motorised tensioner with a strain gauge on each belt
# (stock [belt_mdl mdlx] / [belt_mdl mdly]). They answer on the same RS485
# bus as the X/Y motors, at 0x21 (X) and 0x22 (Y), with the bus's usual
# frames: f7 addr len status func data... crc. Functions (from Creality's
# belt_mdl.py, GPLv3):
#   0  version      -> hw(2) sw(2)
#   2  read flash   [3] -> [n, place(4), idle_adc(4), full_adc(4)]
#   4  write flash  [3, place(4), idle_adc(4), full_adc(4)] -> echo
#   6  read ADC     -> adc(4)
#   8  move slider  [dir, steps(4)] -> [dir?, moved(4)]; 13.2 ms a step
# The tension in newtons is a straight line through two calibration points
# stored in the module's flash: the ADC at 140 N (idle_adc) and at 160 N
# (full_adc), set at the factory with a gauge. Stock's BELT_TENSION homes,
# parks, frees the motors, tightens each belt to 140 N (it only ever
# tightens: a belt over target is left), works the axis back and forth to
# settle it, and tightens again.
#
#   BELT_TENSION_STATUS              tension, slider position, calibration
#   BELT_TENSION [AXES=XY] [TARGET=140] CONFIRM=1
#                                    stock's automatic tensioning
#   BELT_TENSION_MOVE AXIS=X STEPS=n [DIR=1] CONFIRM=1
#                                    move a slider by hand (service)
#   BELT_TENSION_CALIBRATE AXIS=X STEP=0|1|2 [TENSION=] CONFIRM=1
#                                    stock's factory calibration (needs a
#                                    belt tension gauge): 0 zero the
#                                    position, 1 record the ADC at the
#                                    gauge's reading, 2 the second point
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import struct

from . import k2_motor_proto as proto

FUNC_VERSION, FUNC_READ_FLASH, FUNC_WRITE_FLASH = 0, 2, 4
FUNC_READ_ADC, FUNC_MOVE = 6, 8
UNSET = 0xffffffff
STEP_TIME = 0.0132          # seconds the slider takes per step
TENSION_MIN, TENSION_MAX = -300., 600.   # beyond these the gauge is wrong


def be32(value):
    return list(struct.pack('>I', int(value) & UNSET))


def from_be32(data):
    return struct.unpack('>I', bytes(data[:4]))[0]


def parse_flash(data):
    """(place, idle_adc, full_adc) from a read-flash reply payload
    [n, 12 bytes], or None when it is short."""
    data = bytes(data)
    if len(data) < 13:
        return None
    return (from_be32(data[1:5]), from_be32(data[5:9]),
            from_be32(data[9:13]))


def tension_fit(idle_adc, full_adc, t1=140., t2=160.):
    """(slope, intercept): newtons = slope * adc + intercept through the
    two calibration points. None when the module was never calibrated."""
    if UNSET in (idle_adc, full_adc) or 0 in (idle_adc, full_adc) \
            or idle_adc == full_adc:
        return None
    slope = (t2 - t1) / float(full_adc - idle_adc)
    return slope, t1 - slope * idle_adc


def tension_from_adc(adc, fit):
    slope, intercept = fit
    return adc * slope + intercept


def next_move(target, current, attempt, mistake=0.02):
    """Stock's tightening schedule: steps to tighten by this attempt, 0 when
    the belt is within `mistake` of the target or over it (stock never
    loosens), None when 200 attempts have not got there."""
    if attempt > 200:
        return None
    if current >= target * (1. - mistake):
        return 0
    gap = min(10., abs(target - current))
    move = int(gap) + 2
    # The step shrinks with the attempts, against a non-linear screw
    if move > attempt:
        move -= attempt
    elif attempt > 110:
        move = 1
    elif attempt > 70:
        move = 2
    elif attempt > 50:
        move = 3
    elif attempt > 40:
        move = 4
    elif attempt > 20 and move > attempt - 20:
        move = move + 20 - attempt
    return max(1, move)


class K2BeltTension:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.addrs = {'x': config.getint('x_addr', 0x21),
                      'y': config.getint('y_addr', 0x22)}
        self.target = config.getfloat('target', 140., above=0.)
        self.tolerance = config.getfloat('tolerance', 0.15, above=0.)
        self.mistake = config.getfloat('settle', 0.02, above=0.)
        self.cal_points = (config.getfloat('cal_tension_1', 140.),
                           config.getfloat('cal_tension_2', 160.))
        self.park_y_margin = config.getfloat('park_y_margin', 20., minval=0.)
        self.bus = None
        self.last = {}
        self.printer.register_event_handler('klippy:ready', self._ready)
        for name, func, desc in (
                ('BELT_TENSION_STATUS', self.cmd_BELT_TENSION_STATUS,
                 "Read both belt tensioners"),
                ('BELT_TENSION', self.cmd_BELT_TENSION,
                 "Tighten the belts to the target (needs CONFIRM=1)"),
                ('BELT_TENSION_MOVE', self.cmd_BELT_TENSION_MOVE,
                 "Move a tensioner slider by hand (needs CONFIRM=1)"),
                ('BELT_TENSION_CALIBRATE', self.cmd_BELT_TENSION_CALIBRATE,
                 "Factory calibration with a tension gauge (CONFIRM=1)")):
            self.gcode.register_command(name, func, desc=desc)

    def _ready(self):
        self.bus = self.printer.lookup_object('k2_motor_bus', None)

    def get_status(self, eventtime):
        return dict(self.last)

    # --- bus ---
    def _request(self, axis, func, data=()):
        """Send [status 0, func, data...] to the axis's tensioner and return
        the reply's data bytes, or raise."""
        if self.bus is None:
            raise self.printer.command_error("No motor bus")
        addr = self.addrs[axis]
        try:
            frame, resp = self.bus.xfer(addr, [0x00, func] + list(data),
                                        read_len=64, timeout=0.3, check=True)
        except Exception as e:
            # A bus that cannot open (no port, in use) is a command error,
            # not a Klipper shutdown
            raise self.printer.command_error(
                "Belt tensioner %s: motor bus error: %s" % (axis.upper(), e))
        try:
            reply = proto.parse_frame(resp)
        except ValueError:
            reply = None
        if reply is None:
            raise self.printer.command_error(
                "Belt tensioner %s (0x%02x) did not answer (%s): is this a "
                "K2 Plus with automatic tensioners?" % (
                    axis.upper(), addr, resp.hex(' ') if resp else 'nothing'))
        if reply['func'] != func:
            raise self.printer.command_error(
                "Belt tensioner %s answered function %d to %d (%s)" % (
                    axis.upper(), reply['func'], func, resp.hex(' ')))
        return bytes(reply['payload'])

    def read_version(self, axis):
        data = self._request(axis, FUNC_VERSION)
        return (data[:2].hex(), data[2:4].hex()) if len(data) >= 4 \
            else ('?', '?')

    def read_flash(self, axis):
        parsed = parse_flash(self._request(axis, FUNC_READ_FLASH, [3]))
        if parsed is None:
            raise self.printer.command_error(
                "Belt tensioner %s: short flash reply" % (axis.upper(),))
        return parsed

    def write_flash(self, axis, place, idle_adc, full_adc):
        data = [3] + be32(place) + be32(idle_adc) + be32(full_adc)
        back = parse_flash(self._request(axis, FUNC_WRITE_FLASH, data))
        if back != (place & UNSET, idle_adc & UNSET, full_adc & UNSET):
            raise self.printer.command_error(
                "Belt tensioner %s: flash write read back %s" % (
                    axis.upper(), back))

    def read_adc(self, axis):
        data = self._request(axis, FUNC_READ_ADC)
        if len(data) < 4:
            raise self.printer.command_error(
                "Belt tensioner %s: short ADC reply" % (axis.upper(),))
        return from_be32(data)

    def move(self, axis, direction, steps):
        steps = int(steps)
        if not 0 <= steps < 0xffff:
            raise self.printer.command_error("steps must be 0-65534")
        data = self._request(axis, FUNC_MOVE, [1 if direction else 0]
                             + be32(steps))
        self.reactor.pause(self.reactor.monotonic() + steps * STEP_TIME)
        return from_be32(data[1:5]) if len(data) >= 5 else None

    # --- readings ---
    def read(self, axis):
        """Everything about one tensioner, or raise."""
        hw, sw = self.read_version(axis)
        place, idle_adc, full_adc = self.read_flash(axis)
        fit = tension_fit(idle_adc, full_adc, *self.cal_points)
        adc = self.read_adc(axis)
        tension = tension_from_adc(adc, fit) if fit else None
        info = {'hw': hw, 'sw': sw, 'place': place,
                'idle_adc': idle_adc, 'full_adc': full_adc, 'adc': adc,
                'tension': None if tension is None else round(tension, 1),
                'calibrated': fit is not None,
                'ok': (fit is not None
                       and abs(tension - self.target)
                       <= self.target * self.tolerance)}
        self.last[axis] = info
        if tension is not None and not (
                TENSION_MIN < tension < TENSION_MAX):
            raise self.printer.command_error(
                "Belt tensioner %s reads %.0f N: the strain gauge or its "
                "calibration is wrong (stock stops here too)" % (
                    axis.upper(), tension))
        return info

    def _describe(self, axis, info):
        if not info['calibrated']:
            return ("%s belt: tensioner not calibrated (flash place %s, "
                    "ADC %s/%s), firmware %s/%s" % (
                        axis.upper(), info['place'], info['idle_adc'],
                        info['full_adc'], info['hw'], info['sw']))
        return ("%s belt: %.0f N (%s; target %.0f +-%.0f%%), slider at %d "
                "steps, ADC %d, firmware %s/%s" % (
                    axis.upper(), info['tension'],
                    "ok" if info['ok'] else "out of range", self.target,
                    self.tolerance * 100., info['place'], info['adc'],
                    info['hw'], info['sw']))

    def cmd_BELT_TENSION_STATUS(self, gcmd):
        for axis in ('x', 'y'):
            gcmd.respond_info(self._describe(axis, self.read(axis)))

    # --- tightening ---
    def _tighten(self, axis, target, gcmd):
        """Stock's run_tension: tighten until within `settle` of target."""
        info = self.read(axis)
        if not info['calibrated']:
            raise self.printer.command_error(
                "Belt tensioner %s is not calibrated" % (axis.upper(),))
        fit = tension_fit(info['idle_adc'], info['full_adc'],
                          *self.cal_points)
        place = info['place']
        tension = info['tension']
        for attempt in range(1, 202):
            steps = next_move(target, tension, attempt, self.mistake)
            if steps is None:
                raise self.printer.command_error(
                    "Belt %s: not at %.0f N after 200 moves (%.0f N now)"
                    % (axis.upper(), target, tension))
            if steps == 0:
                break
            self.move(axis, 1, steps)
            place += steps
            tension = tension_from_adc(self.read_adc(axis), fit)
            if not (TENSION_MIN < tension < TENSION_MAX):
                raise self.printer.command_error(
                    "Belt %s reads %.0f N while tightening: stopped" % (
                        axis.upper(), tension))
            if attempt % 10 == 0:
                gcmd.respond_info("  %s belt: %.0f N after %d moves" % (
                    axis.upper(), tension, attempt))
        self.write_flash(axis, place, info['idle_adc'], info['full_adc'])
        return tension

    def _work_axes(self):
        """Stock's settle moves: one belt 200 mm each way with the stall
        protection on, then the motors off again."""
        g = self.gcode.run_script_from_command
        g("MOTOR_STALL_MODE DATA=2")
        self.reactor.pause(self.reactor.monotonic() + 0.3)
        g("FORCE_MOVE STEPPER=stepper_x DISTANCE=-200 VELOCITY=60")
        g("FORCE_MOVE STEPPER=stepper_y DISTANCE=200 VELOCITY=60")
        g("M400")
        g("FORCE_MOVE STEPPER=stepper_x DISTANCE=200 VELOCITY=60")
        g("FORCE_MOVE STEPPER=stepper_y DISTANCE=-200 VELOCITY=60")
        g("M400")
        g("M84")

    def cmd_BELT_TENSION(self, gcmd):
        if not gcmd.get_int('CONFIRM', 0):
            raise gcmd.error(
                "BELT_TENSION moves the belt tensioners and the head: "
                "BELT_TENSION CONFIRM=1 (AXES=X, Y or XY, TARGET=N)")
        axes = [a for a in gcmd.get('AXES', 'XY').lower() if a in 'xy']
        target = gcmd.get_float('TARGET', self.target, above=0.,
                                maxval=300.)
        toolhead = self.printer.lookup_object('toolhead')
        g = self.gcode.run_script_from_command
        if 'xy' not in toolhead.get_status(
                self.reactor.monotonic())['homed_axes'].replace('z', ''):
            g("G28 X Y")
        # Park at the back, centred: the settle moves run the head 200 mm
        # towards the front and back again
        limits = toolhead.get_status(self.reactor.monotonic())
        amin, amax = limits['axis_minimum'], limits['axis_maximum']
        g("G90")
        g("G1 X%.1f Y%.1f F6000" % ((amin[0] + amax[0]) / 2.,
                                     amax[1] - self.park_y_margin))
        g("M400")
        g("M84")
        for axis in axes:
            gcmd.respond_info("Tightening the %s belt to %.0f N"
                              % (axis.upper(), target))
            for pass_no in range(2):
                self._tighten(axis, target, gcmd)
                self._work_axes()
            tension = self._tighten(axis, target, gcmd)
            gcmd.respond_info("%s belt: %.0f N" % (axis.upper(), tension))
        g("M84")
        for axis in axes:
            gcmd.respond_info(self._describe(axis, self.read(axis)))
        gcmd.respond_info("Belt tension done; home before printing (G28)")

    def cmd_BELT_TENSION_MOVE(self, gcmd):
        if not gcmd.get_int('CONFIRM', 0):
            raise gcmd.error("BELT_TENSION_MOVE AXIS=X|Y STEPS=n DIR=0|1 "
                             "CONFIRM=1 (1 tightens)")
        axis = gcmd.get('AXIS').lower()
        if axis not in self.addrs:
            raise gcmd.error("AXIS must be X or Y")
        steps = gcmd.get_int('STEPS', minval=1, maxval=2000)
        direction = gcmd.get_int('DIR', 1, minval=0, maxval=1)
        info = self.read(axis)
        moved = self.move(axis, direction, steps)
        place = info['place'] + (steps if direction else -steps)
        self.write_flash(axis, max(0, place), info['idle_adc'],
                         info['full_adc'])
        gcmd.respond_info("%s tensioner moved %s steps %s (module says %s)"
                          % (axis.upper(), steps,
                             "tighter" if direction else "looser", moved))
        gcmd.respond_info(self._describe(axis, self.read(axis)))

    def cmd_BELT_TENSION_CALIBRATE(self, gcmd):
        if not gcmd.get_int('CONFIRM', 0):
            raise gcmd.error(
                "BELT_TENSION_CALIBRATE AXIS=X|Y STEP=0|1|2 [TENSION=N] "
                "CONFIRM=1: stock's factory procedure with a belt tension "
                "gauge (0 zero the slider position, 1 record the first "
                "gauge reading, 2 the second)")
        axis = gcmd.get('AXIS').lower()
        if axis not in self.addrs:
            raise gcmd.error("AXIS must be X or Y")
        step = gcmd.get_int('STEP', minval=0, maxval=2)
        place, idle_adc, full_adc = self.read_flash(axis)
        if step == 0:
            self.write_flash(axis, 0, idle_adc, full_adc)
            gcmd.respond_info("%s tensioner: slider position zeroed"
                              % (axis.upper(),))
            return
        tension = gcmd.get_float('TENSION', self.cal_points[step - 1],
                                 above=0.)
        adc = self.read_adc(axis)
        if step == 1:
            self.write_flash(axis, place, adc, full_adc)
        else:
            self.write_flash(axis, place, idle_adc, adc)
        gcmd.respond_info("%s tensioner: ADC %d recorded for %.0f N (point "
                          "%d; cal_tension_%d in the config must say %.0f)"
                          % (axis.upper(), adc, tension, step, step,
                             tension))
        logging.info("k2_belt_tension: %s cal point %d adc=%d tension=%.0f",
                     axis, step, adc, tension)


def load_config(config):
    return K2BeltTension(config)
