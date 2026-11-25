# Native touchscreen developer guide

This guide covers building, changing and testing the native touchscreen, then
shipping the matching printer binary. All commands run from the repository root.
Use `touchscreen/out/` for local outputs; it is ignored by Git.

## 1. Understand the components

The interface is written in **C11 with LVGL**, with a **Python backend**. The same
C layout runs on the printer, in an interactive Linux desktop window and in
headless screenshot tests. The desktop application is a native preview, not a
printer firmware or CPU emulator.

Touch events become JSON requests over a private Unix socket. The Python service
checks current state and configuration, reserves the resources needed for the
action, and calls Moonraker/Klipper or printer-local platform services. Motion,
heating and CFS loading sequences belong to the firmware, not the UI.

`backend/storage.py:prepare_image()` uses **Pillow** to decode, crop,
resize and cache images in a backend worker. LVGL renders the controls, text and
frames; Pillow is not the screen renderer. Install Pillow for backend development
and the complete test suite. The C preview executable does not import Python or
Pillow: it reads prepared fixture data and image assets.

Paths below are relative to `touchscreen/`:

| Location | Responsibility |
| --- | --- |
| `src/main.c` | Startup, event loop and fixture command-line options |
| `src/platform.c` | Framebuffer/touch, X11, socket transport and capture |
| `src/ui.c` | Shared controls, navigation, dialogs and fixture-only updates |
| `src/views.c` | Screen layouts, keyboard and UI actions |
| `installation/uninstall.sh`, `installation/uninstall.py` | Verified return to upstream HarkScreen; installed beside the native runtime |
| `src/mesh.c` | Shaded bed mesh, touch rotation, statistics and profile controls |
| `src/view_inputs.c` | State dependencies for efficient live updates |
| `src/json.c`, `src/screen.h` | JSON helpers, UI state and layout constants |
| `backend/service.py` | Moonraker integration, requests, operations and thumbnails |
| `backend/action_policy.py` | Action availability and resource ownership |
| `backend/model.py` | Printer capabilities, limits and filament compatibility |
| `backend/platform_services.py` | Network and printer-local services |
| `backend/storage.py` | Image cache, file/export and time-zone helpers |
| `backend/gcode_metadata.py` | Bounded fallback reader for embedded G-code metadata and PNGs |
| `../klippy/extras/k2_cfs.py`, `hark_cfs_slots.py` | Slot-event history and persistent inventory invalidation |
| `assets/` | Production SVGs, fonts and licenses |
| `build/` | CMake, ARM toolchain and LVGL configuration |
| `runtime/` | Startup scripts and example configuration |
| `installation/` | Installation, validation and preference migration |
| `precompiled/armv7/` | Committed printer binary, assets, licenses and manifest |
| `developer-tools/` | Development scripts and this guide |
| `developer-tools/fixtures/` | Portable preview recipes and artwork |
| `tests/` | Backend and native UI regression tests |

Production layouts are defined in C. There is no separate design document that
generates the UI. Printer-specific hotend, extruder, cutter and Z-reference changes
belong in printer configuration or a separate integration branch.

## 2. Set up and build on Linux

Use a C compiler, CMake 3.18+, Python 3.10+, Pillow, Node/npm, ImageMagick
`convert` with SVG support, and X11 development headers. On Debian/Ubuntu:

```sh
sudo apt install build-essential cmake python3 python3-pil nodejs npm imagemagick libx11-dev clang-format
sh touchscreen/developer-tools/build.sh host
```

The build downloads pinned, hash-checked LVGL 8.4.0 and cJSON 1.7.19, installs
locked asset tools, generates icons/fonts and produces
`touchscreen/out/host/openklipper-screen`. Network access is needed to obtain
uncached dependencies.

After C-only edits, rebuild incrementally:

```sh
cmake --build touchscreen/out/host --parallel 4
```

