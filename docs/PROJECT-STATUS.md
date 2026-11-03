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

## Still only in Creality's firmware (pass of 3 Nov 2026)

Every stock G-code command, macro and Klipper module was compared with
ours. What is left, ranked by what it would do for prints:

1. **Bed heater power calibration (110 V mains).** Stock
   `BED_HEADER_POWER_CALIBRATION` heats the bed from 70 to 90 C, fits the
   slope, and below 0.45 C/s treats the printer as on 110 V: full power
   and the 110 V PID set (52.98 / 0.849 / 826.5) instead of half power and
   the 220 V set. Ours fixes the K2 bed at `max_power: 0.5` with the 220 V
   PID, so on 110 V it has a quarter of the power it should and may never
   reach 100 C. Port: a `bed_voltage` setting plus the slope test at first
   start.
2. **Nozzle-camera flow and PA calibration** (K2 Plus "flow dynamics"):
   templates, `ai_capture`, `EMdetection` and `yolo_attention` are all on
   the printer; waiting for camera hardware here.
3. **Saved Z tilt** (K2 Plus): stock keeps the Z/Z1 adjustment from the
   last `Z_TILT_ADJUST` in `stepper_adjustment.json` and `ADJUST_STEPPERS`
   reapplies it, so a print start need not tilt-probe every time. Ours
   probes every start (about 30 s).
4. **Maintenance counters** (`MAINTENANCE_ITEM_PARAM`): hours, cuts and
   grams through the nozzle against thresholds (shaper calibration 500 h,
   belt tension 1000 h, lubrication 500 h, nozzle 20 kg, cutter 100k
   cuts, PTFE 1000 h, filter and desiccant 500 h), shown as reminders.
   Ours has per-slot CFS counts only; the touchscreen could carry these.
5. **First-layer pause** (`FIRST_FLOOR_PAUSE`): stock can pause after the
   first layer for a look when the app asks. Our AI watcher covers the
   failure case; a plain "pause after layer 1" option is cheap to add.
6. **Second extruder PID set above 280 C** (`pid_*_high_temp`, switched
   in stock's heaters.py). Only matters for 290 C+ materials.
7. **Chamber fan as a `temperature_fan`** (stock: exhaust on the chamber
   thermistor, 35 C watermark; `CANCEL_CHAMBER_FAN_SWITCH`). Ours is
   manual plus `_CHAMBER_FAN`.
8. **Silent mode** (`Qmode`): accel 2500, 150 mm/s, SCV 5, PA 0.05, 50%
   speed, `M204` clamped. Easy as a macro if anyone wants it.
9. **Rear spool rack sensor** (`filament_rack`, closed): stock watches the
   rear holder's filament sensor (`!PA5`) for runout and flushing. Ours
   uses the toolhead sensor for the rear spool.

Not worth porting: `io_remap` (older K2 Plus board layout we do not
support), `ms3200x`/`tmc2262` (newer models), the EEPROM commands (stock's
first-use flags and Z adjustment store), `bl24c16f`, `dirzctl`/`hx711s`
(stock PRTouch internals, replaced by the open probe), `CXSAVE_CONFIG`,
`SET_IOREMAP`, the factory `A_TriPress`/`A_G28_test` macros, `photograph`
(the stock camera capture, replaced by harkcam), and master-server's
own flows (`PRINT_CALIBRATION`, `CHECK_BED_MESH`, `SET_G29_FLAG`).

### Beta

- validate on K2 Pro units and more firmware revisions (K2 Plus: done, one tester's printer, no CFS, from 31 Oct 2026)
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
