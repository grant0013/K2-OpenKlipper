# K2 OpenKlipper v0.4.8-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Fix: false motor warning on every K2 Plus

Every K2 Plus warned "Motor check: ... calibration param 19 is 0.15 ... may
not be calibrated" for all four motors at start-up, and the touchscreen
showed it at the next print start. K2 Plus motors read 0.15 there with a
normal calibration offset (param 09), and home and probe accurately. The
warning now needs the offset itself to be near zero.

## Touchscreen: one box at a time

- Only one box shows at a time, most important first: a question you are
  answering, an error or safety message, a calibration result or pick, the
  slot menu, then an update offer. Boxes no longer stack.
- A pressure advance result clears when a print starts, or after 10 minutes.
  It used to stay on screen over the next print start.
- The heating rings and spinners of the page underneath no longer draw over
  a box.
- Messages from while the printer was idle (start-up lines, console typos)
  no longer pop up when the next print starts.
- The busy banner shows on the Status page only and fits its text; other
  pages show the work in the header.

Every page, printer state and box (924 combinations) was checked for
overlapping, clipped or off-screen text.

Everything from v0.4.7-alpha.1 (early colour changes no longer stop
multicolour prints) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
