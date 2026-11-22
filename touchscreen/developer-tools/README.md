# Native touchscreen developer guide

This folder contains the native touchscreen's development utilities. They run on
a Linux PC and are not part of the printer's runtime. Edit and build the native
C/LVGL interface directly; the same sources render the printer panel, desktop
preview and offscreen screenshots.

Keep printer-specific calibration and hardware overrides out of this shared
implementation. Heater limits, travel limits, printer identity and CFS
capabilities come from runtime configuration. Changes should include a desktop
preview, relevant tests and an ARM build. Hardware acceptance is separate from
fixture tests.

## Architecture and files

Paths in the source map below are relative to `touchscreen/`.

```text
Touch / mouse -> native C + LVGL UI -> typed JSON over private Unix socket
                                      -> Python service -> Moonraker -> Klipper
                                                        -> local platform tools
```

The native application never implements motor, heater or CFS loading sequences.
The service validates requests against fresh printer state and configuration,
then calls the existing Klipper macros. A bounded resource coordinator tracks
operations without blocking navigation or queuing conflicting movements.
The activity indicator stays in the navigation rail; routine actions do not open
a loading modal. See [the control contract](CONTROL-CONTRACT.md) for the action
policy, required widget bindings, view inputs and extension rules.

| File or directory | Responsibility |
| --- | --- |
| `src/main.c` | Arguments, startup, event loop, fixture and screenshot modes |
| `src/platform.c` | Linux framebuffer, touch input, X11 window, socket transport and screenshots |
| `src/json.c` | JSON access helpers shared by views and state handling |
| `src/ui.c` | Reusable widgets, navigation, numeric keypad, dialogs and updates |
| `src/views.c` | Home, printer controls, filament, files, settings and their actions |
| `src/view_inputs.c` | Declarative snapshot dependencies; avoids rebuilding pages for unrelated telemetry |
| `src/screen.h` | Application state and C interfaces |
| `backend/action_policy.py` | Required action declarations, resource ownership and shared availability |
| `backend/service.py` | Moonraker HTTP/WebSocket connection, typed request validation, operations and snapshots |
| `backend/model.py` | Printer capabilities, heater/axis limits, filament records and mapping |
| `backend/platform_services.py` | Printer-local Wi-Fi, time zone, brightness and storage services |
| `backend/storage.py` | Image preparation, file/export helpers, time-zone choices and filament-reading policy |
| `assets/` | Editable SVG icons, source fonts and their licences |
| `assets/generated/` | Build output: raster icons and LVGL font C files; ignored by Git |
| `CMakeLists.txt`, `lv_conf.h` | Native build and LVGL configuration |
| `cmake/arm-linux.cmake` | ARMv7 hard-float cross-compilation and static linking |
| `native-screen.cfg.example` | Installation identity, panel/touch settings and optional local artwork |
| `migrate.py` | One-time display preference migration; leaves hardware calibration alone |
| `launch.py`, `start.sh` | Printer process startup, display settings, PID files and logs |
| `install.sh` | Staged installation of the prepared package, assets and Python backend |
| `tests/` | Backend guards and actual LVGL touch/layout integration tests |

The layout is defined directly in `src/ui.c` and `src/views.c`.

The installer at the repository root invokes `touchscreen/install.sh`. The
Python service does not draw frames. Pillow is used for cached image preparation,
not for the rendering loop. Fonts and icons are generated at build time.

## Tools in this folder

| File | Purpose | Output |
| --- | --- | --- |
| `build.sh host` | Install pinned build dependencies, generate assets and build the desktop app | `build-host/`, `assets/generated/` |
| `build.sh arm` | Generate the same assets and cross-compile/package the printer app | `build-arm/`, `package-arm/` |
| `build_assets.py` | Convert the bundled fonts and SVG icons using pinned tools | `assets/generated/` |
| `render_screenshots.py` | Render the user README screenshots from synthetic states and the bundled example part | `screenshots/` |
| `demo_snapshot.py` | Generate synthetic printer states for safe UI previews | A JSON snapshot at the requested path |
| `preview.sh` | Run the native desktop application using a snapshot/scenario | An interactive window; no printer commands |
| `package_arm.py` | Assemble an already-built ARM executable, assets, licenses and revision manifest | `package-arm/` |
| `package.json`, `package-lock.json` | Pin `lv_font_conv` and `lucide-static` used during asset generation | Installed build dependencies in `node_modules/` |


## Build on a Linux PC

Run commands from the repository root. Required tools are a C compiler, CMake,
Python 3.9 or newer, Node/npm and ImageMagick with SVG support. The interactive
preview also needs X11 headers and a desktop display, including XWayland on a
Wayland desktop. This is a native Linux preview, not a CPU or firmware emulator.

For a Debian/Ubuntu development machine, the relevant packages are:

```sh
sudo apt install build-essential cmake python3 nodejs npm imagemagick libx11-dev
sh touchscreen/developer-tools/build.sh host
```

The build downloads pinned LVGL 8.4.0 and cJSON 1.7.19 archives with SHA-256
checks, installs the locked asset tools, generates fonts/icons, and produces
`touchscreen/build-host/openklipper-screen`. Source fonts are bundled with their
licences. Node/npm and font tools are not runtime requirements on the printer.