After production asset or build-system changes, run `build.sh host` again.
`build_assets.py` lists the exact icon sizes and fonts used by the layouts; update
that inventory when adding a control. Unused size variants are not shipped.
Interactive previews need an X11 display, or XWayland on a Wayland desktop.
Headless captures do not need a display server.

## 3. Preview the interface

### Start with a bundled case

A **case** combines generated state, bundled artwork, an opening page and optional
interaction steps. This is the simplest way to inspect a complete UI state:

```sh
sh touchscreen/developer-tools/preview.sh --case idle
sh touchscreen/developer-tools/preview.sh --case printing
sh touchscreen/developer-tools/preview.sh --case heating
sh touchscreen/developer-tools/preview.sh --case multi-cfs
```

The window is 800 × 480. Mouse presses act as touches. List all cases with:

```sh
sh touchscreen/developer-tools/preview.sh --list
```

| Case | State or workflow |
| --- | --- |
| `idle` | Printer illustration and one CFS |
| `printing` | Progress, holding temperatures and three print objects |
| `heating` | Current-to-target temperature arrows |
| `multi-cfs` | Four units and sixteen spools |
| `mesh`, `mesh-flat`, `mesh-empty`, `mesh-unloaded` | Rotatable sample mesh, flat surface, no data, and saved profiles with no active mesh |
| `mesh-delete` | Inline saved-profile delete confirmation |
| `mesh-printing`, `meshing` | Read-only viewing during a print and measurement progress |
| `motors-on`, `motors-off` | Crossed/plain motor action icons and homing restrictions |
| `file` | File preview, metadata and calibration option |
| `file-reading`, `file-no-preview`, `printing-no-preview` | Reading and missing-thumbnail states |
| `idle-lit` | Light icon with visible rays |
| `spool-inserted`, `spool-removed` | Filament-change prompt and empty CFS slot |
| `mapping` | File filament requirements and source selection |
| `objects` | Object outlines and skip confirmation |
| `empty-extruder` | Spools present, no filament feeding the nozzle |
| `no-cfs` | Printer without CFS and spool-holder controls |
| `wifi-password` | Secured network password keyboard |
| `wifi-connected`, `wifi-failed` | Network result notices |
| `homing`, `error` | Activity indicator and recoverable fault dialog |
| `calibrating` | Calibration activity and blocked Home print selection |
| `refreshing-files` | Visible file list with background sidebar progress |
| `printing-long`, `dark-preview` | Hours/minutes remaining and dark model visibility |
| `printing-extreme`, `printing-status` | Long time/layer counts and two-line truncated status |
| `wifi-scanning`, `wifi-connecting` | Centered network progress states |
| `usb-ejected` | Physically present but unavailable drive |
| `starting`, `shutdown` | Initial loading screen and blocking connection recovery |
| `service-lost` | Separate local touchscreen-service reconnecting screen |

To inspect another page using a case's state, add `--page`:

```sh
sh touchscreen/developer-tools/preview.sh --case idle --page fans
sh touchscreen/developer-tools/preview.sh --case idle --page filament
sh touchscreen/developer-tools/preview.sh --case idle --page restart
```

The preview runs in fixture mode and never connects to a printer. Navigation,
dialogs, mapping and object selection work locally. Confirmed demo deletions and
preference switches affect only fixture state; reopening the case resets it.
Motion, heating, network changes and exports are not simulated to completion.

### Preview the bed mesh and motor icons

```sh
sh touchscreen/developer-tools/preview.sh --case mesh
sh touchscreen/developer-tools/preview.sh --case meshing
sh touchscreen/developer-tools/preview.sh --case mesh-printing
sh touchscreen/developer-tools/preview.sh --case motors-on
sh touchscreen/developer-tools/preview.sh --case motors-off
```

