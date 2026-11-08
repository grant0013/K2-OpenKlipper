# K2 Plus (F008): first run, step by step

K2 OpenKlipper was developed on a K2 Combo (F021). The K2 Plus config is
built from Creality's own K2 Plus 1.1.7.0 configuration and from Jacob10383's
K2 Plus firmware, and since 31 October 2026 it has run on a real K2 Plus
(board CR0CN240319C13, no CFS): homing, Z tilt, probing, the chamber heater
and full prints, with the fixes from that testing in every release since
v0.4.4. This guide is still the way to do a first run: it checks each part
of the printer on its own before anything prints, so a difference between
your machine and the tested one shows up early. Go step by step, stay next
to the printer, and keep the power switch in reach.

Uninstalling puts the printer back to stock at any point:

    sh /mnt/UDISK/k2-mainline-20260729-v62/uninstall.sh

## What is different from the K2

| | K2 | K2 Plus |
|---|---|---|
| Travel | X 262, Y 296.5, Z 270 | X 352.5, Y -6.2 to 400, Z 360 |
| Z motors | one, geared 64:20, TMC2208 | two closed-loop motors (Z left, Z1 right) on the motor bus, direct 8 mm lead screws |
| Motor bus | X 0x81, Y 0x82, extruder 0x81 | adds Z 0x83, Z1 0x84; extruder tried at 0x85 then 0x81 |
| Bed levelling | one bottom switch (PA15) | two bottom switches (PA15, PA8) + `Z_TILT_ADJUST` on two taps across X |
| Bed heater | custom thermistor, half power | EPCOS 100K, full power, stock PID, max 120 C |
| Fans | hotend PB7, aux PB4, exhaust PB1+PB3 | hotend PB7+PB1, aux PB4+PB3, chamber PA0 |
| Mesh | 7x7 over 255 mm | 9x9 over 345 mm |
| Chute, brush, cutter | K2 positions | stock K2 Plus `box.cfg` positions |

The config is generated from the K2 one by `tools/k2plus/make_k2plus_cfg.py`
(every change is listed there with its source).

## Known gaps

- **Bottom switch height** is `position_endstop: 349.5`: the tested K2 Plus
  measured 349.85 / 350.10 and Creality's config puts the bottom at 350, less
  a little (too low is safe: the bed stops short and the nozzle tap fails
  instead of touching). Step 6 checks it on your printer.
- **Rear cleaning strip scrub is off** (`scrub_strip: False`): the strip
  positions were confirmed on the tested printer, but the scrub itself has
  not been run on a K2 Plus. Step 7 is where to try it.
- Not ported yet: belt tension tool, Creality's per-motor gain change (stock
  runs one X/Y motor at a lower position gain while printing; this config
  leaves the motor firmware default, which is what the K2 uses).
- Untested on the Plus: a CFS (the tested printer has none), the camera, the
  flow and retraction test prints (they print centred at X130 Y130, which is
  still on the bed).

Supported layout: the installer checks `get_sn_mac.sh board` and
`structure_version` and only installs on boards CR0CN240110C10, CR0CN240319C13
or CR0CN240319C14 with structure_version 0 (Creality's config folder
`F008_CR0CN240319C13`). Anything else stops with a message: send the output.

## Before you start

1. Creality firmware 1.1.7.0 (Settings > About). Update first if older.
2. Root on (Settings > Root account information), SSH in as root.
3. Empty bed, nothing in the chute area, filament unloaded.

## Step 1: check (changes nothing)

    python3 -c "import urllib.request as u; open('/tmp/install.sh','wb').write(u.urlopen('https://raw.githubusercontent.com/grant0013/K2-OpenKlipper/k2plus-beta/install.sh').read())" && OKL_REF=k2plus-beta sh /tmp/install.sh --check

Send the output.

## Step 2: install

    OKL_REF=k2plus-beta sh /tmp/install.sh

It installs, starts Klipper and waits for the motors. Then open Mainsail at
`http://PRINTER-IP:4409/`. Send the installer output whether it worked or not.

In the Mainsail console run, and send the replies:

    MOTOR_FAULTS
    MOTOR_E_ADDR
    KZ_ALIGN_STATUS

`MOTOR_FAULTS` should list X, Y, Z, Z1 and E with no errors.
`KZ_ALIGN_STATUS` should show PA15 and PA8 as TRIGGERED with the bed all the
way down, and open once the bed is raised.

## Step 3: heaters and fans (no motion)

    M140 S50
    M104 S150

Both temperatures should rise steadily in Mainsail. Then `M104 S0` and
`M140 S0`. Fans: `M106 S128` (part fan), `M107`; the hotend fans start by
themselves above 50 C. Send a screenshot of the temperature graph.

## Step 4: home X and Y

Hand on the power switch. Home one axis at a time:

    G28 Y
    G28 X

Y should go to the front, X to the right, each stopping on the stall detect
without a bang. If anything runs the wrong way or keeps pushing, switch off.

## Step 5: position check (X and Y only, slow)

    CHUTE_POSITION_CHECK

The nozzle visits each chute, brush and cutter position at 20 mm/s and waits
3 s at each; the console names every stop. Nothing extrudes, wipes or cuts.
Tell me any stop that is off (for example the nozzle not over the chute, or
not centred on the brush) or close to hitting something. A photo or short
video helps a lot.

## Step 6: Z, both motors, and the bottom switches

Empty bed. Then:

    G28
    Z_TILT_ADJUST
    G28 Z

`G28` lowers the bed 5 mm (both Z motors), homes X and Y, then taps the
nozzle on the bed centre. `Z_TILT_ADJUST` taps at the left and right and
levels the two Z motors; send its output.

