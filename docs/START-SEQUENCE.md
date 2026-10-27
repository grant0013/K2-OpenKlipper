# Print start sequence (`START_PRINT`)

```
START_PRINT EXTRUDER_TEMP=<t> BED_TEMP=<t> TOOL=<0-3> [TOWER_X=<x> TOWER_Y=<y> TOWER_W=<w>]
```

The goal is one heat cycle before probing, a clean nozzle for the Z
measurement and no ooze on the measured bed.

1. **Bed heat starts** immediately; the nozzle holds at 140 °C.
2. **`KZ_ALIGN_BOTTOM`.** The bed drives down to the PA15 optical switch
   (Z = 263.13 on F021) and backs off 10 mm. This gives an absolute Z
   reference on every print, whatever happened to Z before. It takes about
   50 s.
3. **`G28 X Y`** only. No Z contact yet, so a crusted nozzle cannot fake a
   probe trigger.
4. **CFS load and purge at print temperature over the chute** (with `TOOL`):
   two 90 mm flush blocks with Creality's cleaning (see below). The bed heats
   and soaks meanwhile, so no separate soak is needed.
5. **`_COOL_WIPE`.** The fan comes on, the nozzle wipes on the silicone while
   cooling to 180 °C, wipes again at 140 °C, and parks.
6. **Precise Z home**, with the bed rising to 5 mm from the bottom reference
   and a strain-gauge tap at the bed centre. On the first print with the
   bottom reference, the tap landed 0.064 mm from the reference's
   prediction.
7. **`NOZZLE_CLEAR`.** Probe the bed's rear strip and scrape the nozzle on it
   at 140 °C, then go straight into the **adaptive mesh** (covering the
   prime tower too, see below) and `BED_MESH_CHECK`.
8. Reheat over the chute and run the third 90 mm flush block
   (`_CREALITY_FLUSH`), then Creality's own prime line along the front edge
   (`_CREALITY_PRIME_LINE`, Y-1, X105-160).
9. **First-layer Z offset** `0 - hot_z_comp` (0.04 mm lower), applied
   after the prime line.

### Opt-in variables (default off, test before switching on)

Set them in `printer-overrides.cfg` under `[gcode_macro START_PRINT]`.

- `variable_scrub_until_clean: 1` runs step 7 as
  `NOZZLE_CLEAR UNTIL_CLEAN=1`: scrub, probe the strip again at X85, and
  repeat (up to 3 passes) until the strip contact drops by less than
  0.01 mm. Debris under the tip reads as a higher contact, so a contact that
  stops dropping means the tip is clean. Then it taps Z again with the clean
  nozzle before the mesh. Adapted from the K2 Plus firmware's
  `PRTOUCH_SCRUB`, without its flexible-tab force detection.
- `variable_scrub_method: 'detect'` (default `'fixed'`, today's sequence)
  moves the scrub BEFORE the Z tap, like the K2 Plus: after the cool-wipe
  (and the cold-start soak) the head goes to the bed centre, then
  `NOZZLE_CLEAR METHOD=detect HOT=140 COOL=140` runs the full
  `PRTOUCH_SCRUB` (firm-press tab detection, slope-following scrub on
  X155-165, repeated until the strip contact stops dropping; see
  [PRTOUCH.md](PRTOUCH.md)). Step 6 then taps Z with the scrubbed nozzle and
  step 7 goes straight to the mesh, so no second Z tap is needed.
  `scrub_until_clean` is ignored in this mode. The scrub taps use the Z from
  `KZ_ALIGN_BOTTOM` (within about 0.06 mm of the tap); depths are relative
  to the contacts measured on the strip. If the scrub stops (a missed tap or
  a strip that does not look like one line), it says so and the print
  carries on to the Z tap.
- `variable_hot_z_rate` (mm/°C, 0 = off) with `variable_hot_z_ref_temp`
  (default 220): the offset becomes
  `hot_z_comp - hot_z_rate * (EXTRUDER_TEMP - hot_z_ref_temp)`. The K2 Plus
  firmware ships 0.00083 mm/°C and models the nozzle growing toward the bed
  as it heats, so hotter prints get the nozzle slightly higher (30 °C hotter
  = 0.025 mm). Our tap is always at 140 °C, so only the print temperature
  varies. Set `hot_z_ref_temp` to the print temperature `hot_z_comp` was
  tuned at. A total above 0.3 mm stops the print.

Without `TOOL` (Creality Print / stock Orca files), the CFS load is deferred
to the first `T` command and an adaptive heat soak runs instead:

| Bed temperature gap at job start | Soak |
| --- | --- |
| 3 °C or less | skipped (bed already settled) |
| 3-15 °C | 30-120 s, scaled |
| more than 15 °C | 120 s |

A cool bed matters: the precise Z read 0.042 mm lower after a 2-minute soak
than before it.

## Lessons behind it

- **Temperature swings.** The first open-stack sequence swung the nozzle
  140 → 230 → 140 → 250 → 140 → 170 → 140 → 250: four heat-ups, each one
  oozing. The current sequence goes 140 → 250 → 140 → 250 and saves about
  5 minutes.
- **G28 parameters.** `G28 Z NOWIPE=1` does not work. G28 is a classic
  G-code, so Klipper parses `NOWIPE=1` as `N` = `OWIPE=1`. Flags for
  `homing_override` are passed through a macro variable
  (`START_PRINT.skip_home_wipe`) instead.
- **Stale Z.** A tap from a stale Z (left over from a manual home with
  idling in between) found no contact three times. The bottom reference
  removes that failure mode, and if a tap ever fails now, the cause is
  sensing, not the reference.

## Nozzle cleaning: Creality's own routine

The first open-stack cleaning wiped straight after purging. The strand stayed
soft, got dragged out and ended up on the bed (once it knocked the prime tower
off). The routine now copies the K2 profile in Creality Print step for step,
for every purge (print start and colour changes):

- Purge in blocks of at most 90 mm. Each block is five fast pushes, each
  followed by a 1 mm/s pause, so the plastic comes out in pieces.
- Part fan at about 12% while purging, then **full fan for 5 s** so the strand
  sets and snaps off, then retract 2 mm.
- A full wipe after every block: swing in the chute, a circle on each half
  of the silicone brush, three passes back over the chute.

Print start uses 3 x 90 mm, like Creality: two blocks at the load, one at
print temperature after the mesh.

## A mesh that covers the prime tower

`BED_MESH_CALIBRATE ADAPTIVE=1` only probes under the objects. A prime tower
outside that area printed on a stretched mesh edge and came loose. On F021 the
steepest part of the bed turned out to be exactly where the tower was.

When the slicer passes the tower (`TOWER_X`/`TOWER_Y` = front-left corner,
`TOWER_W` = width, 0 = no tower), `START_PRINT` probes the area covering the
objects and the tower, plus `adaptive_margin`, at the full-bed point density.
See `config/ORCA-K2-PROFILE.cfg` for the slicer side. `BED_MESH_CHECK` then
stops the print before the first layer if the mesh deviation or the slope
between points is implausible (limits are variables at the top of
`START_PRINT`).
