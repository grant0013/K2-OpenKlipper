# K2 OpenKlipper v0.4.9-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Fix: print preview missing on the touchscreen

The screen asked Moonraker for the print's thumbnail once, the moment the
print started. When the slicer uploads a file and starts it straight away,
Moonraker can still be reading the file then, and the screen showed no
preview for the whole print. It now tries again every 15 seconds for 5
minutes, and writes the reason to `/tmp/harkscreen.log` if a file has no
thumbnail.

Everything from v0.4.8-alpha.1 (one box at a time on the screen, K2 Plus
motor warning fix) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
