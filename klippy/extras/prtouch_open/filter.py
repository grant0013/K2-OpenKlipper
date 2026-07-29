# filter.py — pure-Python IIR/triplet filter chain
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# Algorithm derived from the GPL-3 `filter.py` Klipper extras module shipped
# on the K2 (mirrored verbatim at docs/source-reference/filter.py). That module
# itself bears a confusingly stale "Support for 1-wire based temperature sensors"
# header attributed to Alan Lord — the header is wrong (it's Klipper's
# `thermistor.py` license header pasted in) but the GPL-3 grant is genuine.
#
# We reimplement here rather than `lookup_object('filter')`-ing the K2's module
# so that prtouch_open has no implicit dependency on a Creality-only sibling
# module — the goal is upstream-Klipper compatibility (see ADRs/ADR-001).
"""Filter chain for prtouch_v3 contact detection.

Three filter primitives, applied in offset and bandpass-ish chains:

  RCT  — triplet-minimum-magnitude (median-3-style spike rejection)
  RCH  — single-pole IIR high-pass
  RCL  — single-pole IIR low-pass (EMA)

The two aggregator entry points are:

  cal_offset_by_vals(s_count, valss, lft_k1, cut_len)
      Per channel: RCT -> RCL. Cross-channel absolute sum.
      Used to track slow baseline drift (DC removal source).

  cal_filter_by_vals(s_count, valss, hft_hz, lft_k1, cut_len)
      Per channel: RCT -> RCH -> RCL. Cross-channel absolute sum.
      Used as the contact-detection input — produces the `fit_vals` /
      `tmp_fit_vals` series that `pres.check_trigger` consumes.

`cut_len` trims the leading edge of each output to match the trigger window
size (`PrtouchConfig.pi_count`). Trimming is from the FRONT because the
filter outputs are aligned with the most-recent sample on the right.
"""

import math


# ---------------------------------------------------------------------------
# Acquisition-frequency assumption
# ---------------------------------------------------------------------------
# The upstream K2 implementation hard-codes 80 Hz as the acquisition rate it
# feeds into the high-pass filter's RC constant. This is independent of the
# raw HX711 sample rate (~1.28 kHz) — by the time the values reach the HP
# filter, they have been downsampled / decimated to roughly 80 Hz of
# effective bandwidth. We preserve the constant for behavioural parity.
HP_FILTER_ACQ_FRQ_HZ = 80.0


# ---------------------------------------------------------------------------
# Primitive filters
# ---------------------------------------------------------------------------

class TripletMinFilter:
    """Sliding-window minimum-magnitude over triplets.

    For each contiguous (vals[i], vals[i+1], vals[i+2]) triplet, emit the
    member with the smallest absolute value. Padded with the last two input
    samples so output length equals input length.

    This is the "RCTFilter" in upstream filter.py:11-22. Acts as a
    fast spike rejector: a single large excursion is unlikely to be the
    minimum-magnitude member of its enclosing triplet.
    """

    def apply(self, vals):
        if len(vals) < 3:
            return list(vals)
        out = []
        for i in range(len(vals) - 2):
            tmp = (math.fabs(vals[i]), math.fabs(vals[i + 1]), math.fabs(vals[i + 2]))
            min_offset = tmp.index(min(tmp))
            out.append(vals[i + min_offset])
        out.append(vals[-2])
        out.append(vals[-1])
        return out


class HighPassIIR:
    """Single-pole RC high-pass IIR.

    Recurrence (from upstream filter.py:25-38):
        rc   = 1 / (2 * pi * cut_frq_hz)
        coff = rc / (rc + 1/acq_frq_hz)
        y[i] = (x[i] - x[i-1] + y[i-1]) * coff

    Output is seeded with `0` (per upstream) — implies a brief initial
    transient on the first ~3 samples. Trimming at the head via cut_len
    discards that transient by construction.
    """

    def __init__(self, cut_frq_hz, acq_frq_hz):
        rc = 1.0 / (2.0 * math.pi * cut_frq_hz)
        self.coff = rc / (rc + 1.0 / acq_frq_hz)

    def apply(self, vals):
        if not vals:
            return []
        out = [0.0]
        prev_in = vals[0]
        for x in vals[1:]:
            y = (x - prev_in + out[-1]) * self.coff
            out.append(y)
            prev_in = x
        return out


