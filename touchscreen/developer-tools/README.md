# Native touchscreen developer guide

The touchscreen is a native C11/LVGL application with a Python backend. The same
C sources render the printer panel, interactive Linux desktop preview and
headless screenshots. This folder contains development tools; the printer uses
the files in `runtime/`, `backend/` and the committed ARM package.

Run the commands below from the repository root. Keep local builds and generated
snapshots in `touchscreen/out/`, which is ignored by Git. Printer-specific hotend,
extruder, cutter and Z-reference changes belong in printer configuration or a
separate integration branch.

## Architecture and source map

```text
Touch / mouse -> C + LVGL UI -> typed JSON over private Unix socket
                               -> Python service -> Moonraker -> Klipper macros
                                                 -> local platform services
```

The UI does not implement motion, heating or CFS loading sequences. The backend
validates requests against current state and configuration, reserves resources
and calls the existing firmware functions. See the [control contract](CONTROL-CONTRACT.md)
for availability, operations, widget bindings and extension rules.

Paths in this table are relative to `touchscreen/`:

| Path | Responsibility |
| --- | --- |
| `src/main.c` | Startup, event loop, fixture actions and screenshot arguments |
| `src/platform.c` | Framebuffer/touch, X11, socket transport and screen capture |
| `src/ui.c` | Shared controls, navigation, dialogs, fixture-only updates |
| `src/views.c` | Page layouts, keyboard, image fitting and UI actions |
| `src/view_inputs.c` | Snapshot dependencies; live updates without unnecessary page rebuilds |
| `src/json.c`, `src/screen.h` | JSON helpers, UI state and shared layout constants |
| `backend/action_policy.py` | Action declarations, resource ownership and availability |
| `backend/service.py` | Moonraker connection, validation, operations and thumbnail loading |
| `backend/model.py` | Configuration-derived limits, capabilities and filament compatibility |
| `backend/platform_services.py` | Wi-Fi and printer-local platform integration |
| `backend/storage.py` | Cached image preparation, files, USB export and time-zone helpers |
| `assets/` | Production icon SVGs, source fonts and licenses |
| `assets/generated/` | Generated production PNGs/font C files; ignored by Git |
| `build/` | CMake, ARM toolchain and LVGL configuration |
| `runtime/` | Startup scripts and example display configuration |
| `installation/` | Installer, package validation and preference migration |
| `precompiled/armv7/` | Committed printer executable, assets, licenses and manifest |
| `developer-tools/fixtures/` | Portable desktop cases and bundled demo artwork |
| `tests/` | Backend and actual native UI regression checks |
| `out/` | Ignored build, preview and diagnostic outputs |

Pillow prepares images in the backend worker; it does not draw UI frames.
Production layout is defined in the C source, not a separate layout document.

## Build the desktop application

Required tools: C compiler, CMake 3.18+, Python 3.10+, Pillow, Node/npm,
ImageMagick's `convert` with SVG support, and X11 development headers. Interactive
windows require a desktop display, including XWayland on a Wayland desktop.
Headless rendering does not need a display server.

On Debian/Ubuntu:

```sh
sudo apt install build-essential cmake python3 python3-pil nodejs npm imagemagick libx11-dev clang-format
sh touchscreen/developer-tools/build.sh host
```

The build fetches pinned, hash-checked LVGL 8.4.0/cJSON 1.7.19 sources, installs
locked asset tools, generates fonts/icons and creates
`touchscreen/out/host/openklipper-screen`. Re-run the build after C or production
asset changes. This is a native desktop preview, not a firmware/CPU emulator.

## Run the bundled PC cases

All assets and case definitions are in [fixtures/](fixtures/README.md). No files
from a developer's home directory or earlier preview outputs are needed.

```sh
sh touchscreen/developer-tools/preview.sh --case idle
sh touchscreen/developer-tools/preview.sh --case printing
sh touchscreen/developer-tools/preview.sh --case heating
sh touchscreen/developer-tools/preview.sh --case multi-cfs
```

