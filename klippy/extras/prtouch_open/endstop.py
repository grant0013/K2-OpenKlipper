# endstop.py — PRTouchEndstopWrapper (duck-typed probe-endstop facade)
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# Class structure derived from upstream Creality prtouch.py
# (GPL-3, wangyulong) at docs/source-reference/prtouch.py, specifically:
#   PRTouchEndstopWrapper          prtouch.py:106-118  (class + constructor)
#   _handle_mcu_identify           prtouch.py:120-138  (mesh-bounds lookups)
#   multi_probe_begin/end          prtouch.py:143-147  (no-ops, preserved)
#   probe_prepare/finish           prtouch.py:149-150
#   home_start                     prtouch.py:152-154  (always True — v1/v2 deferred to probe_by_step)
#   add_stepper / get_steppers     prtouch.py:156-160
#
# Portions adapted from Jacob10383's K2 Plus custom firmware
# (https://github.com/Jacob10383/k2-plus-custom-firmware, extras/prtouch.py),
# GPLv3: fail-closed arming (ack err check, trigger line asserted/released
# checks, disarm on motors off), the no-trigger pressure-buffer dump and the
# firmer-press hold scaling used by PRTOUCH_SCRUB.
#
# Note: the upstream `home_start` returns True immediately because v1/v2
# prtouch implements its own probe motion via `probe_by_step` rather than
# Klipper's standard endstop polling. For our v3-targeted reimplementation
# we keep the same behaviour for compatibility, but in M2 we'll add a
# proper Klipper-native probe.PrinterProbe-driven path as well so upstream
# bed_mesh + screws_tilt_adjust work straight through.
"""PRTouchEndstopWrapper — the duck-typed endstop facade.

This is the class that gets instantiated by `load_config()` and registered as
`printer.lookup_object('prtouch_v3')`. It implements Klipper's endstop-wrapper
interface (multi_probe_begin/end, probe_prepare/finish, home_start, get_steppers,
add_stepper, query_endstop, get_position_endstop) and orchestrates the
PressureSampler and StepCapture subsystems to perform the actual probing.

Per ADR-001 (see docs/ADRs/ADR-001-probe-api-integration.md):
- We provide a drop-in replacement for `prtouch_v3_wrapper.PRTouchEndstopWrapper`.
- Users keep their existing `[prtouch_v3]` config section — no breaking changes.
- We deliberately DO NOT register the same instance as `axis_twist_compensation`
  the way Creality's `extras/prtouch_v3.py` does (line 13). That hijack blocks
  upstream Klipper's own `axis_twist_compensation` module from loading. Our
  load_config() entry-point drops the hijack line.
"""

import contextlib
import logging
import math

from . import cfg as cfg_mod
from . import pres as pres_mod
from . import step as step_mod
from . import motion as motion_mod
from . import zip as zip_mod
from . import scrub as scrub_mod

# The nozzle MCU's pressure buffer holds 64 samples (docs/PRTOUCH.md).
_PRES_BUF_LEN = 64


def _contact_hysteresis_hold(filtered_abs, entry_threshold,
                             release_threshold, previous_hold):
    """Advance a sustained-contact counter using a Schmitt-style latch.

    A contact candidate must first cross the conservative entry threshold.
    Once entered, it only resets below the lower release threshold. This
    rejects isolated motion spikes while allowing a real nozzle/bed contact
    whose pressure rings around the entry threshold between sample windows.
    """
    active_threshold = (
        entry_threshold if previous_hold <= 0 else release_threshold
    )
    if filtered_abs >= active_threshold:
        return previous_hold + 1
    return 0


def _parse_pin_mcu(pin_spec, default_mcu='mcu'):
    """Extract the MCU name from a pin spec like 'nozzle_mcu:PA15' or '!PC7'.

    Returns (mcu_name, bare_pin) where bare_pin keeps any inversion/pullup
    prefix (e.g. '!' or '^') stripped (Klipper handles those at pin_resolver).
    """
    s = pin_spec.strip()
    # Strip leading prefix characters (!, ^, ~)
    while s and s[0] in '!^~':
        s = s[1:]
    if ':' in s:
        mcu_name, bare = s.split(':', 1)
        return mcu_name.strip(), bare.strip()
    return default_mcu, s.strip()


class _PrtouchVal:
    """Runtime state. Mirrors prtouch.py:64-74 PRTouchVAL."""

    def __init__(self):
        self.out_index = 0
        self.out_val_mm = 0.0
        self.rdy_pos = [[0., 0., 0.]] * 4
        self.gap_pos = None
        self.g29_cnt = 0
        self.re_probe_cnt = 0
        self.home_xy = None
        self.jump_probe_ready = False


