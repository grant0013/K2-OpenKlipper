# Native touchscreen

A fast, responsive native LVGL interface for **K2 OpenKlipper**. It installs and
updates with K2 OpenKlipper using the included precompiled package; no separate
installation or compilation on the printer is needed.

Printer identity, heater and travel limits, homing state and available features
come from the printer configuration. The interface is currently in English.
The screenshots below are rendered by the native application with example data.

## Home

- Idle Home shows the printer illustration, automatically with or without CFS.
- Tap temperature tiles to change targets; open files, filament controls or fans,
  and switch the light.

| Idle with CFS | Idle without CFS |
| --- | --- |
| ![Idle with CFS](screenshots/idle.png) | ![Idle without CFS](screenshots/idle-no-cfs.png) |

### During a print

- See the model preview, progress, remaining hours/minutes and `Layer current / total`.
- Pause, stop or open the plate view to skip failed objects.
- Tap the status text or history icon to open **Status history**. One or two lines
  are centered beside CFS; longer messages end with an ellipsis.
- CFS colors and the nozzle marker show available and feeding filament.
- The light icon shows its current state: rays when on, a crossed bulb when off.
  Light and fan shortcuts remain available; filament colors do not follow the UI accent.

| Printing | Heating toward targets |
| --- | --- |
| ![Printing](screenshots/home.png) | ![Heating toward targets](screenshots/heating.png) |

| Long status message | Long time and layer counts |
| --- | --- |
| ![Long status message](screenshots/printing-status.png) | ![Long time and layer counts](screenshots/printing-extreme.png) |

| Hours and minutes remaining | Dark model preview |
| --- | --- |
| ![Hours and minutes remaining](screenshots/printing-long.png) | ![Dark model preview](screenshots/dark-preview.png) |

![Bulb with rays when on](screenshots/idle-lit.png)

### Temperatures

- Choose a preset or tap the target for numeric entry. Limits follow configuration.
- While approaching a target, tiles show current → target; near the target they
  show the current value alone. Display rounding is stabilized to reduce flicker.
- Targets stay editable while heating. Calibration and filament safety locks still apply.

| Nozzle target | Numeric entry |
| --- | --- |
| ![Nozzle target](screenshots/temperature.png) | ![Numeric entry](screenshots/numeric.png) |

| Bed target | Chamber target |
| --- | --- |
| ![Bed target](screenshots/bed-temperature.png) | ![Chamber target](screenshots/chamber-temperature.png) |

### Activity, messages and recovery

- Routine work uses a small sidebar spinner with a short caption, such as
  **Homing**, **Loading filament** or **Refreshing files**. Navigation stays available.
- The badge disappears on completion and is hidden when idle. Progress already
  shown on the current page is not duplicated; printing alone creates no badge.
- Tap an active badge for history. When idle, use **System → Help & logs**.
- Errors appear as dialogs even during an operation. Calibration blocks choosing
  another print; homing alone still allows browsing files.

| Homing | Reading a file |
| --- | --- |
| ![Homing](screenshots/executing.png) | ![Reading a file](screenshots/loading.png) |

| Calibration in progress | Printer error |
| --- | --- |
| ![Calibration in progress](screenshots/calibrating.png) | ![Printer error](screenshots/error.png) |

| Notice | Status history |
| --- | --- |
| ![Notice](screenshots/notice.png) | ![Status history](screenshots/history.png) |

| Recent errors | Printer startup |
| --- | --- |
| ![Recent errors](screenshots/errors.png) | ![Printer startup](screenshots/startup.png) |

Startup stays on a loading screen until printer control and initial state are
ready. Losing Klipper or an MCU replaces controls with recovery actions; losing
the local touchscreen service shows a separate reconnecting screen. Recovery
returns to the previous page automatically.

| Printer connection lost | MCU or Klipper shutdown |
| --- | --- |
| ![Printer connection lost](screenshots/connection-lost.png) | ![MCU or Klipper shutdown](screenshots/shutdown.png) |

![Touchscreen service unavailable](screenshots/service-lost.png)

## Printer controls

### Move

- Home, jog X/Y and Z, or tap a coordinate to enter a destination within travel limits.
- Motion and movement-step selection follow homing and operation availability.
  Printing is identified by Klipper print state, not its generic idle timer.
- Extrude/retract only when the nozzle is hot enough and the printer state allows it.
- Unlock motors for manual movement; confirmation warns that the bed may drop.

| Movement | Unlock motors |
| --- | --- |
| ![Movement](screenshots/move.png) | ![Unlock motors](screenshots/unlock-motors.png) |

### Thermals and fans

