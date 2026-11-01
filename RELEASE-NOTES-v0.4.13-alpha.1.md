# K2 OpenKlipper v0.4.13-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Klipper fix: small curves printed as straight lines

Klipper turns each arc (G2/G3) in the G-code into straight pieces of one fixed
length, and it rounded the number of pieces down. Any arc shorter than two
pieces was printed as a single straight line across the curve: a small half
circle (a fillet, the curve of a letter, a small hole) could miss by its whole
radius. We replayed 564,000 arcs from six real K2 prints through Klipper's own
arc code: the worst miss was 0.30 mm, nearly a line width.

Arcs are now cut by accuracy: no piece strays more than 0.01 mm from the true
curve, small curves get short pieces and large curves long ones. On the same
prints that is 30 times more accurate with 11% fewer moves, which is also
lighter on the K2's processor. The bug is in upstream Klipper too.

If you set `[gcode_arcs] resolution` in printer-overrides.cfg, remove it: it
now caps the longest piece and would undo most of the gain.

## Touchscreen clock follows your time zone

The "done about" time on the touchscreen used UK time. It now uses the time
zone set in Creality's settings (`timezone: auto`), and updating switches the
old default over.

Everything from v0.4.12-alpha.1 (OrcaSlicer previews on the K2) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
