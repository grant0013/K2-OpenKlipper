# k2_motor_bus.py - direct RS485 access to the K2's closed-loop motor bus
# for MAINLINE Klipper (the Creality stack drives this bus via its closed
# serial_485/motor_control .so modules; mainline never opens ttyS5, so this
# module can own it).
#
# Protocol (reverse-engineered 2026-07-07 from strace captures, see
# k2-dev:/root/k2-fork/motor_re/):
#   frame = 0xF7 | addr | len | payload... | crc8
#     - len   = number of bytes after the len byte (payload + crc)
#     - crc8  = poly 0x07, init 0x00, unreflected, xorout 0x00, computed over
#               [len, payload...] (preamble and addr EXCLUDED — verified
#               against 10 captured frames)
#   X/Y motor MCUs = addr 0x81, 0x82 (assigned by auto_addr; E motor is on a
#   separate bus behind the nozzle MCU's transparent_send bridge, also 0x81).
#
# Known commands (from live capture of stock homing):
#   00 11 <mode>  = MOTOR_STALL_MODE (mode 1 before homing arms stall-as-
#                   endstop on PB11/PB12; mode 2 after homing = print
#                   protection). Stock homing_override sends DATA=1 pre-home,
#                   DATA=2 post-home.
#   00 0c 0b      = periodic motor status/stall query (response seen:
#                   f7 <addr> 0b 00 0c 00*8 fb). Reply byte 3 is a status
#                   latch (1 stall, 2 error, 4 warning), so a faulted motor
#                   answers 02 0c <error u32> <warning u32>; decoded in
#                   k2_motor_proto.py
#   00 14 02      = seen once post-home (MOTOR_CHECK_PROTECTION_AFTER_HOME?)
#   00 08 <n>     = get value n, float32 reply: 5 position, 6 speed, 7 id,
#                   8 iq, 0x11 controller MCU temperature (seen with n=5..8
#                   on addr 0x82 during homing)
#
# Motor wake-up (captured from stock motor_control 2026-10-24, motors in
# power-on state). Until this runs the drivers ignore every command, so the
# stall watch can't be armed and homing fails ("Endstop stepper_y still
# triggered after retract"). Stock used to do it before mainline took over.
#   ff 00 0e 01   = broadcast wake to addr 0xff (no reply); stock then waits
#                   ~2.2 s before talking to the motors
#   00 0e 02      = handshake, reply 02 0e <own addr>
#   00 0c 0b      = status / protection check (MOTOR_..._PROTECTION DATA=11)
#   00 0c 05      = clear error/warning codes (no reply), sent twice
#   00 06 02 <id> = read one flash parameter (float32 LE); X/Y only
#   00 06 03      = end of parameter read-back, reply 00 06 01
# During the wake stock also holds the X/Y step/dir lines (main MCU PB7-PB10,
# shared with the steppers) at PB9=0 PB10=0 PB7=1 PB8=0, then lets them go.
# The drivers sample them: with PB7 low the 0x82 motor never answers. The
# extruder driver samples its lines too (nozzle MCU PB4 dir, PB5 step): with
# its dir line high, as a print often leaves it, it ignores the wake.
# wake_pins holds all of them and afterwards restores what the steppers left.
# The extruder motor is on its own RS485 bus behind the nozzle MCU, reached
# with transparent_send (oid, frame, timeout_ms) -> transparent_response.
# It gets the same broadcast, then 0e 02, 11 02, 0c 0b and 0c 05 x2 at 0x81.
#
# The CFS also forgets its bus address at power-off. Stock auto_addr gives it
# back with a broadcast to 0xfe: 00 a0 <addr> <12-byte unique id>, which the
# CFS answers from its new address. Without it k2_cfs sees no CFS at all.
#
# Config:
#   [k2_motor_bus]
#   #serial: /dev/ttyS5
#   #baud: 230400
#   #init_motors: False      - wake the motors at klippy:ready
#   #extruder_motor_mcu:     - MCU with the extruder motor bus (nozzle_mcu)
#   #wake_delay: 2.2
#   #wake_pins:              - main MCU pin levels held during the wake,
#                              e.g. PB9=0, PB10=0, PB7=1, PB8=0
#   #cfs_uniid:              - CFS unique id (hex, from [auto_addr]
#                              mb_addr_table_uniids); give it its address at
#                              ready when it doesn't answer
#   #cfs_addr: 1
#   #reply_check: strict     - strict: a reply must pass CRC, come from the
#                              address we asked (any for a broadcast) and,
#                              for a motor, echo our function byte; anything
#                              else (noise, a late CFS reply) is skipped and
#                              counted. Motor reads get one retry.
#                              log: count and log only, take replies as
#                              before. off: no checks.
#   #stall_mode_check: warn  - MOTOR_STALL_MODE reads the motor's echo of
#                              the mode. warn: report a missing echo; abort:
#                              raise an error (stops the homing); off.
#   #homing_fault_check: off - before MOTOR_STALL_MODE DATA=1: read X/Y
#                              protection, clear latched faults, read again.
#                              warn: report an error still latched after the
#                              clear; abort: raise an error; off: skip.
#   #temp_poll_interval: 0   - seconds between motor MCU temperature reads
#                              (00 08 11); 0 = only on MOTOR_TEMPS. Skipped
#                              while the bus is busy or the CFS is working.
#   #temp_warn: 100          - warn above this (firmware limit 115 C)
#
# Gcode:
#   MOTOR_STALL_MODE DATA=<1|2>     - sent to both 0x81 and 0x82
#   MOTOR_BUS_SEND ADDR=<int> PAYLOAD=<hex> [CHECK=1] - raw frame (len+crc
#                                     added); CHECK=1 waits for a valid reply
#   MOTOR_INIT                      - run the wake-up again
#   MOTOR_FAULTS                    - read and decode X/Y/E protection
#   MOTOR_CLEAR_FAULTS [BLOCK=1]    - read, clear, read again; BLOCK=1 raises
#                                     an error if an error is still latched
#   MOTOR_TEMPS                     - read the X/Y/E controller temperatures
#   MOTOR_E_ADDR                    - ask the extruder controller its address
#                                     (broadcast 00 0e 02); diagnostic only
#
# Reply validation, the protection, stall mode, temperature and address
# reads and the fault recovery: portions adapted from Jacob10383's K2 Plus
# custom firmware (https://github.com/Jacob10383/k2-plus-custom-firmware,
# extras/motor_control.py and extras/serial_485.py), GPLv3. See also
# k2_motor_proto.py.

