# PRTouch findings

## Active v62 path

The validated v62 Z-home and mesh operations use the K2's hardware contact
path:

```text
pressure sensors -> pressure MCU -> PA15 -> main MCU PC7 -> Klipper trsync
```

`prtouch_mainline.py` creates the PC7 MCU endstop and calls Klipper's native
`homing.probing_move()`. `prtouch_open/endstop.py` starts the pressure and step
capture session, then delegates motion stop timing to that MCU endstop.

The pressure MCU, not a Python polling loop, decides when to assert the edge
that stops Z.

## Probe safety and tap selection (10-27)

Ported from Jacob10383's K2 Plus custom firmware (GPLv3,
https://github.com/Jacob10383/k2-plus-custom-firmware). Both stacks give the
trigger decision to the nozzle MCU with the same `start_prtouch_pres`
parameters; these are host-side changes.

On by default (they only refuse or report probes that could not have
measured the bed):

- **Fail-closed arming** (`pres_arm_checks: True`). The step and pressure
  start commands already wait for their `ack_prtouch`; a non-zero `err` now
  stops the probe before any movement. After the dead-time warmup, PC7 must
  read "no contact", otherwise the probe is refused (a stuck or preloaded
  line would report the start height as the bed). After each probe PC7 must
  release within `pres_release_timeout` (0.25 s), otherwise a warning is
  printed and the next probe refuses to start. Motors off during a probe
  disarms it; a shutdown forgets the session.
- **No-trigger diagnostics** (`probe_fail_dump: True`). When a probe runs to
  its end without a contact edge, the MCU's 64-sample pressure buffer is read
  while still streaming and logged to `klippy.log` (summary on the console).
- **Bed-mesh contact plane** (`probe_plane_check: True`,
  `probe_arm_below_z: 0.45`, `probe_plane_retries: 1`). `PRTOUCH_HOME_Z`
  defines the contact plane as Z=0. While the Z coordinate frame is still
  the one it set (anything that redefines Z, such as `KZ_ALIGN_BOTTOM` or
  `SET_KINEMATIC_POSITION`, or motors off, clears it), a `BED_MESH_CALIBRATE` tap that triggers above Z=0.45 is a blob
  or a false trigger: it is re-probed once, then the mesh stops with an
  error. Real K2 meshes stay inside +-0.35 mm (`BED_MESH_CHECK`), so normal
  meshes are unaffected. `klippy.log` says at each mesh whether the check is
  on.

Off by default (change first-layer results or motion, so test first):

- **Multi-tap selection.** Tap until the tightest N taps among the last
  N + noisy agree within the tolerance, up to a maximum number of touches;
  the result is their median. Every tap still starts at the same Z, follow-up
  taps cannot search more than `home_followup_guard` (0.5 mm) below the taps
  so far, and the descent still stops at `probe_minimum_z` (-0.6).
  - Z home: `home_samples`, `home_samples_tolerance`, `home_max_touches`
    (0 = same as samples), `home_allowed_noisy`; per command
    `PRTOUCH_HOME_Z SAMPLES= SAMPLES_TOLERANCE= MAX_TOUCHES= ALLOWED_NOISY=`.
    With the defaults (1 tap) this is exactly the old behaviour; with
    `MAX_TOUCHES` = `SAMPLES` and no noisy taps it is the old
    "N taps, abort on spread".
  - Probe points: `probe_tap_samples` (1), `probe_tap_tolerance` (0.10),
    `probe_tap_max_touches`, `probe_tap_allowed_noisy`; per command
    `TAP_SAMPLES= TAP_TOLERANCE= TAP_MAX_TOUCHES= TAP_NOISY=` on `PROBE`,
    `PROBE_ACCURACY` or `BED_MESH_CALIBRATE`.
  - The K2 Plus ships 3 samples, 3 noisy, 10 touches, 0.010 mm. Single taps
    are a suspect for the ~0.05 mm start-to-start first-layer variation.
