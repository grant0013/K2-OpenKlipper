# Changelog

## Unreleased

Fixes from a code review of v0.4.24-alpha.1. None of these have been run on a
printer yet.

- **First print on a new K2 no longer stops at the bed voltage check.**
  `BED_POWER_CALIBRATE` refused to run inside `START_PRINT`, its automatic
  caller.
- **A failed home no longer ends motor fault watching** for the rest of the
  session.
- **A runout outside a print no longer sticks.** The next print could not
  pause and ignored a real runout. A second `PAUSE` no longer lifts and parks
  again.
- **Runout refill works in the first few layers.** It now raises to the
  height the chute needs, as colour changes do.
- **Resume after a failed colour change re-arms the CFS feed** when the
  filament was already at the nozzle.
- **`RELOAD_GCODE_MACROS` is refused during a print**, where it can stall
  the host and shut the MCU down.
- **A stopped hotend fan switches the nozzle off** as well as pausing. Resume
  reheats it.
- **Loading the rear spool during a long pause uses the print's temperature**,
  so Resume does not carry on at the load's. `RESUME` with nothing paused no
  longer heats anything.
- **The K2 Plus chamber over-temperature cut-off can now fire.** It needed
  heater power, which is already off at that point.
- **Power-loss resume**: refuses a part too tall for the Z reference instead
  of pushing it into the nozzle, and excluded objects stay excluded.
- **Typos and full disks give an error instead of shutting Klipper down**:
  `FLOW_CAL_RUN FLOWS=`, `CALIB_PICK VALUES=`, `MOTOR_VALUES INDEX=`,
  `MOTOR_BUS_SEND PAYLOAD=`, saving slot records, `PA_LOG_STOP`, a motor bus
  that cannot open, and CFS bus errors at print start.

## v0.4.24-alpha.1 (2026-11-09)

