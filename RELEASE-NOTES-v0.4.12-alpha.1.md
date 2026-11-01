# K2 OpenKlipper v0.4.12-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Fix: no print preview for OrcaSlicer files

The K2 runs Creality's own copy of Moonraker, which did not recognise
OrcaSlicer files. It read them as from an unknown slicer and skipped their
thumbnails, so neither the touchscreen nor Mainsail showed a preview (or the
slicer's time estimate). The installer now teaches it OrcaSlicer, as current
Moonraker does. The original file is backed up, and uninstalling restores it.

**Send your files again after updating:** files already on the printer keep
the details read when they arrived. Files sent after the update get the
preview on the screen and in Mainsail.

Everything from v0.4.11-alpha.1 (the touchscreen updating itself on a K2 run
from a separate PC) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
