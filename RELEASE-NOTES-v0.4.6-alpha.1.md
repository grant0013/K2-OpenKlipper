# K2 OpenKlipper v0.4.6-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Your print on the touchscreen

The Status page shows the slicer's thumbnail of the print. It starts as a
faint ghost and fills in with colour from the bottom as the print climbs,
with a shimmering line at the layer being printed. It follows the real Z
height against the model's height, and shows in full colour when the print
completes. Files sliced without a thumbnail keep the old layout.

## Smoother screen

- Boxes (confirmations, update and error notices, the slot menu, results)
  fade in and out.
- Error notices pulse their warning icon.

## Everything fits

Every page was checked with the printer's own font: long filament, file and
network names end in "…" instead of running off their tile or under buttons,
CFS slot names use two lines, and button labels shrink to fit.

Everything from v0.4.5-alpha.1 (update and error notices on the screen) and
v0.4.4-alpha.1 (stock-style cancel and print end, no re-homing between
prints, idle settings, network announce) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
