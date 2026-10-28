# K2 OpenKlipper v0.4.1-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021). Supervise prints and
keep the rollback path. Update from v0.4 or v0.3 with the Update button in
Mainsail or on the touchscreen (your `printer-overrides.cfg` is kept), or
install with the one-line installer in [docs/INSTALL.md](docs/INSTALL.md).

Everything new here is off by default. Turn it on in
`printer-overrides.cfg`; the settings below are the ones used on our K2.

## Pressure advance every time a slot loads

```
[gcode_macro _PA_AUTO]
variable_on_load: 1          # CFS_LOAD_SLOT and LOAD_FILAMENT
variable_on_print_start: 1   # a print start that loads a different slot
```

After a load the nozzle stays over the chute and the strain gauge measures
pressure advance (about 90 s). A plausible result is saved to the slot; a
failed one is reported and the load carries on. Not during a print.

The stationary value reads lower than printing needs, by filament type. A
printed chevron pattern (`tools/pa/pa_pattern.py`) picked PLA 1.34 times
and PETG 1.13 times the measured value on our printer, so the save can
scale it per material:

```
[gcode_macro _PA_STATIC_SAVE]
variable_scale_material: {'PLA': 1.34, 'PETG': 1.13}
```

Print the pattern for your own filaments to set yours.

## Fixes

- `PA_CAL_STATIC` no longer leaves the nozzle hot when the test fails (new
  `RUN_SAFE` command runs the cleanup either way).
- The PA test pulls the filament back 2 mm afterwards, so the tip does not
  weep after the wipe.

## Stronger nozzle wipe

```
[k2_cfs]
clean_speed: 300
clean_circle_speed: 250
clean_accel: 10000
clean_brush_strokes: 4
```

Four full-length strokes across the silicone brush after Creality's circles,
faster. On our printer it took off hardened filament the stock wipe left.

## First layer per material

After the K2 Plus firmware's method: a per-material offset added to
`hot_z_comp` (the material comes from the loaded slot) and its nozzle growth
rate. Ours, which cured a slight elephant foot on PLA:

```
[gcode_macro START_PRINT]
variable_hot_z_rate: 0.00083
variable_hot_z_ref_temp: 220
variable_hot_z_material: {'PLA': -0.01}
```

Full list: [CHANGELOG.md](https://github.com/grant0013/K2-OpenKlipper/blob/main/CHANGELOG.md).
