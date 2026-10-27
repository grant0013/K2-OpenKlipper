"""Upstream-Klipper entry point for the open K2 PRTouch driver."""
# Portions adapted from Jacob10383's K2 Plus custom firmware
# (https://github.com/Jacob10383/k2-plus-custom-firmware, extras/prtouch.py),
# GPLv3: multi-tap selection and the bounded no-trigger retry.

import logging
import statistics

from . import manual_probe
from . import probe as probe_helpers
from . import k2_mesh_guard
from .prtouch_open import endstop as prtouch_endstop
from .prtouch_open import tap_select

_NO_TRIGGER_ERROR = "No trigger on probe after full movement"


class _PRTouchHardwareSession:
    """Adapt the PA15->PC7 MCU endstop to Klipper's probe-session API."""

    def __init__(self, config, owner):
        self.printer = config.get_printer()
        self.owner = owner
        self.driver = owner.driver
        self.offsets = owner.probe_offsets
        self.param_helper = owner.param_helper
        self.settle_time = owner.probe_settle_time
        self.minimum_z = owner.probe_minimum_z
        self.results = []
        self.target_z = probe_helpers.lookup_minimum_z(config)

    def run_probe(self, gcmd):
        toolhead = self.printer.lookup_object("toolhead")
        reactor = self.printer.get_reactor()
        owner = self.owner
        # Unlike the dedicated Z-home taps, standard probe sessions begin
        # immediately after an XY mesh move. Let frame vibration decay before
        # opening the pressure stream.
        toolhead.wait_moves()
        if self.settle_time:
            reactor.pause(reactor.monotonic() + self.settle_time)
        pos = toolhead.get_position()
        start_z = pos[2]
        params = self.param_helper.get_probe_params(gcmd)
        speed = params["probe_speed"]
        lift_speed = params["lift_speed"]
        samples, tolerance, max_touches, noisy = owner.get_probe_tap_settings(
            gcmd)
        plane_limit = owner.contact_plane_limit(gcmd)

        def lift():
            current = toolhead.get_position()
            if current[2] < start_z:
                current[2] = start_z
                toolhead.manual_move(current, lift_speed)
                toolhead.wait_moves()

        def tap(collected):
            # Every tap starts at the same Z. Follow-up taps may only search
            # home_followup_guard below the taps so far, and never below
            # probe_minimum_z.
            target_z = max(self.target_z, self.minimum_z)
            if collected:
                target_z = max(
                    statistics.median(collected) - owner.home_followup_guard,
                    self.minimum_z)
            movepos = list(toolhead.get_position())
            movepos[2] = target_z
            for _attempt in range(owner.probe_plane_retries + 1):
                # The pressure MCU asserts PA15 and the main MCU receives it
                # on PC7. probing_move() arms Klipper's native trsync endstop
                # and returns the stepper position at that hardware edge.
                # minimum_z remains only the finite no-trigger abort target.
                trigger_z = owner.probing_move(movepos, speed, start_z, gcmd)
                if plane_limit is None or trigger_z <= plane_limit:
                    return trigger_z
                gcmd.respond_info(
                    "PRTouch: rejected trigger at X%.2f Y%.2f Z=%.4f, above "
                    "the contact plane limit Z=%.4f (blob or false trigger)"
                    % (pos[0], pos[1], trigger_z, plane_limit))
                lift()
                if owner.home_settle_time:
                    reactor.pause(reactor.monotonic() + owner.home_settle_time)
            raise gcmd.error(
                "PRTouch probe at X%.2f Y%.2f triggered above the contact "
                "plane limit Z=%.4f on %d tries (last Z=%.4f). Clean the "
                "nozzle and bed, or raise probe_arm_below_z."
                % (pos[0], pos[1], plane_limit,
                   owner.probe_plane_retries + 1, trigger_z))

        def between():
            lift()
            if owner.home_settle_time:
                reactor.pause(reactor.monotonic() + owner.home_settle_time)

        try:
            median_z, collected, best = tap_select.collect_taps(
                tap, samples, tolerance, max_touches, noisy, between)
        except tap_select.TapSelectError as e:
            raise gcmd.error(str(e))
        if len(collected) > 1:
            gcmd.respond_info(
                "PRTouch taps at X%.2f Y%.2f: %s -> median %.6f of %d "
                "(spread %.6f)"
                % (pos[0], pos[1],
                   ", ".join("%.6f" % z for z in collected), median_z,
                   len(best), tap_select.sample_range(best)))
        result = manual_probe.create_probe_result(
            [pos[0], pos[1], median_z], self.offsets.get_offsets(gcmd)
        )
        self.results.append(result)

    def pull_probed_results(self):
        results = self.results
        self.results = []
        return results

    def end_probe_session(self):
        self.results = []
        # Each probing_move() owns and cleans up its pressure/step session.


