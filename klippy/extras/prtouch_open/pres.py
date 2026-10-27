# pres.py — pressure-sensing subsystem
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# Algorithms derived from upstream Creality prtouch.py (GPL-3, wangyulong)
# at docs/source-reference/prtouch.py — specifically:
#   _check_trigger        prtouch.py:199-248  (contact-detection cascade)
#   probe_by_step          prtouch.py:619-658  (probing-motion + sampling loop)
#   read_base / query_start patterns           (baseline + streaming setup)
#
# Wire-level wrappers from mcu_protocol.py (our work, this repo).
"""Pressure / strain-gauge sampling.

Owns one or more sampling channels (the K2 has one active per nozzle on F021
boards; the .o supports up to 4 indexed channels). Provides:

- Baseline acquisition: average N samples while the nozzle is clear of the bed.
- Streaming sampling: continuous fill of a host-side ring buffer from the MCU.
- Contact-detection trigger: applies the multi-rule cascade from the open
  prtouch.py to detect when the strain-gauge readings indicate nozzle-on-bed.

The MCU-side state (per docs/MCU_SYMBOLS.md):
- `pr_pres` global, 4900 bytes, holds 4-channel pressure data + control regs
- Streaming uses `prtouch_*_zip` compression to fit 4ch × 1.28 kHz over 230400 baud UART
- Read via `read_prtouch_pres` returns a windowed slice of the compressed buffer

The Klippy-side state (per docs/ATTRS.md PrtouchPres):
- pres_data[0..3]      — accumulated samples per channel
- pres_time[0..3]      — paired sample-tick deltas
- pres_data_amplitude  — current p2p amplitude
- pres_diff_isOK_cnt   — internal validity counter

Threading: like Klipper's standard MCU helpers, all calls happen from the
main Klippy reactor greenlet. No locks needed.
"""

import logging
import math

from . import mcu_protocol as proto
from . import filter as _filter


# -- contact-detection rule constants -------------------------------------
# Mirrors prtouch.py:199-248. The thresholds inside _check_trigger were hand-
# tuned by Creality; we keep them as named constants here so they're tuneable
# in future PRs without rewriting the cascade.

# Three-in-a-row above max_hold auto-triggers.
RULE_OUT_MAX_RUN = 3

# Normalized rise threshold: the last sample must rise above the worst-case
# trend by >= this fraction across the window (after rotation to flat baseline).
RULE_NORMALIZED_RISE = 0.8

# Filter coefficient applied to suppress lone spikes inside the window
# (a sample > max_hold flanked by two samples < max_hold/2 gets clipped).
RULE_SPIKE_NEIGHBOUR_FRAC = 0.5


class PressureChannel:
    """One pressure-sensing channel — wraps the per-channel MCU state."""

    def __init__(self, mcu_obj, oid, idx, swp_pin, clk_pin, sdo_pin):
        self.mcu = mcu_obj
        self.oid = oid
        self.idx = idx
        self.swp_pin = swp_pin
        self.clk_pin = clk_pin
        self.sdo_pin = sdo_pin

        # Recent baseline (computed once before probing)
        self.baseline = 0
        # Most-recent sample buffer (filled by streaming reads)
        self.samples = []
        self.sample_ticks = []

    def __repr__(self):
        return f"<PressureChannel oid={self.oid} idx={self.idx}>"


