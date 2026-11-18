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

FUNC_ENCODER_CALIBRATE = 0x03
FUNC_ELEC_OFFSET_CALIBRATE = 0x04
FUNC_SYS_PARAM = 0x06
FUNC_GET = 0x08
FUNC_PROTECTION = 0x0c
FUNC_READ_ADDR = 0x0e
FUNC_STALL_MODE = 0x11

# Indices for FUNC_GET, each a float32 reply (seen on 0x82 during stock
# homing; the units are the controller's: radians for position)
GET_POSITION = 0x05
GET_SPEED = 0x06
GET_ID = 0x07
GET_IQ = 0x08
# Motor MCU temperature, deg C
GET_MCU_TEMP = 0x11
GET_LABELS = {GET_POSITION: 'position', GET_SPEED: 'speed', GET_ID: 'id',
              GET_IQ: 'iq', GET_MCU_TEMP: 'mcu_temp'}
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

# Electrical-offset calibration (request 00 04 00). The motor turns its own
# shaft, works out the offset, stores it and answers when done:
#   payload = status (u8, 1 = ok) + max deviation (f32 LE, degrees)
#             + mean pulse (f32 LE, the new offset, param 09)
# Encoder calibration (00 03 00) answers one result byte; 0x0c = stage 1
# done. Stock only runs it on the extruder motor and asks for a power cycle
# before the offset stage, so k2_motor_bus does not send it yet.
# Layouts from Jacob10383's K2 Plus custom firmware (extras/motor_control.py
# elec_offset_calibrate, encoder_calibrate_official and
# _decode_calibration_payload), GPLv3. Not yet checked against a stock
# capture on the Creality Hi.
CALIBRATE_REQUEST = 0x00
CALIBRATION_OK = 0x01
ENCODER_STAGE1_DONE = 0x0c

# System parameters (opcode 06), RAM only: a power cycle restores the motor
# board's flash values.
#   read    00 06 02 <id>          -> 00 06 <value>
#   write   00 06 01 <id> <value>  -> 00 06 <value>   (echo of what it stored)
#   commit  00 06 03               -> 00 06 01
# The value is float32 LE, or int16 LE for PARAM_INT_IDS. Frames as captured
# from stock motor_init (gitstonelabs creality-klipper-unlocked,
# docs/protocol/README.md 3.2 and 5.3: X write 06 01 46 00 00 c8 42 =
# 0x46 100.0, int16 write 06 01 ad 01 00, commit 06 03 -> 06 01). Opcode 07
# (flash) is never built here.
PARAM_WRITE = 0x01
PARAM_READ = 0x02
PARAM_COMMIT = 0x03
PARAM_COMMIT_OK = 0x01
# The 52 parameters stock motor_init reads (and writes when they differ),
# from the same capture; names as in Creality's motor_control.cfg (with
# the x_ / y_ prefix dropped) and tools/motor/motor_param_map.json.
STOCK_PARAMS = {
    0x0c: 'param_stall_mode', 0x0d: 'param_stall_cur_A',
    0x0e: 'param_stall_pos_err_rad', 0x14: 'param_ud_cal_set',
    0x15: 'param_motion_dir',
    0x17: 'param_encoder_calibtate_official_ud_cal_set',
    0x29: 'motor_param_R', 0x2a: 'motor_param_L', 0x2b: 'motor_param_Ke',
    0x42: 'controller_param_en_feedforward_idiq',
    0x43: 'controller_param_en_feedforward_uduq',
    0x46: 'controller_pos_loop_pid_param_kp',
    0x56: 'controller_spd_loop_pid_param_kp',
    0x57: 'controller_spd_loop_pid_param_ki',
    0x59: 'controller_spd_loop_pid_param_kc',
    0x5e: 'controller_spd_loop_pid_fal_param_en',
    0x5f: 'controller_spd_loop_pid_fal_param_a',
    0x61: 'controller_spd_loop_pid_fal_param_zoom',
    0x66: 'controller_cur_loop_pid_param_kp',
    0x67: 'controller_cur_loop_pid_param_ki',
    0x69: 'controller_cur_loop_pid_param_kc',
    0x6e: 'controller_cur_loop_pid_fal_param_en',
    0x6f: 'controller_cur_loop_pid_fal_param_a',
    0x71: 'controller_cur_loop_pid_fal_param_zoom',
    0x74: 'controller_leso_param_b0k', 0x76: 'controller_leso_param_z3k',
    0x77: 'controller_leso_param_wp', 0x78: 'controller_leso_param_ws',
    0x79: 'controller_leso_param_wd', 0x7a: 'controller_fwc_param_en',
    0x7b: 'controller_fwc_param_I_max', 0x7f: 'controller_fwc_param_wm_base',
    0x80: 'controller_cur_filter_param_en',
    0x82: 'controller_cur_filter_param_fc',
    0xad: 'protection_param_protect_en',
    0xb3: 'protection_param_prt_peak_cur_A',
    0xb4: 'protection_param_prt_continuous_cur_A',
    0xb5: 'protection_param_prt_continuous_time_s',
    0xb6: 'protection_param_prt_over_speed_rad_s',
    0xb7: 'protection_param_prt_over_speed_time_s',
    0xbb: 'protection_param_prt_track_max_err',
    0xbc: 'protection_param_prt_track_err_time',
    0xbd: 'protection_param_power_voltage_min',
    0xbe: 'protection_param_mcu_temp_max',
    0xc8: 'step_controller_param_subdivision',
    0xcc: 'cmd_int_param_char_cmd_support', 0xd9: 'zazen_param_zazen_en',
    0xda: 'zazen_param_zazen_trigger_time_s',
    0xdc: 'zazen_param_zazen_gain_spd_kp',
    0xdd: 'zazen_param_zazen_gain_spd_ki',
    0xde: 'zazen_param_zazen_gain_cur_kp',
    0xdf: 'zazen_param_zazen_gain_cur_ki',
}
# int16 parameters (same capture); every other one is float32
PARAM_INT_IDS = frozenset((0x0c, 0x42, 0x43, 0x5e, 0x6e, 0x7a, 0x80, 0xad,
                           0xc8, 0xcc, 0xd9))
