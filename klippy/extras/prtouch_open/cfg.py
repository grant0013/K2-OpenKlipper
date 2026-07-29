# cfg.py — configuration schema for prtouch_open
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# Configuration schema derived from upstream Creality prtouch.py
# (Copyright (C) 2022 wangyulong, GPL-3) at docs/source-reference/prtouch.py.
# Specifically: PRTouchCFG class, lines 14-61. Field-by-field equivalence
# is maintained so existing K2 printer.cfg `[prtouch_v3] ...` sections work
# unchanged.
"""Config schema for the prtouch_v3 / prtouch_open module.

Reads from the `[prtouch_v3]` section in printer.cfg, matching what Creality's
closed-source module accepts. Keeps the same field names and ranges so existing
K2 printer.cfg files are drop-in compatible with our replacement.
"""


class PrtouchConfig:
    """Configuration values read at startup.

    Field semantics — see docs/source-reference/prtouch.py:14 for the original
    documentation in comments and downstream usage. Notable fields:

    Sampling / trigger thresholds:
      base_count : number of baseline samples to average before probing
      pi_count   : sliding window size for trigger detection (32 default)
      min_hold   : lower-bound ADC delta to register a contact candidate
      max_hold   : upper-bound — three samples in a row above this auto-trigger

    Geometry:
      max_z      : Z stage extent (inherited from stepper_z config)
      bed_max_err: max allowed bed-vs-reference deviation (mm)
      max_dis_bef_g28 / dead_zone_bef_g28: pre-G28 Z motion limits

    Speeds:
      g28_sta0_speed : first-pass Z probing speed (mm/s)
      g28_sta1_speed : second-pass refinement speed
      g29_speed      : per-point bed-mesh probing speed
      g29_xy_speed   : XY traverse speed during bed mesh

    Temperatures (only enforced if g28_wait_cool_down is True):
      hot_min_temp / hot_max_temp / bed_max_temp

    Nozzle clear / PA-purge geometry:
      clr_noz_* / pa_*  — used by the NOZZLE_CLEAR and PA-related macros.
      Probably not exercised by M1 (we focus on probing first).

    Z-gap calibration (per-corner Z offsets from a stored profile):
      z_gap_00/01/10/11 — saved bed-corner offsets from previous gap measurement
    """

    def __init__(self, config):
        # Sampling
        self.base_count = config.getint('base_count', default=40, minval=10, maxval=100)
        self.pi_count = config.getint('pi_count', default=32, minval=16, maxval=128)

        # MCU pin map. K2 ships these in [prtouch_v3]; defaults match the F021 board.
        self.pres_swap_pin = config.get('pres_swap_pin', default='nozzle_mcu:PA15')
        self.step_swap_pin = config.get('step_swap_pin', default='!PC7')
        # HX711 pair as a comma-separated list — [0]=CLK, [1]=SDO
        # K2 ships: "nozzle_mcu:PB13, nozzle_mcu:PB14"
        _cs0 = config.get('pres_cs0_pin', default='nozzle_mcu:PB13, nozzle_mcu:PB14')
        _cs0_pins = [p.strip() for p in _cs0.split(',')]
        if len(_cs0_pins) < 2:
            raise config.error(
                f"pres_cs0_pin must list at least CLK,SDO; got: {_cs0!r}"
            )
        self.clk_pin = _cs0_pins[0]
        self.sdo_pin = _cs0_pins[1]

        # HX711 ADC config register byte (Creality default 60)
        self.cfg_regs = config.getint('pres_cfg_regs', default=60, minval=0, maxval=255)

        # Trigger thresholds.
        # K2's `pres_tri_hold: min, max, add` (e.g. "4000, 10000, 500") overrides
        # individual keys when present.
        _tri = config.get('pres_tri_hold', default=None)
        if _tri is not None:
            _tri_vals = [int(v.strip()) for v in _tri.split(',')]
            if len(_tri_vals) < 3:
                raise config.error(
                    f"pres_tri_hold must be 'min, max, add'; got: {_tri!r}"
                )
            self.min_hold = _tri_vals[0]
            self.max_hold = _tri_vals[1]
            self.add_hold = _tri_vals[2]
        else:
            self.min_hold = config.getint('min_hold', default=3000, minval=100, maxval=50000)
            self.max_hold = config.getint('max_hold', default=50000, minval=100, maxval=100000)
            self.add_hold = config.getint('add_hold', default=0, minval=0, maxval=50000)

        # MCU-side trigger filter. These are the exact defaults used by
        # Creality's K2 prtouch_v3 wrapper:
        #   ned_tftr=5, ned_hftr=1, ned_lftr=0.8 (sent as per-mille 800)
        # The MCU treats the first two values as enable/config bytes and the
        # third as a low-pass coefficient.
        self.pres_tri_fter = tuple(config.getfloatlist(
            'pres_tri_fter', default=(5.0, 1.0, 0.8),
            sep=',', count=3,
        ))
        self.pres_ned_tftr = int(self.pres_tri_fter[0])
        self.pres_ned_hftr = int(self.pres_tri_fter[1])
        self.pres_ned_lftr = int(round(self.pres_tri_fter[2] * 1000.0))
        self.pres_dead_samples = int(round(config.getfloat(
            'pres_ded_tkms', default=128.0, minval=0.0, maxval=2000.0,
        )))

        self.g28_sta0_min_hold = config.getint(
            'g28_sta0_min_hold', default=self.min_hold * 2,
            minval=100, maxval=100000,
        )

        # Filter coefficients (consumed by prtouch_open.filter.Filter).
        # Defaults match upstream K2 filter.py.
        self.hft_hz = config.getfloat('hft_hz', default=5.0, minval=0.1, maxval=10.0)
        self.lft_k1 = config.getfloat('lft_k1', default=0.8, minval=0.0, maxval=1.0)
        self.lft_k1_oft = config.getfloat('lft_k1_oft', default=0.8, minval=0.0, maxval=1.0)
        self.lft_k1_cal = config.getfloat('lft_k1_cal', default=0.8, minval=0.0, maxval=1.0)

        # Step capture: base-step multiplier (mirrors upstream dirzctl.step_base)
        self.step_base = config.getint('step_base', default=16, minval=1, maxval=256)

        # ---- K2-specific keys we DRAIN so Klipper's strict-config validation
        # ---- doesn't reject existing [prtouch_v3] sections.
        # These were consumed by Creality's closed-source module but aren't
        # used by our M1 probe flow. We read them (so they're "consumed")
        # and stash them on cfg for later milestones that may want them.
        self.prth_msg_show = config.getboolean('prth_msg_show', default=False)
        self.enable_not_linear_comp = config.getboolean(
            'enable_not_linear_comp', default=False
        )
        # Temperature-compensation polynomial coefficients (cubic fit a + b·T + c·T²)
        self.temp_non_linaer_a = config.getfloat('temp_non_linaer_a', default=0.0)
        self.temp_non_linaer_b = config.getfloat('temp_non_linaer_b', default=0.0)
        self.temp_non_linaer_c = config.getfloat('temp_non_linaer_c', default=0.0)
        # Channel & motion knobs (not yet wired into M1)
        self.prth_max_chps = config.getint('prth_max_chps', default=8, minval=1, maxval=64)
        self.prth_tri_zacc = config.getfloat(
            'prth_tri_zacc', default=100.0, minval=1.0, maxval=10000.0,
        )
        self.prth_min_fans = config.getfloat(
            'prth_min_fans', default=0.0, minval=0.0, maxval=1.0,
        )
        # Bed-zone coordinate triples (consumed as raw strings; parsed lazily
        # if a future macro needs them)
        self.prth_clr_brush_pos = config.get('prth_clr_brush_pos', default=None)
        self.prth_clr_probe_pos = config.get('prth_clr_probe_pos', default=None)
        self.prth_clr_pose = config.get('prth_clr_pose', default=None)
        # Steadiness / acceleration check ratio (1 = strict, 0 = ignore)
        self.need_steady_regional = config.getfloat(
            'need_steady_regional', default=0.0, minval=0.0, maxval=1.0,
        )
        # Bed-mesh helper: short-form Z-lift between probe points (1 = enabled)
        self.horizontal_move_z_quick = config.getint(
            'horizontal_move_z_quick', default=0, minval=0, maxval=1,
        )
        # Material-slot X positions (CFS / multi-colour related on K2 Combo).
        # Not used by M1; drained for strict-config compliance.
        self.slot_left_pos = config.getfloat(
            'slot_left_pos', default=0.0, minval=0.0, maxval=1000.0,
        )
        self.slot_mid_pos = config.getfloat(
            'slot_mid_pos', default=0.0, minval=0.0, maxval=1000.0,
        )

        # Temperatures (probe environment)
        self.hot_min_temp = config.getfloat('s_hot_min_temp', default=140, minval=80, maxval=200)
        self.hot_max_temp = config.getfloat('s_hot_max_temp', default=200, minval=180, maxval=300)
        self.bed_max_temp = config.getfloat('s_bed_max_temp', default=60, minval=45, maxval=100)
        self.g28_wait_cool_down = config.getboolean('g28_wait_cool_down', default=False)

        # PA + nozzle-clear geometry (deferred to M3)
        self.pa_fil_len_mm = config.getint('pa_fil_len_mm', default=2, minval=2, maxval=100)
        self.pa_fil_dis_mm = config.getint('pa_fil_dis_mm', default=30, minval=2, maxval=100)
        self.pa_clr_dis_mm = config.getint('pa_clr_dis_mm', default=20, minval=2, maxval=100)
        self.pa_clr_down_mm = config.getfloat('pa_clr_down_mm', default=-0.1, minval=-1, maxval=1)
        self.clr_noz_start_x = config.getfloat('clr_noz_start_x', default=0, minval=0, maxval=1000)
        self.clr_noz_start_y = config.getfloat('clr_noz_start_y', default=0, minval=0, maxval=1000)
        # NOTE: upstream prtouch.py minval=self.pa_clr_dis_mm+6 — we relax that here
        # because users may zero-init these and the run-time defaults are set in
        # the endstop wrapper after find_objs() based on mesh bounds.
        self.clr_noz_len_x = config.getfloat('clr_noz_len_x', default=0, minval=0, maxval=1000)
        self.clr_noz_len_y = config.getfloat('clr_noz_len_y', default=0, minval=0, maxval=1000)

        # Geometry
        self.bed_max_err = config.getint('bed_max_err', default=2, minval=2, maxval=10)
        self.max_z = config.getsection('stepper_z').getfloat(
            'position_max', default=300, minval=100, maxval=500
        )
        self.max_dis_bef_g28 = config.getfloat('max_dis_bef_g28', default=10, minval=0, maxval=50)
        self.dead_zone_bef_g28 = config.getfloat(
            'dead_zone_bef_g28', default=self.max_dis_bef_g28 / 2,
            minval=0, maxval=50,
        )

        # Speeds
        self.g28_sta0_speed = config.getfloat('g28_sta0_speed', default=2.0, minval=0.1, maxval=10)
        self.g28_sta1_speed = config.getfloat('g28_sta1_speed', default=2.5, minval=0.1, maxval=10)
        self.g29_rdy_speed = config.getfloat('g29_rdy_speed', default=2.5, minval=0.1, maxval=10)
        self.g29_speed = config.getfloat('g29_speed', default=2.0, minval=0.1, maxval=10)
        self.g29_xy_speed = config.getfloat('g29_xy_speed', default=150, minval=10, maxval=1000)
        self.best_above_z = config.getfloat('best_above_z', default=1.5, minval=0.5, maxval=10)
        self.max_probe_descent = config.getfloat(
            'max_probe_descent', default=1.5, minval=0.5, maxval=10.0
        )
        self.host_probe_warmup_polls = config.getint(
            'host_probe_warmup_polls', default=8, minval=4, maxval=32
        )
        self.host_probe_period_ms = config.getint(
            'host_probe_period_ms', default=80, minval=55, maxval=200
        )
        self.host_probe_failure_retract = config.getfloat(
            'host_probe_failure_retract', default=2.0,
            minval=0.5, maxval=10.0
        )
        self.host_probe_contact_threshold = config.getint(
            'host_probe_contact_threshold', default=800,
            minval=100, maxval=100000
        )
        self.host_probe_contact_release_ratio = config.getfloat(
            'host_probe_contact_release_ratio', default=0.75,
            minval=0.1, maxval=1.0
        )
        self.host_probe_contact_polls = config.getint(
            'host_probe_contact_polls', default=2, minval=1, maxval=8
        )
        self.fix_z_offset = config.getfloat('fix_z_offset', default=0.0, minval=-1, maxval=1)
        self.self_z_offset = config.getfloat('self_z_offset', default=0.0, minval=-2, maxval=2)

        # Motor-shake parameters (defeat backlash before precision probing)
        self.shake_cnt = config.getint('shake_cnt', default=4, minval=1, maxval=512)
        self.shake_range = config.getint('shake_range', default=0.5, minval=0.1, maxval=2)
        self.shake_max_velocity = config.getfloat(
            'shake_max_velocity', default=100, minval=1, maxval=5000
        )
        self.shake_max_accel = config.getfloat(
            'shake_max_accel', default=1000, minval=1, maxval=50000
        )

        # Bed-mesh corner gap calibration (stored offsets from prior measurement)
        self.need_measure_gap = config.getboolean('need_measure_gap', default=True)
        self.gap_dis_range = config.getfloat('gap_dis_range', default=0.6, minval=0.2, maxval=2)
        self.z_gap_00 = config.getfloat('z_gap_00', default=0, minval=-1, maxval=1)
        self.z_gap_01 = config.getfloat('z_gap_01', default=0, minval=-1, maxval=1)
        self.z_gap_10 = config.getfloat('z_gap_10', default=0, minval=-1, maxval=1)
        self.z_gap_11 = config.getfloat('z_gap_11', default=0, minval=-1, maxval=1)
        self.check_bed_mesh_max_err = config.getfloat(
            'check_bed_mesh_max_err', default=0.2, minval=0.01, maxval=1
        )

        # Diagnostics
        self.show_msg = config.getboolean('show_msg', default=False)
        self.tri_wave_ip = config.get('tri_wave_ip', None)

        # Stored profiles from previous gap measurements (if present)
        stored = config.get_prefix_sections('prtouch')
        self.stored_profs = stored[1] if (len(stored) == 2 and self.need_measure_gap) else None