| Case | What to check |
| --- | --- |
| `idle` | Printer illustration, one CFS, light/fan and heater controls |
| `printing` | Holding target temperatures, progress and three print objects |
| `heating` | Current-to-target arrows while heating |
| `multi-cfs` | Three unit choices, twelve spools and mixed PLA/PETG/ASA |
| `file` | Large padded preview, metadata and calibration checkbox |
| `mapping` | Three file filaments mapped across CFS units; aligned colors/materials |
| `objects` | Selectable outlines, zoom and skip confirmation |
| `empty-extruder` | Spools present without a nozzle-loaded marker |
| `no-cfs` | Printer-only illustration and no CFS shortcut |
| `wifi-password` | Full-screen password keyboard, eye control and `?123`/`ABC` |
| `wifi-connected`, `wifi-failed` | Connection result notices |
| `homing`, `error` | Activity indicator and printer fault dialog |

List cases or open another page using the same state:

```sh
sh touchscreen/developer-tools/preview.sh --list
sh touchscreen/developer-tools/preview.sh --case multi-cfs --page file
sh touchscreen/developer-tools/preview.sh --case idle --page fans
sh touchscreen/developer-tools/preview.sh --case idle --page restart
```

The native window is 800 × 480; mouse presses represent touches. `?123` opens one
numbers/punctuation page, `ABC` returns to letters, and the capital arrow changes
case. The password field masks input by default. Wi-Fi success follows association
and address assignment; the preview uses synthetic responses.

Fixture mode never connects to the printer. Navigation, dialogs, mapping and
object selection run locally. Confirmed file/recording deletions and timelapse/CFS
switches update only demo state; restarting resets it. Motion, heating, actual
Wi-Fi changes, exports and other firmware operations are not simulated to completion.
The sample printer settings are not configuration to apply to real hardware.

### Custom states and saved snapshots

Existing `--scenario` commands still work. Scenarios provide raw generated state;
cases additionally select artwork, an opening page and optional click actions.
Useful additional scenarios include `heated`, `load`, `unload`, `meshing`,
`notice`, `recovery` and `disconnected`.

```sh
sh touchscreen/developer-tools/preview.sh --scenario heated --page move
sh touchscreen/developer-tools/preview.sh --scenario disconnected --page move
python3 touchscreen/developer-tools/demo_snapshot.py \
  touchscreen/out/preview/idle.json --case idle
sh touchscreen/developer-tools/preview.sh touchscreen/out/preview/idle.json
```

Generated snapshots contain paths for the current checkout. Commit portable case
recipes and their assets, rather than generated snapshots. To add a new state or
workflow, follow the [fixture instructions](fixtures/README.md).

### Headless rendering

The same launcher can write a PPM screenshot instead of opening a window:

```sh
sh touchscreen/developer-tools/preview.sh --case mapping \
  --snapshot touchscreen/out/preview/mapping.ppm
convert touchscreen/out/preview/mapping.ppm touchscreen/out/preview/mapping.png
```

For geometry and recorded requests, use the native binary directly:

```sh
touchscreen/out/host/openklipper-screen \
  --assets touchscreen/assets/generated \
  --fixture touchscreen/out/preview/idle.json --page home \
  --snapshot touchscreen/out/preview/home.ppm \
  --geometry touchscreen/out/preview/home-geometry.json \
  --record-commands touchscreen/out/preview/commands.jsonl
```

Fixture action sequences are described in `tests/test_native_ui.py`. Bundled
case actions are applied automatically by the launcher, including password entry
and filament-mapping workflows.

## Shared UI patterns

- Use `row()` for navigation/information cards and `toggle_card()` for binary
  preferences. Settings card gaps come from `SETTINGS_CARD_GAP` and
  `SETTINGS_ROW_PITCH`. Footer actions use spacing rather than separator rules.
- Use `image_fit()` for previews. Cached thumbnails preserve proportions, remove
  transparent margins, and leave a small UI inset. Missing/unreadable list images
  fall back to the file icon.
- A CFS spool being present is different from filament reaching the extruder.
  The nozzle marker requires the extruder sensor and the reported active source.
- Mapping places the file/source captions above aligned swatches and material
  names. Material mismatches remain disabled and are rechecked by the backend.
