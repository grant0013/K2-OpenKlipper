# K2 OpenKlipper v0.4.16-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Touchscreen

- **Eject the USB drive safely.** Settings > Storage has an **Eject** button
  next to Format. The printer moves its files, time-lapses and logs back to
  its own storage, writes everything out and tells you when it is **Safe to
  remove**. If Klipper's log was on the drive, Klipper restarts first (about
  a minute). Not while printing. The drive isn't used again until you unplug
  it and plug it back in.
- **Chamber target shown.** On a K2 Plus the Chamber gauge now shows the
  chamber heater's target under the temperature, like the nozzle and bed.

Everything from v0.4.15-alpha.1 (PA calibration saves print-ready values) and
v0.4.14-alpha.1 (Settings page, USB storage, time-lapse, temperature and fan
controls) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
