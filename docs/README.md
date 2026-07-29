# K2 open-stack build v62

This build runs upstream Klipper commit
`d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65` with the K2-specific open
PRTouch, Z-alignment, motor-bus, fan-feedback, and CFS integrations documented
in `OPEN-STACK.md`.

The running configuration is kept in
`/mnt/UDISK/printer_data_mainline/config` and is visible in Mainsail.

Creality's firmware-packaged Moonraker remains pinned because the appliance
image has no git client and lacks the native dependency toolchain needed for a
safe in-place Moonraker upgrade. Mainsail web-client updates are supported.

`system/moonraker-compat/update_manager.py` also disables the late
Klipper-updater construction performed after Klipper identification. Without
that guard, Moonraker displays Klipper as ready but print-start requests wait
for an incomplete connection task and time out.

The final v62 live check successfully completed upload, print-start, homing,
nozzle cleaning, a second PRTouch Z home, adaptive mesh, purge-bucket parking,
CFS purge, and first-layer printing. See `OPEN-STACK.md` for the measured
results and remaining qualification work.
