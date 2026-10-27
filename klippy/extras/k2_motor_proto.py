# k2_motor_proto.py - frame parsing and fault decoding for the K2's RS485
# closed-loop motor controllers (and the CFS, which shares the framing).
#
# Pure functions with no Klipper imports, so k2_motor_bus, hark_safety and
# the offline tests can all use them. This is not a config section.
#
# Reply frame (motor controllers and CFS):
#   f7 | addr | len | status | func | payload... | crc8
#     - len    = bytes after the len byte (status + func + payload + crc), so
#                a valid reply has len >= 3
#     - status = latch bits on a motor reply: 0x01 stall, 0x02 error,
#                0x04 warning. A motor with a latched fault answers 02 0c ...
#                (the CFS uses this byte for its own status codes instead)
#     - func   = echo of the request's function byte (payload[1] of the
#                request we build)
#     - crc8   = poly 0x07 over [len, status, func, payload...]
# Requests use the same layout; the status byte is 0x00 (motors) or 0xff
# (most CFS commands).
#
# Protection reply (request 00 0c 0b): payload = error_code (uint32 LE) +
# warning_code (uint32 LE), one bit per protection, labels below.
#
# Portions adapted from Jacob10383's K2 Plus custom firmware
# (https://github.com/Jacob10383/k2-plus-custom-firmware,
# extras/motor_control.py motor_fault_decoder, parse_response_frame and
# is_near_zero_calibration_value, and extras/serial_485.py
# check_485_frame_crc and _matches_response), GPLv3.
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import struct

PACK_HEAD = 0xf7
PACK_LEN_MIN = 3        # status + func + crc
BROADCAST_ADDRS = (0xfe, 0xff)
# Motor controllers answer from 0x81..0x8f (addr = 0x81 + DIR + 2 * STEP
# latched at wake); the CFS boxes from 0x01..0x04.
MOTOR_ADDRS = range(0x81, 0x90)

FUNC_SYS_PARAM = 0x06
FUNC_GET = 0x08
FUNC_PROTECTION = 0x0c
FUNC_READ_ADDR = 0x0e
FUNC_STALL_MODE = 0x11

# Index for FUNC_GET: motor MCU temperature, float32 deg C
GET_MCU_TEMP = 0x11
# Motor firmware limit (protection_param_mcu_temp_max, 0xbe)
MCU_TEMP_MAX = 115.

FRAME_STATUS_STALL = 0x01
FRAME_STATUS_ERROR = 0x02
FRAME_STATUS_WARNING = 0x04
FRAME_STATUS_KNOWN = (FRAME_STATUS_STALL | FRAME_STATUS_ERROR
                      | FRAME_STATUS_WARNING)

ERROR_CODE_LABELS = {
    0: "encoder mutation",
    1: "encoder read error",
    2: "software peak overcurrent",
    3: "software continuous overcurrent",
    4: "hardware overcurrent",
    5: "speed feedback continuously over limit",
    6: "position feedback over limit",
    7: "position command mutation",
    8: "excessive position tracking error",
    9: "motor check value abnormal",
    10: "motor check unstable",
    11: "motor phase not connected",
    12: "motor phase resistance mismatch",
    13: "motor check other error",
}

WARNING_CODE_LABELS = {
    0: "communication error",
    1: "motor power supply too low",
    2: "MCU overheating",
}

# Flash calibration parameters: electrical offset and its error. A value at
# or below this means the motor was never calibrated (their firmware warns
# not to use the motor then).
CALIBRATION_PARAM_IDS = (0x09, 0x19)
CALIBRATION_NEAR_ZERO = 0.15


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xff if crc & 0x80 \
                else (crc << 1) & 0xff
    return crc


def build_frame(addr, payload):
    body = bytes([len(payload) + 1]) + bytes(payload)
    return bytes([PACK_HEAD, addr]) + body + bytes([crc8(body)])


def check_crc(frame):
    """True when the last byte is the CRC of [len .. payload]."""
    frame = bytes(frame)
    return len(frame) >= 4 and crc8(frame[2:-1]) == frame[-1]


def request_func(addr, payload):
    """Function byte the reply must echo, or None when we do not check it.
    Only motor-controller replies are checked; CFS replies get CRC and
    address checks only."""
    if addr in MOTOR_ADDRS and len(payload) >= 2:
        return payload[1]
    return None


def reply_problem(frame, addr, func=None):
    """Why a complete frame is not the reply to a request for (addr, func),
    or None when it is. A broadcast request accepts any sender."""
    if not check_crc(frame):
        return 'crc'
    if frame[2] < PACK_LEN_MIN:
        return 'len'
    if addr not in BROADCAST_ADDRS and frame[1] != addr:
        return 'addr'
    if func is not None and frame[4] != func:
        return 'func'
    return None


