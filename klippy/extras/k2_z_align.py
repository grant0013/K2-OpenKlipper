"""Open host support for the K2 main-MCU bottom Z reference.

The Creality main MCU firmware exposes a small ``z_align`` command family that
directly drives the configured Z step/dir pins until the PA15 photoelectric
switch is reached. This module uses that existing MCU dictionary surface
without loading any Creality host module.

Motion is deliberately kept behind both a config flag and CONFIRM=1. It is not
itself wired into G28; the supervised bring-up command must be proven first.
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
        self.retract_dist = config.getfloat(
            "retract_dist", 10.0, minval=0.0)
        self.retract_speed = config.getfloat(
            "retract_speed", 10.0, above=0.0)
        self.endstop_pin = config.get("endstop_pin", "PA15")
        self.zd_up = config.getint("zd_up", 1, minval=0, maxval=1)
        self.zes_untrig = config.getint(
            "zes_untrig", 1, minval=0, maxval=1)

        stepper = config.getsection("stepper_z")
        self.step_pin = self._plain_pin(stepper.get("step_pin"))
        self.dir_pin = self._plain_pin(stepper.get("dir_pin"))
        self.rotation_distance = stepper.getfloat("rotation_distance")
        self.microsteps = stepper.getint("microsteps")

        self.oid = self.mcu.create_oid()
        self.query_cmd = None
        self.abort_cmd = None
        self.last_flag = None
        self.last_delta = None
        self.switch_triggered = None
        self.active = False
        self.completion = None

        self.mcu.register_config_callback(self._build_config)
        self.mcu.register_serial_response(
            self._handle_z_align_status, _RESP_Z_ALIGN, self.oid)

        buttons = self.printer.load_object(config, "buttons")
        buttons.register_buttons([self.endstop_pin], self._handle_switch)

        self.gcode.register_command(
            "KZ_ALIGN_STATUS", self.cmd_KZ_ALIGN_STATUS,
            desc="Report the K2 bottom Z optical-switch state")
        self.gcode.register_command(
            "KZ_ALIGN_BOTTOM", self.cmd_KZ_ALIGN_BOTTOM,
            desc="Supervised MCU-controlled move to the bottom Z reference")
        self.gcode.register_command(
            "KZ_ALIGN_ABORT", self.cmd_KZ_ALIGN_ABORT,
            desc="Immediately stop an active K2 bottom-reference move")
        self.printer.register_event_handler(
            "klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler(
            "klippy:disconnect", self._handle_shutdown)

    @staticmethod
    def _plain_pin(pin):
        return pin.lstrip("!^~")

    def _build_config(self):
        self.mcu.add_config_cmd("config_z_align oid=%d" % (self.oid,))
        self.mcu.add_config_cmd(
            "config_z_align_add oid=%d z_indx=0 zs_pin=%s zd_pin=%s "
            "zd_up=%d zes_pin=%s zes_untrig=%d"
            % (self.oid, self.step_pin, self.dir_pin, self.zd_up,
               self._plain_pin(self.endstop_pin), self.zes_untrig))
        self.query_cmd = self.mcu.lookup_command(
            "query_z_align oid=%c enable=%c quickSpeed=%u slowSpeed=%u "
            "risingDist=%u filterCnt=%c safeDist=%u")
        self.abort_cmd = self.mcu.lookup_command("z_align_force_stop oid=%c")

    def _handle_switch(self, eventtime, state):
        # With zes_untrig=1, PA15 is low while the optical switch is active.
        raw_high = bool(state & 0x01)
        self.switch_triggered = raw_high != bool(self.zes_untrig)

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

    def _timings(self):
        # Match the shipping K2 host calculation byte-for-byte. The custom MCU
        # routine toggles step pins directly and uses its own two-edge scale.
        clock_freq = self.mcu.get_constant_float("CLOCK_FREQ")
        step_distance = self.rotation_distance / (200.0 * self.microsteps)

        def speed_ticks(speed):
            return int(clock_freq * step_distance / speed / 2.0)

        def distance_steps(distance):
            return int(distance / step_distance) * 2

        return (speed_ticks(self.quick_speed),
                speed_ticks(self.slow_speed),
                distance_steps(self.rising_dist),
                distance_steps(self.safe_dist))

    def _abort(self):
        if self.abort_cmd is not None:
            self.abort_cmd.send([self.oid])

    def get_status(self, eventtime):
        return {
            "active": self.active,
            "endstop_triggered": self.switch_triggered,
            "last_flag": self.last_flag,
            "last_delta": self.last_delta,
        }

    def cmd_KZ_ALIGN_STATUS(self, gcmd):
        state = "unknown" if self.switch_triggered is None else (
            "TRIGGERED" if self.switch_triggered else "open")
        gcmd.respond_info(
            "K2 Z bottom switch: %s; active=%s; last_flag=%s; last_delta=%s"
            % (state, self.active, self.last_flag, self.last_delta))

    def cmd_KZ_ALIGN_ABORT(self, gcmd):
        self._abort()
        gcmd.respond_info("K2 Z bottom-reference abort sent")

    def cmd_KZ_ALIGN_BOTTOM(self, gcmd):
        if not self.allow_motion:
            raise gcmd.error(
                "K2 Z bottom-reference motion is disabled in the config")
        if gcmd.get_int("CONFIRM", 0, minval=0, maxval=1) != 1:
            raise gcmd.error("KZ_ALIGN_BOTTOM requires CONFIRM=1")
        if self.active:
            raise gcmd.error("K2 Z bottom-reference motion is already active")

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
                        "K2 Z bottom reference refused while print state "
                        "is %s (homed=%s total=%.3fs print=%.3fs)"
                        % (state, homed or "none", total_duration,
                           print_duration))

        toolhead.wait_moves()
        # The MCU routine drives the Z step pin directly, outside Klipper's
        # trap queue. From this point until a successful optical reference,
        # the previous logical Z coordinate must not be trusted.
        toolhead.get_kinematics().clear_homing_state("z")
        self.gcode.run_script_from_command(
            "SET_STEPPER_ENABLE STEPPER=stepper_z ENABLE=1")
        quick, slow, rising, safe = self._timings()
        self.last_flag = 0
        self.last_delta = 0
        self.completion = self.reactor.completion()
        self.active = True
        started = self.reactor.monotonic()
        logging.info(
            "k2_z_align: start oid=%d quick=%d slow=%d rising=%d "
            "filter=%d safe=%d timeout=%.1f",
            self.oid, quick, slow, rising, self.filter_count, safe,
            self.timeout)
        try:
            self.query_cmd.send(
                [self.oid, 1, quick, slow, rising, self.filter_count, safe])
            result = self.completion.wait(started + self.timeout)
            if result is None:
                self._abort()
                raise gcmd.error(
                    "K2 Z bottom reference timed out after %.1fs; abort sent"
                    % (self.timeout,))
            flag, delta = result
            if flag != 1:
                self._abort()
                raise gcmd.error(
                    "K2 Z bottom reference failed: flag=%d delta=%d"
                    % (flag, delta))

            now = toolhead.get_position()
            now[2] = self.position_endstop
            toolhead.set_position(now, homing_axes="z")
            if self.retract_dist:
                target = list(now)
                target[2] -= self.retract_dist
                toolhead.manual_move(target, self.retract_speed)
                toolhead.wait_moves()
            elapsed = self.reactor.monotonic() - started
            gcmd.respond_info(
                "K2 Z bottom reference complete in %.3fs: "
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
