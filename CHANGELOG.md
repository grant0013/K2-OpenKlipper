# Changelog

## v0.4.16-alpha.1 (2026-11-01)

- **Eject the USB drive from the touchscreen** (Settings > Storage, next to
  Format). Moves G-codes and time-lapses back to the printer's storage; when
  Klipper's log is on the drive, restarts Klipper the way the printer starts
  it (the rc.local hook's `switch-to-mainline.sh` and config), which reopens
  the log internally and restarts Moonraker and the screen; the restarted
  screen finishes the eject. Then `sync` and `umount` (retried for 40 s),
  and "Safe to remove" or, if the drive is still busy, "switch the printer
  off before unplugging it". Refused while printing. The prefs flag
  `usb_ejected` keeps the drive unused (`storage-links.sh` and the screen)
  until it is unplugged, or Storage: USB is chosen while it is still
  mounted; no "USB drive found" prompt for an ejected drive.
- **Chamber target on the Status page.** With the K2 Plus chamber heater
  enabled, the Chamber gauge shows its target (`hark_chamber.requested`)
  like the nozzle and bed.

## v0.4.15-alpha.1 (2026-11-01)

- **Pressure advance calibration saves print-ready values by default.** The
  stationary strain-gauge measurement (`PA_CAL_STATIC`, the touchscreen's
  Calibrate PA) reads lower than the value that prints best; printed PA
  patterns put the factor at x1.34 for PLA and x1.13 for PETG (10-28). That
  was only in our test printer's overrides, so other printers saved PA
  13-34% low. `_PA_STATIC_SAVE variable_scale_material` now defaults to
  `{'PLA': 1.34, 'PETG': 1.13}` (other materials: `variable_scale`, 1.0).
  Already-saved slot values are not changed: run Calibrate PA again.

## v0.4.14-alpha.1 (2026-11-01)

- **USB drive: plug in and go.** The touchscreen asks once per drive whether
  to use a newly plugged drive for storage, and offers to format one the
  K2 cannot read (two confirmations). Format (also on Settings > Storage)
  makes the whole drive one FAT32 volume (`mkfs.fat -F 32 -I`, label
  K2PRINTER, the kernel's own driver; the K2 mounts a partitionless sda
  itself). Refused for anything but sdX (the K2's memory is mmcblk0), for
  sizes outside 256 MB-2.2 TB, while printing, and while the drive holds
  the G-code, time-lapse or log folders in use.
- **Time-lapse on the K2** (moonraker-timelapse, vendored and adapted: the
  K2 has no wget, frames are fetched with Python): one frame a layer from
  the chamber camera into RAM, the video rendered after the print with the
  K2's own ffmpeg at lowest priority, shown in Mainsail's Timelapse tab.
  Off until switched on (Mainsail or the touchscreen's Settings). The Orca
  profiles' layer change G-code takes the frames and reports the layer
  number (exact layer counts in Mainsail); TIMELAPSE_TAKE_FRAME is in the
  printer config everywhere and does nothing while off.
- **Storage choice covers G-code files, time-lapse videos and logs**: with a
  USB drive chosen, uploads (Moonraker writes its temporary upload file in
  the same folder), prints, thumbnails, videos and Klipper's log stay off
  the eMMC (system/storage-links.sh at start-up, before Klipper opens its
  log; the touchscreen moves G-code and videos between prints).
- **K2 Plus chamber heater on by default** after the supervised test on a
  real K2 Plus heated as stock did (`[hark_chamber] enable: False` turns it
  off). Mainsail's chamber box, the slicer's M141/M191 and the touchscreen
  set it.
- **Safety watch stays off the motor bus while homing**: a protection query
  between stall-homing arm (`MOTOR_STALL_MODE DATA=1`) and `G28 Y` made Y
  homing fail at a print start (10-31). It now waits while homing or stall
  homing is armed, and a few seconds after; fan checks carry on. The
  `[hark_safety] start_grace: 300` workaround can come out.
- **From upstream Klipper** (our base is 28 July): `PID_CALIBRATE` recommended
  values twice too high (Astrom-Hagglund Ku), now corrected, with a smaller
  oscillation band and `PID_PROFILE CALIBRATE` using the same fix; a lost
  MCU link fails waiting commands at once ("No communication with MCU")
  instead of hanging; `BED_MESH_CALIBRATE` always checks the mesh algorithm
  and the direct mode no longer shares its matrix; motion reports no stale
  speed between moves. Existing PID values are kept (upstream: re-tune only
  if heating oscillates).
- **Touchscreen Settings page** (gear in the side bar): panel brightness,
  chamber light off time, idle time before heaters and motors go off, the
  printer's time zone (picker of 17 regions, written to the printer's own
  setting) and the highlight colour. Kept in harkscreen-settings.json; the
  Klipper ones are sent again whenever Klipper restarts. The idle message
  shows the timeout in force. Also on the Settings page: screen-off time,
  and where G-code files are kept (the printer's storage or a USB drive in
  the K2's USB port; follows the drive when it is unplugged and plugged back,
  only while idle). While printing, the Controls page has Z offset buttons
  (-0.05/-0.01/+0.01/+0.05) and Save Z, kept for every print. Audited across
  1,320 page/state/box renders. The installer repoints a G-code link left
  dangling by a removed USB drive.
- **Touchscreen temperature and fan control** (nur00): tap the Nozzle or Bed
  gauge for a temperature box (presets, steps, limits from the config), the
  Chamber gauge for the K2 Plus chamber heater once enabled, and the fan in
  the bottom bar for part, side and exhaust fans (exhaust Auto = automatic
  chamber control). Audited across 1,800 renders; every command run through
  Klipper on both configs.

## v0.4.13-alpha.1 (2026-11-01)

- Touchscreen clock: `timezone: auto` (new default) uses the printer's own
  time zone as set in Creality's settings; it was fixed to UK time. Updating
  switches the shipped `timezone: uk` to `auto` when the printer has a time
  zone set.
- **Klipper fix: small arcs printed as straight lines.** Klipper's G2/G3
  support cuts every arc into pieces of one fixed length and rounded the
  count down, so any arc shorter than two pieces became a single straight
  chord: a half circle of radius 0.3 mm (fillets, letters, small holes)
  missed its curve by 0.3 mm at our resolution 0.5. Measured on 564,000 arcs
  from six real K2 prints: worst miss 0.30 mm. `gcode_arcs` now rounds up and
  takes `max_deviation`: arcs are cut by accuracy, with `resolution` as the
  longest segment. The K2 config uses 0.01 mm / 2 mm: worst miss 0.01 mm
  with 11% fewer moves than before (lighter on the K2's processor). Upstream
  Klipper has the same bug. If you set `[gcode_arcs] resolution` in
  printer-overrides.cfg, remove it. New test: tests/test_gcode_arcs.py.

## v0.4.12-alpha.1 (2026-11-01)

- **Fix: no print preview on the K2 for OrcaSlicer files** (Mainsail and
  touchscreen). Creality's Moonraker read OrcaSlicer files as an unknown
  slicer, which skips thumbnails (and the slicer's time estimate). The
  installer now adds OrcaSlicer to its PrusaSlicer-style reader, as current
  Moonraker does; the original is backed up and uninstall.sh restores it.
  Files already on the printer keep their old details: send them again.
  Checked on Creality's metadata.py with a real Orca 2.5 file: "Unknown",
  no thumbnails before; OrcaSlicer, 300/96/32 px thumbnails after.

## v0.4.11-alpha.1 (2026-11-01)

- **K2 driven by a separate PC: the touchscreen now updates itself.** A host
  update replaced Klipper's files on the PC only; the touchscreen program on
  the K2 board stayed on whatever was copied at setup (Grant's was from
  10-28, missing every screen fix since). While idle, the screen now fetches
  the release the host runs from GitHub, checks it, keeps the old copy and
  restarts; a copy that fails to start is rolled back and not fetched again.
  One manual copy is needed to get this version onto an existing setup
  (docs/EXTERNAL-HOST.md, K2 side step 6). The installer now writes the
  screen's VERSION file.

## v0.4.10-alpha.1 (2026-11-01)

- **Fix: colours bled on multicolour prints.** An OrcaSlicer printer preset
  whose change-filament G-code lacks the `next_purge` line passed no flush
  length, so every PLA-to-PLA change purged a fixed 70 mm. Orca's own matrix
  in HT-P-2026-0416 asked for up to 743 mm^3 = 309 mm (black to white).
  `[k2_cfs] toolchange_purge_from_file` is now on by default: the printer
  reads the flush matrix from the print file itself. Colour-change purges
  can now be up to 360 mm (was 240), the load's own limit. New test:
  tests/test_cfs_flush_matrix.py.

## v0.4.9-alpha.1 (2026-11-01)

- Touchscreen preview: the thumbnail was asked for once, the moment a print
  started. A file the slicer had just uploaded and started could still be
  in Moonraker's metadata scan, and the screen then showed no preview for
  the whole print. A miss is now retried every 15 s for 5 minutes, and the
  reason is written to /tmp/harkscreen.log.

## v0.4.8-alpha.1 (2026-11-01)

- Fixed: every K2 Plus warned "Motor check: ... calibration param 19 is 0.15
  ... may not be calibrated" for all four motors at start-up. K2 Plus motors
  read 0.15 there with a normal calibration offset (param 09); the warning
  now needs the offset itself near zero.
- Touchscreen: messages from while the printer was idle (start-up lines,
  console typos) no longer pop up when the next print starts; the heating
  rings and spinners of the page underneath no longer draw over a box.
- Touchscreen: only one box shows at a time (a question, then an error or
  safety message, a calibration result or pick, the slot menu, an update
  offer). A PA calibration result clears when a print starts, or after 10
  minutes. The busy banner shows on the Status page only (other pages show
  the work in the header) and fits its text; the footer's fan no longer
  animates through it. Checked across every page, printer state and box
  (924 combinations) for overlapping, clipped or off-screen text.

## v0.4.7-alpha.1 (2026-11-01)

- **Fix: colour changes low in a print stopped the print.** A CFS change
  below about Z 2.5 mm failed with "CFS move requires Z >= 4.500 (current
  4.461)". The change lifts to `purge_min_z` before the cut, but it sent
  that height as G-code Z while the purge gate checks the real toolhead
  height. The hot-nozzle first-layer offset (about -0.04 mm for PETG at
  250C) left the nozzle just under the gate. The lift, and the
  filament-path retry's lift and return, now allow for the G-code offset.
  New test: tests/test_cfs_toolchange_lift.py.

## v0.4.6-alpha.1 (2026-11-01)

- **Print preview on the touchscreen:** the Status page shows the slicer's
  thumbnail of the print, a faint ghost that fills in with colour from the
  bottom as Z climbs (Z / model height from Moonraker's metadata), with a
  shimmering line at the layer being printed. Shown in full when the print
  completes. Files without a thumbnail keep the old layout.
- Touchscreen animations: boxes (confirmations, notices, slot menu, results)
  fade in and out; error notices pulse their warning icon.
- Touchscreen fit pass on every page with the printer's font: long filament,
  file and network names end in "…" instead of running off their tile or
  under buttons, CFS slot names use two lines, button labels shrink to fit,
  a long print-start step ends in "…".

## v0.4.5-alpha.1 (2026-11-01)

- **Touchscreen notices:** a pending update shows a box with **Update now**
  and **Later** (Later puts that version off for 24 h; the Machine page row
  still updates any time). A print that stops on an error shows the reason,
  and error messages during a print (safety watch, runout, failed bed mesh)
  show in a box with OK; they wake the screen. While idle only safety-watch
  and chamber-heater messages pop up. A Klipper shutdown shows Klipper's own
  reason on the not-ready page.

## v0.4.4-alpha.1 (2026-11-01)

- **Cancel works like stock:** `CANCEL_PRINT` runs `END_PRINT` (heaters off,
  lift, park) instead of stopping dead with the motors off. The safety watch
  cancels with `CANCEL_PRINT SAFE=1`: no motion, motors off, as before.
- **END_PRINT keeps the motors on** (stock comments out `M84`) and lowers the
  bed half way, like stock's END_PRINT_POINT, so the bed no longer drops on
  the K2 Plus and the print is presented.
- **Warm start:** a print that follows one that finished or was cancelled
  normally, with the motors still on, skips `KZ_ALIGN_BOTTOM` and `G28 X Y`;
  the Z tap (and the K2 Plus Z tilt) still runs.
  `[gcode_macro START_PRINT] variable_warm_start: False` always homes in full.
- **Chamber light auto-off:** `[gcode_macro _LIGHT_IDLE] variable_off_after:`
  seconds after a print, a cancel or start-up (0 = stays on, the default).
- Idle timeout messages show the configured time; `[idle_timeout] timeout:`
  in printer-overrides.cfg sets how long before heaters and motors go off.
- **Network announce:** Moonraker `[zeroconf]`, added to existing installs.
- **Nozzle tap with the fans down:** like stock `prth_min_fans`, the hotend
  fans drop to 60% while the strain gauge is armed (8 s settle,
  `prth_fan_settle`), held through the tap. Fan vibration fired hot taps on
  the K2 Plus with nothing touching. The safety watch checks the hotend fan
  only at full power.
- **Failed bed mesh:** the nozzle is cleaned and Z and the mesh measured
  again (`variable_dirty_retries: 2`) before the print stops and asks for a
  nozzle and plate clean.
- **K2 Plus first layer:** Creality's own hot-nozzle correction
  (0.017316 + 5.1e-6 x (print - tap temperature)^2, 0.050 mm for PLA at
  220C). `variable_z_offset` is your fine-tune, in Creality's direction
  (negative = nozzle closer).
- **K2 Plus beta, first prints on a real K2 Plus:** hotend fans on toolhead
  PB7 + main PB1 as stock; G28 outside a print sets Z from the bottom
  switches first; scrub and probe points from stock; chamber heater
  supervisor (`[hark_chamber]`, off until enabled). A printer already on the
  K2 Plus config now updates with the Update button like a K2 (the first
  install still needs `OKL_K2PLUS_BETA=1`).

## v0.4.3-alpha.1 (2026-11-01)

- **K2 Plus (F008) beta config** (`k2plus-beta` branch, untested on a real
  K2 Plus): `config/mainline_stage2_k2plus.cfg`, generated from the K2 config
  by `tools/k2plus/make_k2plus_cfg.py` from Creality's stock K2 Plus 1.1.7.0
  values. Two closed-loop Z motors woken on the motor bus (`bus_motors`),
  extruder address list (`extruder_addr`), two bottom switches in
  `k2_z_align` (`endstop_pin: PA15, PA8`) plus `KZ_ALIGN_MEASURE`,
  `Z_TILT_ADJUST` in START_PRINT, `CHUTE_POSITION_CHECK`. The installer takes
  the K2 Plus only with `OKL_K2PLUS_BETA=1` on the board layout the config
  was written for. Tester guide: docs/K2-PLUS-BETA.md.
- **Filament sync with stock OrcaSlicer:** lane_data now carries Orca's own
  filament id for Creality filaments (`klippy/extras/hark_orca_ids.json`,
  built by `tools/orca/make_orca_filament_ids.py` from Orca's Creality K2
  profiles), so OrcaSlicer 2.5.0-beta and newer pick "Hyper PLA @K2-all"
  instead of "Generic PLA". Our own library id moves to `k2_filament`. Setup
  in docs/INSTALL.md, "Filament sync with OrcaSlicer".
- Fixed: after a restart Orca could see empty CFS slots. Two lane_data posts
  raced at start-up (CFS not found yet, then found) and the older, empty one
  could land last; one poster now always sends the newest slots.
- hark_safety watches every motor k2_motor_bus wakes (Z and Z1 on the K2
  Plus) instead of a fixed X/Y/E list.
- Chamber fan: `variable_prefer_material: 1` uses the material table even
  when the slicer sends a chamber temperature (OrcaSlicer's K2 filament
  profiles all send `M191 S35`).
- **The touchscreen shows what the printer is doing** during a print start,
  load, tool change, runout refill or calibration: the current step
  (homing, heating, loading slot X and purging, measuring pressure advance,
  cooling and wiping, Z tap, bed mesh, purge, prime line...) with a timer,
  the earlier steps with their times, the total set-up time and the latest
  console line. Macros set it with `_STAGE TEXT="..."` (`printer["gcode_macro
  _STAGE"].text` for other UIs).
- **Calibrate PA on the touchscreen and in Mainsail runs the stationary
  test** (`PA_CAL_STATIC SLOT=x SAVE=1`, with the per-material scale) instead
  of the older moving one, and the touchscreen shows its result (saved value,
  measured value and scale). With auto PA on load, "load then calibrate" no
  longer measures twice.
- **Soft filaments (TPU, TPE):** loads, the print start's prime-out, flush
  and prime line run them at a flow cap (`[gcode_macro _SOFT_FILAMENT]
  variable_max_flow`, default TPU 3, TPE 2 mm3/s) so they don't buckle in the
  extruder gears, and the automatic pressure advance test is skipped for
  them (the slot keeps its saved PA).
- Prime line: the nozzle lifts 1 mm after the Z0 wipe, so the slicer's first
  travel no longer drags it across the plate.
- **Mainsail updates come from the Hark releases only**
  (grant0013/mainsail, full releases): stock mainsail-crew/mainsail would
  replace the Hark build. The updater's beta channel takes the newest
  published release (GitHub lists the one marked Latest first).
- **External Klipper host: updates from Mainsail's Update Manager.** Each
  release now carries `k2-openklipper-host.zip`
  (`tools/release/make_host_zip.py`, attached by a release workflow), which
  Moonraker installs with `[update_manager k2-openklipper]` (type zip). Setup
  in docs/EXTERNAL-HOST.md, "Updating a host".

## v0.4.2-alpha.1 (2026-10-28)

- **CFS lights stay white:** the driver now talks to the CFS every 2 s
  (`env_poll_interval`, was 10 s). With the longer gap the CFS showed red
  flashing (not connected) in quiet stretches such as the print start's mesh,
  until the next feed.
- **Chamber fan by material:** without a slicer `M141`, the print start
  takes the chamber target from the loaded filament
  (`[gcode_macro _CHAMBER_FAN] variable_material_targets`, default PLA/TPU
  35C, PETG 40C, ABS/ASA/PC/PA 50C); the exhaust fan vents 2C above it.
- Hark Screen switches the panel on at start (a restart could leave it dark
  if the previous copy had put it to sleep).
- **End-of-print vent** (opt-in): `variable_end_vent: 180` runs the exhaust
  fan at full for that many seconds after a print or cancel, to clear fumes
  and cool the chamber. The next print start stops it.
- **Printed flow and retraction tests from the screen or Mainsail**
  (`[hark_calib]`): Flow test and Retraction test in the slot menu (screen)
  and slot dialog (Mainsail), or `CALIBRATE_FLOW SLOT=x` /
  `CALIBRATE_RETRACTION SLOT=x`. The printer writes and prints the test
  (nine labelled flow-ratio pads; seven labelled pillar pairs, each pair's
  travel retracting by its own length), then asks for the best one, a
  picker on the touchscreen and the Mainsail card plus a Mainsail prompt.
  The pick is saved to the slot (`FLOW=` / `RETRACT=`), so OrcaSlicer and
  the next print use it. Mainsail revision 3.

## v0.4.1-alpha.1 (2026-10-28)

- **Automatic pressure advance when a slot loads** (opt-in):
  `[gcode_macro _PA_AUTO] variable_on_load: 1` measures it with the nozzle
  still over the chute after `CFS_LOAD_SLOT` / `LOAD_FILAMENT`, and
  `variable_on_print_start: 1` when a print start loads a different slot. A
  plausible result is saved to the slot (`PA_CAL_STATIC ... SAVE=1` does the
  same by hand); a failed one is reported and the load carries on.
- `RUN_SAFE RUN= FINALLY= [ON_ERROR=report]` ([hark_safe_run]): runs a macro
  and always its cleanup. `PA_CAL_STATIC` uses it, so a failed test no longer
  leaves the nozzle hot.
- **Stronger nozzle wipe** (opt-in): `[k2_cfs] clean_brush_strokes` adds
  full-length strokes across the silicone brush (`clean_brush_left_x` /
  `clean_brush_right_x`, about X146-158 on the F021) after Creality's
  circles, and the wipe speed and acceleration limits are lifted (e.g.
  `clean_speed: 300`, `clean_circle_speed: 250`, `clean_accel: 10000`). It
  took hardened filament off that the stock wipe left (10-28).
- PA test: 2 mm pressure relief afterwards, so the tip does not weep after
  the wipe. Auto PA can scale the stationary value to the printed one per
  material (`[gcode_macro _PA_STATIC_SAVE] variable_scale_material`, e.g.
  `{'PLA': 1.34, 'PETG': 1.13}` from chevron patterns,
  `tools/pa/pa_pattern.py`).
- **First layer, after the K2 Plus firmware's method:** per-material
  offsets, `variable_hot_z_material: {'PLA': -0.01}` in `START_PRINT`, added
  to `hot_z_comp` (material from `START_PRINT MATERIAL=` or the loaded slot's
  record), alongside its nozzle growth rate (`variable_hot_z_rate`, 0.00083
  mm/C in that firmware).

## v0.4.0-alpha.1 (2026-10-28)

### Printers without a CFS, and any CFS found by itself

- **K2 without a CFS** (filament from the rear spool holder), handled like
  stock and on the fly: new `[k2_cfs] cfs_fitted: auto` (default) looks for
  the CFS at every start, and a CFS unplugged or plugged in while the printer
  is idle is noticed within about 30 s with no restart (`True` always expects
  one, `False` never uses one; `OKL_CFS=yes|no` for the installer). The
  external spool has its own filament record, slot `E` (`CFS_SLOT_SET SLOT=E
  ...`), which OrcaSlicer sees as its first filament without a CFS. Without a
  CFS the box is not polled, feed and pre-load calls do nothing, the print
  start primes the spool at the chute, T0 uses slot E and T1-T3 stop with a
  message. With `True` and a CFS that does not
  answer, print starts and CFS loads stop before homing or heating. New `LOAD_FILAMENT` and
  `UNLOAD_FILAMENT` (Mainsail Load/Unload buttons): heat, feed and purge at
  the chute; cut at the knife and pull back. Runout pauses with a plain
  message. Mainsail (revision 2) shows an External spool card in place of the
  CFS card and the touchscreen an External spool page in place of the CFS
  page, with filament editing, load/unload and pressure-advance calibration;
  Mainsail's slot dialog now also has load, unload and calibration buttons
  for CFS slots. See docs/INSTALL.md, "Printers without a CFS".
- **The CFS is found without a configured id.** The shipped config carried
  our test printer's CFS id (`cfs_uniid`), which is what re-addresses the CFS
  after a power-off, so another printer's CFS could go unanswered after a cold
  start. At startup the driver now asks the CFS at its address, and if it does
  not answer, uses the stock discover broadcast (`00 a1`, after Jacob10383's
  AutoAddressClient) to get the id and give it back its address; a CFS
  powered up while the printer runs waits in its loader and is started with
  the loader-to-app broadcast (`00 0b 01`), as Jacob10383's firmware does.
  `cfs_uniid`
  is optional (the installer copies it from Creality's config); `cfs_detect`
  turns the search off.

### Ported from Jacob10383's K2 Plus firmware

Logic adapted (GPLv3, with credit in each file) from
[Jacob10383/k2-plus-custom-firmware](https://github.com/Jacob10383/k2-plus-custom-firmware).

- Motor fault detection fixed: a faulted controller answers with a status
  byte of 02, which the watch used to read as no answer, so it never tripped.
  Replies are now parsed and decoded (logic adapted from Jacob10383's K2 Plus
  firmware): only an error code pauses or cancels, and the message names it
  (for example "excessive position tracking error"). Warnings such as low
  supply voltage or a hot controller MCU are reported and the print carries
  on. Print start reads, clears and re-reads the motor faults.
- `[k2_motor_bus]` checks replies (CRC, sender address, function echo) so a
  late CFS reply or noise is not taken as a motor reply, and retries motor
  reads once (`reply_check`). `MOTOR_STALL_MODE` reports a missing echo
  (`stall_mode_check: abort` stops the homing instead). New diagnostics:
  `MOTOR_FAULTS`, `MOTOR_CLEAR_FAULTS`, `MOTOR_TEMPS`, `MOTOR_E_ADDR`, a
  calibration check at wake, optional `homing_fault_check` and
  `temp_poll_interval`.
- PRTouch: fail-closed arming (the MCU's acknowledgement is checked, a probe
  is refused when the trigger line already reads contact, and a line that
  does not release is reported), the 64-sample pressure buffer is logged when
  a probe never triggers, and mesh taps that trigger far above the bed (for
  example on a plastic blob) are re-probed once, then stop the mesh. Optional,
  off by default: multi-tap selection (keep the tightest taps of a window) for
  the Z home and mesh points, a temperature-scaled hot-nozzle correction
  (`variable_hot_z_rate`), scrub-until-clean before the Z tap, one retry after
  a missed probe.
- PRTouch: `PRTOUCH_SCRUB`, the K2 Plus strip-detecting nozzle scrub. Three
  normal and three firmer taps (MCU hold thresholds x 2.5) at the strip centre
  tell a flexible tab from a solid strip and set the depth (0.15 or 0.05 mm),
  then the nozzle scrubs the strip line following its measured slope and
  repeats until the contact stops dropping. K2 coordinates (X155-165,
  Y264.5-266.5) come from the existing strip scrub. Opt-in:
  `NOZZLE_CLEAR METHOD=detect`, or `variable_scrub_method: 'detect'` in
  `START_PRINT` to scrub before the Z tap; settings `scrub_*` in
  `[prtouch_mainline]`. Default print starts are unchanged.
- CFS: a same-filament runout refill purges 40 mm instead of the full colour
  purge (`runout_purge_length`), box BUSY replies are retried, a final load
  nudge that barely moves (0x0E) counts as success when the nozzle sensor sees
  filament, every status code has a plain-English message, slot-event replies
  are no longer read as temperature and humidity, tool changes accept the slot
  feeding the print when the box reports none (checked against the hub
  sensor), and the load sees filament arrive within 0.1 s. Optional, off by
  default: a PTFE-freeing toolhead sweep before stall retries, purge lengths
  from the file's flush matrix, clog detection from the measuring wheel.
  `CFS_RFID_READ` reads a slot's RFID record. `CFS_STATUS` no longer wipes the
  feeding slot.

### Pressure advance and flow

- `PA_CAL_STATIC SLOT=x`: pressure advance from nozzle force with the nozzle
  standing still over the chute (the way Bambu's force-sensor printers do it),
  using Dmitry Butyugin's `pa_tester` (GPLv3, from his Klipper branch) with the
  K2 strain gauge as its load cell. The gauge samples in bursts, so step
  lengths are varied (`segment_jitter`) and steps whose start was not sampled
  are left out (`max_start_gap`); both the speeding-up and slowing-down steps
  now fit. On our printer: PLA 0.048, PETG 0.065.
- Flow tools (`tools/flow/`): a maximum-flow test that steps the flow over
  the chute and finds where the nozzle pressure leaves its trend (PETG about
  16 mm3/s, PLA about 22-24 mm3/s at 250/220C here), and a flow-ratio pad
  generator (nine labelled pads around the slot's ratio; pick the smoothest
  and save it with `CFS_SLOT_SET SLOT=x FLOW=`).

### Other

- `OKL_UPDATE_CHECK` always shows the update dialog (a Mainsail opened after
  the timed check never saw it).
- External host guide: the two-file config layout, delete the shipped ARM C
  helper on a PC, and how to update a host.

## v0.3.0-alpha.1 (2026-10-27)

### Updates and settings

- Updates from GitHub releases only (tested versions, never branches): a
  dialog in Mainsail with **Update now**, an **Update** row on the
  touchscreen's Machine page, `OKL_UPDATE_CHECK` / `OKL_UPDATE CONFIRM=1`
  (`[hark_update]`), and `install.sh --update`. Never offers an older version.
- The configuration is now `k2-openklipper.cfg` (managed, refreshed from the
  installed version on every start) plus `printer-overrides.cfg` (this
  printer's settings, loaded last, never touched by updates). The first
  update moves a v0.2 install's settings across. SAVE_CONFIG blocks in
  printer.cfg are kept.
- `creality-update.sh`: saves your settings to the G-code folder and returns
  the printer to stock for a Creality firmware update; the installer restores
  them afterwards.

### Stock features

- Runout auto-refill: `CFS_RUNOUT_REFILL` loads another slot with the same
  material and colour from the runout pause and resumes; if none matches, or
  anything fails, the print stays paused as before.
- Early runout warning: when the end of a spool passes its CFS slot's entry
  sensor mid-print, the console, Mainsail's status line and the touchscreen
  say which slot takes over (or that the print will pause), while about a
  tube's length is still on its way to the nozzle.
- Automatic chamber fan: the exhaust fan vents above the chamber target (35C,
  stock's watermark) while printing; M141/M191 set the target; an explicit
  M106 P1 / M107 P1 wins for that print.
- Creality's Fluidd page (4408) talks to the open stack's Moonraker.
- Hark Screen stops Creality's screen app (`display-server`), which could draw
  over it after an install without a reboot and used CPU Klipper needs;
  `switch-to-stock.sh` and the uninstall start it again.
- No nginx access logs: on the K2 they live in RAM, and a camera-polling tab
  filled 222 MB overnight.

### Installer

- `install.sh` / `uninstall.sh`: one-command install next to the stock
  firmware with full backups, and one command back to stock
  ([docs/INSTALL.md](docs/INSTALL.md)). Tested end to end on a stock K2
  (install about a minute, reboot, test print, uninstall, clean reinstalls).
- Asks whether the stock extruder or a Phaetus DXC2 is fitted and sets the
  post-cut pull length (10 or 20 mm) and cutter depth (the printer's own
  Creality calibration for stock; at least -8.0 for the DXC2). The shipped
  config now defaults to the stock extruder.
- Waits for Klipper after install and clears a main board left shut down by
  stock Klipper.
- Edits the printer's own `/etc/init.d/app` in place (removes master-server,
  Monitor and upgrade-server from its start list) instead of shipping a
  modified copy of Creality's script; `system/app.init.mainline` is gone.
- Warranty note in the install guide.

### Start sequence

- The cool-wipe no longer parks at the far-right return point between its
  four wipes (`NOZZLE_WIPE PARK=0`); the head stays over the chute.

### Klipper on a separate PC (external host)

- Klipper and Moonraker can run on any Linux PC, connected to the K2's
  mini-USB port. The K2 board turns into a three-port USB serial gadget and
  relays its main MCU, nozzle MCU and motor/CFS bus (`host/`,
  [docs/EXTERNAL-HOST.md](docs/EXTERNAL-HOST.md)). klippy went from about 108%
  CPU on the K2 to a median 5% of one core on a Celeron host; the first
  7.5-hour print had zero "Timer too close" and zero invalid bytes.
- Automatic reconnect when the K2 reboots or the cable is replugged.
- The K2's internal camera works on the host with a USB-A lead
  (`k2-camera.service`, ustreamer MJPEG, 15 fps).

### Touchscreen

- Generic default look: the Klipper logo and Klipper's colours, header
  "K2 OpenKlipper". The Hark Tech look moved to `harkscreen/themes/hark-tech.cfg`.
- [docs/SCREEN-CUSTOMISE.md](docs/SCREEN-CUSTOMISE.md): your own name, logo,
  colours, font and pages, edited in Mainsail and applied live.
- A mistyped number or a broken line in `harkscreen.cfg` no longer stops the
  screen: it falls back to the default or keeps the last good settings.

### Print safety

- `[hark_safety]`: pauses when the hotend or part fan is commanded on but its
  tachometer reads zero, cancels on an X/Y motor controller fault, and pauses on
  an extruder controller fault (clog or jam). Stock firmware did this in
  master-server and motor_control, which the open stack does not run.
- Hotend `max_temp` back to the stock 350.
- `PAUSE` lifts the nozzle 2 mm and parks it off the part. A job left paused
  for 30 minutes turns the hotend off but keeps the bed and homing, so it can
  still resume.

### First layer and print start

- First-layer height correction `hot_z_comp` (0.04 mm): Z is probed with the
  nozzle at 140C but printed hotter. Stock PRTouch corrects this
  (`temp_non_linaer_c` 0.045); the open driver did not, and edge-to-edge first
  layers lifted.
- Faster start: the bed waits only while it is still heating (no M190 settle
  or cool-down wait), and mesh taps run at 3 mm/s. Tap speeds of 0.75, 3 and
  5 mm/s measured the same bed shape.
- Cold starts get an extra 90 s soak, because the plate edges warm after the
  centre.

### AI failure detection (`tools/ai-detect/`)

- Obico's failure model on every 10th second, with a local vision-language
  model (Qwen3-VL 4B on llama.cpp, CPU only) as a second opinion: it confirms or
  drops Obico warnings and takes its own look every 3 minutes. Runs on the
  host PC, alerts only by default.

### Colour changes

- Cleaner cuts: cut position X-8.0 at acceleration 5000; the CFS rewind now
  takes about 15 s instead of 24 s, and the "nozzle sensor still detects
  filament" stops are gone (23/23 clean on a two-colour test print).
- Automatic clearing of a snapped piece left under the nozzle sensor.
- The tool change no longer waits for a fully settled PID; travel at
  Creality's speeds. About 101 s per change, down from 113-131 s.
- Chute purge per change from the slicer's flush matrix (`{flush_length}`),
  run as Creality's pulsed flush blocks with a 5 s fan blast and a full wipe.

### Print start

- Nozzle cleaning copied from Creality Print's K2 profile, including its
  front-edge prime line.
- The adaptive mesh covers the prime tower when the slicer passes its
  position (`TOWER_X`/`TOWER_Y`/`TOWER_W`); the tower no longer comes loose.
- `BED_MESH_CHECK` stops a print on an implausible mesh.
- CFS load, unload and PA calibration take Z from the bottom switch, with no
  nozzle contact.

### Ideas from Kalico, as add-on modules (no core changes)

- `RELOAD_GCODE_MACROS`: reload macro bodies without a restart.
- `PID_PROFILE`: named hotend PID profiles, loaded per CFS slot material.
- `BED_MESH_CHECK`: deviation and slope limits on the probed points.

### Tools

- `tools/multicolour-test/`: a generated two-colour pumpkin test print.

## v0.2.0-alpha.1 - 2026-10-25

### New

- **Strain-gauge pressure advance calibration** (`PA_CAL_RUN`,
  `pa_pressure_log.py`, `pa_analysis.py`): per-slot PA measured over the purge
  chute, validated by a printed comparison. See `docs/PA-CALIBRATION.md`.
- **CFS slot records** (`hark_cfs_slots.py`): per-slot filament, temperatures,
  PA, flow and retraction, applied on slot selection and mirrored for
  OrcaSlicer.
- **Head accelerometer driver** (`lis2dw_k2.py`) for `SHAPER_CALIBRATE` on
  Creality's nozzle-board LIS2DW protocol.
- **Hark Screen** touchscreen UI and **harkcam** MJPEG camera for Mainsail.
- **Motor controller tooling**: `MOTOR_BUS_SEND READ_LEN/TIMEOUT` for
  telemetry, a full parameter map, scan and A/B test tools. See
  `docs/MOTOR-CONTROLLER.md`.

### Print start

- One heat cycle before probing: CFS load/purge at temperature over the chute,
  a cool-wipe, a clean precise Z tap, a bed-strip scrape and the mesh.
- PA15 bottom-switch Z reference on every print (`k2_z_align` now allows it
  until the first extrusion).
- Adaptive heat soak for jobs without a slicer tool.
- The swamp-and-drag purge line keeps a margin from the parts.

### CFS

- Idle pre-loading (spool take-up) restored after boot and failed loads.
- EXTRUDE_ERR8 retry, stale active-mask cleanup, a purge-tail retract and a
  fan blast before the wipe.
- `CFS_CHANGE_SLOT` and `CFS_LOAD_SLOT` for idle slot changes; firmware
  retraction set per slot.

### Tuning on F021

- Input shaper X mzv 54.8 Hz / Y mzv 45.4 Hz, extruder PID at 250 °C,
  minimum cruise ratio 0, `pressure_advance_smooth_time` 0.06.
- `[gcode_arcs] resolution: 0.5`. 0.1 starved the move queue on an arc-heavy
  organic model (MCU "Stepper too far in past"); 1.0 shows chord joints.

### Fixes

- Manual G28 no longer runs the in-print nozzle wipe; the in-print wipe is
  skipped around START_PRINT's own Z home.

## v62 - 2026-07-29

### Printer control

- Improved the experimental host PRTouch fallback classifier with contact
  hysteresis while retaining conservative entry detection and three-window
  persistence. The validated v62 motion stop remains the PA15-to-PC7 hardware
  edge through native Klipper trsync.
- Kept Creality's rough-home, thermal-settle, nozzle-clean, precision-Z-home
  sequencing before adaptive bed meshing.
- Moved to the purge bucket immediately after adaptive mesh so nozzle ooze
  cannot land on the measured surface.
- Corrected CFS steady-feed validation and failure cleanup.

### Scheduling and UI

- Restored Creality's factory-safe 1.0 mm arc resolution after a 0.1 mm setting
  caused dense move queues and a dual-MCU timing shutdown.
- Added isolated Mainsail 2.18.2 on port 4409 and Moonraker on port 7126.
- Kept the legacy Fluidd/Moonraker pair on ports 4408/7125 for rollback.
- Exposed all active open-stack configuration through Mainsail.

### Final print-start fixes

- Disabled the late firmware-incompatible Klipper updater construction that
  left Moonraker's connection task incomplete and caused 20-second
  `/printer/print/start` timeouts.
- Added the safe OrcaSlicer initial tool-change Z guard and removed the source
  of the pre-first-layer left-side bed scrape.

### Live validation

- Verified Moonraker ready with no warnings or failed components.
- Verified a safe nonexistent-file start probe reached Klipper in 0.061 s.
- Verified real Orca upload and print start.
- Verified homing, nozzle cleaning, second PRTouch home, adaptive mesh,
  purge-bucket parking, CFS purge, and first-layer printing.
- Benchy passed 32% with no pause, MCU timing error, or reported toolhead
  stall at the saved validation point.

### Known items

- A completed long print is still required for production qualification.
- Print-duration accounting includes startup/CFS activity incorrectly and may
  display an unrealistic value in the UI; motion and virtual-SD progress are
  unaffected.
- Creality's stock touchscreen control plane is unavailable while
  `master-server` remains disabled.
