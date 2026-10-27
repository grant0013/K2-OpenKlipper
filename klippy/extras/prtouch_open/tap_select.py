# tap_select.py - choose a consistent set of PRTouch taps
#
# Part of K2-PRTouch - an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Portions adapted from Jacob10383's K2 Plus custom firmware
# (https://github.com/Jacob10383/k2-plus-custom-firmware, extras/prtouch.py), GPLv3.
# Distributed under the GNU GPLv3 license.
"""Pure helpers for multi-tap selection and hot-nozzle Z compensation.

No Klipper imports, so the functions can be checked offline with synthetic
tap lists (see tests/test_prtouch_tap_select.py).

Tap selection follows the K2 Plus firmware: keep tapping until the tightest
`samples` taps inside a sliding window of the last `samples + allowed_noisy`
taps agree within `tolerance`, or give up after `max_touches` taps. With
samples=1 the first tap is accepted, and with max_touches == samples and
allowed_noisy=0 it is the plain "N taps, abort on spread" check.
"""

import statistics


class TapSelectError(Exception):
    """No acceptable set of taps was found within max_touches."""

    def __init__(self, message, collected):
        Exception.__init__(self, message)
        self.collected = list(collected)


def sample_range(values):
    return max(values) - min(values)


def best_subset(values, size):
    """Return the `size` values with the smallest range, sorted, or None."""
    if size < 1 or len(values) < size:
        return None
    values = sorted(values)
    start = min(
        range(len(values) - size + 1),
        key=lambda i: values[i + size - 1] - values[i])
    return values[start:start + size]


def check_tap_settings(samples, tolerance, max_touches, allowed_noisy):
    if samples < 1:
        raise ValueError("tap samples must be at least 1")
    if tolerance < 0.:
        raise ValueError("tap tolerance must not be negative")
    if max_touches < samples:
        raise ValueError("tap max_touches must be at least samples")
    if allowed_noisy < 0:
        raise ValueError("tap allowed_noisy must not be negative")


def collect_taps(tap, samples, tolerance, max_touches, allowed_noisy,
                 between=None):
    """Tap until a consistent subset is found.

    tap(collected) performs one tap and returns its contact Z. It receives
    the taps so far, so the caller can bound the next search. between() runs
    after every tap that is followed by another one (the lift).

    Returns (median_z, collected, best). Raises TapSelectError when no
    subset agrees within tolerance after max_touches taps.
    """
    check_tap_settings(samples, tolerance, max_touches, allowed_noisy)
    window_size = samples + allowed_noisy
    collected = []
    best = None
    for attempt in range(1, max_touches + 1):
        collected.append(tap(list(collected)))
        window = collected[-window_size:]
        best = best_subset(window, samples)
        if best is not None and sample_range(best) <= tolerance:
            return statistics.median(best), collected, best
        if attempt < max_touches and between is not None:
            between()
    if best is None:
        best = best_subset(collected, min(samples, len(collected)))
    raise TapSelectError(
        "PRTouch could not find %d taps within %.4fmm after %d touches "
        "(tightest %d spread %.4fmm): %s"
        % (samples, tolerance, len(collected), len(best),
           sample_range(best), ", ".join("%.6f" % z for z in collected)),
        collected)


def hot_z_comp(base_comp, rate_mm_c, print_temp, ref_temp):
    """First-layer Z compensation (mm, positive lowers the nozzle).

    base_comp is the START_PRINT hot_z_comp tuned for printing at ref_temp
    after the 140C Z tap. The K2 Plus firmware models the hot nozzle as
    growing toward the bed by rate_mm_c per degree, which needs the nozzle
    RAISED as the print temperature goes up, so each degree above ref_temp
    takes rate_mm_c off the lowering. rate_mm_c=0 returns base_comp.
    """
    return base_comp - rate_mm_c * (print_temp - ref_temp)
