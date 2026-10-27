# scrub.py - pure helpers for the strip-detecting nozzle scrub
#
# Part of K2-PRTouch - an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Portions adapted from Jacob10383's K2 Plus custom firmware (https://github.com/Jacob10383/k2-plus-custom-firmware, extras/prtouch.py), GPLv3.
# Distributed under the GNU GPLv3 license.
"""Pure helpers for PRTOUCH_SCRUB (prtouch_mainline.py).

No Klipper imports, so the functions can be checked offline (see
tests/test_prtouch_scrub.py).

The K2 Plus method: tap the strip at the line centre three times at the
normal pressure thresholds and three times with them scaled up (a firmer
press). A flexible tab gives way under the firmer press, so the median
difference between the pairs (the deflection) tells a tab from a solid
surface, and picks how deep the scrub presses. Both line ends are then
tapped, the nozzle runs start -> centre -> end -> centre -> start at the
measured surface minus that depth (following the measured slope), and the
ends are tapped again. Debris under the tip reads as a higher contact, so
passes repeat until the average end contact stops dropping.
"""

import random
import statistics

# The MCU sends min_hold/max_hold as %i. Keep scaled values inside the
# ranges cfg.py accepts for the unscaled options.
MIN_HOLD_LIMITS = (100, 50000)
MAX_HOLD_LIMITS = (100, 100000)

# Contacts along the scrub line that differ by more than this are not one
# strip (a missed edge, a blob, or the wrong coordinates).
LINE_MAX_SPREAD = 0.5


class ScrubError(Exception):
    """The scrub line measurements cannot be trusted."""


def _clamp(value, limits):
    return max(limits[0], min(limits[1], value))


def scaled_holds(min_hold, max_hold, add_hold, ratio):
    """MCU hold thresholds for a press `ratio` times firmer.

    Like the K2 Plus firmware, min_hold and max_hold scale and add_hold is
    sent unchanged. A ratio of 1 returns the configured values untouched.
    """
    if ratio <= 0.:
        raise ValueError("hold ratio must be above 0")
    if ratio == 1.:
        return min_hold, max_hold, add_hold
    scaled_min = _clamp(int(round(min_hold * ratio)), MIN_HOLD_LIMITS)
    scaled_max = _clamp(int(round(max_hold * ratio)), MAX_HOLD_LIMITS)
    return scaled_min, max(scaled_max, scaled_min), add_hold


def pick_y(y_min, y_max, rng=random):
    """A random Y inside the strip band, so wear spreads across it."""
    return rng.uniform(y_min, y_max)


def deflection(normal_zs, firm_zs):
    """Median |normal - firm| over the tap pairs, rounded to 6 decimals."""
    if not normal_zs or len(normal_zs) != len(firm_zs):
        raise ValueError("need matching, non-empty normal and firm taps")
    diffs = [abs(n - f) for n, f in zip(normal_zs, firm_zs)]
    return round(float(statistics.median(diffs)), 6)


def choose_depth(defl, threshold, tab_depth, no_tab_depth):
    """(tab detected, scrub depth) for a measured deflection."""
    tab = defl > threshold
    return tab, (tab_depth if tab else no_tab_depth)


def check_ends(z_start, z_end, max_spread=LINE_MAX_SPREAD):
    if abs(z_end - z_start) > max_spread:
        raise ScrubError(
            "scrub endpoints differ by more than %.3f mm (%.4f and %.4f)"
            % (max_spread, z_start, z_end))


def check_centre(z_start, z_centre, z_end, max_spread=LINE_MAX_SPREAD):
    if abs(z_centre - 0.5 * (z_start + z_end)) > max_spread:
        raise ScrubError(
            "scrub centre contact %.4f is more than %.3f mm from the ends "
            "(%.4f and %.4f)" % (z_centre, max_spread, z_start, z_end))


def round_trip(start, end, z_start, z_centre, z_end, depth):
    """Scrub waypoints (x, y, z): start, centre, end, centre, start.

    Each Z is that point's measured contact minus `depth`, so straight moves
    between the waypoints follow the strip's measured slope.
    """
    centre = (0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1]))
    a = (start[0], start[1], z_start - depth)
    c = (centre[0], centre[1], z_centre - depth)
    b = (end[0], end[1], z_end - depth)
    return [a, c, b, c, a]


def clear_z(z_start, z_centre, z_end, retract):
    """Height to travel along the line at, retract above its highest contact."""
    return max(z_start, z_centre, z_end) + retract


def run_passes(scrub_pass, measure_ends, z_start, z_centre, z_end,
               max_passes, shorten_done):
    """Scrub and re-measure until the end contacts stop dropping.

    scrub_pass(z_start, z_centre, z_end) runs one round trip and
    measure_ends() returns the new (z_start, z_end). The centre keeps its
    first measured offset from the ends' average. Returns
    (passes, shortened, z_start, z_centre, z_end); shortened is how far the
    average end contact dropped on the last pass.
    """
    if max_passes < 1:
        raise ValueError("max_passes must be at least 1")
    centre_offset = z_centre - 0.5 * (z_start + z_end)
    passes = 0
    shortened = 0.
    for passes in range(1, max_passes + 1):
        scrub_pass(z_start, z_centre, z_end)
        next_start, next_end = measure_ends()
        shortened = 0.5 * (z_start + z_end) - 0.5 * (next_start + next_end)
        z_start, z_end = next_start, next_end
        z_centre = 0.5 * (z_start + z_end) + centre_offset
        if shortened < shorten_done:
            break
    return passes, shortened, z_start, z_centre, z_end
