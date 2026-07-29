# K2 mainline v62 open control plane

## Active architecture

- Upstream Klipper host: `d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65`
- Mainline K2 build: `/mnt/UDISK/k2-mainline-20260729-v62`
- Klipper API socket: `/tmp/klippy_uds`
- Dedicated Moonraker: port 7126
- Mainsail 2.18.2: port 4409
- Legacy Fluidd/Moonraker remain available on 4408/7125 for rollback
- Camera/WebRTC: port 8000
- Open K2 drivers: PRTouch, Z alignment, motor RS485, and CFS

Creality `master-server` and `Monitor` are disabled in `/etc/init.d/app`.
Both have direct printer-control paths; `Monitor` respawns `master-server`.
Passive Creality audio, Wi-Fi, app, upgrade, and web services remain enabled.
The stock touchscreen control plane is not available without `master-server`.

## v62 PRTouch contact hysteresis

- Amplitude-mode contact must still cross the conservative configured entry
  threshold before qualification begins.
- Once contact begins, a lower release threshold (75% by default) preserves
  the sustained-contact counter while the clean nozzle/bed load signal rings.
- Three-window persistence remains in place for Z homing, so isolated
  print-motion spikes cannot qualify as contact.
- The captured failed contact `710, 574, 771` now qualifies at
  entry/release thresholds `600/450`; the earlier vibration sequence
  `597, 721` still cannot qualify.

## v61 CFS steady-feed fix

- Stock logs confirm that `SET_BOX_MODE ff 04 <slot-mask> 00` arms the selected
  slot for print feed while `GET_BOX_MODE` reports the separate load/unload
  operation engine.
- Once steady feed is armed, stock and live slot-C traces both report
  `GET_BOX_MODE = 0` (IDLE), not `2` (PRINTING).
- Feed start, load completion, and purge now require the requested slot mask
  plus an IDLE operation engine. Error, test, preload, and wrapping states are
  still rejected, and the automatic disarm remains in place.

## Inherited v60 print-start fixes

- CFS purge/load verification now reads the authoritative `GET_BOX_MODE`
  response instead of the measuring-wheel selection response.
- Any CFS print-start exception now disables tension and returns the CFS to
  IDLE automatically.
- After adaptive mesh, the toolhead immediately moves to the purge bucket
  before final nozzle heating so ooze cannot land on the measured bed.

## Inherited v59 nozzle-contact fix

- `START_PRINT` now follows Creality's important sequencing principle: rough
  home, thermal settle, clean nozzle, then the Z home used for bed probing.
- `NOZZLE_WIPE CONFIRM=1` uses the physically validated stock route to the
  rear silicone cleaner, performs the circular and linear wipe pattern, and
  exits the wipe zone before the next PRTouch operation.
- Creality's PRTouch-controlled heated bed-drag cleaner is not emulated by
  blind G-code. It remains a separate reverse-engineering and validation item.

## Inherited v58 safety fixes

- `fan_feedback.py` exposes the K2 MCU tachometers through both the
  `fan_feedback` object and Creality-compatible `get_cx_fan_status` webhook.
- `CANCEL_PRINT` cancels virtual SD first, then turns off heaters and fans,
  stops CFS feed, clears print transforms, and disables steppers without
  attempting any toolhead movement.
- `switch-to-mainline.sh` refreshes the cross-built `c_helper.so` timestamp so
  a package transfer cannot trigger an impossible on-printer C rebuild.
- CFS stop now uses Creality's exact global-idle frame (`ff 04 00 01`) and
  verifies the dedicated `GET_BOX_MODE` response. The measuring-wheel tail is
  retained as slot-selection data and is no longer misreported as live mode.
- Cancel and end-print use upstream Klipper's parameterized exclude-object
  reset commands.

## Live backups

- Pre-v55 config: `/mnt/UDISK/mainline-backups/pre-v55-20260729-2150`
- Stock app init: `/mnt/UDISK/mainline-backups/pre-master-isolation-20260729-2155/app.init.stock`
- Previous complete build: `/mnt/UDISK/k2-mainline-20260729-v61`

## Restore Creality control plane

```sh
cp /mnt/UDISK/mainline-backups/pre-master-isolation-20260729-2155/app.init.stock /etc/init.d/app
chmod 755 /etc/init.d/app
/etc/init.d/app restart
```

## Return to v61

```sh
sh /mnt/UDISK/k2-mainline-20260729-v61/switch-to-mainline.sh mainline_stage2.cfg
```

Do not switch Klipper builds while a print is printing or paused.

## v62 print scheduling and UI isolation

- Arc segmentation now uses Creality's factory-safe 1.0 mm resolution. The
  previous 0.1 mm value produced dense sub-millisecond move queues and the
  observed dual-MCU `Timer too close` / `Stepper too far in past` shutdown.
- The active Klipper configuration now lives under
  `/mnt/UDISK/printer_data_mainline/config`, so it is fully visible and
  editable in Mainsail without exposing years of Creality backup configs.
- Mainsail and its dedicated Moonraker run on 4409/7126. The original Fluidd
  and Moonraker remain untouched on 4408/7125.
- Mainsail web-client updates are exposed. Creality's firmware-packaged
  Moonraker/Klipper self-updaters are deliberately omitted because its image
  has no git client and strips the updater metadata and dependency toolchain.
- The left-side pre-print scrape is generated by OrcaSlicer's K2
  `change_filament_gcode`, which also runs for the initial `Tn` selection
  immediately after `START_PRINT`. Replace its legacy spiral / `X0 Y140` /
  `Z{z_after_toolchange}` sequence with the safe template recorded in
  `ORCA-K2-PROFILE.cfg`. This holds at least Z0.6 for the initial travel and
  leaves later-layer toolchanges at their requested Z.

## Moonraker print-start handshake fix

Creality packages Moonraker and Klipper as firmware files under `/usr/share`.
The image does not contain Klipper's normal updater requirements file or Git
metadata. The compatibility patch therefore omits the built-in Klipper and
Moonraker deployers while retaining Mainsail's web updater.

The `server:klippy_identified` event must also avoid constructing a late
Klipper updater. Before this final v62 fix, that event raised a `ConfigError`
for the missing `scripts/klippy-requirements.txt`. Moonraker still displayed
Klipper as ready, but its connection task never completed, so
`POST /printer/print/start` timed out after 20 seconds.

The final compatibility module sets Moonraker's Klipper-identified event and
leaves the firmware-packaged Klipper under this build's control. A safe
nonexistent-file probe reached Klipper in 0.061 seconds and received the
expected immediate `Unable to open file` response. A real Orca upload then
started `3dbenchy_PLA_34m19s.gcode` normally.

Backup of the previously deployed compatibility module:

`/usr/share/moonraker/components/update_manager/update_manager.py.before-start-handshake-fix-20260729`

## Live validation snapshot

Validated on the K2 without remote motion commands:

- The dedicated Moonraker reports ready with no failed components or warnings.
- Orca upload, virtual-SD selection, and print start work through port 7126.
- Initial homing, the 120-second thermal settle, nozzle wipe, second PRTouch Z
  home, adaptive mesh, post-mesh purge park, CFS purge, and first-layer
  transition all completed.
- The adaptive mesh completed before the toolhead moved to the purge bucket at
  X115/Y291.5/Z5.
- The sliced opening contains no legacy `X0 Y140` or `G1 Z0` scrape move.
- The monitored Benchy passed 10% with stable heaters, no pause, no MCU timing
  shutdown, and zero reported toolhead stalls.

This is a partial print validation. A completed long print is still required
before calling v62 production-qualified.