- The keyboard sets single-line mode before its final size/padding. Keep the
  text and eye control centered, and retain all printable password characters.

Controls still use the shared action-policy bindings; a different visual style
must not bypass availability, current-file protection or confirmation checks.

## Tools and generated files

| Tool | Purpose |
| --- | --- |
| `build.sh host\|arm` | Install pinned asset tools and build the chosen target; ARM also packages it |
| `format.sh [--check]` | Apply/check the C style in `.clang-format` |
| `build_assets.py` | Generate production icons and LVGL font sources |
| `build_fixtures.py` | Rebuild committed preview PNGs from the bundled SVG artwork |
| `demo_snapshot.py` | Materialize a case/scenario, or list available cases |
| `preview.sh` | Interactive or headless native preview |
| `render_screenshots.py` | Regenerate the PNGs referenced by the user README |
| `package_arm.py` | Package an already-built ARM runtime and record its provenance |
| `package.json`, `package-lock.json` | Pin the asset-generation dependencies |

```sh
sh touchscreen/developer-tools/format.sh --check
python3 touchscreen/developer-tools/build_fixtures.py
python3 touchscreen/developer-tools/render_screenshots.py
```

Commit fixture artwork and the user-facing screenshots after reviewing the
rendered output. Do not commit `out/`, `node_modules/` or `assets/generated/`.

## Tests and diagnostics

Build the host binary before testing; native UI tests otherwise skip themselves.
The suite uses mocks/fixtures and does not operate a printer.

```sh
python3 -m unittest discover -s touchscreen/tests -v
```

To check native memory/undefined-behavior issues in a separate build:

```sh
cmake -S touchscreen/build -B touchscreen/out/asan \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer" \
  -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined"
cmake --build touchscreen/out/asan --parallel 4
ASAN_OPTIONS=detect_leaks=0 SCREEN_BINARY="$PWD/touchscreen/out/asan/openklipper-screen" \
  python3 -m unittest discover -s touchscreen/tests -v
```

On the printer, the socket is `/tmp/openklipper-screen.sock`. Logs are
`/tmp/openklipper-screen-service.log` and `/tmp/openklipper-screen.log`; matching
PID files use the same base names. Display configuration is normally
`native-screen.cfg`, with preferences in `native-screen-settings.json` beside it.
Desktop checks do not replace real installation and hardware acceptance testing.

## Maintain the precompiled printer package

Normal users run the project installer. Its selected release source archive
already contains `precompiled/armv7/`; no separate touchscreen download or printer
compilation is needed. The root installer invokes `installation/install.sh`.

After committing runtime/build source changes:

```sh
sudo apt install gcc-arm-linux-gnueabihf
sh touchscreen/developer-tools/build.sh arm
python3 touchscreen/installation/validate_package.py \
  --source touchscreen --package touchscreen/precompiled/armv7
```

Commit the updated package as a second commit. Its manifest hashes runtime/build
inputs and package files, and records the build-source commit as provenance.
Uncommitted source inputs prevent packaging; stale packages prevent installation.
Documentation, fixture recipes and test-only changes do not require a new binary.

The target is statically linked ARMv7/NEON/hard-float. Confirm architecture support
before using it on another controller. The printer needs no X11, Node/npm or
font-generation tools.

`.github/workflows/host-release-asset.yml` separately checks out a release tag,
builds the ARM runtime and creates `k2-openklipper-host.zip`. The packer includes
the compiled screen and omits the K2-specific Klipper C helper, which an external
host builds locally. It uploads the ZIP to the GitHub release, not a test machine.
Normal printer installation downloads the release-tag source archive, not this ZIP.

### Isolated touchscreen testing on a printer

For developer-only screen testing, copy the matching checkout to an idle printer
and run from that checkout. Replace the example configuration path/profile with
the printer's actual values:

```sh
sh touchscreen/installation/install.sh /mnt/UDISK/printer_data_mainline/config F008
sh /mnt/UDISK/openklipper-screen/start.sh /mnt/UDISK/printer_data_mainline/config/native-screen.cfg
```

This installs the screen/service without reinstalling Klipper. Use the full
project installer for normal installations and complete-system acceptance tests.
