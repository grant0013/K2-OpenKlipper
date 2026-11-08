# Creality Filament System protocol notes

Portions adapted from Jacob10383's K2 Plus custom firmware
(https://github.com/Jacob10383/k2-plus-custom-firmware, extras/box.py and
extras/box_protocol.py), GPLv3. Addressing, bootloader and flashing notes
cross-checked against Lamar1007's CFSTool
(https://github.com/Lamar1007/CFSTool, PROTOCOL.md and cfstool.py; GitHub
lists it as MIT, its author describes it as GPLv3), an independent
reverse-engineering pass from the K1 side, and his notes in Discussion #1
(5 Nov 2026). He compared live K2 payloads with a K1 dump (`box_wrapper`):
every command byte matches, so the K1 and K2 run the same CFS command layer.

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
| `VERSION_SN` | `ff 14` | three ASCII version digits then the serial (`150...` = 1.5.0); CFSTool decodes it the same way |
| `MEASURING_WHEEL` | `ff 0e 01` | hub wheel, big-endian float mm |
| (RFID records) | `ff 02 <mask>` | ASCII `A:<40 chars>;B:...;` |
| `COMMUNICATION_TEST` | `ff 55` | plain acknowledge (CFSTool); not used by the driver |

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
  which matches our `flags` and `uniid` fields. Lamar1007's two-box test on
  a K1 settled the first byte: boxes `0x01` and `0x02` both answer
  `00 a2 01 00 <uniid>` to `ff a2`, so it is a device type, not a count.
  Each box reports only itself, and `ff 14` gives each box's own version
  and serial. `ff a2` to `0xfe` is not a safe query: addressed boxes gave no
  reply, and two unaddressed boxes collided into one garbled frame with a
  bad CRC.
- **Multiple boxes.** His firmware addresses up to four boxes (1-4) through
  `a0`/`a1`, and Lamar1007 confirms it from the K1 side: boxes chain on the
  bus at `0x01`-`0x04`, Creality's `auto_addr` assigns them at startup, and
  Creality numbers the slots T1A-T4D, i.e. T1-T16. This printer has one box
  at address 1 and the driver keeps that.

## Addressing and cold boot

The box keeps no address over a power-off and boots either its
application (app mode) or its loader. `address_cfs()` in
`klippy/extras/k2_motor_bus.py` runs at every Klipper start (`cfs_detect`),
following Creality's `auto_addr` and Jacob10383's AutoAddressClient; CFSTool
found the same frames independently on a K1.

| Step | Address | Payload | Meaning |
| --- | --- | --- | --- |
| query | `0x01` | `00 a2` | answers `00 a2 01 <mode> <uniid>` when addressed; mode 0 app, 1 loader |
| start app | `0xff` | `00 0b 01` | loader to application; it answers about 2 s later. Sent when the query reports mode 1 (a box powered up while the printer runs) |
| discover | `0xfe` | `00 a1 fe fe` | an unaddressed box answers with its 12-byte UniID |
| assign | `0xfe` | `00 a0 01 <uniid>` | gives that box address 1 |

- The address family (`a0`/`a1`/`a2`) goes out with first byte `00` and
  works in app mode as well as in the loader. CFSTool's notes tie `00` to the
  loader and `ff` to app mode; that holds for the other commands, where the
  wrong one gets no answer.
- `cfs_uniid` in `[k2_motor_bus]` is tried before the discover broadcast;
  it is not needed.
- Only one box is handled. Up to four, chained at addresses `0x01`-`0x04`,
  is how Creality, Jacob10383's firmware and the K1 side use `a0`/`a1`;
  Creality's slot names run T1A-T4D (T1-T16) with the usual A-D masks in
  each box.
- Several unaddressed boxes do not collide on the discover. On a K1 with two
  boxes (Lamar1007), `00 a1 fe fe` to `0xfe` returned two clean frames, one
  after the other. Both are `00 a1 01 00 <uniid>`, and the driver here still
  reads only the first.

## Stock K1 filament change (Lamar1007, K1, not checked on a K2)

