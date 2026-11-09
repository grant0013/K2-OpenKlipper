# K2 OpenKlipper v0.4.23-alpha.1

**Power-loss resume no longer pulls the part off the bed, and Klipper on an
external host reconnects after a USB drop.**

Please keep the reports coming. A klippy.log and what you saw are the most
useful things to send.

## Power-loss resume

- **`PLR_RESUME` heats up before anything moves.** The nozzle stops on the
  part and the plastic sets round it. Resume lowered the bed with the nozzle
  cold, so the part came off the bed with it. It now waits for the bed, then
  for the nozzle at full print temperature, and only then moves. A resume
  takes a few minutes longer to get going, and the nozzle may leave a small
  mark where it was sitting. **Not confirmed yet:** this has not been run on
  a real stopped print, so stay with the printer for your first one.

## Klipper on an external host

- **The relay reconnects after a USB drop.** It kept its dead USB ports open
  after a disconnect, so Klipper got no answer from the boards until the K2
  was rebooted. It now opens them again when the host comes back. Klipper
  still has to restart after a drop and a print in progress is still lost,
  but the K2 no longer needs a reboot. Tested with a real USB disconnect on a
  K2 running Klipper on an external host: ready again in 30 s with no manual
  restart.

  The relay runs on the K2 and does not come with the update. If you use an
  external host, copy `host/k2/relay.py` to `/mnt/UDISK/k2-hostlink/` and
  reboot the K2.

Not run on a K2 Plus.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Updating

Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1`. Your settings are kept.

Alpha software on your own printer: stay near it for the first prints.