## Preview without a printer

```sh
python3 touchscreen/developer-tools/demo_snapshot.py /tmp/screen-demo.json
sh touchscreen/developer-tools/preview.sh /tmp/screen-demo.json
```

Open a particular screen or a transient state directly:

```sh
sh touchscreen/developer-tools/preview.sh --scenario idle --page display
sh touchscreen/developer-tools/preview.sh --scenario idle --page fans
sh touchscreen/developer-tools/preview.sh --scenario heated --page move
sh touchscreen/developer-tools/preview.sh --scenario error
sh touchscreen/developer-tools/preview.sh --scenario loading
sh touchscreen/developer-tools/preview.sh --scenario homing
```

The `heated` scenario uses synthetic nozzle, bed and chamber temperatures so
manual extrusion and active heater targets can be previewed safely.

Other scenarios: `printing`, `meshing`, `load`, `unload`, `notice`, `recovery`,
and `disconnected` (use `--page move` to see the connection warning). These are
synthetic states rendered by the same dialogs as the printer. Operation scenarios
keep the activity indicator running until the window is closed; controls reflect
the real resource policy, but no printer operation is executed.

Files keeps its cached list visible while refreshing in the background; its
header reports refresh progress or failure without opening an operation modal.
Time-zone rows select a draft value. The highlighted row shows the selection. A pending row reveals its own **Apply** button; Back discards it.
The compact current-zone summary and full-height list keep browsing space available.

The window is 800 by 480 pixels. Mouse presses represent touches. For printing
layouts, generate the snapshot with `--printing`.

Fixture mode never connects to the printer service or sends printer commands.
Navigation and dialogs run locally; operations needing a backend response do not
complete as live operations. It is a UI preview, not a simulated printer backend.
The dimensions and heater limits in the demo generator are synthetic examples,
not defaults applied to a real printer.

For offscreen rendering, without a desktop display:

```sh
touchscreen/build-host/openklipper-screen \
  --assets touchscreen/assets/generated \
  --fixture /tmp/screen-demo.json --page home \
  --snapshot /tmp/screen-home.ppm
```

Other useful pages include `move`, `heat`, `fans`, `filament`, `calibration`,
`files`, `display` and `timezone`. The application also supports fixture action
scripts, geometry exports and command recording; see `tests/test_native_ui.py`
for examples that exercise the real LVGL controls.

## Refresh the user guide screenshots

After building the desktop target, run:

```sh
python3 touchscreen/developer-tools/render_screenshots.py
```

This uses the same native application and fixture mode, with the original example
part in `fixtures/demo-part.svg`. It writes the six PNGs referenced by the user
README. It does not connect to a printer. Review the screenshots after changing
layout or typography; keep documentation images aligned with the current UI.

## Build the precompiled printer package

On the Linux build machine, install the cross compiler and run:

```sh
sudo apt install gcc-arm-linux-gnueabihf
sh touchscreen/developer-tools/build.sh arm
```

The ARM target is statically linked for ARMv7 with NEON and hard-float ABI. Confirm
that target compatibility before using it on another controller architecture.
No display server or X11 runtime is required on the printer.

The build creates `touchscreen/package-arm/` containing the executable, generated
assets, dependency licences and a source revision identifier. The touchscreen
installer copies the Python backend and startup files from the matching source
checkout. Therefore this package currently accompanies that checkout; it is not
a standalone firmware updater. Never mix a binary/assets package with unrelated
backend sources.

Build release packages from a clean, committed checkout so the recorded revision
identifies the included source.

To produce the complete installable archive from that same committed checkout:

```sh
python3 tools/release/make_native_bundle.py k2-openklipper-native-armv7.tar.gz
```

The tool rejects mismatched source/package revisions or changed package files.
It includes source, the compiled runtime package, `SOURCE_REVISION`, per-file
checksums and an archive checksum. It excludes local settings and build caches.

For distribution, a maintainer can build this package manually and include it
with the full versioned OpenKlipper bundle. Include the source revision,
checksums, licence notices and corresponding source. Do not require compilation
on the printer. GitHub Actions is optional for this process.

The current root installer checks that the native package exists before changing
the installation. It does not automatically download missing native packages.
Use the documented project installation procedure for full-system installation;
`touchscreen/install.sh` alone changes only the screen application.

Keep model-specific hotend, extruder, cutter and Z-reference settings in the
printer's configuration or its separate integration branch. They are not part of
the shared native touchscreen implementation.

## Checks and runtime diagnostics

After building the host target:

```sh
python3 -m unittest discover -s touchscreen/tests -v
```

UI integration tests require the host binary and generated assets. They are
skipped when the binary is missing, so build before checking the result. Set
`SCREEN_BINARY` to test another native build, for example an AddressSanitizer
build. The Python tests use mocks rather than operating a printer.

On the printer, the service uses `/tmp/openklipper-screen.sock`. Runtime logs are
`/tmp/openklipper-screen-service.log` and `/tmp/openklipper-screen.log`; PID files
use the same names. Configuration is normally `native-screen.cfg` alongside the
printer configuration, with preferences in `native-screen-settings.json`.

First activation and complete-system testing still require validation on actual
hardware. A successful PC rendering test does not establish printer hardware
compatibility or replace a full installation backup.
