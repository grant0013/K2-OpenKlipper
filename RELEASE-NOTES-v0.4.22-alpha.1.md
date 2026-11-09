# K2 OpenKlipper v0.4.22-alpha.1

**A warped bed now gets a mesh instead of an error, and a change aimed at the
`Stepper too far in past` shutdown when a dense mesh finishes.**

Please keep the reports coming. A klippy.log and what you saw are the most
useful things to send.

## Bed mesh

- **The mesh guard no longer stops a mesh on a warped bed**
  ([#3](https://github.com/grant0013/K2-OpenKlipper/issues/3), thanks
  masteryodha). The guard compares each point to its neighbours, so real
  curve looks abnormal, above all at the edges. With more than
  `mesh_guard_max_points` of those it gave up and left no mesh at all. It now
  warns, re-checks the worst points and finishes. `BED_MESH_CHECK` still
  judges the result at print start, so a bed that is too far out still stops
  there (`variable_mesh_max_deviation` and `variable_mesh_max_slope` under
  `[gcode_macro START_PRINT]`).
- **The last probe lift finishes before the mesh is built**
  ([#4](https://github.com/grant0013/K2-OpenKlipper/issues/4), thanks
  mylife4aiurr). Building a dense mesh holds up Klipper for up to a second on
  the K2's board, and the `M400`s in v0.4.21-alpha.1 only ran after the
  build. **Not confirmed yet:** the shutdown has only been seen with Klipper
  on the printer's own board and a 19x19 or 21x21 mesh. If you can run one,
  please try `mesh_pps: 2, 2` and report back.

## Probing

- A probe that runs out of travel now says what to check: the bed stopping
  short (`KZ_ALIGN_MEASURE CONFIRM=1`, then `position_endstop`) or a dirty
  nozzle.

## Docs

- `docs/CFS-PROTOCOL.md` has Lamar1007's stock K1 filament change sequence,
  the measuring wheel and box state decodes, and Creality's address scheme.

Tested on a K2 running Klipper on an external host: a 5x5 mesh with the new
wait, the guard forced over its limit, and a forced probe miss. Not run on a
K2 Plus.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Updating

Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1`. Your settings are kept.

Alpha software on your own printer: stay near it for the first prints.
