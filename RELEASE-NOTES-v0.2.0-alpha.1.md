# K2 OpenKlipper v0.2.0-alpha.1

The open stack has moved from "it can start a print" (v0.1) to daily use on a
Creality K2 (F021): full prints, CFS jobs, its own touchscreen and camera, and
measured pressure advance.

Highlights:

- **Pressure advance measured by the nozzle strain gauge**, per CFS slot, from
  the touchscreen or `PA_CAL_RUN SLOT=x`.
- **A faster, cleaner print start**: bottom-switch Z reference, one heat cycle
  before probing, a clean-nozzle Z tap, then scrape and mesh.
- **CFS reliability**: pre-loading take-up, ERR8 retry, armed feed for
  sustained extrusion, idle slot changes.
- **Hark Screen** replaces the dead stock touchscreen; the **camera** works in
  Mainsail and with AI failure detectors.
- **Closed-loop motor documentation**: protocol, parameter map, telemetry,
  baseline measurements, and the FAL lockup warning.

Read before installing: [README](README.md),
[Project status](docs/PROJECT-STATUS.md), [OPEN-STACK.md](OPEN-STACK.md).
This is alpha firmware: supervise prints and keep the stock rollback.
