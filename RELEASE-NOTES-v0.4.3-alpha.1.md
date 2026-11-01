# K2 OpenKlipper v0.4.3-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021). Supervise prints and
keep the rollback path. Update with the Update button in Mainsail or on the
touchscreen (your `printer-overrides.cfg` is kept), or install with the
one-line installer in [docs/INSTALL.md](docs/INSTALL.md).

## The touchscreen shows what the printer is doing

During a print start, load, tool change, runout refill or calibration the
screen shows the current step (homing, heating, loading slot X and purging,
measuring pressure advance, Z tap, bed mesh, purge, prime line...) with a
timer, the earlier steps with their times and the total set-up time.

## Pressure advance from the screen uses the stationary test

**Calibrate PA** on the touchscreen and in Mainsail runs `PA_CAL_STATIC` with
the per-material scale, and the screen shows the result (saved value,
measured value, scale).

## Soft filaments (TPU, TPE)

Loads, the print start's prime-out, flush and prime line run soft filaments
at a flow cap so they don't buckle in the extruder gears, and the automatic
PA test is skipped for them (the slot keeps its saved value). Change the caps
in `printer-overrides.cfg`:

```
[gcode_macro _SOFT_FILAMENT]
variable_max_flow: {'TPU': 3.0, 'TPE': 2.0}   # mm3/s
```

## Filament sync with stock OrcaSlicer

Slots now carry OrcaSlicer's own filament id for Creality filaments, so
OrcaSlicer 2.5.0-beta and newer pick "Hyper PLA @K2-all" instead of "Generic
PLA". Setup in docs/INSTALL.md, "Filament sync with OrcaSlicer".

## Also

- **Chamber fan:** `variable_prefer_material: 1` in `[gcode_macro
  _CHAMBER_FAN]` uses the material table even when the slicer sends a chamber
  temperature (OrcaSlicer's K2 filaments all send `M191 S35`).
- **Prime line:** the nozzle lifts 1 mm after the Z0 wipe, so the slicer's
  first travel no longer drags it across the plate.
- **Mainsail updates come from the Hark releases only** (grant0013/mainsail).
  An update moves an existing install off stock Mainsail, which would
  replace the Hark build.
- **External Klipper host:** each release carries `k2-openklipper-host.zip`,
  which Moonraker's Update Manager installs on the host. Setup in
  docs/EXTERNAL-HOST.md.
- Fixed: after a restart OrcaSlicer could see empty CFS slots (two slot
  updates raced at start-up).
- **K2 Plus (F008) beta config**, not yet run on a real K2 Plus: installs only
  with `OKL_K2PLUS_BETA=1`. Read docs/K2-PLUS-BETA.md first.

Mainsail Hark K2 edition revision 3 (v2.18.2-hark.3). Full list:
[CHANGELOG.md](https://github.com/grant0013/K2-OpenKlipper/blob/main/CHANGELOG.md).
