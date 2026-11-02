# K2 OpenKlipper v0.4.18-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Faster CFS colour changes

- **Loading the new colour is up to about 10 s quicker per change.** During
  the load the printer waited up to 15 s for a reply the CFS never sends
  while it feeds, even after the filament had reached the toolhead. It now
  moves on as soon as the toolhead sensor sees the filament.
- **A slow feed no longer stops the print.** If the filament took longer
  than about 18 s to reach the toolhead (long or tight PTFE tube, a heavy
  spool), the load failed. It now waits up to the normal 90 s limit.

## Tip: shorter purges

The printer now purges what OrcaSlicer's flush volumes ask for. If you
raised Orca's **flush multiplier** to stop colour bleed on older versions,
set it back to 1 and re-slice: the file's flush volumes already include
the multiplier, so a 3 purges about 9 times Orca's normal amount (every
change hits the 360 mm limit).

Everything from v0.4.17-alpha.1 is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
