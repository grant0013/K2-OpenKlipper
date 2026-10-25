# Pressure advance from the nozzle strain gauge

The K2's nozzle sits on a strain gauge (used as the Z probe). While
extruding, the same gauge reads melt pressure in the hotend. `PA_CAL_RUN`
uses it to measure pressure advance directly, in the style of CNC Kitchen's
PATuner. No test print or camera is needed: it extrudes over the purge chute.

## Use

```
PA_CAL_RUN SLOT=D            ; measure and report, save nothing
PA_CAL_RUN SLOT=D AUTO=1     ; also save the result to slot D if it passes
```

The touchscreen slot sheet has a **Calibrate PA** button on every loaded slot.
It runs the AUTO version, loading that slot first when it isn't the one in
the nozzle (including after a restart, when no active slot is known). When it
finishes, the screen shows the result (saved PA, or why it was not saved) in a
box that stays up until OK is pressed. Saved values live in the slot record
(`hark_cfs_slots.json`) and are applied by `CFS_SLOT_APPLY` whenever the slot
is selected (T0-T3), so they persist across prints and restarts. The value is stored with the CFS slot record (`hark_cfs_slots`) and
applied whenever that slot is selected. Re-run it for each new reel.

Optional parameters: `TEMP`, `KS` (PA values, default `0.02` to `0.14`),
`SLOW`/`FAST` (mm³/s, default 2 / 12), `CYCLES` (default 8), `NAME`.

## What it does

1. References Z on the bottom switch if needed, homes X/Y only (no Z probing,
   so a part left on the bed is fine) and moves to the purge chute.
2. Heats to the slot temperature, **arms the CFS feed** and primes. Two
   unlogged warm-up cycles follow.
3. Logs the gauge while the head wiggles 14 mm over the chute, stepping flow
   slow → fast → slow 8 times per PA value.
4. Writes `config/pa_logs/<name>_{pressure,extruder,marks}.csv`, analyses on
   the printer and prints a table plus the recommended PA.
5. Cools and wipes the nozzle.

About 5 minutes and roughly 0.5 m of filament.

## Method

- **Signal.** The gauge rests at about -1.6 million counts with roughly 200
  counts of noise. A 2 → 12 mm³/s PETG flow step is about 210,000 counts,
  giving a signal-to-noise ratio of 200-400.
- **Sampling.** The nozzle MCU fills a 64-sample buffer (0.78125 ms per
  sample, 50 ms) and only hands it over once full. Each read returns 7-16
  samples, so the buffer is read in chunks by index, before stopping the
  stream (stopping first empties it). Every sample carries its MCU clock tick,
  so it lands exactly on Klipper's print-time axis. A read cycle takes about
  170 ms, so about 29% of the time is covered.
- **Gaps.** Each PA value repeats its flow steps 8 times, and the samples
  around every step are overlaid by time since the step (equivalent-time
  sampling).
- **Criterion.** For a lagging nozzle, the net volume error after an up step
  minus that after a down step is linear in PA and crosses zero at the right
  value. A straight line through all tested PA values gives the result
  (R² typically above 0.97). The CNC Kitchen no-undershoot rule is reported
  alongside.
- **Non-linear melt.** PETG pressure is far from proportional to flow (the
  slow level reads about 70% of a line through zero), so the measured slow
  and fast pressure levels are used directly. A line through zero biased PA
  high (0.12 instead of 0.09).
- **Quality gates.** Signal-to-noise at least 8, a flow step of at least
  20,000 counts, little head motion on the gauge, enough sample coverage, a
  linear fit and a result inside the tested range. Only then does AUTO save.

`tools/pa/sim_pa.py` simulates gappy gauge logs with a known answer;
`analyze_pa.py` runs the same analysis on a Mac and plots the step responses.

## Validation

Creality Hyper PETG at 250 °C measured **0.092**. Printed check, three boxes
at 200 mm/s outer walls:

- PA 0.05 bulged at corners.
- PA 0.085 gave the cleanest corners and seams.
- PA 0.10 starved at line ends.

A second test showed smoothing 0.06 beats 0.038 at the same PA. Inner walls
above about 18 mm³/s starve on this hotend whatever the PA, so keep PETG flow
caps at 16-18.

## Gotchas

- Without `CFS_FEED_START` the extruder pulls the filament off the spool
  against the CFS take-up tension and slips within a second at 5 mm/s.
- `pressure_advance_smooth_time` is printer-wide in Klipper; 0.06 worked
  better than 0.038 on this machine.
