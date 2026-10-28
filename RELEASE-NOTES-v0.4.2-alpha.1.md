# K2 OpenKlipper v0.4.2-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021). Supervise prints and
keep the rollback path. Update with the Update button in Mainsail or on the
touchscreen (your `printer-overrides.cfg` is kept), or install with the
one-line installer in [docs/INSTALL.md](docs/INSTALL.md).

## Flow and retraction tests from the screen or Mainsail

Tap a filament, then **Flow test** or **Retraction test** (touchscreen slot
menu, Mainsail slot dialog), or run `CALIBRATE_FLOW SLOT=x` /
`CALIBRATE_RETRACTION SLOT=x`. The printer writes and prints the test with
that filament:

- **Flow test:** nine labelled pads around the filament's flow ratio.
- **Retraction test:** seven labelled pairs of pillars, 0.2 to 1.4 mm. Only
  the travel inside each pair uses its own retraction, so the pair with no
  strings shows the length you need.

When it finishes, a picker asks for the best one, on the touchscreen and on
the Mainsail CFS card (plus a Mainsail prompt). The pick is saved to the
filament, so OrcaSlicer and the next print use it. Works for the external
spool without a CFS too.

## Chamber fan by material

Without a slicer `M141`, the print start sets the chamber target from the
loaded filament: PLA and TPU 35C, PETG 40C, ABS, ASA, PC and PA 50C. The
exhaust fan vents 2C above it. Change the table in `printer-overrides.cfg`:

```
[gcode_macro _CHAMBER_FAN]
variable_material_targets: {'PLA': 35, 'TPU': 35, 'PETG': 40, 'ABS': 50, 'ASA': 50, 'PC': 50, 'PA': 50}
variable_end_vent: 180   # optional: vent for 3 minutes after each print
```

## Fixes

- **CFS lights stay white:** the driver talks to the CFS every 2 s instead of
  10 s. With the longer gap the CFS flashed red (not connected) during quiet
  stretches such as the mesh, until the next feed.
- **Touchscreen:** a restarted Hark Screen always switches the panel on (it
  could stay dark if the previous copy had put it to sleep).

Mainsail Hark K2 edition revision 3. Full list:
[CHANGELOG.md](https://github.com/grant0013/K2-OpenKlipper/blob/main/CHANGELOG.md).
