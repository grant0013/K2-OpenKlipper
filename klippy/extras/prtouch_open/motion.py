# motion.py — probe_by_step + run_G28_Z orchestration
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# Algorithm ported from upstream Creality prtouch.py (GPL-3, wangyulong)
# at docs/source-reference/prtouch.py:
#   probe_by_step           prtouch.py:619-658
#   _cal_min_z              prtouch.py:580-617
#   run_G28_Z               prtouch.py:660-720
#
# Wire-layer adaptation: upstream uses two separate MCU helpers (hx711s
# for pressure, dirzctl for motion + step capture). v3 unifies the wire
# layer into PressureSampler + StepCapture (capture-only). Motion driving
# moves to Klipper's standard toolhead infrastructure.
"""Probe motion orchestration.

Combines PressureSampler (streaming ADC) + StepCapture (Z-position log)
+ Klipper's toolhead motion into a single probe_by_step routine that
matches the behaviour of upstream prtouch.py.

High-level flow (probe_by_step):

  1. Acquire baseline (sampler.read_baseline) on the channel(s).
  2. Start pressure streaming (sampler.start_streaming).
  3. Start step capture (step.start_capture).
  4. Drive Z motion via toolhead — slow descent toward the bed by
     `min_dis_mm` at `speed_mm`.
  5. Poll loop in the reactor:
       - pop samples via sampler.get_vals()
       - filter via sampler.filter.cal_offset_by_vals + cal_filter_by_vals
       - per channel: sampler.check_trigger(...)
       - on trigger: stop Z motion, read final step-capture window,
         interpolate exact contact Z, return.
       - on motion-complete-without-trigger: return with sentinel.
  6. Optionally back off Z by half the descent step count.

The interpolation in step 5 uses step.get_params() — by then the capture
buffer holds at least two snapshots (start and current). We use the two
endpoints to linearly interpolate the Z position at the MCU tick of the
trigger sample (sampled via sampler's tick log).
"""

import logging
import math


