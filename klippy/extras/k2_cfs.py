# k2_cfs.py - open CFS (Creality Filament System) driver for
# MAINLINE Klipper on the K2. Shares the RS485 bus with k2_motor_bus.
#
# The CFS is a bus device at addr 0x01 on /dev/ttyS5 (0xf7-framed protocol,
# see k2_motor_bus.py for framing/CRC). Opcode map mined from 6 days of stock
# klippy logs (2026-07-02..07) by correlating Creality's named command log
# lines with the hex frames sent immediately after — see
# scratchpad/mine_cfs_protocol.py and the RUNBOOK.
#
# Separately gated commands arm/disarm normal feed, move through validated
# purge/cutter routes, and reproduce the stock cut, unload, load, and purge
# phases. The phases remain separate until each has been qualified on hardware.
#
# Config:
#   [k2_cfs]
#   #addr: 1
#   #allow_actuation: false
#   #purge_speed: 100
#   #cutter_speed: 100
#
# Gcode:
#   CFS_STATUS            - query + decode everything read-only we know
#   CFS_QUERY CMD=<name>  - fire one named read-only query (see QUERIES)
#   CFS_FEED_START SLOT=D CONFIRM=1
#   CFS_FEED_STOP
#   CFS_PURGE_POSITION CONFIRM=1
#   CFS_PURGE_RETURN CONFIRM=1
#   CFS_CUTTER_POSITION CONFIRM=1  - movement only; does not cut/retract
#   CFS_CUTTER_RETURN CONFIRM=1    - movement only; return to safe position
#   CFS_CUTTER_SENSOR               - read the nozzle-MCU PB9 hall input
#   CFS_UNLOAD                      - hard-blocked; cutting must come first
#   CFS_CUT_UNLOAD SLOT=D CONFIRM=1 - cut, release, then CFS retract
#   CFS_LOAD SLOT=D CONFIRM=1       - stock staged CFS/hotend load handshake
#   CFS_PURGE CONFIRM=1              - stock nominal-140/actual-160mm flush
#   CFS_PRINT_START SLOT=D CONFIRM=1 - select/load/purge initial print slot
#   CFS_RFID_READ [SLOT=x]           - read the RFID records (no motion)
#   T0/T1/T2/T3           - arm only the already-selected A/B/C/D slot
#
# Portions adapted from Jacob10383's K2 Plus custom firmware
# (https://github.com/Jacob10383/k2-plus-custom-firmware, extras/box.py,
# extras/box_protocol.py, extras/box_change.py, extras/box_gcode.py), GPLv3:
# the status code texts, the ff 0a slot-event/runout decode, the stall
# retry motion, the measuring-wheel clog check, the flush matrix purge and
# the RFID record decode.

import math
import os
import re
import struct
import logging


# Box status byte (the first payload byte of every reply): Creality's own
# name where we know it from the stock module tables, Jacob10383's name and
# his plain-English text (box_protocol.py STATUS_NAMES / STATUS_TEXT).
STATUS_OK = 0x00
STATUS_BUSY = 0x03
STATUS_ODOMETER_STALLED = 0x0c
STATUS_STAGE7_NO_MOTION = 0x0e
STATUS_UNLOAD_MOTOR_BLOCKED = 0x19
STATUS_SLOT_EVENT = 0x30
STATUS_RUNOUT = 0x50
STATUS_INFO = {
    0x01: ('PARAMS', 'INVALID_PARAM',
           "The box rejected a command as invalid."),
    0x02: ('CRC', 'BAD_CRC',
           "The box is rejecting corrupted messages. If this keeps "
           "happening, check its cable."),
    0x03: ('STATE', 'BUSY',
           "The box is still finishing another operation."),
    0x04: ('LENGTH', None, "The box rejected the message length."),
    0x05: ('EXTRUDE_ERR1', 'STAGE0_SENSOR_TIMEOUT',
           "Filament never reached the hub's sensor within 7 s. Check that "
           "it's seated in the slot gears and not tangled."),
    0x06: (None, 'BUFFER_FILL_TIMEOUT', "The buffer did not fill in time."),
    0x08: ('EXTRUDE_ERR4', 'SLOT_EMPTY',
           "There's no filament at this slot's inlet."),
    0x09: ('EXTRUDE_ERR5', 'STAGE0_ODOMETER_TIMEOUT',
           "The hub's measuring wheel didn't turn for 2 s. Check for a "
           "tangle, or that the hub is gripping the filament."),
    0x0a: ('EXTRUDE_ERR6', 'FEED_TIMEOUT',
           "The hub fed for 25 s without filament reaching the printhead."),
    0x0b: ('EXTRUDE_ERR7', 'OVERTRAVEL',
           "The hub fed 3 m, but filament never reached the printhead. "
           "Check for a disconnected PTFE tube."),
    0x0c: ('EXTRUDE_ERR8', 'ODOMETER_STALLED',
           "The hub's measuring wheel stopped while feeding. Check for a "
           "jam or resistance in the path."),
    0x0d: ('EXTRUDE_ERR10', 'BUFFER_NOT_FULL',
           "The buffer didn't report full within 3 s of feeding. Check the "
           "PTFE into the buffer."),
    0x0e: ('EXTRUDE_ERR9', 'STAGE7_NO_MOTION',
           "The final load nudge moved under 1.5 mm."),
    0x13: ('RETRUDE_ERR1', 'UNLOAD_BUFFER_TIMEOUT',
           "The hub tried to pull back, but the buffer never released "
           "within 5 s, so the filament is likely jammed. Check for a jam "
           "between the hub and the printhead."),
    0x14: (None, 'UNLOAD_HUB_PE_TIMEOUT',
           "The retract waited for the hub path to clear, and it never "
           "did. Check for filament stuck in the hub."),
    0x16: (None, 'UNLOAD_NO_FILAMENT',
           "This slot shows no filament at its inlet."),
    0x17: (None, 'UNLOAD_INLET_CLEAR',
           "The slot's inlet already showed no filament when the retract "
           "started."),
    0x18: (None, 'UNLOAD_ALL_EMPTY', "No slot shows filament."),
    0x19: (None, 'UNLOAD_MOTOR_BLOCKED',
           "The hub motor stalled while pulling filament back. Check for a "
           "jam."),
    0x1a: (None, 'UNLOAD_ODOMETER_TIMEOUT',
           "The hub pulled back for 40 s without the path clearing."),
    0x30: (None, 'SLOT_EVENT', "A spool was inserted or removed."),
    0x50: (None, 'RUNOUT', "The spool ran out."),
    0x51: (None, 'BUFFER_REFILL_STALLED',
           "The hub is feeding, but the buffer stays empty. Check the PTFE "
           "from the hub into the buffer."),
    0x52: (None, 'BUFFER_REFILL_NO_MOTION',
           "The hub tried to refill the buffer, but the filament moved "
           "less than 5 mm. Check for a jam or tangle."),
}


def status_label(code):
    # "0x0c EXTRUDE_ERR8 / ODOMETER_STALLED: The hub's measuring wheel..."
    if code is None:
        return "no response: the box didn't respond."
    creality, name, text = STATUS_INFO.get(
        code, (None, None, "The box reported an unknown status."))
    names = " / ".join(n for n in (creality, name) if n)
    return "0x%02x%s: %s" % (code, " " + names if names else "", text)


class CFSStatusError(RuntimeError):
    # A reply that came back with a non-zero box status byte
    def __init__(self, message, status):
        RuntimeError.__init__(self, message)
        self.status = status


def _parse_moves(text, x_max, y_max, error):
    # "Y255, X220, Y40, X40": comma-separated absolute XY moves (Jacob10383's
    # filament_retry_moves format), kept inside the bed and in front of the
    # purge safe line
    moves = []
    for item in str(text).split(','):
        words = item.upper().split()
        move = {}
        try:
            if not words:
                raise ValueError(item)
            for word in words:
                axis, value = word[0], float(word[1:])
                if axis not in 'XY' or axis in move:
                    raise ValueError(item)
                limit = x_max if axis == 'X' else y_max
                if not 0. <= value <= limit:
                    raise ValueError(item)
                move[axis] = value
        except ValueError:
            raise error("Invalid stall_retry_moves entry %r (X 0-%.0f, "
                        "Y 0-%.0f)" % (item.strip(), x_max, y_max))
        moves.append(move)
    return moves


def read_flush_metadata(path, limit=1024 * 1024):
    # Orca/Creality Print write their settings at the end of the file:
    # "; flush_volumes_matrix = 0,280,..." (mm^3, row = from tool, column =
    # to tool) and "; flush_multiplier = 1". After Jacob10383's
    # box_gcode.read_metadata.
    with open(path, 'rb') as stream:
        offset = max(0, os.fstat(stream.fileno()).st_size - limit)
        stream.seek(offset)
        tail = stream.read(limit)
    if offset:
        tail = tail.partition(b'\n')[2]
    return parse_flush_metadata(tail.decode('utf-8', 'replace'))


def parse_flush_metadata(text):
    fields = dict(re.findall(
        r"^;[ \t]*(flush_volumes_matrix|flush_multiplier)[ \t]*=[ \t]*"
        r"([^\r\n]*)", text, re.M))
    try:
        volumes = [float(v) for v in
                   fields.get('flush_volumes_matrix', '').split(',')]
    except ValueError:
        volumes = []
    size = int(math.sqrt(len(volumes)) + .5) if volumes else 0
    matrix = None
    if (size and size * size == len(volumes)
            and all(v >= 0. and not math.isinf(v) for v in volumes)):
        matrix = [volumes[i * size:(i + 1) * size] for i in range(size)]
    try:
        multiplier = float(fields.get('flush_multiplier', '1').split(',')[0])
    except ValueError:
        multiplier = 1.
    if not multiplier > 0.:
        multiplier = 1.
    return {'matrix': matrix, 'multiplier': multiplier}


# Creality RFID record layout: (field, offset, length) in the 40-character
# record (Jacob10383's box_protocol.RFID_RECORD_FIELDS)
RFID_RECORD_FIELDS = (
    ('month', 0x00, 1), ('day', 0x01, 2), ('year', 0x03, 2),
    ('supplier', 0x05, 4), ('batch', 0x09, 2), ('mat_id', 0x0b, 6),
    ('color', 0x11, 7), ('len', 0x18, 4), ('number', 0x1c, 6),
    ('reserve', 0x22, 6),
)


def decode_rfid_records(p):
    # p = status 02 "A:<40 chars>;B:...;" (ascii)
    if len(p) < 2 or p[0] != STATUS_OK:
        return {'error': status_label(p[0] if p else None)}
    try:
        text = bytes(p[2:]).decode('ascii')
    except UnicodeDecodeError:
        return {'error': 'record is not ASCII', 'raw': bytes(p).hex(' ')}
    records = {}
    for entry in text.split(';'):
        if ':' not in entry:
            continue
        slot, record = entry.split(':', 1)
        record = record.strip('\x00')
        fields = {}
        if (len(record) == 0x28
                and record.strip().lower() not in ('busy', 'none')):
            fields = dict((name, record[o:o + n])
                          for name, o, n in RFID_RECORD_FIELDS)
        records[slot.strip()] = {'record': record, 'fields': fields}
    return {'records': records}