# Never written from a config, whatever it says
PARAM_WRITE_REFUSED = {
    0x09: "electrical offset: factory calibration, only MOTOR_CALIBRATE "
          "changes it",
    0x0a: "phase order: factory calibration",
    0x19: "electrical offset error: factory calibration",
    0x29: "motor resistance R: motor model constant",
    0x2a: "motor inductance L: motor model constant",
    0x2b: "motor back-EMF constant Ke: motor model constant",
    0x5e: "speed-loop FAL enable: it locked both K2 X/Y controllers off "
          "the bus until a power cycle (docs/MOTOR-CONTROLLER.md)",
    0x6e: "current-loop FAL enable: same class as 0x5e, never tested",
}


def param_read_request(pid):
    return (0x00, FUNC_SYS_PARAM, PARAM_READ, pid)


def encode_param(pid, value):
    """The value bytes of a write: int16 LE for PARAM_INT_IDS, else
    float32 LE."""
    if pid in PARAM_INT_IDS:
        if float(value) != int(value):
            raise ValueError("param 0x%02x is an integer, got %r"
                             % (pid, value))
        return struct.pack('<h', int(value))
    return struct.pack('<f', float(value))


def param_write_request(pid, value):
    """00 06 01 <id> <value>. Refuses PARAM_WRITE_REFUSED here too, so no
    caller can build one."""
    if pid in PARAM_WRITE_REFUSED:
        raise ValueError("param 0x%02x may not be written: %s"
                         % (pid, PARAM_WRITE_REFUSED[pid]))
    return (0x00, FUNC_SYS_PARAM, PARAM_WRITE, pid) + tuple(
        encode_param(pid, value))


def decode_param(pid, payload):
    """A read or write-echo payload as a number, or None when it is not
    the width this parameter has (00 06 af = not readable)."""
    payload = bytes(payload or b'')
    if pid in PARAM_INT_IDS:
        if len(payload) != 2:
            return None
        return struct.unpack('<h', payload)[0]
    if len(payload) != 4:
        return None
    return struct.unpack('<f', payload)[0]


def param_matches(pid, current, wanted):
    """True when the motor already holds wanted (float32 rounding
    allowed)."""
    if current is None:
        return False
    if pid in PARAM_INT_IDS:
        return int(current) == int(wanted)
    stored = struct.unpack('<f', struct.pack('<f', float(wanted)))[0]
    return abs(float(current) - stored) <= 1e-6 * max(1., abs(stored))


def format_param(pid, value):
    if value is None:
        return '?'
    if pid in PARAM_INT_IDS:
        return "%d" % (value,)
    # float32 -> shortest text that reads back the same (0.085, not
    # 0.0850000009)
    return "%.6g" % (value,)


def parse_param_writes(text):
    """'0x46=100, 0x56=0.085' -> [(0x46, 100.0), (0x56, 0.085)]. Raises
    ValueError naming the ID for a refused, unknown, repeated or badly
    typed parameter."""
    out = []
    seen = set()
    for item in (text or '').replace('\n', ',').split(','):
        item = item.strip()
        if not item:
            continue
        if '=' not in item:
            raise ValueError("'%s' is not id=value" % (item,))
        key, value = [part.strip() for part in item.split('=', 1)]
        try:
            pid = int(key, 0)
        except ValueError:
            raise ValueError("'%s': id must be a number such as 0x56"
                             % (item,))
        if pid in PARAM_WRITE_REFUSED:
            raise ValueError("param 0x%02x may not be written: %s"
                             % (pid, PARAM_WRITE_REFUSED[pid]))
        if pid not in STOCK_PARAMS:
            raise ValueError("param 0x%02x is not one stock motor_init "
                             "sets; refusing to write it" % (pid,))
        if pid in seen:
            raise ValueError("param 0x%02x is listed twice" % (pid,))
        try:
            number = float(value)
        except ValueError:
            raise ValueError("param 0x%02x: '%s' is not a number"
                             % (pid, value))
        if number != number or number in (float('inf'), float('-inf')):
            raise ValueError("param 0x%02x: '%s' is not a finite number"
                             % (pid, value))
        if pid in PARAM_INT_IDS:
            if number != int(number) or not -32768 <= number <= 32767:
                raise ValueError("param 0x%02x (%s) is an int16, got '%s'"
                                 % (pid, STOCK_PARAMS[pid], value))
            number = int(number)
        seen.add(pid)
        out.append((pid, number))
    return out


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


def elec_offset_request():
    return (0x00, FUNC_ELEC_OFFSET_CALIBRATE, CALIBRATE_REQUEST)


def decode_elec_offset_reply(frame):
    """Decode a parsed reply to 00 04 00. ok is True only for status 1 with
    both floats present; a short payload keeps what it has."""
    payload = bytes(frame['payload'])
    status = payload[0] if payload else None
    max_dev = mean_pulse = None
    if len(payload) >= 9:
        max_dev = struct.unpack('<f', payload[1:5])[0]
        mean_pulse = struct.unpack('<f', payload[5:9])[0]
    return {'status_code': status,
            'ok': status == CALIBRATION_OK and mean_pulse is not None,
            'max_deviation_deg': max_dev,
            'mean_pulse': mean_pulse,
            'frame_status': decode_frame_status(frame['status'])}
