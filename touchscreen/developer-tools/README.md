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
| `build/` | CMake build definition, ARM toolchain and compile-time LVGL configuration |
| `runtime/` | Printer startup, launcher and example display configuration |
| `installation/` | Package validation, installation and display preference migration |
| `precompiled/armv7/` | Shipped executable, assets, licenses and integrity manifest |
| `out/` | Ignored local host/ARM build outputs |
| `tests/` | Backend guards and actual LVGL touch/layout integration tests |

The layout is defined directly in `src/ui.c` and `src/views.c`.

The installer at the repository root invokes `touchscreen/installation/install.sh`. The
Python service does not draw frames. Pillow is used for cached image preparation,
not for the rendering loop. Fonts and icons are generated at build time.

## Tools in this folder

| File | Purpose | Output |
| --- | --- | --- |
| `build.sh host` | Install pinned build dependencies, generate assets and build the desktop app | `out/host/`, `assets/generated/` |
| `build.sh arm` | Generate the same assets and cross-compile/package the printer app | `out/arm/`, `precompiled/armv7/` |
| `format.sh` | Check or apply the native C style from this folder | C source formatting |
| `build_assets.py` | Convert the bundled fonts and SVG icons using pinned tools | `assets/generated/` |
| `render_screenshots.py` | Render the user README screenshots from synthetic states and the bundled example part | `screenshots/` |
| `demo_snapshot.py` | Generate synthetic printer states for safe UI previews | A JSON snapshot at the requested path |
| `preview.sh` | Run the native desktop application using a snapshot/scenario | An interactive window; no printer commands |
| `package_arm.py` | Assemble an already-built ARM executable, assets, licenses and revision manifest | `precompiled/armv7/` |
| `package.json`, `package-lock.json` | Pin `lv_font_conv` and `lucide-static` used during asset generation | Installed build dependencies in `node_modules/` |


## Build on a Linux PC

Run commands from the repository root. Required tools are a C compiler, CMake,
Python 3.9 or newer, Node/npm and ImageMagick with SVG support. The interactive
preview also needs X11 headers and a desktop display, including XWayland on a
Wayland desktop. This is a native Linux preview, not a CPU or firmware emulator.

For a Debian/Ubuntu development machine, the relevant packages are:

```sh
sudo apt install build-essential cmake python3 nodejs npm imagemagick libx11-dev clang-format
sh touchscreen/developer-tools/build.sh host
```

The build downloads pinned LVGL 8.4.0 and cJSON 1.7.19 archives with SHA-256
checks, installs the locked asset tools, generates fonts/icons, and produces
`touchscreen/out/host/openklipper-screen`. Source fonts are bundled with their
licences. Node/npm and font tools are not runtime requirements on the printer.

## Source formatting and build configuration

Run `sh touchscreen/developer-tools/format.sh --check` to check C formatting,
or omit `--check` to apply it. The style file lives beside the developer tools.
CMake, its ARM toolchain and `lv_conf.h` live together in `build/`. Build artifacts
are written to `out/host/` and `out/arm/`; do not commit those directories.

## Preview without a printer

```sh
python3 touchscreen/developer-tools/demo_snapshot.py /tmp/screen-demo.json
sh touchscreen/developer-tools/preview.sh /tmp/screen-demo.json
```

Open a particular screen or a transient state directly:

```sh
sh touchscreen/developer-tools/preview.sh --scenario idle
sh touchscreen/developer-tools/preview.sh --scenario printing
sh touchscreen/developer-tools/preview.sh --scenario heating
sh touchscreen/developer-tools/preview.sh --scenario printing --page objects

sh touchscreen/developer-tools/preview.sh --scenario idle --page display
sh touchscreen/developer-tools/preview.sh --scenario idle --page fans
sh touchscreen/developer-tools/preview.sh --scenario heated --page move
sh touchscreen/developer-tools/preview.sh --scenario error
sh touchscreen/developer-tools/preview.sh --scenario loading
sh touchscreen/developer-tools/preview.sh --scenario homing
```

The printing and heating scenarios include three sample objects. Open the jump
button on Home, or use `--page objects`, to preview object selection. The
`heating` scenario shows current temperatures below their targets; `printing`
shows them holding their targets.

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
complete as live operations. Confirmed file/recording deletions remove only
demo list entries, and the time-lapse/CFS switches update demo state; reopening
the preview resets both. It is a UI preview, not a simulated printer backend.
The dimensions and heater limits in the demo generator are synthetic examples,
not defaults applied to a real printer.

For offscreen rendering, without a desktop display:

```sh
touchscreen/out/host/openklipper-screen \
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

The build creates `touchscreen/out/arm/` and assembles the ready-built runtime in
`touchscreen/precompiled/armv7/`. Commit this package with the source so GitHub's
normal source archive contains everything the project installer needs. Users run
the normal installer; they do not download a separate touchscreen bundle.

### Publishing a source change

1. Commit the source/build/runtime input changes.
2. Build the ARM target and assemble the package with `build.sh arm`.
3. Validate it and commit the updated `precompiled/armv7/` files.

```sh
python3 touchscreen/installation/validate_package.py \
  --source touchscreen --package touchscreen/precompiled/armv7
```

The manifest records hashes of runtime/build inputs and all compiled package
files. Source fingerprints are used instead of requiring the package commit to
match its own Git hash: committing compiled files necessarily creates a new
commit. The build-source commit is retained as provenance. Documentation-only
changes do not require rebuilding; changing an input without updating the package
is rejected before installation changes the printer.

The root installer copies startup scripts and the backend into their existing
printer locations. Moving source folders does not change the printer's boot
commands or configuration paths. `installation/install.sh` is an internal step,
not a second user installation procedure.

### External-host release automation

`.github/workflows/host-release-asset.yml` checks out the selected release tag,
installs the ARM cross-compiler, and runs the same `build.sh arm` command before
creating `k2-openklipper-host.zip`. The release packer includes that freshly built
panel runtime and omits the K2-specific Klipper C helper, which the external host
builds itself. The ZIP is uploaded to the GitHub release; it is not deployed to
a testing machine. Earlier tags without native UI keep their original packer.

This is separate from normal printer installation. The printer installer selects
the newest published release tag (or an explicit `OKL_REF`) and downloads its
source archive, which already includes the committed precompiled runtime.
Maintainers must update those committed files for runtime/build source changes.

### Touchscreen-only installation for developers

For isolated screen testing, copy the matching project checkout to an idle
printer and run the internal installer from that checkout:

```sh
sh touchscreen/installation/install.sh /mnt/UDISK/printer_data_mainline/config F008
sh /mnt/UDISK/openklipper-screen/start.sh /mnt/UDISK/printer_data_mainline/config/native-screen.cfg
```

The optional profile argument should match the printer (F008 is only an example).
This updates and restarts the touchscreen/service without reinstalling Klipper.
Use the full project installer for normal printer installations and updates.

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
