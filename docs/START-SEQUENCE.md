# Print start sequence (`START_PRINT`)

```
START_PRINT EXTRUDER_TEMP=<t> BED_TEMP=<t> TOOL=<0-3>
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
4. **CFS load and purge at print temperature over the chute** (with `TOOL`).
   The bed heats and soaks meanwhile, so no separate soak is needed.
5. **`_COOL_WIPE`.** The fan comes on, the nozzle wipes on the silicone while
   cooling to 180 °C, wipes again at 140 °C, and parks.
6. **Precise Z home**, with the bed rising to 5 mm from the bottom reference
   and a strain-gauge tap at the bed centre. On the first print with the
   bottom reference, the tap landed 0.064 mm from the reference's
   prediction.
7. **`NOZZLE_CLEAR`.** Probe the bed's rear strip and scrape the nozzle on it
   at 140 °C, then go straight into the **adaptive mesh**.
8. Reheat over the chute, prime, wipe, then `_HARK_LINE_PURGE`: a 30 mm
   "swamp" plus a 20 mm drag line at the bed front.

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
