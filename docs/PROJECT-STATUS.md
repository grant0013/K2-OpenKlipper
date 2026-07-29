# Project status

Status at the `v0.1.0-alpha.1` developer preview.

## What works on the development K2 Plus

| Area | Current result |
| --- | --- |
| Upstream Klipper host | Running the pinned commit in the build manifest |
| X/Y homing | Live validated with K2 closed-loop motor protection |
| Z homing | Live validated through PRTouch PA15-to-PC7 hardware endstop |
| Nozzle cleaning | Live validated on the K2 rear silicone cleaner |
| Adaptive mesh | Live validated |
| Post-mesh purge park | Live validated |
| CFS read-only status | Live validated |
| CFS initial load and purge | Live validated |
| Orca upload and start | Live validated through Moonraker on port 7126 |
| Mainsail | Running on port 4409 with active config exposed |
| Cancel safety | Heaters, fans, CFS feed, transforms, and steppers are cleared |
| Print duration | Benchy passed 32% at the last saved observation |

## Alpha limitations

- A full long-duration print has not yet completed on v62.
- Testing covers one K2 Plus and one known CFS firmware combination.
- The stock touchscreen control plane is unavailable while Creality
  `master-server` is disabled.
- Klipper and Moonraker self-update are disabled because Creality's packaged
  appliance lacks the usual Git metadata, requirements, and build toolchain.
- The UI's duration accounting includes startup and CFS work incorrectly.
- Recovery, power-loss, runout, multi-colour changes, and every cancel phase
  still need structured testing.

## Release gates

### Next alpha

- complete a full Benchy and a longer functional print
- capture restart, cancel, filament-runout, and CFS error recovery
- clean up active versus fallback PRTouch configuration
- add replayable protocol fixtures from sanitised logs
- remove synchronous serial reads from any recurring CFS path

### Beta

- validate on multiple K2 Plus units and firmware revisions
- publish CFS electrical interface findings
- split CFS transport, device state, and K2 motion adapters
- document a recovery-tested install and rollback flow
- add automated configuration and protocol checks

### Stable

- multiple completed long prints without timing faults
- validated failure handling for heaters, runout, CFS jams, and MCU restarts
- repeatable PRTouch results across nozzle and bed conditions
- release artifacts reproducible from source

## Evidence standard

When reporting a result, include:

1. printer model and hardware revision if known
2. Creality and CFS firmware versions
3. repository commit and configuration changes
4. exact command or sliced file phase
5. relevant, sanitised Klipper and Moonraker log excerpts
6. whether motion, heaters, CFS, and recovery were physically supervised

Never publish tokens, passwords, Wi-Fi details, device serial numbers, or
private network information.
