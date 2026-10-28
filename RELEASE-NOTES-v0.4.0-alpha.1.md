# K2 OpenKlipper v0.4.0-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021). Supervise prints and
keep the rollback path. Update from v0.3 with the Update button in Mainsail
or on the touchscreen (your `printer-overrides.cfg` is kept), or install with
the one-line installer in [docs/INSTALL.md](docs/INSTALL.md).

## K2 without a CFS, and CFS hot-plug

- **Printers without a CFS work.** Like Creality's firmware, the printer
  looks for the CFS at every start and, without one, feeds from the rear
  spool holder. Unplugging or plugging in a CFS while the printer is idle is
  picked up in under a minute, with no restart.
- **External spool** card in Mainsail and page on the touchscreen, in place
  of the CFS ones: set the filament, load, unload (cut at the knife) and
  calibrate pressure advance. The external spool has its own filament record
  (slot E), which OrcaSlicer sees as its first filament.
- New `LOAD_FILAMENT` / `UNLOAD_FILAMENT` (Mainsail Load/Unload buttons).
  Files that ask for T1-T3 on a printer without a CFS stop with a message.
- **Fix for every CFS owner:** v0.3 shipped our test printer's CFS id, which
  is what gives the CFS its bus address back after a power-off. The driver
  now finds any CFS itself with Creality's discover command, and starts a
  CFS that is still in its loader after being plugged in.
- `[k2_cfs] cfs_fitted: auto` (default), `True` or `False` in
  `printer-overrides.cfg` if you want to force it.

## Calibration

- `PA_CAL_STATIC SLOT=x`: pressure advance with the nozzle standing still
  over the chute, from the nozzle strain gauge (Dmitry Butyugin's
  `pa_tester`). Both the speeding-up and slowing-down steps fit now.
- Flow tools in `tools/flow/`: maximum-flow test from nozzle pressure, and a
  flow-ratio pad generator.
- Mainsail's slot dialog has Load, Unload and Calibrate PA buttons for CFS
  slots too.

## Ported from Jacob10383's K2 Plus firmware (credited in each file)

- Motor faults are decoded and acted on (a faulted controller used to look
  like no answer), with new `MOTOR_FAULTS`, `MOTOR_TEMPS` and related checks.
- PRTouch: fail-closed arming, re-probe of mesh taps that land on a blob,
  optional multi-tap Z home and mesh, and `PRTOUCH_SCRUB`, the
  strip-detecting nozzle scrub (opt-in).
- CFS: shorter purge on a same-filament runout refill, BUSY retries,
  plain-English status codes, `CFS_RFID_READ`, and optional clog detection.

Full list: [CHANGELOG.md](CHANGELOG.md). Tested on our K2 (F021) running on
an external host; not yet tested on a K2 that never had a CFS.
