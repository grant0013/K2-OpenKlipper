# Creality Filament System protocol notes

These findings describe the CFS connected to the development Creality K2 Plus.
They are sufficient for the current K2 integration, but not yet a portable
hardware specification.

## Transport

The K2 communicates with its closed-loop motors and CFS over `/dev/ttyS5` at
230400 baud. Frames use:

```text
F7 | address | length | payload... | crc8
```

- `length` counts payload bytes plus the CRC byte.
- CRC8 uses polynomial `0x07`, initial value `0x00`, no reflection, and no
  final XOR.
- CRC covers `length + payload`; it excludes the `F7` preamble and address.
- The observed CFS address is `0x01`.
- K2 X and Y motor addresses are `0x81` and `0x82`.

Example motor frames:

```text
f7 81 04 00 11 01 1d
f7 81 04 00 11 02 14
```

## Read commands

Payload bytes exclude the frame preamble, address, length, and CRC.

| Name | Payload | Current interpretation |
| --- | --- | --- |
| `BOX_STATE` | `00 a2` | CFS unit and slot table |
| `MEASURING` | `ff 0a` | selected slot mask and measuring state |
| `BOX_MODE` | `ff 0c` | load/unload operation engine |
| `BUFFER_STATE` | `ff 05` | buffer arm state |
| `REMAIN_LEN` | `ff 03 0f` | remaining filament estimate |
| `FILAMENT_SENSOR` | `ff 08 00` | per-slot filament state |
| `HARDWARE_STATUS` | `ff 15` | raw hardware status |
| `VERSION_SN` | `ff 14` | firmware/version identity data |
| `MEASURING_WHEEL` | `ff 0e 01` | raw measuring-wheel data |

Some decoders are confirmed live; others remain provisional. Check the
comments in `klippy/extras/k2_cfs.py` before relying on a field.

## Slot masks

| Slot | Mask |
| --- | --- |
| A | `0x01` |
| B | `0x02` |
| C | `0x04` |
| D | `0x08` |

## Steady print feed

To arm a selected slot after filament is loaded:

```text
SET_BOX_MODE: ff 04 <slot-mask> 00
TIGHTEN_UP:   ff 0f 00
```

The integration verifies that `MEASURING` reports the requested mask while
`BOX_MODE` reports an idle operation engine. Stock and live traces show that
steady feed does not require `BOX_MODE=PRINTING`; that state is transient
operation activity.

To stop feed and return the CFS to global idle:

```text
TIGHTEN_UP:   ff 0f 01
SET_BOX_MODE: ff 04 00 01
```

## Staged load

The validated K2 sequence uses payload:

```text
ff 10 <slot-mask> <stage> <value>
```

The observed flow is:

1. global idle and tension disabled
2. stage 0
3. stage 4
4. repeated stage 5 while waiting for the nozzle filament sensor
5. hotend extruder forward 5 mm
6. stage 6
7. hotend extruder forward 9 mm
8. stage 7 with value 3
9. arm steady print feed and verify state

The implementation also includes the K2-specific cutter, unload, purge, and
print-start sequence. Those routines contain physical coordinates, the K2
nozzle sensor, and the cutter switch on `!nozzle_mcu:PB9`. They must not be
treated as generic CFS commands.

## What is not yet known

- connector pinout and signalling voltage
- power requirements and safe external power sequencing
- bus termination and topology limits
- address assignment and collision handling
- cold-boot and reset behaviour
- compatibility across CFS hardware and firmware revisions
- whether the protocol can safely coexist with other printer serial stacks

Do not connect a CFS to another controller based only on this document.
Electrical validation is required first.

## Portability plan

The current code should be separated into:

1. generic framing and non-blocking serial transport
2. CFS commands, response decoders, and state machine
3. printer adapter for motion, hotend, cutter, and filament sensors

`k2_motor_bus.xfer()` is synchronous and can wait up to its configured timeout.
It is acceptable for the currently gated commands but unsuitable for frequent
background polling in a general Klipper deployment. A reactor-safe transport
is a priority contribution.

## Status codes, stages and recovery (v0.2.0)

These values come from Creality's CFS module tables, read on the printer, and
from live behaviour.

**Frame status byte:**

| Code | Status |
| --- | --- |
| 0 | OK |
| 1 / 2 / 3 / 4 | PARAMS / CRC / STATE / LENGTH error |
| 5 | EXTRUDE_ERR1 (blocked at the connections) |
| 8-11 | EXTRUDE_ERR4-7 |
| 12 (0x0c) | EXTRUDE_ERR8: transient. Stock retries internally and it has no user message; the open driver re-sends stage 4 up to twice |
| 13 / 14 | EXTRUDE_ERR10 / ERR9 |
| 19 (0x13) | RETRUDE_ERR1 (failed to exit connections) |

**Load stages:** STAGE_1 = 0, 2 = 4, 3 = 5, 4 = 6, 6 = 7.
**Box modes:** IDLE 0, PRELOADING 1, PRINTING 2, WRAPPERING 3, ERR 4, TEST 5.
**Slot masks:** A 1, B 2, C 4, D 8.
**Command timeouts:** extrude 15 s, retrude 150 s, pre-loading 300 s, others
2 s.

**Practical findings:**

- **Pre-loading.** Keep pre-loading on while idle
  (`ff 0d 0f 01` + tension `ff 0f 00`). It takes up spool slack; without it,
  loads failed at stage 0 with ERR1. `CFS_PRELOAD` and the idle hook do this.
- **Stale active mask.** If the nozzle sensor is clear but the box still
  reports an active mask, set the box IDLE before loading.
- **Feed arming.** Print and calibration extrusion must run with the feed
  armed (`SET_BOX_MODE PRINT` + `TIGHTEN_UP ENABLE`, i.e. `CFS_FEED_START
  SLOT=x CONFIRM=1`), then `CFS_FEED_STOP` + `CFS_PRELOAD ENABLE=1` after.
- **Purge tail.** A short retract (1.5 mm) plus a part-fan blast before the
  wipe stops the purge tail from oozing back onto the nozzle.
- **Idle slot change.** `CFS_CHANGE_SLOT SLOT=x CONFIRM=1` and the
  `CFS_LOAD_SLOT` macro load a new slot and purge the old filament while idle.
- **Per-slot records.** `hark_cfs_slots` keeps a record for each slot
  (filament, temperatures, PA, flow, retraction) in `hark_cfs_slots.json`.
  `CFS_SLOT_APPLY` applies it, including `SET_RETRACTION` for firmware
  retraction. The records are mirrored to Moonraker's `lane_data` database
  namespace for OrcaSlicer.