Now check the bottom switches:

    KZ_ALIGN_MEASURE CONFIRM=1

The bed goes down slowly until each switch triggers and the console prints
where, with a suggested `position_endstop`. Send the output. If the
suggestion is not 349.5, put it in **printer-overrides.cfg** (Mainsail >
Machine), save and restart:

    [k2_z_align]
    position_endstop: 351.20

A small difference can matter. On one K2 Plus, 350.10 instead of 349.5 was
enough for the nozzle tap to fail with `No trigger on probe after full
movement`, and setting the measured value fixed it.

Then check the full homing sequence that every print starts with:

    KZ_ALIGN_BOTTOM CONFIRM=1
    G28
    Z_TILT_ADJUST

Send the output of all three.

## Step 7: the rear cleaning strip

Stock measures the strip at X150 Y355 and scrubs the nozzle on it at X185
Y355.5 (Creality's built-in default). After `G28`, look at both from close
to the bed (at Z10 the nozzle seems further back than it is):

    G90
    G1 Z2 F600
    G1 X150 Y355 F3000
    G1 X185 Y355.5 F3000
    G1 Z10 F600

Send a photo at each. Once the nozzle is over the strip at both, the scrub
goes on with, in printer-overrides.cfg:

    [gcode_macro START_PRINT]
    variable_scrub_strip: True

## Step 8: mesh, then a first print

    BED_MESH_CALIBRATE

Send the mesh (Mainsail > Height map). Then print something small in PLA from
the rear spool (single colour; in OrcaSlicer import
`orca/Creality K2 Plus OpenKlipper.json` as in docs/INSTALL.md step 6 and
set your printer's IP). Filament sync works as described there
("Filament sync with OrcaSlicer"); without a CFS the rear spool is slot 1. Watch the
first layer. It uses Creality's own K2 Plus first-layer correction (0.050 mm
closer for PLA at 220 C, a little more for hotter filaments; the console
prints it as "First-layer Z comp"). Only if the first layer is still off,
fine-tune it like Creality's Z offset in printer-overrides.cfg:
`[gcode_macro START_PRINT]` `variable_z_offset: -0.02` (negative = nozzle
closer to the bed: lines sitting apart or not sticking; positive = further
away: ridges or a scraped look).

## Step 9: the chamber heater

The PTC chamber heater follows stock's rules: it only heats for a chamber
target over 40C (M141/M191 from the slicer, Mainsail's chamber box,
`CHAMBER_HEAT TARGET=`, or the touchscreen's Chamber gauge; PLA's 35C never
heats), never above 60C, never while the bed is still heating up, at half
power for the first minute. It switches off with a `!! Chamber heater off`
message if its fan stops turning for 15 s or the chamber runs 10C over the
target. It is on since v0.4.14, after the supervised test on a real K2 Plus;
to turn it off, in printer-overrides.cfg:

    [hark_chamber]
    enable: False

To check yours: door shut, nothing printing, then

    CHAMBER_HEAT TARGET=45
    CHAMBER_STATUS

The heater fan starts within a few seconds; `fan tach` must be above 0 while
it heats and the chamber should climb steadily. `CHAMBER_HEAT TARGET=0` to
stop. If anything smells hot, the fan is silent or the console shows
`!! Chamber heater off`, stop and send the output.

## Step 10: belt tension (automatic tensioners)

The K2 Plus has a motorised tensioner with a strain gauge on each belt.
Creality's firmware tightens both to 140 N from the screen; ours does the
same over the motor bus. First just read them:

    BELT_TENSION_STATUS

Each belt answers with its tension in newtons, "ok" when it is within 15%
of 140 N, its slider position and the module's firmware. "did not answer"
means the module is not on the bus at 0x21/0x22 (send the output); "not
calibrated" means the module's own two-point gauge calibration is missing
(Creality sets it at the factory; `BELT_TENSION_CALIBRATE` redoes it with a
belt tension gauge).

If a belt is out of range:

    BELT_TENSION CONFIRM=1

homes X and Y, parks at the back, frees the motors, tightens each belt to
140 N (it only ever tightens, as stock does), runs the head 200 mm forward
and back to settle the belt, tightens again, and reports. Home afterwards.
`AXES=X` or `AXES=Y` does one belt; `TARGET=` changes the newtons. This has
not run on a real K2 Plus yet: stay by the printer and send the output.

## Between prints

- **Cancel** works like stock: heaters off, the nozzle lifts and parks, and
  the motors stay on. Only the safety watch (after a motor fault) stops
  without moving and switches the motors off.
- **Warm start:** after a print that finished or was cancelled normally,
  the next print skips the trip to the bottom switches and the X/Y homing
  (the Z tap and Z tilt still run). A restart, motors off or the idle
  timeout homes in full.
- **Idle settings**, in printer-overrides.cfg (seconds):

      [gcode_macro _LIGHT_IDLE]
      variable_off_after: 600

      [idle_timeout]
      timeout: 3600

  The first switches the chamber light off 10 minutes after a print (0 =
  stays on). The second is how long the printer sits idle before heaters
  and motors go off, which lets the bed drop (default 1800 = 30 minutes).
- **Network:** Mainsail, Mobileraker and other Klipper apps find the
  printer on the network by name. The printer's IP is on the touchscreen's
  Wi-Fi page.

## Updates

After the first install, updates come from the project's GitHub releases
like any Klipper update: Mainsail offers a new version in a dialog with an
Update button (also on the touchscreen's Machine page, or `OKL_UPDATE
CONFIRM=1` in the console). Your printer-overrides.cfg is kept.

## What to send back after each step

- the console output of the step
- `klippy.log` (Mainsail > Machine > logs), after any problem
- photos or a short video where something moved

Thank you for testing.