class PRTouchMainlineProbe:
    """Native upstream probe facade backed by open PRTouch sampling."""

    def __init__(self, config, driver):
        self.config = config
        self.printer = config.get_printer()
        self.driver = driver
        self.probe_offsets = probe_helpers.ProbeOffsetsHelper(config)
        self.param_helper = probe_helpers.ProbeParameterHelper(config)
        self.home_samples = config.getint("home_samples", 3, minval=1, maxval=9)
        self.home_start_z = config.getfloat(
            "home_start_z", 2.0, minval=0.5, maxval=10.0
        )
        self.home_tolerance = config.getfloat(
            "home_samples_tolerance", 0.1, minval=0.0, maxval=1.0
        )
        self.home_settle_time = config.getfloat(
            "home_settle_time", 0.5, minval=0.0, maxval=5.0
        )
        self.home_followup_guard = config.getfloat(
            "home_followup_guard", 0.5, minval=0.1, maxval=2.0
        )
        self.probe_settle_time = config.getfloat(
            "probe_settle_time", 0.75, minval=0.0, maxval=5.0
        )
        self.probe_arm_below_z = config.getfloat(
            "probe_arm_below_z", 0.75, minval=-1.0, maxval=3.0
        )
        self.probe_minimum_z = config.getfloat(
            "probe_minimum_z", -0.60, minval=-5.0, maxval=0.0
        )
        # probe_contact_threshold/polls/max_window_p2p/contact_mode belong
        # to the experimental host classifier. The active path lets the
        # pressure MCU decide contact, so they are read here only so existing
        # configs still load. probe_arm_below_z is used: see
        # contact_plane_limit().
        self.probe_contact_threshold = config.getint(
            "probe_contact_threshold", 450, minval=100, maxval=100000
        )
        self.probe_contact_polls = config.getint(
            "probe_contact_polls", 1, minval=1, maxval=8
        )
        self.probe_max_window_p2p = config.getint(
            "probe_max_window_p2p", 100000,
            minval=1000, maxval=100000000
        )
        self.probe_contact_mode = config.get(
            "probe_contact_mode", "amplitude").lower()
        if self.probe_contact_mode not in ("amplitude", "stock"):
            raise config.error(
                "probe_contact_mode must be 'amplitude' or 'stock'"
            )
        if self.probe_minimum_z >= self.probe_arm_below_z:
            raise config.error(
                "probe_minimum_z must be below probe_arm_below_z"
            )
        # Multi-tap selection (K2 Plus firmware method). The defaults keep a
        # single tap per Z home and per probe point. home_max_touches and
        # probe_tap_max_touches of 0 mean "same as the sample count".
        self.home_max_touches = config.getint(
            "home_max_touches", 0, minval=0, maxval=20
        )
        self.home_allowed_noisy = config.getint(
            "home_allowed_noisy", 0, minval=0, maxval=10
        )
        self.probe_tap_samples = config.getint(
            "probe_tap_samples", 1, minval=1, maxval=9
        )
        self.probe_tap_tolerance = config.getfloat(
            "probe_tap_tolerance", 0.1, minval=0.0, maxval=1.0
        )
        self.probe_tap_max_touches = config.getint(
            "probe_tap_max_touches", 0, minval=0, maxval=20
        )
        self.probe_tap_allowed_noisy = config.getint(
            "probe_tap_allowed_noisy", 0, minval=0, maxval=10
        )
        # Bed-mesh contact-plane check: after PRTOUCH_HOME_Z has defined the
        # contact plane as Z=0, a mesh trigger above probe_arm_below_z is a
        # blob or a false trigger, not the bed. It is re-probed, then fails.
        self.probe_plane_check = config.getboolean("probe_plane_check", True)
        self.probe_plane_retries = config.getint(
            "probe_plane_retries", 1, minval=0, maxval=3
        )
        # Bounded retry after a clean no-trigger probe, from the original
        # start Z. Off by default; only moves up to the max distance qualify
        # (a 1 mm mesh tap is 1.6 mm, the 5 mm home tap 5.6 mm).
        self.no_trigger_retries = config.getint(
            "probe_no_trigger_retries", 0, minval=0, maxval=3
        )
        self.no_trigger_retry_max_distance = config.getfloat(
            "probe_no_trigger_retry_max_distance", 2.0, above=0.0, maxval=10.0
        )
        self.contact_plane_frame = None
        self.probe_session = probe_helpers.SampleAveragingHelper(
            config, self.param_helper, self._start_hardware_session
        )
        self.cmd_helper = probe_helpers.ProbeCommandHelper(
            config, self, self.driver.query_endstop
        )
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "PRTOUCH_HOME_Z",
            self.cmd_PRTOUCH_HOME_Z,
            desc="Probe Z using configured samples and define median contact as zero",
        )
        probe_helpers.HomingViaProbeHelper(
            config,
            self.probe_offsets.get_offsets()[2],
            self.driver.query_endstop,
        )
        # Register every active Z stepper with the PC7 MCU_endstop dispatch.
        self.z_stepper_lookup = probe_helpers.LookupZSteppers(
            config, self.driver.add_stepper
        )
        self.mesh_guard = k2_mesh_guard.K2MeshGuard(config, self)
        self.printer.register_event_handler(
            "stepper_enable:motor_off", self._handle_motor_off
        )

    def _start_hardware_session(self, gcmd):
        if gcmd.get_command() == "BED_MESH_CALIBRATE":
            limit = self.contact_plane_limit(gcmd)
            logging.info(
                "PRTouch mesh: contact plane check %s",
                "off (no PRTOUCH_HOME_Z plane)" if limit is None
                else "on, triggers above Z=%.3f rejected" % (limit,))
        return _PRTouchHardwareSession(self.config, self)

    # ---- tap selection, contact plane, no-trigger retry ----------------

    def get_probe_tap_settings(self, gcmd):
        # Klipper's G28 Z homes through run_probe (HOME_ATTEMPT_NUM is set)
        # with the start position forced to a fake height, so lifting back
        # to it between taps is out of range. Homing taps stay single: G28
        # does its own two passes and PRTOUCH_HOME_Z has its own tap settings.
        if gcmd.get('HOME_ATTEMPT_NUM', None) is not None:
            return 1, self.probe_tap_tolerance, 1, 0
        samples = gcmd.get_int(
            "TAP_SAMPLES", self.probe_tap_samples, minval=1, maxval=9
        )
        tolerance = gcmd.get_float(
            "TAP_TOLERANCE", self.probe_tap_tolerance, minval=0.0, maxval=1.0
        )
        max_touches = gcmd.get_int(
            "TAP_MAX_TOUCHES", self.probe_tap_max_touches,
            minval=0, maxval=20
        )
        noisy = gcmd.get_int(
            "TAP_NOISY", self.probe_tap_allowed_noisy, minval=0, maxval=10
        )
        return samples, tolerance, max(max_touches, samples), noisy

    def _z_frame(self):
        """Signature of the current Z coordinate frame.

        mcu_to_commanded_position(0) only changes when something redefines
        logical Z with set_position (KZ_ALIGN_BOTTOM, SET_KINEMATIC_POSITION,
        a new home). Probing moves keep it to within a fraction of a step.
        """
        kin = self.printer.lookup_object("toolhead").get_kinematics()
        return [
            s.mcu_to_commanded_position(0)
            for s in kin.get_steppers() if s.is_active_axis("z")
        ]

    def _handle_motor_off(self, print_time=None):
        self.contact_plane_frame = None

    def contact_plane_valid(self):
        frame = self.contact_plane_frame
        if frame is None:
            return False
        current = self._z_frame()
        return len(current) == len(frame) and all(
            abs(a - b) < 0.05 for a, b in zip(current, frame)
        )

    def contact_plane_limit(self, gcmd):
        """Z above which a bed-mesh trigger is rejected, or None."""
        if (not self.probe_plane_check
                or gcmd.get_command() != "BED_MESH_CALIBRATE"
                or not self.contact_plane_valid()):
            return None
        return self.probe_arm_below_z

    def probing_move(self, movepos, speed, start_z, gcmd):
        """probing_move() with one optional bounded retry on a clean miss."""
        toolhead = self.printer.lookup_object("toolhead")
        phoming = self.printer.lookup_object("homing")
        lift_speed = self.param_helper.get_probe_params(gcmd)["lift_speed"]
        distance = abs(start_z - movepos[2])
        retry = 0
        while True:
            try:
                return phoming.probing_move(
                    self.driver, movepos, speed, check_movement=True
                )[2]
            except self.printer.command_error as e:
                if (str(e) != _NO_TRIGGER_ERROR
                        or self.printer.is_shutdown()
                        or not self.driver.last_no_trigger
                        or not self.driver.last_cleanup_ok
                        or distance > self.no_trigger_retry_max_distance
                        or retry >= self.no_trigger_retries):
                    raise
            retry += 1
            gcmd.respond_info(
                "PRTouch: no trigger; back to Z=%.3f and retrying (%d/%d)"
                % (start_z, retry, self.no_trigger_retries)
            )
            current = toolhead.get_position()
            current[2] = start_z
            toolhead.manual_move(current, lift_speed)
            toolhead.wait_moves()

    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)

    def get_offsets(self, gcmd=None):
        return self.probe_offsets.get_offsets(gcmd)

    def get_status(self, eventtime):
        return self.cmd_helper.get_status(eventtime)

    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)

    def cmd_PRTOUCH_HOME_Z(self, gcmd):
        """Run bounded K2-specific taps and define median contact as Z zero."""
        toolhead = self.printer.lookup_object("toolhead")
        reactor = self.printer.get_reactor()
        params = self.param_helper.get_probe_params(gcmd)
        speed = params["probe_speed"]
        lift_speed = params["lift_speed"]
        sample_count = gcmd.get_int(
            "SAMPLES", self.home_samples, minval=1, maxval=9
        )
        start_z = gcmd.get_float(
            "START_Z", self.home_start_z, minval=0.5, maxval=10.0
        )
        tolerance = gcmd.get_float(
            "SAMPLES_TOLERANCE", self.home_tolerance, minval=0.0, maxval=1.0
        )
        max_touches = gcmd.get_int(
            "MAX_TOUCHES", self.home_max_touches, minval=0, maxval=20
        )
        max_touches = max(max_touches, sample_count)
        allowed_noisy = gcmd.get_int(
            "ALLOWED_NOISY", self.home_allowed_noisy, minval=0, maxval=10
        )
        # A failed or partial home must not leave an old plane advertised.
        self.contact_plane_frame = None

        def tap(trigger_zs):
            # Every tap starts from the same temporary absolute coordinate.
            # This prevents a retry or false trigger from walking the bed
            # downward one retract distance at a time.
            current = toolhead.get_position()
            current[2] = start_z
            toolhead.manual_move(current, lift_speed)
            toolhead.wait_moves()
            if self.home_settle_time:
                reactor.pause(reactor.monotonic() + self.home_settle_time)

            try:
                target_z = probe_helpers.lookup_minimum_z(self.config)
                if trigger_zs:
                    reference_z = statistics.median(trigger_zs)
                    target_z = reference_z - self.home_followup_guard
                target_z = max(target_z, self.probe_minimum_z)
                movepos = toolhead.get_position()
                movepos[2] = target_z
                trigger_z = self.probing_move(movepos, speed, start_z, gcmd)
            except self.printer.command_error:
                # Return all the way to the fixed start coordinate before
                # propagating any hardware-endstop failure.
                current = toolhead.get_position()
                if current[2] < start_z:
                    current[2] = start_z
                    toolhead.manual_move(current, lift_speed)
                    toolhead.wait_moves()
                # A failed contact search did not establish the nozzle plane.
                # Do not leave the coarse or temporary Z coordinate advertised
                # as a completed home.
                toolhead.get_kinematics().clear_homing_state("z")
                raise
            if max_touches == sample_count:
                gcmd.respond_info(
                    "PRTouch tap %d/%d: contact Z=%.6f"
                    % (len(trigger_zs) + 1, sample_count, trigger_z)
                )
            else:
                gcmd.respond_info(
                    "PRTouch tap %d (best %d of up to %d): contact Z=%.6f"
                    % (len(trigger_zs) + 1, sample_count, max_touches,
                       trigger_z)
                )

            # Unload the strain gauge immediately after every successful tap,
            # including the final one.
            current = toolhead.get_position()
            current[2] = start_z
            toolhead.manual_move(current, lift_speed)
            toolhead.wait_moves()
            return trigger_z

        # With the defaults (max_touches == samples, allowed_noisy 0) this is
        # exactly "take SAMPLES taps, abort if their spread exceeds the
        # tolerance". More touches or noisy taps let it keep the tightest
        # SAMPLES of a sliding window instead.
        try:
            median_z, trigger_zs, best = tap_select.collect_taps(
                tap, sample_count, tolerance, max_touches, allowed_noisy
            )
        except tap_select.TapSelectError as e:
            toolhead.get_kinematics().clear_homing_state("z")
            raise gcmd.error(str(e))
        spread = tap_select.sample_range(best)

        current = toolhead.get_position()
        # We are retracted at start_z. Preserve that physical clearance while
        # defining the median nozzle/bed contact plane as logical Z=0.
        current[2] -= median_z
        toolhead.set_position(current, homing_axes="z")
        self.contact_plane_frame = self._z_frame()

        gcode = self.printer.lookup_object("gcode")
        self.cmd_helper.last_probe_position = gcode.Coord(
            (current[0], current[1], median_z)
        )
        self.cmd_helper.last_z_result = median_z
        msg = (
            "PRTouch Z home: median %.6f spread %.6f, retracted Z %.6f"
            % (median_z, spread, current[2])
        )
        if len(best) != len(trigger_zs):
            msg += " (best %d of %d taps)" % (len(best), len(trigger_zs))
        gcmd.respond_info(msg)


def load_config(config):
    prtouch = prtouch_endstop.PRTouchEndstopWrapper(config)
    probe = PRTouchMainlineProbe(config, prtouch)
    config.get_printer().add_object("probe", probe)
    return prtouch