class LowPassIIR:
    """Single-pole EMA low-pass.

    Recurrence (from upstream filter.py:41-49):
        y[0] = x[0]
        y[i] = y[i-1] * (1 - k) + x[i] * k     ; 0 < k <= 1
    """

    def __init__(self, k):
        self.k = k

    def apply(self, vals):
        if not vals:
            return []
        out = [vals[0]]
        k = self.k
        for x in vals[1:]:
            out.append(out[-1] * (1.0 - k) + x * k)
        return out


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

class Filter:
    """Top-level filter — owns the K2-style hft_hz / lft_k1* coefficients.

    `cfg` may be either a `PrtouchConfig` or any object exposing the four
    coefficient attributes. Defaults (matching Creality's printer.cfg
    factory shipping values for K2 F021):

        hft_hz       = 5.0     # HP cut-off in cal_filter_by_vals
        lft_k1       = 0.8     # LP coefficient for the trigger filter
        lft_k1_oft   = 0.8     # LP coefficient for the offset (DC) filter
        lft_k1_cal   = 0.8     # LP coefficient used during calibration

    Note: `lft_k1_oft` is also consumed directly inside
    `pres.check_trigger` (the rotate-and-find-min rule), so it is exposed
    as an attribute on the Filter instance for that read.
    """

    def __init__(self, cfg=None, *, hft_hz=5.0, lft_k1=0.8, lft_k1_oft=0.8, lft_k1_cal=0.8):
        if cfg is not None:
            self.hft_hz = getattr(cfg, "hft_hz", hft_hz)
            self.lft_k1 = getattr(cfg, "lft_k1", lft_k1)
            self.lft_k1_oft = getattr(cfg, "lft_k1_oft", lft_k1_oft)
            self.lft_k1_cal = getattr(cfg, "lft_k1_cal", lft_k1_cal)
        else:
            self.hft_hz = hft_hz
            self.lft_k1 = lft_k1
            self.lft_k1_oft = lft_k1_oft
            self.lft_k1_cal = lft_k1_cal

    # ---- aggregator entry points ----

    def cal_offset_by_vals(self, s_count, valss, lft_k1, cut_len):
        """Slow-baseline (offset) filter chain.

        Per channel: TripletMin -> LowPass(lft_k1).
        Returns (out_vals, tmp_vals):
            tmp_vals : per-channel filtered series (abs-valued, trimmed to cut_len)
            out_vals : cross-channel absolute-sum, trimmed to cut_len
        """
        return self._apply_chain(
            s_count, valss, cut_len,
            chain=[TripletMinFilter(), LowPassIIR(lft_k1)],
        )

    def cal_filter_by_vals(self, s_count, valss, hft_hz, lft_k1, cut_len):
        """Bandpass-ish trigger filter chain.

        Per channel: TripletMin -> HighPass(hft_hz, 80Hz) -> LowPass(lft_k1).
        Returns (out_vals, tmp_vals) — same shape as cal_offset_by_vals.
        """
        return self._apply_chain(
            s_count, valss, cut_len,
            chain=[
                TripletMinFilter(),
                HighPassIIR(hft_hz, HP_FILTER_ACQ_FRQ_HZ),
                LowPassIIR(lft_k1),
            ],
        )

    # ---- internals ----

    @staticmethod
    def _apply_chain(s_count, valss, cut_len, *, chain):
        # Per-channel application
        tmp_vals = [[], [], [], []]
        for i in range(s_count):
            series = valss[i]
            for f in chain:
                series = f.apply(series)
            tmp_vals[i] = series

        # Cross-channel absolute sum
        # Width = the shortest non-empty channel; iterate up to that length.
        if s_count == 0 or not tmp_vals[0]:
            return [], tmp_vals
        out_vals = []
        for i in range(len(tmp_vals[0])):
            s = 0.0
            for j in range(s_count):
                if i < len(tmp_vals[j]):
                    s += math.fabs(tmp_vals[j][i])
            out_vals.append(s)

        # Front-trim to cut_len
        if len(out_vals) > cut_len:
            del out_vals[0:(len(out_vals) - cut_len)]
        for i in range(s_count):
            if len(tmp_vals[i]) > cut_len:
                del tmp_vals[i][0:(len(tmp_vals[i]) - cut_len)]
            # Upstream takes abs() of tmp_vals on the way out.
            tmp_vals[i] = [abs(v) for v in tmp_vals[i]]

        return out_vals, tmp_vals
