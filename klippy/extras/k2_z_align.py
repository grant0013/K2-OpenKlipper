"""Open host support for the K2 main-MCU bottom Z reference.

The Creality main MCU firmware exposes a small ``z_align`` command family that
directly drives the configured Z step/dir pins until the PA15 photoelectric
switch is reached. This module uses that existing MCU dictionary surface
without loading any Creality host module.

Motion is deliberately kept behind both a config flag and CONFIRM=1. It is not
itself wired into G28; the supervised bring-up command must be proven first.

The K2 Plus has two Z motors, each with its own switch (stock z_align:
endstop_pin_z: PA15, PA8). endstop_pin takes a list: switch N belongs to
stepper_z (N=0) or stepper_zN, and the MCU drives each motor to its own
switch, so the bed also comes back level. KZ_ALIGN_MEASURE finds the Z of
the switches on a printer whose position_endstop is not known yet.

The Creality Hi (F018) uses the same MCU routine to drive its gantry UP to
two optical switches at the top of Z (stock z_align: endstop_pin_z PB15,
endstop_pin_z1 PB14, zd_up 0, zes_untrig 0). Its main-MCU firmware differs
in three ways, all handled here: query_z_align has no safeDist field, there
is no z_align_force_stop, and the speeds and distances count whole steps
(step_edges: 1). It also has a second routine, z_align_up, which stock uses
to lift the gantry a safe distance before X/Y homing (KZ_ALIGN_UP_SAFE,
enabled by up_safe_dist). Stock repeats the switch move until the two motors
stop within retry_tolerance steps of each other (retries / retry_tolerance).
"""

import logging

import mcu


_RESP_Z_ALIGN = "z_align_status oid=%c flag=%i deltaError1=%i"


