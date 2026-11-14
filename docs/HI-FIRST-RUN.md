# Creality Hi (F018): bench bring-up

`config/mainline_stage2_hi.cfg` is a hand-written port of the stock Hi 1.1.0.79
configuration. It loads in Klipper (batch mode, against the Hi's own main and
nozzle MCU dictionaries) but **has not moved a Hi yet**. This guide checks one
part at a time before anything prints. Stay at the printer with a hand near the
power switch.

**Nothing here is permanent.** It runs our Klipper next to Creality's: no files
of Creality's are changed, and **a power cycle puts the printer back to
stock.** The installer (`install.sh`) doesn't support the Hi yet; that comes
after this bench run passes.

Tested so far: nothing on a printer. Written 14 Nov 2026 from Creality's Hi
files, the K2 OpenKlipper modules and the stock Hi's own logs.

## What is different on the Hi

| | K2 | Hi |
| --- | --- | --- |
| Motion | CoreXY | bed-slinger (cartesian), X -19..285, Y -7..273 |
| X homing | stall line on main PB11 | stall line on nozzle PB0, copied onto the probe line PC7 while X homes (`HI_HOME_X`) |
| Z | one motor, bottom switch PA15 | two TMC2208 motors, optical switches at the **top** (PB15, PB14) |
| Z home | bottom switch, then nozzle tap | gantry up to the switches (levels it), down, nozzle tap |
| Extruder | closed-loop on the motor bus | open loop on a TMC2208 |
| MCUs | main, nozzle | main, nozzle, bed (Y accelerometer) |
| CFS | supported | not in this build |
| Screen | Hark Screen 800x480 | 240x320: no Hark Screen yet; the panel is blank while ours runs |

## 0. Before you start

- Stock firmware 1.1.0.79, root on, SSH working (`ssh hi`).
- The printer is idle, the bed is empty, filament is loaded.
- A full stock backup exists: `~/creality-hi/backup-stock-1.1.0.48-20261114`.
- Y motor repaired and calibrated, bed wheels snug.

## 1. Copy the build to the Hi (changes nothing yet)

From the Mac, in the K2-OpenKlipper checkout on branch `hi-port`:

```bash
git archive hi-port | ssh hi 'rm -rf /mnt/UDISK/okl-hi && mkdir -p /mnt/UDISK/okl-hi && tar -x -C /mnt/UDISK/okl-hi'
```

(The Hi's SSH server has no SFTP, so `scp` fails; the tar pipe works.)

## 2. Start our Klipper

```bash
ssh hi 'killall Monitor master-server upgrade-server display-server 2>/dev/null; sh /mnt/UDISK/okl-hi/switch-to-mainline.sh mainline_stage2_hi.cfg'
```

`Monitor` goes first, because it restarts Creality's processes. The stock
Moonraker stays up, so Fluidd at `http://<hi>:4408` talks to our Klipper.

Check, in the Fluidd console:

1. `Printer is ready`. If not, `/mnt/UDISK/printer_data_mainline/logs/klippy.log`
   says why.
2. `HARK_DUMP_DICTS`: saves the three MCU dictionaries. The bed MCU's has
   never been read; send it in.
3. `MOTOR_VALUES`: X (0x81) and Y (0x82) answer, motors ready.
4. **Board fan:** the mainboard fan under the printer should be running, as on
   stock. If it isn't, tell me: `[output_pin board_fan]` keeps stock's
   setting, and which level runs the fan isn't known.
5. Part fan: `M106 S128`, then `M107`. Hotend fan: `M104 S60`; it should come
   on at 50 C. Then `M104 S0`.

## 3. Home X (watch the gantry)

`G28 X`. Z isn't known yet, so first the gantry **lifts up to 15 mm** (stock
does the same, `UP_SAFE_Z`), then X homes to the left. If the gantry goes
**down**, or keeps going, cut the power.

## 4. Home Y

`G28 Y`. The bed runs to the front stop and comes back 10 mm, as on stock.

## 5. Home Z, then measure the top switches

`G28 Z` moves the head to the bed centre, drives **both Z motors up to the top
switches** (up to 7 tries until they agree; that also levels the gantry), sets
Z to 294.0, comes down to Z 5 and taps the bed with the nozzle.

Then measure where the switches really are:

```
KZ_ALIGN_MEASURE CONFIRM=1
```

Put the value it suggests in `printer-overrides.cfg` under `[k2_z_align]`
`position_endstop:`. The stock Hi saved 294.6. A little low is safe; too high
is not.

## 6. Probe

1. `PROBE_ACCURACY`: the range should be under 0.02 mm.
2. `Z_TILT_ADJUST`: two taps at X40 and X220, with corrections under 0.1 mm.
3. `BED_MESH_CALIBRATE`: 9x9. Compare with the stock mesh (about a 1 mm
   front-to-back slope before the wheels were done).

## 7. Heaters

```
PID_CALIBRATE HEATER=extruder TARGET=220
PID_CALIBRATE HEATER=heater_bed TARGET=60
SAVE_CONFIG
```

Mainline has no copy of stock's bed temperature correction table, so the bed
runs a few degrees warmer than the number shown.

## 8. Input shaping

`SHAPER_CALIBRATE`, then `SAVE_CONFIG`. The stock result before the wheels were
tightened was X mzv 46.8 Hz, Y mzv 36.0 Hz.

## 9. Positions, then the first print

1. `G28`, then `HI_WIPE` and `HI_PURGE LENGTH=10` with the nozzle hot.
   Check the nozzle wipes on the Teflon at the left (X0 to X-5, Y130) and
   purges over the chute at X-18.5. Stock positions; not yet seen on ours.
2. A one-layer first-layer test. Tune `variable_hot_z_comp` (positive lowers
   the nozzle) in `printer-overrides.cfg`:

   ```
   [gcode_macro START_PRINT]
   variable_hot_z_comp: 0.02
   ```

3. A Benchy from Orca or Creality Print. They send `START_PRINT
   EXTRUDER_TEMP= BED_TEMP=`.

## Back to stock

Either power-cycle the printer, or:

```bash
ssh hi 'sh /mnt/UDISK/okl-hi/switch-to-stock.sh; /etc/init.d/app restart'
```

## Not ported yet

- Installer support (`install.sh` F018 branch: board gate C20/C21, no Hark
  Screen or boot animation until there's a 240x320 version)
- Hark Screen for the 240x320 panel
- PA and flow calibration macros (the K2's PA_CAL / FLOW_CAL), power-loss
  recovery, maintenance counters, fan tachometers (fan health), CFS
- Creality's stock bed temperature correction table
