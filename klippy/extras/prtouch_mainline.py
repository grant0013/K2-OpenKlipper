"""Upstream-Klipper entry point for the open K2 PRTouch driver."""

import statistics

from . import manual_probe
from . import probe as probe_helpers
from . import k2_mesh_guard
from .prtouch_open import endstop as prtouch_endstop


class _PRTouchHardwareSession:
    """Adapt the PA15->PC7 MCU endstop to Klipper's probe-session API."""

    def __init__(
        self,
        config,
        driver,
        offsets,
        param_helper,
        settle_time,
        arm_below_z,
        minimum_z,
        contact_threshold,
        contact_polls,
        max_window_p2p,
        contact_mode,
    ):
        self.printer = config.get_printer()
        self.driver = driver
        self.offsets = offsets
        self.param_helper = param_helper
        self.settle_time = settle_time
        self.arm_below_z = arm_below_z
        self.minimum_z = minimum_z
        self.contact_threshold = contact_threshold
        self.contact_polls = contact_polls
        self.max_window_p2p = max_window_p2p
        self.contact_mode = contact_mode
        self.results = []
        self.target_z = probe_helpers.lookup_minimum_z(config)

    def run_probe(self, gcmd):
        toolhead = self.printer.lookup_object("toolhead")
        # Unlike the dedicated Z-home taps, standard probe sessions begin
        # immediately after an XY mesh move. Let frame vibration decay before
        # opening the pressure stream.
        toolhead.wait_moves()
        if self.settle_time:
            reactor = self.printer.get_reactor()
            reactor.pause(reactor.monotonic() + self.settle_time)
        pos = toolhead.get_position()
        speed = self.param_helper.get_probe_params(gcmd)["probe_speed"]
        target_z = max(self.target_z, self.minimum_z)
        # The pressure MCU asserts PA15 and the main MCU receives it on PC7.
        # probing_move() arms Klipper's native trsync endstop and returns the
        # stepper position at that hardware edge. minimum_z remains only the
        # finite no-trigger abort target.
        movepos = list(pos)
        movepos[2] = target_z
        phoming = self.printer.lookup_object("homing")
        trigger_z = phoming.probing_move(
            self.driver, movepos, speed, check_movement=True
        )[2]
        result = manual_probe.create_probe_result(
            [pos[0], pos[1], trigger_z], self.offsets.get_offsets(gcmd)
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

    def _start_hardware_session(self, gcmd):
        return _PRTouchHardwareSession(
            self.config,
            self.driver,
            self.probe_offsets,
            self.param_helper,
            self.probe_settle_time,
            self.probe_arm_below_z,
            self.probe_minimum_z,
            self.probe_contact_threshold,
            self.probe_contact_polls,
            self.probe_max_window_p2p,
            self.probe_contact_mode,
        )

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

        trigger_zs = []
        for sample_index in range(sample_count):
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
                phoming = self.printer.lookup_object("homing")
                trigger_z = phoming.probing_move(
                    self.driver, movepos, speed, check_movement=True
                )[2]
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
            trigger_zs.append(trigger_z)
            gcmd.respond_info(
                "PRTouch tap %d/%d: contact Z=%.6f"
                % (sample_index + 1, sample_count, trigger_z)
            )

            # Unload the strain gauge immediately after every successful tap,
            # including the final one.
            current = toolhead.get_position()
            current[2] = start_z
            toolhead.manual_move(current, lift_speed)
            toolhead.wait_moves()

        spread = max(trigger_zs) - min(trigger_zs)
        if spread > tolerance:
            toolhead.get_kinematics().clear_homing_state("z")
            raise gcmd.error(
                "PRTouch taps disagree by %.6fmm (limit %.6fmm): %s"
                % (
                    spread,
                    tolerance,
                    ", ".join("%.6f" % z for z in trigger_zs),
                )
            )

        median_z = statistics.median(trigger_zs)
        current = toolhead.get_position()
        # We are retracted at start_z. Preserve that physical clearance while
        # defining the median nozzle/bed contact plane as logical Z=0.
        current[2] -= median_z
        toolhead.set_position(current, homing_axes="z")

        gcode = self.printer.lookup_object("gcode")
        self.cmd_helper.last_probe_position = gcode.Coord(
            (current[0], current[1], median_z)
        )
        self.cmd_helper.last_z_result = median_z
        gcmd.respond_info(
            "PRTouch Z home: median %.6f spread %.6f, retracted Z %.6f"
            % (median_z, spread, current[2])
        )


def load_config(config):
    prtouch = prtouch_endstop.PRTouchEndstopWrapper(config)
    probe = PRTouchMainlineProbe(config, prtouch)
    config.get_printer().add_object("probe", probe)
    return prtouch
