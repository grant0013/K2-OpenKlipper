# Creality K2 open Klipper stack

This repository snapshot runs upstream Klipper on the Creality K2 while
preserving the machine-specific MCU protocols needed for PRTouch, Z alignment,
motor RS485 control, fan feedback, and CFS operation.

Version v62 is the first snapshot validated through a real OrcaSlicer upload
and first-layer print using a dedicated open control plane:

- Klipper API socket: `/tmp/klippy_uds`
- Moonraker: port 7126
- Mainsail 2.18.2: port 4409
- Legacy Fluidd/Moonraker retained for rollback: ports 4408/7125

Creality `master-server` and `Monitor` are disabled because both can issue
printer-control commands. The stock touchscreen control plane is consequently
unavailable. Passive Creality Wi-Fi, camera, audio, app, web, and upgrade
services are left intact.

## Current validation

The live K2 has completed:

- X/Y/Z homing with the open PRTouch implementation
- nozzle wipe and second precision PRTouch Z home
- adaptive bed mesh
- post-mesh movement to the purge bucket
- CFS print-start purge
- transition into the sliced first layer
- more than 10% of the current Benchy without MCU timing errors or toolhead
  stalls at the saved validation point

This remains experimental printer firmware. Supervise initial prints, keep the
stock rollback path available, and do not switch Klipper builds while a job is
printing or paused.

## OrcaSlicer

Use:

```gcode
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] TOOL=[initial_no_support_extruder]
```

Replace Creality's legacy **Change filament G-code** with:

```gcode
G90
G1 Z{max(z_after_toolchange, 0.6)} F600
```

The full explanation is in `config/ORCA-K2-PROFILE.cfg`.

## Repository layout

- `config/` - K2 configurations and Orca profile notes
- `klippy/` - pinned Klipper host code plus K2 integrations
- `system/` - Mainsail, Moonraker service/configuration, compatibility patch,
  and Creality service isolation
- `tools/` - release deployment helper
- `OPEN-STACK.md` - architecture, fixes, backups, rollback, and live evidence
- `K2-BUILD-MANIFEST` - exact upstream and integration commit provenance

## Important limitation

The bundled Moonraker is Creality's firmware package. Its Klipper and
Moonraker self-updaters are deliberately disabled because the appliance lacks
their updater metadata, Git client, requirements files, and build toolchain.
The Mainsail web client can still update independently.

## License

The Klipper-derived code is distributed under GNU GPLv3; see `COPYING`.
Bundled third-party components retain their own upstream copyrights and
licenses. Exact pinned versions are recorded in `K2-BUILD-MANIFEST` and
`system/mainsail/release_info.json`.
