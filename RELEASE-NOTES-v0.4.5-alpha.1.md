# K2 OpenKlipper v0.4.5-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Notices on the touchscreen

- **Update available:** while the printer is idle, the screen offers a new
  version with **Update now** and **Later**. Later puts that version off for
  24 hours; the Update row on the Machine page still works any time.
- **Print stopped:** a print that ends on an error shows the reason.
- **During a print:** safety-watch pauses, filament runouts and other error
  messages show in a box with OK. They wake the screen and keep it on until
  OK is pressed. While idle, only safety-watch and chamber-heater messages
  pop up.
- **Klipper shutdown:** the not-ready page shows Klipper's own reason.

Everything from v0.4.4-alpha.1 (stock-style cancel and print end, no
re-homing between prints, idle settings, network announce) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
