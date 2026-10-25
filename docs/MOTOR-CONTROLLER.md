# K2 closed-loop motor controllers

The K2's X and Y motors (and the extruder) are closed-loop servo drives with
their own controller board. Klipper still sends them step and direction, but
setup, stall homing, protection and tuning go over an RS485 bus
(`/dev/ttyS5`, 230400 baud, shared with the CFS). The extruder controller is
reached through the nozzle MCU's `transparent_send` command.

`klippy/extras/k2_motor_bus.py` handles the wake-up handshake at boot and
exposes `MOTOR_BUS_SEND ADDR=<n> PAYLOAD=<hex> [READ_LEN=<bytes>]
[TIMEOUT=<s>]` for raw frames.

## Addresses

| Motor | Address |
| --- | --- |
| X (CoreXY motor A) | 0x81 (129) |
| Y (CoreXY motor B) | 0x82 (130) |
| E | via nozzle MCU |

## Opcodes (second payload byte)

| Opcode | Use |
| --- | --- |
| 01 | reboot |
| 03 / 04 | encoder / electrical-offset calibration (factory procedures) |
| 05 | control |
| 06 | system parameters: read, write, end (below) |
| 07 | **flash parameters: do not use** |
| 08 | get: 5 position, 6 speed, 7 id, 8 iq feedback |
| 0c | protection status |
| 0f | version |
| 11 | stall mode (used around stall homing) |
| 13 | error detail record (77 bytes) |
| 15 / 16 | position / speed telemetry snapshot (164 bytes) |

## Reading and writing parameters

```
read   00 06 02 <id>              -> 00 06 <value>
write  00 06 01 <id> <value>      -> 00 06 <value>  (echo)
end    00 06 03                   -> 00 06 01
```

Float parameters are 4-byte little-endian `float32`; integer parameters are
2-byte little-endian. A reply of `00 06 af` means the ID is not readable.
Writes go to RAM only: a power cycle restores the factory values. Example,
X position-loop gain (0x46 = 300.0):

```
MOTOR_BUS_SEND ADDR=129 PAYLOAD=00060246
MOTOR_BUS_SEND ADDR=129 PAYLOAD=000603
```

## Parameter map

Creality's own firmware logs every parameter name with its ID, and whether
its updater treats it as configurable. Only the configurable ones can be read
or written through opcode 06. The full map (227 names) is in
`tools/motor/motor_param_map.json`. The useful ones, with stock K2 values:

| ID | Parameter | Stock |
| --- | --- | --- |
| 0x46 | position loop kp | 300 (K2 Plus: X 100) |
| 0x56 / 0x57 / 0x59 | speed loop kp / ki / kc | 0.028 / 6 / 0.5 |
| 0x5e / 0x5f / 0x61 | speed loop FAL enable / a / zoom | 0 / 1 / 0.1 (**do not enable**) |
| 0x66 / 0x67 / 0x69 | current loop kp / ki / kc | 2.5 / 200 / 0.5 |
| 0x6e | current loop FAL enable | 0 |
| 0x74, 0x76-0x79 | LESO observer b0k, z3k, wp, ws, wd | 1, 0, 4000, 4000, 400 |
| 0x42 / 0x43 | feedforward id/iq, ud/uq | 1 / 1 (on) |
| 0x7a / 0x7b / 0x7f | field weakening enable / I max / base speed | 1 / 5.5 / 120 |
| 0x80 / 0x82 | current filter enable / cutoff | 0 / 6000 |
| 0x0c-0x0e | stall mode / current / position error | 2 / 0.7 / 0.012 |
| 0xbb / 0xbc | tracking-error trip / time | 0.3 rad / 0.1 s |
| 0xd9-0xdf | "zazen" idle mode (reduced gains after 1 s still) | on |

**Locked (not readable or writable through opcode 06):** the three speed-loop
notch filters (0x92-0x9d), the torque-ripple compensator (0x9e-0xa7), the
second-order output filters (0x83-0x8c) and the position loop ki/kd. No K2
model enables them in Creality's configs either.

## Telemetry

`PAYLOAD=0015 READ_LEN=400` returns 10 consecutive control-loop records of
4 `float32` each: target position, feedback position, an internal value and
the error, in radians. `0016` returns the speed equivalents. It is a snapshot
of about 1-2 ms, not a stream. `tools/motor/run_motor_tests.py` polls it during
test moves together with the head accelerometer, and
`analyze_motor_tests.py` summarises a run.

## Measured baseline (stock gains, F021)

| Test | Tracking error RMS / max | Vibration above 35 Hz |
| --- | --- | --- |
| 200 mm/s, 5000 mm/s² | 6-7 / 8 µm | 550-750 mm/s² |
| 300 mm/s, 20000 mm/s² | 8-10 / 11 µm | 1150-1400 mm/s² |

The micrometre figures assume a 20-tooth GT2 pulley, which has not been
confirmed on the K2.

The controllers track to about a hundredth of a millimetre, because the
feedforward cancels the usual lag. Wall quality on the K2 is limited by frame
resonance (35-46 Hz) and acceleration, not by motor tuning: tune input shaping
and wall acceleration first.

## The FAL incident (read before experimenting)

Setting speed-loop FAL (0x5e) to 1 on X and Y worked in motion: tracking
improved slightly and vibration was unchanged. Some minutes later, with the
motors idle, both controllers stopped answering on the bus. Homing then failed
and the head moved erratically. A Klipper restart could not wake them; a full
power cycle did, with factory values restored. The likely cause is FAL
combined with the idle "zazen" gain reduction.

Rules for any motor experiment:

- read back every write
- idle-test for at least 5 minutes with periodic reads before walking away
- restore the stock value before leaving the printer idle or starting a print
- never use opcode 07 (flash)

## Firmware updates

Creality ships motor firmware (`mot*_C30-*.bin`) in its update images. The
stock `mcu_update` boot script checks every board, motors included, on every
boot and flashes newer files from `/usr/share/klipper/fw/`. With Creality's
OTA service disabled, no new files arrive.
