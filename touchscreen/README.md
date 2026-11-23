# Native touchscreen

A fast, responsive native LVGL interface for **K2 OpenKlipper**, with touch
controls for printing, filament and everyday printer settings. It installs and
updates as part of K2 OpenKlipper—no separate touchscreen installation is needed.
Printer identity, temperature limits, travel limits and available controls follow
the printer configuration.

## Interface examples

These screenshots come from the native application using example data. The
interface is currently in English. Available features depend on the printer and
its installed firmware components.

### Home: ready to print

- See the original printer illustration when idle, with or without CFS automatically.
- Tap a temperature tile to set its target.
- Open files, filament controls or fans, and switch the light.

![Idle Home screen](screenshots/idle.png)

### Home: printing

- See the model preview, remaining hours/minutes, layer count and progress.
- Pause or stop the print, or select failed objects to skip.
- Tap the print status area or its history icon to review printer messages.
- Print status is centered beside CFS and supports two lines, with an ellipsis for longer messages.
- Check spool colors and the feeding-filament marker in the CFS shortcut.

![Home during a print](screenshots/home.png)

### Set temperatures and watch heating

- Tap the nozzle, bed or chamber tile to change its target.
- Choose a preset or tap the target value for numeric entry.
- Temperature limits follow the printer configuration.
- Change a target again while heating; calibration and filament safety locks still apply.

![Nozzle temperature control](screenshots/temperature.png)

While heating or cooling toward a target, the tile shows the current temperature
and an arrow to the target. Once the temperature is within range, it shows a
single current value.

![Home while heating](screenshots/heating.png)

### See what the printer is doing

Routine operations show a compact activity indicator with the operation name in
the sidebar. You can continue navigating; controls that conflict with the
operation become unavailable.

![Operation in progress](screenshots/executing.png)

The indicator disappears when the operation finishes. Idle screens have no
activity badge, and printing is represented in the Home status area. Calibration failures
appear immediately as error dialogs. During calibration, the Home file button
shows **Calibration in progress** instead of offering another print. Homing alone
does not block file browsing.

![Calibration in progress](screenshots/calibrating.png)

### Startup and connection recovery

- During startup, a full-screen indicator stays visible until printer control,
  configuration and the initial motor handshake are ready.
- If Klipper or an MCU stops responding after connection, a blocking recovery
  screen replaces all printer controls. Restart Klipper or controller firmware
  using the two actions, each with confirmation.
- When a healthy connection returns, the previous page comes back automatically.
- If the local touchscreen service is unavailable, a separate reconnecting screen
  appears without Klipper restart controls.

![Printer startup](screenshots/startup.png)

![Connection lost after startup](screenshots/connection-lost.png)

![MCU or Klipper shutdown](screenshots/shutdown.png)

![Touchscreen service unavailable](screenshots/service-lost.png)

## Printer controls

### Move

- Home the printer, jog X/Y and Z, or enter a destination.
- Movement becomes available after the relevant axes are homed.
- Unlock motors for manual movement.
- Extrude or retract when the nozzle is hot enough and the printer state allows it.

![Movement controls](screenshots/move.png)

### Thermals

- View nozzle, bed and chamber temperatures together.
- Tap a tile to set its target.
- When idle, use **Turn heaters off** to switch off all heaters.

![Thermal controls](screenshots/thermals.png)

### Fans

- Adjust part cooling, side cooling and chamber exhaust with touch sliders.
- Hotend cooling remains under firmware control.

![Fan controls](screenshots/fans.png)

### Filament: one CFS

- Select a spool, then edit its material, color and nozzle temperature, or Load/Unload.
- Use material filters to shorten the material list.
- The nozzle marker identifies feeding filament; the accent border identifies selection.
- Read CFS temperature and relative humidity below the spools.

![Single-CFS filament screen](screenshots/filament.png)

### Filament: multiple CFS units

- Switch units using the color-dot selectors above the four spools.
- Two, three or four selectors share the available width; the spool holder stays separate.
- With one CFS, the selector is hidden and the spool tiles use the extra height.

![Four-unit CFS layout](screenshots/multi-cfs.png)

This example demonstrates the multi-unit interface. The current OpenKlipper CFS
driver operates one configured unit; additional-unit hardware support requires
backend work. Environmental readings are not duplicated across units when only
one unit's readings are available.

### Filament: no CFS

- Tap the spool-holder card to edit filament information.
- Feed filament into the extruder manually; there are no Load/Unload buttons here.

![Spool-holder screen without CFS](screenshots/no-cfs.png)