import logging
import struct

import mcu
from . import k2_motor_proto as proto

WAKE = (0x00, 0x0e, 0x01)
HANDSHAKE = (0x00, 0x0e, 0x02)
STATUS = (0x00, 0x0c, 0x0b)
CLEAR_ERRORS = (0x00, 0x0c, 0x05)
END_PARAM_READ = (0x00, 0x06, 0x03)
# Flash parameters stock reads back from each X/Y motor at start-up
PARAM_IDS = (0x09, 0x0a, 0x19, 0x46, 0x56, 0x57, 0x66, 0x67, 0x74, 0x76,
             0x77, 0x78, 0x79, 0xbb, 0xbc)
BUS_MOTORS = (('x', 0x81), ('y', 0x82))
EXTRUDER_ADDR = 0x81
WAKE_ATTEMPTS = 3
CFS_BOX_STATE = (0x00, 0xa2)
CHECK_MODES = {'strict': 'strict', 'log': 'log', 'off': 'off'}
GUARD_MODES = {'off': 'off', 'warn': 'warn', 'abort': 'abort'}
# Motor requests that only read, so a second try is harmless: protection
# status, parameter read, stall mode read. (The 0e 02 handshake has its own
# retries in the wake.)
READ_REQUESTS = ((0x0c, 0x0b), (0x06, 0x02), (0x11, 0x04))
# A CFS command this recent means a load/unload is likely still running
CFS_QUIET_TIME = 5.
TEMP_HYSTERESIS = 5.

def is_read_request(addr, payload):
    if addr not in proto.MOTOR_ADDRS or len(payload) < 3:
        return False
    return (payload[1] == proto.FUNC_GET
            or (payload[1], payload[2]) in READ_REQUESTS)

