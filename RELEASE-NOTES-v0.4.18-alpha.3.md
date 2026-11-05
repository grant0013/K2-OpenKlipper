# K2 OpenKlipper v0.4.18-alpha.3

**Camera fix: watching the camera could stop a print.** The camera stream
on the printer sent 720p at 15 frames a second to every viewer, about
1.3 MB/s each with the chamber light on. That slowed the printer's WiFi
until the toolhead board missed its moves and Klipper shut down (`MCU
'nozzle_mcu' shutdown: Stepper too far in past`), and two viewers took the
network down altogether. Thanks to nur00 for finding and measuring it
([#4](https://github.com/grant0013/K2-OpenKlipper/issues/4)).

- The camera now runs at **10 frames a second shared by every viewer**:
  live streams and snapshots draw on one budget, so a second open tab gets
  half the frames instead of doubling the data sent.
- **At most 2 streams** stay open; a new one closes the oldest, so a
  fullscreen camera page never comes up blank.
- **New `harkcam.cfg`** in your config folder (editable in Mainsail): picture
  size, frames a second and the stream cap. On a weak WiFi signal,
  `fps: 5` or `size: 640x480` leaves more room. Run
  `sh /mnt/UDISK/harkcam/start.sh` or reboot after a change.
- The update restarts a running camera, so nothing else to do.

Printers running Klipper on an external host are not affected (their camera
is served by the host), and nothing else changed in this release.

**Update:** Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1` in the console. Your printer-overrides.cfg is kept.

Everything from v0.4.18-alpha.2 is included; see
[CHANGELOG.md](CHANGELOG.md).