class PressureSampler:
    """Orchestrates pressure-channel sampling.

    A PressureSampler owns the `mcu_protocol.PresCommands` wrapper and a list
    of PressureChannel objects. It exposes the higher-level operations:

      read_base(n, max_hold)  — average n samples while idle, store baseline
      query_start(n, timeout) — open the streaming buffer for live probing
      get_vals()              — pop accumulated samples since last call
      check_trigger(channel_idx, fit_vals, unfit_vals, min_hold, max_hold)
                              — apply the contact-detection cascade

    Most of this maps onto the open `prtouch.py`'s `obj.hx711s.*` calls. The
    key implementation difference: v1/v2 prtouch had a separate `hx711s`
    MCU object; v3 unifies the HX711 + step + APAX into one `prtouch_v3`
    MCU object addressed by oid. Calling shape is the same; underlying wire
    commands differ — that mapping is what `mcu_protocol.PresCommands` does.
    """

    def __init__(self, mcu_obj, cfg):
        self.mcu = mcu_obj
        self.cfg = cfg
        # Command wrappers require the MCU data dictionary, which upstream
        # Klipper does not have while it is initially parsing printer.cfg.
        # Build them from our registered config callback after mcu_identify.
        self.proto = None
        self.filter = _filter.Filter(cfg)
        self.channels = []
        self._streaming = False
        # The number of active sampling channels — only 1 on K2 F021 per
        # observation (per memory: sen_cout=1), but the MCU supports up to 4.
        self.s_count = 0

    # ---- lifecycle ----

    def add_channel(self, oid, idx, swp_pin, clk_pin, sdo_pin):
        ch = PressureChannel(self.mcu, oid, idx, swp_pin, clk_pin, sdo_pin)
        self.channels.append(ch)
        self.s_count = len(self.channels)
        return ch

    def build_config(self):
        """Queue MCU configuration after its command dictionary is known."""
        self.proto = proto.PresCommands(self.mcu, self.channels[0].oid)
        for ch in self.channels:
            self.mcu.add_config_cmd(
                "config_prtouch_pres oid=%d idx=%d "
                "swp_pin=%s clk_pin=%s sdo_pin=%s"
                % (
                    ch.oid,
                    ch.idx,
                    ch.swp_pin,
                    ch.clk_pin,
                    ch.sdo_pin,
                )
            )

    def start_streaming(
        self, *, cfg_regs=60, acq_tick=None,
        ned_tftr=0, ned_hftr=0, ned_lftr=0,
        min_hold=None, max_hold=None, add_hold=0, lmt_dead=0,
    ):
        """Begin continuous sampling on all configured channels.

        Defaults pulled from cfg if not provided. cfg_regs=60 matches what
        Creality uses in the wild (the value observed via runtime introspection).
        """
        if min_hold is None:
            min_hold = self.cfg.min_hold
        if max_hold is None:
            max_hold = self.cfg.max_hold
        if acq_tick is None:
            # acq_tkms = 0.78125 ms per sample on K2 (see docs/ATTRS.md).
            # Klipper expresses MCU ticks via the chip's CLOCK_FREQ — the
            # `mcu_protocol` layer converts seconds to ticks. Here we pass
            # the seconds-equivalent; conversion is the caller's
            # responsibility for now (deferred — see TODO below).
            acq_tick = int(self.mcu.seconds_to_clock(0.00078125))

        acks = []
        for ch in self.channels:
            acks.append(self.proto.start_sampling(
                ch.oid, cfg_regs, acq_tick,
                ned_tftr, ned_hftr, ned_lftr,
                min_hold, max_hold, add_hold, lmt_dead,
            ))
        self._streaming = True
        # One ack_prtouch reply per channel, for callers that check err.
        return acks

    def stop_streaming(self, sta_swap=0):
        if not self._streaming:
            return
        for ch in self.channels:
            self.proto.stop_sampling(ch.oid, sta_swap)
        self._streaming = False

    # ---- single-shot reads ----

    def read_baseline(self, n_samples=None, max_hold=None):
        """Average n_samples samples while idle — establishes per-channel baseline.

        Mirrors `hx711s.read_base(n, max_hold)` in upstream prtouch.py:620.
        Stores result on each channel's `.baseline`.
        """
        if n_samples is None:
            n_samples = int(self.cfg.base_count / 2)
        if max_hold is None:
            max_hold = self.cfg.max_hold

        # TODO(M1): start a short streaming session, accumulate n_samples,
        # stop, average. Defer until live K2 access — exact semantics of
        # is_src=0 vs is_src=1 in read_prtouch_pres need empirical verification.
        # For now, call the read primitive and let callers compose.
        raise NotImplementedError(
            "read_baseline: requires live K2 access to verify is_src semantics "
            "of read_prtouch_pres. Stub for M1 first-pass."
        )

    def read_buffer(self, channel_idx, idx=0, length=None):
        """Pop the latest streaming-buffer window from one channel."""
        if length is None:
            length = self.cfg.pi_count
        ch = self.channels[channel_idx]
        # is_src=0 selects processed (post-zip-decode) samples
        return self.proto.read_buffer(ch.oid, is_src=0, ch=channel_idx, idx=idx, length=length)

    # ---- contact-detection cascade --------------------------------------

    def check_trigger(self, arg_index, fit_vals, unfit_vals, min_hold, max_hold):
        """Apply the multi-rule trigger cascade.

        Ported verbatim from upstream prtouch.py:199-248. Returns True when the
        sample sequence indicates the nozzle has touched the bed.

        Rules (applied in order):
        1. Streaming has run out: the MCU reports the motion stepper completed
           its travel without trigger — fall through and return triggered=True
           so the caller can wind up the probe gracefully. (Caller passes
           `out_of_motion=True` via len(all_params)==2 in upstream.)
        2. Out-max-run: three consecutive samples at the end of the window
           exceed max_hold. Strong contact.
        3. The window must be exactly pi_count long for the fit-rule.
        4. Spike suppression: a sample > max_hold flanked by both neighbours
           < max_hold/2 gets clipped to the previous sample (defends against
           ADC glitches).
        5. Rotate-and-find-min: normalize the window to [0,1], rotate so the
           endpoint trend is flat, find the minimum index. Apply a low-pass
           backfill from that index onwards.
        6. Final-three rising: last three samples must be strictly increasing.
        7. Last-three exceed window max: each of the last three must be
           greater than the max of the rest of the window.
        8. Normalized rise: across (pi_count-1) iterations, the difference
           between the last sample and sample i, scaled by (1 - i/pi_count),
           must exceed RULE_NORMALIZED_RISE (0.8) — i.e. the contact is
           clearly above the baseline trend.
        9. Absolute thresholds: last sample >= min_hold, second-last >= half,
           third-last >= third (graceful absolute floor).

        See upstream prtouch.py for the original implementation. Behaviour is
        intended to be byte-identical for the same input sequence.
        """
        pi_count = self.cfg.pi_count

        # Rule 2: out-max-run at end of window
        if (
            len(fit_vals) >= (pi_count // 2)
            and math.fabs(fit_vals[-1]) >= max_hold
            and math.fabs(fit_vals[-2]) >= max_hold
            and math.fabs(fit_vals[-3]) >= max_hold
        ):
            return True, 'out_max_hold'

        # Rule 3: window must be full
        if len(fit_vals) != pi_count:
            return False, None

        # Rule 4: spike-suppression
        # Copy for in-place edits (upstream prtouch.py does this too).
        vals = list(fit_vals)
        for i in range(1, pi_count - 1):
            if (
                vals[i] >= max_hold
                and vals[i - 1] < (max_hold * RULE_SPIKE_NEIGHBOUR_FRAC)
                and vals[i + 1] < (max_hold * RULE_SPIKE_NEIGHBOUR_FRAC)
            ):
                vals[i] = vals[i - 1]

        # Rule 5: normalize + rotate to flat baseline + low-pass backfill
        vp = list(vals)
        max_val = max(vp)
        min_val = min(vp)
        # Defensive divide-by-zero guard (upstream uses += 1)
        if (max_val - min_val) == 0:
            max_val += 1
        for i in range(len(vp)):
            vp[i] = (vp[i] - min_val) / (max_val - min_val)

        # Rotate: subtract baseline trend (angle = arctan(rise / window-length))
        angle = math.atan((vp[-1] - vp[0]) / len(vp))
        sin_a = math.sin(-angle)
        cos_a = math.cos(-angle)
        for i in range(len(vp)):
            vp[i] = i * sin_a + vp[i] * cos_a

        out_index = vp.index(min(vp))
        # Low-pass backfill from out_index onwards. Matches the upstream
        # prtouch.py:228-230 loop verbatim, including the apparent quirk
        # that `i` is the loop variable but the LHS indexes `out_index` —
        # this converges fit_vals_t[out_index] toward fit_vals_t[out_index-1]
        # over (pi_count - out_index) iterations. Whether the upstream meant
        # `vals[i]` or `vals[out_index]` is unclear; we preserve byte-for-byte
        # behaviour and revisit in M2 once we have live trigger traces to
        # compare against.
        lft_coef = self.filter.lft_k1_oft / 2.0
        if out_index > 0:
            for i in range(out_index, pi_count):
                vals[out_index] = (
                    vals[out_index] * lft_coef
                    + vals[out_index - 1] * (1.0 - lft_coef)
                )

        # Rule 6: final-three rising
        if not (vals[-1] > vals[-2] > vals[-3]):
            return False, None

        # Rule 7: last-three exceed window max
        head_max = max(vals[0:(pi_count - 3)])
        if not (vals[-1] > head_max and vals[-2] > head_max and vals[-3] > head_max):
            return False, None

        # Rule 8: normalized rise
        max_val = max(vals)
        min_val = min(vals)
        if (max_val - min_val) == 0:
            return False, None
        normed = [(v - min_val) / (max_val - min_val) for v in vals]
        for i in range(pi_count - 1):
            scale = (pi_count - i) / pi_count
            if (normed[-1] - normed[i]) / scale < RULE_NORMALIZED_RISE:
                return False, None

        # Rule 9: absolute thresholds
        if (
            fit_vals[-1] < min_hold
            or fit_vals[-2] < (min_hold / 2)
            or fit_vals[-3] < (min_hold / 3)
        ):
            return False, None

        return True, 'fit_all_rules'


# Filter implementation lives in filter.py (this package). The upstream
# `printer.lookup_object('filter')` pattern is deliberately *not* used — see
# docs/ADRs/ADR-001-probe-api-integration.md for the rationale. PressureSampler
# instantiates its own Filter in __init__ from the same PrtouchConfig.
