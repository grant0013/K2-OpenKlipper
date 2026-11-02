# K2 OpenKlipper v0.4.18-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Power-loss recovery

If the power goes off mid-print, the printer can carry on when it comes
back, as stock firmware could. The touchscreen shows **Print interrupted**
with **Resume** and **Discard**. Resume finds Z again from the bottom
switches (it never taps the nozzle on your part), heats up, raises the bed
clear, purges at the chute and continues from the last checkpoint (up to
10 seconds may print twice). Check the part is still stuck down first.
This is new: stay with the printer for your first resume.

## The CFS tries a failed load again

A load that fails (a snag, a missed grip) is now rewound and retried up to
twice by itself before a colour change pauses. Each slot keeps a record of
its loads and failures, shown in the slot's menu on the touchscreen, and
the printer warns you when one slot keeps failing.

## Less purge waste

The printer now uses OrcaSlicer's own purge length for every colour change,
which includes what Orca already flushes into infill, objects or support.

## Cutter calibration without the knife sensor

`CFS_CUTTER_CALIBRATE METHOD=STALL CONFIRM=1` finds the cutter position
with the X motor, for toolheads whose knife sensor never changes. It rams
the cutter repeatedly: stay with the printer.

## A failed colour change no longer ends the print

If a CFS colour change fails (a tangle, a spool that won't turn, a chewed
filament end), the print now pauses at the purge chute with the nozzle hot,
like stock firmware, instead of stopping. The screen and console say which
slot and why. Sort the slot out, then press **Resume**: the printer tries
the change again and carries on printing. If it fails again it simply
stays paused. A slot that doesn't feed is also retried once automatically
before it ever comes to that.

## Old filament cleared at its own temperature

If the last print left a hotter filament in the nozzle (PETG before a PLA
print), the printer now heats to that filament's temperature to cut and
purge it out, then goes back to the new print's temperature. Before, it
was pushed out at the cooler temperature and could leave the nozzle
partly blocked.

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
