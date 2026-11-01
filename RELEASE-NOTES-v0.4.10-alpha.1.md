# K2 OpenKlipper v0.4.10-alpha.1

Alpha firmware for the Creality K2 / K2 Combo (F021), with the K2 Plus (F008)
beta. Supervise prints and keep the rollback path. Update with the Update
button in Mainsail or on the touchscreen (your `printer-overrides.cfg` is
kept), or install with the one-line installer in
[docs/INSTALL.md](docs/INSTALL.md).

## Fix: colours bleeding on multicolour prints

On a colour change between two filaments of the same material, the printer
purges over the chute by the amount OrcaSlicer's flush volumes ask for. That
length reaches the printer through a line in the printer profile's
change-filament G-code. Presets without that line passed nothing, and every
PLA-to-PLA change purged a fixed 70 mm, even where the file's own flush table
asked for over 300 mm (black to white). Colours bled.

The printer now reads the flush table from the print file itself, so the
purge follows OrcaSlicer whatever the preset. Colour-change purges can be up
to 360 mm (was 240). A change between different materials still purges at
least the full stock length. To turn it off:

```
[k2_cfs]
toolchange_purge_from_file: False
```

If a colour pair still bleeds, raise OrcaSlicer's flush volumes for it
(Flushing volumes, next to the filament list).

Everything from v0.4.9-alpha.1 (print preview retry) is included.

Full list in [CHANGELOG.md](CHANGELOG.md).
