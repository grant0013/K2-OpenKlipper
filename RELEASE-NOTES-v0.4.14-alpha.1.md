# K2 OpenKlipper v0.4.14-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

A big one: most of what the stock touchscreen let you do is back, plus
time-lapse, USB storage and fixes from upstream Klipper.

## Touchscreen

- **Set temperatures from the Status page.** Tap the Nozzle or Bed gauge for
  presets (PLA, PETG, ABS, TPU) and +/- steps. On a K2 Plus the Chamber gauge
  sets the chamber heater.
- **Fans.** Tap the fan in the bottom bar for the part, side and exhaust fans.
  The exhaust fan's Auto gives it back to the automatic chamber control.
- **Z offset while printing.** The Controls page has -0.05, -0.01, +0.01 and
  +0.05 mm buttons (negative = nozzle closer) and Save, which keeps it for
  every print.
- **New Settings page** (the gear at the bottom of the side bar):
  brightness, screen off time, chamber light off time, idle time before the
  heaters and motors switch off, time zone, storage, time-lapse and the
  highlight colour. No config file editing needed; your choices survive
  updates and restarts.

## Storage: keep files off the printer's built-in memory

The K2's internal storage is small and wears with use. Plug a USB stick into
the K2's USB port (FAT32, exFAT or NTFS) and choose **Storage: USB** in
Settings:

- Files sent from OrcaSlicer or Mainsail land on the stick and print from it.
- Time-lapse videos and Klipper's log go there too (the log is the biggest
  writer: about 50 MB a day while printing). The log moves at the next
  restart.
- If the stick is missing, everything uses the printer's storage until it is
  plugged in again. The switch only happens while nothing is printing.
- **Plug and go:** plug a stick in and the screen asks whether to use it. A
  stick the printer can't read (for example one formatted on a Mac) gets a
  **Format** offer, with a second "erase everything?" check. It becomes a
  FAT32 drive that still works on Windows and Mac. Settings > Storage has a
  Format button too. The printer never formats while printing or while the
  stick holds the files in use.

## Time-lapse

One frame each layer from the chamber camera. The video is made on the
printer after the print and appears in Mainsail's Timelapse tab. It is off
until you switch it on (Settings or Mainsail).

It needs the new layer change G-code in your OrcaSlicer printer profile:
import the profile again from `orca/`, or add these two lines under
Printer Settings > Machine G-code > Layer change G-code:

    SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}
    TIMELAPSE_TAKE_FRAME

(They also give Mainsail the exact layer count. Nothing happens while
time-lapse is off.)

## K2 Plus

- **Chamber heater on.** Tested on a real K2 Plus, it heats as stock did:
  only above 40C, never above 60C, waits for the bed, and switches itself
  off if its fan stops or the chamber runs too hot. Set it from the
  touchscreen, Mainsail's chamber box or the slicer.

## Fixes

- **Print start: Y homing could fail.** The safety watch checked the motors
  at the moment stall homing was armed. It now leaves the motors alone while
  homing.
- **From upstream Klipper:**
  - PID tuning (`PID_CALIBRATE`) suggested values twice too high; corrected.
    Your current values are kept: re-tune only if temperatures wobble.
  - A lost connection to a printer board stops waiting commands straight
    away instead of hanging.
  - Bed mesh checks its settings on every run.

## If you set these in printer-overrides.cfg, you can remove them

- `[hark_safety] start_grace: 300` (the Y homing workaround)
- `[hark_chamber] enable: True` (now the default)
- `[gcode_arcs] resolution: ...` (since v0.4.13 it would undo the arc fix)

## K2 run from a separate PC

The touchscreen updates itself from v0.4.11 on. If your screen is older,
copy it once as described in [docs/EXTERNAL-HOST.md](docs/EXTERNAL-HOST.md).
Storage and time-lapse settings apply to a K2 that runs its own Klipper;
on a PC setup the files live on the PC.

Everything from v0.4.13-alpha.1 (small curves no longer printed as straight
lines, touchscreen clock in your time zone) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