class K2ZAlign:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.mcu = mcu.get_printer_mcu(self.printer, "mcu")

        self.allow_motion = config.getboolean("allow_motion", False)
        self.quick_speed = config.getfloat("quick_speed", 10.0, above=0.0)
        self.slow_speed = config.getfloat("slow_speed", 10.0, above=0.0)
        self.rising_dist = config.getfloat("rising_dist", 10.0, above=0.0)
        self.safe_dist = config.getfloat("safe_dist", 40.0, above=0.0)
        self.filter_count = config.getint(
            "filter_count", 10, minval=1, maxval=255)
        self.timeout = config.getfloat("timeout", 60.0, above=0.0)
        self.position_endstop = config.getfloat(
            "position_endstop", 263.12734375)
        self.bottom_mcu = None
        self.retract_dist = config.getfloat(
            "retract_dist", 10.0, minval=0.0)
        self.retract_speed = config.getfloat(
            "retract_speed", 10.0, above=0.0)
        self.endstop_pins = config.getlist("endstop_pin", ("PA15",))
        self.zd_up = config.getint("zd_up", 1, minval=0, maxval=1)
        self.zes_untrig = config.getint(
            "zes_untrig", 1, minval=0, maxval=1)
        # The K2 MCU routine counts both step edges (2); the Hi's counts
        # whole steps (1), as Creality's Hi z_align.py computes them.
        self.step_edges = config.getint("step_edges", 2, minval=1, maxval=2)
        # Repeat the switch move until the motors' reported difference
        # (deltaError1, in steps) is under retry_tolerance. 0 = accept the
        # first result (K2). Stock Hi: 7 tries, 10 steps.
        self.retries = config.getint("retries", 0, minval=0, maxval=20)
        self.retry_tolerance = config.getint("retry_tolerance", 0, minval=0)
        # Hi: lift the gantry this far (or to the switches, if sooner) with
        # the MCU z_align_up routine before X/Y homing. 0 = not used.
        self.up_safe_dist = config.getfloat("up_safe_dist", 0., minval=0.)

        # (stepper name, step pin, dir pin, switch pin) per Z motor
        self.z_motors = []
        for index, pin in enumerate(self.endstop_pins):
            name = "stepper_z" if index == 0 else "stepper_z%d" % (index,)
            if not config.has_section(name):
                raise config.error(
                    "k2_z_align: endstop_pin %d (%s) needs a [%s] section"
                    % (index, pin, name))
            section = config.getsection(name)
            self.z_motors.append((name,
                                  self._plain_pin(section.get("step_pin")),
                                  self._plain_pin(section.get("dir_pin")),
                                  self._plain_pin(pin)))
        stepper = config.getsection("stepper_z")
        self.rotation_distance = stepper.getfloat("rotation_distance")
        self.microsteps = stepper.getint("microsteps")
        self.position_max = stepper.getfloat("position_max")

        self.oid = self.mcu.create_oid()
        self.up_oid = self.mcu.create_oid() if self.up_safe_dist else None
        self.query_cmd = None
        self.query_has_safe = True
        self.up_query_cmd = None
        self.abort_cmd = None
        self.last_flag = None
        self.last_delta = None
        self.switch_triggered = None
        self.switches = [None] * len(self.endstop_pins)
        # The buttons module assumes every pin starts low and only calls back
        # on a change, so a switch already active (low) when Klipper starts,
        # e.g. the bed parked at the bottom, never reports. Start from that
        # same all-low state.
        self._handle_switch(None, 0)
        self.active = False
        self.completion = None

        self.mcu.register_config_callback(self._build_config)
        self.mcu.register_serial_response(
            self._handle_z_align_status, _RESP_Z_ALIGN, self.oid)
        if self.up_oid is not None:
            self.mcu.register_serial_response(
                self._handle_z_align_status, _RESP_Z_ALIGN, self.up_oid)

        buttons = self.printer.load_object(config, "buttons")
        buttons.register_buttons(self.endstop_pins, self._handle_switch)

        self.gcode.register_command(
            "KZ_ALIGN_STATUS", self.cmd_KZ_ALIGN_STATUS,
            desc="Report the Z reference switch state")
        self.gcode.register_command(
            "KZ_ALIGN_BOTTOM", self.cmd_KZ_ALIGN_BOTTOM,
            desc="Supervised MCU-controlled move to the bottom Z reference")
        self.gcode.register_command(
            "KZ_ALIGN_ABORT", self.cmd_KZ_ALIGN_ABORT,
            desc="Immediately stop an active Z switch reference move")
        self.gcode.register_command(
            "KZ_ALIGN_MEASURE", self.cmd_KZ_ALIGN_MEASURE,
            desc="Find the Z where the bottom switches trigger (Z homed)")
        if self.up_oid is not None:
            self.gcode.register_command(
                "KZ_ALIGN_UP_SAFE", self.cmd_KZ_ALIGN_UP_SAFE,
                desc="Lift Z a safe distance with the MCU z_align_up routine")
        self.printer.register_event_handler(
            "klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler(
            "klippy:disconnect", self._handle_shutdown)

    @staticmethod
    def _plain_pin(pin):
        return pin.lstrip("!^~")

    def _build_config(self):
        self.mcu.add_config_cmd("config_z_align oid=%d" % (self.oid,))
        for index, (name, step_pin, dir_pin, pin) in enumerate(self.z_motors):
            self.mcu.add_config_cmd(
                "config_z_align_add oid=%d z_indx=%d zs_pin=%s zd_pin=%s "
                "zd_up=%d zes_pin=%s zes_untrig=%d"
                % (self.oid, index, step_pin, dir_pin, self.zd_up, pin,
                   self.zes_untrig))
        if self.up_oid is not None:
            self.mcu.add_config_cmd("config_z_align_up oid=%d"
                                    % (self.up_oid,))
            for index, (name, step_pin, dir_pin, pin) in enumerate(
                    self.z_motors):
                self.mcu.add_config_cmd(
                    "config_z_align_up_add oid=%d z_indx=%d zs_pin=%s "
                    "zd_pin=%s zd_up=%d zes_pin=%s zes_untrig=%d"
                    % (self.up_oid, index, step_pin, dir_pin, self.zd_up,
                       pin, self.zes_untrig))
            self.up_query_cmd = self.mcu.lookup_command(
                "query_z_align_up oid=%c enable=%c quickSpeed=%u "
                "slowSpeed=%u risingDist=%u filterCnt=%c")
        # K2 firmware takes a safeDist field; the Hi's does not
        try:
            self.query_cmd = self.mcu.lookup_command(
                "query_z_align oid=%c enable=%c quickSpeed=%u slowSpeed=%u "
                "risingDist=%u filterCnt=%c safeDist=%u")
            self.query_has_safe = True
        except Exception:
            self.query_cmd = self.mcu.lookup_command(
                "query_z_align oid=%c enable=%c quickSpeed=%u slowSpeed=%u "
                "risingDist=%u filterCnt=%c")
            self.query_has_safe = False
        try:
            self.abort_cmd = self.mcu.lookup_command(
                "z_align_force_stop oid=%c")
        except Exception:
            # Hi: no stop command. _abort() turns the motors off instead.
            self.abort_cmd = None
        logging.info("k2_z_align: query safeDist=%s force_stop=%s up=%s",
                     self.query_has_safe, self.abort_cmd is not None,
                     self.up_query_cmd is not None)

    def _handle_switch(self, eventtime, state):
        # With zes_untrig=1, a switch pin is low while the optical switch is
        # active. Bit N of state is endstop_pin N.
        self.switches = [bool(state & (1 << i)) != bool(self.zes_untrig)
                         for i in range(len(self.endstop_pins))]
        self.switch_triggered = any(self.switches)

    def _handle_z_align_status(self, params):
        self.last_flag = int(params.get("flag", 0))
        self.last_delta = int(params.get("deltaError1", 0))
        if self.active and self.last_flag in (1, 2):
            self.completion.complete((self.last_flag, self.last_delta))

    def _handle_shutdown(self):
        if self.active and self.abort_cmd is not None:
            try:
                self.abort_cmd.send([self.oid])
            except Exception:
                logging.exception("k2_z_align: shutdown abort failed")

    def _distance_steps(self, distance):
        step_distance = self.rotation_distance / (200.0 * self.microsteps)
        return int(distance / step_distance) * self.step_edges

    def _timings(self):
        # Match the shipping host calculation byte-for-byte. The custom MCU
        # routine toggles step pins directly: the K2 firmware counts both
        # edges (step_edges 2), the Hi's whole steps (step_edges 1).
        clock_freq = self.mcu.get_constant_float("CLOCK_FREQ")
        step_distance = self.rotation_distance / (200.0 * self.microsteps)

        def speed_ticks(speed):
            return int(clock_freq * step_distance / speed / self.step_edges)

        return (speed_ticks(self.quick_speed),
                speed_ticks(self.slow_speed),
                self._distance_steps(self.rising_dist),
                self._distance_steps(self.safe_dist))

    def _send_query(self, enable, quick, slow, rising, safe):
        args = [self.oid, enable, quick, slow, rising, self.filter_count]
        if self.query_has_safe:
            args.append(safe)
        self.query_cmd.send(args)

    def _abort(self):
        if self.abort_cmd is not None:
            self.abort_cmd.send([self.oid])
            if self.up_oid is not None:
                self.abort_cmd.send([self.up_oid])
        elif self.active:
            # No stop command in this MCU firmware (Hi). Stock turns the
            # motors off (M84) when its z_align times out; the Z motors
            # share their enable line with X/Y, so this stops everything.
            try:
                self.gcode.run_script_from_command("M84")
            except Exception:
                logging.exception("k2_z_align: M84 abort failed")

    def get_status(self, eventtime):
        return {
            "active": self.active,
            "endstop_triggered": self.switch_triggered,
            "switches": list(self.switches),
            "last_flag": self.last_flag,
            "last_delta": self.last_delta,
        }

    def _describe_switches(self):
        return ", ".join(
            "%s %s" % (pin, "unknown" if s is None
                       else ("TRIGGERED" if s else "open"))
            for pin, s in zip(self.endstop_pins, self.switches))

    def cmd_KZ_ALIGN_STATUS(self, gcmd):
        gcmd.respond_info(
            "Z reference switch: %s; active=%s; last_flag=%s; last_delta=%s"
            % (self._describe_switches(), self.active, self.last_flag,
               self.last_delta))

    def _step_until(self, toolhead, z, z_end, step, speed, want):
        """Move Z down (bed away from the nozzle) in steps from z to z_end
        until a switch in want (indexes) triggers. Returns the Z, or None."""
        pos = toolhead.get_position()
        while z <= z_end + 1e-9:
            pos[2] = min(z, self.position_max)
            toolhead.manual_move(pos, speed)
            toolhead.wait_moves()
            self.reactor.pause(self.reactor.monotonic() + 0.05)
            if any(self.switches[i] for i in want):
                return pos[2]
            z += step
        return None

    def cmd_KZ_ALIGN_MEASURE(self, gcmd):
        """With Z homed by the nozzle tap, lower the bed until each bottom
        switch triggers and report where. Ordinary Klipper moves, never past
        position_max. The result is what position_endstop should be."""
        if gcmd.get_int("CONFIRM", 0, minval=0, maxval=1) != 1:
            raise gcmd.error("KZ_ALIGN_MEASURE requires CONFIRM=1")
        toolhead = self.printer.lookup_object("toolhead")
        eventtime = self.reactor.monotonic()
        if "z" not in toolhead.get_status(eventtime).get("homed_axes", ""):
            raise gcmd.error("Home Z with the nozzle tap first (G28)")
        print_stats = self.printer.lookup_object("print_stats", None)
        if (print_stats is not None and print_stats.get_status(
                eventtime).get("state") in ("printing", "paused")):
            raise gcmd.error("KZ_ALIGN_MEASURE refused while printing")
        if any(s is None for s in self.switches):
            raise gcmd.error("Bottom switch state not known yet: %s"
                             % (self._describe_switches(),))
        if any(self.switches):
            raise gcmd.error("A bottom switch already reads triggered (%s);"
                             " raise the bed and try again"
                             % (self._describe_switches(),))
        start = gcmd.get_float("START", self.position_max * 0.8, above=0.,
                               maxval=self.position_max)
        speed = gcmd.get_float("SPEED", 10., above=0., maxval=20.)
        toolhead.wait_moves()
        z = max(toolhead.get_position()[2], 0.)
        if start > z:
            pos = toolhead.get_position()
            pos[2] = start
            toolhead.manual_move(pos, speed)
            toolhead.wait_moves()
            z = start
        self.reactor.pause(self.reactor.monotonic() + 0.1)
        if any(self.switches):
            raise gcmd.error("A switch triggered before Z=%.1f (%s); run "
                             "again with a lower START"
                             % (start, self._describe_switches()))
        found = {}
        remaining = list(range(len(self.endstop_pins)))
        while remaining:
            coarse = self._step_until(toolhead, z + 1., self.position_max,
                                      1., speed, remaining)
            if coarse is None:
                break
            # Back off 1.5 mm, then creep in 0.05 mm steps
            pos = toolhead.get_position()
            pos[2] = coarse - 1.5
            toolhead.manual_move(pos, speed)
            toolhead.wait_moves()
            self.reactor.pause(self.reactor.monotonic() + 0.1)
            fine = self._step_until(toolhead, coarse - 1.5,
                                    min(coarse + 0.5, self.position_max),
                                    0.05, 2., remaining)
            if fine is None:
                fine = coarse
            for i in remaining[:]:
                if self.switches[i]:
                    found[i] = fine
                    remaining.remove(i)
            z = fine
        pos = toolhead.get_position()
        pos[2] = max(z - 10., 0.)
        toolhead.manual_move(pos, speed)
        toolhead.wait_moves()
        lines = ["%s triggers at Z=%.2f" % (self.endstop_pins[i], found[i])
                 for i in sorted(found)]
        lines += ["%s did not trigger before Z=%.1f (position_max)"
                  % (self.endstop_pins[i], self.position_max)
                  for i in remaining]
        if found:
            lines.append("Suggested [k2_z_align] position_endstop: %.2f "
                         "(the lowest; too low is safe, too high is not)"
                         % (min(found.values()),))
        gcmd.respond_info("KZ_ALIGN_MEASURE:\n" + "\n".join(lines))

    def cmd_KZ_ALIGN_ABORT(self, gcmd):
        self._abort()
        gcmd.respond_info("Z switch reference: abort sent")

    def _check_motion_allowed(self, gcmd, name):
        if not self.allow_motion:
            raise gcmd.error(
                "Z switch reference motion is disabled in the config")
        if gcmd.get_int("CONFIRM", 0, minval=0, maxval=1) != 1:
            raise gcmd.error("%s requires CONFIRM=1" % (name,))
        if self.active:
            raise gcmd.error("Z switch reference motion is already active")

        toolhead = self.printer.lookup_object("toolhead")
        print_stats = self.printer.lookup_object("print_stats", None)
        if print_stats is not None:
            eventtime = self.reactor.monotonic()
            stats = print_stats.get_status(eventtime)
            state = stats.get("state")
            if state in ("printing", "paused"):
                homed = toolhead.get_status(
                    eventtime).get("homed_axes", "")
                total_duration = stats.get("total_duration", 1.0e9)
                print_duration = stats.get("print_duration", 1.0e9)
                # virtual_sdcard changes print_stats to "printing" before it
                # invokes START_PRINT. START_PRINT references Z here on every
                # print, so permit it until the first extrusion (print_duration
                # only starts counting then); never once printing has begun.
                # This is deliberately independent of G28 parameters:
                # homing_override does not reliably receive non-axis
                # parameters on all Klipper builds.
                startup_recovery = (
                    state == "printing"
                    and print_duration == 0.
                )
                if not startup_recovery:
                    raise gcmd.error(
                        "Z switch reference refused while print state "
                        "is %s (homed=%s total=%.3fs print=%.3fs)"
                        % (state, homed or "none", total_duration,
                           print_duration))
        return toolhead

    def _start_motion(self, toolhead):
        toolhead.wait_moves()
        # The MCU routine drives the Z step pin directly, outside Klipper's
        # trap queue. From this point until a successful optical reference,
        # the previous logical Z coordinate must not be trusted.
        toolhead.get_kinematics().clear_homing_state("z")
        for name, step_pin, dir_pin, pin in self.z_motors:
            self.gcode.run_script_from_command(
                "SET_STEPPER_ENABLE STEPPER=%s ENABLE=1" % (name,))
        self.last_flag = 0
        self.last_delta = 0
        self.active = True

    def _wait_result(self, gcmd, send, what):
        """Send one MCU move and wait for its z_align_status. Returns the
        reported delta; raises on a timeout or a failure flag."""
        self.completion = self.reactor.completion()
        started = self.reactor.monotonic()
        send()
        result = self.completion.wait(started + self.timeout)
        self.completion = None
        if result is None:
            self._abort()
            raise gcmd.error("%s timed out after %.1fs; %s"
                             % (what, self.timeout,
                                "abort sent" if self.abort_cmd is not None
                                else "motors turned off"))
        flag, delta = result
        if flag != 1:
            self._abort()
            raise gcmd.error("%s failed: flag=%d delta=%d"
                             % (what, flag, delta))
        return delta

    def cmd_KZ_ALIGN_UP_SAFE(self, gcmd):
        """Hi: lift the gantry up_safe_dist, or to the switches if they come
        first, before X/Y homing (stock UP_SAFE_Z). Z stays unhomed."""
        toolhead = self._check_motion_allowed(gcmd, "KZ_ALIGN_UP_SAFE")
        if any(self.switches):
            gcmd.respond_info("KZ_ALIGN_UP_SAFE: already at the switches (%s)"
                              % (self._describe_switches(),))
            return
        self._start_motion(toolhead)
        quick, slow, _, _ = self._timings()
        rising = self._distance_steps(self.up_safe_dist)
        logging.info("k2_z_align: up oid=%d quick=%d slow=%d rising=%d "
                     "filter=%d", self.up_oid, quick, slow, rising,
                     self.filter_count)
        try:
            self._wait_result(gcmd, lambda: self.up_query_cmd.send(
                [self.up_oid, 1, quick, slow, rising, self.filter_count]),
                "Z safe lift")
            gcmd.respond_info("KZ_ALIGN_UP_SAFE: Z lifted (Z not homed)")
        finally:
            self.active = False
            self.completion = None

    def cmd_KZ_ALIGN_BOTTOM(self, gcmd):
        toolhead = self._check_motion_allowed(gcmd, "KZ_ALIGN_BOTTOM")
        self._start_motion(toolhead)
        quick, slow, rising, safe = self._timings()
        started = self.reactor.monotonic()
        logging.info(
            "k2_z_align: start oid=%d quick=%d slow=%d rising=%d "
            "filter=%d safe=%d timeout=%.1f safeDist=%s",
            self.oid, quick, slow, rising, self.filter_count, safe,
            self.timeout, self.query_has_safe)
        try:
            attempt = 0
            while True:
                delta = self._wait_result(
                    gcmd, lambda: self._send_query(1, quick, slow, rising,
                                                   safe),
                    "Z switch reference")
                if (not self.retry_tolerance
                        or abs(delta) < self.retry_tolerance):
                    break
                attempt += 1
                if attempt > self.retries:
                    raise gcmd.error(
                        "Z switch reference: the Z motors still differ by "
                        "%d steps after %d tries (limit %d)"
                        % (delta, attempt, self.retry_tolerance))
                logging.info("k2_z_align: delta %d >= %d, try %d",
                             delta, self.retry_tolerance, attempt + 1)

            now = toolhead.get_position()
            now[2] = self.position_endstop
            toolhead.set_position(now, homing_axes="z")
            # Each Z motor's step count at the switches: hark_plr maps it
            # into the nozzle-tap frame to find Z again after a power cut
            self.bottom_mcu = dict(
                (s.get_name(), s.get_mcu_position()) for s in
                toolhead.get_kinematics().rails[2].get_steppers())
            if self.retract_dist:
                target = list(now)
                target[2] -= self.retract_dist
                toolhead.manual_move(target, self.retract_speed)
                toolhead.wait_moves()
            elapsed = self.reactor.monotonic() - started
            gcmd.respond_info(
                "Z switch reference complete in %.3fs: "
                "Z=%.6f, retracted to Z=%.6f, delta=%d"
                % (elapsed, self.position_endstop,
                   self.position_endstop - self.retract_dist, delta))
        except Exception:
            try:
                self._abort()
            except Exception:
                logging.exception("k2_z_align: secondary abort failed")
            raise
        finally:
            self.active = False
            self.completion = None


def load_config(config):
    return K2ZAlign(config)
