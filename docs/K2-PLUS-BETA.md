# K2 Plus beta (F008)

K2 OpenKlipper was developed on a K2 Combo (F021). This branch adds a K2 Plus
config. **It has not run on a real K2 Plus yet.** Everything below is built
from Creality's own K2 Plus 1.1.7.0 configuration and from Jacob10383's K2
Plus firmware, and checked offline (Klipper loads the config and builds the
board commands), but the first real run is yours. Go step by step, stay next
to the printer, and keep the power switch in reach.

Uninstalling puts the printer back to stock at any point:

    sh /mnt/UDISK/k2-mainline-20260729-v62/uninstall.sh

## What is different from the K2

| | K2 | K2 Plus beta |
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

## Known gaps in the beta

- **Chamber heater stays off.** The PTC heater is not configured; `M141` and
  `M191` run the chamber fan only, like on the K2. ABS/ASA print, but without
  active chamber heat.
- **Bottom switch height is a placeholder** (`position_endstop: 330`). Step 6
  measures the real value. Until then the bottom-switch moves stop the bed
  short and the nozzle tap fails safely instead of touching.
- **Rear cleaning strip scrub is off** (`scrub_strip: False`) until step 7
  confirms where the strip is.
- Not ported yet: belt tension tool, chamber heating, Creality's per-motor gain
  change (stock runs one X/Y motor at a lower position gain while printing; the
  beta leaves the motor firmware default, which is what the K2 uses).
- Untested on the Plus: Hark Screen, the camera, a CFS, the flow and retraction
  test prints (they print centred at X130 Y130, which is still on the bed).

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

    OKL_REF=k2plus-beta OKL_K2PLUS_BETA=1 sh /tmp/install.sh

It installs, starts Klipper and waits for the motors. Then open Mainsail at
`http://PRINTER-IP:4409/`. Send the installer output whether it worked or not.

In the Mainsail console run, and send the replies:

    MOTOR_FAULTS
    MOTOR_E_ADDR
    KZ_ALIGN_STATUS

`MOTOR_FAULTS` should list X, Y, Z, Z1 and E with no errors.
`KZ_ALIGN_STATUS` should show PA15 and PA8 as open with the bed up.

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

Now measure the bottom switches:

    KZ_ALIGN_MEASURE CONFIRM=1

The bed goes down slowly until each switch triggers and the console prints
something like `Suggested [k2_z_align] position_endstop: 351.20`. Put that
value in **printer-overrides.cfg** (Mainsail > Machine):

    [k2_z_align]
    position_endstop: 351.20

Save and restart, then check the switch reference:

    KZ_ALIGN_BOTTOM CONFIRM=1
    G28

Send the output of both.

## Step 7: the rear cleaning strip

Look at the back edge of the bed for the cleaning strip (stock probes it at
X150 Y355). Send a photo. Once its position is confirmed the scrub goes on
with, in printer-overrides.cfg:

    [gcode_macro START_PRINT]
    variable_scrub_strip: True

## Step 8: mesh, then a first print

    BED_MESH_CALIBRATE

Send the mesh (Mainsail > Height map). Then print something small in PLA from
the rear spool (single colour, slicer set up as in docs/INSTALL.md). Watch the
first layer. It starts with the K2's first-layer correction (`hot_z_comp`
0.04 mm closer when hot; Creality's K2 Plus config does not state its own),
and we tune it from there: lines sitting apart or not sticking means raise it
(positive is closer), ridges or a scraped look means lower it.

## What to send back after each step

- the console output of the step
- `klippy.log` (Mainsail > Machine > logs), after any problem
- photos or a short video where something moved

Thank you for testing.
