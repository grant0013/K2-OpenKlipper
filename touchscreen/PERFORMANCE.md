# Performance and responsiveness

The native screen responded fastest both while idle and during printing. Its CPU
usage changed little during printing: without taps it was close to stock Creality,
and while tapping it had the lowest load. HarkScreen used the least CPU while idle
without taps, but nearly three times as much during printing.

These measurements used a Creality K2 Plus with one CFS, stock firmware 1.1.8.0,
K2 OpenKlipper 0.4.25-alpha.1 and native package `abc5ce26700b`. CPU percentages
combine the screen and its backend, excluding Moonraker, Klipper and camera
processes. **100% means one core**, not the whole printer.

Idle CPU was measured for 20 seconds on Home after settling. Printing tests used
the same synthetic G-code with heated nozzle/bed, X/Y/Z movements and an empty
extruder. Navigation began at layer 1; the native run then supplied 64 seconds
of untouched Home sampling. Mainsail and live camera were open for the
OpenKlipper printing tests; stock used the Creality app camera.

Response times cover 20 automated navigation taps, from injected touch-down to
the first visible framebuffer change, sampled about every 20 ms. Physical touch
sensor and LCD scan-out delays are excluded. The observer's own CPU is also
excluded from the table, though it adds load (about 5–6% of one core while tapping).
Bold values mark the lowest result for each comparison.

| Setup | CPU, idle (no taps / tapping) | Response, idle (median / max) | CPU, printing (no taps / tapping) | Response, printing (median / max) |
| --- | ---: | ---: | ---: | ---: |
| Stock Creality | 28.4% / 35.5% | 0.28 / 0.40 s | **19.6%** / 43.5% | 0.30 / 0.44 s |
| K2 OpenKlipper with HarkScreen | **18.4%** / 44.2% | 0.45 / 0.96 s | 49.3% / 55.1% | 0.76 / 1.30 s |
| K2 OpenKlipper with native touchscreen | 19.8% / **29.5%** | **0.18** / **0.23 s** | 21.0% / **29.6%** | **0.19** / **0.21 s** |

## Moonraker load

Moonraker used slightly more CPU with native while idle, but about a fifth less
than with HarkScreen during printing. These are measurements of the Moonraker
process alone, separate from the screen/backend figures above.

| Condition | HarkScreen | Native touchscreen |
| --- | ---: | ---: |
| Idle, no taps | **11.7%** | 12.1% |
| Idle, tapping | **11.3%** | 12.7% |
| Printing, no taps | 19.1% | **15.6%** |
| Printing, tapping | 21.5% | **17.0%** |

The backend receives live status and G-code responses through WebSocket
subscriptions. CFS startup reads discover fields that appear during initialization,
then renew the subscription. Opening Filament performs one CFS refresh; recurring
CFS-only reads run while that tab is open and the printer is idle. They stop during
printing, on leaving the tab, or when no CFS is fitted. Full-status polling is a
connection/reconnection fallback. Configuration and system information still have
separate background refreshes.

Changed screen sections are sent without repeating the whole state. Configuration,
time zones and the material catalogue use separate cached files, and unchanged
labels do not redraw. A clean ARM rebuild of this package was byte-for-byte
identical using the same toolchain and dependency sources.
