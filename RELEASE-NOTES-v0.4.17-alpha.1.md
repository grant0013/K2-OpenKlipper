# K2 OpenKlipper v0.4.17-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Touchscreen fixes

- **Time zone picker no longer freezes the screen.** Setting a time zone now
  works the way Creality's own screen did it, in the background, so the
  screen stays responsive.
- **One failing button can't stop the screen any more.** The error goes to
  the screen's log and everything else keeps working.
- The chamber target under the Chamber gauge updates as soon as it changes.

Everything from v0.4.16-alpha.1 (Eject USB, chamber target on Status) is
included.

Full list in [CHANGELOG.md](CHANGELOG.md).