Read from Creality's K1 `box_wrapper` log, 116 real colour changes with box 2
at `0x02` on CFS 1.5.2/1.5.3. Full write-up:
[PROTOCOL.md, "Filament change (stock K1 sequence)"](https://github.com/Lamar1007/CFSTool/blob/main/PROTOCOL.md).
One change, slot C to slot D, about 70 s:

| Step | Frames | Notes |
| --- | --- | --- |
| Cut | none | printer only |
| Unload C | `ff 04 00 01` | no slot active |
| | `ff 11 04 00` | prepare, about 1 s, then the extruder retracts 20 mm |
| | `ff 11 04 01` | the box winds it back; the reply comes when done, about 30 s |
| Load D | `ff 08 00` to `0f` | |
| | `ff 10 08 00 00` | stage 0, reply after about 10 s |
| | `ff 10 08 04 00` | |
| | `ff 10 08 05 00` | polled every 2 s until the printer sees filament at the head; the reply is the measuring wheel |
| | `ff 10 08 06 00` | stop. Creality's log calls this "stage7", on the bus it is stage 06 |
| Finish | `ff 05` | buffer, `00` = middle |
| | `ff 04 08 00` | slot D printing |
| Purge | none | printer only, flush matrix x 0.7 |

- The measuring wheel (`0x0e` and `0x10` stage 5) is a big-endian float, for
  example `c5 e9 9b 73` = -7475.43, the same value `box_wrapper` logs. The
  unit is not confirmed.
- In the `0x0a` box state, byte 1 is the temperature in C and byte 2 the
  humidity in %.
- The loader-to-application broadcast `f7 ff 04 00 0b 01 c8` only affects a
  box in the loader. With two boxes on the bus, the box in the loader
  answered `0xa1` from its application 3 s later and the other was untouched.
  It is Creality's own `CMD_LOADER_TO_APP`, sent about once a second (every
  10 s while printing) when a reply marks a box as mode 1. Whether it also
  starts a freshly flashed application is still open.
- Creality's K1 address scheme: type 1 box `0x01`-`0x04` (type broadcast
  `0xfe`), type 2 closed-loop motor `0x81`-`0x84` (`0xfd`), type 3
  belt-tension motor `0x91`/`0x92` (`0xfc`), `0xff` everything. UUIDs are
  saved in `printer.cfg` (`[auto_addr] mb_addr_table_uniids`) and re-assigned
  with `0xa0` at start-up without an enumerate, so box N stays the same box.
  `0xa3` is `CMD_GET_ADDR_TABLE`.

## Bootloader and flashing (CFSTool, K1; not used here)

This driver never flashes the CFS. CFSTool (MIT) flashes it on K1 printers,
verified on real hardware from 1.1.3 to 1.5.0. Summary of its notes:

- Enter the loader with the broadcast `F7 EB 03 FF 56 CF` (address `0xeb`,
  command `56`). Re-discover and re-assign it (`a1`, `a0` as above) after:
  the loader does not keep the app address.
- Loader commands are `00 f0 <sub> ...` at address 1: `00` version, `03`
  sector size, `06` erase the application (the loader survives, so a broken
  flash can be repeated), `01` request update, then the whole file length
  (4 bytes, little-endian), the file in chunks of at most 252 bytes, and
  `02` to start the application. Each step is acknowledged; up to 3 tries.
- After a flash the box does not answer on the bus until its power is cut
  (two users on K1, and Lamar1007: it does not re-enumerate on a soft
  reset, so it looks dead until power-cycled).
- One box can enter the loader on its own: `f7 02 03 ff 56 cf` to box `0x02`
  acknowledges with `00 56`, and box `0x01` stays in app mode. In the loader
  the box has lost its address, and `a0` with its UniID (read with `a2` in
  app mode) gets it back without `a1`, answering `00 a0 01 01 <uniid>` (mode
  1). Version and sector reads work on that path.
- **Do not flash on that path.** Every step acknowledged like a good flash,
  but the loader then reported the version as `cfs0_050_G32-` and zeros, and
  the box stayed in the loader even after a power cycle (Lamar1007, known-good
  1.5.2). The same files flashed correctly with the broadcast `0xeb`, `a1`,
  `a0` path, so use that one. He suspects `a1` in the loader arms the write
  session; that is untested.
- Soft starts from the loader did not work on a K1: `00 0b 01` sent to the
  box's address got no reply, `00 f0 02` with no flash left it in the loader,
  and the loader did not time out in 120 s. The `0xff` broadcast of `00 0b
  01` that this driver uses was not tried there. Creality's `auto_addr`
  brought an unflashed loader box back, and after a real flash only a power
  cycle did.
- A box stuck in the loader is addressed by stock Klipper at boot and then
  ignores `a1`: power-cycle it with Klipper stopped so it is unaddressed.
