# K2 OpenKlipper v0.4.18-alpha.2

**Hotfix for v0.4.18-alpha.1: update before printing.** On alpha.1 a CFS
print start could shut Klipper down ("Internal error on command
CFS_SLOT_APPLY") when the slot's hotend PID profile was applied. Found on the
first real print after the release and fixed here; nothing else about the
print start changed.

Also in this release: console messages from the motors and the CFS read as
words (`Motors X, Y: stall mode 2, print protection ...`, `X: ok`) instead of
bus frames; the raw frames still go to klippy.log, and `console_frames: True`
in `[k2_motor_bus]` or `[k2_cfs]` brings them back to the console.

Everything from v0.4.18-alpha.1 is included: flow calibration at print start,
fan rpm and health, bed power on 110 V, the K2 Plus out of beta with belt
tensioning and saved Z tilt, maintenance counters, the boot animation and the
rest. See the [v0.4.18-alpha.1 notes](https://github.com/grant0013/K2-OpenKlipper/releases/tag/v0.4.18-alpha.1)
and [CHANGELOG.md](CHANGELOG.md).
