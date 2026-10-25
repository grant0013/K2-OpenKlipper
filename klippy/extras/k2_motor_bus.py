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
#                   f7 <addr> 0b 00 0c 00*8 fb)
#   00 14 02      = seen once post-home (MOTOR_CHECK_PROTECTION_AFTER_HOME?)
#   00 08 <n>     = seen with n=5..8 on addr 0x82 during homing (unknown -
#                   possibly current/speed staging)
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
#
# Gcode:
#   MOTOR_STALL_MODE DATA=<1|2>     - sent to both 0x81 and 0x82
#   MOTOR_BUS_SEND ADDR=<int> PAYLOAD=<hex>  - raw frame (len+crc added)
#   MOTOR_INIT                      - run the wake-up again

import logging
import struct

import mcu

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
        self.init_motors = config.getboolean('init_motors', False)
        self.wake_delay = config.getfloat('wake_delay', 2.2, minval=0.)
        self.motors = {}
        self.motors_ready = not self.init_motors
        self.cfs_addr = config.getint('cfs_addr', 1, minval=1, maxval=127)
        uniid = config.get('cfs_uniid', '').replace(' ', '')
        self.cfs_uniid = bytes.fromhex(uniid) if uniid else None
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
        if self.init_motors or self.cfs_uniid:
            self.printer.register_event_handler("klippy:ready",
                                                self._handle_ready)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('MOTOR_STALL_MODE', self.cmd_MOTOR_STALL_MODE,
                               desc="Set X/Y closed-loop motor stall mode "
                                    "(1=homing/endstop, 2=print protection)")
        gcode.register_command('MOTOR_BUS_SEND', self.cmd_MOTOR_BUS_SEND,
                               desc="Send raw frame on the motor RS485 bus")
        gcode.register_command('MOTOR_INIT', self.cmd_MOTOR_INIT,
                               desc="Wake the closed-loop motors")
        self.printer.register_event_handler("klippy:disconnect",
                                            self._close)

    # --- wire helpers ---
    @staticmethod
    def _crc8(data):
        crc = 0
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc << 1) ^ 0x07) & 0xff if crc & 0x80 \
                    else (crc << 1) & 0xff
        return crc

    def _build(self, addr, payload):
        body = bytes([len(payload) + 1]) + bytes(payload)
        return bytes([0xf7, addr]) + body + bytes([self._crc8(body)])

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

    def _read_frame(self, timeout, read_len):
        # Poll between reactor pauses rather than block in a serial read: a
        # CFS stage can take 20+ s to answer, and klippy has to keep driving
        # its heaters meanwhile (a blocked reactor let the hotend drift and
        # tripped verify_heater mid tool change).
        deadline = self.reactor.monotonic() + timeout
        self.ser.timeout = 0
        buf = b''
        while True:
            buf += self.ser.read(256)
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
                return buf
            self.reactor.pause(min(deadline, now + 0.005))

    def xfer(self, addr, payload, read_len=64, timeout=0.2):
        """Build and send a frame, then return one bounded response frame."""
        with self._xfer_lock:
            self._open()
            frame = self._build(addr, payload)
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            resp = self._read_frame(timeout, read_len)
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
            values.append("%02x=%s" % (pid, "%g" % struct.unpack(
                '<f', resp[5:9])[0] if len(resp) >= 10 else '?'))
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
        return self.motors_ready

    def _wake_once(self):
        before = None
        self.motors = {}
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
        except Exception:
            logging.exception("k2_motor_bus: motor wake-up failed")
        if before is not None:
            self._release_wake_pins(before)
        return bool(self.motors) and all(self.motors.values())

    def get_status(self, eventtime):
        return {'motors_ready': self.motors_ready,
                'motors': dict(self.motors)}

    # --- gcode ---
    def cmd_MOTOR_STALL_MODE(self, gcmd):
        mode = gcmd.get_int('DATA', 1, minval=0, maxval=255)
        out = []
        for addr in (0x81, 0x82):
            frame, resp = self.xfer(addr, [0x00, 0x11, mode])
            out.append("addr %02x: sent %s resp %s"
                       % (addr, frame.hex(' '),
                          resp.hex(' ') if resp else '(none)'))
        gcmd.respond_info("MOTOR_STALL_MODE DATA=%d\n%s"
                          % (mode, "\n".join(out)))

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
        frame, resp = self.xfer(addr, payload, read_len, timeout)
        gcmd.respond_info("sent %s\nresp %s"
                          % (frame.hex(' '),
                             resp.hex(' ') if resp else '(none)'))

def load_config(config):
    return K2MotorBus(config)
