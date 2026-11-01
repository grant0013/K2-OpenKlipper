# K2 OpenKlipper v0.4.11-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## K2 run from your own PC: the touchscreen updates itself

If Klipper runs on a separate PC ([docs/EXTERNAL-HOST.md](docs/EXTERNAL-HOST.md)),
updates replace the files on the PC. The touchscreen program runs on the K2
itself, so it stayed on whatever was copied at setup and missed every screen
fix since.

From this release the screen keeps itself in step: while the printer is idle
it compares the version the PC runs with its own, downloads that release's
screen program from GitHub, checks it, keeps the old copy and restarts. If a
new copy fails to start, the old one comes back and that release is not
fetched again. It only ever moves forward.

**One-time step for an existing PC setup:** copy this release's
`harkscreen/harkscreen.py` and `VERSION` onto the K2 as described in
docs/EXTERNAL-HOST.md (K2 side, step 6). After that it updates itself.

Printers running everything on the K2 itself are not affected: their update
already includes the screen.

Everything from v0.4.10-alpha.1 (colour-change purge from the slicer's flush
volumes) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
