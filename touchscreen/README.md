# Native touchscreen

A native LVGL interface for K2 OpenKlipper, with touch controls for printing,
filament, calibration and everyday printer settings. Printer identity, temperature
limits, travel limits and available features come from the printer configuration.

## Use the precompiled bundle

The full precompiled bundle includes the touchscreen executable, assets, Python
service and matching OpenKlipper source. You do not need a compiler, Node.js or
font tools on the printer. Use a bundle built from the version you want to test;
the installer checks its files and preserves your printer-specific overrides.

Get `k2-openklipper-native-armv7.tar.gz` and its `.sha256` file from the maintainer
of that build. On your PC:

```sh
sha256sum -c k2-openklipper-native-armv7.tar.gz.sha256
scp -O k2-openklipper-native-armv7.tar.gz root@PRINTER-IP:/mnt/UDISK/
```

Connect with `ssh root@PRINTER-IP`, then install while the printer is idle:

```sh
cd /mnt/UDISK
tar -xzf k2-openklipper-native-armv7.tar.gz
cd K2-OpenKlipper-native
sh install.sh --check
sh install.sh
```

The installer starts the full stack and retains a rollback copy. The original
Creality firmware remains available. This branch supplies the native touchscreen;
there is no automatic download of its compiled package. For general printer
setup and supported hardware, see the [installation guide](../docs/INSTALL.md).

## Printing at a glance

View the print preview, progress, remaining time and layers. Temperature tiles
open nozzle, bed and chamber controls. Light, fans, pause, stop, object exclusion
and status history are available without leaving the main workflow.

![Printing home screen](screenshots/home.png)

## Printer controls

Move axes, enter positions, heat the nozzle and extrude or retract filament.
Unknown positions disable movement until homing. Lock/unlock controls distinguish
motor holding state from homing. Calibration actions follow the printer's
capabilities. Routine operations show a compact activity indicator while
navigation stays available.

![Printer movement controls](screenshots/move.png)

## Filament and CFS

Select a spool, load/unload, or edit its material, color and nozzle temperature.
Material filters shorten the selection list. Connected CFS units and the external
spool are handled from the same workflow, with CFS temperature and humidity shown
when reported.

![Filament controls](screenshots/filament.png)

Before printing, map each file filament to a printer filament. The picker states
the required material and disables incompatible choices. Colors may differ;
material mismatches block Start print. Long lists scroll by touch.

![Material-aware filament selection](screenshots/mapping.png)

## Files and recordings

Browse printer files and review a file before printing. Each list row has a trash
icon with a filename-specific Cancel/OK confirmation. Saved time-lapse recordings
use the same deletion flow and can be exported to USB when the time-lapse
component and a USB drive are available.

![Print files](screenshots/files.png)

## Settings

Adjust brightness and highlight color, screen/light/idle timers, CFS preferences,
network connections and time zone. System pages show machine information,
software information and restart controls. Unavailable operations explain why
instead of showing active controls.

![Screen, light and idle timers](screenshots/timers.png)

Screenshots are rendered by the native application with example data. Controls
and limits on a real printer depend on its configuration. The current interface
is in English.

## For developers

Build, desktop preview, architecture, tests and packaging are covered in the
[Native touchscreen developer guide](developer-tools/README.md). All developer
utilities live in `developer-tools/`; layout and behavior are implemented in the
native source and Python backend. Keep hardware-specific changes in printer
configuration or a separate integration branch.
