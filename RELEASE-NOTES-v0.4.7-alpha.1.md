# K2 OpenKlipper v0.4.7-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Fix: early colour changes stopped multicolour prints

A CFS colour change in the first couple of millimetres of a print (below
about Z 2.5 mm) stopped the print with:

    CFS move requires Z >= 4.500 (current 4.461)

Before it cuts the filament, the change lifts the nozzle to the safe purge
height (`purge_min_z`, 4.5 mm). It sent that height as a G-code Z, but the
safety check reads the real toolhead height. The hot-nozzle first-layer
offset (about -0.04 mm for PETG at 250C) left the nozzle just under 4.5 mm,
so the check refused the move. Changes higher up were not affected.

The lift now allows for the G-code offset. So do the lift and the return in
the filament-path retry. If you hit this error, update and reprint.

Everything from v0.4.6-alpha.1 (print preview and screen polish) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