- Thermals groups nozzle, bed and chamber targets in three columns.
- **Turn heaters off** is available when idle.
- Touch sliders control part cooling, side cooling and chamber exhaust where supported.
  Hotend cooling stays under firmware control.

| Thermals | Fans |
| --- | --- |
| ![Thermals](screenshots/thermals.png) | ![Fans](screenshots/fans.png) |

### Filament and CFS

- Select a spool to edit its material, color and nozzle temperature, or Load/Unload.
- The accent border marks selection; the nozzle marker identifies feeding filament.
- Material filters shorten the list. CFS temperature and humidity appear below the spools.
- With one CFS, all five spool tiles share the same height, with temperature and
  humidity centered below them. No unit selector is shown. With multiple reported
  units, two to four selectors share the available width; the spool holder remains separate.
- Without CFS, edit the spool-holder card and feed filament manually. Load/Unload
  buttons are hidden. **Reset** in any spool editor removes its saved details
  with confirmation; it does not move or unload filament.

| One CFS | No CFS |
| --- | --- |
| ![One CFS](screenshots/filament.png) | ![No CFS](screenshots/no-cfs.png) |

| Two CFS units | Three CFS units |
| --- | --- |
| ![Two CFS units](screenshots/two-cfs.png) | ![Three CFS units](screenshots/three-cfs.png) |

| Four CFS units | Filament editor |
| --- | --- |
| ![Four CFS units](screenshots/multi-cfs.png) | ![Filament editor](screenshots/edit.png) |

| Material filters | Color picker |
| --- | --- |
| ![Material filters](screenshots/materials.png) | ![Color picker](screenshots/colors.png) |

| Loading filament | Unloading filament |
| --- | --- |
| ![Loading filament](screenshots/load.png) | ![Unloading filament](screenshots/unload.png) |

| CFS preferences | Automatic refill sequence |
| --- | --- |
| ![CFS preferences](screenshots/refill.png) | ![Automatic refill sequence](screenshots/refill-sequence.png) |

| Spool-holder editor | Reset confirmation |
| --- | --- |
| ![Edit spool holder](screenshots/spool-holder-edit.png) | ![Reset spool-holder details](screenshots/reset-spool-holder.png) |

![Reset a CFS slot](screenshots/reset-cfs-slot.png)

Removing CFS filament makes its slot grey/empty and clears the editable details.
Insertion shows **Filament changed**; press **OK** to open the filament page with
that slot selected, then enter its material, color and nozzle temperature. This
manual-entry workflow does not guess material or fill it from an RFID record.

| Slot emptied on removal | Insertion notice |
| --- | --- |
| ![Empty slot after removal](screenshots/spool-removed.png) | ![Filament changed notice](screenshots/spool-inserted.png) |

![Affected slot selected after OK](screenshots/spool-inserted-slot.png)

The matching Klipper driver keeps the former profile internally for an in-flight
filament tail's purge temperature and refill matching. It is not offered as the
new spool's details. This flow requires the complete updated installation, not
just replacing the touchscreen binary.

Automatic refill uses loaded slots with matching material and color; the sequence
page shows matching sources. Automatic RFID reading is not exposed as a setting until record updates are supported.

**Multi-unit limitation:** the current OpenKlipper CFS driver operates one configured
unit. Multi-unit screenshots demonstrate the UI; additional hardware support needs
backend work. A single unit's environmental readings are not copied to other units.

### Tune and calibrate

- Tune print speed and flow using steps or numeric entry, up to 200%.
- Adjust Z offset for the current print; each change is limited to 0.1 mm.
- Calibration offers homing, Z tilt, full bed mesh, input shaping, belt tension
  and cutter calibration where supported. Z tilt homes missing axes first.
- Save pending configuration changes with confirmation. Calibration controls are
  replaced by an explanation during printing.

| Print tuning | Calibration |
| --- | --- |
| ![Print tuning](screenshots/tune.png) | ![Calibration](screenshots/calibrate.png) |

| Save configuration | Calibration unavailable during printing |
| --- | --- |
| ![Save configuration](screenshots/save.png) | ![Calibration unavailable during printing](screenshots/calibrate-printing.png) |

## Files and starting a print

1. Browse sliced files with thumbnails or a fallback icon. Refresh keeps the list
   visible and reports progress in the sidebar. File review shows a spinner while
   reading a new preview, then a larger fallback icon if no preview is available.
   Home uses the same fallback during printing. Previous previews are never reused.

   Embedded Creality/Orca PNGs and material metadata are read when Moonraker has
   not extracted them; file names are not used to guess material.