- **No-trigger retry** (`probe_no_trigger_retries: 0`): one retry from the
  original start Z after a clean miss, only for moves up to
  `probe_no_trigger_retry_max_distance` (2 mm, so mesh taps but not the
  5 mm Z home tap). Off because a miss means the nozzle already pressed to
  -0.6 without a trigger, and a retry presses again.

### Dead time (`pres_ded_tkms` -> `lmt_dead`)

We send `lmt_dead=128`. The K2 Plus firmware reads the option as
milliseconds and sends `round(128 / 0.78125) = 164`. The suffix `_tkms`
matches `pres_acq_tkms` (0.78125, a period in ms), which supports their
reading, but the MCU field is a bare `%u` and nothing in hand shows its unit.
If the MCU counts samples, ours is 100 ms of dead time and theirs 128 ms;
our warmup waits `(lmt_dead + 8)` samples (106 ms) before the move either
way, so our dead window always ends before Z moves. Unchanged until a
capture of what stock Creality firmware sends settles it.

### Hot-nozzle compensation sign

The K2 Plus `PRTOUCH_HOME PRINT_TEMP=` writes
`Z = z_offset - rate * (T_print - T_touch)` at the contact point. With a
hotter print the contact is labelled lower, so commanding Z=0 holds the
cold-measured tip `rate * dT` above the bed, and the hot nozzle grows down
into it: their term RAISES the nozzle for hotter prints. Our `hot_z_comp`
LOWERS it by a fixed 0.04 mm, tuned on prints (the stock firmware applied a
similar 0.045). So the opt-in `hot_z_rate` only scales around the tuning
temperature, in their direction, and is applied as a G-code offset rather
than written into homed Z, because our mesh is not zero-referenced and would
cancel it.

## Captured data

Pressure and step samples are still captured around probe moves. They are
useful for contact diagnostics, repeatability measurement, comparing clean and
contaminated nozzle behaviour, and developing a future portable classifier.

One clean single-tap observation on the development K2 was:

```text
PRTouch tap 1/1: contact Z=0.114063
PRTouch Z home: median 0.114063 spread 0.000000, retracted Z 4.885937
```

That is one observation, not a universal offset or quality threshold.

## Experimental host classifier

The repository retains a host amplitude/hysteresis probe implementation. It
uses a conservative entry threshold, a lower release threshold, and
multi-window persistence to reject isolated spikes.

It is not the active v62 motion-stop path. Changing host classifier settings
will not necessarily change the PC7 hardware trip force. The configuration
still needs cleanup so active hardware settings and experimental fallback
settings are clearly separated.

## Current operating lesson

Softened filament on the nozzle can preload bed contact and produce misleading
results. The validated print-start sequence therefore uses:

1. rough home
2. thermal settle
3. move to the rear silicone cleaner
4. nozzle wipe
5. precision Z home
6. adaptive mesh
7. immediate move to the purge area

The heated bed-drag cleaning routine used by Creality is not reproduced with
blind G-code. Its force-controlled behaviour remains a reverse-engineering
item.

## Help wanted

- identify how the pressure MCU derives and asserts PA15
- collect repeatability data across multiple K2 units
- compare clean nozzle, oozed nozzle, temperature, bed surface, and tap count
- map which configuration values influence the hardware edge
- provide sanitised pressure and step traces with exact test conditions
- determine whether a portable host classifier can safely replace the
  pressure-MCU decision

Do not test thresholds with an unattended printer. Keep travel short, speed
low, and an immediate power cutoff available.

## Reading the raw gauge stream (v0.2.0)

Used by `pa_pressure_log.py`:

- After `start_streaming`, the nozzle MCU fills a 64-sample buffer at
  0.78125 ms per sample and only exposes it once full (about 50 ms). Reads
  before that return zero ticks.
- `read_prtouch_pres oid is_src ch idx len` returns at most 7-16 samples per
  reply. Read from `idx` 0 onward until 64, while still streaming: stopping
  first empties the buffer.
- `ticks` decode (unsigned) to MCU clock values. Convert with
  `clock32_to_clock64` + `clock_to_print_time` for exact timestamps.
- Drop the first sample after a restart, which reads offset. Resting level is
  about -1.6M counts with roughly 200 counts of noise.