- `SYSTEM_RESET` is command `25`. Over CAN (the K1's IAP path) the same `F7`
  frame is sent wrapped, e.g. `cansend 0FE#F7FE04FF25A2FB`: `F7`, broadcast
  `0xfe`, length 4, `ff 25 a2`, CRC `fb` (checked).
- Firmware files are `<boot_ver>-<app_ver>.bin`, e.g.
  `cfs0_050_G32-cfs0_000_142.bin`. The `G30`/`G32` suffix is the hardware
  variant (the wrong one can brick the box), the last three digits of the
  app part are the version. Newer Creality bundles also list a `cfs6`
  generation with its own scheme: K2 firmware 1.1.7.0 carries `cfs6_100_G31`
  with `cfs6_222_000`, and 1.1.8.3 carries `cfs6_236_000` for it plus a new
  `cfs6_100_G51` board with `cfs6_237_000`. `cfs0` stays at `G30` 1.5.0 and
  `G32` 1.5.3. 1.1.8.3 also names a "CFS Nano" product; nothing else about
  it is known here.
- In app mode `06` and `07` are motor-control writes: do not probe unknown
  commands on a live box.
- **On the K2** the CFS shares `/dev/ttyS5` with the closed-loop motors
  (X `0x81`, Y `0x82`; K2 Plus Z `0x83`, `0x84`), not a USB adapter of its
  own as on the K1. A flash would need Klipper stopped, and whether the
  motors ignore the `0xeb` frame is not yet checked.

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
- compatibility across CFS hardware and firmware revisions beyond the
  boxes seen so far (firmware 1.4.2 to 1.5.3, on K1 and K2; see the naming
  notes above), and anything about the `cfs6` generation on a real box
- whether the K2's bus motors ignore the CFS loader frames
- whether the protocol can safely coexist with other printer serial stacks

Do not connect a CFS to another controller based only on this document.
Electrical validation is required first.

## Portability plan

The current code should be separated into:

1. generic framing and non-blocking serial transport
2. CFS commands, response decoders, and state machine
3. printer adapter for motion, hotend, cutter, and filament sensors

`k2_motor_bus.xfer()` does not block Klipper while it waits for a reply. Since
v0.2.0 `_read_frame` keeps the port non-blocking, reads in small chunks and
yields to the reactor between reads, so a CFS stage that takes 20+ s to
answer leaves the heaters running. In strict mode `proto.find_reply` checks
the CRC and address and reads past frames that fail, and a reactor mutex
holds the bus for the whole transfer, so a timer cannot take another
caller's reply. The only blocking step is `ser.flush()`, about half a
millisecond for a short frame at 230400 baud.

The limit is the shared bus: the CFS and the closed-loop motors both use
`/dev/ttyS5`, so a long CFS stage holds the lock and the motor temperature
poll waits for it.

Lamar1007's `k1_cfs_bus.py` (a Klipper module on the K1, RS485 working, CAN
a stub) derives its read loop from this one and puts it behind a single
`xfer()` that picks the transport, which is a useful model for the split
above. Its framing matches the K2 motor frames (`f7 81 04 00 11 01 1d`).

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
- **Pin shake.** On the DXC2 the sensor pin's spring is weak and the pin can
  stay in with the filament gone (11-02). Before anything is pushed, the
  toolhead swings 6 times between purge_x and purge_x + 8 mm over the chute
  at the wipe's speed and acceleration, then looks again
  (`unload_shake_moves`, 0 = off; `unload_shake_distance`).
- **Auto-clear.** If the sensor still sees filament after that, the
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
- **Stage-5 reply wait** (`load_stage5_timeout` [1], since v0.4.18). CFS
  firmware 1.5.0 does not answer stage 5 while it feeds: every change on
  11-01 waited the full 15 s for a reply, after the filament had already
  arrived, and a feed slower than 15 s + 3 s failed the load. Like his
  driver, a send now waits 1 s; no reply means still feeding, and the
  nozzle sensor (or `load_timeout`) ends the wait. A reply with an error
  status is handled as before.
- **Stall retry motion** (`stall_retry_motion` [off],
  `stall_retry_moves` [`Y255, X220, Y40, X40`]). His
  `filament_retry_motion` sweeps the head (K2 Plus: Y350 X300 Y50 X50) to
  free the PTFE tube; ours is scaled to the F021 bed, starts and ends at the
  chute and stays in front of `purge_safe_y` at the current Z.
- **Flush matrix purge** (`toolchange_purge_from_file` [off]). Reads
  `flush_volumes_matrix` (and `flush_multiplier`) from the end of the print
  file when the slicer passes no `PURGE`: volume / filament area. Like
  materials: SHORT purge, 30-240 mm. Otherwise the stock purge, longer if
  the file asks. The matrix is indexed by the file's tools: the T macros pass
  `TOOL=`/`OLD_TOOL=` to `CFS_TOOLCHANGE`; without them T0-T3 are slots A-D.
- **Clog check** (`clog_detection` [off], `clog_extruder_length` [80],
  `clog_wheel_reset_length` [18], `print_poll_interval` [0 = 10 s]). Pauses
  when the extruder feeds 80 mm while the hub wheel moves under 18 mm (the
  box refills the buffer in ~25-30 mm chunks). Skipped while a slot is
  running out.
- **`CFS_RFID_READ [SLOT=x]`** reads the cached RFID records and remaining %
  (no motion).
