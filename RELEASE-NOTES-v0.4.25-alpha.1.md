# K2 OpenKlipper v0.4.25-alpha.1

**Fixes from a full code review: a first print on a new K2 no longer stops at
the bed voltage check, and several faults that could lose a print or go
unnoticed are closed.**

Please keep the reports coming. A klippy.log and what you saw are the most
useful things to send.

## Tested on one K2

A 9-pad test print ran through the whole print start and finished cleanly on
this code, and these were each checked on the printer:

- **The first print on a new K2 no longer stops at the bed voltage check.**
  `BED_POWER_CALIBRATE` refused to run inside `START_PRINT`, the one place it
  runs by itself, so the print ended there until you ran it by hand.
- **A runout outside a print no longer sticks.** Filament leaving the sensor
  during a manual load left the printer marked as paused, and the next print
  could not pause and ignored a real runout. `START_PRINT` now clears it.
- **A second `PAUSE` does nothing.** It no longer lifts and parks again or
  overwrites the temperatures Resume returns to.
- **`RESUME` with nothing paused no longer heats anything.**
- **`RELOAD_GCODE_MACROS` is refused during a print.** Re-reading the config
  can stall the K2's CPU long enough to shut the MCU down.
- **Typing errors give an error message instead of shutting Klipper down**:
  `MOTOR_VALUES INDEX=`, `MOTOR_BUS_SEND PAYLOAD=` and `CALIB_PICK VALUES=`
  were tried on the printer. `FLOW_CAL_RUN FLOWS=`, `PA_LOG_STOP` on a full
  disk, saving slot records on a full disk, a motor bus that cannot open and
  CFS bus errors at print start have the same fix.

## Not tested on a printer yet

These need a real fault to trigger, so they have only been through the
offline tests. Please tell me how they behave if you meet one:

- **Runout refill works in the first few layers.** It stopped with "CFS move
  requires Z" because the pause only lifts 2 mm.
- **Resume after a failed colour change re-arms the CFS feed** when the
  filament was already at the nozzle.
- **A stopped hotend fan switches the nozzle off** as well as pausing, so it
  does not sit hot with no heatsink airflow. Resume reheats it.
- **A failed home no longer ends motor fault watching** for the rest of the
  session.
- **Loading the rear spool during a long pause uses the print's temperature**,
  so the print does not carry on at the load's temperature.
- **K2 Plus: the chamber over-temperature cut-off can now fire.** It needed
  heater power, which is already off by then.
- **Power-loss resume** refuses a part too tall for the Z reference instead of
  pushing it into the nozzle, and excluded objects stay excluded.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Updating

Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1`. Your settings are kept.

Alpha software on your own printer: stay near it for the first prints.
