# Creality Filament System protocol notes

Portions adapted from Jacob10383's K2 Plus custom firmware
(https://github.com/Jacob10383/k2-plus-custom-firmware, extras/box.py and
extras/box_protocol.py), GPLv3.

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
| `MEASURING` | `ff 0a` | temperature, humidity, box state, slot masks |
| `BOX_MODE` | `ff 0c` | load/unload operation engine |
| `BUFFER_STATE` | `ff 05` | buffer arm state |
| `REMAIN_LEN` | `ff 03 0f` | RFID remaining % per slot |
| `FILAMENT_SENSOR` | `ff 08 00` | per-slot filament state |
| (hub sensor) | `ff 08 01` | slot whose filament is in the hub |
| `HARDWARE_STATUS` | `ff 15` | raw hardware status |
| `VERSION_SN` | `ff 14` | firmware/version identity data |
| `MEASURING_WHEEL` | `ff 0e 01` | hub wheel, big-endian float mm |
| (RFID records) | `ff 02 <mask>` | ASCII `A:<40 chars>;B:...;` |

Some decoders are confirmed live; others remain provisional. Check the
comments in `klippy/extras/k2_cfs.py` before relying on a field.

Reply payloads start with a status byte, then the echoed command byte
(`f7 addr len <status> <cmd> <data...> crc`). The status table is below.

### MEASURING (`ff 0a`) reply

Jacob10383's K2 Plus firmware (`extras/box_protocol.py`, GPLv3) decodes this
reply more fully than we had; the open driver now follows his reading:

| Status | Data | Meaning |
| --- | --- | --- |
| `00` | `temp rh ?? state mask setting` | normal reply. `temp` is signed C, `rh` %, `state` the box state (0 idle, 1 preload, 2 print, 3 reload, 4 error, 5 test), `mask` the slot in the path (`active_mask`), `setting` a second slot mask |
| `50` | same as `00` | RUNOUT: the box's own runout flag, with state 2 |
| `30` | `e0 e1 e2 e3` | SLOT_EVENT: one byte per slot, 1 inserted, 2 removed, 3 RFID record ready. Read again for the normal reply (events queue) |
| `01` / `02` | none | invalid / bad CRC |

Before 10-27 the driver read a `30` reply's event bytes as temperature and
humidity. `box_state`, `box_runout` and `last_slot_event` are now in the
`k2_cfs` status. The box reports `active_mask` 0 while it just follows the
extruder; the driver then accepts the slot it armed (`feed_slot`) when the
nozzle sensor sees filament and the hub sensor (`ff 08 01`) shows that slot
(`path_owner_from_feed_slot`).

### Unclear items (alternative readings, behaviour unchanged)

- **`ff 0d`.** We send `ff 0d 0f 01` / `ff 0d 0f 00` as stock's
  SET_PRE_LOADING on/off (mask, value). Jacob10383 sends `ff 0d 00 <en>` as
  "read RFID on insertion" on/off and `ff 0d <mask> 02` as a forced RFID read
  that spins the slot (up to 190 s per slot). Both may be modes of one
  command (byte 3: 0 off, 1 on, 2 force read; byte 2 a slot mask). The
  forced read is not used here.
- **`ff 05` buffer value.** We read 0 slack, 1 mid, 2 tensioned (seen moving
  the arm by hand). Jacob10383 reads two sensor bits: 0 middle, 1 full,
  2 empty, 3 fault. The two agree only on 0 being the middle of travel.
- **`00 a2`.** We call it BOX_STATE. Jacob10383 calls it AUTO_QUERY, part of
  the address protocol (`a0` assign, `a1` discover, `a2` query): the reply
  is device type (1 = CFS), mode (0 app, 1 loader) and the 12-byte UniID,
  which matches our `flags` and `uniid` fields.
- **Multiple boxes.** His firmware addresses up to four boxes (1-4) through
  `a0`/`a1`. This printer has one box at address 1 and the driver keeps that.

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

Creality names come from the stock module tables; the second name and the
meaning are Jacob10383's (`box_protocol.py`). Error messages now print both
and the plain-English text.

