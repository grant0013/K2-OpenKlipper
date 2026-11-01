# K2 OpenKlipper v0.4.15-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Pressure advance calibration saves print-ready values

**Calibrate PA** (touchscreen slot or spool menu, or `PA_CAL_STATIC`) measures
pressure advance with the nozzle's strain gauge over the chute, no test print
needed. That measurement reads lower than the value that actually prints
best: printed PA patterns put the difference at x1.34 for PLA and x1.13 for
PETG. Those corrections were only set on our test printer, so on other
printers Calibrate PA saved values 13-34% too low.

They are now the default. **Run Calibrate PA again** on your PLA and PETG
spools: values you saved before are not changed. Other materials save the
measured value (adjust with `variable_scale` if your printed tests say so).

If you tuned your own, they stay: in printer-overrides.cfg

    [gcode_macro _PA_STATIC_SAVE]
    variable_scale_material: {'PLA': 1.34, 'PETG': 1.13}

Everything from v0.4.14-alpha.1 (touchscreen controls, Settings, USB storage,
time-lapse, upstream Klipper fixes) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
