# Performance and responsiveness

Compared with stock Creality and K2 OpenKlipper with HarkScreen, the native
touchscreen had the lowest screen CPU usage and fastest tap response, both idle
and during printing.

## Measurement approach

These measurements used a Creality K2 Plus with one CFS, stock firmware 1.1.8.0,
K2 OpenKlipper 0.4.25-alpha.1 and native package `248047452a55`. CPU percentages
combine the screen and its backend, excluding Moonraker, Klipper and camera
processes. **100% means one core**, not the whole printer.

Native idle CPU was measured for 60 seconds on Home after settling. Printing
tests used the same synthetic G-code with heated nozzle/bed, X/Y/Z movements
and an empty extruder, with 64 seconds of untouched Home sampling.

Response times cover 20 automated navigation taps, from injected touch-down to
the first visible framebuffer change, sampled about every 20 ms. Physical touch
sensor and LCD scan-out delays are excluded. The observer's own CPU is also
excluded from the table, though it adds load while tapping.

## Performance and responsiveness comparison

Bold values mark the lowest result for each comparison.

| Setup | CPU, idle (no taps / tapping) | Response, idle (median / max) | CPU, printing (no taps / tapping) | Response, printing (median / max) |
| --- | ---: | ---: | ---: | ---: |
| Stock Creality | 28.4% / 35.5% | 0.28 / 0.40 s | 19.6% / 43.5% | 0.30 / 0.44 s |
| K2 OpenKlipper with HarkScreen | 18.4% / 44.2% | 0.45 / 0.96 s | 49.3% / 55.1% | 0.76 / 1.30 s |
| K2 OpenKlipper with native touchscreen | **7.4%** / **22.4%** | **0.19** / **0.24 s** | **9.5%** / **18.2%** | **0.18** / **0.20 s** |

## Moonraker load

Moonraker used more CPU with native during idle navigation and less than
HarkScreen during printing. These figures measure the Moonraker process alone,
separately from the screen/backend figures above.

| Condition | HarkScreen | Native touchscreen |
| --- | ---: | ---: |
| Idle, no taps | **11.7%** | 12.6% |
| Idle, tapping | **11.3%** | 17.4% |
| Printing, no taps | 19.1% | **15.7%** |
| Printing, tapping | 21.5% | **16.6%** |

## Backend updates and caching

The backend receives live status and G-code responses through WebSocket
subscriptions. Move and Tune request their values only when needed; returning
to a page refreshes its values. During printing, Move is unavailable and its
coordinate subscriptions stay off, which likely explains the lower tapping CPU.

CFS startup reads discover fields added during initialization, then renew the
subscription. Opening Filament refreshes CFS; recurring CFS-only reads run only
while that tab is open and the printer is idle. HTTP polling remains a connection
fallback. Configuration and system information have separate background refreshes.

Screen updates are event-driven. Changed sections are sent without repeating the
whole state; unchanged derived data and file lists are reused. Configuration,
time zones and the material catalogue use cached files, and unchanged labels do
not redraw. Preparing cached configuration, limits and the file list during
startup briefly raised screen CPU to 73.2% of one core in a one-second sample.
