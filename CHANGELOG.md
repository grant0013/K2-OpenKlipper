# Changelog

## Unreleased

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