### Tune

- Adjust print speed and flow ratio with buttons or direct numeric entry, up to 200%.
- Adjust Z offset for the current print with steps or direct entry; each change is limited to 0.1 mm.

![Live print tuning](screenshots/tune.png)

### Calibrate

- Run homing, Z tilt, a full bed mesh or input shaping where supported.
- Z tilt homes the axes first when needed.
- Start belt-tension or cutter calibration where supported.
- Save pending calibration changes with **Save configuration**.
- During printing, see an explanation instead of calibration controls.

![Calibration controls](screenshots/calibrate.png)

## Choose and start a print

### 1. Browse files

- Browse files with slicer thumbnails, or a fallback icon.
- Tap a file to review it; use the trash icon for Cancel/OK deletion confirmation.
- Delete other files during printing; the active print file stays protected.

![Printer file list](screenshots/files.png)

### 2. Review the file

- Check the larger preview and print details.
- Choose whether to calibrate before printing.
- Continue to filament selection before starting.

![Selected print file](screenshots/file.png)

### 3. Match filaments

Match each filament required by the file to a source on the printer. The overview
pairs the requested color/material with the selected source. Tap a source to
choose another available filament; long lists scroll by touch.

![File-to-printer filament mapping](screenshots/mapping-review.png)

The picker shows the required material and disables incompatible choices. Colors
may differ, but material mismatches block starting the print. Review the mappings,
then choose **Start print**.

![Material-aware source picker](screenshots/mapping.png)

## Settings

### General

- Change brightness and the interface highlight color.
- Set screen, light and idle timers.
- Configure automatic filament reading and refill when CFS is connected.

![General settings](screenshots/general.png)

![Screen, light and idle timers](screenshots/timers.png)

### Network

- View Ethernet and Wi-Fi connection details.
- Scan for Wi-Fi networks, choose one and enter its password.
- See connection success or failure feedback.

![Network settings](screenshots/network.png)

### Time-lapse

- Enable or disable recording when the time-lapse component is available, including during a print.
- Browse recordings, save individual recordings to USB or confirm deletion.

![Time-lapse settings and recordings](screenshots/timelapse.png)

### Storage

- Check available printer storage and connected USB drives.
- Eject or format USB drives when available. Ejected drives remain unavailable until remounted.
- During printing, unavailable storage operations show an explanation.

![Storage settings](screenshots/storage.png)

### System

- View machine information and software versions.
- Set the time zone and access logs.
- Restart Klipper or controller firmware with a confirmation warning.

![System settings](screenshots/system.png)

## More interface examples

The galleries below show the supporting screens and dialogs used in these
workflows. Screens that depend on hardware are shown with example connection and
capability data.

### Temperature entry, activity and messages

Temperature controls share the same layout. Numeric entry opens a larger keypad.
The bottom of the sidebar names active work, including **Loading filament**,
**Unloading filament** and **Refreshing files**. Refresh keeps the file list visible.
Progress already shown on the current page is not repeated in the sidebar.
Faults and notices appear separately, with a clear dismissal action.

| Bed temperature | Chamber temperature |
| --- | --- |
| ![Bed temperature](screenshots/bed-temperature.png) | ![Chamber temperature](screenshots/chamber-temperature.png) |
| Numeric entry | Reading file |
| ![Numeric temperature entry](screenshots/numeric.png) | ![Reading file activity](screenshots/loading.png) |
| Loading filament | Unloading filament |
| ![Loading filament activity at the bottom of the sidebar](screenshots/load.png) | ![Unloading filament activity at the bottom of the sidebar](screenshots/unload.png) |
| Printer error | Notice |
| ![Printer error dialog](screenshots/error.png) | ![Printer notice](screenshots/notice.png) |
| Status history | Recent errors |
| ![Status history](screenshots/history.png) | ![Recent errors](screenshots/errors.png) |

| File deletion confirmation | Stop-print confirmation |
| --- | --- |
| ![Confirm deleting a file](screenshots/delete-file.png) | ![Confirm stopping a print](screenshots/stop-print.png) |
| Unlock motors | Printer startup |
| ![Unlock motors warning](screenshots/unlock-motors.png) | ![Waiting for printer startup](screenshots/startup.png) |

### Filament editing and unit selection

Edit material, color and nozzle temperature from the slot editor. Material
filters and the color picker keep common choices within reach. The unit selector
shows only the reported CFS units; changing the viewed unit does not load filament.

