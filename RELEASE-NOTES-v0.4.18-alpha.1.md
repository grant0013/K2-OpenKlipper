# K2 OpenKlipper v0.4.18-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## A failed colour change no longer ends the print

If a CFS colour change fails (a tangle, a spool that won't turn, a chewed
filament end), the print now pauses at the purge chute with the nozzle hot,
like stock firmware, instead of stopping. The screen and console say which
slot and why. Sort the slot out, then press **Resume**: the printer tries
the change again and carries on printing. If it fails again it simply
stays paused. A slot that doesn't feed is also retried once automatically
before it ever comes to that.

## Faster CFS colour changes

- **Loading the new colour is up to about 10 s quicker per change.** During
  the load the printer waited up to 15 s for a reply the CFS never sends
  while it feeds, even after the filament had reached the toolhead. It now
  moves on as soon as the toolhead sensor sees the filament.
- **A slow feed no longer stops the print.** If the filament took longer
  than about 18 s to reach the toolhead (long or tight PTFE tube, a heavy
  spool), the load failed. It now waits up to the normal 90 s limit.

## PETG purge no longer curls into the nozzle

While purge filament came out, the part fan ran low (as on stock). The
K2's part fan blows from one side, so PETG cooled unevenly, curled back
up, stuck to the nozzle and could block the chute. PETG, PCTG, ABS, ASA
and PC now flush with the fan off; PLA and the rest are unchanged. The
short full-fan blast that snaps the strand off still runs. To change it,
in printer-overrides.cfg (0-255, matched on the start of the material):

    [gcode_macro _PURGE_FAN]
    variable_default: 30
    variable_material: {'PETG': 0, 'PCTG': 0, 'ABS': 0, 'ASA': 0, 'PC': 0}

## K2 Plus

- Three settings back to Creality's K2 Plus values, after nur00's review
  of the beta against his printer: the hotend PID, the bed heater's
  warm-up check (120 s) and the Z tilt travel speed. If you tuned your own
  PID with PID_CALIBRATE and saved it, yours is kept.

## Tip: shorter purges

The printer now purges what OrcaSlicer's flush volumes ask for. If you
raised Orca's **flush multiplier** to stop colour bleed on older versions,
set it back to 1 and re-slice: the file's flush volumes already include
the multiplier, so a 3 purges about 9 times Orca's normal amount (every
change hits the 360 mm limit).

Everything from v0.4.17-alpha.1 is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
