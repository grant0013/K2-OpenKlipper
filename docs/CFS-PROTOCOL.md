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