| Two CFS units | Three CFS units |
| --- | --- |
| ![Two CFS selectors](screenshots/two-cfs.png) | ![Three CFS selectors](screenshots/three-cfs.png) |
| Filament editor | Material selection |
| ![Filament editor](screenshots/edit.png) | ![Filtered material selection](screenshots/materials.png) |
| Color selection | CFS preferences |
| ![Filament color picker](screenshots/colors.png) | ![CFS detection and refill preferences](screenshots/refill.png) |
| Automatic refill sequence | Print object selection |
| ![Matching backup filament sequence](screenshots/refill-sequence.png) | ![Print object selection](screenshots/objects.png) |

Automatic refill uses loaded slots with matching material and color. The sequence
screen shows the available matching sources. Object selection shows the plate and
part outlines: select failed parts, then confirm skipping them.

### Display and network details

Display settings control brightness and the interface highlight color. Network
pages distinguish Ethernet from Wi-Fi. For a secured wireless network, enter the
password with the on-screen keyboard; the eye icon shows or hides it.

| Display and appearance | Ethernet details |
| --- | --- |
| ![Display settings](screenshots/display.png) | ![Ethernet connection](screenshots/ethernet.png) |
| Wi-Fi details | Available networks |
| ![Wi-Fi connection](screenshots/wifi.png) | ![Available wireless networks](screenshots/networks.png) |
| Password entry | Successful connection |
| ![Wi-Fi password keyboard](screenshots/wifi-password.png) | ![Wi-Fi connected notice](screenshots/wifi-connected.png) |
| Connection failure | USB drive details |
| ![Wi-Fi failure notice](screenshots/wifi-failed.png) | ![USB storage actions](screenshots/usb.png) |

### Recordings and system details

Saved recordings provide individual USB-save and delete actions. System pages
show printer/software information, time-zone selection and logs. Saving
configuration and restarting require deliberate confirmation; unavailable
calibration shows an explanation instead of live controls.

| Saved recordings | Machine information |
| --- | --- |
| ![Saved time-lapse recordings](screenshots/recordings.png) | ![Machine information](screenshots/machine.png) |
| Software versions | Time zone |
| ![Software versions](screenshots/software.png) | ![Time-zone selection](screenshots/timezone.png) |
| Help and logs | Restart choices |
| ![Help and logs](screenshots/help.png) | ![Restart options](screenshots/restart.png) |
| Restart Klipper confirmation | Firmware restart confirmation |
| ![Restart Klipper warning](screenshots/restart-klipper.png) | ![Firmware restart warning](screenshots/restart-firmware.png) |
| Save configuration | Calibration during a print |
| ![Save configuration confirmation](screenshots/save.png) | ![Calibration unavailable during printing](screenshots/calibrate-printing.png) |

## For developers

See the [Native touchscreen developer guide](developer-tools/README.md) for
architecture, Linux builds, local previews, testing and packaging. Portable
[preview cases and artwork](developer-tools/fixtures/README.md) are included in
the repository.

### More workflow examples

| Longer print and readable dark preview | Calibration activity |
| --- | --- |
| ![Hours and minutes remaining](screenshots/printing-long.png) | ![Calibration in progress](screenshots/calibrating.png) |
| ![Dark model on a neutral preview background](screenshots/dark-preview.png) | ![Ejected USB drive](screenshots/usb-ejected.png) |

| Finding networks | Connecting to the chosen network |
| --- | --- |
| ![Scanning for Wi-Fi](screenshots/wifi-scanning.png) | ![Connecting to Wi-Fi](screenshots/wifi-connecting.png) |

The password field has a blinking caret and a muted hint that disappears when
you type. The password keyboard uses one-shot Shift; double-tap Shift for capitals until
you tap it again. Restart controls remain available during a print, with a warning
that the print cannot continue.

| Long print and high layer counts | Long print status |
| --- | --- |
| ![Long print with six-digit layers](screenshots/printing-extreme.png) | ![Two-line status and history access](screenshots/printing-status.png) |

### Optional print-failure detection

K2 OpenKlipper includes an optional failure watcher for an external PC, using
Obico and a local vision model. Its settings, including automatic pause, live on
that PC in `/etc/default/k2-detect`. It does not expose a touchscreen settings
API. See [the upstream watcher implementation](../tools/ai-detect/) for setup.

### Refresh without blocking browsing

The file list stays in place while **Refreshing files** appears in the sidebar.
The badge disappears when the read completes. Tap an active badge to open status
history; history is also available under **Help & logs** when idle.

![Background file refresh](screenshots/refreshing-files.png)
