# K2 OpenKlipper v0.4.21-alpha.1

**A proper nozzle scrub, a full purge when printing by object, choosing the
slot for each filament from the touchscreen, and a fix aimed at the
`Stepper too far in past` shutdown at the purge line.**

This release turns things on rather than hiding them behind options, so
please report what breaks. A klippy.log and what you saw are the most useful
things to send.

## Nozzle scrub

Traced from Creality's own `NOZZLE_CLEAR` code: tap for height, scrub hot,
fans on, scrub again further along the strip, then stay down on the strip
until the nozzle reaches 140 C and lift.

| | Hot strokes | Cool strokes and park |
| --- | --- | --- |
| K2 | X160-170 | X175-180 |
| K2 Plus | X185-195 | X200-205 |

- Print start now scrubs from 180 C (it was 140 C). The tap and scrub take
  about 2 minutes 20 seconds.
- It presses 0.32 mm below the height tap (it was 0.1). On one K2, 0.1 left
  the tip dirty and 0.32 got it clean. Creality presses 0.6.
- **The K2 Plus now scrubs by default, and this has not been run on a K2
  Plus.** Check the positions in step 7 of
  [K2-PLUS-BETA.md](docs/K2-PLUS-BETA.md) before your first print.
  `variable_scrub_strip: False` under `[gcode_macro START_PRINT]` in
  `printer-overrides.cfg` turns it off.

## Colour changes

- **Printing by object gets the full purge.** Between two slots of the same
  material the purge could be as short as 30 mm, and by object has no prime
  tower or infill to take the rest, so the next object's first layers
  blended. These files now purge at least the stock 160 mm.
- **`T0`-`T3` wait for queued moves before touching the CFS**
  ([#5](https://github.com/grant0013/K2-OpenKlipper/issues/5), thanks
  Promotheux). Your Orca profile picks up the same `M400` when you re-import
  it, but files sliced with an older profile are covered too. **Not confirmed
  yet on a K2 running Klipper on its own board.** If you had this crash,
  please try a few prints without your own `M400` and report back.
- **Choose the slot each filament prints from**
  ([#3](https://github.com/grant0013/K2-OpenKlipper/issues/3), thanks
  masteryodha): on the touchscreen's file details, or `CFS_TOOL_MAP` from the
  console. You can also pick the rear spool with a CFS fitted, which is the
  least tested part of this release.
- Low colour changes no longer pause on the bed mesh offset.
- `RESUME` loads and purges a new spool after a runout, and reheats after a
  long pause.

## Bed mesh and homing

- The print-start mesh waits for the toolhead around `BED_MESH_CHECK`, from
  mylife4aiurr's test in
  [#4](https://github.com/grant0013/K2-OpenKlipper/issues/4). One run so far,
  so `mesh_pps: 1, 1` is still the advice above about 13x13.
- A failed mesh check no longer aborts the print while cleaning the nozzle.
- K2 Plus: set the measured `position_endstop` whenever it isn't 349.5. The
  guide used to say only above 1 mm, and 0.6 mm was enough to fail the nozzle
  tap on one printer.

## Also in this release

- The stepper threads use both of the K2's cores. 12% faster in a batch test
  on the K2's own board, not yet measured on a real print.
- The chamber camera recovers from a USB drop instead of serving its last
  frame.
- Camera WebRTC mode: viewer limit, always 720p, safer start.
- Other filament brands sync to their own Orca preset.
- go2rtc's web UI and config editor are off the network.
- The touchscreen stays on for the sleep time after a print.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Updating

Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1`. Your settings are kept.

Alpha software on your own printer: stay near it for the first prints.