2. Review the preview and print details; select **Calibrate before printing** if available.
3. Match each file filament's color/material to a printer source, then **Start print**.
   Source lists scroll by touch. Incompatible materials are disabled and block starting;
   colors may differ.

- Trash icons open a filename-specific **Cancel / OK** confirmation.
- Other files can be deleted while printing; the active or pending print file is protected.
- Select failed parts on the plate view and confirm skipping them.

| Files | Background refresh |
| --- | --- |
| ![Files](screenshots/files.png) | ![Background refresh](screenshots/refreshing-files.png) |

| File review | Filament mapping |
| --- | --- |
| ![File review](screenshots/file.png) | ![Filament mapping](screenshots/mapping-review.png) |

| Material-aware source picker | Skip print objects |
| --- | --- |
| ![Material-aware source picker](screenshots/mapping.png) | ![Skip print objects](screenshots/objects.png) |

| Delete confirmation | Stop-print confirmation |
| --- | --- |
| ![Delete confirmation](screenshots/delete-file.png) | ![Stop-print confirmation](screenshots/stop-print.png) |

| Reading preview | No embedded preview |
| --- | --- |
| ![Reading the selected file](screenshots/file-reading.png) | ![Selected file without preview](screenshots/file-no-preview.png) |

![Print without preview](screenshots/printing-no-preview.png)

## Settings

### General

- Change brightness and the interface highlight color.
- Set screen sleep, light-off and idle timers. Printing, operations and urgent
  messages keep the screen awake; the first touch wakes it without activating a control.
- Open CFS refill preferences when connected.

| General | Display and appearance |
| --- | --- |
| ![General](screenshots/general.png) | ![Display and appearance](screenshots/display.png) |

![Screen, light and idle timers](screenshots/timers.png)

### Network

- View Ethernet and Wi-Fi details; scan networks and enter a password.
- The password hint disappears when typing. A blinking caret marks entry, and
  the eye icon shows/hides the password. Shift is one-shot; double-tap for capitals.
- Scanning and connecting use centered progress screens, followed by success or failure feedback.

| Network | Ethernet |
| --- | --- |
| ![Network](screenshots/network.png) | ![Ethernet](screenshots/ethernet.png) |

| Wi-Fi details | Available networks |
| --- | --- |
| ![Wi-Fi details](screenshots/wifi.png) | ![Available networks](screenshots/networks.png) |

| Password entry | Scanning |
| --- | --- |
| ![Password entry](screenshots/wifi-password.png) | ![Scanning](screenshots/wifi-scanning.png) |

| Connecting | Connected |
| --- | --- |
| ![Connecting](screenshots/wifi-connecting.png) | ![Connected](screenshots/wifi-connected.png) |

![Connection failed](screenshots/wifi-failed.png)

### Time-lapse and storage

- Enable/disable recording when the time-lapse component is available, including during printing.
- Save recordings to USB or confirm deletion with the icons beside each recording.
- View printer/USB storage. Eject or format when allowed; an ejected drive stays
  unavailable until remounted. Restricted operations show an explanation during printing.

| Time-lapse settings | Recordings |
| --- | --- |
| ![Time-lapse settings](screenshots/timelapse.png) | ![Recordings](screenshots/recordings.png) |

| Storage | USB actions |
| --- | --- |
| ![Storage](screenshots/storage.png) | ![USB actions](screenshots/usb.png) |

![Ejected USB drive](screenshots/usb-ejected.png)

### System

- View machine/software information, choose a time zone and access logs/history.
- Restart Klipper or controller firmware with confirmation, including during a print.
  The warning explains that an ongoing print cannot continue.

| System | Machine information |
| --- | --- |
| ![System](screenshots/system.png) | ![Machine information](screenshots/machine.png) |

| Software version | Time zone |
| --- | --- |
| ![Software version](screenshots/software.png) | ![Time zone](screenshots/timezone.png) |

| Help and logs | Restart options |
| --- | --- |
| ![Help and logs](screenshots/help.png) | ![Restart options](screenshots/restart.png) |

| Restart Klipper | Restart controller firmware |
| --- | --- |
| ![Restart Klipper](screenshots/restart-klipper.png) | ![Restart controller firmware](screenshots/restart-firmware.png) |

## Optional print-failure detection

K2 OpenKlipper's optional [failure watcher](../tools/ai-detect/) runs on an external
PC. Its settings, including automatic pause, live in `/etc/default/k2-detect` on
that PC. It does not expose a touchscreen settings API.

## For developers

The [developer guide](developer-tools/README.md) covers architecture, builds,
local previews, tests and packaging. Bundled [preview cases](developer-tools/fixtures/README.md)
use synthetic data and work without connecting to a printer.