class K2MotorBus:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.serialport = config.get('serial', '/dev/ttyS5')
        self.baud = config.getint('baud', 230400, minval=9600)
        self.ser = None
        self.reactor = self.printer.get_reactor()
        # A reactor mutex: a caller waiting for the bus pauses its greenlet
        # instead of blocking the whole klippy thread.
        self._xfer_lock = self.reactor.mutex()
        self._e_lock = self.reactor.mutex()
        self.init_motors = config.getboolean('init_motors', False)
        self.wake_delay = config.getfloat('wake_delay', 2.2, minval=0.)
        self.motors = {}
        self.motors_ready = not self.init_motors
        self.cfs_addr = config.getint('cfs_addr', 1, minval=1, maxval=127)
        uniid = config.get('cfs_uniid', '').replace(' ', '')
        self.cfs_uniid = bytes.fromhex(uniid) if uniid else None
        self.reply_check = config.getchoice('reply_check', CHECK_MODES,
                                            'strict')
        self.stall_mode_check = config.getchoice('stall_mode_check',
                                                 GUARD_MODES, 'warn')
        self.homing_fault_check = config.getchoice('homing_fault_check',
                                                   GUARD_MODES, 'off')
        self.temp_poll_interval = config.getfloat('temp_poll_interval', 0.,
                                                  minval=0.)
        if 0. < self.temp_poll_interval < 5.:
            raise config.error("temp_poll_interval must be 0 or at least 5")
        self.temp_warn = config.getfloat('temp_warn', 100., above=0.,
                                         maxval=proto.MCU_TEMP_MAX)
        # Rejected replies by reason, plus retries and failed reads
        self.bus_errors = {'crc': 0, 'len': 0, 'addr': 0, 'func': 0,
                           'retries': 0, 'read_failures': 0}
        self._reject_logged = 0.
        self.temps = {}
        self._temp_hot = set()
        self.e_reported_addr = None
        self.calibration_warnings = []
        self.last_faults = {}
        # (name, pin, oid, wake level, chip); chip = per-MCU commands
        self.wake_pins = []
        chips = {}
        for item in config.getlist('wake_pins', ()):
            name, value = [part.strip() for part in item.split('=')]
            chip_name, pin = (name.split(':', 1) if ':' in name
                              else ('mcu', name))
            chip = chips.get(chip_name)
            if chip is None:
                chip = chips[chip_name] = {
                    'mcu': mcu.get_printer_mcu(self.printer, chip_name)}
                chip['mcu'].register_config_callback(
                    lambda chip=chip: self._build_pin_config(chip))
            oid = chip['mcu'].create_oid()
            chip.setdefault('oid', oid)
            # Same pin as a stepper: value 0 matches its idle level
            chip['mcu'].add_config_cmd(
                "config_digital_out oid=%d pin=%s value=0 default_value=0"
                " max_duration=0" % (oid, pin))
            self.wake_pins.append((name, pin, oid, int(value), chip))
        self.e_mcu = self.e_send = None
        e_mcu_name = config.get('extruder_motor_mcu', None)
        if e_mcu_name:
            self.e_mcu = mcu.get_printer_mcu(self.printer, e_mcu_name)
            self.e_oid = self.e_mcu.create_oid()
            self.e_mcu.add_config_cmd("config_transparent oid=%d"
                                      % (self.e_oid,))
            self.e_mcu.register_config_callback(self._build_config)
        if self.init_motors or self.cfs_uniid or self.temp_poll_interval:
            self.printer.register_event_handler("klippy:ready",
                                                self._handle_ready)
        gcode = self.gcode = self.printer.lookup_object('gcode')
        gcode.register_command('MOTOR_STALL_MODE', self.cmd_MOTOR_STALL_MODE,
                               desc="Set X/Y closed-loop motor stall mode "
                                    "(1=homing/endstop, 2=print protection)")
        gcode.register_command('MOTOR_BUS_SEND', self.cmd_MOTOR_BUS_SEND,
                               desc="Send raw frame on the motor RS485 bus")
        gcode.register_command('MOTOR_INIT', self.cmd_MOTOR_INIT,
                               desc="Wake the closed-loop motors")
        gcode.register_command('MOTOR_FAULTS', self.cmd_MOTOR_FAULTS,
                               desc="Read and decode X/Y/E motor protection")
        gcode.register_command('MOTOR_CLEAR_FAULTS',
                               self.cmd_MOTOR_CLEAR_FAULTS,
                               desc="Read, clear and re-read motor faults")
        gcode.register_command('MOTOR_TEMPS', self.cmd_MOTOR_TEMPS,
                               desc="Read the motor controller temperatures")
        gcode.register_command('MOTOR_E_ADDR', self.cmd_MOTOR_E_ADDR,
                               desc="Ask the extruder motor controller its "
                                    "bus address (diagnostic)")
        self.printer.register_event_handler("klippy:disconnect",
                                            self._close)

    # --- wire helpers ---
    @staticmethod
    def _crc8(data):
        return proto.crc8(data)

    def _build(self, addr, payload):
        return proto.build_frame(addr, payload)

    def _open(self):
        if self.ser is not None:
            return
        import serial
        self.ser = serial.Serial(self.serialport, self.baud, timeout=0.2,
                                 exclusive=True)
        logging.info("k2_motor_bus: opened %s @ %d", self.serialport,
                     self.baud)

    def _close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _read_frame(self, timeout, read_len, match=None):
        # Poll between reactor pauses rather than block in a serial read: a
        # CFS stage can take 20+ s to answer, and klippy has to keep driving
        # its heaters meanwhile (a blocked reactor let the hotend drift and
        # tripped verify_heater mid tool change).
        # match = (addr, func) keeps reading past frames that fail the CRC
        # or are not the reply we asked for, and returns b'' at the deadline.
        deadline = self.reactor.monotonic() + timeout
        self.ser.timeout = 0
        buf = b''
        while True:
            buf += self.ser.read(256)
            if match is not None:
                frame, buf, rejects = proto.find_reply(
                    buf, match[0], match[1], max_len=read_len)
                self._note_rejects(rejects, match)
                if frame is not None:
                    return frame
            else:
                # Ignore any stale/noise bytes until the next frame preamble.
                start = buf.find(b'\xf7')
                buf = buf[start:] if start >= 0 else b''
                if len(buf) >= 3:
                    frame_len = buf[2]
                    if frame_len < 1 or frame_len + 3 > read_len:
                        return buf[:3]
                    if len(buf) >= frame_len + 3:
                        return buf[:frame_len + 3]
            now = self.reactor.monotonic()
            if now >= deadline:
                return b'' if match is not None else buf
            self.reactor.pause(min(deadline, now + 0.005))

    def _note_rejects(self, rejects, match):
        if not rejects:
            return
        for why in rejects:
            self.bus_errors[why] += 1
        # One log line per 10 s at most: a noisy bus must not flood the log
        now = self.reactor.monotonic()
        if now - self._reject_logged >= 10.:
            self._reject_logged = now
            logging.info("k2_motor_bus: skipped reply frames (%s) waiting "
                         "for addr %02x; totals %s", ",".join(rejects),
                         match[0], self.bus_errors)

    def xfer(self, addr, payload, read_len=64, timeout=0.2, check=None,
             retry=None):
        """Build and send a frame, then return one bounded response frame.

        With reply_check strict (or check=True) only a valid reply from addr
        is returned, else b''; motor reads are sent a second time when the
        first gets no valid reply. k2_cfs calls this too: CFS replies get the
        CRC and address checks but not the function check."""
        mode = self.reply_check
        if check is not None:
            mode = 'strict' if check else 'off'
        func = proto.request_func(addr, payload)
        match = (addr, func) if mode == 'strict' else None
        if retry is None:
            retry = mode == 'strict' and is_read_request(addr, payload)
        frame = self._build(addr, payload)
        with self._xfer_lock:
            self._open()
            for attempt in range(2 if retry else 1):
                if attempt:
                    self.bus_errors['retries'] += 1
                self.ser.reset_input_buffer()
                self.ser.write(frame)
                self.ser.flush()
                resp = self._read_frame(timeout, read_len, match)
                if resp or not retry:
                    break
            else:
                self.bus_errors['read_failures'] += 1
        if mode == 'log' and resp:
            why = proto.reply_problem(resp, addr, func) \
                if len(resp) >= 6 else 'len'
            if why is not None:
                self._note_rejects([why], (addr, func))
        return frame, resp

    # kept for any older references
    _xfer = xfer

    def _build_config(self):
        self.e_send = self.e_mcu.lookup_query_command(
            "transparent_send oid=%c write=%*s timeout_ms=%u",
            "transparent_response oid=%c read=%*s", oid=self.e_oid,
            cq=self.e_mcu.alloc_command_queue())

    def _build_pin_config(self, chip):
        chip['set'] = chip['mcu'].lookup_command(
            "update_digital_out oid=%c value=%c")
        chip['query'] = chip['mcu'].lookup_query_command(
            "query_gpio_status oid=%c gpio_pin=%c",
            "gpio_status oid=%c status=%c", oid=chip['oid'])

    def _read_wake_pins(self):
        return [chip['query'].send([chip['oid'], pin])['status']
                for name, pin, oid, value, chip in self.wake_pins]

    def _describe_pins(self, levels):
        return " ".join("%s=%d" % (pin[0], level)
                        for pin, level in zip(self.wake_pins, levels))

    def _hold_wake_pins(self):
        """Set the wake levels; return the levels to put back afterwards."""
        before = self._read_wake_pins()
        for name, pin, oid, value, chip in self.wake_pins:
            chip['set'].send([oid, value])
        logging.info("k2_motor_bus: wake pins before %s, held %s",
                     self._describe_pins(before),
                     self._describe_pins(self._read_wake_pins()))
        return before

    def _release_wake_pins(self, before):
        # Put back what the steppers left. A stepper flips its dir pin
        # relative to the current level, so forcing a level here would
        # reverse its later moves (after a print the extruder dir is often
        # left high).
        for (name, pin, oid, value, chip), level in zip(self.wake_pins,
                                                         before):
            chip['set'].send([oid, level])
        logging.info("k2_motor_bus: wake pins released %s",
                     self._describe_pins(self._read_wake_pins()))

    def e_xfer(self, addr, payload, timeout_ms=50):
        """Send a frame on the extruder motor bus behind the nozzle MCU."""
        frame = self._build(addr, payload)
        # One query at a time: the safety watch and the temperature poll
        # both talk to this bus from reactor timers
        with self._e_lock:
            params = self.e_send.send([self.e_oid, frame, timeout_ms])
        return frame, bytes(params['read'])

    # --- motor wake-up ---
    def _handle_ready(self):
        self.reactor.register_callback(lambda eventtime: self._start_up())

    def _start_up(self):
        if self.init_motors:
            self.wake_motors()
        if self.cfs_uniid:
            self.address_cfs()
        if self.temp_poll_interval:
            self.reactor.register_timer(
                self._poll_temps,
                self.reactor.monotonic() + self.temp_poll_interval)

    def address_cfs(self):
        """Give the CFS its bus address if it lost it at power-off."""
        try:
            if self.xfer(self.cfs_addr, CFS_BOX_STATE)[1]:
                return True
            for attempt in range(2):
                self.xfer(0xfe, (0x00, 0xa0, self.cfs_addr) +
                          tuple(self.cfs_uniid))
                self._pause(0.5)
                if self.xfer(self.cfs_addr, CFS_BOX_STATE)[1]:
                    logging.info("k2_motor_bus: CFS given address %d",
                                 self.cfs_addr)
                    return True
        except Exception:
            logging.exception("k2_motor_bus: CFS addressing failed")
        logging.warning("k2_motor_bus: CFS does not answer at address %d",
                        self.cfs_addr)
        return False

    def _pause(self, delay):
        self.reactor.pause(self.reactor.monotonic() + delay)

    def _start_motor(self, name, addr, send, steps):
        for attempt in range(3):
            resp = send(addr, HANDSHAKE)
            if len(resp) >= 6 and resp[4] == 0x0e and resp[5] == addr:
                break
            self._pause(0.5)
        else:
            logging.warning("k2_motor_bus: motor %s (%02x) did not answer "
                            "the handshake", name, addr)
            return False
        for payload in steps:
            send(addr, payload)
        return True

    def _read_params(self, name, addr):
        values = []
        for pid in PARAM_IDS:
            resp = self.xfer(addr, (0x00, 0x06, 0x02, pid))[1]
            value = None
            if len(resp) >= 10:
                value = struct.unpack('<f', resp[5:9])[0]
            values.append("%02x=%s" % (pid, '?' if value is None
                                       else "%g" % value))
            # Electrical offset and its error: near zero means the motor
            # was never calibrated (check from Jacob10383's firmware)
            if (pid in proto.CALIBRATION_PARAM_IDS
                    and proto.is_near_zero_calibration(value)):
                self.calibration_warnings.append(
                    "motor %s (%02x) calibration param %02x is %g (at or "
                    "below %.2f): it may not be calibrated"
                    % (name.upper(), addr, pid, value,
                       proto.CALIBRATION_NEAR_ZERO))
        done = self.xfer(addr, END_PARAM_READ)[1]
        logging.info("k2_motor_bus: motor %s params %s, end %s", name,
                     " ".join(values), done.hex(' ') if done else '(none)')

    def wake_motors(self):
        """Replay stock motor_control's start-up handshake. After a power
        cycle one driver can miss the first pass, so repeat it a few times."""
        for attempt in range(1, WAKE_ATTEMPTS + 1):
            if self._wake_once():
                break
            logging.warning("k2_motor_bus: wake pass %d of %d incomplete: %s",
                            attempt, WAKE_ATTEMPTS, self.motors)
        self.motors_ready = bool(self.motors) and all(self.motors.values())
        logging.info("k2_motor_bus: motors %s, ready=%s", self.motors,
                     self.motors_ready)
        for msg in self.calibration_warnings:
            logging.warning("k2_motor_bus: %s", msg)
            self.gcode.respond_raw("!! Motor check: %s" % (msg,))
        return self.motors_ready

    def _wake_once(self):
        before = None
        self.motors = {}
        self.calibration_warnings = []
        try:
            bus = lambda addr, payload: self.xfer(addr, payload)[1]
            if self.wake_pins:
                before = self._hold_wake_pins()
                self._pause(0.7)
            self.xfer(0xff, WAKE, timeout=0.05)
            if self.e_send is not None:
                self.e_xfer(0xff, WAKE, timeout_ms=20)
            self._pause(self.wake_delay)
            for name, addr in BUS_MOTORS:
                self.motors[name] = self._start_motor(
                    name, addr, bus, (STATUS, CLEAR_ERRORS, CLEAR_ERRORS))
            if before is not None:
                self._release_wake_pins(before)
                before = None
            for name, addr in BUS_MOTORS:
                if self.motors[name]:
                    self._read_params(name, addr)
            if self.e_send is not None:
                ebus = lambda addr, payload: self.e_xfer(addr, payload)[1]
                self.motors['e'] = self._start_motor(
                    'e', EXTRUDER_ADDR, ebus,
                    ((0x00, 0x11, 0x02), STATUS, CLEAR_ERRORS, CLEAR_ERRORS))
                if self.motors['e']:
                    self._log_extruder_addr()
        except Exception:
            logging.exception("k2_motor_bus: motor wake-up failed")
        if before is not None:
            self._release_wake_pins(before)
        return bool(self.motors) and all(self.motors.values())

    # --- protection, stall mode, temperature, address ---
    def motor_addr(self, name):
        return EXTRUDER_ADDR if name == 'e' else dict(BUS_MOTORS)[name]

    def motor_names(self):
        names = [name for name, addr in BUS_MOTORS]
        if self.e_send is not None:
            names.append('e')
        return names

    def motor_request(self, name, payload, addr=None, timeout=0.2):
        """Send a request to motor x, y or e (via the nozzle MCU). Returns
        (parsed reply or None, raw reply bytes). Only a whole, valid reply
        from addr that echoes our function byte is parsed."""
        if addr is None:
            addr = self.motor_addr(name)
        func = proto.request_func(addr, payload)
        if name == 'e':
            if self.e_send is None:
                return None, b''
            # The nozzle MCU hands back whatever it read; find the reply
            for attempt in range(2 if is_read_request(addr, payload) else 1):
                raw = self.e_xfer(addr, payload)[1]
                resp = proto.find_reply(raw, addr, func)[0]
                if resp is not None:
                    break
        else:
            raw = resp = self.xfer(addr, payload, timeout=timeout)[1]
        try:
            frame = proto.parse_frame(resp)
        except ValueError:
            return None, raw
        if proto.reply_problem(resp, addr, func) is not None:
            return None, raw
        return frame, raw

    def query_protection(self, name, addr=None):
        """Decoded protection status (00 0c 0b) of one motor, or None when
        it gives no valid reply."""
        frame, raw = self.motor_request(name, STATUS, addr)
        if frame is None:
            return None
        detail = proto.decode_protection(frame)
        detail['raw'] = raw.hex(' ')
        self.last_faults[name] = detail
        return detail

    def clear_faults(self, name, addr=None):
        # No reply; stock sends it twice
        if addr is None:
            addr = self.motor_addr(name)
        for i in range(2):
            if name == 'e':
                if self.e_send is not None:
                    self.e_xfer(addr, CLEAR_ERRORS)
            else:
                self.xfer(addr, CLEAR_ERRORS, timeout=.05)

    def recover_faults(self, names=None, always_clear=False, addrs=None):
        """Read, clear what is latched, read again (after Jacob10383's
        recover_faults_for_homing_start). always_clear clears every motor
        even when it reads clean or does not answer. Returns (before, after,
        errors still latched after the clear)."""
        names = tuple(names or self.motor_names())
        addrs = addrs or {}
        before = dict((n, self.query_protection(n, addrs.get(n)))
                      for n in names)
        after = dict(before)
        for n in names:
            d = before[n]
            if always_clear or (d is not None
                                and (d['active'] or d['status'])):
                self.clear_faults(n, addrs.get(n))
                after[n] = self.query_protection(n, addrs.get(n))
        persistent = dict((n, d) for n, d in after.items()
                          if d is not None and d['has_error'])
        return before, after, persistent

    @staticmethod
    def describe_faults(details):
        return ", ".join("%s: %s" % (n.upper(), "no reply" if d is None
                                     else proto.describe_protection(d))
                         for n, d in sorted(details.items()))

    def _stall_ack(self, resp, addr):
        try:
            frame = proto.parse_frame(resp)
        except ValueError:
            return None
        if (proto.reply_problem(resp, addr, proto.FUNC_STALL_MODE)
                or not frame['payload']):
            return None
        return frame['payload'][0]

    def set_stall_mode(self, mode):
        """Send the stall mode to X and Y. The motor echoes the mode it
        took (Jacob10383's set_stall_mode validator); a missing echo gets
        one more try. Returns [(name, addr, confirmed, frame, resp)]."""
        results = []
        attempts = 1 if self.stall_mode_check == 'off' else 2
        for name, addr in BUS_MOTORS:
            for attempt in range(attempts):
                frame, resp = self.xfer(addr, [0x00, proto.FUNC_STALL_MODE,
                                               mode])
                ok = self._stall_ack(resp, addr) == mode
                if ok:
                    break
            results.append((name, addr, ok, frame, resp))
        return results

    def read_temp(self, name):
        frame, raw = self.motor_request(
            name, (0x00, proto.FUNC_GET, proto.GET_MCU_TEMP), timeout=0.1)
        if frame is None or len(frame['payload']) != 4:
            return None
        return round(proto.decode_float(frame['payload']), 1)

    def read_temps(self):
        for name in self.motor_names():
            temp = self.read_temp(name)
            if temp is None:
                continue
            self.temps[name] = temp
            if temp > self.temp_warn and name not in self._temp_hot:
                self._temp_hot.add(name)
                msg = ("Motor %s controller MCU at %.0f C (warning above "
                       "%.0f C, firmware limit %.0f C)"
                       % (name.upper(), temp, self.temp_warn,
                          proto.MCU_TEMP_MAX))
                logging.warning("k2_motor_bus: %s", msg)
                self.gcode.respond_raw("!! %s" % (msg,))
            elif temp < self.temp_warn - TEMP_HYSTERESIS:
                self._temp_hot.discard(name)
        return dict(self.temps)

    def _bus_quiet(self, eventtime):
        if self._xfer_lock.test() or self._e_lock.test():
            return False
        cfs = self.printer.lookup_object('k2_cfs', None)
        last = getattr(cfs, 'last_actuation_time', 0.)
        return not (last and eventtime - last < CFS_QUIET_TIME)

    def _poll_temps(self, eventtime):
        if not self.motors_ready:
            return eventtime + self.temp_poll_interval
        if not self._bus_quiet(eventtime):
            return eventtime + 1.
        try:
            self.read_temps()
        except Exception:
            logging.exception("k2_motor_bus: temperature poll failed")
        return self.reactor.monotonic() + self.temp_poll_interval

    def read_extruder_addr(self):
        """Ask the extruder controller its address with a broadcast 00 0e 02
        (it is the only device on its bus). 0x80 means it has none latched;
        None means no reply. Diagnostic only: we keep using EXTRUDER_ADDR."""
        frame, raw = self.motor_request('e', HANDSHAKE, addr=0xff)
        addr = None
        if (frame is not None and frame['func'] == proto.FUNC_READ_ADDR
                and len(frame['payload']) == 1):
            addr = frame['payload'][0]
        self.e_reported_addr = addr
        return addr

    def _log_extruder_addr(self):
        addr = self.read_extruder_addr()
        if addr == EXTRUDER_ADDR:
            logging.info("k2_motor_bus: extruder controller reports address "
                         "%02x", addr)
        else:
            logging.warning("k2_motor_bus: extruder controller reports "
                            "address %s, expected %02x",
                            "(no reply)" if addr is None else "%02x" % addr,
                            EXTRUDER_ADDR)

    def get_status(self, eventtime):
        faults = dict((n, {'status': d['status'],
                           'error_code': d['error_code'],
                           'warning_code': d['warning_code'],
                           'errors': list(d['errors']),
                           'warnings': list(d['warnings'])})
                      for n, d in self.last_faults.items())
        return {'motors_ready': self.motors_ready,
                'motors': dict(self.motors),
                'faults': faults,
                'temps': dict(self.temps),
                'e_reported_addr': self.e_reported_addr,
                'calibration_warnings': list(self.calibration_warnings),
                'bus_errors': dict(self.bus_errors)}

    # --- gcode ---
    def cmd_MOTOR_STALL_MODE(self, gcmd):
        mode = gcmd.get_int('DATA', 1, minval=0, maxval=255)
        if mode == 1 and self.homing_fault_check != 'off':
            # Their homing start: a fault latched from before must not be
            # mistaken for the stall that ends the homing move
            before, after, persistent = self.recover_faults(('x', 'y'))
            if persistent:
                msg = ("motor error still latched after a clear: %s"
                       % (self.describe_faults(persistent),))
                logging.warning("k2_motor_bus: %s", msg)
                if self.homing_fault_check == 'abort':
                    raise gcmd.error("MOTOR_STALL_MODE: %s. Homing stopped; "
                                     "check the axis, then power cycle"
                                     % (msg,))
                gcmd.respond_raw("!! MOTOR_STALL_MODE: %s" % (msg,))
        out = []
        missing = []
        for name, addr, ok, frame, resp in self.set_stall_mode(mode):
            out.append("addr %02x: sent %s resp %s%s"
                       % (addr, frame.hex(' '),
                          resp.hex(' ') if resp else '(none)',
                          " (confirmed)" if ok else ""))
            if not ok:
                missing.append("%s (%02x)" % (name.upper(), addr))
        gcmd.respond_info("MOTOR_STALL_MODE DATA=%d\n%s"
                          % (mode, "\n".join(out)))
        if missing and self.stall_mode_check != 'off':
            msg = ("stall mode %d not confirmed by motor %s"
                   % (mode, ", ".join(missing)))
            logging.warning("k2_motor_bus: %s", msg)
            if self.stall_mode_check == 'abort':
                raise gcmd.error("MOTOR_STALL_MODE: %s. Homing stopped"
                                 % (msg,))
            gcmd.respond_raw("!! MOTOR_STALL_MODE: %s" % (msg,))

    def cmd_MOTOR_FAULTS(self, gcmd):
        lines = []
        for name in self.motor_names():
            d = self.query_protection(name)
            if d is None:
                lines.append("%s: no valid reply" % (name.upper(),))
                continue
            lines.append("%s: %s (status %02x, error %08x, warning %08x)"
                         % (name.upper(), proto.describe_protection(d),
                            d['status'], d['error_code'], d['warning_code']))
        gcmd.respond_info("Motor protection\n%s" % ("\n".join(lines),))

    def cmd_MOTOR_CLEAR_FAULTS(self, gcmd):
        block = gcmd.get_int('BLOCK', 0, minval=0, maxval=1)
        before, after, persistent = self.recover_faults()
        gcmd.respond_info("Motor faults before: %s\nafter: %s"
                          % (self.describe_faults(before),
                             self.describe_faults(after)))
        if persistent:
            msg = ("motor error still latched after a clear: %s"
                   % (self.describe_faults(persistent),))
            if block:
                raise gcmd.error("MOTOR_CLEAR_FAULTS: %s" % (msg,))
            gcmd.respond_raw("!! MOTOR_CLEAR_FAULTS: %s" % (msg,))

    def cmd_MOTOR_TEMPS(self, gcmd):
        temps = self.read_temps()
        gcmd.respond_info("Motor controller MCU temperatures: %s (warning "
                          "above %.0f C, firmware limit %.0f C)"
                          % (", ".join("%s %.1f C" % (n.upper(), t)
                                       for n, t in sorted(temps.items()))
                             or "no reply", self.temp_warn,
                             proto.MCU_TEMP_MAX))

    def cmd_MOTOR_E_ADDR(self, gcmd):
        if self.e_send is None:
            raise gcmd.error("No extruder_motor_mcu configured")
        addr = self.read_extruder_addr()
        if addr is None:
            msg = "no reply"
        elif addr == 0x80:
            msg = ("0x80: no address latched. Run MOTOR_INIT (it wakes the "
                   "controller with its dir and step lines low)")
        elif addr != EXTRUDER_ADDR:
            msg = ("0x%02x, but we talk to 0x%02x. The controller latches "
                   "0x81 + DIR + 2 * STEP at wake, so its lines were not "
                   "both low; run MOTOR_INIT" % (addr, EXTRUDER_ADDR))
        else:
            msg = "0x%02x (as expected)" % (addr,)
        gcmd.respond_info("Extruder motor controller address: %s" % (msg,))

    def cmd_MOTOR_INIT(self, gcmd):
        ok = self.wake_motors()
        gcmd.respond_info("Motors %s: %s" % ("ready" if ok else "NOT ready",
                                             self.motors))

    def cmd_MOTOR_BUS_SEND(self, gcmd):
        addr = gcmd.get_int('ADDR', minval=0, maxval=255)
        payload = bytes.fromhex(gcmd.get('PAYLOAD'))
        # Telemetry records (pos/spd samples, error detail) run past 64 bytes
        read_len = gcmd.get_int('READ_LEN', 64, minval=8, maxval=1024)
        timeout = gcmd.get_float('TIMEOUT', 0.2, minval=0.05, maxval=2.)
        # Raw by default (as before); CHECK=1 waits for a valid reply
        check = bool(gcmd.get_int('CHECK', 0, minval=0, maxval=1))
        frame, resp = self.xfer(addr, payload, read_len, timeout,
                                check=check, retry=False)
        note = ""
        try:
            parsed = proto.parse_frame(resp)
        except ValueError:
            parsed = None
            if resp:
                note = "\n(not a valid frame)"
        if parsed is not None and parsed['func'] == proto.FUNC_PROTECTION \
                and len(parsed['payload']) == 8:
            note = "\n%s" % (proto.describe_protection(
                proto.decode_protection(parsed)),)
        elif parsed is not None and addr in proto.MOTOR_ADDRS:
            status = proto.decode_frame_status(parsed['status'])
            flags = [k for k in ('stalled', 'faulted', 'warned') if status[k]]
            if flags:
                note = "\nstatus %02x: %s" % (parsed['status'],
                                              ", ".join(flags))
        gcmd.respond_info("sent %s\nresp %s%s"
                          % (frame.hex(' '),
                             resp.hex(' ') if resp else '(none)', note))

def load_config(config):
    return K2MotorBus(config)
