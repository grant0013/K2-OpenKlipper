# K2 OpenKlipper v0.4.4-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Cancel and print end work like stock

Cancel now lifts and parks the nozzle with the heaters off, instead of
stopping dead with the motors off. At the end of a print the motors stay on
and the bed goes half way down under control (on the K2 Plus it used to drop
under its own weight). The safety watch still stops without moving after a
motor fault.

## No re-homing between prints

After a print that finished or was cancelled normally, the next print skips
the trip to the bottom switch and the X/Y homing. The nozzle tap still
measures Z every time. A restart, motors off, the idle timeout or a safety
stop homes in full. To always home in full:

```
[gcode_macro START_PRINT]
variable_warm_start: False
```

## Idle settings

```
[gcode_macro _LIGHT_IDLE]
variable_off_after: 600   # chamber light off 10 min after a print (0 = stays on)

[idle_timeout]
timeout: 3600             # heaters and motors off after 1 h idle (default 30 min)
```

## Nozzle tap and first layer

- The hotend fans slow to 60% while the nozzle strain gauge is armed, like
  stock: their vibration could fire a tap with nothing touching.
- A bed mesh that looks like debris on the nozzle triggers a nozzle clean and
  a second measurement before the print stops.
- K2 Plus: Creality's own hot-nozzle first-layer correction.
  `variable_z_offset` fine-tunes it like Creality's Z offset (negative =
  closer).

## K2 Plus beta

First prints on a real K2 Plus are in: fan pins, bottom-switch homing, scrub
position and the first layer are from stock values. A K2 Plus already on
the beta config updates with the Update button like a K2. New installs still
follow [docs/K2-PLUS-BETA.md](docs/K2-PLUS-BETA.md).

## Also

- Moonraker announces the printer on the network (mDNS), so Mainsail,
  Mobileraker and other Klipper apps find it.
- PTC chamber heater supervisor on the K2 Plus (`[hark_chamber]`, off until
  enabled).

Full list in [CHANGELOG.md](CHANGELOG.md).
