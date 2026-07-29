# K2 OpenKlipper v0.1.0-alpha.1

This is the first public developer preview of the open Klipper control stack
validated on our Creality K2 Plus.

## Working in this preview

- pinned upstream Klipper host with K2 integrations
- X/Y/Z homing and PRTouch hardware contact stop
- nozzle wipe, adaptive mesh, and purge-area parking
- CFS status, initial load, purge, steady feed, cutter, and unload work
- OrcaSlicer upload and start through Moonraker
- isolated Mainsail 2.18.2 on port 4409
- safe print cancellation and stock rollback path

The monitored Benchy passed 32% with stable heaters, no pause, no MCU timing
fault, and no reported toolhead stalls at the last validation point.

## Important warning

This is alpha software for experienced developers. It has been tested on one
K2 Plus. A full long print has not yet completed on v62. CFS electrical
details and use on non-K2 printers are not validated. Keep a recovery path,
supervise testing, and read the project status before deployment.

## Where help is needed

- testing on more K2 Plus hardware and firmware revisions
- completed long-print and recovery testing
- CFS electrical interface and cold-boot research
- generic, non-blocking CFS transport
- PRTouch pressure traces and hardware-edge research
- separation of reusable protocol code from K2 motion coordinates

Read `CONTRIBUTING.md` and use the repository issue templates when reporting
results.