class _PrtouchObj:
    """Klipper-object lookups. Mirrors prtouch.py:77-103 PRTouchOBJ but
    drops the v1/v2-only `hx711s` and `dirzctl` lookups (in v3 these are
    consolidated into the prtouch_v3 MCU module — accessed via PressureSampler
    and StepCapture). Also drops the `filter` lookup pending the M2 filter
    reimplementation — see pres.py TODO(filter)."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        # Filled in by find_objs()
        self.toolhead = None
        self.pheaters = None
        self.heater_hot = None
        self.heater_bed = None
        self.bed_mesh = None
        self.mcu = None
        self.kin = None

    def find_objs(self, pres_mcu_name='nozzle_mcu', step_mcu_name='mcu'):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.pheaters = self.printer.lookup_object('heaters')
        self.heater_hot = self.printer.lookup_object('extruder').heater
        self.heater_bed = self.printer.lookup_object('heater_bed').heater
        self.bed_mesh = self.printer.lookup_object('bed_mesh')
        self.kin = self.toolhead.get_kinematics()
        # The pres subsystem lives on the MCU named in pres_swap_pin (nozzle_mcu on K2);
        # step capture lives on the main MCU (whichever owns the Z stepper).
        self.pres_mcu = self.printer.lookup_object(
            'mcu' if pres_mcu_name == 'mcu' else f'mcu {pres_mcu_name}'
        )
        self.step_mcu = self.printer.lookup_object(
            'mcu' if step_mcu_name == 'mcu' else f'mcu {step_mcu_name}'
        )
        # Back-compat alias — most callers want the pres-side MCU
        self.mcu = self.pres_mcu


class PRTouchEndstopWrapper:
    """Open replacement for prtouch_v3_wrapper.PRTouchEndstopWrapper.

    Creality's wrapper is an MCU_endstop on the main-MCU PC7 swap input.
    This implementation composes Klipper's native MCU_endstop returned by
    pins.setup_pin() and delegates the endstop interface to it. The pressure
    MCU therefore triggers normal trsync homing over the physical PA15->PC7
    link; host sampling is retained only for diagnostics.

    M1 surface (what works in this milestone):
      get_steppers()           — return the Z steppers managed by the prtouch flow
      multi_probe_begin/end()  — no-op
      probe_prepare/finish()   — no-op
      home_start()             — returns True (v1/v2 deferred-probing pattern)
      query_endstop()          — TBD, depends on live K2 testing
      get_position_endstop()   — returns the probe-Z offset
      add_stepper()            — registers a stepper that participates in homing

    M2 additions (deferred):
      Native upstream probing motion via `_check_trigger` cascade against
      the PressureSampler streaming buffer. This is what makes
      `BED_MESH_CALIBRATE` work through us.
    """

    cmd_PRTOUCH_TEST_help = "K2-PRTouch: open replacement of PRTOUCH_TEST"
    cmd_PRTOUCH_READY_help = "K2-PRTouch: open replacement of PRTOUCH_READY"

    def __init__(self, config):
        self.cfg = cfg_mod.PrtouchConfig(config)
        self.val = _PrtouchVal()
        self.obj = _PrtouchObj(config)

        # ---- Parse pin specs and resolve MCUs at __init__ time -----------
        # CRITICAL: Klipper processes register_config_callback callbacks
        # during the connect phase. Registering AFTER klippy:mcu_identify
        # would race with the MCU config phase. So we wire up OIDs and
        # callbacks here, before any event handlers fire.
        pres_mcu_name, swp_pin_bare = _parse_pin_mcu(self.cfg.pres_swap_pin)
        step_mcu_name, step_swp_pin_bare = _parse_pin_mcu(self.cfg.step_swap_pin)
        _, clk_pin_bare = _parse_pin_mcu(self.cfg.clk_pin)
        _, sdo_pin_bare = _parse_pin_mcu(self.cfg.sdo_pin)
        logging.info(
            "K2-PRTouch __init__: pres_mcu=%s swp=%s clk=%s sdo=%s | "
            "step_mcu=%s swp=%s",
            pres_mcu_name, swp_pin_bare, clk_pin_bare, sdo_pin_bare,
            step_mcu_name, step_swp_pin_bare,
        )

        printer = self.obj.printer
        # Stock K2 architecture: nozzle_mcu:PA15 is wired to the main MCU's
        # inverted PC7 input. Register PC7 as a real Klipper MCU_endstop so
        # its edge halts probing through trsync instead of an 80ms host timer.
        self._mcu_endstop = printer.lookup_object('pins').setup_pin(
            'endstop', self.cfg.step_swap_pin,
        )
        self.pres_mcu = printer.lookup_object(
            'mcu' if pres_mcu_name == 'mcu' else f'mcu {pres_mcu_name}'
        )
        self.step_mcu = self._mcu_endstop.get_mcu()
        self._probe_active = False
        # Outcome of the last probe move, read by the bounded no-trigger
        # retry in prtouch_mainline.py.
        self.last_no_trigger = False
        self.last_cleanup_ok = False
        # Pressure hold thresholds scale by this for the next probe moves
        # (PRTOUCH_SCRUB's firmer press). Only firm_press() changes it.
        self.hold_ratio = 1.0

        # ---- PressureSampler + OID + callbacks (early registration) ------
        self.sampler = pres_mod.PressureSampler(self.pres_mcu, self.cfg)
        self.pres_oid = self.pres_mcu.create_oid()
        self.sampler.add_channel(
            oid=self.pres_oid, idx=0,
            swp_pin=swp_pin_bare, clk_pin=clk_pin_bare, sdo_pin=sdo_pin_bare,
        )
        # M2 diagnostic: async-push listener. Counts every resault_prtouch_pres
        # the MCU sends to *us* (matched by our pres_oid). If the MCU pushes
        # samples unsolicited while streaming, count > 0 without any
        # `read_prtouch_pres` having been issued — that's the green light
        # for an async-handler-based probe architecture.
        self._async_pres = {
            'count': 0,           # total pushes since last reset
            'last_tri_chxs': 0,
            'last_buf_len': None,
            'last_idx': None,
            'last_len': None,
            'last_datas_len': 0,
            'tri_events': 0,      # pushes with tri_chxs != 0
            'enabled': True,
        }
        self.pres_mcu.register_config_callback(self.sampler.build_config)

        # ---- StepCapture + OID + callbacks ------------------------------
        # Note: stepper-OID lookup is deferred to _handle_mcu_identify
        # because the kinematics object isn't available yet. We pre-allocate
        # the step OID and queue config with placeholder stepper OIDs (0);
        # the real values get patched in just before build_config fires.
        self.step = step_mod.StepCapture(self.step_mcu, self.cfg)
        self.step_oid = self.step_mcu.create_oid()
        self.step.configure(
            oid=self.step_oid,
            oid_xstp=0, oid_ystp=0, oid_zstp=0,  # patched in _handle_mcu_identify
            swp_pin=step_swp_pin_bare,
        )
        self.step_mcu.register_config_callback(self.step.build_config)

        # Cache pin parses for the mcu_identify finalisation step
        self._pres_mcu_name = pres_mcu_name
        self._step_mcu_name = step_mcu_name

        # Wire up to Klipper's mcu_identify event for the rest (find_objs,
        # geometry, attach steppers, etc.).
        printer.register_event_handler(
            'klippy:mcu_identify', self._handle_mcu_identify
        )

        # ---- Creality-fork compat attributes/methods ---------------------
        # The K2's Klipper fork patches homing.py and probe.py to call back
        # into prtouch_v3 (homing.py:46, 47, 120, 134, 268 + probe.py:124,
        # 126). The minimal surface they touch:
        #   self.prtouch_v3.z_full_movement_flag   (settable bool)
        #   self.prtouch_v3.get_suspended_det_status()   (returns bool)
        self.z_full_movement_flag = False

        # Minimal gcode surface — the macro layer can grow in M3.
        self.obj.gcode.register_command(
            'PRTOUCH_TEST', self.cmd_PRTOUCH_TEST, desc=self.cmd_PRTOUCH_TEST_help,
        )
        self.obj.gcode.register_command(
            'PRTOUCH_READY', self.cmd_PRTOUCH_READY, desc=self.cmd_PRTOUCH_READY_help,
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_BASELINE', self.cmd_K2_PRTOUCH_BASELINE,
            desc="K2-PRTouch: read a single sample window without moving Z (M1 wire-layer smoke test)",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_TAP', self.cmd_K2_PRTOUCH_TAP,
            desc="K2-PRTouch: stream samples for N seconds and report peak-to-peak — tap the nozzle to test",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_TRIGGER_TEST', self.cmd_K2_PRTOUCH_TRIGGER_TEST,
            desc="K2-PRTouch: run the full filter+check_trigger cascade on live samples; reports whether trigger ever fires",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_PROBE_BUF', self.cmd_K2_PRTOUCH_PROBE_BUF,
            desc="K2-PRTouch: diagnostic — sweep idx + is_src to find the live MCU sample buffer pointer",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_TAP_LIVE', self.cmd_K2_PRTOUCH_TAP_LIVE,
            desc="K2-PRTouch: brackets each read with start/stop streaming — fresh buffer every iteration",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_PROBE_ONCE', self.cmd_K2_PROBE_ONCE,
            desc="K2-PRTouch: chunked Z descent + trigger cascade — open-source Z-probing motion",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_LISTEN', self.cmd_K2_PROBE_LISTEN,
            desc="K2-PRTouch: diagnostic — does the MCU push resault_prtouch_pres unsolicited?",
        )
        self.obj.gcode.register_command(
            'PRTOUCH_OPEN_DURING_MOTION', self.cmd_K2_PROBE_DURING_MOTION,
            desc="K2-PRTouch: M2 — reactor-timer sampler runs in parallel with non-blocking Z descent",
        )

        # Start/stop the pressure and step sessions around only those homing
        # moves that use this PC7 endstop.
        printer.register_event_handler(
            'homing:homing_move_begin', self._handle_homing_move_begin,
        )
        printer.register_event_handler(
            'homing:homing_move_end', self._handle_homing_move_end,
        )
        printer.register_event_handler('klippy:shutdown', self._handle_shutdown)
        printer.register_event_handler(
            'stepper_enable:motor_off', self._handle_motor_off,
        )

    def _handle_mcu_identify(self):
        # OID allocation + register_config_callback already happened in
        # __init__ so the MCU config phase has our callbacks.
        # Here we do the rest: find objects, compute geometry, attach steppers.
        self.obj.find_objs(
            pres_mcu_name=self._pres_mcu_name,
            step_mcu_name=self._step_mcu_name,
        )

        # Compute corner positions in the bed-mesh region for probe-ready moves.
        # Mirrors prtouch.py:122-138.
        min_x, min_y = self.obj.bed_mesh.bmc.mesh_min
        max_x, max_y = self.obj.bed_mesh.bmc.mesh_max
        self.val.rdy_pos = [
            [min_x, min_y, self.cfg.bed_max_err + 1.],
            [min_x, max_y, self.cfg.bed_max_err + 1.],
            [max_x, max_y, self.cfg.bed_max_err + 1.],
            [max_x, min_y, self.cfg.bed_max_err + 1.],
        ]
        self.val.gap_pos = [
            [min_x + 1., min_y + 1., self.cfg.z_gap_00],
            [min_x + 1., max_y - 1., self.cfg.z_gap_01],
            [max_x - 1., max_y - 1., self.cfg.z_gap_11],
            [max_x - 1., min_y + 1., self.cfg.z_gap_10],
        ]
        # If the user didn't configure the nozzle-clear region, default it.
        if (
            self.cfg.clr_noz_start_x <= 0 or self.cfg.clr_noz_start_y <= 0
            or self.cfg.clr_noz_len_x <= 0 or self.cfg.clr_noz_len_y <= 0
        ):
            self.cfg.clr_noz_start_x = (max_x - min_x) * 1 / 3 + min_x
            self.cfg.clr_noz_start_y = max_y - 6
            self.cfg.clr_noz_len_x = (max_x - min_x) * 1 / 3
            self.cfg.clr_noz_len_y = 5
        self.val.home_xy = [(max_x - min_x) / 2 + min_x, (max_y - min_y) / 2 + min_y]

        # ---- Patch stepper OIDs into step.bound_oids ----
        # These weren't available at __init__ (kinematics not built yet),
        # so we filled placeholders. Patch them now; the build_config
        # callback hasn't fired yet (klippy:connect comes after mcu_identify).
        x_stepper_oid = self._lookup_stepper_oid('stepper_x')
        y_stepper_oid = self._lookup_stepper_oid('stepper_y')
        z_stepper_oid = self._lookup_stepper_oid('stepper_z')
        self.step.bound_oids = (x_stepper_oid, y_stepper_oid, z_stepper_oid)
        # Attach Z stepper for step_dist + step_base math
        z_steppers = [s for s in self.obj.kin.get_steppers() if 'z' in s.get_name().lower()]
        self.step.attach_steppers(z_steppers)

        # ---- ProbeMotion (composes sampler + step) ----
        self.motion = motion_mod.ProbeMotion(self)

    def _check_ack(self, label, ack):
        """Fail closed when the MCU acks a start command with an error.

        The pres/step start commands are query commands, so send() already
        waits for the matching ack_prtouch (and raises on timeout). The err
        field was never looked at: an MCU that refused to start would still
        let the probing move run with nothing to stop it.
        """
        if not self.cfg.pres_arm_checks or not isinstance(ack, dict):
            return
        err = ack.get('err', 0)
        if err:
            raise self.obj.printer.command_error(
                "K2-PRTouch: %s refused by the MCU (ack err=%s expar0=%s "
                "expar1=%s); not probing"
                % (label, err, ack.get('expar0'), ack.get('expar1')))

    def _swap_triggered(self):
        """Read PC7 now (True = the pressure MCU is signalling contact)."""
        mcu = self.step_mcu
        if mcu.is_fileoutput():
            return False
        reactor = self.obj.printer.get_reactor()
        print_time = mcu.estimated_print_time(reactor.monotonic())
        return bool(self._mcu_endstop.query_endstop(print_time))

    def _wait_swap_release(self):
        reactor = self.obj.printer.get_reactor()
        deadline = reactor.monotonic() + self.cfg.pres_release_timeout
        while self._swap_triggered():
            now = reactor.monotonic()
            if now >= deadline:
                return False
            reactor.pause(min(deadline, now + 0.005))
        return True

    def _handle_shutdown(self):
        # The MCUs are shut down; nothing can be sent. Forget the session so
        # a restart does not refuse its first probe as "already active".
        self._probe_active = False
        self.sampler._streaming = False
        self.step._capture_active = False

    def _handle_motor_off(self, print_time=None):
        if not self._probe_active:
            return
        logging.warning("K2-PRTouch: motors off during a probe; disarming")
        try:
            self.probe_finish(None)
        except Exception:
            logging.exception("K2-PRTouch: disarm on motors off failed")
            self._probe_active = False

    def _handle_async_pres(self, params):
        """Capture every resault_prtouch_pres pushed by the MCU.

        Registered against our pres_oid so we only catch our channel's
        responses. Used by K2_PROBE_LISTEN to determine whether the MCU
        pushes samples unsolicited (no read_prtouch_pres outstanding).

        Also serves as the foundation for the future M2 architecture:
        an async handler that runs the trigger cascade as samples arrive,
        decoupled from motion.
        """
        st = self._async_pres
        if not st.get('enabled', True):
            return
        st['count'] += 1
        st['last_tri_chxs'] = params.get('tri_chxs', 0)
        st['last_buf_len'] = params.get('buf_len')
        st['last_idx'] = params.get('idx')
        st['last_len'] = params.get('len')
        datas = params.get('datas', b'')
        st['last_datas_len'] = len(datas) if datas is not None else 0
        if st['last_tri_chxs']:
            st['tri_events'] += 1

    def _lookup_stepper_oid(self, stepper_name):
        """Find the MCU OID for a named stepper via Klipper introspection.

        TODO(live): the exact attribute path depends on Klipper version. On
        recent Klipper builds, the stepper object exposes its OID via
        `stepper.get_mcu_position` or similar. The robust path is to walk
        `mcu._oid_count_table` and find the entry whose alloc_msg matches.
        For now, we try a few likely attributes and fall back to 0.
        """
        for s in self.obj.kin.get_steppers():
            if s.get_name() == stepper_name:
                # Try common attribute paths
                for attr in ('_oid', '_mcu_position_oid'):
                    if hasattr(s, attr):
                        return getattr(s, attr)
                # Some Klipper builds expose it via a method:
                if hasattr(s, 'get_mcu_position'):
                    # The internal MCU position helper carries the OID
                    pass
                # Walk into the stepper's internal MCU helper
                mcu_helper = getattr(s, '_stepper_kinematics', None) \
                    or getattr(s, '_step_motor', None)
                if mcu_helper is not None and hasattr(mcu_helper, '_oid'):
                    return mcu_helper._oid
                logging.warning(
                    "K2-PRTouch: could not extract OID for stepper %s "
                    "— prtouch_step may not bind correctly", stepper_name,
                )
                return 0
        # Stepper not found
        logging.warning("K2-PRTouch: stepper %s not found in kinematics", stepper_name)
        return 0

    # ---- Klipper endstop-wrapper interface --------------------------------

    def get_mcu(self):
        return self._mcu_endstop.get_mcu()

    def add_stepper(self, stepper):
        return self._mcu_endstop.add_stepper(stepper)

    def get_steppers(self):
        return self._mcu_endstop.get_steppers()

    def multi_probe_begin(self):
        # No-op in v1/v2; preserved for behavioural identity.
        pass

    def multi_probe_end(self):
        pass

    def _handle_homing_move_begin(self, hmove):
        if self in hmove.get_mcu_endstops():
            self.probe_prepare(hmove)

    def _handle_homing_move_end(self, hmove):
        if self in hmove.get_mcu_endstops():
            self.probe_finish(hmove)

    @contextlib.contextmanager
    def firm_press(self, ratio):
        """Arm probes inside the block with the hold thresholds x ratio.

        The ratio goes back to its previous value however the block ends.
        """
        if ratio <= 0.:
            raise ValueError("hold ratio must be above 0")
        previous = self.hold_ratio
        self.hold_ratio = ratio
        try:
            yield
        finally:
            self.hold_ratio = previous

    def probe_prepare(self, hmove):
        if self._probe_active:
            raise self.obj.printer.command_error(
                "K2-PRTouch pressure session already active")
        self.last_no_trigger = False
        self.last_cleanup_ok = False
        min_hold, max_hold, add_hold = scrub_mod.scaled_holds(
            self.cfg.min_hold, self.cfg.max_hold, self.cfg.add_hold,
            self.hold_ratio)
        # Arm step capture first so no pressure edge can precede its timestamp
        # history. Both MCUs use the stock 0.78125ms acquisition period.
        step_ack = self.step.start_capture()
        try:
            self._check_ack('start_prtouch_step', step_ack)
            pres_acks = self.sampler.start_streaming(
                cfg_regs=self.cfg.cfg_regs,
                ned_tftr=self.cfg.pres_ned_tftr,
                ned_hftr=self.cfg.pres_ned_hftr,
                ned_lftr=self.cfg.pres_ned_lftr,
                min_hold=min_hold,
                max_hold=max_hold,
                add_hold=add_hold,
                lmt_dead=self.cfg.pres_dead_samples,
            )
            for ack in pres_acks or ():
                self._check_ack('start_prtouch_pres', ack)
        except Exception:
            try:
                self.sampler.stop_streaming(sta_swap=1)
            finally:
                self.step.stop_capture()
            raise
        self._probe_active = True
        # The MCU deliberately suppresses triggering for the first
        # pres_dead_samples. Establish that baseline while stationary, before
        # Klipper arms PC7 and begins the probing move.
        warmup = (self.cfg.pres_dead_samples + 8) * 0.00078125
        reactor = self.obj.printer.get_reactor()
        reactor.pause(reactor.monotonic() + warmup)
        logging.info(
            "K2-PRTouch hardware session armed: filters=%d,%d,%d "
            "holds=%d,%d,%d dead=%d warmup=%.4f",
            self.cfg.pres_ned_tftr, self.cfg.pres_ned_hftr,
            self.cfg.pres_ned_lftr, min_hold, max_hold, add_hold,
            self.cfg.pres_dead_samples, warmup,
        )
        # A line that already reads "contact" would stop the move at once and
        # report the start height as the bed. Refuse instead.
        if self.cfg.pres_arm_checks and self._swap_triggered():
            self.probe_finish(None)
            raise self.obj.printer.command_error(
                "K2-PRTouch: contact line PA15->PC7 is already triggered "
                "after arming, before any movement; refusing to probe. Lift "
                "the nozzle clear of the bed, clean it, and retry.")

    def probe_finish(self, hmove):
        if not self._probe_active:
            return
        try:
            # sta_swap=1 returns PA15/PC7 to the untriggered high state.
            self.sampler.stop_streaming(sta_swap=1)
        finally:
            self.step.stop_capture()
            self._probe_active = False
        self.last_cleanup_ok = True
        # The next probe refuses to start on a stuck line anyway, so a line
        # that does not release is reported rather than failing a probe that
        # has already measured.
        if not self.cfg.pres_arm_checks:
            return
        try:
            released = self._wait_swap_release()
        except Exception:
            logging.exception("K2-PRTouch: PC7 release query failed")
            released = False
        if not released:
            self.last_cleanup_ok = False
            msg = ("K2-PRTouch: contact line PC7 did not release within "
                   "%.3fs after the probe" % (self.cfg.pres_release_timeout,))
            logging.warning(msg)
            self.obj.gcode.respond_info(msg)

    def home_start(
        self, print_time, sample_time, sample_count, rest_time, triggered=True
    ):
        try:
            return self._mcu_endstop.home_start(
                print_time, sample_time, sample_count, rest_time,
                triggered=triggered,
            )
        except Exception:
            self.probe_finish(None)
            raise

    def home_wait(self, home_end_time):
        try:
            result = self._mcu_endstop.home_wait(home_end_time)
            if result == 0. and self._probe_active:
                # The move ran to its end without a contact edge.
                self.last_no_trigger = True
                if self.cfg.probe_fail_dump:
                    self._dump_pressure_buffer()
            return result
        finally:
            # HomingMove emits its end event later, but clean up immediately
            # after the trigger result so PC7 cannot remain asserted.
            self.probe_finish(None)

    def _dump_pressure_buffer(self):
        """Log the MCU's 64-sample pressure window after a missed contact.

        Read while still streaming: stopping first empties the buffer. Pages
        decode on their own (prtouch_open.zip). Never raises.
        """
        try:
            if self.step_mcu.is_fileoutput() or self.sampler.proto is None:
                return
            oid = self.pres_oid
            idx = 0
            meta = None
            ticks = []
            values = []
            for _page in range(16):
                if idx >= _PRES_BUF_LEN:
                    break
                resp = self.sampler.proto.read.send(
                    [oid, 0, 0, idx, _PRES_BUF_LEN - idx])
                if meta is None:
                    meta = resp
                n = resp.get('len', 0) if isinstance(resp, dict) else 0
                if not n:
                    break
                _, vals = zip_mod.unzip_data(resp.get('datas', b''), True)
                _, tks = zip_mod.unzip_data(resp.get('ticks', b''), False)
                values.extend(vals)
                ticks.extend(tks)
                idx += n
            meta = meta if isinstance(meta, dict) else {}
            if values:
                summary = ("samples=%d first=%d last=%d min=%d max=%d "
                           "range=%d"
                           % (len(values), values[0], values[-1],
                              min(values), max(values),
                              max(values) - min(values)))
            else:
                summary = "samples=0"
            msg = ("K2-PRTouch no-trigger pressure buffer: tri_chxs=%s "
                   "buf_len=%s %s (full window in klippy.log)"
                   % (meta.get('tri_chxs'), meta.get('buf_len'), summary))
            logging.info("%s\n  pressure=%s\n  ticks=%s", msg, values, ticks)
            self.obj.gcode.respond_info(msg)
        except Exception:
            logging.exception("K2-PRTouch: pressure buffer dump failed")

    def query_endstop(self, print_time):
        return self._mcu_endstop.query_endstop(print_time)

    def get_position_endstop(self):
        # The probe's Z offset relative to the nozzle tip. For an integrated
        # strain-gauge probe like prtouch this is effectively 0 — the nozzle
        # IS the probe. self_z_offset can fine-tune.
        return self.cfg.self_z_offset

    # ---- Creality-fork callbacks (homing.py / probe.py patches) ---------
    def get_suspended_det_status(self):
        """Called by Creality's patched homing.py:120 and probe.py:126.

        Returns True if the probe is in a "suspended detect" state where the
        host-side homing module should skip the standard trigger-check
        assertion. We don't implement this state machine yet — returning
        False matches the safe default (let homing.py do its normal check).
        """
        return False

    # ---- Helpers shared with macros / future M2 motion --------------------

    def ck_sys_sta(self):
        """Check that the sampler subsystem is healthy (mirrors prtouch.py:162)."""
        if self.sampler is None:
            return False
        # When pres.py grows is_shutdown / is_timeout flags, add them here.
        return True

    # ---- gcode commands (M1: minimal stubs) ------------------------------

    def cmd_PRTOUCH_TEST(self, gcmd):
        """K2-PRTouch test: read a single sample buffer and respond with stats.

        Minimal M1 surface — proves the wire layer works end-to-end. The
        equivalent of upstream prtouch.py:760.
        """
        gcmd.respond_info("K2-PRTouch M1: cmd_PRTOUCH_TEST stub")
        # TODO(M1): when sampler.read_baseline() is implemented, call it here
        # and report min/max/mean of the sample window so we have a smoke test
        # for the read path.

    def cmd_PRTOUCH_READY(self, gcmd):
        """Mark the bed/sensor as 'ready for probing'. Stub for now."""
        gcmd.respond_info("K2-PRTouch M1: cmd_PRTOUCH_READY stub")

    def cmd_K2_PRTOUCH_BASELINE(self, gcmd):
        """Read a single sample window from the pressure subsystem.

        No motion — pure wire-layer smoke test. Starts streaming, waits for
        a few sample periods, reads one buffer, stops streaming, reports
        the min/max/mean of the returned datas blob.

        Args (optional):
            DURATION_MS=<int>  — how long to stream before reading (default 200ms)
            CHANNEL=<int>      — which channel index (default 0)
        """
        if self.sampler is None:
            raise gcmd.error("K2-PRTouch: sampler not initialised — MCU identify hasn't fired yet")
        if not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: runtime wrappers not built yet (MCU config phase incomplete)")

        duration_ms = gcmd.get_int('DURATION_MS', 200, minval=10, maxval=5000)
        channel = gcmd.get_int('CHANNEL', 0, minval=0, maxval=3)

        gcmd.respond_info("K2-PRTouch BASELINE: starting %dms stream on ch%d..."
                          % (duration_ms, channel))
        reactor = self.obj.printer.get_reactor()
        try:
            self.sampler.start_streaming(
                min_hold=self.cfg.min_hold, max_hold=self.cfg.max_hold,
                add_hold=self.cfg.add_hold,
            )
        except Exception as e:
            raise gcmd.error("K2-PRTouch BASELINE: start_streaming failed: %s" % (e,))

        try:
            reactor.pause(reactor.monotonic() + duration_ms / 1000.0)
            try:
                response = self.sampler.read_buffer(channel, idx=0, length=self.cfg.pi_count)
            except Exception as e:
                raise gcmd.error("K2-PRTouch BASELINE: read_buffer failed: %s" % (e,))
        finally:
            try:
                self.sampler.stop_streaming()
            except Exception:
                logging.exception("K2-PRTouch BASELINE: stop_streaming failed")

        # Decode the response — `datas` should be a binary blob of samples.
        if not response:
            gcmd.respond_info("K2-PRTouch BASELINE: empty response")
            return
        datas = response.get('datas', b'') if isinstance(response, dict) else b''
        ticks = response.get('ticks', b'') if isinstance(response, dict) else b''

        # Report response metadata + raw hex + multiple decode attempts.
        import struct
        # Raw bytes (truncated for log readability)
        datas_hex = datas.hex()[:128]
        ticks_hex = ticks.hex()[:128]
        gcmd.respond_info(
            "K2-PRTouch BASELINE ch%d: response meta oid=%s tri_chxs=%s buf_len=%s "
            "idx=%s len=%s datas=%dB ticks=%dB"
            % (channel,
               response.get('oid'), response.get('tri_chxs'),
               response.get('buf_len'), response.get('idx'),
               response.get('len'),
               len(datas), len(ticks))
        )
        gcmd.respond_info("  datas hex: %s" % datas_hex)
        gcmd.respond_info("  ticks hex: %s" % ticks_hex)

        # DECODED via prtouch_open.zip (which bridges to Creality's
        # unzip_data shim while we finalise our pure-Python decoder).
        from . import zip as _zip
        try:
            data_count, data_vals = _zip.unzip_data(datas, signed=True)
            tick_count, tick_vals = _zip.unzip_data(ticks, signed=True)
            gcmd.respond_info(
                "  decoded datas: n=%d min=%d max=%d mean=%.0f p2p=%d"
                % (data_count, min(data_vals), max(data_vals),
                   sum(data_vals) / len(data_vals) if data_vals else 0,
                   max(data_vals) - min(data_vals) if data_vals else 0)
            )
            gcmd.respond_info("  datas vals: %s" % data_vals)
            if tick_vals and len(tick_vals) >= 2:
                tick_deltas = [tick_vals[i] - tick_vals[i - 1]
                               for i in range(1, len(tick_vals))]
                gcmd.respond_info(
                    "  decoded ticks: n=%d first=%d last=%d mean_delta=%.0f"
                    % (tick_count, tick_vals[0], tick_vals[-1],
                       sum(tick_deltas) / len(tick_deltas))
                )
                # Convert mean delta to seconds via MCU clock
                mcu_freq = self.obj.pres_mcu.seconds_to_clock(1.0)
                mean_dt_us = (sum(tick_deltas) / len(tick_deltas)) / mcu_freq * 1e6
                gcmd.respond_info("  sample period (mean): %.2f us" % mean_dt_us)
        except Exception as e:
            gcmd.respond_info("  decode error: %s" % e)
            gcmd.respond_info("  datas hex: %s" % datas.hex()[:80])
            gcmd.respond_info("  ticks hex: %s" % ticks.hex()[:80])

    def cmd_K2_PRTOUCH_TAP(self, gcmd):
        """Stream samples for N seconds, report extremes — manual tap test.

        Sampling proceeds in a poll loop: every 50ms we read a window of
        samples and accumulate. After DURATION_S seconds we report the
        overall min/max/p2p of all decoded values.

        With the nozzle untouched the p2p should be small (idle noise).
        Manually pressing the nozzle should produce a CLEAR p2p spike.

        Args (optional):
            DURATION_S=<float>  — seconds to sample (default 5.0)
            CHANNEL=<int>       — channel index (default 0)
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")

        duration_s = gcmd.get_float('DURATION_S', 5.0, minval=0.5, maxval=60.0)
        channel = gcmd.get_int('CHANNEL', 0, minval=0, maxval=3)
        gcmd.respond_info(
            "K2-PRTouch TAP: streaming %.1fs on ch%d — tap the nozzle now"
            % (duration_s, channel)
        )

        from . import zip as _zip
        reactor = self.obj.printer.get_reactor()
        self.sampler.start_streaming(
            min_hold=self.cfg.min_hold, max_hold=self.cfg.max_hold,
            add_hold=self.cfg.add_hold,
        )
        all_vals = []
        try:
            t_end = reactor.monotonic() + duration_s
            while reactor.monotonic() < t_end:
                try:
                    resp = self.sampler.read_buffer(channel, idx=0, length=self.cfg.pi_count)
                    datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                    _, vals = _zip.unzip_data(datas, signed=True)
                    all_vals.extend(vals)
                except Exception as e:
                    logging.exception("TAP read failed: %s", e)
                reactor.pause(reactor.monotonic() + 0.05)
        finally:
            try:
                self.sampler.stop_streaming()
            except Exception:
                pass

        if not all_vals:
            gcmd.respond_info("K2-PRTouch TAP: no samples received!")
            return

        vmin = min(all_vals)
        vmax = max(all_vals)
        vmean = sum(all_vals) / len(all_vals)
        p2p = vmax - vmin
        gcmd.respond_info(
            "K2-PRTouch TAP done: n=%d min=%d max=%d mean=%.0f p2p=%d"
            % (len(all_vals), vmin, vmax, vmean, p2p)
        )
        # Quartile snapshot for distribution context
        if len(all_vals) >= 10:
            srt = sorted(all_vals)
            q1 = srt[len(srt) // 4]
            q3 = srt[3 * len(srt) // 4]
            gcmd.respond_info(
                "  Q1=%d median=%d Q3=%d IQR=%d  (idle p2p typically <500; tap typically >5000)"
                % (q1, srt[len(srt) // 2], q3, q3 - q1)
            )

    def cmd_K2_PRTOUCH_TRIGGER_TEST(self, gcmd):
        """Run the full filter + check_trigger pipeline on live samples.

        Uses the start/stop-bracket-per-read pattern (the MCU's pres buffer
        stops updating after one full 64-sample fill, so re-starting between
        reads gives fresh data). Each iteration: start_streaming → 60ms
        wait → read 64 samples → stop_streaming → run filter + check_trigger.

        Args:
            N=<int>           cycles to run (default 60 = ~8 sec)
            DEBUG=1           respond_info every iteration (verbose)
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")

        n = gcmd.get_int('N', 60, minval=1, maxval=500)
        debug = gcmd.get_int('DEBUG', 0)
        gcmd.respond_info(
            "K2-PRTouch TRIGGER_TEST: %d cycles, start/stop bracket — tap the nozzle"
            % n
        )
        gcmd.respond_info(
            "  cfg: min_hold=%d max_hold=%d pi_count=%d hft_hz=%.1f lft_k1=%.2f"
            % (self.cfg.min_hold, self.cfg.max_hold, self.cfg.pi_count,
               self.cfg.hft_hz, self.cfg.lft_k1)
        )

        from . import zip as _zip
        reactor = self.obj.printer.get_reactor()
        trigger_count = 0
        trigger_reasons = []
        max_filtered = 0
        max_p2p = 0

        for i in range(n):
            self.sampler.start_streaming(
                min_hold=self.cfg.min_hold, max_hold=self.cfg.max_hold,
                add_hold=self.cfg.add_hold,
            )
            reactor.pause(reactor.monotonic() + 0.06)
            try:
                resp = self.sampler.proto.read.send([
                    self.pres_oid, 0, 0, 0, 64,
                ])
                datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                _, vals = _zip.unzip_data(datas, signed=True)
            finally:
                self.sampler.stop_streaming()

            if not vals:
                continue
            p2p = max(vals) - min(vals)
            if p2p > max_p2p:
                max_p2p = p2p

            # Run filter on whatever we have (≥3 needed for triplet filter)
            if len(vals) >= 3:
                # Pad to pi_count with the mean, so check_trigger sees a
                # window of the expected size. The end of the window
                # carries the live values; the front is filler.
                window_len = max(self.cfg.pi_count, len(vals))
                mean_v = sum(vals) // len(vals)
                padded = [mean_v] * (window_len - len(vals)) + list(vals)
                window = [padded]
                f = self.sampler.filter
                _, tmp_unfit = f.cal_offset_by_vals(1, window, f.lft_k1_oft, self.cfg.pi_count)
                _, tmp_fit = f.cal_filter_by_vals(1, window, f.hft_hz, f.lft_k1, self.cfg.pi_count)
                if tmp_fit[0]:
                    wmax = max(abs(v) for v in tmp_fit[0])
                    if wmax > max_filtered:
                        max_filtered = wmax
                try:
                    triggered, reason = self.sampler.check_trigger(
                        0, tmp_fit[0], tmp_unfit[0],
                        self.cfg.min_hold, self.cfg.max_hold,
                    )
                    if triggered:
                        trigger_count += 1
                        trigger_reasons.append(reason)
                        gcmd.respond_info(
                            "  *** TRIGGER iter=%d reason='%s' p2p=%d filt_max=%d ***"
                            % (i, reason, p2p, int(wmax))
                        )
                except Exception as e:
                    if debug:
                        gcmd.respond_info("  iter %d check_trigger err: %s" % (i, e))
                if debug and p2p > 5000:
                    gcmd.respond_info(
                        "  iter %d: p2p=%d filt_max=%d (no trigger)"
                        % (i, p2p, int(wmax))
                    )
            reactor.pause(reactor.monotonic() + 0.04)

        gcmd.respond_info(
            "K2-PRTouch TRIGGER_TEST done: triggers=%d max_filtered=%d max_p2p=%d"
            % (trigger_count, int(max_filtered), max_p2p)
        )
        if trigger_reasons:
            from collections import Counter
            for reason, n_ in Counter(trigger_reasons).most_common():
                gcmd.respond_info("  reason '%s': %d times" % (reason, n_))

        gcmd.respond_info(
            "K2-PRTouch TRIGGER_TEST done: triggers=%d max_filtered_abs=%d"
            % (trigger_count, int(max_filtered))
        )
        if trigger_reasons:
            from collections import Counter
            c = Counter(trigger_reasons)
            for reason, n in c.most_common():
                gcmd.respond_info("  reason '%s': %d times" % (reason, n))
        gcmd.respond_info(
            "  (max_hold threshold=%d. Trigger requires filtered amplitude >= max_hold "
            "for 3 consecutive samples.)" % self.cfg.max_hold
        )

    def cmd_K2_PRTOUCH_TAP_LIVE(self, gcmd):
        """Tap test with start/stop bracket per read — defeats buffer staleness.

        Probe loop:
          for N iterations:
            start_streaming
            wait acq_window_ms
            read buf @ idx=0 len=64
            stop_streaming
            scan for max p2p
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")
        iters = gcmd.get_int('N', 30, minval=1, maxval=200)
        from . import zip as _zip
        reactor = self.obj.printer.get_reactor()
        gcmd.respond_info(
            "K2-PRTouch TAP_LIVE: %d cycles, start/stop bracket — tap nozzle now"
            % iters
        )
        best_p2p = 0
        best_iter = -1
        for i in range(iters):
            self.sampler.start_streaming(
                min_hold=self.cfg.min_hold, max_hold=self.cfg.max_hold,
                add_hold=self.cfg.add_hold,
            )
            reactor.pause(reactor.monotonic() + 0.06)  # let 64 samples fill
            try:
                resp = self.sampler.proto.read.send([
                    self.pres_oid, 0, 0, 0, 64,
                ])
                datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                _, vals = _zip.unzip_data(datas, signed=True)
            finally:
                self.sampler.stop_streaming()
            if vals:
                p2p = max(vals) - min(vals)
                if p2p > best_p2p:
                    best_p2p = p2p
                    best_iter = i
                    gcmd.respond_info(
                        "  iter %d: n=%d p2p=%d min=%d max=%d  (mean=%d)"
                        % (i, len(vals), p2p, min(vals), max(vals),
                           sum(vals) // len(vals))
                    )
            reactor.pause(reactor.monotonic() + 0.05)
        gcmd.respond_info(
            "K2-PRTouch TAP_LIVE done. Best p2p=%d at iter %d (idle baseline ~400-700)"
            % (best_p2p, best_iter)
        )

    def cmd_K2_PROBE_ONCE(self, gcmd):
        """Open-source Z probing motion. Drives Z down in small chunks,
        runs the trigger cascade between chunks, stops on first trigger.

        Args:
            SPEED=<float>    descent speed in mm/s (default 2.0, max 10.0)
            MAX_DIST=<float> maximum descent distance in mm (default 5.0)
            CHUNK=<float>    per-chunk Z step in mm (default 0.5)
            FORCE=<0|1>      if 1, set_kinematic_position to treat current Z
                             as homed (use only when you know current Z is safe)

        Behaviour:
          1. Streams pressure samples (start/stop-bracket pattern)
          2. After each chunk's motion completes, polls the trigger cascade
          3. On trigger: reports the contact Z position
          4. After trigger or full descent: retracts to start Z
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")

        speed = gcmd.get_float('SPEED', 2.0, minval=0.1, maxval=10.0)
        max_dist = gcmd.get_float('MAX_DIST', 5.0, minval=0.1, maxval=50.0)
        chunk_size = gcmd.get_float('CHUNK', 0.5, minval=0.05, maxval=2.0)
        force_homed = gcmd.get_int('FORCE', 0, minval=0, maxval=1)

        toolhead = self.obj.toolhead
        start_pos = toolhead.get_position()
        start_z = start_pos[2]

        # Verify Z is homed (or force it)
        homed_axes = toolhead.get_status(self.obj.printer.get_reactor().monotonic()).get(
            'homed_axes', ''
        )
        if 'z' not in homed_axes:
            if not force_homed:
                raise gcmd.error(
                    "K2-PRTouch: Z not homed. Use FORCE=1 to override "
                    "(only if you KNOW the current Z position is correct)"
                )
            # Treat the current toolhead position as homed Z
            toolhead.set_position(start_pos, homing_axes=[2])
            gcmd.respond_info(
                "K2-PRTouch PROBE: FORCE=1 — claiming Z=%.3f as homed" % start_z
            )

        # Safety: cap descent at axis_minimum + a margin
        kin = self.obj.kin
        axis_min = self.obj.toolhead.kin.axes_min if hasattr(self.obj.toolhead.kin, 'axes_min') else None
        z_floor = max(0.5, start_z - max_dist)  # don't go below 0.5mm above bed
        gcmd.respond_info(
            "K2-PRTouch PROBE: start_z=%.3f max_dist=%.2fmm chunk=%.2fmm "
            "speed=%.2fmm/s z_floor=%.3f"
            % (start_z, max_dist, chunk_size, speed, z_floor)
        )

        from . import zip as _zip
        reactor = self.obj.printer.get_reactor()
        triggered = False
        trigger_z = None
        trigger_reason = None
        last_p2p = 0

        try:
            n_chunks = int(max_dist / chunk_size)
            for i in range(n_chunks):
                # Sample BEFORE moving (catches contact at this Z).
                # SAMPLE_MS controls the sampling window — longer windows
                # give more chance to catch a tap that lasts ~100ms.
                sample_ms = gcmd.get_int('SAMPLE_MS', 200, minval=50, maxval=1000)
                self.sampler.start_streaming(
                    min_hold=self.cfg.min_hold, max_hold=self.cfg.max_hold,
                    add_hold=self.cfg.add_hold,
                )
                reactor.pause(reactor.monotonic() + sample_ms / 1000.0)
                try:
                    resp = self.sampler.proto.read.send([
                        self.pres_oid, 0, 0, 0, 64,
                    ])
                    datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                    _, vals = _zip.unzip_data(datas, signed=True)
                finally:
                    self.sampler.stop_streaming()

                if vals:
                    last_p2p = max(vals) - min(vals)
                    if len(vals) >= 3:
                        window_len = max(self.cfg.pi_count, len(vals))
                        mean_v = sum(vals) // len(vals)
                        padded = [mean_v] * (window_len - len(vals)) + list(vals)
                        window = [padded]
                        f = self.sampler.filter
                        _, tmp_unfit = f.cal_offset_by_vals(
                            1, window, f.lft_k1_oft, self.cfg.pi_count,
                        )
                        _, tmp_fit = f.cal_filter_by_vals(
                            1, window, f.hft_hz, f.lft_k1, self.cfg.pi_count,
                        )
                        try:
                            fired, reason = self.sampler.check_trigger(
                                0, tmp_fit[0], tmp_unfit[0],
                                self.cfg.min_hold, self.cfg.max_hold,
                            )
                            if fired:
                                cur = toolhead.get_position()
                                trigger_z = cur[2]
                                trigger_reason = reason
                                triggered = True
                                gcmd.respond_info(
                                    "*** PROBE TRIGGER chunk=%d Z=%.3f reason='%s' p2p=%d ***"
                                    % (i, trigger_z, reason, last_p2p)
                                )
                                break
                        except Exception as e:
                            logging.debug("PROBE check_trigger err: %s", e)

                # Descend by chunk_size
                target_z = start_z - (i + 1) * chunk_size
                if target_z < z_floor:
                    gcmd.respond_info(
                        "PROBE: z_floor reached at chunk %d, target=%.3f" % (i, target_z)
                    )
                    break
                # toolhead.manual_move blocks until move is queued; wait_moves
                # ensures it's actually completed before we sample again.
                toolhead.manual_move(
                    [start_pos[0], start_pos[1], target_z], speed,
                )
                toolhead.wait_moves()

        finally:
            # Always retract to start position
            try:
                toolhead.manual_move(
                    [start_pos[0], start_pos[1], start_z], max(speed * 2, 5.0),
                )
                toolhead.wait_moves()
            except Exception:
                logging.exception("PROBE retract failed")

        if triggered:
            gcmd.respond_info(
                "K2-PRTouch PROBE done: TRIGGERED z=%.3f reason=%s (last p2p=%d)"
                % (trigger_z, trigger_reason, last_p2p)
            )
        else:
            gcmd.respond_info(
                "K2-PRTouch PROBE done: no trigger after %.2fmm descent (last p2p=%d)"
                % (max_dist, last_p2p)
            )

    def cmd_K2_PRTOUCH_PROBE_BUF(self, gcmd):
        """Diagnostic: probe the MCU's sample buffer at various idx/is_src.

        Streams for a brief period. Then issues SEVERAL read_prtouch_pres
        calls with varying parameters to figure out where the live samples
        are in the MCU's ring buffer.

        Args:
            IS_SRC=<0|1>  — 0=MCU-filtered (ftr_data), 1=raw (buf_data). Default 0.
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")
        is_src = gcmd.get_int('IS_SRC', 0, minval=0, maxval=1)

        from . import zip as _zip
        reactor = self.obj.printer.get_reactor()
        self.sampler.start_streaming(
            min_hold=self.cfg.min_hold, max_hold=self.cfg.max_hold,
            add_hold=self.cfg.add_hold,
        )
        # Let the buffer fill
        reactor.pause(reactor.monotonic() + 0.5)

        gcmd.respond_info("K2-PRTouch PROBE_BUF: is_src=%d" % is_src)
        try:
            # Probe idx 0..63 (full ring) with length=8
            for idx in [0, 8, 16, 24, 32, 40, 48, 56]:
                try:
                    resp = self.sampler.proto.read.send([
                        self.pres_oid, is_src, 0, idx, 8,
                    ])
                    datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                    _, vals = _zip.unzip_data(datas, signed=True)
                    if vals:
                        gcmd.respond_info(
                            "  idx=%d resp.idx=%s resp.len=%s vals=%s"
                            % (idx, resp.get('idx'), resp.get('len'), vals)
                        )
                except Exception as e:
                    gcmd.respond_info("  idx=%d ERR %s" % (idx, e))

            # Then do 3 back-to-back idx=0 reads to see if buffer advances
            gcmd.respond_info("=== three back-to-back idx=0 reads ===")
            for i in range(3):
                try:
                    resp = self.sampler.proto.read.send([
                        self.pres_oid, is_src, 0, 0, 8,
                    ])
                    datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                    _, vals = _zip.unzip_data(datas, signed=True)
                    gcmd.respond_info(
                        "  pass %d: resp.idx=%s vals=%s"
                        % (i, resp.get('idx'), vals)
                    )
                    reactor.pause(reactor.monotonic() + 0.1)
                except Exception as e:
                    gcmd.respond_info("  pass %d ERR %s" % (i, e))
        finally:
            try:
                self.sampler.stop_streaming()
            except Exception:
                pass

    def cmd_K2_PROBE_LISTEN(self, gcmd):
        """Diagnostic: does the MCU push resault_prtouch_pres unsolicited?

        Pure passive listener. Starts streaming, waits N ms WITHOUT
        issuing any read_prtouch_pres, then stops and reports how many
        async pushes our `_handle_async_pres` caught. This is the
        decisive test for the M2 architecture:

          count > 0  → MCU pushes async; build async-handler probe
          count == 0 → MCU is sync-only; need reactor-timer polling

        Args:
            DURATION_MS=<int> — listen window in ms (default 1000, max 5000)
            CFG_REGS=<int>    — start_prtouch_pres cfg_regs byte (default 60)

        Pass CFG_REGS=different values to probe whether a config-register
        bit gates the push behaviour.
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")
        duration_ms = gcmd.get_int('DURATION_MS', 1000, minval=100, maxval=5000)
        cfg_regs = gcmd.get_int('CFG_REGS', 60, minval=0, maxval=255)

        reactor = self.obj.printer.get_reactor()
        st = self._async_pres

        # Snapshot the counter BEFORE we start streaming so we measure
        # only the pushes captured during the listen window.
        st['enabled'] = True
        count_before = st['count']
        tri_before = st['tri_events']

        gcmd.respond_info(
            "K2_PROBE_LISTEN: cfg_regs=%d, listening %dms with NO read_prtouch_pres..."
            % (cfg_regs, duration_ms)
        )

        try:
            self.sampler.start_streaming(
                cfg_regs=cfg_regs,
                min_hold=self.cfg.min_hold,
                max_hold=self.cfg.max_hold,
                add_hold=self.cfg.add_hold,
            )
            reactor.pause(reactor.monotonic() + duration_ms / 1000.0)
        finally:
            try:
                self.sampler.stop_streaming()
            except Exception:
                logging.exception("K2_PROBE_LISTEN: stop_streaming failed")

        delta_count = st['count'] - count_before
        delta_tri = st['tri_events'] - tri_before
        rate_hz = delta_count * 1000.0 / duration_ms

        if delta_count == 0:
            verdict = "MCU is SYNC-ONLY — need reactor-timer polling for M2"
        else:
            verdict = ("MCU PUSHES ASYNC — proceed with async-handler probe "
                       "(rate ~%.1f pushes/s)" % rate_hz)

        gcmd.respond_info(
            "K2_PROBE_LISTEN result:\n"
            "  async pushes during window: %d (%.1fHz)\n"
            "  pushes with tri_chxs!=0:   %d\n"
            "  last buf_len=%s idx=%s len=%s tri_chxs=%s datas_bytes=%d\n"
            "  verdict: %s"
            % (delta_count, rate_hz, delta_tri,
               st['last_buf_len'], st['last_idx'], st['last_len'],
               st['last_tri_chxs'], st['last_datas_len'],
               verdict)
        )

    def cmd_K2_PROBE_DURING_MOTION(self, gcmd):
        """M2 — sample+motion coordination via reactor-timer polling.

        Issues a single non-blocking toolhead.manual_move() Z descent.
        In parallel, a reactor timer fires every PERIOD_MS to:
            stop_streaming -> read_prtouch_pres -> start_streaming
            decode samples, check MCU tri_chxs and host check_trigger cascade
        On trigger: capture Z position immediately (motion may continue
        until MAX_DIST is reached — see SAFETY below).

        SAFETY: we do NOT abort the toolhead move on trigger because
        Klipper's lookahead queue can't be cleanly cancelled without
        TRSYNC. Set MAX_DIST conservatively (default 1.5mm) so worst-case
        overshoot doesn't crash the nozzle. Run from a known Z where the
        bed is at most MAX_DIST below.

        Args:
            SPEED=<float>      descent mm/s (default 1.0, max 5.0)
            MAX_DIST=<float>   max descent in mm (default 1.5, max 10.0)
            PERIOD_MS=<int>    sampler restart cadence (default 50, min 30)
            FORCE=<0|1>        force-claim current Z as homed
            DRY=<0|1>          if 1, skip the actual move (sample only)
        """
        if self.sampler is None or not self.sampler.proto._configured:
            raise gcmd.error("K2-PRTouch: sampler not ready")

        speed = gcmd.get_float('SPEED', 1.0, minval=0.05, maxval=5.0)
        max_dist = gcmd.get_float('MAX_DIST', 1.5, minval=0.1, maxval=10.0)
        period_ms = gcmd.get_int('PERIOD_MS', 50, minval=30, maxval=500)
        force_homed = gcmd.get_int('FORCE', 0, minval=0, maxval=1)
        dry = gcmd.get_int('DRY', 0, minval=0, maxval=1)

        toolhead = self.obj.toolhead
        printer = self.obj.printer
        reactor = printer.get_reactor()

        start_pos = toolhead.get_position()
        start_z = start_pos[2]

        homed_axes = toolhead.get_status(reactor.monotonic()).get('homed_axes', '')
        if 'z' not in homed_axes:
            if not force_homed:
                raise gcmd.error(
                    "K2-PRTouch: Z not homed. Use FORCE=1 to override "
                    "(only if you know the current Z position is correct)"
                )
            toolhead.set_position(start_pos, homing_axes=[2])
            gcmd.respond_info(
                "K2-PRTouch DURING_MOTION: FORCE=1 — claiming Z=%.3f as homed" % start_z
            )

        target_z = max(0.2, start_z - max_dist)
        actual_dist = start_z - target_z

        from . import zip as _zip
        f = self.sampler.filter
        cfg = self.cfg

        state = {
            'triggered': False,
            'trigger_z': None,
            'trigger_reason': None,
            'sample_count': 0,
            'poll_count': 0,
            'max_p2p': 0,
            'max_filt': 0,
            'last_tri_chxs': 0,
            'mcu_trigger_count': 0,
            'streaming': False,
        }

        gcmd.respond_info(
            "K2_PROBE_DURING_MOTION: start_z=%.3f target_z=%.3f dist=%.3fmm "
            "speed=%.2fmm/s period=%dms dry=%d"
            % (start_z, target_z, actual_dist, speed, period_ms, dry)
        )

        def sampler_cb(eventtime):
            """Stop+read+start each tick. Run filter + check_trigger."""
            if state['triggered']:
                return reactor.NEVER
            try:
                if state['streaming']:
                    self.sampler.stop_streaming()
                    state['streaming'] = False
                    try:
                        resp = self.sampler.proto.read.send([
                            self.pres_oid, 0, 0, 0, 64,
                        ])
                    except Exception:
                        logging.exception("DURING_MOTION read failed")
                        resp = {}
                    tri = resp.get('tri_chxs', 0) if isinstance(resp, dict) else 0
                    state['last_tri_chxs'] = tri
                    datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                    try:
                        _, vals = _zip.unzip_data(datas, signed=True)
                    except Exception:
                        vals = []

                    state['poll_count'] += 1
                    if vals:
                        state['sample_count'] += len(vals)
                        p2p = max(vals) - min(vals)
                        if p2p > state['max_p2p']:
                            state['max_p2p'] = p2p

                    # Live K2 validation shows tri_chxs=1 at idle: it is a
                    # populated-channel mask, not a contact indication.
                    # Contact is decided exclusively by the host cascade.
                    if vals and len(vals) >= 3:
                        window_len = max(cfg.pi_count, len(vals))
                        mean_v = sum(vals) // len(vals)
                        padded = [mean_v] * (window_len - len(vals)) + list(vals)
                        window = [padded]
                        _, tmp_unfit = f.cal_offset_by_vals(
                            1, window, f.lft_k1_oft, cfg.pi_count,
                        )
                        _, tmp_fit = f.cal_filter_by_vals(
                            1, window, f.hft_hz, f.lft_k1, cfg.pi_count,
                        )
                        filt_p2p = max(tmp_fit[0]) - min(tmp_fit[0]) if tmp_fit and tmp_fit[0] else 0
                        if filt_p2p > state['max_filt']:
                            state['max_filt'] = filt_p2p
                        try:
                            fired, reason = self.sampler.check_trigger(
                                0, tmp_fit[0], tmp_unfit[0],
                                cfg.min_hold, cfg.max_hold,
                            )
                        except Exception:
                            fired, reason = False, None
                        if fired:
                            state['triggered'] = True
                            state['trigger_reason'] = 'host:%s' % reason
                            state['trigger_z'] = toolhead.get_position()[2]
                            return reactor.NEVER

                # Restart streaming for the next period (64 samples ~ 50ms at K2 rate)
                self.sampler.start_streaming(
                    min_hold=cfg.min_hold, max_hold=cfg.max_hold,
                    add_hold=cfg.add_hold,
                )
                state['streaming'] = True
            except Exception:
                logging.exception("DURING_MOTION sampler_cb")

            return eventtime + period_ms / 1000.0

        # Kick the timer off at next-loop, so the first iteration just starts streaming.
        timer = reactor.register_timer(
            sampler_cb, reactor.monotonic() + 0.002,
        )

        move_failed = None
        try:
            if not dry:
                # Queue the non-blocking move. manual_move adds to lookahead and
                # returns once queued — actual stepping happens via the toolhead
                # timestep loop. wait_moves() would block synchronously, so we
                # instead poll with reactor.pause to let the sampler timer fire.
                toolhead.manual_move(
                    [start_pos[0], start_pos[1], target_z], speed,
                )
                expected_dur = actual_dist / speed
                deadline = reactor.monotonic() + expected_dur + 1.0
                while reactor.monotonic() < deadline:
                    if state['triggered']:
                        break
                    # Poll the toolhead's last queued position vs current.
                    # When wait_moves would return (queue empty), we're done.
                    reactor.pause(reactor.monotonic() + 0.01)
                    cur_z = toolhead.get_position()[2]
                    if abs(cur_z - target_z) < 0.001:
                        # Reached target without trigger
                        break
            else:
                # Dry: just sample for max_dist/speed seconds, no motion.
                sample_dur = actual_dist / speed
                deadline = reactor.monotonic() + sample_dur
                while reactor.monotonic() < deadline:
                    if state['triggered']:
                        break
                    reactor.pause(reactor.monotonic() + 0.01)
        except Exception as e:
            move_failed = str(e)
            logging.exception("DURING_MOTION move failed")
        finally:
            # Stop the sampler timer
            try:
                reactor.unregister_timer(timer)
            except Exception:
                pass
            # Ensure streaming is off
            try:
                if state['streaming']:
                    self.sampler.stop_streaming()
                    state['streaming'] = False
            except Exception:
                logging.exception("DURING_MOTION cleanup stop_streaming")
            # Retract to start_z (only if we actually moved)
            if not dry:
                try:
                    toolhead.manual_move(
                        [start_pos[0], start_pos[1], start_z],
                        max(speed * 3, 5.0),
                    )
                    toolhead.wait_moves()
                except Exception:
                    logging.exception("DURING_MOTION retract failed")

        if move_failed:
            raise gcmd.error("K2_PROBE_DURING_MOTION: move failed: %s" % move_failed)

        if state['triggered']:
            gcmd.respond_info(
                "K2_PROBE_DURING_MOTION: TRIGGERED at Z=%.3f reason='%s'\n"
                "  polls=%d samples=%d max_p2p=%d max_filt=%d "
                "mcu_trigger_count=%d last_tri_chxs=%d"
                % (state['trigger_z'], state['trigger_reason'],
                   state['poll_count'], state['sample_count'],
                   state['max_p2p'], state['max_filt'],
                   state['mcu_trigger_count'], state['last_tri_chxs'])
            )
        else:
            gcmd.respond_info(
                "K2_PROBE_DURING_MOTION: NO TRIGGER over %.3fmm descent\n"
                "  polls=%d samples=%d max_p2p=%d max_filt=%d "
                "(idle baseline p2p ~400-700; firm tap >1e6)"
                % (actual_dist,
                   state['poll_count'], state['sample_count'],
                   state['max_p2p'], state['max_filt'])
            )

    # ---- M3: Klipper-native probing move (drip_move, no TRSYNC) -----------

    def run_host_probe(self, target_z, speed, period_ms=None,
                       arm_below_z=None, contact_threshold=None,
                       contact_polls=None, max_window_p2p=None,
                       contact_mode=None):
        """Host-driven probing move for the standard probe.PrinterProbe path.

        Descends the toolhead from its current position toward ``target_z``
        as a ``toolhead.drip_move``, while a reactor timer samples the strain
        gauge every ``period_ms``. The 80ms default lets the K2's 64-sample
        buffer fill at its measured ~804us cadence (~51.5ms per window).
        Live K2 testing showed that reading at 60ms can expose a partially
        encoded buffer as a repeatable ~780k-count false contact, so the
        shorter interval must not be used for homing.
        The instant our host-side check_trigger
        cascade detects bed contact we complete the
        drip_completion, which raises DripModeEndSignal inside the toolhead's
        drip loop and halts the move within one DRIP_SEGMENT_TIME — bounded
        overshoot, no TRSYNC required. (The K2 prtouch MCU is sync-only and
        exposes no reliable hardware trigger, so the contact decision MUST be
        host-side; see project_k2_prtouch_re notes.)

        Returns the Z height at contact. Leaves the toolhead AT that height —
        the caller (PrinterProbe.run_probe) does the sample_retract between
        samples, exactly as with the stock probing_move path.

        ``arm_below_z`` optionally rejects triggers above an already measured
        contact window. The K2 three-tap home uses it to prevent a retract or
        motion impulse from becoming a later tap's result.

        ``contact_threshold`` optionally selects a caller-specific qualified
        load threshold. Mainline bed mesh uses a lighter threshold after Z
        home has established and guarded the expected contact plane.

        ``contact_polls`` optionally selects how many consecutive qualified
        windows are required. Dedicated Z homing retains the conservative
        configured default. A guarded bed-mesh caller may stop on the first
        clean contact pulse, matching the K2's physical tap behaviour.

        ``max_window_p2p`` optionally rejects an implausibly large pressure
        window before it can contribute to contact qualification. The K2 can
        occasionally return a partially encoded buffer with a peak-to-peak
        value hundreds of times larger than a physical nozzle tap.

        ``contact_mode='stock'`` disables the generic filtered-amplitude
        shortcut and accepts contact only from Creality's original nine-rule
        PRTouch cascade. This is the correct mode for bed-mesh probing: normal
        Z-motion vibration can exceed a small absolute threshold even when the
        nozzle has not touched the bed.

        Raises command_error with the stock "Timeout during endstop homing"
        text if the descent reaches ``target_z`` without a trigger, so
        PrinterProbe's samples-tolerance/retry logic behaves normally.
        """
        if (self.sampler is None or self.sampler.proto is None
                or not self.sampler.proto._configured):
            raise self.obj.printer.command_error("K2-PRTouch: sampler not ready")

        toolhead = self.obj.toolhead
        printer = self.obj.printer
        reactor = printer.get_reactor()
        if period_ms is None:
            period_ms = self.cfg.host_probe_period_ms
        if contact_threshold is None:
            contact_threshold = self.cfg.host_probe_contact_threshold
        if contact_polls is None:
            contact_polls = self.cfg.host_probe_contact_polls
        if contact_mode is None:
            contact_mode = 'amplitude'
        if contact_mode not in ('amplitude', 'stock'):
            raise printer.command_error(
                "K2-PRTouch: unknown host contact mode '%s'"
                % (contact_mode,))
        contact_release_threshold = max(
            1, int(round(
                contact_threshold
                * self.cfg.host_probe_contact_release_ratio)))

        start_pos = toolhead.get_position()
        min_target_z = start_pos[2] - self.cfg.max_probe_descent
        if target_z < min_target_z:
            logging.info(
                "run_host_probe: clamping target_z %.4f to %.4f "
                "(max_probe_descent=%.3f)",
                target_z, min_target_z, self.cfg.max_probe_descent)
            target_z = min_target_z
        curtime = reactor.monotonic()
        if 'z' not in toolhead.get_status(curtime).get('homed_axes', ''):
            raise printer.command_error(
                '{"code":"key96", "msg": "Must home before probe", "values": []}')

        newpos = list(start_pos)
        newpos[2] = target_z
        completion = reactor.completion()

        from . import zip as _zip
        f = self.sampler.filter
        cfg = self.cfg
        state = {
            'trigger_z': None, 'trigger_reason': None,
            'streaming': False, 'poll_count': 0, 'error': None,
            'max_p2p': 0, 'max_filtered_abs': 0, 'contact_hold': 0,
        }
        warmup_polls = self.cfg.host_probe_warmup_polls

        def get_live_z(eventtime):
            """Return requested Z at eventtime, not the final commanded Z."""
            motion_report = printer.lookup_object("motion_report")
            dtrapq = motion_report.dtrapqs.get("toolhead")
            if dtrapq is None:
                raise printer.command_error(
                    "K2-PRTouch: live toolhead trap queue is unavailable")
            main_mcu = printer.lookup_object("mcu")
            print_time = main_mcu.estimated_print_time(eventtime)
            pos, unused_velocity = dtrapq.get_trapq_position(print_time)
            if pos is None:
                raise printer.command_error(
                    "K2-PRTouch: could not resolve live toolhead position")
            return pos[2]

        def sampler_cb(eventtime):
            if completion.test():
                return reactor.NEVER
            try:
                if state['streaming']:
                    try:
                        resp = self.sampler.proto.read.send(
                            [self.pres_oid, 0, 0, 0, 64])
                    except Exception:
                        logging.exception("run_host_probe read failed")
                        resp = {}
                    finally:
                        self.sampler.stop_streaming()
                        state['streaming'] = False
                    datas = resp.get('datas', b'') if isinstance(resp, dict) else b''
                    try:
                        _, vals = _zip.unzip_data(datas, signed=True)
                    except Exception:
                        vals = []
                    state['poll_count'] += 1
                    p2p = max(vals) - min(vals) if vals else 0
                    state['max_p2p'] = max(state['max_p2p'], p2p)

                    fired, reason = False, None
                    filtered_abs = 0
                    # The first stream windows contain the ADC start transient
                    # and Z acceleration vibration. Live K2 validation showed
                    # an immediate false out_max_hold on poll one. Discard
                    # 200ms before arming contact detection; at the configured
                    # 5mm/s probe speed this is at most 1mm of travel.
                    if (state['poll_count'] > warmup_polls
                            and vals and len(vals) >= 3):
                        window_len = max(cfg.pi_count, len(vals))
                        mean_v = sum(vals) // len(vals)
                        padded = [mean_v] * (window_len - len(vals)) + list(vals)
                        window = [padded]
                        _, tmp_unfit = f.cal_offset_by_vals(
                            1, window, f.lft_k1_oft, cfg.pi_count)
                        _, tmp_fit = f.cal_filter_by_vals(
                            1, window, f.hft_hz, f.lft_k1, cfg.pi_count)
                        if tmp_fit and tmp_fit[0]:
                            filtered_abs = max(abs(v) for v in tmp_fit[0])
                            state['max_filtered_abs'] = max(
                                state['max_filtered_abs'], filtered_abs)
                        try:
                            legacy_fired, legacy_reason = (
                                self.sampler.check_trigger(
                                    0, tmp_fit[0], tmp_unfit[0],
                                    cfg.min_hold, cfg.max_hold))
                        except Exception:
                            legacy_fired, legacy_reason = False, None
                        corrupt_window = (
                            max_window_p2p is not None
                            and p2p > max_window_p2p
                        )
                        fired, reason = False, None
                        if corrupt_window:
                            state['contact_hold'] = 0
                        elif contact_mode == 'stock':
                            # Creality's full cascade checks shape, trend, and
                            # absolute load. Do not combine it with the generic
                            # amplitude counter: ordinary descent vibration
                            # repeatedly crosses that threshold without bed
                            # contact. Corrupt encoded windows remain rejected.
                            state['contact_hold'] = 0
                            if legacy_fired:
                                fired = True
                                reason = 'stock:%s' % (
                                    legacy_reason or 'cascade')
                        else:
                            state['contact_hold'] = (
                                _contact_hysteresis_hold(
                                    filtered_abs,
                                    contact_threshold,
                                    contact_release_threshold,
                                    state['contact_hold']))
                            # The legacy cascade can fire from a single encoded
                            # window. Live K2 testing found isolated ~812k
                            # startup artifacts that satisfy out_max_hold but
                            # are not physical contact. Generic host homing
                            # therefore requires sustained load across multiple
                            # fresh MCU windows.
                            if state['contact_hold'] >= contact_polls:
                                fired = True
                                reason = (
                                    'host:qualified_contact'
                                    if contact_polls == 1
                                    else 'host:sustained_contact'
                                )
                                if legacy_fired:
                                    reason += ':%s' % legacy_reason

                    logging.info(
                        "run_host_probe poll=%d p2p=%d filtered_abs=%d "
                        "mode=%s entry_threshold=%d release_threshold=%d "
                        "contact_hold=%d/%d corrupt=%d armed=%d fired=%d "
                        "reason=%s",
                        state['poll_count'], p2p, filtered_abs,
                        contact_mode, contact_threshold,
                        contact_release_threshold,
                        state['contact_hold'], contact_polls,
                        bool(max_window_p2p is not None
                             and p2p > max_window_p2p),
                        state['poll_count'] > warmup_polls, fired, reason)

                    if fired:
                        candidate_z = get_live_z(eventtime)
                        if (arm_below_z is not None
                                and candidate_z > arm_below_z):
                            logging.warning(
                                "run_host_probe: rejected early trigger "
                                "z=%.4f above arm_below_z=%.4f reason=%s",
                                candidate_z, arm_below_z, reason)
                            state['contact_hold'] = 0
                        else:
                            state['trigger_z'] = candidate_z
                            state['trigger_reason'] = reason
                            completion.complete(1)
                            return reactor.NEVER

                self.sampler.start_streaming(
                    min_hold=cfg.min_hold, max_hold=cfg.max_hold,
                    add_hold=cfg.add_hold)
                state['streaming'] = True
            except Exception as exc:
                logging.exception("run_host_probe sampler_cb")
                # Fail closed: stop the drip move immediately instead of
                # continuing toward position_min without pressure feedback.
                state['error'] = str(exc)
                completion.complete(1)
                return reactor.NEVER
            return eventtime + period_ms / 1000.0

        timer = reactor.register_timer(sampler_cb, reactor.monotonic() + 0.002)
        try:
            # drip_move pumps the reactor while it waits, so sampler_cb fires
            # concurrently; DripModeEndSignal (raised when completion.test())
            # halts the move cleanly at the contact point.
            toolhead.drip_move(newpos, speed, completion)
        finally:
            try:
                reactor.unregister_timer(timer)
            except Exception:
                pass
            if state['streaming']:
                try:
                    self.sampler.stop_streaming()
                except Exception:
                    logging.exception("run_host_probe cleanup stop_streaming")
                state['streaming'] = False

        if state['trigger_z'] is not None:
            # drip_move leaves toolhead.commanded_pos at its destination even
            # when completion aborts early. The steppers' itersolve positions,
            # however, stop at the last steps actually generated. Rebase to
            # that halted position so a fixed-coordinate retract returns to
            # the same physical height on every tap. Keep trigger_z separately
            # as the earlier first-contact measurement.
            kin = toolhead.get_kinematics()
            stepper_positions = {
                stepper.get_name(): stepper.get_commanded_position()
                for stepper in kin.get_steppers()
            }
            halted_z = kin.calc_position(stepper_positions)[2]
            halted_pos = toolhead.get_position()
            halted_pos[2] = halted_z
            toolhead.set_position(halted_pos)
            logging.info(
                "run_host_probe: halt z=%.4f trigger_to_halt=%.4f",
                halted_z, state['trigger_z'] - halted_z)

        if state['error'] is not None or state['trigger_z'] is None:
            # A command error aborts the surrounding gcode macro immediately,
            # so its normal post-probe lift will not run. Always unload the
            # nozzle here before reporting a missed trigger or sampler fault.
            recovery_pos = toolhead.get_position()
            recovery_pos[2] = min(
                start_pos[2],
                recovery_pos[2] + self.cfg.host_probe_failure_retract)
            if recovery_pos[2] > toolhead.get_position()[2]:
                toolhead.manual_move(recovery_pos, max(speed, 1.0))
                toolhead.wait_moves()
                logging.warning(
                    "run_host_probe: failure retract to z=%.4f",
                    recovery_pos[2])

        if state['error'] is not None:
            raise printer.command_error(
                "K2-PRTouch sampling failed during probe: %s"
                % (state['error'],))

        if state['trigger_z'] is None:
            raise printer.command_error(
                "Timeout during endstop homing: no bed contact over %.3fmm "
                "descent (polls=%d). Reduce start Z or check the strain gauge."
                % (start_pos[2] - target_z, state['poll_count']))

        logging.info(
            "run_host_probe: contact z=%.4f reason=%s polls=%d warmup=%d "
            "max_p2p=%d max_filtered_abs=%d",
            state['trigger_z'], state['trigger_reason'], state['poll_count'],
            warmup_polls, state['max_p2p'], state['max_filtered_abs'])
        return state['trigger_z']

    def _install_probe_override(self):
        """On klippy:ready, replace probe.PrinterProbe._probe with a host-driven
        implementation so PROBE / PROBE_ACCURACY / BED_MESH_CALIBRATE /
        screws_tilt_adjust all route through run_host_probe() instead of
        phoming.probing_move() (which needs TRSYNC we can't provide).

        Additive + reversible: keeps a reference to the original bound method
        and only patches once. No printer.cfg section required.
        """
        printer = self.obj.printer
        probe = printer.lookup_object('probe', None)
        if probe is None:
            logging.warning(
                "K2-PRTouch: no 'probe' object at ready; _probe not patched")
            return
        if not hasattr(probe, '_probe'):
            logging.info(
                "K2-PRTouch: modern upstream probe-session adapter active; "
                "legacy _probe monkey patch not required")
            return
        if getattr(probe, '_prtouch_host_patched', False):
            return
        self._orig_probe_method = probe._probe
        endstop = self

        def _host_probe(speed, max_z_dist=None):
            toolhead = printer.lookup_object('toolhead')
            reactor = printer.get_reactor()
            if 'z' not in toolhead.get_status(
                    reactor.monotonic())['homed_axes']:
                raise printer.command_error(
                    '{"code":"key96", "msg": "Must home before probe", '
                    '"values": []}')
            pos = toolhead.get_position()
            # Mirror Creality _probe's target-Z clamp (suspended_det_status).
            target_z = probe.z_position
            try:
                suspended = endstop.get_suspended_det_status()
            except Exception:
                suspended = False
            if max_z_dist is not None and suspended is True:
                max_target_z = pos[2] - max_z_dist
                if target_z < max_target_z:
                    target_z = max_target_z
            trig_z = endstop.run_host_probe(target_z, speed)
            epos = [pos[0], pos[1], trig_z]
            atc = printer.lookup_object('axis_twist_compensation', None)
            if atc is not None:
                try:
                    epos[2] += atc.get_z_compensation_value(pos)
                except Exception:
                    logging.exception("K2-PRTouch: axis_twist_comp failed")
            printer.lookup_object('gcode').respond_info(
                "probe at %.3f,%.3f is z=%.6f (K2-PRTouch host-driven)"
                % (epos[0], epos[1], epos[2]))
            return epos[:3]

        probe._probe = _host_probe
        probe._prtouch_host_patched = True
        logging.info(
            "K2-PRTouch: probe._probe patched to host-driven run_host_probe")
