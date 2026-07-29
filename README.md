# K2 OpenKlipper

An experimental, community-oriented upstream Klipper stack for the Creality K2
Plus. It preserves the K2-specific MCU protocols needed for PRTouch, Z
alignment, motor RS485 control, fan feedback, and CFS operation.

The first public snapshot, `v0.1.0-alpha.1`, is based on the live-tested v62
build. It is for developers and experienced K2 owners who can recover their
printer if testing fails. It uses this dedicated open control plane:

- Klipper API socket: `/tmp/klippy_uds`
- Moonraker: port 7126
- Mainsail 2.18.2: port 4409
- Legacy Fluidd/Moonraker retained for rollback: ports 4408/7125

Creality `master-server` and `Monitor` are disabled because both can issue
printer-control commands. The stock touchscreen control plane is consequently
unavailable. Passive Creality Wi-Fi, camera, audio, app, web, and upgrade
services are left intact.

## Project status

The live K2 has completed:

- X/Y/Z homing with the open PRTouch implementation
- nozzle wipe and second precision PRTouch Z home
- adaptive bed mesh
- post-mesh movement to the purge bucket
- CFS print-start purge
- transition into the sliced first layer
- 32% of the current Benchy without MCU timing errors or toolhead
  stalls at the saved validation point

This is an alpha release, not production firmware. A full long-duration print
has not yet completed on v62. CFS works on the development K2, but its
electrical interface and portability to other printers are not yet validated.
PRTouch currently stops Z through the K2 hardware PA15-to-PC7 signal and native
Klipper trsync; the in-tree host classifier is experimental fallback work.

See [PROJECT-STATUS.md](docs/PROJECT-STATUS.md) for the validation matrix and
release gates.

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
- `docs/PROJECT-STATUS.md` - readiness and validation matrix
- `docs/CFS-PROTOCOL.md` - current CFS wire and state-machine findings
- `docs/PRTOUCH.md` - current PRTouch architecture and open questions
- `CONTRIBUTING.md` - safe ways to test and contribute

## Help wanted

- validation on other K2 Plus firmware and hardware revisions
- CFS electrical, addressing, and cold-boot findings
- separation of the generic CFS protocol from K2 motion and sensors
- non-blocking serial transport for CFS polling
- PRTouch repeatability data across clean and contaminated nozzles
- completed long-print and recovery testing

Use the issue templates and include exact versions, logs, and the phase being
tested. Do not publish credentials, serial numbers, Wi-Fi details, or other
private data.

## Important limitation

The bundled Moonraker is Creality's firmware package. Its Klipper and
Moonraker self-updaters are deliberately disabled because the appliance lacks
their updater metadata, Git client, requirements files, and build toolchain.
The Mainsail web client can still update independently.

## License

The Klipper-derived code is distributed under GNU GPLv3; see `COPYING`.
Bundled third-party components retain their upstream copyrights and licenses.
See `THIRD_PARTY.md`, `K2-BUILD-MANIFEST`, and
`system/mainsail/release_info.json`.