class K2CFS:
    # name -> (payload bytes, decoder method name)
    # Payload EXCLUDES addr/len/crc (k2_motor_bus builds the frame).
    # Opcode map mined 2026-07-07 from 6 days / ~205k transactions of stock
    # klippy logs (name<->hex correlation). Read-only set ONLY. Payload
    # EXCLUDES addr/len/crc (k2_motor_bus builds the frame). Actuation opcodes
    # (SET_BOX_MODE ff04, TIGHTEN_UP ff0f, SET_PRE_LOADING ff0d, EXTRUDE/
    # RETRUDE) DELIBERATELY EXCLUDED — phase 3, supervised only.
    QUERIES = {
        # CONFIRMED live (valid-CRC response captured 2026-07-07):
        'BOX_STATE':       ([0x00, 0xa2], '_dec_box_state'),   # uniid table
        'MEASURING':       ([0xff, 0x0a], '_dec_measuring'),   # hub feed encoder
        # Stock box_wrapper communication_get_box_mode, confirmed live on
        # CFS firmware 1.5.0.  This is the authoritative activity mode;
        # MEASURING's trailing selection bytes are not a live mode report.
        'BOX_MODE':        ([0xff, 0x0c], '_dec_box_mode'),
        # BUFFER_STATE ff05 CONFIRMED live 2026-07-07: byte tracks buffer arm
        # 0=slack 1=mid 2=tensioned (cycled as Grant moved the arm). This is
        # THE runout/feed signal for phase 2.
        'BUFFER_STATE':    ([0xff, 0x05], '_dec_buffer'),
        # MINED (frame uniquely correlated to named cmd; raw decode until
        # validated live):
        'REMAIN_LEN':      ([0xff, 0x03, 0x0f], '_dec_remain'),
        'FILAMENT_SENSOR': ([0xff, 0x08, 0x00], '_dec_filament'),
        'HARDWARE_STATUS': ([0xff, 0x15], '_dec_raw'),
        'VERSION_SN':      ([0xff, 0x14], '_dec_version_sn'),
        # Hub measuring wheel, big-endian float mm (Jacob10383's
        # query_encoder)
        'MEASURING_WHEEL': ([0xff, 0x0e, 0x01], '_dec_wheel'),
    }
    # ff 0a box_state byte (Jacob10383's BOX_STATE_*; the same numbering as
    # Creality's GET_BOX_MODE table)
    BOX_STATE_NAMES = {0: 'idle', 1: 'preload', 2: 'print', 3: 'reload',
                       4: 'error', 5: 'test'}
    BOX_STATE_PRINT = 2
    BOX_STATE_ERROR = 4
    # ff 0a SLOT_EVENT reply: one byte per slot
    SLOT_EVENT_NAMES = {1: 'inserted', 2: 'removed', 3: 'rfid_ready'}
    # Slot events queue in the box; read until a normal reply comes back
    STATE_EVENT_DRAIN = 4
    NUM_SLOTS = 4
    SLOT_MASKS = {'A': 0x01, 'B': 0x02, 'C': 0x04, 'D': 0x08}
    MODE_PRINT = 0x00
    MODE_IDLE = 0x01
    BOX_MODE_IDLE = 0x00
    BOX_MODE_PRELOADING = 0x01
    BOX_MODE_PRINTING = 0x02
    BOX_MODE_WRAPPERING = 0x03
    BOX_MODE_ERROR = 0x04
    BOX_MODE_TEST = 0x05
    # SET_BOX_MODE's final byte selects routing behaviour (PRINT/IDLE).
    # GET_BOX_MODE reports the separate load/unload operation engine. Stock
    # logs and a live slot-C start both show that engine returning IDLE once
    # steady print feed is armed; PRINTING is a transient operation state.
    TENSION_ENABLE = 0x00
    TENSION_DISABLE = 0x01

    def __init__(self, config):
        self.printer = config.get_printer()
        self.addr = config.getint('addr', 1, minval=1, maxval=127)
        # cfs_fitted: auto (default) works like stock: no CFS found at
        # startup means the rear spool holder (external spool), and a CFS
        # plugged in later is picked up while idle. True expects a CFS and
        # reports when it does not answer; False never talks to one. Without
        # a CFS feed/preload calls are no-ops, T0 just prints, and commands
        # that need the box say so.
        mode = config.get('cfs_fitted', 'auto').strip().lower()
        modes = {'auto': 'auto', 'true': 'yes', 'yes': 'yes', '1': 'yes',
                 'false': 'no', 'no': 'no', '0': 'no'}
        if mode not in modes:
            raise config.error("cfs_fitted must be auto, True or False")
        self.fitted_mode = modes[mode]
        self.fitted = self.fitted_mode != 'no'
        self.reprobe_interval = config.getfloat(
            'cfs_reprobe_interval', 60., minval=10.)
        self.ready_time = 0.
        self.allow_actuation = config.getboolean('allow_actuation', False)
        self.purge_safe_y = config.getfloat('purge_safe_y', 261.)
        self.purge_entry_x = config.getfloat('purge_entry_x', 131.)
        self.purge_gate_x = config.getfloat('purge_gate_x', 127.)
        self.purge_x = config.getfloat('purge_x', 115.)
        self.purge_y = config.getfloat('purge_y', 291.5)
        self.purge_return_x = config.getfloat('purge_return_x', 205.)
        self.purge_position_tolerance = config.getfloat(
            'purge_position_tolerance', 2., minval=0., maxval=10.)
        self.purge_speed = config.getfloat(
            'purge_speed', 100., above=0.)
        self.purge_min_z = config.getfloat('purge_min_z', 5.)
        # Height added above the print before a mid-print tool change moves
        self.toolchange_lift = config.getfloat(
            'toolchange_lift', 2., minval=0.)
        # How far below the flush temperature a tool change may start
        # loading (the nozzle finishes heating during the CFS feed)
        self.toolchange_temp_margin = config.getfloat(
            'toolchange_temp_margin', 3., minval=0., maxval=10.)
        # Pull back right after the flush so the melt chamber is not left
        # pressurised (Bambu and stock both retract after flushing). A tool
        # change pushes it back in over the print before resuming.
        self.purge_retract_length = config.getfloat(
            'purge_retract_length', 1.5, minval=0., maxval=5.)
        self.purge_retract_speed = config.getfloat(
            'purge_retract_speed', 30., above=0., maxval=60.)
        # Stock blasts the part fan before the post-purge wipe: the strand
        # sets and snaps off at the nozzle instead of stretching into a
        # string. 0 disables.
        self.purge_cool_time = config.getfloat(
            'purge_cool_time', 5., minval=0., maxval=10.)
        self.purge_cool_fan = config.getfloat(
            'purge_cool_fan', 1., minval=0., maxval=1.)
        # Fans stopped for a mid-print change, like stock BOX_SAVE_FAN: with
        # the part and side fans blowing the purge sets into a long string
        # instead of dropping into the bucket.
        self.purge_fans = config.getlist(
            'purge_fans', ('fan', 'fan_generic auxiliary_fan'))
        self.purge_extrude_length = config.getfloat(
            'purge_extrude_length', 140., above=0., maxval=240.)
        self.purge_extrude_speed = config.getfloat(
            'purge_extrude_speed', 6., above=0., maxval=9.58)
        self.purge_chunk_length = config.getfloat(
            'purge_chunk_length', 90., above=0., maxval=90.)
        self.purge_slow_fraction = config.getfloat(
            'purge_slow_fraction', .2, above=0., maxval=.5)
        self.purge_fast_multiplier = config.getfloat(
            'purge_fast_multiplier', 1.2, minval=1., maxval=1.2)
        self.purge_followup_chunk_length = config.getfloat(
            'purge_followup_chunk_length', 70., above=0., maxval=70.)
        self.purge_minimum_actual_length = config.getfloat(
            'purge_minimum_actual_length', 160.,
            minval=160., maxval=160.)
        self.clean_plate_x = config.getfloat('clean_plate_x', 155.)
        self.clean_left_x = config.getfloat('clean_left_x', 127.)
        self.clean_right_x = config.getfloat('clean_right_x', 137.)
        self.clean_circle_left_x = config.getfloat(
            'clean_circle_left_x', 146.)
        self.clean_circle_right_x = config.getfloat(
            'clean_circle_right_x', 154.)
        self.clean_y = config.getfloat('clean_y', 291.5)
        self.clean_speed = config.getfloat(
            'clean_speed', 200., above=0., maxval=200.)
        self.clean_circle_speed = config.getfloat(
            'clean_circle_speed', 166.666667, above=0., maxval=166.666667)
        self.clean_accel = config.getfloat(
            'clean_accel', 5000., above=0., maxval=5000.)
        self.cutter_x = config.getfloat('cutter_x', 10.)
        # Runout refill: filament pushed through at the chute before the
        # new slot loads (stock runout_gcode pushes E30)
        self.runout_push_length = config.getfloat(
            'runout_push_length', 30., minval=0., maxval=60.)
        # Runout refill purge (mm). Both slots hold the same material and
        # colour, so only the butt joint with the old tail and the air gap
        # have to clear: the load already feeds 14 mm, then this short
        # purge (Jacob10383's runout primes 20 mm). 0 = the full stock
        # purge, as before.
        self.runout_purge_length = config.getfloat(
            'runout_purge_length', 40., minval=0., maxval=240.)
        if 0. < self.runout_purge_length < 30.:
            raise config.error(
                "runout_purge_length must be 0 or 30-240 (CFS_PURGE SHORT)")
        self.cutter_y = config.getfloat('cutter_y', 150.)
        self.cutter_speed = config.getfloat(
            'cutter_speed', 100., above=0.)
        self.cutter_cut_x = config.getfloat(
            'cutter_cut_x', -7.2, minval=-10., maxval=-5.)
        self.cutter_release_x = config.getfloat(
            'cutter_release_x', -1.7,
            minval=self.cutter_cut_x, maxval=0.)
        self.cutter_ram_speed = config.getfloat(
            'cutter_ram_speed', 500., above=0., maxval=500.)
        self.cutter_release_speed = config.getfloat(
            'cutter_release_speed', 150., above=0., maxval=150.)
        self.cutter_ram_accel = config.getfloat(
            'cutter_ram_accel', 3000., above=0., maxval=5000.)
        self.cutter_retract_length = config.getfloat(
            'cutter_retract_length', 2., above=0., maxval=2.)
        self.cutter_retract_speed = config.getfloat(
            'cutter_retract_speed', 6., above=0., maxval=6.)
        self.cutter_tip_length = config.getfloat(
            'cutter_tip_length', 7., above=0., maxval=7.)
        self.cutter_tip_push_speed = config.getfloat(
            'cutter_tip_push_speed', 9., above=0., maxval=9.)
        self.cutter_tip_pull_speed = config.getfloat(
            'cutter_tip_pull_speed', 5., above=0., maxval=5.)
        self.cutter_switch_pin = config.get(
            'cutter_switch_pin', '!nozzle_mcu:PB9')
        self.unload_fast_pulls = config.getint(
            'unload_fast_pulls', 3, minval=1, maxval=3)
        self.unload_pull_length = config.getfloat(
            'unload_pull_length', 10., above=0., maxval=20.)
        self.unload_pull_speed = config.getfloat(
            'unload_pull_speed', 6., above=0., maxval=6.)
        self.unload_final_length = config.getfloat(
            'unload_final_length', 10., above=0., maxval=10.)
        self.unload_final_speed = config.getfloat(
            'unload_final_speed', 1., above=0., maxval=1.)
        self.unload_rewind_pause_ms = config.getint(
            'unload_rewind_pause_ms', 0, minval=0, maxval=1000)
        self.unload_bus_timeout = config.getfloat(
            'unload_bus_timeout', 30., above=0., maxval=150.)
        self.unload_sensor_timeout = config.getfloat(
            'unload_sensor_timeout', 10., above=0., maxval=15.)
        # Pushes through the nozzle to clear a snapped piece left under the
        # sensor after an unload (0 tries = stop the print as before)
        self.unload_clear_tries = config.getint(
            'unload_clear_tries', 3, minval=0, maxval=5)
        self.unload_clear_length = config.getfloat(
            'unload_clear_length', 30., above=0., maxval=60.)
        self.unload_clear_speed = config.getfloat(
            'unload_clear_speed', 3., above=0., maxval=6.)
        self.sensor_poll_interval = config.getfloat(
            'sensor_poll_interval', .1, minval=.05, maxval=1.)
        self.load_bus_timeout = config.getfloat(
            'load_bus_timeout', 15., above=0., maxval=30.)
        self.load_timeout = config.getfloat(
            'load_timeout', 90., above=0., maxval=150.)
        self.load_sensor_grace = config.getfloat(
            'load_sensor_grace', 3., minval=0.)
        self.load_poll_interval = config.getfloat(
            'load_poll_interval', 1., minval=0.1, maxval=5.)
        # Between stage-5 sends, check the nozzle sensor this often. The
        # sensor is local (no bus traffic), so the stage-5 rate is
        # unchanged; stock reacts on the sensor callback, Jacob10383 polls
        # every 0.1 s. Set it equal to load_poll_interval for the old
        # check-once-per-send behaviour.
        self.load_arrival_poll_interval = config.getfloat(
            'load_arrival_poll_interval', .1, minval=.05, maxval=5.)
        # A box BUSY (0x03) reply is not a fault: wait and send again
        self.busy_retries = config.getint(
            'busy_retries', 2, minval=0, maxval=10)
        self.busy_retry_delay = config.getfloat(
            'busy_retry_delay', 1., minval=.1, maxval=5.)
        # EXTRUDE_ERR8 (hub wheel stopped) on a load or UNLOAD_MOTOR_BLOCKED
        # (0x19) on a rewind: flex the PTFE path with a toolhead move before
        # retrying, like Jacob10383's filament_retry_motion. Off = only the
        # plain retries, as before.
        self.stall_retry_motion = config.getboolean(
            'stall_retry_motion', False)
        x_max = 262.
        try:
            x_max = config.getsection('stepper_x').getfloat(
                'position_max', x_max, note_valid=False)
        except Exception:
            pass
        self.stall_retry_z_lift = config.getfloat(
            'stall_retry_z_lift', 10., minval=0., maxval=50.)
        self.stall_retry_moves = _parse_moves(
            config.get('stall_retry_moves', 'Y255, X220, Y40, X40'),
            x_max, self.purge_safe_y, config.error)
        # The box can report active mask 0 while it follows the extruder.
        # Accept feed_slot as the owner of the toolhead path when the nozzle
        # sensor sees filament and the hub sensor shows that slot.
        self.path_owner_from_feed_slot = config.getboolean(
            'path_owner_from_feed_slot', True)
        # Tool change purge from the print file's flush_volumes_matrix when
        # the slicer did not pass one (no PURGE=), like Jacob10383's
        # _purge_plan. Off = the fixed fallbacks, as before.
        self.toolchange_purge_from_file = config.getboolean(
            'toolchange_purge_from_file', False)
        self.flush_cache = None
        # Clog check from the hub measuring wheel (Jacob10383's _check_clog):
        # pause when the extruder has fed clog_extruder_length while the
        # wheel moved less than clog_wheel_reset_length
        self.clog_detection = config.getboolean('clog_detection', False)
        self.clog_extruder_length = config.getfloat(
            'clog_extruder_length', 80., minval=30.)
        # The box refills the buffer in ~25-30 mm chunks
        self.clog_wheel_reset_length = config.getfloat(
            'clog_wheel_reset_length', 18., above=0.)
        self.clog_baseline = None
        self.load_engage_length = config.getfloat(
            'load_engage_length', 5., above=0., maxval=5.)
        self.load_engage_speed = config.getfloat(
            'load_engage_speed', 1., above=0., maxval=1.)
        self.load_extrude_length = config.getfloat(
            'load_extrude_length', 9., above=0., maxval=9.)
        self.load_extrude_speed = config.getfloat(
            'load_extrude_speed', 3., above=0., maxval=3.)
        self.load_box_finish_length = config.getint(
            'load_box_finish_length', 3, minval=1, maxval=3)
        self.filament_sensor_name = config.get(
            'filament_sensor_name', 'filament_sensor')
        self.manage_runout_sensor = config.getboolean(
            'manage_runout_sensor', True)
        self.print_start_transaction = False
        # Background environment poll (temperature/humidity + selected slot).
        # Stock master-server polls the same query every ~4 s; we poll less
        # often and stay quiet while an actuation sequence is in progress.
        self.env_poll_interval = config.getfloat(
            'env_poll_interval', 10., minval=0.)
        # Poll interval while printing (0 = env_poll_interval)
        self.print_poll_interval = config.getfloat(
            'print_poll_interval', 0., minval=0.)
        self.last_actuation_time = 0.
        self.env_poll_failures = 0
        self.cutter_switch_state = None
        # Knife-sensor changes recorded during a cut, to tell whether the
        # blade went all the way through (None = not recording)
        self.cutter_events = None
        # Stock box.cfg calibration window (pre_cut_cal_pos_x and
        # check_cut_pos_x_min/max)
        self.cutter_cal_start_x = config.getfloat(
            'cutter_cal_start_x', -5., maxval=0.)
        self.cutter_cal_min_x = config.getfloat(
            'cutter_cal_min_x', -9.5, minval=-11.)
        self.cutter_cal_max_x = config.getfloat(
            'cutter_cal_max_x', -5.5, maxval=0.)
        self.bus = None
        self.status = {'fitted': self.fitted,
                       'fitted_setting': self.fitted_mode, 'detected': None,
                       'online': None if self.fitted else False,
                       'feed_enabled': None,
                       'feed_slot': None, 'last_loaded_slot': None,
                       'running_out': None, 'runout_next': '',
                       'runout_warning': ''}
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons(
            [self.cutter_switch_pin], self._handle_cutter_switch)
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'CFS_STATUS', self.cmd_CFS_STATUS,
            desc="Query CFS state (read-only)")
        self.gcode.register_command(
            'CFS_QUERY', self.cmd_CFS_QUERY,
            desc="Fire one named read-only CFS query")
        self.gcode.register_command(
            'CFS_FEED_START', self.cmd_CFS_FEED_START,
            desc="Arm buffer-controlled CFS feed for loaded filament")
        self.gcode.register_command(
            'CFS_FEED_STOP', self.cmd_CFS_FEED_STOP,
            desc="Disarm buffer-controlled CFS feed")
        self.gcode.register_command(
            'CFS_PURGE_POSITION', self.cmd_CFS_PURGE_POSITION,
            desc="Move to the K2 purge bucket using the stock safe route")
        self.gcode.register_command(
            'CFS_PURGE_RETURN', self.cmd_CFS_PURGE_RETURN,
            desc="Leave the K2 purge bucket using the stock safe route")
        self.gcode.register_command(
            'NOZZLE_WIPE', self.cmd_NOZZLE_WIPE,
            desc="Wipe the nozzle on the K2 rear silicone cleaner")
        self.gcode.register_command(
            'CFS_CUTTER_POSITION', self.cmd_CFS_CUTTER_POSITION,
            desc="Move to the K2 cutter staging point without cutting")
        self.gcode.register_command(
            'CFS_CUTTER_RETURN', self.cmd_CFS_CUTTER_RETURN,
            desc="Leave the K2 cutter staging point without cutting")
        self.gcode.register_command(
            'CFS_CUTTER_CALIBRATE', self.cmd_CFS_CUTTER_CALIBRATE,
            desc="Find where the knife sensor trips (toolhead empty)")
        self.gcode.register_command(
            'CFS_CUTTER_SENSOR', self.cmd_CFS_CUTTER_SENSOR,
            desc="Report the K2 cutter hall-sensor logical state")
        self.gcode.register_command(
            'CFS_UNLOAD', self.cmd_CFS_UNLOAD,
            desc="Blocked unsafe standalone CFS rewind")
        self.gcode.register_command(
            'CFS_CUT_UNLOAD', self.cmd_CFS_CUT_UNLOAD,
            desc="Cut and release filament before CFS rewind")
        self.gcode.register_command(
            'CFS_LOAD', self.cmd_CFS_LOAD,
            desc="Load a selected CFS slot using the stock K2 handshake")
        self.gcode.register_command(
            'CFS_PURGE', self.cmd_CFS_PURGE,
            desc="Purge loaded CFS filament using the stock K2 baseline")
        self.gcode.register_command(
            'CFS_PRINT_START', self.cmd_CFS_PRINT_START,
            desc="Select, load, and purge the initial CFS print slot")
        self.gcode.register_command(
            'CFS_CHANGE_SLOT', self.cmd_CFS_CHANGE_SLOT,
            desc="While idle: cut/rewind the loaded slot, load SLOT, purge")
        self.gcode.register_command(
            'CFS_TOOLCHANGE', self.cmd_CFS_TOOLCHANGE,
            desc="Change to another CFS slot during a print")
        self.gcode.register_command(
            'CFS_RUNOUT_REFILL', self.cmd_CFS_RUNOUT_REFILL,
            desc="From a runout pause: load SLOT (same filament) and resume")
        self.gcode.register_command(
            'CFS_PRELOAD', self.cmd_CFS_PRELOAD,
            desc="Turn CFS pre-loading and buffer tension on (idle) or off")
        self.gcode.register_command(
            'CFS_RFID_READ', self.cmd_CFS_RFID_READ,
            desc="Read the CFS RFID records and remaining (no motion)")
        for tool, slot in enumerate(('A', 'B', 'C', 'D')):
            def select_tool(gcmd, selected_slot=slot):
                self._select_tool(gcmd, selected_slot)
            self.gcode.register_command(
                'T%d' % tool, select_tool,
                desc="Arm already-selected CFS slot %s" % slot)

    def _handle_connect(self):
        self.bus = self.printer.lookup_object('k2_motor_bus')
        if self.fitted_mode == 'no':
            self.bus.cfs_detect = False

    def _handle_ready(self):
        reactor = self.printer.get_reactor()
        if self.fitted_mode == 'no':
            return
        self.ready_time = reactor.monotonic()
        reactor.register_timer(self._report_detection,
                               reactor.monotonic() + 5.)
        if self.env_poll_interval > 0.:
            reactor.register_timer(self._env_poll, reactor.monotonic() + 5.)
        # Stock idles with pre-loading on, so a spool pushed into a slot is
        # drawn in. Give k2_motor_bus time to address the CFS first.
        reactor.register_timer(self._idle_preload, reactor.monotonic() + 8.)

    def _report_detection(self, eventtime):
        # k2_motor_bus looks for the CFS at ready, after the motor wake
        reactor = self.printer.get_reactor()
        detected = getattr(self.bus, 'cfs_detected', None)
        if detected is None and eventtime - self.ready_time < 60.:
            return eventtime + 2.
        self.status['detected'] = detected
        uniid = getattr(self.bus, 'cfs_found_uniid', None)
        if uniid:
            self.status['uniid'] = uniid.hex()
        if detected or self.status.get('online'):
            return reactor.NEVER
        if self.fitted_mode == 'auto':
            self._set_fitted(False)
            self.gcode.respond_info(
                "No CFS found: printing from the rear spool holder (external"
                " spool). A CFS connected later is picked up while the"
                " printer is idle.")
            reactor.register_timer(self._reprobe,
                                   eventtime + self.reprobe_interval)
        else:
            self.gcode.respond_info(
                "No CFS answered at startup. Check its cable and power. If"
                " this printer has no CFS, set cfs_fitted: auto or False"
                " under [k2_cfs] in printer-overrides.cfg and restart.")
        return reactor.NEVER

    def _set_fitted(self, fitted):
        self.fitted = fitted
        self.status['fitted'] = fitted
        if not fitted:
            self.status.update({'online': False, 'feed_enabled': None,
                                'active_slot': None, 'active_mask': None})

    def _reprobe(self, eventtime):
        # cfs_fitted: auto with no CFS: look for one now and then while idle
        # (never while printing), the way stock notices a CFS plugged in
        if self.fitted:
            return self.printer.get_reactor().NEVER
        stats = self.printer.lookup_object('print_stats', None)
        state = stats.get_status(eventtime).get('state') if stats else ''
        if state not in ('printing', 'paused'):
            try:
                found = self.bus.address_cfs()
            except Exception:
                logging.exception("k2_cfs: CFS re-probe failed")
                found = False
            if found:
                self._set_fitted(True)
                self.status['detected'] = True
                uniid = getattr(self.bus, 'cfs_found_uniid', None)
                if uniid:
                    self.status['uniid'] = uniid.hex()
                self.env_poll_failures = 0
                self.gcode.respond_info(
                    "CFS connected: the CFS feeds the printer again")
                try:
                    self._restore_idle_preload()
                except Exception:
                    logging.exception("k2_cfs: pre-loading on failed")
                return self.printer.get_reactor().NEVER
        return eventtime + self.reprobe_interval

    def _require_cfs(self, gcmd):
        if not self.fitted:
            raise gcmd.error(
                "No CFS %s: the printer uses the rear spool holder. Use"
                " LOAD_FILAMENT / UNLOAD_FILAMENT."
                % ("found" if self.fitted_mode == 'auto'
                   else "on this printer (cfs_fitted: False)"))
        if (self.status.get('online') is False
                and getattr(self.bus, 'cfs_detected', None) is False):
            raise gcmd.error(
                "The CFS is not answering. Check its cable and power. If this"
                " printer has no CFS, set cfs_fitted: auto or False under"
                " [k2_cfs] in printer-overrides.cfg and restart.")

    def _idle_preload(self, eventtime):
        # Only once k2_motor_bus has found (and addressed) the CFS
        detected = getattr(self.bus, 'cfs_detected', None)
        if detected is None and eventtime - self.ready_time < 60.:
            return eventtime + 2.
        if not detected:
            return self.printer.get_reactor().NEVER
        try:
            self._restore_idle_preload()
        except Exception:
            logging.exception("k2_cfs: pre-loading on at ready failed")
        return self.printer.get_reactor().NEVER

    def _restore_idle_preload(self):
        self._set_preloading(True)
        self._run_actuation('TIGHTEN_UP ENABLE',
                            [0xff, 0x0f, self.TENSION_ENABLE])

    def _printing(self):
        stats = self.printer.lookup_object('print_stats', None)
        return (stats is not None and
                stats.get_status(0.).get('state') == 'printing')

    def _env_poll(self, eventtime):
        interval = self.env_poll_interval
        if not self.fitted:
            return eventtime + interval
        if self.status.get('online') and self.status.get('detected') is False:
            # Answered later than the startup check
            self.status['detected'] = True
        if self.print_poll_interval and self._printing():
            interval = self.print_poll_interval
        if (self.print_start_transaction
                or eventtime - self.last_actuation_time < 5.):
            self.clog_baseline = None
            return eventtime + interval
        try:
            m = self._measuring()
        except Exception:
            logging.exception("k2_cfs: environment poll failed")
            m = {'error': 'exception'}
        if 'temperature' in m or 'box_state' in m:
            self.env_poll_failures = 0
            self.status['online'] = True
            for key in ('temperature', 'humidity', 'active_mask',
                        'active_slots', 'active_slot', 'selection_setting'):
                if key in m:
                    self.status[key] = m[key]
        else:
            self.env_poll_failures += 1
            if self.env_poll_failures >= 3:
                self.status['online'] = False
        self._check_running_out()
        try:
            self._check_clog(eventtime)
        except Exception:
            logging.exception("k2_cfs: clog check failed")
            self.clog_baseline = None
        return eventtime + interval

    def _check_clog(self, eventtime):
        # Jacob10383's _check_clog: while the box feeds the print, the hub
        # wheel turns as the buffer is refilled in ~25-30 mm chunks. If the
        # extruder has pushed clog_extruder_length since the wheel last
        # moved, nothing is coming through from the CFS.
        # A spool that is running out stops the wheel too: leave that to
        # the runout handling.
        feeding = (self.status.get('feed_enabled')
                   or self.status.get('box_state') == self.BOX_STATE_PRINT)
        if (not self.clog_detection or not self._printing() or not feeding
                or not self.status.get('feed_slot')
                or self.status.get('running_out')
                or self.status.get('box_runout')):
            self.clog_baseline = None
            return
        wheel = self._run_query('MEASURING_WHEEL').get('wheel_mm')
        if wheel is None:
            return
        toolhead = self.printer.lookup_object('toolhead')
        mcu = self.printer.lookup_object('mcu')
        position = toolhead.get_extruder().find_past_position(
            mcu.estimated_print_time(eventtime))
        self.status['wheel_mm'] = round(wheel, 2)
        base = self.clog_baseline
        if base is None or abs(wheel - base[1]) > self.clog_wheel_reset_length:
            self.clog_baseline = (position, wheel)
            return
        extruded = position - base[0]
        if extruded <= self.clog_extruder_length:
            return
        self.clog_baseline = None
        msg = ("CFS clog check: the extruder fed %.0f mm but the CFS"
               " measuring wheel moved only %.1f mm, so filament is not"
               " coming through from slot %s. Print paused: check for a"
               " tangle or a jam between the CFS and the toolhead, then"
               " RESUME." % (extruded, abs(wheel - base[1]),
                             self.status.get('feed_slot')))
        self.status['last_clog'] = msg
        logging.info("k2_cfs: %s", msg)
        pause_resume = self.printer.lookup_object('pause_resume', None)
        if pause_resume is None:
            return
        pause_resume.send_pause_command()

        def pause(eventtime):
            try:
                self.gcode.respond_raw("!! " + msg)
                self.gcode.run_script("PAUSE")
            except Exception:
                logging.exception("k2_cfs: clog pause failed")
        self.printer.get_reactor().register_callback(pause)

    def _check_running_out(self):
        # Early runout warning. The end of a spool passes the slot's entry
        # sensor while about a tube's length is still on its way to the
        # nozzle, so tell the user what will happen when it gets there.
        slot = self.status.get('feed_slot')
        printing = self._printing()
        if not printing or not slot or not self.status.get('feed_enabled'):
            if self.status.get('running_out'):
                self._clear_runout_warning()
            return
        if self.status.get('running_out') == slot:
            return
        try:
            loaded = self._run_query('FILAMENT_SENSOR').get('slots_loaded')
        except Exception:
            logging.exception("k2_cfs: runout check failed")
            return
        if not loaded or len(loaded) < 4:
            return
        self.status['slots_loaded'] = loaded
        if loaded[ord(slot) - ord('A')]:
            return
        pick = self._matching_slot(slot, loaded)
        macro = self.printer.lookup_object('gcode_macro _RUNOUT_REFILL', None)
        enabled = macro is None or int(
            macro.variables.get('enabled', 1) or 0) == 1
        if pick and enabled:
            msg = ("Slot %s is running out. Slot %s (same filament) takes over"
                   " automatically when it does." % (slot, pick))
        else:
            msg = ("Slot %s is running out and no other slot has the same"
                   " filament. The print will pause when it runs out: have a"
                   " new spool ready." % slot)
        self.status['running_out'] = slot
        self.status['runout_next'] = pick if (pick and enabled) else ''
        self.status['runout_warning'] = msg
        self.gcode.respond_info(msg)
        display = self.printer.lookup_object('display_status', None)
        if display is not None:
            display.message = msg
        logging.info("k2_cfs: %s", msg)

    def _clear_runout_warning(self):
        display = self.printer.lookup_object('display_status', None)
        if (display is not None and self.status.get('runout_warning')
                and display.message == self.status['runout_warning']):
            display.message = None
        self.status['running_out'] = None
        self.status['runout_next'] = ''
        self.status['runout_warning'] = ''

    def _matching_slot(self, slot, loaded=None):
        # The slot _RUNOUT_REFILL would pick: same material type and colour,
        # with a record, and filament at its entry sensor when known
        slots = self.printer.lookup_object('hark_cfs_slots', None)
        if slots is None:
            return None
        records = slots.get_status(0.)['slots']
        old = records.get(slot, {})
        want_type = (old.get('type') or '').upper()
        want_colour = (old.get('color') or '').upper()
        if not want_type:
            return None
        for index, name in enumerate('ABCD'):
            rec = records.get(name, {})
            if (name != slot and rec.get('loaded')
                    and (rec.get('type') or '').upper() == want_type
                    and (rec.get('color') or '').upper() == want_colour
                    and (loaded is None or loaded[index])):
                return name
        return None

    def get_status(self, eventtime):
        out = dict(self.status)
        out['cutter_switch_state'] = self.cutter_switch_state
        return out

    def _handle_cutter_switch(self, eventtime, state):
        # Preserve the pin resolver's logical state exactly until physical
        # open/pressed semantics have been validated on the K2.
        self.cutter_switch_state = bool(state & 0x01)
        if self.cutter_events is not None:
            self.cutter_events.append((eventtime, self.cutter_switch_state))
        logging.info("k2_cfs: cutter sensor %s",
                     "returned" if self.cutter_switch_state else "pressed")

    # --- decoders: (payload_bytes_after_len, ) -> dict ---
    @staticmethod
    def _payload(resp):
        # resp = f7 addr len <payload...> crc ; validate shape loosely
        if len(resp) >= 5 and resp[0] == 0xf7:
            ln = resp[2]
            return bytes(resp[3:3 + ln - 1])
        return None

    def _dec_box_state(self, p):
        # observed: 00 a2 01 00 <12-byte uniid table>
        return {'echo': p[:2].hex(), 'flags': p[2:4].hex(),
                'uniid': p[4:16].hex(' ')}

    def _dec_measuring(self, p):
        # observed: <status> 0a <temp> <humidity> 00 <state> <mask> <setting>.
        # Bytes 2/3 are the CFS AHT10 reading: degrees C and relative
        # humidity % (matched against stock master-server's
        # boxTemp/dry_and_humidity over a day of stock logs, 2026-10-24).
        # Byte 5 is the box state (Jacob10383's decode: 2 = PRINT while the
        # box follows the extruder). Bytes 6/7 retain the selected-slot
        # mask (his "downstream" path mask) and selection setting (which he
        # also reads as a slot mask). They do not change when stock's
        # global IDLE command (ff 04 00 01) succeeds, so they must not be
        # used as the live CFS mode. GET_BOX_MODE (ff 0c) is authoritative
        # for that.
        # Status 0x30 (SLOT_EVENT) replaces the whole body with one event
        # byte per slot (1 inserted, 2 removed, 3 RFID ready), and status
        # 0x50 (RUNOUT) is the box's own runout flag on a normal body.
        # Before 10-27 an event reply was read as temperature/humidity.
        status = p[0] if len(p) else None
        out = {'raw': p.hex(' '), 'status': status}
        body = p[2:]
        if status == STATUS_SLOT_EVENT:
            if len(body) == self.NUM_SLOTS:
                out['slot_events'] = dict(
                    (name, self.SLOT_EVENT_NAMES.get(body[i], body[i]))
                    for i, name in enumerate('ABCD') if body[i])
            return out
        if status in (0x01, 0x02) or len(body) < 2:
            return out
        out['temperature'] = struct.unpack('b', bytes(body[:1]))[0]
        out['humidity'] = body[1]
        if len(body) >= 6:
            state, mask, selection_setting = body[3], body[4], body[5]
            slots = [name for name, bit in self.SLOT_MASKS.items()
                     if mask & bit]
            out.update({
                'box_state': state,
                'box_state_name': self.BOX_STATE_NAMES.get(state, 'unknown'),
                'box_runout': status == STATUS_RUNOUT,
                'active_mask': mask,
                'active_slots': slots,
                'active_slot': slots[0] if len(slots) == 1 else None,
                'selection_setting': selection_setting,
            })
        return out

    def _dec_wheel(self, p):
        # observed: 00 0e <4-byte big-endian float>, mm the hub wheel has
        # measured (Jacob10383's query_encoder)
        if len(p) >= 6 and p[0] == STATUS_OK:
            return {'wheel_mm': struct.unpack('>f', bytes(p[2:6]))[0],
                    'raw': p.hex(' ')}
        return {'raw': p.hex(' ')}

    def _measuring(self):
        # MEASURING with any queued slot events read off first
        m = {}
        for unused in range(self.STATE_EVENT_DRAIN):
            m = self._run_query('MEASURING')
            events = m.get('slot_events')
            if events is None:
                break
            if events:
                text = ", ".join("%s %s" % (s, e)
                                 for s, e in sorted(events.items()))
                self.status['last_slot_event'] = text
                logging.info("k2_cfs: slot event: %s", text)
        else:
            logging.info("k2_cfs: slot events did not drain after %d reads",
                         self.STATE_EVENT_DRAIN)
        if 'box_state' in m:
            if m['box_runout'] and not self.status.get('box_runout'):
                logging.info("k2_cfs: the box reports a runout (0x50)")
            for key in ('box_state', 'box_state_name', 'box_runout'):
                self.status[key] = m[key]
            self.status['box_status_code'] = m['status']
        return m

    def _path_mask(self, measuring):
        # Mask of the slot whose filament is in the toolhead path. The box
        # reports mask 0 while it just follows the extruder, which used to
        # fail tool changes. Then the slot we armed (feed_slot) owns the
        # path when the nozzle sensor sees filament and the hub sensor
        # shows that slot's filament (Jacob10383 keeps his path owner while
        # the nozzle sensor sees filament).
        mask = measuring.get('active_mask', 0)
        slot = self.status.get('feed_slot')
        if (mask or not self.path_owner_from_feed_slot
                or slot not in self.SLOT_MASKS
                or measuring.get('box_state') == self.BOX_STATE_ERROR
                or self._nozzle_has_filament() is not True):
            return mask
        try:
            hub = self._hub_mask()
        except Exception:
            logging.exception("k2_cfs: hub read for the path owner failed")
            return mask
        if hub != self.SLOT_MASKS[slot]:
            logging.info("k2_cfs: mask 0, feed slot %s, hub 0x%02x: no owner",
                         slot, hub)
            return mask
        logging.info("k2_cfs: box reports mask 0; slot %s owns the path "
                     "(nozzle sensor and hub)", slot)
        return self.SLOT_MASKS[slot]

    def _dec_box_mode(self, p):
        # observed live: 00 0c 00 after stock IDLE command.  Creality's own
        # mode table is IDLE=0, PRELOADING=1, PRINTING=2, WRAPPERING=3,
        # ERR=4, TEST=5.
        if len(p) >= 3:
            code = p[2]
            names = {
                self.BOX_MODE_IDLE: 'idle',
                self.BOX_MODE_PRELOADING: 'preloading',
                self.BOX_MODE_PRINTING: 'printing',
                self.BOX_MODE_WRAPPERING: 'wrappering',
                self.BOX_MODE_ERROR: 'error',
                self.BOX_MODE_TEST: 'test',
            }
            return {
                'box_mode_code': code,
                'box_mode': names.get(code, 'unknown'),
                'raw': p.hex(' '),
            }
        return {'raw': p.hex(' ')}

    def _dec_version_sn(self, p):
        # observed: 00 14 <ascii "142" + serial> -> fw 1.4.2, sn ...
        try:
            s = bytes(p[2:]).decode('ascii', 'replace')
        except Exception:
            return {'raw': p.hex(' ')}
        ver = '.'.join(s[:3]) if len(s) >= 3 else s
        return {'firmware': ver, 'sn': s[3:], 'raw_ascii': s}

    def _dec_filament(self, p):
        # observed: 00 08 <mask> ; mask bit i = slot i has filament
        if len(p) >= 3:
            mask = p[2]
            slots = [bool(mask >> i & 1) for i in range(self.NUM_SLOTS)]
            return {'mask': '0x%02x' % mask, 'slots_loaded': slots}
        return {'raw': p.hex(' ')}

    def _dec_buffer(self, p):
        # observed: 00 05 <state> ; state 0=slack 1=mid 2=tensioned
        if len(p) >= 3:
            names = {0: 'slack', 1: 'mid', 2: 'tensioned'}
            return {'buffer_state': p[2], 'buffer': names.get(p[2], '?')}
        return {'raw': p.hex(' ')}

    def _dec_remain(self, p):
        # observed: 00 03 <s0> <s1> <s2> <s3> ; per-slot remaining (0-100)
        vals = list(p[2:2 + self.NUM_SLOTS])
        return {'remain': vals}

    def _dec_raw(self, p):
        return {'raw': p.hex(' ')}

    # --- gcode ---
    def _run_query(self, name):
        payload, dec = self.QUERIES[name]
        frame, resp = self.bus.xfer(self.addr, payload)
        p = self._payload(resp)
        if p is None:
            return {'error': 'no/short response',
                    'resp': resp.hex(' ') if resp else '(none)'}
        return getattr(self, dec)(p)

    def cmd_CFS_STATUS(self, gcmd):
        self._require_cfs(gcmd)
        out = []
        online = False
        results = {}
        for name in self.QUERIES:
            r = (self._measuring() if name == 'MEASURING'
                 else self._run_query(name))
            results[name] = r
            if 'error' not in r:
                online = True
            out.append("%s: %s" % (name, r))
        # surface the useful decoded fields to get_status (Fluidd/Moonraker)
        st = {'online': online}
        v = results.get('VERSION_SN', {})
        if 'firmware' in v:
            st['firmware'], st['sn'] = v['firmware'], v['sn']
        f = results.get('FILAMENT_SENSOR', {})
        if 'slots_loaded' in f:
            st['slots_loaded'] = f['slots_loaded']
        rl = results.get('REMAIN_LEN', {})
        if 'remain' in rl:
            st['remain'] = rl['remain']
        b = results.get('BUFFER_STATE', {})
        if 'buffer_state' in b:
            st['buffer_state'], st['buffer'] = b['buffer_state'], b['buffer']
        m = results.get('MEASURING', {})
        for key in ('temperature', 'humidity', 'active_mask', 'active_slots',
                    'active_slot', 'selection_setting'):
            if key in m:
                st[key] = m[key]
        bm = results.get('BOX_MODE', {})
        for key in ('box_mode_code', 'box_mode'):
            if key in bm:
                st[key] = bm[key]
        # Feed arming is a host-issued setting, not the instantaneous activity
        # returned by GET_BOX_MODE. Preserve the locally known state instead
        # of inventing it from MEASURING's stale selection bytes.
        st['feed_enabled'] = self.status.get('feed_enabled')
        # Update rather than replace: feed_slot and the load history must
        # survive a status query
        self.status.update(st)
        gcmd.respond_info("CFS addr %d\n%s" % (self.addr, "\n".join(out)))

    def cmd_CFS_QUERY(self, gcmd):
        self._require_cfs(gcmd)
        name = gcmd.get('CMD').upper()
        if name not in self.QUERIES:
            raise gcmd.error("Unknown query %s. Known: %s"
                             % (name, ", ".join(self.QUERIES)))
        gcmd.respond_info("%s: %s" % (name, self._run_query(name)))

    def _require_actuation(self, gcmd):
        if not self.allow_actuation:
            raise gcmd.error(
                "CFS actuation is disabled; set allow_actuation: true")

    def _run_actuation(self, name, payload, timeout=0.2):
        reactor = self.printer.get_reactor()
        opcode = payload[1]
        busy = 0
        while True:
            start = self.last_actuation_time = reactor.monotonic()
            frame, resp = self.bus.xfer(
                self.addr, payload, timeout=timeout)
            logging.info("k2_cfs: %s took %.2fs resp %s", name,
                         reactor.monotonic() - start,
                         resp.hex(' ') if resp else '(none)')
            self.last_resp = resp
            p = self._payload(resp)
            if p is not None and len(p) >= 2 and p[1] == opcode:
                if p[0] == STATUS_OK:
                    return frame, resp
                if p[0] == STATUS_BUSY and busy < self.busy_retries:
                    # Not a fault: the box is finishing another operation
                    busy += 1
                    logging.info("k2_cfs: %s: box busy, retry %d/%d", name,
                                 busy, self.busy_retries)
                    reactor.pause(reactor.monotonic() + self.busy_retry_delay)
                    continue
                raise CFSStatusError(
                    "%s failed: box status %s (sent %s resp %s)"
                    % (name, status_label(p[0]), frame.hex(' '),
                       resp.hex(' ')), p[0])
            raise RuntimeError(
                "%s failed: sent %s resp %s"
                % (name, frame.hex(' '),
                   resp.hex(' ') if resp else '(none)'))

    def _nozzle_has_filament(self):
        sensor = self.printer.lookup_object(
            'filament_switch_sensor %s' % self.filament_sensor_name, None)
        if sensor is None:
            return None
        return bool(sensor.get_status(0.).get('filament_detected'))

    def _set_runout_sensor_enabled(self, enabled):
        if not self.manage_runout_sensor:
            return
        self.gcode.run_script_from_command(
            "SET_FILAMENT_SENSOR SENSOR=%s ENABLE=%d"
            % (self.filament_sensor_name, int(enabled)))

    def _wait_nozzle_filament(self, expected, timeout, poll=None):
        reactor = self.printer.get_reactor()
        deadline = reactor.monotonic() + timeout
        while True:
            if self._nozzle_has_filament() is expected:
                return True
            now = reactor.monotonic()
            if now >= deadline:
                return False
            reactor.pause(
                min(deadline, now + (poll or self.sensor_poll_interval)))

    def _require_idle(self, gcmd):
        # The standalone cut/load/purge commands remain blocked once a print
        # starts. CFS_PRINT_START is the sole exception: it owns a bounded,
        # single-threaded transaction invoked by START_PRINT before model
        # extrusion begins.
        if self.print_start_transaction:
            return
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            return
        state = print_stats.get_status(0.).get('state', '')
        if state in ('printing', 'paused'):
            raise gcmd.error(
                "CFS operation refused while print state is %s" % state)

    def _require_print_start(self, gcmd):
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            raise gcmd.error(
                "CFS_PRINT_START requires virtual_sdcard print state")
        state = print_stats.get_status(0.).get('state', '')
        if state != 'printing':
            raise gcmd.error(
                "CFS_PRINT_START is only available during START_PRINT "
                "(state is %s)" % (state or "unknown"))

    def _move_to_cut_start(self):
        feedrate = self.purge_speed * 60.
        self.gcode.run_script_from_command(
            "G90\n"
            "G1 Y%.3f F%.3f\n"
            "G1 X%.3f F%.3f\n"
            "M400"
            % (self.purge_safe_y, feedrate,
               self.purge_return_x, feedrate))

    def _require_hot_extruder(self, gcmd):
        extruder = self.printer.lookup_object('extruder', None)
        if extruder is None:
            raise gcmd.error("Extruder object is unavailable")
        heater = extruder.get_heater()
        if not heater.can_extrude:
            status = heater.get_status(0.)
            raise gcmd.error(
                "CFS unload requires a preheated extruder "
                "(current %.1fC, target %.1fC)"
                % (status.get('temperature', 0.),
                   status.get('target', 0.)))

    def _best_effort_stop(self, mask):
        try:
            self._run_actuation(
                'TIGHTEN_UP DISABLE',
                [0xff, 0x0f, self.TENSION_DISABLE])
            # Stock BOX_SET_CURRENT_BOX_IDLE_MODE uses a zero selection byte.
            # A slot mask here is accepted at the transport layer but does
            # not transition firmware 1.5.0 to IDLE.
            self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
        except Exception:
            pass

    def _start_feed(self, gcmd, slot, require_confirm, require_selected):
        self._require_actuation(gcmd)
        if (require_confirm
                and gcmd.get_int(
                    'CONFIRM', 0, minval=0, maxval=1) != 1):
            raise gcmd.error(
                "CFS_FEED_START requires CONFIRM=1")
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "No filament at nozzle; loaded-path feed only")

        measuring = self._measuring()
        old_mask = self._path_mask(measuring)
        if require_selected and old_mask != mask:
            raise gcmd.error(
                "T%d requests slot %s but CFS selected mask is 0x%02x; "
                "physical tool change is not implemented"
                % (ord(slot) - ord('A'), slot, old_mask))
        if old_mask not in (0, mask):
            raise gcmd.error(
                "CFS selected mask is 0x%02x; refusing slot %s"
                % (old_mask, slot))

        sent = []
        try:
            frame, _ = self._run_actuation(
                'SET_BOX_MODE PRINT',
                [0xff, 0x04, mask, self.MODE_PRINT])
            sent.append(frame.hex(' '))
            frame, _ = self._run_actuation(
                'TIGHTEN_UP ENABLE',
                [0xff, 0x0f, self.TENSION_ENABLE])
            sent.append(frame.hex(' '))
        except Exception as exc:
            # Best-effort fail-safe: disarm tension and return this slot idle.
            self._best_effort_stop(mask)
            raise gcmd.error(str(exc))

        verified = self._measuring()
        box_mode = self._run_query('BOX_MODE')
        if (self._path_mask(verified) != mask
                or box_mode.get('box_mode_code')
                != self.BOX_MODE_IDLE):
            self._best_effort_stop(mask)
            raise gcmd.error(
                "CFS did not arm steady slot %s feed: selection=%s "
                "operation_mode=%s"
                % (slot, verified, box_mode))
        self.status.update({
            'feed_enabled': True,
            'active_mask': mask,
            'active_slots': [slot],
            'active_slot': slot,
            'box_mode_code': box_mode.get('box_mode_code'),
            'box_mode': box_mode.get('box_mode'),
            # The slot feeding the print. Kept when the box deselects a slot
            # that ran out, so the runout refill knows what to match.
            'feed_slot': slot,
        })
        gcmd.respond_info(
            "CFS feed armed for slot %s\n%s"
            % (slot, "\n".join(sent)))

    def cmd_CFS_FEED_START(self, gcmd):
        if not self.fitted:
            gcmd.respond_info("No CFS: the extruder feeds from the rear spool")
            return
        slot = gcmd.get('SLOT').upper()
        self._start_feed(
            gcmd, slot, require_confirm=True, require_selected=False)

    def _select_tool(self, gcmd, slot):
        if not self.fitted:
            if slot != 'A':
                raise gcmd.error(
                    "No CFS on this printer: only T0 (the rear spool) exists")
            return
        self._start_feed(
            gcmd, slot, require_confirm=False, require_selected=True)
        gcmd.respond_info(
            "T%d selected already-loaded CFS slot %s"
            % (ord(slot) - ord('A'), slot))

    def cmd_CFS_FEED_STOP(self, gcmd):
        if not self.fitted:
            return
        self._require_actuation(gcmd)
        sent = []
        try:
            frame, _ = self._run_actuation(
                'TIGHTEN_UP DISABLE',
                [0xff, 0x0f, self.TENSION_DISABLE])
            sent.append(frame.hex(' '))
            frame, _ = self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
            sent.append(frame.hex(' '))
        except Exception as exc:
            raise gcmd.error(str(exc))
        verified = self._run_query('BOX_MODE')
        if verified.get('box_mode_code') != self.BOX_MODE_IDLE:
            raise gcmd.error(
                "CFS did not enter IDLE mode: %s" % verified)
        self.status.update({
            'feed_enabled': False,
            'box_mode_code': self.BOX_MODE_IDLE,
            'box_mode': 'idle',
        })
        gcmd.respond_info(
            "CFS feed disarmed\n%s" % "\n".join(sent))

    def _require_safe_motion(self, gcmd, command):
        self._require_actuation(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("%s requires CONFIRM=1" % command)
        toolhead = self.printer.lookup_object('toolhead')
        homed = toolhead.get_status(0.).get('homed_axes', '')
        position = toolhead.get_position()
        if gcmd.get_int('UNHOMED_Z', 0, minval=0, maxval=1):
            # Nozzle cleaning before Z is known (G28, NOZZLE_DEEP_CLEAN),
            # like stock's BOX_NOZZLE_CLEAN. The caller has dropped the bed
            # 5 mm, so only X and Y must be homed.
            if not all(axis in homed for axis in 'xy'):
                raise gcmd.error("%s requires X and Y homed" % command)
            return position
        if not all(axis in homed for axis in 'xyz'):
            raise gcmd.error(
                "CFS move requires X, Y, and Z homed")
        if position[2] < self.purge_min_z:
            raise gcmd.error(
                "CFS move requires Z >= %.3f (current %.3f)"
                % (self.purge_min_z, position[2]))
        return position

    def cmd_CFS_PURGE_POSITION(self, gcmd):
        self._require_safe_motion(gcmd, "CFS_PURGE_POSITION")
        feedrate = self.purge_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
            "G1 X%.3f F%.3f" % (self.purge_entry_x, feedrate),
            "G1 Y%.3f F%.3f" % (self.purge_y, feedrate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_gate_x, self.purge_y, feedrate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_x, self.purge_y, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS purge position reached: X%.3f Y%.3f"
            % (self.purge_x, self.purge_y))

    def _ensure_purge_position(self, gcmd):
        position = self._require_safe_motion(gcmd, "CFS_PURGE_POSITION")
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - self.purge_x) <= tolerance
                and abs(position[1] - self.purge_y) <= tolerance):
            return
        self.cmd_CFS_PURGE_POSITION(gcmd)

    def cmd_CFS_PURGE_RETURN(self, gcmd):
        position = self._require_safe_motion(gcmd, "CFS_PURGE_RETURN")
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - self.purge_x) > tolerance
                or abs(position[1] - self.purge_y) > tolerance):
            raise gcmd.error(
                "CFS purge return requires the toolhead at X%.3f Y%.3f "
                "within %.3fmm (current X%.3f Y%.3f)"
                % (self.purge_x, self.purge_y, tolerance,
                   position[0], position[1]))

        feedrate = self.purge_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
            "G1 X%.3f F%.3f" % (self.purge_return_x, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS purge return reached: X%.3f Y%.3f"
            % (self.purge_return_x, self.purge_safe_y))

    def cmd_NOZZLE_WIPE(self, gcmd):
        position = self._require_safe_motion(gcmd, "NOZZLE_WIPE")
        # PARK=0 ends the wipe over the chute instead of parking at the
        # return point, for back-to-back wipes (START_PRINT's cool-wipe).
        park = gcmd.get_int('PARK', 1, minval=0, maxval=1)
        travel_rate = self.purge_speed * 60.
        entry_script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, travel_rate),
            "G1 X%.3f F%.3f" % (self.purge_entry_x, travel_rate),
            "G1 Y%.3f F%.3f" % (self.purge_y, travel_rate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_gate_x, self.purge_y, travel_rate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_x, self.purge_y, travel_rate),
            "M400",
        ))
        # Already at the chute (the last wipe ended there): wipe in place
        # rather than driving out to the safe line and back in.
        tol = self.purge_position_tolerance
        if (position is not None
                and abs(position[0] - self.purge_x) <= tol
                and abs(position[1] - self.purge_y) <= tol):
            entry_script = "G90\nM400"
        toolhead = self.printer.lookup_object('toolhead')
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            raise gcmd.error("Unable to read current acceleration limit")
        saved = False
        accel_changed = False
        try:
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=NOZZLE_WIPE")
            saved = True
            accel_changed = True
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f" % self.clean_accel)
            self.gcode.run_script_from_command(entry_script)
            self.gcode.run_script_from_command(
                self._purge_wipe_script(bool(park)))
        except Exception as exc:
            raise gcmd.error("K2 nozzle wipe failed: %s" % exc)
        finally:
            if accel_changed:
                self.gcode.run_script_from_command(
                    "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=NOZZLE_WIPE")
        if park:
            gcmd.respond_info(
                "K2 nozzle wipe complete; parked at X%.3f Y%.3f"
                % (self.purge_return_x, self.purge_safe_y))
        else:
            gcmd.respond_info("K2 nozzle wipe complete; left at the chute")

    def _require_position(self, gcmd, position, expected_x, expected_y,
                          description):
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - expected_x) > tolerance
                or abs(position[1] - expected_y) > tolerance):
            raise gcmd.error(
                "%s requires the toolhead at X%.3f Y%.3f within %.3fmm "
                "(current X%.3f Y%.3f)"
                % (description, expected_x, expected_y, tolerance,
                   position[0], position[1]))

    def cmd_CFS_CUTTER_POSITION(self, gcmd):
        position = self._require_safe_motion(
            gcmd, "CFS_CUTTER_POSITION")
        self._require_position(
            gcmd, position, self.purge_return_x, self.purge_safe_y,
            "CFS cutter position")
        feedrate = self.cutter_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.cutter_y, feedrate),
            "G1 X%.3f F%.3f" % (self.cutter_x, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS cutter staging position reached: X%.3f Y%.3f "
            "(movement only; cutter not actuated)"
            % (self.cutter_x, self.cutter_y))

    def cmd_CFS_CUTTER_RETURN(self, gcmd):
        position = self._require_safe_motion(
            gcmd, "CFS_CUTTER_RETURN")
        self._require_position(
            gcmd, position, self.cutter_x, self.cutter_y,
            "CFS cutter return")
        feedrate = self.cutter_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
            "G1 X%.3f F%.3f" % (self.purge_return_x, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS cutter return reached: X%.3f Y%.3f"
            % (self.purge_return_x, self.purge_safe_y))

    def cmd_CFS_CUTTER_CALIBRATE(self, gcmd):
        # Stock K2 calibrates the cut by pushing the cutter lever slowly
        # from pre_cut_cal_pos_x until the knife sensor trips, and stores
        # that X (cut_pos_x, -6.10 on this printer under stock firmware).
        # Same here, reported only: set cutter_cut_x from the result.
        self._require_idle(gcmd)
        self._require_safe_motion(gcmd, "CFS_CUTTER_CALIBRATE")
        if (self._nozzle_has_filament() is True
                and not gcmd.get_int('FORCE', 0, minval=0, maxval=1)):
            raise gcmd.error(
                "Filament is at the nozzle: the calibration push would cut "
                "it. Unload first (or FORCE=1)")
        if self.cutter_switch_state is not True:
            raise gcmd.error("The cutter is not in its returned state")
        start = gcmd.get_float('START', self.cutter_cal_start_x, maxval=0.)
        stop = gcmd.get_float('STOP', self.cutter_cal_min_x, minval=-11.,
                              below=start)
        step = gcmd.get_float('STEP', .05, minval=.01, maxval=.5)
        push_rate = gcmd.get_float('SPEED', 5., above=0., maxval=20.) * 60.
        travel_rate = self.cutter_speed * 60.
        reactor = self.printer.get_reactor()
        trip = None
        self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=K2_CUT_CAL")
        try:
            self.gcode.run_script_from_command(
                "G90\nG1 Y%.3f F%.3f\nG1 X%.3f F%.3f\nG1 X%.3f F%.3f\nM400"
                % (self.cutter_y, travel_rate, self.cutter_x, travel_rate,
                   start, push_rate))
            reactor.pause(reactor.monotonic() + .2)
            if self.cutter_switch_state is not True:
                raise gcmd.error(
                    "Knife sensor already tripped at X%.2f; use a START "
                    "nearer 0" % start)
            x = start
            while x - step >= stop - 1e-6:
                x -= step
                self.gcode.run_script_from_command(
                    "G1 X%.3f F%.3f\nM400" % (x, push_rate))
                reactor.pause(reactor.monotonic() + .08)
                if self.cutter_switch_state is False:
                    trip = x
                    break
        finally:
            self.gcode.run_script_from_command(
                "G1 X%.3f F%.3f\nG1 X%.3f F%.3f\nM400"
                % (self.cutter_release_x, push_rate,
                   self.cutter_x, travel_rate))
            self.gcode.run_script_from_command(
                "RESTORE_GCODE_STATE NAME=K2_CUT_CAL")
        reactor.pause(reactor.monotonic() + .2)
        returned = self.cutter_switch_state is True
        self.status['cutter_cal_x'] = trip
        if trip is None:
            raise gcmd.error(
                "Knife sensor never tripped between X%.2f and X%.2f"
                % (start, stop))
        in_range = self.cutter_cal_min_x <= trip <= self.cutter_cal_max_x
        gcmd.respond_info(
            "CFS cutter: knife sensor trips at X%.2f (stock valid range "
            "%.1f to %.1f: %s). Current cut position X%.2f is %.2fmm "
            "past it. Knife %s after release."
            % (trip, self.cutter_cal_max_x, self.cutter_cal_min_x,
               "OK" if in_range else "OUT OF RANGE", self.cutter_cut_x,
               trip - self.cutter_cut_x,
               "returned" if returned else "DID NOT RETURN"))

    def cmd_CFS_CUTTER_SENSOR(self, gcmd):
        state = ("unknown" if self.cutter_switch_state is None else
                 str(int(self.cutter_switch_state)))
        gcmd.respond_info(
            "CFS cutter sensor %s logical=%s"
            % (self.cutter_switch_pin, state))

    def _set_preloading(self, enable):
        # Stock keeps pre-loading on between prints (ff 0d 0f 01) and turns
        # it off before the first load of a print (ff 0d 0f 00).
        self._run_actuation(
            'SET_PRE_LOADING %s' % ('ON' if enable else 'OFF'),
            [0xff, 0x0d, 0x0f, 0x01 if enable else 0x00])

    def _hub_mask(self):
        # Slot whose filament is in the shared connections (hub). Stock reads
        # this (ff 08 01) before every load and expects it empty.
        frame, resp = self._run_actuation(
            'GET_CONNECTIONS_SENSOR', [0xff, 0x08, 0x01])
        p = self._payload(resp)
        return p[2] if len(p) > 2 else 0

    def _clear_hub(self, gcmd):
        # An aborted load leaves filament in the hub, and the next load of
        # any slot then fails (EXTRUDE_ERR8). The retrude finish stage alone
        # rewinds it out of the hub (verified live 10-25).
        present = self._hub_mask()
        if not present:
            return
        gcmd.respond_info(
            "CFS hub holds filament (mask 0x%02x); rewinding it" % present)
        self._run_actuation(
            'SET_BOX_MODE IDLE', [0xff, 0x04, 0x00, self.MODE_IDLE])
        self._finish_unload(present)
        still = self._hub_mask()
        if still:
            raise gcmd.error(
                "CFS hub still holds filament after rewinding (mask 0x%02x)"
                % still)

    def cmd_CFS_PRELOAD(self, gcmd):
        if not self.fitted:
            return
        # Mode change only, no motion: END_PRINT calls it while the print
        # state is still printing.
        self._require_actuation(gcmd)
        enable = gcmd.get_int('ENABLE', 1, minval=0, maxval=1)
        try:
            self._set_preloading(enable)
            self._run_actuation(
                'TIGHTEN_UP %s' % ('ENABLE' if enable else 'DISABLE'),
                [0xff, 0x0f,
                 self.TENSION_ENABLE if enable else self.TENSION_DISABLE])
        except Exception as exc:
            raise gcmd.error("CFS pre-loading change failed: %s" % exc)
        gcmd.respond_info("CFS pre-loading %s" % ("on" if enable else "off"))

    def cmd_CFS_RFID_READ(self, gcmd):
        self._require_cfs(gcmd)
        # Read-only: the box's cached RFID records (ff 02 <mask>) and
        # remaining % (ff 03 <mask>), after Jacob10383's query_rfid_records
        # and query_rfid_remaining. No forced read (ff 0d <mask> 02), which
        # spins the slot to find the tag.
        if self.print_start_transaction:
            raise gcmd.error("A CFS transaction is active")
        slot = gcmd.get('SLOT', '').upper()
        if slot and slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot] if slot else 0x0f
        frame, resp = self.bus.xfer(
            self.addr, [0xff, 0x02, mask], read_len=260, timeout=.5)
        p = self._payload(resp)
        if p is None or len(p) < 2 or p[1] != 0x02:
            raise gcmd.error("CFS RFID read: no/short response %s"
                             % (resp.hex(' ') if resp else '(none)'))
        result = decode_rfid_records(p)
        if 'error' in result:
            raise gcmd.error("CFS RFID read failed: %s" % result['error'])
        remain = self._dec_remain(self._payload(self.bus.xfer(
            self.addr, [0xff, 0x03, mask], timeout=.5)[1]) or b'').get(
                'remain', [])
        lines = []
        for index, name in enumerate('ABCD'):
            if not mask & (1 << index):
                continue
            entry = result['records'].get(name)
            left = ("%d%%" % remain[index] if index < len(remain)
                    and remain[index] <= 100 else "?")
            if entry is None:
                lines.append("%s: no record" % name)
            elif entry['fields']:
                f = entry['fields']
                lines.append(
                    "%s: material %s colour %s supplier %s batch %s "
                    "length %s date %s-%s-%s serial %s reserve %s, "
                    "remaining %s"
                    % (name, f['mat_id'], f['color'], f['supplier'],
                       f['batch'], f['len'], f['year'], f['month'],
                       f['day'], f['number'], f['reserve'], left))
            else:
                lines.append("%s: %s, remaining %s"
                             % (name, entry['record'] or 'empty', left))
        self.status['rfid'] = dict(
            (name, entry['fields'] or entry['record'])
            for name, entry in result['records'].items())
        gcmd.respond_info("CFS RFID\n" + "\n".join(lines))

    def _finish_unload(self, mask, report_error=True):
        try:
            return self._run_actuation(
                'RETRUDE FINISH',
                [0xff, 0x11, mask, 0x01],
                timeout=self.unload_bus_timeout)
        except Exception:
            if report_error:
                raise
            return None

    def cmd_CFS_UNLOAD(self, gcmd):
        raise gcmd.error(
            "Standalone CFS_UNLOAD is disabled: Creality requires CUT "
            "before RETRUDE. Use CFS_CUT_UNLOAD.")

    def _cmd_CFS_UNLOAD_AFTER_CUT(self, gcmd):
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_UNLOAD requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        path_mask = self._path_mask(self._measuring())
        if path_mask != mask:
            raise gcmd.error(
                "CFS_UNLOAD slot %s requires active mask 0x%02x "
                "(reported 0x%02x)"
                % (slot, mask, path_mask))
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS unload requires filament at the nozzle sensor")
        self._require_hot_extruder(gcmd)

        saved = False
        finish_sent = False
        try:
            # The current K2 wrapper idles every slot, then issues four
            # RETRUDE stage-0 frames around 3x fast + 1x slow 10mm pulls.
            self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_UNLOAD")
            saved = True
            if self.unload_rewind_pause_ms:
                self.gcode.run_script_from_command(
                    "G4 P%d" % self.unload_rewind_pause_ms)
            for _ in range(self.unload_fast_pulls):
                self._run_actuation(
                    'RETRUDE START',
                    [0xff, 0x11, mask, 0x00],
                    timeout=self.unload_bus_timeout)
                self.gcode.run_script_from_command(
                    "M83\n"
                    "G1 E0.010 F60\n"
                    "G1 E-%.3f F%.3f\n"
                    "M400"
                    % (self.unload_pull_length,
                       self.unload_pull_speed * 60.))
            self._run_actuation(
                'RETRUDE START',
                [0xff, 0x11, mask, 0x00],
                timeout=self.unload_bus_timeout)
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E-%.3f F%.3f\n"
                "M400"
                % (self.unload_final_length,
                   self.unload_final_speed * 60.))
            try:
                self._finish_unload(mask)
            except CFSStatusError as exc:
                if (exc.status != STATUS_UNLOAD_MOTOR_BLOCKED
                        or not self.stall_retry_motion
                        or not self._flex_filament_path(
                            gcmd, "CFS hub motor stalled during the rewind"
                            " (UNLOAD_MOTOR_BLOCKED); moving the toolhead to"
                            " free the tube, then retrying")):
                    raise
                self._finish_unload(mask)
            finish_sent = True
        except Exception as exc:
            if not finish_sent:
                self._finish_unload(mask, report_error=False)
            raise gcmd.error("CFS unload failed: %s" % exc)
        finally:
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=K2_CFS_UNLOAD")

        # Firmware can acknowledge RETRUDE FINISH before the nozzle-side
        # switch callback reaches Klippy. Give the physical filament time to
        # clear instead of reporting a false failure immediately.
        if not self._wait_nozzle_filament(
                False, self.unload_sensor_timeout) \
                and not self._clear_nozzle_stub(gcmd):
            raise gcmd.error(
                "CFS unload finished but the nozzle sensor still "
                "detects filament after %.1fs"
                % self.unload_sensor_timeout)
        self.status.update({
            'feed_enabled': False,
            'active_mask': 0,
            'active_slots': [],
            'active_slot': None,
            'box_mode_code': self.BOX_MODE_IDLE,
            'box_mode': 'idle',
            'last_unloaded_slot': slot,
            'feed_slot': None,
        })
        gcmd.respond_info(
            "CFS slot %s unload complete; nozzle sensor is clear" % slot)

    def _clear_nozzle_stub(self, gcmd):
        # Brittle filament can snap during the unload pulls and leave a
        # short loose piece under the nozzle sensor while the CFS rewinds
        # the rest (twice on white Hyper PLA, 10-25). The unload runs over
        # the chute with a hot nozzle, so push the piece through the melt
        # zone a little at a time until the sensor clears.
        if not self.unload_clear_tries:
            return False
        position = self.printer.lookup_object('toolhead').get_position()
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - self.purge_x) > tolerance
                or abs(position[1] - self.purge_y) > tolerance):
            return False
        heater = self.printer.lookup_object('extruder').get_heater()
        if not heater.can_extrude:
            return False
        pushed = 0.
        for unused in range(self.unload_clear_tries):
            self.gcode.run_script_from_command(
                "M83\nG1 E%.3f F%.3f\nM400"
                % (self.unload_clear_length,
                   self.unload_clear_speed * 60.))
            pushed += self.unload_clear_length
            if self._wait_nozzle_filament(False, 1.):
                self.gcode.run_script_from_command(
                    "G1 E-%.3f F%.3f\nM400"
                    % (self.purge_retract_length,
                       self.purge_retract_speed * 60.))
                gcmd.respond_info(
                    "CFS unload: pushed a loose piece of filament out over"
                    " the chute (%.0fmm); nozzle sensor now clear" % pushed)
                return True
        return False

    def _flex_filament_path(self, gcmd, message):
        # Jacob10383's filament_retry_motion: sweep the toolhead across the
        # bed so the PTFE tube to the head changes shape, which frees
        # filament dragging in a tight bend. Runs from the purge chute (the
        # load and the rewind both run there) at the current Z, which the
        # callers have already checked (purge_min_z, or the bed parked low
        # at print start), and ends back at the chute. The moves stay inside
        # the bed and in front of the purge safe line.
        toolhead = self.printer.lookup_object('toolhead')
        position = toolhead.get_position()
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - self.purge_x) > tolerance
                or abs(position[1] - self.purge_y) > tolerance):
            return False
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            return False
        gcmd.respond_info(message)
        rate = self.purge_speed * 60.
        # Mid-print the head sits only just above the part, so lift clear of
        # it before crossing the bed and come back down at the chute
        z_max = toolhead.get_status(0.).get('axis_maximum', [0., 0., 0.])[2]
        z_lift = min(position[2] + self.stall_retry_z_lift, z_max)
        lines = ["G90", "G1 Z%.3f F600" % z_lift,
                 "G1 Y%.3f F%.3f" % (self.purge_safe_y, rate)]
        for move in self.stall_retry_moves:
            lines.append("G1 %s F%.3f" % (" ".join(
                "%s%.3f" % (axis, move[axis]) for axis in sorted(move)), rate))
        lines.extend([
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, rate),
            "G1 X%.3f F%.3f" % (self.purge_entry_x, rate),
            "G1 Y%.3f F%.3f" % (self.purge_y, rate),
            "G1 X%.3f Y%.3f F%.3f" % (self.purge_gate_x, self.purge_y, rate),
            "G1 X%.3f Y%.3f F%.3f" % (self.purge_x, self.purge_y, rate),
            "G1 Z%.3f F600" % position[2],
            "M400",
        ])
        self.gcode.run_script_from_command(
            "SET_VELOCITY_LIMIT ACCEL=%.3f" % self.clean_accel)
        try:
            self.gcode.run_script_from_command("\n".join(lines))
        finally:
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
        self.status['stall_retry_motions'] = \
            self.status.get('stall_retry_motions', 0) + 1
        return True

    def cmd_CFS_CUT_UNLOAD(self, gcmd):
        self._require_cfs(gcmd)
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_CUT_UNLOAD requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        position = self._require_safe_motion(gcmd, "CFS_CUT_UNLOAD")
        self._require_position(
            gcmd, position, self.purge_return_x, self.purge_safe_y,
            "CFS cut/unload")
        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        path_mask = self._path_mask(self._measuring())
        if path_mask != mask:
            raise gcmd.error(
                "CFS_CUT_UNLOAD slot %s requires active mask 0x%02x "
                "(reported 0x%02x)"
                % (slot, mask, path_mask))
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS cut/unload requires filament at the nozzle sensor")
        self._require_hot_extruder(gcmd)
        if self.cutter_switch_state is not True:
            raise gcmd.error(
                "CFS cutter is not in its returned state before cutting")

        toolhead = self.printer.lookup_object('toolhead')
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            raise gcmd.error("Unable to read current acceleration limit")
        saved = False
        accel_changed = False
        cut_completed = False
        runout_disabled = False
        try:
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_CUT_UNLOAD")
            saved = True
            # A deliberate unload transitions the same physical switch used
            # for print runout. Mask only its runout handler while retaining
            # physical state reads for completion verification.
            self._set_runout_sensor_enabled(False)
            runout_disabled = self.manage_runout_sensor
            # Exact current-wrapper release sequence: tiny engagement, 2mm
            # retract, cutter stage/ram/release, another 2mm retract, then
            # a 7mm forward/back tip-shaping cycle.
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E0.010 F60\n"
                "G1 E-%.3f F%.3f\n"
                "M400"
                % (self.cutter_retract_length,
                   self.cutter_retract_speed * 60.))
            travel_rate = self.cutter_speed * 60.
            self.gcode.run_script_from_command(
                "G90\n"
                "G1 Y%.3f F%.3f\n"
                "G1 X%.3f F%.3f\n"
                "M400"
                % (self.cutter_y, travel_rate,
                   self.cutter_x, travel_rate))
            if self.cutter_switch_state is not True:
                raise RuntimeError(
                    "cutter did not report returned at staging point")
            accel_changed = True
            self.cutter_events = []
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f\n"
                "G90\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "M400"
                % (self.cutter_ram_accel,
                   self.cutter_cut_x, self.cutter_y,
                   self.cutter_ram_speed * 60.,
                   self.cutter_release_x, self.cutter_y,
                   self.cutter_release_speed * 60.))
            reactor = self.printer.get_reactor()
            reactor.pause(reactor.monotonic() + .2)
            pressed = any(not st for t, st in self.cutter_events)
            self.cutter_events = None
            self.status['last_cut_sensor_pressed'] = pressed
            key = 'cut_sensor_hits' if pressed else 'cut_sensor_misses'
            self.status[key] = self.status.get(key, 0) + 1
            if not pressed:
                # With the DXC2 the sensor has never changed during a cut
                # (10-25), so this is a log line, not a warning
                logging.info("k2_cfs: knife sensor did not change during "
                             "the cut at X%.2f", self.cutter_cut_x)
            if self.cutter_switch_state is not True:
                raise RuntimeError(
                    "cutter did not return after ram/release")
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E-%.3f F%.3f\n"
                "G1 E%.3f F%.3f\n"
                "G1 E-%.3f F%.3f\n"
                "M400"
                % (self.cutter_retract_length,
                   self.cutter_retract_speed * 60.,
                   self.cutter_tip_length,
                   self.cutter_tip_push_speed * 60.,
                   self.cutter_tip_length,
                   self.cutter_tip_pull_speed * 60.))
            cut_completed = True
            self.gcode.run_script_from_command(
                "G90\n"
                "G1 Y%.3f F%.3f\n"
                "G1 X%.3f F%.3f\n"
                "G1 Y%.3f F%.3f\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "M400"
                % (self.purge_safe_y, travel_rate,
                   self.purge_entry_x, travel_rate,
                   self.purge_y, travel_rate,
                   self.purge_gate_x, self.purge_y, travel_rate,
                   self.purge_x, self.purge_y, travel_rate))
            if not cut_completed:
                raise RuntimeError(
                    "internal safety gate: rewind requested before cut")
            self._cmd_CFS_UNLOAD_AFTER_CUT(gcmd)
        except Exception as exc:
            raise gcmd.error("CFS cut/unload failed: %s" % exc)
        finally:
            try:
                if accel_changed:
                    self.gcode.run_script_from_command(
                        "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
                if saved:
                    self.gcode.run_script_from_command(
                        "RESTORE_GCODE_STATE NAME=K2_CFS_CUT_UNLOAD")
            finally:
                if runout_disabled:
                    self._set_runout_sensor_enabled(True)

        self.status['last_cut_unloaded_slot'] = slot
        gcmd.respond_info(
            "CFS slot %s cut and unload complete" % slot)

    def _load_stage(self, name, mask, stage, value=0,
                    report_error=True):
        try:
            return self._run_actuation(
                name,
                [0xff, 0x10, mask, stage, value],
                timeout=self.load_bus_timeout)
        except Exception:
            if report_error:
                raise
            return None

    def cmd_CFS_LOAD(self, gcmd):
        self._require_cfs(gcmd)
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_LOAD requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        position = self._require_safe_motion(gcmd, "CFS_LOAD")
        self._require_position(
            gcmd, position, self.purge_x, self.purge_y,
            "CFS load")
        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        measuring = self._measuring()
        old_mask = measuring.get('active_mask', 0)
        if old_mask not in (0, mask):
            raise gcmd.error(
                "CFS selected mask is 0x%02x; refusing load from slot %s"
                % (old_mask, slot))
        if self._nozzle_has_filament() is not False:
            raise gcmd.error(
                "CFS load requires the nozzle filament sensor to be clear")
        self._require_hot_extruder(gcmd)

        saved = False
        try:
            # Current Creality wrapper sequence, reconstructed offline:
            # all-idle -> stage 0 -> stage 4 -> repeated stage 5. Its
            # filament-sensor callback then feeds E+5 and sends stage 6.
            # The callback releases the stage-5 loop; E+9 and stage 7(value=3)
            # follow immediately. Sending another stage 5 after stage 6 is
            # rejected by CFS firmware 1.5.0 as EXTRUDE_ERR8 (state 0x0c).
            # Stock: pre-loading off and nothing in the shared hub first.
            self._set_preloading(False)
            self._clear_hub(gcmd)
            self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
            # Stock issues ff 0f 01 before stage 0, then ff 0f 00 only after
            # selecting the steady print route. Despite the command name,
            # value 1 stops normal buffer tension while the staged load owns
            # the hub motor.
            self._run_actuation(
                'TIGHTEN_UP DISABLE',
                [0xff, 0x0f, self.TENSION_DISABLE])
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_LOAD")
            saved = True
            self._load_stage('EXTRUDE STAGE 0', mask, 0)
            self._load_stage('EXTRUDE STAGE 4', mask, 4)

            reactor = self.printer.get_reactor()
            deadline = reactor.monotonic() + self.load_timeout
            err8_retries = 0
            flexed = False
            while True:
                try:
                    self._load_stage('EXTRUDE STAGE 5', mask, 5)
                except Exception as exc:
                    # Firmware 1.5.0 may advance while the nozzle-sensor
                    # callback is arriving, then reject the in-flight stage-5
                    # retry with state 0x0c. The asserted physical sensor
                    # makes that response terminal success. The filament can
                    # still be a moment short of the sensor when the CFS
                    # advances (seen live 10-24: sensor tripped after the
                    # load had been failed), so give it a few seconds.
                    if self._wait_nozzle_filament(
                            True, self.load_sensor_grace):
                        break
                    if (getattr(exc, 'status', None)
                            == STATUS_ODOMETER_STALLED
                            and err8_retries < 2):
                        # EXTRUDE_ERR8 has no user message in stock; its
                        # wrapper retries instead of failing the load.
                        err8_retries += 1
                        moved = False
                        if self.stall_retry_motion and not flexed:
                            moved = flexed = self._flex_filament_path(
                                gcmd, "CFS hub wheel stopped during the load"
                                " (EXTRUDE_ERR8); moving the toolhead to free"
                                " the tube, then retrying")
                        if not moved:
                            gcmd.respond_info(
                                "CFS stage 5 returned EXTRUDE_ERR8; re-sending"
                                " stage 4 (retry %d/2)" % err8_retries)
                        self._load_stage('EXTRUDE STAGE 4', mask, 4)
                        reactor.pause(reactor.monotonic()
                                      + self.load_poll_interval)
                        continue
                    raise
                if self._nozzle_has_filament() is True:
                    break
                now = reactor.monotonic()
                if now >= deadline:
                    raise RuntimeError(
                        "no filament at nozzle sensor after %.1fs"
                        % self.load_timeout)
                # Watch the sensor until the next stage-5 send, so the load
                # moves on as soon as filament arrives instead of up to a
                # whole interval later (the box keeps feeding meanwhile)
                if self._wait_nozzle_filament(
                        True, min(deadline - now, self.load_poll_interval),
                        poll=self.load_arrival_poll_interval):
                    break

            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E%.3f F%.3f\n"
                "M400"
                % (self.load_engage_length,
                   self.load_engage_speed * 60.))
            self._load_stage('EXTRUDE STAGE 6', mask, 6)
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E%.3f F%.3f\n"
                "M400"
                % (self.load_extrude_length,
                   self.load_extrude_speed * 60.))
            try:
                self._load_stage(
                    'EXTRUDE STAGE 7', mask, 7,
                    value=self.load_box_finish_length)
            except CFSStatusError as exc:
                # STAGE7_NO_MOTION: the final nudge moved under 1.5 mm
                # because the filament is already home. Jacob10383 logs
                # it and carries on when the nozzle sensor confirms.
                if (exc.status != STATUS_STAGE7_NO_MOTION
                        or self._nozzle_has_filament() is not True):
                    raise
                logging.info("k2_cfs: stage 7 moved under 1.5 mm (0x0e);"
                             " nozzle sensor confirms filament, carrying on")
            self._run_query('BUFFER_STATE')
            self._run_actuation(
                'SET_BOX_MODE PRINT',
                [0xff, 0x04, mask, self.MODE_PRINT])
            self._run_actuation(
                'TIGHTEN_UP ENABLE',
                [0xff, 0x0f, self.TENSION_ENABLE])
        except Exception as exc:
            self._best_effort_stop(mask)
            if self._nozzle_has_filament() is not True:
                # Don't leave a part-loaded slot in the hub.
                try:
                    self._clear_hub(gcmd)
                except Exception:
                    logging.exception("k2_cfs: hub clear after failed load")
                try:
                    self._restore_idle_preload()
                except Exception:
                    logging.exception("k2_cfs: pre-loading on after failed load")
            raise gcmd.error("CFS load failed: %s" % exc)
        finally:
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=K2_CFS_LOAD")

        if self._nozzle_has_filament() is not True:
            self._best_effort_stop(mask)
            raise gcmd.error(
                "CFS load finished but the nozzle sensor is not triggered")
        verified = self._measuring()
        box_mode = self._run_query('BOX_MODE')
        if (verified.get('active_mask') != mask
                or box_mode.get('box_mode_code')
                != self.BOX_MODE_IDLE):
            self._best_effort_stop(mask)
            raise gcmd.error(
                "CFS loaded filament but did not arm steady slot %s feed: "
                "selection=%s operation_mode=%s"
                % (slot, verified, box_mode))
        self.status.update({
            'feed_enabled': True,
            'active_mask': mask,
            'active_slots': [slot],
            'active_slot': slot,
            'box_mode_code': box_mode.get('box_mode_code'),
            'box_mode': box_mode.get('box_mode'),
            'last_loaded_slot': slot,
            'feed_slot': slot,
        })
        gcmd.respond_info(
            "CFS slot %s load complete; nozzle sensor is triggered" % slot)

    def _purge_wipe_script(self, final):
        # Creality Print's K2 wipe, run after every flush block: swing in
        # the chute, a full circle on each half of the silicone brush, then
        # three passes back over the chute.
        feedrate = self.clean_speed * 60.
        circle_rate = self.clean_circle_speed * 60.
        lines = [
            "G90",
            "G1 X%.3f F%.3f" % (self.clean_right_x, feedrate),
            "G1 X%.3f F%.3f" % (self.clean_left_x, feedrate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.clean_circle_left_x, self.clean_y, feedrate),
            "G2 I4 J0 P1 F%.3f" % circle_rate,
            "G1 X%.3f Y%.3f F%.3f"
            % (self.clean_circle_right_x, self.clean_y, feedrate),
            "G3 I-4 J0 P1 F%.3f" % circle_rate,
        ]
        for unused in range(3):
            lines.extend([
                "G1 X%.3f Y%.3f F%.3f"
                % (self.clean_right_x, self.clean_y, feedrate),
                "G1 X%.3f Y%.3f F%.3f"
                % (self.purge_x, self.purge_y, feedrate),
            ])
        if final:
            lines.extend([
                "G1 X%.3f F%.3f" % (self.clean_right_x, feedrate),
                "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
                "G1 X%.3f F%.3f" % (self.purge_return_x, feedrate),
            ])
        lines.append("M400")
        return "\n".join(lines)

    def _purge_segments(self, requested_length, clamp=True):
        # The stock wrapper clamps its nominal 140mm baseline to an actual
        # 160mm flush. Offline traces of 140/160/240mm requests produce
        # 90+70, 90+70, and 90+70+80 respectively. A short colour-change
        # purge (the prime tower flushes the rest) is not clamped.
        # Flushed in Creality's blocks of at most purge_chunk_length (90mm),
        # each one followed by a fan blast and a full wipe.
        actual_length = max(
            requested_length,
            self.purge_minimum_actual_length if clamp else 0.)
        count = int(math.ceil(actual_length / self.purge_chunk_length - 1e-6))
        return actual_length, [actual_length / count] * count

    def cmd_CFS_PURGE(self, gcmd):
        self._require_cfs(gcmd)
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_PURGE requires CONFIRM=1")

        position = self._require_safe_motion(gcmd, "CFS_PURGE")
        self._require_position(
            gcmd, position, self.purge_x, self.purge_y, "CFS purge")
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS purge requires filament at the nozzle sensor")
        self._require_hot_extruder(gcmd)

        mask = self._path_mask(self._measuring())
        if mask not in self.SLOT_MASKS.values():
            raise gcmd.error(
                "CFS purge requires exactly one active slot "
                "(reported mask 0x%02x)" % mask)
        box_mode = self._run_query('BOX_MODE')
        if box_mode.get('box_mode_code') != self.BOX_MODE_IDLE:
            raise gcmd.error(
                "CFS purge requires an idle operation engine "
                "(reported %s)"
                % box_mode.get('box_mode'))

        short = gcmd.get_int('SHORT', 0, minval=0, maxval=1)
        change = gcmd.get_int('CHANGE', 0, minval=0, maxval=1)
        length = gcmd.get_float(
            'LENGTH', self.purge_extrude_length,
            minval=30. if short else 140., maxval=360.)
        speed = gcmd.get_float(
            'SPEED', self.purge_extrude_speed,
            above=0., maxval=9.58)
        actual_length, segments = self._purge_segments(
            length, clamp=not short)
        # Creality Print's K2 FLUSH block: fast pushes broken by 1 mm/s
        # pauses so the strand comes out in pieces, part fan low while
        # flushing, a full blast so the strand sets and snaps off, then the
        # retract. A colour change opens with one 36 mm push of the old
        # colour. Later blocks first push back the previous retract.
        rate = speed * 60.
        retract = self.purge_retract_length
        scripts = []
        for index, chunk in enumerate(segments):
            final = index == len(segments) - 1
            lines = ["M83", "M106 S30"]
            if change and index == 0 and chunk > 36.:
                rest = chunk - 36.
                lines.append("G1 E36.000 F%.3f" % rate)
                for unused in range(4):
                    lines.append("G1 E%.3f F60" % (rest * .02))
                    lines.append("G1 E%.3f F%.3f" % (rest * .23, rate))
            else:
                pre = retract if index else 0.
                for pulse in range(5):
                    lines.append("G1 E%.3f F%.3f"
                                 % (chunk * .18 + (pre if not pulse else 0.),
                                    rate))
                    lines.append("G1 E%.3f F60" % (chunk * .02))
            lines.append("M400")
            if self.purge_cool_time:
                lines.extend([
                    "M106 S%d" % round(self.purge_cool_fan * 255.),
                    "G4 P%d" % (self.purge_cool_time * 1000.),
                    "M106 S30",
                ])
            if retract:
                lines.append("G1 E-%.3f F%.3f"
                             % (retract, self.purge_retract_speed * 60.))
            scripts.append("\n".join(lines))
            scripts.append(self._purge_wipe_script(final))
        toolhead = self.printer.lookup_object('toolhead')
        fans_before = self._fan_speeds()
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            raise gcmd.error("Unable to read current acceleration limit")
        saved = False
        accel_changed = False
        try:
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_PURGE")
            saved = True
            accel_changed = True
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f" % self.clean_accel)
            for script in scripts:
                self.gcode.run_script_from_command(script)
        except Exception as exc:
            raise gcmd.error("CFS purge failed: %s" % exc)
        finally:
            self._set_fans(fans_before)
            if accel_changed:
                self.gcode.run_script_from_command(
                    "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=K2_CFS_PURGE")

        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS purge finished but the nozzle sensor is clear")
        verified = self._measuring()
        box_mode = self._run_query('BOX_MODE')
        if (self._path_mask(verified) != mask
                or box_mode.get('box_mode_code')
                != self.BOX_MODE_IDLE):
            raise gcmd.error(
                "CFS purge changed selection or left an operation active: "
                "selection=%s operation_mode=%s" % (verified, box_mode))
        self.status.update({
            'last_purge_requested_length': length,
            'last_purge_length': actual_length,
            'last_purge_speed': speed,
        })
        gcmd.respond_info(
            "CFS purge complete: requested %.3fmm, stock-clamped %.3fmm "
            "at %.3fmm/s" % (length, actual_length, speed))

    def _slot_nozzle_temp(self, slot):
        slots = self.printer.lookup_object('hark_cfs_slots', None)
        if slots is None:
            return 0.
        record = slots.get_status(0.)['slots'].get(slot, {})
        return float(record.get('nozzle_temp') or 0.)

    def _fan_speeds(self):
        speeds = {}
        for name in self.purge_fans:
            fan = self.printer.lookup_object(name, None)
            if fan is not None:
                speeds[name] = fan.get_status(0.).get('speed', 0.)
        return speeds

    def _set_fans(self, speeds):
        lines = []
        for name, speed in speeds.items():
            if name == 'fan':
                lines.append("M106 S%d" % round(speed * 255.))
            else:
                lines.append("SET_FAN_SPEED FAN=%s SPEED=%.3f"
                             % (name.split()[-1], speed))
        if lines:
            self.gcode.run_script_from_command("\n".join(lines))

    def cmd_CFS_TOOLCHANGE(self, gcmd):
        self._require_cfs(gcmd)
        # The work stock firmware does behind a T command mid-print: lift
        # clear of the print, cut and rewind the loaded slot, load and purge
        # the new one at the hotter of the two temperatures, wipe, and go
        # back down to where the print left off. Same bounded transaction as
        # CFS_PRINT_START, so the standalone cut/load/purge gates still apply
        # everywhere else.
        # PURGE=<mm>: a shorter chute purge for a colour change between
        # like materials, with the prime tower doing the rest of the flush.
        # PURGE_FALLBACK=<mm>: the same, used only when
        # toolchange_purge_from_file finds nothing in the print file.
        purge_length = gcmd.get_float('PURGE', None, minval=30., maxval=240.)
        purge_fallback = gcmd.get_float(
            'PURGE_FALLBACK', None, minval=30., maxval=240.)
        self._require_actuation(gcmd)
        print_stats = self.printer.lookup_object('print_stats', None)
        state = print_stats.get_status(0.).get('state', '') \
            if print_stats is not None else ''
        if state != 'printing':
            raise gcmd.error(
                "CFS_TOOLCHANGE is only available while printing "
                "(state is %s)" % (state or "unknown"))
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        if self.print_start_transaction:
            raise gcmd.error("A CFS transaction is already active")

        mask = self.SLOT_MASKS[slot]
        active_mask = self._path_mask(self._measuring())
        nozzle_loaded = self._nozzle_has_filament() is True
        if nozzle_loaded and active_mask == mask:
            self._start_feed(
                gcmd, slot, require_confirm=False, require_selected=True)
            gcmd.respond_info("CFS slot %s already loaded" % slot)
            return
        old_slot = {m: s for s, m in self.SLOT_MASKS.items()}.get(
            active_mask)
        if not nozzle_loaded or old_slot is None:
            raise gcmd.error(
                "CFS tool change needs a loaded slot to change from "
                "(nozzle sensor %s, active mask 0x%02x)"
                % (nozzle_loaded, active_mask))
        full_length = None
        if purge_length is None and self.toolchange_purge_from_file:
            purge_length, full_length = self._file_purge(
                gcmd, old_slot, slot)
        if purge_length is None and full_length is None:
            purge_length = purge_fallback

        toolhead = self.printer.lookup_object('toolhead')
        start = toolhead.get_position()
        heater = self.printer.lookup_object('extruder').get_heater()
        print_temp = heater.get_status(0.).get('target', 0.)
        new_temp = self._slot_nozzle_temp(slot) or print_temp
        purge_temp = max(print_temp, new_temp)
        lift_z = max(start[2] + self.toolchange_lift, self.purge_min_z)
        fan_speeds = self._fan_speeds()
        self._set_fans({name: 0. for name in fan_speeds})
        reactor = self.printer.get_reactor()
        timings = [reactor.monotonic()]
        self.gcode.run_script_from_command(
            "SAVE_GCODE_STATE NAME=K2_CFS_TOOLCHANGE\n"
            "G90\n"
            "G1 Z%.3f F600\n"
            "M104 S%.1f" % (lift_z, purge_temp))
        self.print_start_transaction = True
        try:
            self._move_to_cut_start()
            unload_gcmd = self.gcode.create_gcode_command(
                "CFS_CUT_UNLOAD", "", {"SLOT": old_slot, "CONFIRM": "1"})
            self.cmd_CFS_CUT_UNLOAD(unload_gcmd)
            timings.append(reactor.monotonic())
            # M109 waits for the PID to settle within 1C, which cost up to
            # 45s per change at an unchanged temperature (10-25). The load
            # only needs the nozzle near flush temperature; the CFS feed
            # takes ~20s more before any filament reaches the melt zone.
            self.gcode.run_script_from_command(
                "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%.1f"
                % (purge_temp - self.toolchange_temp_margin))
            load_params = {"SLOT": slot, "CONFIRM": "1", "CHANGE": "1"}
            if purge_length is not None:
                load_params.update(LENGTH="%.1f" % purge_length, SHORT="1")
            elif full_length is not None:
                load_params.update(LENGTH="%.1f" % full_length)
            load_gcmd = self.gcode.create_gcode_command(
                "CFS_LOAD", "", load_params)
            self.cmd_CFS_LOAD(load_gcmd)
            timings.append(reactor.monotonic())
            self.cmd_CFS_PURGE(load_gcmd)
            timings.append(reactor.monotonic())
        except Exception:
            self._best_effort_stop(mask)
            self.status.update({
                'feed_enabled': False,
                'box_mode_code': self.BOX_MODE_IDLE,
                'box_mode': 'idle',
            })
            raise
        finally:
            self.print_start_transaction = False
            self._set_fans(fan_speeds)

        # Over the print at the lifted height, then straight down.
        self.gcode.run_script_from_command(
            "M104 S%.1f\n"
            "G90\n"
            "G1 X%.3f Y%.3f F%.3f\n"
            "RESTORE_GCODE_STATE NAME=K2_CFS_TOOLCHANGE MOVE=1 MOVE_SPEED=10"
            % (new_temp, start[0], start[1], self.purge_speed * 60.))
        timings.append(reactor.monotonic())
        self.status['last_toolchange'] = "%s>%s" % (old_slot, slot)
        gcmd.respond_info(
            "CFS tool change complete: slot %s to slot %s in %.0fs "
            "(cut/unload %.0fs, load %.0fs, purge %.0fs, return %.0fs)"
            % ((old_slot, slot, timings[-1] - timings[0])
               + tuple(b - a for a, b in zip(timings, timings[1:]))))

    def _flush_matrix(self):
        # The print file's flush matrix, read once per file
        sdcard = self.printer.lookup_object('virtual_sdcard', None)
        path = sdcard.get_status(0.).get('file_path') if sdcard else None
        if not path:
            return None
        try:
            key = (path, os.path.getmtime(path), os.path.getsize(path))
            if self.flush_cache is None or self.flush_cache[0] != key:
                self.flush_cache = (key, read_flush_metadata(path))
        except (OSError, IOError):
            logging.exception("k2_cfs: reading the flush matrix failed")
            return None
        return self.flush_cache[1]

    def _file_purge(self, gcmd, old_slot, slot):
        # Jacob10383's _purge_plan: flush volume (mm^3) for this change from
        # the file's matrix over the filament area. T0-T3 are slots A-D.
        # Returns (short purge, full purge): between like materials it is a
        # SHORT purge clamped to 30-240 mm; otherwise the stock purge, made
        # longer when the file asks for more.
        meta = self._flush_matrix()
        matrix = meta and meta['matrix']
        src, dst = ord(old_slot) - ord('A'), ord(slot) - ord('A')
        if not matrix or src >= len(matrix) or dst >= len(matrix[src]):
            gcmd.respond_info("CFS tool change: no usable flush matrix in "
                              "the print file; using the fixed purge")
            return None, None
        extruder = self.printer.lookup_object('extruder')
        area = getattr(extruder, 'filament_area', math.pi * .875 ** 2)
        volume = matrix[src][dst] * meta['multiplier']
        length = volume / area
        slots = self.printer.lookup_object('hark_cfs_slots', None)
        records = slots.get_status(0.)['slots'] if slots is not None else {}
        old_type = (records.get(old_slot, {}).get('type') or '').upper()
        new_type = (records.get(slot, {}).get('type') or '').upper()
        if old_type and old_type == new_type:
            short, full = min(max(length, 30.), 240.), None
        else:
            short = None
            full = min(max(length, self.purge_extrude_length, 140.), 360.)
        gcmd.respond_info(
            "CFS tool change purge from the file's flush matrix: T%d>T%d "
            "%.0f mm^3 = %.0f mm, purging %.0f mm"
            % (src, dst, volume, length, short or full))
        return short, full

    def cmd_CFS_RUNOUT_REFILL(self, gcmd):
        self._require_cfs(gcmd)
        # Stock BOX_CHECK_MATERIAL_REFILL: the loaded slot ran out mid-print
        # and another slot holds the same filament. Klipper has already
        # paused and parked (pause_on_runout). Push the old tail through at
        # the chute, release the empty slot, load and purge the new one,
        # wipe, then RESUME. Any failure leaves the print paused, as before.
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_RUNOUT_REFILL requires CONFIRM=1")
        print_stats = self.printer.lookup_object('print_stats', None)
        state = print_stats.get_status(0.).get('state', '') \
            if print_stats is not None else ''
        if state != 'paused':
            raise gcmd.error(
                "CFS_RUNOUT_REFILL runs from a runout pause (state is %s)"
                % (state or "unknown"))
        if self.print_start_transaction:
            raise gcmd.error("A CFS transaction is already active")
        mask = self.SLOT_MASKS[slot]
        self.print_start_transaction = True
        try:
            self._require_actuation(gcmd)
            self._require_hot_extruder(gcmd)
            if self._nozzle_has_filament() is True:
                raise gcmd.error("the nozzle sensor still sees filament")
            self._ensure_purge_position(gcmd)
            if self.runout_push_length > 0:
                self.gcode.run_script_from_command(
                    "M83\nG1 E%.1f F300\nM400" % self.runout_push_length)
            old_mask = self._measuring().get('active_mask', 0)
            if old_mask not in (0, mask):
                # The empty slot is still selected: put the box idle
                self._best_effort_stop(old_mask)
                old_mask = self._measuring().get('active_mask', 0)
                if old_mask not in (0, mask):
                    raise gcmd.error(
                        "the CFS still selects mask 0x%02x" % old_mask)
            load_gcmd = self.gcode.create_gcode_command(
                "CFS_LOAD", "", {"SLOT": slot, "CONFIRM": "1", "CHANGE": "1"})
            self.cmd_CFS_LOAD(load_gcmd)
            if self.runout_purge_length:
                # Same material and colour: a short purge (no colour-change
                # opening push) clears the joint with the old tail
                purge_gcmd = self.gcode.create_gcode_command(
                    "CFS_PURGE", "", {
                        "CONFIRM": "1", "SHORT": "1",
                        "LENGTH": "%.1f" % self.runout_purge_length})
            else:
                purge_gcmd = load_gcmd
            self.cmd_CFS_PURGE(purge_gcmd)
            self.gcode.run_script_from_command("NOZZLE_WIPE CONFIRM=1")
            # The purge ends with a retract; push it back so the first
            # millimetres after RESUME are not thin (a visible line)
            if self.purge_retract_length:
                self.gcode.run_script_from_command(
                    "M83\nG1 E%.3f F%.3f\nM400"
                    % (self.purge_retract_length,
                       self.purge_retract_speed * 60.))
        except Exception as e:
            self._best_effort_stop(mask)
            self.status.update({
                'feed_enabled': False,
                'box_mode_code': self.BOX_MODE_IDLE,
                'box_mode': 'idle',
            })
            self.status['last_runout_refill'] = "failed: %s" % (str(e)[:80],)
            gcmd.respond_raw(
                "!! Automatic refill from slot %s failed (%s). The print "
                "stays paused: load filament, then RESUME." % (slot, e))
            return
        finally:
            self.print_start_transaction = False
        self.status['last_runout_refill'] = slot
        self._clear_runout_warning()
        gcmd.respond_info(
            "Runout: refilled from slot %s, resuming the print" % slot)
        self.gcode.run_script_from_command("RESUME")

    def cmd_CFS_PRINT_START(self, gcmd):
        self._require_cfs(gcmd)
        self._require_print_start(gcmd)
        self._load_and_purge(gcmd, "CFS_PRINT_START")

    def cmd_CFS_CHANGE_SLOT(self, gcmd):
        self._require_cfs(gcmd)
        # The same cut/load/purge as a print start, from the screen while
        # idle. Asking for the slot already loaded just purges it.
        self._require_idle(gcmd)
        self._load_and_purge(gcmd, "CFS_CHANGE_SLOT")

    def _load_and_purge(self, gcmd, command):
        self._require_actuation(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("%s requires CONFIRM=1" % command)
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        if self.print_start_transaction:
            raise gcmd.error("CFS print-start transaction is already active")

        mask = self.SLOT_MASKS[slot]
        self._require_safe_motion(gcmd, command)
        self._require_hot_extruder(gcmd)
        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        active_mask = self._path_mask(self._measuring())
        nozzle_loaded = self._nozzle_has_filament() is True

        mask_to_slot = {
            slot_mask: slot_name
            for slot_name, slot_mask in self.SLOT_MASKS.items()
        }
        self.print_start_transaction = True
        try:
            hub = None
            if nozzle_loaded and active_mask not in mask_to_slot:
                # Feeding a new spool while another is in the nozzle can
                # leave that slot selected too (seen 10-27: mask 0x05 with C
                # in the nozzle). The hub sensor tells which slot's filament
                # is in the shared path; rewind the others home.
                hub = self._hub_mask()
                if hub in mask_to_slot and active_mask & hub:
                    stray = active_mask & ~hub
                    gcmd.respond_info(
                        "CFS selects mask 0x%02x but slot %s is in the path;"
                        " rewinding the others (mask 0x%02x)"
                        % (active_mask, mask_to_slot[hub], stray))
                    self._finish_unload(stray, report_error=False)
                    active_mask = self._measuring().get(
                        'active_mask', 0)
            if nozzle_loaded:
                active_slot = mask_to_slot.get(active_mask)
                if active_slot is None:
                    raise gcmd.error(
                        "Nozzle has filament but CFS active mask is 0x%02x"
                        " (hub 0x%02x; more than one slot selected). Pull"
                        " back the slot you fed last by hand, then retry"
                        % (active_mask, hub or 0))
                if active_mask != mask:
                    self._move_to_cut_start()
                    unload_gcmd = self.gcode.create_gcode_command(
                        "CFS_CUT_UNLOAD", "",
                        {"SLOT": active_slot, "CONFIRM": "1"})
                    self.cmd_CFS_CUT_UNLOAD(unload_gcmd)
                    self.cmd_CFS_LOAD(gcmd)
                else:
                    self._start_feed(
                        gcmd, slot, require_confirm=False,
                        require_selected=True)
                    self._ensure_purge_position(gcmd)
            else:
                if active_mask:
                    # A spool pushed in by hand or an interrupted unload
                    # leaves a slot selected with nothing at the nozzle,
                    # often with its filament still in the hub. Idling alone
                    # does not clear that (10-25), so rewind the selected
                    # slot home first, then idle.
                    gcmd.respond_info(
                        "CFS still selects mask 0x%02x with the nozzle clear;"
                        " rewinding it before the load" % active_mask)
                    self._run_actuation(
                        'SET_BOX_MODE IDLE',
                        [0xff, 0x04, 0x00, self.MODE_IDLE])
                    self._finish_unload(active_mask, report_error=False)
                    left = self._measuring().get('active_mask', 0)
                    if left:
                        raise gcmd.error(
                            "CFS still selects mask 0x%02x after a rewind; "
                            "pull that slot's filament back by hand" % left)
                self._ensure_purge_position(gcmd)
                self.cmd_CFS_LOAD(gcmd)

            self.cmd_CFS_PURGE(gcmd)
        except Exception:
            self._best_effort_stop(mask)
            self.status.update({
                'feed_enabled': False,
                'box_mode_code': self.BOX_MODE_IDLE,
                'box_mode': 'idle',
            })
            raise
        finally:
            self.print_start_transaction = False

        self.status['last_print_start_slot'] = slot
        gcmd.respond_info(
            "CFS print start complete: slot %s loaded, purged, and armed"
            % slot)

def load_config(config):
    return K2CFS(config)