- **The max flow test goes further.** The ramp now runs to 40 mm^3/s instead
  of 24, where nearly half of the runs on one K2 found no limit. It is one
  continuous run that stops at the limit, because a pause partway makes every
  step after it read low. Force falling away under the line now counts as a
  limit as well as force going over it, and a noisy baseline needs a bigger
  bend. A sudden loss of force stops the ramp at once (the idea is
  FlowTune's). "No limit" now says the flow it reached. The log line
  `extruder max flow: no cap` now reads `none set`, because it only means
  no test set one for that print.
- **Print time no longer jumps to days.** A CFS check at print start could
  add the host's uptime to the print time in the history.

## v0.4.23-alpha.1 (2026-11-09)

- **The host-link relay reconnects after a USB drop.** It kept the dead USB
  ports open after a disconnect, so Klipper could not reach the boards again
  until the K2 was rebooted. It now reopens them when the host comes back.
- **Power-loss resume heats up before anything moves.** The nozzle stops on
  the part and the plastic sets round it, and `PLR_RESUME` lowered the bed
  with the nozzle cold, which pulled the part off the bed. It now heats the
  bed, then the nozzle to print temperature, and only then moves. The first
  real resume of a stopped print showed this.

## v0.4.22-alpha.1 (2026-11-09)

- **The last probe lift finishes before a bed mesh is built** (GitHub issue
  #4). Building a dense mesh holds up Klipper for up to a second on the K2's
  board, and the `M400`s added in v0.4.21-alpha.1 only ran after the build.
- **The mesh guard no longer stops a mesh on a warped bed** (GitHub issue
  #3). It compares each point to its neighbours, so real curve looks
  abnormal, above all at the edges, and with more than
  `mesh_guard_max_points` of those it gave up with no mesh at all. It now
  warns, re-checks the worst points and carries on, and `BED_MESH_CHECK`
  still judges the result.
- **A probe that runs out of travel now says what to check**: the bed
  stopping short (`position_endstop`, `KZ_ALIGN_MEASURE`) or a dirty nozzle.

## v0.4.21-alpha.1 (2026-11-08)

- **Printing by object now gets the full purge at a colour change.** Between
  two slots of the same material the purge could be as short as 30 mm, which
  relies on a prime tower or infill taking the rest. By object has neither,
  so the next object's first layers blended (a grey to black PETG job at
  flush multiplier 0.3). These files now purge at least the stock 160 mm,
  and a flush multiplier below 1 is ignored. Painted prints are unchanged.
- **The nozzle scrub follows Creality's own sequence and presses harder.**
  Traced from the stock `prtouch_v3` code: hot strokes start at the stock
  position and run 10 mm right, then fans on and cool strokes further right,
  Y fixed, then the nozzle stays down on the strip until it reaches 140 C.
  K2 X160-170 then X175-180, K2 Plus X185-195 then X200-205. `NOZZLE_CLEAR`
  now scrubs from 180 C at print start (it was 140 C) and presses 0.32 mm
  below its height tap (`PRESS=`, was 0.1), which cleaned the tip on one K2
  where 0.1 did not. Stock presses 0.6. The tap and scrub take about 2
  minutes 20 seconds. The K2 Plus now scrubs by default too, untested on that printer
  (`variable_scrub_strip: False` turns it off).
- **The print-start mesh waits for the toolhead around `BED_MESH_CHECK`**
  (GitHub issue #4, mylife4aiurr). With `M400` after `BED_MESH_CALIBRATE` and
  after `BED_MESH_CHECK`, a 21x21 mesh at `mesh_pps: 2, 2` finished without
  the `Stepper too far in past` shutdown it had before. That is one run on
  one K2 Plus. `mesh_pps: 1, 1` remains the advice for dense meshes.
- **K2 Plus guide: set the measured `position_endstop` even when it is close
  to 349.5.** The guide said to change it only above 1 mm; 0.6 mm was enough
  to fail the nozzle tap on one printer (GitHub issue #3).
- **The stepper threads use both of the K2's cores.** klippy is pinned to
  CPU1, so its one step generation thread per stepper queued up on one core
  while Python waited for them. New `[hark_cpu]` frees those threads while
  Python and the serial threads stay on CPU1, which cut a batch run on the
  T113 by 12%. It does nothing on a host PC.
- **`[printer] buffer_headroom` (experimental, off by default).** Keeps extra
  seconds of motion queued. It is aimed at the host stalls in GitHub issues
  #4 and #5, but stall tests on a host PC showed no fewer shutdowns, so no
  config turns it on. Pause and cancel respond that much later.
- **T0-T3 wait for the prime line before touching the CFS** (GitHub issue #5,
  Promotheux: every CFS print on a fresh install ended in `Stepper too far
  in past`). The `T<n>` line in the slicer's start G-code runs right after
  `START_PRINT`, with the prime line still queued, and the CFS work behind
  it ran while those moves were executing. T0-T3 now wait for the moves
  first, so a file sliced with an older profile is covered, and the Orca
  profiles, `docs/INSTALL.md` and `config/ORCA-K2-PROFILE.cfg` add `M400`
  before the `T` line. Re-import the profile when convenient; it is not
  needed for this fix.
- **Low colour changes no longer pause on the bed mesh.** A change below
  about Z 2.5 lifted to exactly `purge_min_z`, and a negative mesh offset at
  the chute left it a hair short, so the load was refused and the print
  paused (`CFS move requires Z >= 4.500 (current 4.496)`). The change now
  measures the height at the cut position and again before the load, and
  lifts by what is missing.
- **Bed mesh note for a high `probe_count`** (GitHub issue #4, nur00):
  `config/printer-overrides.cfg.example` now says to use `mesh_pps: 1, 1`
  above about 13x13. 19x19 and 21x21 full-bed meshes at `mesh_pps: 2, 2` shut
  a K2 Plus down as the mesh finished at a print start; 21x21 at `1, 1`
  printed. The stall itself is not fixed yet.

- **The chamber camera recovers from a USB drop by itself.** A USB error made
  the camera drop out and come back, Creality's `cam_sub_app` took it, and
  `harkcam` served its last frame for 21 hours as if it were live, blinding
  an AI print watcher. `harkcam` now stops a Creality camera app holding the
  camera, restarts a silent `ffmpeg`, and answers 503 instead of an old frame.
  The AI watcher now counts a repeated frame as no frame, so a frozen feed
  raises its "watcher is blind" alert (`tools/ai-detect/watch.py`).

- **Other brands sync to their own Orca preset**. A CFS slot holding a
  library filament from Elegoo, Bambu Lab, Polymaker, eSUN, SUNLU, JAYO or
  Overture now reaches OrcaSlicer with that preset's filament_id, so it
  syncs as, say, "Elegoo PLA @System" instead of "Generic PLA". 61 of the
  63 library filaments have an Orca preset; DeepLee has none and stays
  generic.

- **Choose the slot each filament prints from** (GitHub issue #3,
  masteryodha: a file started from the touchscreen always printed from slot
  A, and the rear spool could not be used with a CFS connected). Like
  Creality's mapping step: the touchscreen's file details show the file's
  filaments (colour, number, material) and the slot each will print from,
  guessed by material and nearest colour among the loaded slots; tap a slot
  for a box of every slot and the rear spool, and Print sets the map and
  starts. From Mainsail or the
  console: `CFS_TOOL_MAP T0=C T1=E ...` (`RESET=1`, `FILE=`, no arguments
  shows it). Slots are ids with room for more CFS boxes later (`A`-`D`
  today, `2A`-`4D` recognised and refused). T0-T3 and START_PRINT follow
  the map (`printer.hark_cfs_slots
  .tool_map`); it is kept through a power-loss resume and cleared when its
  print ends, also after an error.
- **The external spool with a CFS fitted** (slot `E`). As on Creality's
  firmware a print uses the rear spool or the CFS, not both: mapping a
  filament to `E` prints the whole file from it, with the box idle.
  `LOAD_FILAMENT SLOT=E` unloads the CFS slot in the toolhead first, then
  loads the rear spool; `UNLOAD_FILAMENT SLOT=E` unloads it; START_PRINT
  stops before heating if a slot is still loaded or the spool is not at the
  toolhead; a runout pauses for a new spool instead of a CFS refill.
  Without a CFS, T1-T3 can now be mapped to the rear spool too (one colour)
  instead of stopping the print. Not yet tried with a CFS on a real printer.
- A mid-print change reads the file's flush matrix by its tools, not by
  slot letter (they differ once filaments are mapped).
- Tests: tests/test_cfs_tool_map.py (the map, and the T0-T3, START_PRINT,
  runout and load/unload macros rendered from the real config), filament
  step tests in tests/test_harkscreen_files.py.

- **`RESUME` loads and purges a new spool after a runout.** With no matching
  slot the print pauses for a new spool, but `RESUME` then carried on with
  nothing at the toolhead and the print went on in the air. If the toolhead
  has no filament, `RESUME` now reheats, pushes the old tail through, loads
  the slot, purges and wipes first; if that fails the print stays paused.
  Without a CFS it stays paused and asks for the spool to be loaded.
- **`RESUME` reheats after a long pause.** The idle timeout turns the hotend
  off during a pause; `RESUME` brings the heaters back and waits for them.

Camera `mode: webrtc`, from nur00's K2 Plus tests (GitHub issue #4):

- **At most `max_streams` viewers.** Each viewer is its own session in
  `webrtc_local`; three at 1080p filled the core that serves WiFi and the MCU
  serial lines. A third viewer is now refused.
- **Always 1280x720.** Creality's start picks 1080p on some models.
- **The wrong camera type can no longer leak.** Mainsail's "WebRTC (MediaMTX)"
  left a session running with no video and nobody watching. Its offer is now
  refused with a message, and `start.sh` no longer tells you to use it.
- **Switching from `mjpeg` by hand no longer leaves the camera stuck.**
  `start.sh` waits for `ffmpeg` to let go of the camera, and retries `cam_app`
  and `webrtc_local` up to four times if they do not stay up.

Fixes from nur00's K2 Plus report (GitHub issue #4):

- **Mesh retry no longer aborts the print.** A failed mesh check cleans the
  nozzle and measures again, but the clean began with a retract at 140 C,
  which Klipper refuses, so the print stopped. The retract now needs a hot
  nozzle.
- **go2rtc's web UI and config editor are off the network.** Where the app
  installed go2rtc, nginx passes its video endpoints only, and `/cam` on
  port 4409 is Mainsail's camera page again.
- **Webcams show in newer Mainsail and Fluidd on every install.** `install.sh`
  now applies the `enabled` defaults to Creality's Moonraker; `uninstall.sh`
  restores the original.
- **Touchscreen:** stays on for the sleep time after a print instead of going
  dark as it ends. The light button's bulb is filled when the light is on.
- `printer-overrides.cfg.example` shows the bed mesh limits, for an unshimmed
  K2 Plus bed.

## v0.4.20-alpha.1 (2026-11-05)

- **Camera WebRTC mode (H.264), in testing** (GitHub issue #4, nur00's
  measurements). `mode: webrtc` in `harkcam.cfg` hands the camera to
  Creality's own `cam_app` + `webrtc_local`; harkcam translates for
  Mainsail's and Fluidd's "WebRTC (go2rtc)" camera type (WebSocket). It
  collects the browser's offer and candidates and sends them to
  `webrtc_local` in one call (it never connects without candidates),
  swapping Chrome's `.local` names for the browser's real address, and
  refuses the audio track in the answer. Seen working on a K2 on 5 Nov
  2026, in Mainsail and on the test page: 1280x720 at 15 fps, 224 KB/s a
  viewer against about 820 for MJPEG, `webrtc_local` at 4% CPU. No
  snapshots in this mode (time-lapse and the AI watcher need
  `mode: mjpeg`, still the default). nginx's `/webcam/` (port 4408) now
  passes WebSockets. Creality's camera apps are pinned to CPU0 (Klipper
  has CPU1), like harkcam. tests/test_harkcam_webrtc.py.

## v0.4.19-alpha.1 (2026-11-05)

- **Print a file from the touchscreen** (asked for in GitHub issue #3).
  The idle Status page has a Print a file button: the print files newest
  first with their thumbnails, print time and filament, a detail view with
  a large preview, and Print. A finished print offers Print again (through
  the detail view, so the bed is cleared first) and Other file. Hidden while
  printing or while Klipper is busy; Moonraker's reason is shown if a start
  is refused. Tests in tests/test_harkscreen_files.py (fake Moonraker,
  rendered screens); the list was also read from a real host's 46 files.
- **Quicker touchscreen.** Measured on the K2's own processor: changing
  page 513 -> 219 ms, opening a box (temperatures, fans, a question)
  806 -> 251 ms, a redraw 93 -> 61 ms, with the same look (pages
  pixel-identical). The logo and text widths are cached instead of worked
  out every frame; the halo round a box is pasted as its four edges; the
  dimming behind a box uses a lookup table; a page slide is 3 frames of the
  page body only, and a box appears at once (`transitions: smooth` brings
  back the old animations, `off` drops the slide). The frame a slide or
  fade ended on is no longer written a second time. The file list redraws
  once a page is in, not per thumbnail, loads the next page behind it, and
  scales thumbnails in the background. Previous / Next page the list.

## v0.4.18-alpha.3 (2026-11-05)

- **Fix: watching the camera could stop a print** (GitHub issue #4,
  measured by nur00 on a K2 Plus). harkcam sent 720p at 15 fps to every
  viewer: about 1.3 MB/s each with the chamber light on, which slowed the
  printer's network until the toolhead board missed its moves (`MCU
  'nozzle_mcu' shutdown: Stepper too far in past`); two viewers took the
  network down. The camera now runs at 10 fps and every viewer shares that
  one budget (streams and snapshots together), so a second tab halves each
  tab's frame rate instead of doubling the data. At most 2 streams stay
  open; a new one closes the oldest, so Mainsail's or Fluidd's fullscreen
  camera never comes up blank. Size, frame rate and the stream cap are in
  `harkcam.cfg` (new, editable in Mainsail); an update restarts a running
  camera. Tests in tests/test_harkcam.py.

## v0.4.18-alpha.2 (2026-11-03)

- **Fix: a CFS print start shut Klipper down on v0.4.18-alpha.1.** Applying
  a slot's hotend PID profile (`CFS_SLOT_APPLY`, part of every CFS print
  start) built the heater control with a stand-in config that did not
  know the new optional high-temperature PID options and raised, which
  Klipper treats as an internal error and a shutdown. Found on the first
  real print after the release; the stand-in now returns the defaults.
  Regression test. If you updated to alpha.1, update again before printing.
- **Console messages in words.** `MOTOR_STALL_MODE` says which motors
  confirmed and what the mode means; `MOTOR_FAULTS` shows the status codes
  only when a motor has any; `CFS_FEED_START/STOP` and a failed box
  command no longer print bus frames. The frames still go to klippy.log,
  and `console_frames: True` in `[k2_motor_bus]` or `[k2_cfs]` shows them
  in the console again.

## v0.4.18-alpha.1 (2026-11-03)

- **The melt limit is measured at print start and the print is held to
  it** (`hark_flow_cal`, `SET_MAX_FLOW`). After the slot is loaded and
  purged, still over the chute at print temperature, the flow steps up
  from 8 mm^3/s in 3 s steps while the nozzle strain gauge is read; where
  the pressure leaves the straight line of the low flows by 4% is the
  knee, and the print's moves are capped 3 mm^3/s under it. Purges, primes
  and the slicer's own limit are untouched; `END_PRINT` and `CANCEL_PRINT`
  clear it. Seven runs on CR-PETG at 250 C put the knee at 14-17 and the
  cap at 11-14 mm^3/s; 16 had printed gaps in inner walls. Off by default:
  `[gcode_macro _FLOW_AUTO] variable_on_print_start: 1` in
  printer-overrides.cfg turns it on; `FLOW_CAL_STATIC [SLOT= TEMP= APPLY=]`
  runs it by hand. The flow ramps that led here also showed that 260 C
  lowers the pushing force 20-37% but does not move the knee: the melt
  zone is the limit. Tests: tests/test_hark_flow_cal.py,
  tests/test_extruder_max_flow.py.
- **Every read on the extruder motor's bus came back one request late.**
  The nozzle board's bridge answers with whatever its buffer already
  holds, and the controller's reply lands after that, so the E motor's
  fault status, temperature and stall reads were always the previous
  answer. The bridge is drained first now. New diagnostics on the same
  bus: `MOTOR_VALUES MOTOR=x|y|e` (position, speed, id/iq current, MCU
  temperature), `MOTOR_GET_PROBE`, and `MOTOR_LOG_START/STOP` which
  samples a motor into a CSV beside the PA logs; `tools/flow/flow_test.py`
  logs the extruder servo through a ramp and `flow_analyse.py` prints its
  current and speed per step (on the K2 the servo kept 96-104% of the
  commanded speed and its current rose with pressure right up to 22
  mm^3/s: the gears hold, the hotend is the limit).
- **Fan rpm in Mainsail.** The part fan, hotend fan and (K2 Plus) PTC fan
  carry Klipper's own `tachometer_pin` on the K2's fan-feedback inputs, so
  Mainsail, the API and the touchscreen see each fan's rpm (part fan
  ~7500, hotend fan ~8000 at full on our K2). `[fan_feedback]` is gone;
  hark_safety and the chamber heater read the fans' rpm instead. The fans
  are powered through their PWM, so the rpm is only true at full speed;
  0 still means stopped.
- **Fan health and minimum speed** (`hark_fans`). `FAN_CHECK` reads each
  fan's full-speed rpm (the part fan with a 3 s blast) and keeps it in
  `hark_fans.json`; a reading 15% under the fan's usual, or no pulses, is
  reported with `!!` so the touchscreen shows it. `FAN_MIN_CAL` steps the
  part fan down until the tachometer reads 0 and saves the lowest turning
  duty plus 2% (8% on our K2) as its minimum; `M106` raises any lower
  request to it, so a fan told to run turns. `_FAN_AUTO` (on by default)
  checks the part fan at the start of every print and the hotend fan at
  the chute once hot. `FAN_HEALTH` shows the records.
- **Bed heater power on 110 V mains** (`hark_bed_power`, K2 only).
  Creality's config has a 220 V set (half power) and a 110 V set (full
  power, its own PID) and picks one by heating the bed 30 C at half
  power: at or under 0.35 C/s is 110 V. Ours only ever ran the 220 V set,
  a quarter of the power a 110 V printer's bed should have. The test now
  runs once before the first print's bed heat-up (about a minute from
  cold; 0.76 C/s on our 230 V K2) and the answer is kept in
  `hark_bed_power.json`; `voltage: 110|220` forces a set;
  `BED_POWER_CALIBRATE`, `BED_POWER_STATUS`, `BED_POWER_SET`.
- **Touchscreen: fan rpm and health in the Fans box** (the part fan's rpm
  or its usual, a Hotend fan line, warning colours after a slow or
  stopped reading), and a **new boot animation**: the Hark Tech badge
  lights up with its circuit traces, "K2 OpenKlipper" and harktech.co.uk
  (`harkscreen/bootanim`, built by the rewritten
  `tools/bootanim/make_bootanim.py`). The installer puts it in place and
  keeps Creality's set in `/mnt/UDISK/mainline-backups/boot-play-stock.tar`
  (uninstall.sh restores it); `OKL_BOOTANIM=keep` leaves the printer's
  own, `OKL_BOOTANIM=/folder` installs one made from your logo
  (docs/SCREEN-CUSTOMISE.md).
- **K2 Plus: no more beta gate.** The config has run on a real K2 Plus
  since 31 October (homing, Z tilt, probing, chamber heater, full prints,
  touchscreen), so `install.sh` no longer needs `OKL_K2PLUS_BETA=1` (the
  board-layout check stays); README, PROJECT-STATUS and the K2 Plus guide
  say so. A K2 Plus with a CFS is still untested.
- **K2 Plus: automatic belt tensioners** (`k2_belt_tension`). The
  motorised tensioners sit on the motor bus at 0x21/0x22 with its frame,
  so `BELT_TENSION_STATUS` reads each belt's newtons, slider position and
  calibration, and `BELT_TENSION CONFIRM=1` is Creality's routine (home,
  park at the back, motors off, tighten to 140 N, work the belt 200 mm
  each way, tighten again, save). `BELT_TENSION_MOVE` and
  `BELT_TENSION_CALIBRATE` are the service and factory commands. Not yet
  run on a real K2 Plus (docs/K2-PLUS-BETA.md step 10).
- **K2 Plus: saved Z tilt.** The whole correction of a `Z_TILT_ADJUST`
  run is kept in `hark_z_tilt.json` and `Z_TILT_APPLY_SAVED` puts it back
  without probing. Print start goes through `_Z_TILT_START`: `mode:
  "probe"` (default, as before) or `"saved"` (Creality's ADJUST_STEPPERS,
  about 30 s less per print).
- **Maintenance counters** (`hark_maintenance`): print hours, grams
  through the nozzle and cutter uses against Creality's thresholds
  (nozzle 20 kg, cutter 100k cuts, belt and PTFE 1000 h, shaper, lube,
  camera, fans, wipe strip, filter and desiccant 500 h). `MAINTENANCE_STATUS`,
  `MAINTENANCE_DONE ITEM=`; due items are listed when a print ends.
- **`FIRST_LAYER_PAUSE ENABLE=1`** pauses every print after layer 1 for a
  look (stock's FIRST_FLOOR_PAUSE), through the slicer's layer-change
  line. **`SILENT_MODE ENABLE=1`** is stock's quiet mode (accel 2500,
  150 mm/s, corner speed 5, speed 50%, `M204` held to 2500); `ENABLE=0`
  restores the limits. **A second extruder PID set above 280 C**
  (`pid_*_high_temp`, `high_temp_value`) as stock switches to for hot
  materials; the K2 extruder carries stock's values commented out.
- docs/PROJECT-STATUS.md lists what is still only in Creality's firmware
  after a full pass over 1.1.7.0, ranked.
- **Power-loss recovery** (new `klippy/extras/hark_plr.py`, `[hark_plr]`,
  after Jacob10383's power_loss_recovery and Prusa's power panic). While
  printing, right after a plain move line (G0-G3, never inside a macro
  such as a colour change) the file position and the print's state are
  noted; a note counts once the toolhead has made its moves, and the
  newest is written to `hark_plr.json` in the config folder every 10 s.
  Finished, cancelled or failed prints delete it; a shutdown (the MCUs
  lost power) keeps it. After a restart the console and the touchscreen
  ("Print interrupted": Discard / Resume) offer `PLR_RESUME CONFIRM=1`:
  Z from the bottom switches mapped into the print's nozzle-tap frame
  (k2_z_align now notes each Z motor's step count at the switches), home
  X/Y, heat the bed, raise it clear with the nozzle over the chute, heat,
  load and purge the slot (or prime the rear spool), restore PA, limits,
  mesh, excluded objects, G-code offsets and fans, go back over the part
  and down, then M23/M26/M24 from the saved byte. Up to 10 s is printed
  twice. K2 Plus: a Z tilt made after the switches is kept on average
  (warned over 0.05 mm). `PLR_STATUS`, `PLR_DISCARD`. Not yet tried on a
  real power cut. Tests: tests/test_hark_plr.py; the line watch was
  checked in real Klipper.
- **Silent load retries.** A CFS load that fails is rewound and tried
  again (`load_retries`, 2) before a colour change pauses, like Prusa's
  MMU3; replaces the single stage-0 retry.
- **Per-slot counts** (`hark_cfs_stats.json` next to the config, kept
  across restarts): loads, failed attempts, loads that needed a retry,
  paused changes, clogs, runouts and cut misses (counted only once the knife
  sensor has tripped on a cut since the restart, so a toolhead whose sensor
  never changes, like the DXC2, logs none). A warning when a slot
  fails 3 of its last 10 load attempts; `CFS_SLOT_STATS [RESET=<slot>|ALL]`;
  the touchscreen's slot menu shows the load record (in warning colour
  when the slot keeps failing).
- **Orca's own purge length for every colour change.** Orca's
  `{flush_length}` (the matrix times the flush multiplier, less what is
  flushed into infill, objects or support) was only used between two
  slots of the same material; a change of material looked the length up
  in the file's matrix, which ignores those savings and counted the flush
  multiplier twice. `_CFS_TC` now passes it as `FLUSH=` for a change of
  material too (at least the stock purge, at most 360 mm).
- **Cutter calibration with the X/Y motors**: `CFS_CUTTER_CALIBRATE
  METHOD=STALL CONFIRM=1` for toolheads whose knife sensor never trips
  (e.g. a Phaetus DXC2). Rams the cutter at the real cut speed 0.1 mm
  deeper each time from -5 to -11, reads both CoreXY motors' stall latches
  after each ram (homing X again after a hit, then clearing the stall flag
  homing leaves), and reports the first two hits in
  a row less `cutter_stall_offset` (0.4) as the `cutter_cut_x` to use, as
  stock motor_control and Jacob10383's CALIBRATE_CUT_POS do. Reported
  only. The first run on a K2 (11-02) read only the X motor and missed
  every hit: the stall latched on the Y motor (0x82). Now reads both; not
  yet re-run on a printer: stay with it.
- A filament sensor pin that stays in after an unload (a weak spring on the
  DXC2) is shaken free: 6 quick swings over the chute, then a second look,
  before pushing through the nozzle or stopping (`unload_shake_moves`).
- A CFS unload that fails after the cut (filament still at the nozzle
  sensor, hub blocked) wipes the nozzle at the chute before stopping: the
  cut lets the nozzle ooze, and it used to harden on the tip.
- Touchscreen: long words (file names) wrap instead of running off a box.

- **A failed colour change pauses the print instead of ending it.** Any
  error in CFS_TOOLCHANGE (cut, unload, load or purge) used to end the
  print (HT-P-2026-0416, 11-02: slot A did not feed at the 3-hour mark).
  Now, as stock does, the CFS is put idle, the print pauses at the chute
  with the nozzle hot and the fans back on, and the console and
  touchscreen say which slot failed and why. RESUME runs the new
  CFS_CHANGE_RETRY first: it reheats, works out what is left from the
  nozzle sensor (load and purge if the nozzle is clear; purge only if the
  new slot was loaded by hand; a full change if the old slot is still
  in), goes back over the print at the lifted height, comes straight down
  and saves that as the resume position. If the retry fails the print
  stays paused. The usual 30-minute paused timeout turns the hotend off;
  the retry reheats. `change_pending` / `last_change_error` in
  `printer.k2_cfs`. Tests: tests/test_cfs_change_pause.py.
- **Print start clears the old filament at its own temperature.** The
  filament left in the nozzle from the last print was cut and purged at
  the new print's temperature (PETG left in for a PLA print: cut and
  pushed out at 220C instead of 250C, a poor cut and a sticky, incomplete
  clear). CFS_PRINT_START and CFS_CHANGE_SLOT now heat to the old slot's
  record temperature when it is hotter, wait, cut, load and purge, then
  go back to the print's temperature, as a mid-print change and Creality
  and Bambu do. Tests: tests/test_cfs_old_filament_temp.py.
- **The load retries a slot that did not feed once by itself.** EXTRUDE
  stage 0's STAGE0_ODOMETER_TIMEOUT (0x09, the hub's measuring wheel did
  not turn for 2 s) now rewinds the hub and feeds again once before
  failing.
- **CFS loads up to ~10 s faster per colour change, and a slow feed no
  longer fails.** The load sends EXTRUDE stage 5 and waited up to 15 s for
  the box to answer. CFS firmware 1.5.0 does not answer stage 5 while it
  feeds: on 11-01 every change waited the full 15 s after the filament had
  already reached the nozzle sensor, and a feed taking longer than 15 s +
  3 s grace failed the load. Each send now waits `load_stage5_timeout`
  (1 s, as Jacob10383's driver does); no reply means still feeding, the
  nozzle sensor ends the wait (checked every 0.1 s) and `load_timeout`
  (90 s) still bounds it. Error replies (EXTRUDE_ERR8 retry etc.) are
  handled as before. Tests: tests/test_cfs_load_stage5.py.
- **PETG purge no longer curls back into the nozzle.** Creality flushes
  with the part fan at 30/255; the K2's part fan blows from one side, so a
  PETG strand cooled unevenly while it came out, curled up, stuck to the
  nozzle and blocked the chute (Grant's K2, 11-02). New `_PURGE_FAN` macro
  (`variable_material`, matched on the start of the slot's type like
  `_SOFT_FILAMENT`): PETG, PCTG, ABS, ASA and PC flush with the fan off,
  everything else keeps 30 (`variable_default`). Used by the colour-change
  purge (lowest of the old and new slot's materials) and START_PRINT's
  flush. The 5 s full blast that snaps the strand off is unchanged.
  Tests: tests/test_cfs_purge_fan.py.
- **K2 Plus config: three values back to stock** (from nur00's read-only
  review of the beta against his printer's own F008 config, 11-02): the
  extruder PID is Creality's K2 Plus set (14.296 / 1.271 / 40.206, its
  below-280C set; it was the K2's tuned values), the bed heater check is
  120 s (stock K2 Plus; the K2 uses 180) and Z_TILT_ADJUST travels at
  300 mm/s (was 200).
- OPEN-STACK.md: upgrade-server is disabled too (since 10-24), and the
  camera is Hark Cam MJPEG on 8080, not WebRTC on 8000.

## v0.4.17-alpha.1 (2026-11-01)

- **Touchscreen froze on the time zone picker** (K2 Plus report). Choosing a
  zone ran `/etc/init.d/system reload` (which also resets the clock chip
  with `hwclock`) on the screen's own loop, waiting up to 10 s per step.
  It now does what Creality's `/usr/bin/timezone.sh` does (uci zonename +
  commit, `/tmp/localtime` link, no service reload) in the background, and
  logs the zone and the time taken to /tmp/harkscreen.log.
- **A button that fails no longer stops the screen.** An error in a tap is
  logged and the screen carries on (rendering errors already were).
- The Status page redraws when the chamber heater's target changes.

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
