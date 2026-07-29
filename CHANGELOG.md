# Changelog

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