The mesh case contains a synthetic 9-by-9 surface and three saved profiles.
The layout pairs a shaded surface with current-mesh statistics and a scrollable
profile list, following the useful arrangement in [HelixScreen](https://helixscreen.org/guide/calibration/).
Its renderer and controls are implemented independently for our LVGL 8 application.

Drag the surface to rotate; use the
reset-view icon to restore the initial angle. Probe opens the existing
calibration confirmation. The preview never sends commands to a printer and
does not simulate physical probing; use `meshing` to inspect its progress state.
The normal idle case also includes the sample mesh, accessible from Move.
Profile rows show their Z range and inline Load/Delete controls. The active row
uses Rename instead of Load. Loading does not open another screen; deleting asks
for confirmation. Save creates a named copy of the current mesh. Use Calibrate >
Save configuration for a persistent save and confirmed restart. During printing,
viewing and scrolling remain available, while probing and profile changes are
disabled. The simulator applies profile edits to its local fixture; reopening the case
restores the sample profiles. It never connects to the printer.
The X/Y/Z orientation key uses the same camera projection as the surface.


Motor icons show the **available action**: crossed means turn the motors off;
plain means turn them on. Turning them off still asks for confirmation.

### Capture a screenshot

Add `--snapshot` to render headlessly instead of opening a window. Convert the
resulting PPM to PNG if needed:

```sh
sh touchscreen/developer-tools/preview.sh --case multi-cfs \
  --snapshot touchscreen/out/preview/multi-cfs.ppm
convert touchscreen/out/preview/multi-cfs.ppm touchscreen/out/preview/multi-cfs.png
```

### Create a custom state

A **scenario** generates raw state without a case's opening-page or interaction
recipe. Use it when testing a state on a specific page:

```sh
sh touchscreen/developer-tools/preview.sh --scenario heated --page move
sh touchscreen/developer-tools/preview.sh --scenario disconnected --page move
```

Other scenario names include `load`, `unload`, `meshing`, `notice` and `recovery`;
`SCENARIOS` in `demo_snapshot.py` is the complete list.

For finer control, generate a full JSON snapshot, edit it, then launch that file:

```sh
python3 touchscreen/developer-tools/demo_snapshot.py \
  touchscreen/out/preview/custom.json --case idle
sh touchscreen/developer-tools/preview.sh touchscreen/out/preview/custom.json
```

These snapshots contain asset paths for the current checkout. For a reusable test
case, commit a small recipe and bundled assets instead of the generated snapshot.
See [fixture authoring](fixtures/README.md) for the schema and interaction format.
Demo identities, temperatures and travel limits are synthetic test data.

## 4. Change the UI and backend

Use the source map above to locate the relevant page or service. Follow the
[control contract](CONTROL-CONTRACT.md) when adding actions, resource ownership
or availability rules. A visual change must preserve backend validation and
confirmation behavior.

Reuse the existing UI helpers:

- `row()` for navigation/information cards and `toggle_card()` for preferences.
  Shared spacing comes from `SETTINGS_CARD_GAP` and `SETTINGS_ROW_PITCH`.
- `image_fit()` for proportional previews with a small inset. Missing file
  thumbnails fall back to the file icon.
- `modal_message()` for shared centered dialog bodies. It fills the space between
  header and buttons, with vertical scrolling when text exceeds the viewport.
- Shared keyboard and numeric-entry controls. Single-line keyboard mode must be
  set before final field dimensions; preserve all printable password characters.

After a confirmed Klipper or firmware restart, hide recovery actions while waiting
for a disconnect/ready cycle or a new printer generation. Unchanged cached ready
status does not complete a restart. After 60 seconds, offer retry while continuing
to reconnect; a failed request releases the wait immediately.

Connection state is owned by `ui_connection_mode()` above all page layouts.
Before the first complete ready snapshot, show the startup screen. After a usable
connection, loss or Klipper shutdown replaces the entire page with recovery
controls. Only confirmed restart requests may pass the UI action guard in that
state; backend policy still validates requests. `platform_linked()` detects a lost
local socket so stale snapshots cannot leave controls enabled. Local service loss has its own reconnecting screen without firmware controls.
Reconnection restores the previous route. Fixture actions support `{"transport_lost": true}`
for testing socket-loss UI behavior without touching a real service.

Keep state distinctions explicit: a spool being present differs from filament
feeding the nozzle; the nozzle marker requires the extruder sensor and active
source. The selected spool identifies the target of Edit/Load/Unload. CFS unit
selectors appear only for multiple units and share the available width equally.
The preview supports four units; it does not establish multi-unit driver support.

Read heater limits and printer identity from the backend configuration. Material
compatibility must be checked both in the selector and before starting a print.

### File-list refresh lifecycle

The backend preloads files after its first ready connection. Entering Files from
another section requests a background refresh while retaining the visible list.
Returning from file details or filament mapping reuses that list. Automatic
requests reuse a successful refresh for 15 seconds and coalesce while a read is
in progress; the refresh button always requests a new read. Failed startup reads
retry after 30 seconds without clearing cached files.

## 5. Verify the change

Build the host target first; native tests skip themselves when the binary is
missing. Tests use fixtures and mocks and do not operate a printer.

```sh
sh touchscreen/developer-tools/format.sh --check
python3 -m unittest discover -s touchscreen/tests -v
```

For a separate address/undefined-behavior sanitizer build:

```sh
cmake -S touchscreen/build -B touchscreen/out/asan \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer" \
  -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined"
cmake --build touchscreen/out/asan --parallel 4
ASAN_OPTIONS=detect_leaks=0 SCREEN_BINARY="$PWD/touchscreen/out/asan/openklipper-screen" \
  python3 -m unittest discover -s touchscreen/tests -v
```

For geometry or request diagnostics, first generate the custom snapshot shown in
section 3, then run the binary directly:

```sh
touchscreen/out/host/openklipper-screen \
  --assets touchscreen/assets/generated \
  --fixture touchscreen/out/preview/custom.json --page home \
  --snapshot touchscreen/out/preview/home.ppm \
  --geometry touchscreen/out/preview/home-geometry.json \
  --record-commands touchscreen/out/preview/commands.jsonl
```

`tests/test_native_ui.py` demonstrates `--actions` sequences and assertions against
the actual LVGL tree. Review screenshots at native size as well as running tests.

## 6. Update artwork and documentation images

Production icons/fonts are generated by `build_assets.py` as part of `build.sh`.
Its Node dependencies are pinned in `package.json` and `package-lock.json`.

The original printer illustration lives in `assets/printer.svg`. The fixture
generator creates both production PNGs (with and without CFS) and the matching
preview copies. The backend selects the bundled image automatically from CFS
presence. Image paths are not printer configuration settings.

Preview artwork has its own generator. After editing fixture SVGs, regenerate
their committed PNGs; after changing visible layouts, refresh the user README's
screenshots and inspect them:

```sh
python3 touchscreen/developer-tools/build_fixtures.py
python3 touchscreen/developer-tools/render_screenshots.py
```

Commit reviewed fixture artwork and documentation screenshots. Do not commit
`out/`, `node_modules/` or `assets/generated/`.

## 7. Build and ship the printer package

Normal users run the project installer. It downloads the selected release-tag
source archive, which includes the committed `precompiled/armv7/` runtime. The
root installer invokes `touchscreen/installation/install.sh` to place that runtime
on the printer. Users do not compile or download the touchscreen separately.

After runtime/build changes, commit the source first, then build and validate:

```sh
sudo apt install gcc-arm-linux-gnueabihf
sh touchscreen/developer-tools/build.sh arm
python3 touchscreen/installation/validate_package.py \
  --source touchscreen --package touchscreen/precompiled/armv7
```

`build.sh arm` also runs `package_arm.py`. Commit the updated precompiled package
in a second commit. The manifest records source provenance and hashes build inputs
and package files. Packaging rejects uncommitted source inputs; installation
rejects a stale package. Documentation, fixtures and test-only edits do not require
an updated binary.

The runtime is statically linked ARMv7/NEON/hard-float; verify compatibility before
using another controller. The printer does not need Node/npm, X11 or asset-build
tools. Its Python backend still uses Pillow for image preparation.

The external-host release path is separate:
`.github/workflows/host-release-asset.yml` checks out a release tag, builds the ARM
screen and creates `k2-openklipper-host.zip`. It uploads the ZIP to the GitHub
release, not a test machine. That packer omits the K2-specific Klipper C helper,
which the external host builds locally. Normal printer installation uses the
release-tag source archive, not this ZIP.

## 8. Test on a printer and diagnose runtime issues

Desktop checks do not replace installation and hardware acceptance testing. Use
the full project installer to verify the normal user installation path.

For isolated touchscreen development, copy the matching checkout to an idle
printer and run the following there. Replace the example configuration directory
and `F008` profile with the actual printer values:

```sh
sh touchscreen/installation/install.sh /mnt/UDISK/printer_data_mainline/config F008
sh /mnt/UDISK/openklipper-screen/start.sh /mnt/UDISK/printer_data_mainline/config/native-screen.cfg
```

This installs the screen/service without reinstalling Klipper. Runtime diagnostics:

| Item | Location |
| --- | --- |
| Private socket | `/tmp/openklipper-screen.sock` |
| Backend log | `/tmp/openklipper-screen-service.log` |
| UI log | `/tmp/openklipper-screen.log` |
| PID files | Same base names as the logs, with `.pid` |
| Display configuration | `native-screen.cfg` in the selected configuration directory |
| Preferences | `native-screen-settings.json` beside the display configuration |

### Slow responses and connection recovery

The backend logs slow snapshots (over 0.5 seconds), at most once per 30 seconds
per client. Each entry includes wall time, CPU time, lock wait, snapshot creation,
encoding, sending and payload size. High wall time with little CPU time suggests
scheduling, lock or socket waits; high CPU time suggests expensive computation.
Stream reconnects and background refresh failures are logged separately.

Routine snapshots are sent at up to five updates per second; incoming commands
wake the socket loop immediately. A failed peripheral refresh does not invalidate
a healthy printer stream. Same-host reconnects retain cached settings while
refreshing them; a remote clock reset still invalidates the old configuration.
The backend validates current settings again before printer actions. Connecting
the screen never replays saved idle, light or CFS settings; it reads their live
printer values. Only explicit user changes send preference commands.

Touch state is cancelled on connection transitions and input-event loss. A press
with no new input events for five seconds is cancelled without activating its
button. Device removal errors trigger bounded reopen attempts.

When diagnosing a running printer, start with read-only `/proc` CPU, RSS,
affinity and interrupt samples. Compare RSS over time before claiming a leak.
The UI and backend are not pinned to a CPU; Linux can schedule them on any CPU
allowed by their parent process. Their low nice priorities are retained. Check for
a busy core or interrupt load before changing priorities; do not alter the
printing stack as a diagnostic shortcut.

## CFS inventory event integration

The full installer must include the matching `k2_cfs.py` and `hark_cfs_slots.py`.
A UI-only deployment cannot add the slot-event producer; updating these Klipper
modules requires a Klipper restart. Do not restart an active print.

The driver publishes a bounded sequence-numbered `slot_event_history` and sends
`k2_cfs:slot_event`. Removal marks inventory unloaded while retaining the former
profile internally for the current filament tail and existing refill macros.
Native snapshots mask those details; the editor starts blank. Insertion adds a
slot-specific notice whose OK action selects that unit/slot on the filament page.
Startup/reconnect does not replay historical prompts; removal cancels a pending
insertion notice. RFID-ready does not invent a material profile.

Run driver integration tests with an environment containing CFFI:

```sh
python3 -m unittest discover -s tests -q
```