| Code | Creality / Jacob10383 | Meaning, handling |
| --- | --- | --- |
| 0x00 | OK | |
| 0x01 / 0x02 | PARAMS / CRC | invalid command / corrupted frame |
| 0x03 | STATE / BUSY | box finishing another operation: retried `busy_retries` (2) times, `busy_retry_delay` (1 s) apart |
| 0x04 | LENGTH | |
| 0x05 | EXTRUDE_ERR1 / STAGE0_SENSOR_TIMEOUT | filament never reached the hub sensor in 7 s (blocked at the connections) |
| 0x08 | EXTRUDE_ERR4 / SLOT_EMPTY | no filament at the slot inlet |
| 0x09 | EXTRUDE_ERR5 / STAGE0_ODOMETER_TIMEOUT | hub wheel did not turn for 2 s |
| 0x0a | EXTRUDE_ERR6 / FEED_TIMEOUT | 25 s feed without reaching the head |
| 0x0b | EXTRUDE_ERR7 / OVERTRAVEL | fed 3 m without reaching the head |
| 0x0c | EXTRUDE_ERR8 / ODOMETER_STALLED | hub wheel stopped while feeding. Stock retries internally with no user message. Nozzle sensor checked first; stage 4 re-sent up to twice; with `stall_retry_motion` the first retry sweeps the toolhead first |
| 0x0d | EXTRUDE_ERR10 / BUFFER_NOT_FULL | buffer not full 3 s into feeding |
| 0x0e | EXTRUDE_ERR9 / STAGE7_NO_MOTION | final nudge moved under 1.5 mm: success when the nozzle sensor sees filament |
| 0x13 | RETRUDE_ERR1 / UNLOAD_BUFFER_TIMEOUT | failed to exit connections: the buffer never released on a pull-back (jam) |
| 0x14 | UNLOAD_HUB_PE_TIMEOUT | hub path never cleared |
| 0x16 / 0x17 / 0x18 | UNLOAD_NO_FILAMENT / INLET_CLEAR / ALL_EMPTY | nothing to unload |
| 0x19 | UNLOAD_MOTOR_BLOCKED | hub motor stalled pulling back. With `stall_retry_motion`, sweep and retry the rewind once |
| 0x1a | UNLOAD_ODOMETER_TIMEOUT | 40 s pull-back without clearing |
| 0x30 | SLOT_EVENT | see MEASURING |
| 0x50 | RUNOUT | see MEASURING |
| 0x51 / 0x52 | BUFFER_REFILL_STALLED / NO_MOTION | buffer refill not moving |

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

## Cutting and colour changes (v0.3 work, 10-26)

Measured on F021 with a Phaetus DXC2 extruder, over 23 changes of a two-colour
print:

| | Before | After |
| --- | --- | --- |
| Cut | X-7.6, ram acceleration 3000 | **X-8.0, acceleration 5000** (Creality's own cut acceleration) |
| CFS rewind (RETRUDE FINISH) | 24-25 s | **15 s average, 17 s max** |
| Whole change | 113-131 s (one 211 s) | **101 s average** |

- **Incomplete cuts.** At the old settings the knife only notched the
  filament. The DXC2's 70 mm of post-cut pulls, with the CFS rewinding at the
  same time, then snapped worn filament near the end of a reel and left a
  loose piece under the nozzle filament sensor. Three prints stopped with
  "nozzle sensor still detects filament". The rewind time is a good cut
  health signal: about 13-17 s for a clean cut, 24 s or more for a dragged one.
- **Auto-clear.** If the sensor still sees filament after an unload, the
  module pushes up to 3 x 30 mm through the hot nozzle over the chute and
  carries on (`unload_clear_tries`, 0 = stop the print as before).
- **Knife sensor.** PB9 never changed with the DXC2, not during cuts or slow
  pushes to X-11. Stock firmware only uses it as a knife-at-rest check before
  and after the cut; stock's `cut_pos_x` comes from a closed-loop motor check
  (`MOTOR_CHECK_CUT_POS`), not this sensor.
- **Temperature wait.** The tool change waited with `M109`, which holds until
  the PID settles within 1 °C: up to 106 s at an unchanged temperature. It now
  waits until the nozzle is within `toolchange_temp_margin` (3 °C).
- **Flush length.** Same-material changes purge the slicer's per-change flush
  (`{flush_length}` from the flush volumes matrix), clamped to 30-240 mm,
  instead of a fixed 70 mm. The purge runs as Creality's flush blocks (see
  `docs/START-SEQUENCE.md`).
- Travel to the chute and cutter runs at Creality's speeds (300 / 200 mm/s).

## Ported from Jacob10383's K2 Plus firmware (10-27)

Options in `[k2_cfs]`; defaults in brackets.

- **Runout refill purge** (`runout_purge_length` [40], 0 = full purge). Both
  slots hold the same material and colour, so the refill purges 40 mm SHORT
  instead of the full 160-180 mm. His runout primes 20 mm.
- **Load arrival** (`load_arrival_poll_interval` [0.1]). The nozzle sensor
  is watched between stage-5 sends instead of once per send (up to 2 s
  late with `load_poll_interval: 2`). The stage-5 bus rate is unchanged.
- **Stall retry motion** (`stall_retry_motion` [off],
  `stall_retry_moves` [`Y255, X220, Y40, X40`]). His
  `filament_retry_motion` sweeps the head (K2 Plus: Y350 X300 Y50 X50) to
  free the PTFE tube; ours is scaled to the F021 bed, starts and ends at the
  chute and stays in front of `purge_safe_y` at the current Z.
- **Flush matrix purge** (`toolchange_purge_from_file` [off]). Reads
  `flush_volumes_matrix` (and `flush_multiplier`) from the end of the print
  file when the slicer passes no `PURGE`: volume / filament area. Like
  materials: SHORT purge, 30-240 mm. Otherwise the stock purge, longer if
  the file asks. T0-T3 index the matrix as slots A-D.
- **Clog check** (`clog_detection` [off], `clog_extruder_length` [80],
  `clog_wheel_reset_length` [18], `print_poll_interval` [0 = 10 s]). Pauses
  when the extruder feeds 80 mm while the hub wheel moves under 18 mm (the
  box refills the buffer in ~25-30 mm chunks). Skipped while a slot is
  running out.
- **`CFS_RFID_READ [SLOT=x]`** reads the cached RFID records and remaining %
  (no motion).
