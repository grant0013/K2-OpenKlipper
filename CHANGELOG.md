# Changelog

## Unreleased (since v0.2.0-alpha.1)

### Updates and settings

- Updates from GitHub releases only (tested versions, never branches): a
  dialog in Mainsail with **Update now**, an **Update** row on the
  touchscreen's Machine page, `OKL_UPDATE_CHECK` / `OKL_UPDATE CONFIRM=1`
  (`[hark_update]`), and `install.sh --update`. Never offers an older version.
- The configuration is now `k2-openklipper.cfg` (managed, refreshed from the
  installed version on every start) plus `printer-overrides.cfg` (this
  printer's settings, loaded last, never touched by updates). The first
  update moves a v0.2 install's settings across. SAVE_CONFIG blocks in
  printer.cfg are kept.
- `creality-update.sh`: saves your settings to the G-code folder and returns
  the printer to stock for a Creality firmware update; the installer restores
  them afterwards.

### Stock features

- Runout auto-refill: `CFS_RUNOUT_REFILL` loads another slot with the same
  material and colour from the runout pause and resumes; if none matches, or
  anything fails, the print stays paused as before.
- Automatic chamber fan: the exhaust fan vents above the chamber target (35C,
  stock's watermark) while printing; M141/M191 set the target; an explicit
  M106 P1 / M107 P1 wins for that print.
- Creality's Fluidd page (4408) talks to the open stack's Moonraker.
- No nginx access logs: on the K2 they live in RAM, and a camera-polling tab
  filled 222 MB overnight.

### Installer

- `install.sh` / `uninstall.sh`: one-command install next to the stock
  firmware with full backups, and one command back to stock
  ([docs/INSTALL.md](docs/INSTALL.md)). Tested end to end on a stock K2
  (install about a minute, reboot, test print, uninstall, clean reinstalls).
- Asks whether the stock extruder or a Phaetus DXC2 is fitted and sets the
  post-cut pull length (10 or 20 mm) and cutter depth (the printer's own
  Creality calibration for stock; at least -8.0 for the DXC2). The shipped
  config now defaults to the stock extruder.
- Waits for Klipper after install and clears a main board left shut down by
  stock Klipper.
- Edits the printer's own `/etc/init.d/app` in place (removes master-server,
  Monitor and upgrade-server from its start list) instead of shipping a
  modified copy of Creality's script; `system/app.init.mainline` is gone.
- Warranty note in the install guide.

### Start sequence

- The cool-wipe no longer parks at the far-right return point between its
  four wipes (`NOZZLE_WIPE PARK=0`); the head stays over the chute.

### Klipper on a separate PC (external host)

- Klipper and Moonraker can run on any Linux PC, connected to the K2's
  mini-USB port. The K2 board turns into a three-port USB serial gadget and
  relays its main MCU, nozzle MCU and motor/CFS bus (`host/`,
  [docs/EXTERNAL-HOST.md](docs/EXTERNAL-HOST.md)). klippy went from about 108%
  CPU on the K2 to a median 5% of one core on a Celeron host; the first
  7.5-hour print had zero "Timer too close" and zero invalid bytes.
- Automatic reconnect when the K2 reboots or the cable is replugged.
- The K2's internal camera works on the host with a USB-A lead
  (`k2-camera.service`, ustreamer MJPEG, 15 fps).

### Touchscreen

- Generic default look: the Klipper logo and Klipper's colours, header
  "K2 OpenKlipper". The Hark Tech look moved to `harkscreen/themes/hark-tech.cfg`.
- [docs/SCREEN-CUSTOMISE.md](docs/SCREEN-CUSTOMISE.md): your own name, logo,
  colours, font and pages, edited in Mainsail and applied live.
- A mistyped number or a broken line in `harkscreen.cfg` no longer stops the
  screen: it falls back to the default or keeps the last good settings.

### Print safety

- `[hark_safety]`: pauses when the hotend or part fan is commanded on but its
  tachometer reads zero, cancels on an X/Y motor controller fault, and pauses on
  an extruder controller fault (clog or jam). Stock firmware did this in
  master-server and motor_control, which the open stack does not run.
- Hotend `max_temp` back to the stock 350.
- `PAUSE` lifts the nozzle 2 mm and parks it off the part. A job left paused
  for 30 minutes turns the hotend off but keeps the bed and homing, so it can
  still resume.

### First layer and print start

- First-layer height correction `hot_z_comp` (0.04 mm): Z is probed with the
  nozzle at 140C but printed hotter. Stock PRTouch corrects this
  (`temp_non_linaer_c` 0.045); the open driver did not, and edge-to-edge first
  layers lifted.
- Faster start: the bed waits only while it is still heating (no M190 settle
  or cool-down wait), and mesh taps run at 3 mm/s. Tap speeds of 0.75, 3 and
  5 mm/s measured the same bed shape.
- Cold starts get an extra 90 s soak, because the plate edges warm after the
  centre.

### AI failure detection (`tools/ai-detect/`)

- Obico's failure model on every 10th second, with a local vision-language
  model (Qwen3-VL 4B on llama.cpp, CPU only) as a second opinion: it confirms or
  drops Obico warnings and takes its own look every 3 minutes. Runs on the
  host PC, alerts only by default.

### Colour changes

- Cleaner cuts: cut position X-8.0 at acceleration 5000; the CFS rewind now
  takes about 15 s instead of 24 s, and the "nozzle sensor still detects
  filament" stops are gone (23/23 clean on a two-colour test print).
- Automatic clearing of a snapped piece left under the nozzle sensor.
- The tool change no longer waits for a fully settled PID; travel at
  Creality's speeds. About 101 s per change, down from 113-131 s.
- Chute purge per change from the slicer's flush matrix (`{flush_length}`),
  run as Creality's pulsed flush blocks with a 5 s fan blast and a full wipe.

### Print start

- Nozzle cleaning copied from Creality Print's K2 profile, including its
  front-edge prime line.
- The adaptive mesh covers the prime tower when the slicer passes its
  position (`TOWER_X`/`TOWER_Y`/`TOWER_W`); the tower no longer comes loose.
- `BED_MESH_CHECK` stops a print on an implausible mesh.
- CFS load, unload and PA calibration take Z from the bottom switch, with no
  nozzle contact.

### Ideas from Kalico, as add-on modules (no core changes)

- `RELOAD_GCODE_MACROS`: reload macro bodies without a restart.
- `PID_PROFILE`: named hotend PID profiles, loaded per CFS slot material.
- `BED_MESH_CHECK`: deviation and slope limits on the probed points.

### Tools

- `tools/multicolour-test/`: a generated two-colour pumpkin test print.

## v0.2.0-alpha.1 - 2026-10-25

### New

- **Strain-gauge pressure advance calibration** (`PA_CAL_RUN`,
  `pa_pressure_log.py`, `pa_analysis.py`): per-slot PA measured over the purge
  chute, validated by a printed comparison. See `docs/PA-CALIBRATION.md`.
- **CFS slot records** (`hark_cfs_slots.py`): per-slot filament, temperatures,
  PA, flow and retraction, applied on slot selection and mirrored for
  OrcaSlicer.
- **Head accelerometer driver** (`lis2dw_k2.py`) for `SHAPER_CALIBRATE` on
  Creality's nozzle-board LIS2DW protocol.
- **Hark Screen** touchscreen UI and **harkcam** MJPEG camera for Mainsail.
- **Motor controller tooling**: `MOTOR_BUS_SEND READ_LEN/TIMEOUT` for
  telemetry, a full parameter map, scan and A/B test tools. See
  `docs/MOTOR-CONTROLLER.md`.

### Print start

- One heat cycle before probing: CFS load/purge at temperature over the chute,
  a cool-wipe, a clean precise Z tap, a bed-strip scrape and the mesh.
- PA15 bottom-switch Z reference on every print (`k2_z_align` now allows it
  until the first extrusion).
- Adaptive heat soak for jobs without a slicer tool.
- The swamp-and-drag purge line keeps a margin from the parts.

### CFS

- Idle pre-loading (spool take-up) restored after boot and failed loads.
- EXTRUDE_ERR8 retry, stale active-mask cleanup, a purge-tail retract and a
  fan blast before the wipe.
- `CFS_CHANGE_SLOT` and `CFS_LOAD_SLOT` for idle slot changes; firmware
  retraction set per slot.

### Tuning on F021

- Input shaper X mzv 54.8 Hz / Y mzv 45.4 Hz, extruder PID at 250 °C,
  minimum cruise ratio 0, `pressure_advance_smooth_time` 0.06.
- `[gcode_arcs] resolution: 0.5`. 0.1 starved the move queue on an arc-heavy
  organic model (MCU "Stepper too far in past"); 1.0 shows chord joints.

### Fixes

- Manual G28 no longer runs the in-print nozzle wipe; the in-print wipe is
  skipped around START_PRINT's own Z home.

## v62 - 2026-07-29

### Printer control

- Improved the experimental host PRTouch fallback classifier with contact
  hysteresis while retaining conservative entry detection and three-window
  persistence. The validated v62 motion stop remains the PA15-to-PC7 hardware
  edge through native Klipper trsync.
- Kept Creality's rough-home, thermal-settle, nozzle-clean, precision-Z-home
  sequencing before adaptive bed meshing.
- Moved to the purge bucket immediately after adaptive mesh so nozzle ooze
  cannot land on the measured surface.
- Corrected CFS steady-feed validation and failure cleanup.

### Scheduling and UI

- Restored Creality's factory-safe 1.0 mm arc resolution after a 0.1 mm setting
  caused dense move queues and a dual-MCU timing shutdown.
- Added isolated Mainsail 2.18.2 on port 4409 and Moonraker on port 7126.
- Kept the legacy Fluidd/Moonraker pair on ports 4408/7125 for rollback.
- Exposed all active open-stack configuration through Mainsail.

### Final print-start fixes

- Disabled the late firmware-incompatible Klipper updater construction that
  left Moonraker's connection task incomplete and caused 20-second
  `/printer/print/start` timeouts.
- Added the safe OrcaSlicer initial tool-change Z guard and removed the source
  of the pre-first-layer left-side bed scrape.

### Live validation

- Verified Moonraker ready with no warnings or failed components.
- Verified a safe nonexistent-file start probe reached Klipper in 0.061 s.
- Verified real Orca upload and print start.
- Verified homing, nozzle cleaning, second PRTouch home, adaptive mesh,
  purge-bucket parking, CFS purge, and first-layer printing.
- Benchy passed 32% with no pause, MCU timing error, or reported toolhead
  stall at the saved validation point.

### Known items

- A completed long print is still required for production qualification.
- Print-duration accounting includes startup/CFS activity incorrectly and may
  display an unrealistic value in the UI; motion and virtual-SD progress are
  unaffected.
- Creality's stock touchscreen control plane is unavailable while
  `master-server` remains disabled.
