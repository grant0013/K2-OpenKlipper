# K2 OpenKlipper v0.4.18-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021) and the K2 Plus (F008).
Supervise prints and keep the rollback path. Update with the Update button
in Mainsail or on the touchscreen (your `printer-overrides.cfg` is kept), or
install with the one-line installer in [docs/INSTALL.md](docs/INSTALL.md).

## Highlights

- **Flow calibration at print start:** the printer measures where its
  hotend stops keeping up and caps that print to it (off by default, one
  line to turn on).
- **Fan rpm in Mainsail and on the touchscreen,** with a health check at
  every print start and a measured minimum speed for the part fan.
- **110 V printers get their bed power back:** the mains voltage is checked
  once and the right power and PID set applied.
- **K2 Plus out of beta:** no install gate, automatic belt tensioning, saved
  Z tilt.
- **Power-loss recovery, silent colour-change retries and per-slot CFS
  counts** (from the 2 November part of this release).
- **A new boot animation,** and Creality's kept for anyone who wants it back.

## Printing

- Max flow measured at the chute (`hark_flow_cal`): 8 mm^3/s upwards in 3 s
  steps on the strain gauge; the knee is where the pressure leaves the line
  by 4%, the cap is 3 mm^3/s under it, held with `SET_MAX_FLOW` for that
  print only. Turn on with `[gcode_macro _FLOW_AUTO] variable_on_print_start:
  1`; `FLOW_CAL_STATIC` runs it by hand.
- Extruder motor reads fixed (they were one request behind), plus
  `MOTOR_VALUES`, `MOTOR_LOG_START/STOP` and `MOTOR_GET_PROBE`.
- Power-loss recovery (`PLR_RESUME CONFIRM=1`, touchscreen Discard/Resume),
  a failed colour change pauses instead of ending the print and `RESUME`
  retries it, loads retry silently, per-slot counts with a warning for a
  slot that keeps failing, Orca's own purge length for every change, CFS
  loads ~10 s faster, PETG purges with the part fan off, cutter calibration
  with the X/Y motors, a stuck nozzle-sensor pin shaken free.

## Fans

- `tachometer_pin` on the part, hotend and PTC fans: rpm everywhere.
  `[fan_feedback]` removed. Rpm is true at full speed only (the fans are
  powered through their PWM); 0 means stopped.
- `FAN_CHECK` / `FAN_HEALTH` keep each fan's full-speed rpm and warn with
  `!!` when one slows 15% or stops; `_FAN_AUTO` runs it at every print
  start. `FAN_MIN_CAL` finds the lowest duty the part fan keeps turning at
  and `M106` never asks for less.

## Bed heater

- `hark_bed_power` (K2): the mains test from Creality's firmware (30 C at
  half power, 0.35 C/s divide) runs once before the first print and picks
  the 110 V or 220 V power and PID set; `voltage: 110|220` forces it;
  `BED_POWER_CALIBRATE`, `BED_POWER_STATUS`, `BED_POWER_SET`.

## Touchscreen

- Fans box: part fan rpm (or its usual), a Hotend fan line, colours for a
  slow or stopped fan.
- Boot animation: the Hark Tech badge, "K2 OpenKlipper", harktech.co.uk.
  `OKL_BOOTANIM=keep` keeps Creality's; `OKL_BOOTANIM=/folder` installs your
  own from a logo (`tools/bootanim/make_bootanim.py`,
  docs/SCREEN-CUSTOMISE.md). Creality's set is backed up and `uninstall.sh`
  restores it.
- Time zone picker, USB eject, chamber target and the other screen fixes
  from v0.4.15-v0.4.17 are included.

## K2 Plus

- Install without `OKL_K2PLUS_BETA=1`: tested on a real K2 Plus since 31
  October (no CFS; a CFS-equipped Plus is still untested).
- `BELT_TENSION_STATUS` and `BELT_TENSION CONFIRM=1` drive the automatic
  tensioners (stock's routine, 140 N). Not yet run on a real printer: step
  10 of the K2 Plus guide.
- `Z_TILT_APPLY_SAVED` and `_Z_TILT_START mode: "saved"` skip the tilt probe
  at print start once a printer has proven stable.
- Stock extruder PID, bed check 120 s, Z tilt at 300 mm/s (nur00's review).

## Also

- `MAINTENANCE_STATUS` / `MAINTENANCE_DONE`: hours, grams and cuts against
  Creality's maintenance thresholds, listed when a print ends.
- `FIRST_LAYER_PAUSE ENABLE=1`, `SILENT_MODE ENABLE=1`, and an optional
  second extruder PID set above 280 C.
- docs/PROJECT-STATUS.md: what is still only in Creality's firmware.

## Upgrade notes

- The first print after this update heats the bed 30 C at half power
  before its own heat-up, once, to check the mains voltage (K2 only). To
  skip it: `[gcode_macro _BED_POWER_AUTO] variable_on_first_print: 0`, or
  `[hark_bed_power] voltage: 220` in printer-overrides.cfg.
- Every print start now blasts the part fan for 3 s and reads the hotend
  fan at the chute (`_FAN_AUTO`); turn off with `variable_on_print_start:
  0` if you prefer.
- The flow calibration is off until you turn it on (above); it adds about
  25 s and 70 mm of filament to a print start.
- The boot animation changes at this update unless you install with
  `OKL_BOOTANIM=keep`.
- `[fan_feedback]` in a printer-overrides.cfg of your own must go: its pins
  are the fans' tachometers now.

Full list in [CHANGELOG.md](CHANGELOG.md).
