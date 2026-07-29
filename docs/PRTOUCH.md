# PRTouch findings

## Active v62 path

The validated v62 Z-home and mesh operations use the K2's hardware contact
path:

```text
pressure sensors -> pressure MCU -> PA15 -> main MCU PC7 -> Klipper trsync
```

`prtouch_mainline.py` creates the PC7 MCU endstop and calls Klipper's native
`homing.probing_move()`. `prtouch_open/endstop.py` starts the pressure and step
capture session, then delegates motion stop timing to that MCU endstop.

The pressure MCU, not a Python polling loop, decides when to assert the edge
that stops Z.

## Captured data

Pressure and step samples are still captured around probe moves. They are
useful for contact diagnostics, repeatability measurement, comparing clean and
contaminated nozzle behaviour, and developing a future portable classifier.

One clean single-tap observation on the development K2 was:

```text
PRTouch tap 1/1: contact Z=0.114063
PRTouch Z home: median 0.114063 spread 0.000000, retracted Z 4.885937
```

That is one observation, not a universal offset or quality threshold.

## Experimental host classifier

The repository retains a host amplitude/hysteresis probe implementation. It
uses a conservative entry threshold, a lower release threshold, and
multi-window persistence to reject isolated spikes.

It is not the active v62 motion-stop path. Changing host classifier settings
will not necessarily change the PC7 hardware trip force. The configuration
still needs cleanup so active hardware settings and experimental fallback
settings are clearly separated.

## Current operating lesson

Softened filament on the nozzle can preload bed contact and produce misleading
results. The validated print-start sequence therefore uses:

1. rough home
2. thermal settle
3. move to the rear silicone cleaner
4. nozzle wipe
5. precision Z home
6. adaptive mesh
7. immediate move to the purge area

The heated bed-drag cleaning routine used by Creality is not reproduced with
blind G-code. Its force-controlled behaviour remains a reverse-engineering
item.

## Help wanted

- identify how the pressure MCU derives and asserts PA15
- collect repeatability data across multiple K2 units
- compare clean nozzle, oozed nozzle, temperature, bed surface, and tap count
- map which configuration values influence the hardware edge
- provide sanitised pressure and step traces with exact test conditions
- determine whether a portable host classifier can safely replace the
  pressure-MCU decision

Do not test thresholds with an unattended printer. Keep travel short, speed
low, and an immediate power cutoff available.
