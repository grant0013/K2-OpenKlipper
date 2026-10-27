# Project status

Status at `v0.2.0-alpha.1`. The development printer is a Creality K2 (F021);
earlier notes calling it a K2 Plus were wrong.

## What works on the development K2 (F021)

| Area | Current result |
| --- | --- |
| Upstream Klipper host | Pinned commit in the build manifest |
| Cold boot | Motors woken by `k2_motor_bus` (X usually on the second pass); no stock motor init needed |
| X/Y homing | Closed-loop stall homing |
| Z reference | PA15 bottom switch at every print start, then a strain-gauge tap (within 0.06 mm of the reference) |
| Adaptive mesh | Validated, with a contamination guard |
| CFS | Load, purge, unload, idle slot change, mid-print tool change, ERR8 retry, pre-loading take-up |
| Full prints | Multiple completed jobs, including PETG functional parts and test towers |
| Pressure advance | Strain-gauge measurement validated by printed comparison (PETG 0.085) |
| Input shaping | Head accelerometer via `lis2dw_k2` (X mzv 54.8 Hz, Y mzv 45.4 Hz on F021) |
| Touchscreen | Hark Screen in daily use |
| Camera | MJPEG in Mainsail; AI failure detection works from it |

## Known limitations

- One printer and one CFS firmware combination tested. K2 Pro and K2 Plus are
  untested.
- The board MCUs run Creality firmware. Klipper warns about deprecated
  features (`STEPPER_STEP_BOTH_EDGE`, `spi_set_sw_bus`); the fallbacks work
  today but could be removed upstream.
- The motor notch filters and ripple compensator are locked in the motor
  firmware.
- Klipper and Moonraker self-update are disabled: updates are built and
  deployed deliberately (see `OPEN-STACK.md`).
- Runout refill was tested with real runouts on 2026-10-27 (slot A to slot C,
  1 min 38 s pause). Power-loss resume and multi-box CFS still need
  structured testing.
- The bottom-switch reference takes about 50 s per print; its speeds are
  conservative.

## Release gates

### Next alpha

- complete a full Benchy and a longer functional print
- capture restart, cancel, filament-runout, and CFS error recovery
- clean up active versus fallback PRTouch configuration
- add replayable protocol fixtures from sanitised logs
- remove synchronous serial reads from any recurring CFS path

### Beta

- validate on K2 Pro / K2 Plus units and more firmware revisions
- publish CFS electrical interface findings
- split CFS transport, device state, and K2 motion adapters
- document a recovery-tested install and rollback flow
- add automated configuration and protocol checks

### Stable

- multiple completed long prints without timing faults
- validated failure handling for heaters, runout, CFS jams, and MCU restarts
- repeatable PRTouch results across nozzle and bed conditions
- release artifacts reproducible from source

## Evidence standard

When reporting a result, include:

1. printer model and hardware revision if known
2. Creality and CFS firmware versions
3. repository commit and configuration changes
4. exact command or sliced file phase
5. relevant, sanitised Klipper and Moonraker log excerpts
6. whether motion, heaters, CFS, and recovery were physically supervised

Never publish tokens, passwords, Wi-Fi details, device serial numbers, or
private network information.