def find_reply(buf, addr, func=None, max_len=None):
    """Scan buf for the reply to (addr, func).

    Returns (frame, rest, rejects): frame is the first valid reply or None,
    rest is the unread tail to keep scanning when more bytes arrive, rejects
    lists why frames were thrown away ('crc', 'len', 'addr', 'func'). A bad
    CRC or length skips one byte (the 0xf7 may be noise); a valid frame for
    someone else (a late CFS reply, say) is skipped whole."""
    buf = bytes(buf)
    rejects = []
    while True:
        start = buf.find(bytes([PACK_HEAD]))
        if start < 0:
            return None, b'', rejects
        buf = buf[start:]
        if len(buf) < 3:
            return None, buf, rejects
        total = buf[2] + 3
        if buf[2] < PACK_LEN_MIN or (max_len is not None and total > max_len):
            rejects.append('len')
            buf = buf[1:]
            continue
        if len(buf) < total:
            return None, buf, rejects
        frame = buf[:total]
        why = reply_problem(frame, addr, func)
        if why is None:
            return frame, buf[total:], rejects
        rejects.append(why)
        buf = buf[1:] if why in ('crc', 'len') else buf[total:]


def parse_frame(rx):
    """Split a reply frame. Raises ValueError if it is not a whole, valid
    frame."""
    rx = bytes(rx or b'')
    if len(rx) < 6:
        raise ValueError("short reply %s" % (rx.hex(' ') or '(none)',))
    if rx[0] != PACK_HEAD:
        raise ValueError("bad reply head %02x" % (rx[0],))
    if rx[2] < PACK_LEN_MIN or len(rx) != rx[2] + 3:
        raise ValueError("bad reply length %s" % (rx.hex(' '),))
    if not check_crc(rx):
        raise ValueError("bad reply crc %s" % (rx.hex(' '),))
    return {'addr': rx[1], 'status': rx[3], 'func': rx[4],
            'payload': rx[5:-1]}


def decode_frame_status(status):
    value = int(status or 0) & 0xff
    return {'status': value,
            'stalled': bool(value & FRAME_STATUS_STALL),
            'faulted': bool(value & FRAME_STATUS_ERROR),
            'warned': bool(value & FRAME_STATUS_WARNING),
            'unknown': value & ~FRAME_STATUS_KNOWN}


def decode_bits(mask, labels):
    """Names of the set bits; unnamed bits appear as 'bit N'."""
    out = []
    bit = 0
    mask = int(mask or 0)
    while mask:
        if mask & 1:
            out.append(labels.get(bit, "bit %d" % (bit,)))
        mask >>= 1
        bit += 1
    return out


def decode_protection(frame):
    """Decode a parsed reply to 00 0c 0b. has_error is what should stop a
    print: an error code from the controller. Warnings (low supply, MCU
    hot, comms) and status-byte latches without an error code are only
    reported."""
    payload = bytes(frame['payload'])
    error_code = int.from_bytes(payload[:4].ljust(4, b'\0'), 'little')
    warning_code = int.from_bytes(payload[4:8].ljust(4, b'\0'), 'little')
    out = decode_frame_status(frame['status'])
    out.update({
        'error_code': error_code,
        'warning_code': warning_code,
        'errors': decode_bits(error_code, ERROR_CODE_LABELS),
        'warnings': decode_bits(warning_code, WARNING_CODE_LABELS),
        'has_error': bool(error_code),
        'active': bool(error_code or warning_code),
    })
    return out


def describe_protection(detail):
    """One line for a console message, e.g. 'excessive position tracking
    error; warning: MCU overheating'."""
    parts = []
    if detail['errors']:
        parts.append(", ".join(detail['errors']))
    if detail['warnings']:
        parts.append("warning: " + ", ".join(detail['warnings']))
    flags = [name for name, on in (('stall', detail['stalled']),
                                   ('error', detail['faulted']),
                                   ('warning', detail['warned'])) if on]
    if flags and not (detail['errors'] or detail['warnings']):
        parts.append("status latch %s" % ("/".join(flags),))
    if detail['unknown']:
        parts.append("unknown status bits 0x%02x" % (detail['unknown'],))
    return "; ".join(parts) or "ok"


def decode_float(payload):
    payload = bytes(payload)
    if len(payload) != 4:
        raise ValueError("expected 4 bytes, got %s" % (payload.hex(' '),))
    return struct.unpack('<f', payload)[0]


def is_near_zero_calibration(value):
    return value is not None and abs(float(value)) <= CALIBRATION_NEAR_ZERO \
        + 1e-6