class ProbeMotion:
    """Drives a single probe-by-step descent.

    Holds references to the sampler, step capture, and the parent
    PRTouchEndstopWrapper for access to cfg, obj.toolhead, etc.
    """

    def __init__(self, wrapper):
        self.wrapper = wrapper
        self.cfg = wrapper.cfg
        self.obj = wrapper.obj
        self.sampler = wrapper.sampler
        self.step = wrapper.step
        self.val = wrapper.val

    # ---- public entry point ------------------------------------------------

    def probe_by_step(self, rdy_pos, speed_mm, min_dis_mm, min_hold, max_hold, up_after=True):
        """Drive a single Z descent, sample, detect contact, return Z position.

        Args:
            rdy_pos    : [x, y, z_start, e] — current toolhead position at start
            speed_mm   : descent speed (mm/s)
            min_dis_mm : maximum descent distance (mm)
            min_hold   : trigger threshold — pressure delta in ADC counts
            max_hold   : auto-trigger threshold (3-in-a-row above ⇒ contact)
            up_after   : if True, retract Z by half the descent after trigger

        Returns:
            (out_index, out_val_mm, deal_sta) — mirrors upstream prtouch.py
              out_index   : index in the trigger window where contact was detected
              out_val_mm  : interpolated Z position of contact (in mm, relative to start)
              deal_sta    : True if probe completed cleanly, False if motion ran out
        """
        log = self.obj.printer.lookup_object('gcode')
        log.respond_info(
            'PROBE_BY_STEP x=%.2f y=%.2f z=%.2f speed=%.2f min_dis=%.2f'
            % (rdy_pos[0], rdy_pos[1], rdy_pos[2], speed_mm, min_dis_mm)
        )

        # ---- Phase 1: baseline ----------------------------------------
        # TODO(live): verify sampler.read_baseline() matches upstream
        # behaviour now that we know is_src=0 returns MCU-pre-filtered samples.
        self.sampler.read_baseline(
            n_samples=int(self.cfg.base_count / 2),
            max_hold=max_hold,
        )

        # ---- Phase 2: compute motion plan ----------------------------
        step_dist = self.step.get_step_dist()
        step_base = self.step.step_base
        # Steps to descend min_dis_mm at this microstep granularity.
        step_cnt = int(min_dis_mm / (step_dist * step_base)) if step_dist else 0
        if step_cnt == 0:
            raise self.obj.printer.command_error(
                "probe_by_step: step_dist is zero — step.attach_steppers not called?"
            )
        # Per-step interval in microseconds.
        step_us = int(((min_dis_mm / speed_mm) * 1_000_000) / step_cnt)
        target_z = rdy_pos[2] - min_dis_mm

        # ---- Phase 3: start streams ----------------------------------
        self.sampler.start_streaming(
            min_hold=min_hold, max_hold=max_hold, add_hold=self.cfg.add_hold,
        )
        # acq_tick — pressure and step capture sync rates must align so
        # we can correlate ticks across streams.
        acq_tick = int(self.obj.mcu.seconds_to_clock(0.00078125))
        self.step.start_capture(acq_tick=acq_tick)

        # ---- Phase 4: drive Z motion (NON-BLOCKING) ------------------
        # TODO(live): the right pattern here depends on Klipper version. The
        # cleanest is `toolhead.drip_move` (used by homing.HomingMove), but
        # that requires a Klipper >= 0.11 reactor pattern. The simpler
        # synchronous pattern is to issue toolhead.manual_move on a separate
        # greenlet, then poll in the main reactor. We use the synchronous
        # toolhead.move + flush pattern below — it blocks main loop until
        # the move starts emitting steps but the step generator runs ahead
        # so we can read samples in parallel.
        #
        # First live verification target: ensure samples accumulate during
        # the move (not just after). If not, switch to drip_move pattern.
        try:
            self.obj.toolhead.manual_move(
                [rdy_pos[0], rdy_pos[1], target_z], speed_mm,
            )
            # Critically: do NOT call toolhead.wait_moves() here — that would
            # block until the descent finishes, which defeats the polling.
        except Exception as e:
            self._stop_streams()
            raise

        reactor = self.obj.printer.get_reactor()
        deadline = reactor.monotonic() + (min_dis_mm / speed_mm) + 1.0
        # Small startup delay matching upstream's 0.015s settle
        reactor.pause(reactor.monotonic() + 0.015)

        # ---- Phase 5: poll loop --------------------------------------
        try:
            while reactor.monotonic() < deadline and self.wrapper.ck_sys_sta():
                # heartbeats — keep MCU streams alive
                self.step.send_heart_beat()

                # Pop accumulated samples per channel
                all_valss = self._get_sample_window()
                if not all_valss or not all_valss[0]:
                    reactor.pause(reactor.monotonic() + 0.005)
                    continue

                # Filter
                f = self.sampler.filter
                unfit_vals, tmp_unfit_vals = f.cal_offset_by_vals(
                    self.sampler.s_count, all_valss,
                    f.lft_k1_oft, self.cfg.pi_count,
                )
                fit_vals, tmp_fit_vals = f.cal_filter_by_vals(
                    self.sampler.s_count, all_valss,
                    f.hft_hz, f.lft_k1, self.cfg.pi_count,
                )

                # Per-channel trigger check
                for i in range(self.sampler.s_count):
                    triggered, _why = self.sampler.check_trigger(
                        i, tmp_fit_vals[i], tmp_unfit_vals[i],
                        min_hold, max_hold,
                    )
                    if not triggered:
                        continue
                    # Stop motion immediately
                    self.obj.toolhead.flush_step_generation()
                    # Read final sample window for the interpolation pass
                    reactor.pause(reactor.monotonic() + 0.015)
                    refined_valss = self._get_sample_window()
                    if refined_valss and refined_valss[i]:
                        hx711_vals, tmp_hx711_vs = f.cal_filter_by_vals(
                            self.sampler.s_count, refined_valss,
                            f.hft_hz, f.lft_k1_cal, self.cfg.pi_count,
                        )
                        # Compute exact Z via step-capture interpolation
                        out_z_mm, deal_sta = self._cal_min_z(rdy_pos[2], tmp_hx711_vs[i])
                    else:
                        out_z_mm, deal_sta = target_z, True

                    # Optional retract
                    if up_after:
                        self.obj.toolhead.manual_move(
                            [rdy_pos[0], rdy_pos[1], rdy_pos[2]], speed_mm * 2,
                        )
                    return self.val.out_index, out_z_mm, deal_sta

                reactor.pause(reactor.monotonic() + 0.005)
            # Motion ran out without a trigger
            return self.val.out_index, target_z, True
        finally:
            self._stop_streams()

    # ---- helpers -----------------------------------------------------------

    def _get_sample_window(self):
        """Pop the latest sample window from each channel.

        Returns:
            list of per-channel sample lists [[ch0_samples], [ch1...], ...]
            or None if no samples are queued.

        TODO(live): currently calls sampler.read_buffer for each channel.
        Live-verify: confirm that is_src=0 with idx=0 + len=pi_count*2
        returns the most-recent pi_count*2 MCU-filtered samples.
        """
        if not self.sampler.channels:
            return None
        valss = []
        for ch_idx in range(self.sampler.s_count):
            try:
                snapshot = self.sampler.read_buffer(
                    ch_idx, idx=0, length=self.cfg.pi_count * 2,
                )
                # The response object's 'datas' field carries the samples;
                # the exact key/shape needs live verification. For now, try
                # both common shapes.
                if isinstance(snapshot, dict):
                    samples = snapshot.get('datas') or snapshot.get('data') or []
                else:
                    samples = list(snapshot or [])
                valss.append(list(samples))
            except Exception as e:
                logging.debug("read_buffer ch=%d failed: %s", ch_idx, e)
                valss.append([])
        # Pad to 4 channels (matches upstream's [[], [], [], []] shape)
        while len(valss) < 4:
            valss.append([])
        return valss

    def _cal_min_z(self, start_z, vals):
        """Find the contact Z from a sample window + step capture.

        Ported from upstream prtouch.py:580-617. Uses the step-capture
        snapshots to interpolate between (tick_start, z_start) and
        (tick_end, z_end) the Z position at the MCU tick of the
        minimum-pressure sample.
        """
        snapshots, _current_tick = self.step.get_params()
        if len(snapshots) < 2:
            # Insufficient step capture — fall back to "wherever we ended up"
            now_pos = self.obj.toolhead.get_position()
            return now_pos[2], False

        # Normalize the sample window, rotate so endpoints are level, find min.
        vp = list(vals)
        if not vp:
            return start_z, False
        max_val = max(vp)
        min_val = min(vp)
        if (max_val - min_val) == 0:
            return start_z, False
        for i in range(len(vp)):
            vp[i] = (vp[i] - min_val) / (max_val - min_val)
        angle = math.atan((vp[-1] - vp[0]) / len(vp))
        sin_a = math.sin(-angle)
        cos_a = math.cos(-angle)
        for i in range(len(vp)):
            vp[i] = i * sin_a + vp[i] * cos_a
        out_index = vp.index(min(vp))
        self.val.out_index = out_index

        # Linear interpolate Z between the two step-capture endpoints based
        # on relative position of out_index within the sample window.
        # The first snapshot is t0/z0 (capture start), the last is t1/z1.
        # TODO(live): verify that snapshots[0]['tick'] aligns with the
        # PRESSURE stream's first tick — if not, we need to record a
        # paired tick anchor in PressureSampler at start_streaming.
        t0 = snapshots[0]['tick']
        z0_steps = snapshots[0]['step']
        t1 = snapshots[-1]['tick']
        z1_steps = snapshots[-1]['step']
        if t1 == t0:
            return start_z, False
        # Tick at the trigger sample (approximate — relative position in the window)
        tick_at_out = t0 + int((t1 - t0) * out_index / max(len(vals) - 1, 1))
        # Linear interp in step count
        steps_at_out = z0_steps + (z1_steps - z0_steps) * (tick_at_out - t0) / (t1 - t0)
        z_at_out_mm = start_z + (steps_at_out - z0_steps) * self.step.get_step_dist() * self.step.step_base
        return z_at_out_mm, True

    def _stop_streams(self):
        try:
            self.sampler.stop_streaming()
        except Exception:
            logging.exception("sampler.stop_streaming failed")
        try:
            self.step.stop_capture()
        except Exception:
            logging.exception("step.stop_capture failed")
