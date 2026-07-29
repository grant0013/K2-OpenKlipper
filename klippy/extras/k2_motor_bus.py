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
# Config:
#   [k2_motor_bus]
#   #serial: /dev/ttyS5
#   #baud: 230400
#
# Gcode:
#   MOTOR_STALL_MODE DATA=<1|2>     - sent to both 0x81 and 0x82
#   MOTOR_BUS_SEND ADDR=<int> PAYLOAD=<hex>  - raw frame (len+crc added)
#
# UNTESTED AGAINST HARDWARE as of 2026-07-07 - first live test must be done
# with Grant at the printer, printer idle, hand near power switch.

import logging
import threading
import time

class K2MotorBus:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.serialport = config.get('serial', '/dev/ttyS5')
        self.baud = config.getint('baud', 230400, minval=9600)
        self.ser = None
        self._xfer_lock = threading.Lock()
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('MOTOR_STALL_MODE', self.cmd_MOTOR_STALL_MODE,
                               desc="Set X/Y closed-loop motor stall mode "
                                    "(1=homing/endstop, 2=print protection)")
        gcode.register_command('MOTOR_BUS_SEND', self.cmd_MOTOR_BUS_SEND,
                               desc="Send raw frame on the motor RS485 bus")
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
        deadline = time.monotonic() + timeout
        # Ignore any stale/noise bytes until the next frame preamble.
        while time.monotonic() < deadline:
            self.ser.timeout = max(0., deadline - time.monotonic())
            if self.ser.read(1) == b'\xf7':
                break
        else:
            return b''
        self.ser.timeout = max(0., deadline - time.monotonic())
        header = self.ser.read(2)
        if len(header) != 2:
            return b'\xf7' + header
        frame_len = header[1]
        if frame_len < 1 or frame_len + 3 > read_len:
            return b'\xf7' + header
        self.ser.timeout = max(0., deadline - time.monotonic())
        body = self.ser.read(frame_len)
        return b'\xf7' + header + body

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

    def cmd_MOTOR_BUS_SEND(self, gcmd):
        addr = gcmd.get_int('ADDR', minval=0, maxval=255)
        payload = bytes.fromhex(gcmd.get('PAYLOAD'))
        frame, resp = self.xfer(addr, payload)
        gcmd.respond_info("sent %s\nresp %s"
                          % (frame.hex(' '),
                             resp.hex(' ') if resp else '(none)'))

def load_config(config):
    return K2MotorBus(config)
