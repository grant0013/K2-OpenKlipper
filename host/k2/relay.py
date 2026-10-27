#!/usr/bin/env python3
"""K2 host-link relay: pump bytes between the USB gadget serial ports
(seen by the external Klipper host) and the K2's internal UARTs.

  ttyGS0 <-> ttyS2  main MCU                 (host interface 00)
  ttyGS1 <-> ttyS3  nozzle MCU               (host interface 01)
  ttyGS2 <-> ttyS5  X/Y/E motor + CFS RS485  (host interface 02)

Raw mode on every port: a gadget tty echoes input by default, which would
corrupt the Klipper protocol. Anything queued before the relay starts is
discarded so stale host bytes never reach the boards or the motor bus. A
side that stops reading (host not connected) has its data dropped after a
short wait instead of stalling the other links."""
import errno, os, select, sys, termios, time, tty

PAIRS = [("/dev/ttyGS0", "/dev/ttyS2"),
         ("/dev/ttyGS1", "/dev/ttyS3"),
         ("/dev/ttyGS2", "/dev/ttyS5")]
BAUD = termios.B230400
WRITE_WAIT = 0.2    # seconds a blocked peer may hold up the loop


def open_raw(path, baud=None):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[2] |= termios.CLOCAL | termios.CREAD
    attrs[2] &= ~termios.CRTSCTS
    if baud is not None:
        attrs[4] = attrs[5] = baud
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def drain(fd):
    """Discard everything already queued on fd; return the byte count."""
    termios.tcflush(fd, termios.TCIOFLUSH)
    total = 0
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        try:
            data = os.read(fd, 4096)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EIO):
                time.sleep(0.02)
                continue
            raise
        if not data:
            time.sleep(0.02)
            continue
        total += len(data)
    return total


def write_bounded(fd, data):
    """Write data; give up on the rest after WRITE_WAIT. Returns bytes lost."""
    deadline = time.monotonic() + WRITE_WAIT
    while data:
        try:
            n = os.write(fd, data)
            data = data[n:]
            continue
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EIO):
                raise
        left = deadline - time.monotonic()
        if left <= 0:
            return len(data)
        select.select([], [fd], [], min(left, 0.05))
    return 0


def main():
    peer, name, count, lost = {}, {}, {}, {}
    for gs, uart in PAIRS:
        a, b = open_raw(gs), open_raw(uart, BAUD)
        peer[a], peer[b] = b, a
        name[a], name[b] = gs, uart
        count[a] = count[b] = lost[a] = lost[b] = 0
    stale = {name[fd]: drain(fd) for fd in peer}
    fds = list(peer)
    sys.stderr.write("relay up: %s; discarded stale bytes %s\n" % (
        ", ".join("%s<->%s" % p for p in PAIRS), stale))
    last = time.monotonic()
    while True:
        ready, _, _ = select.select(fds, [], [], 1.0)
        for fd in ready:
            try:
                data = os.read(fd, 4096)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                if e.errno == errno.EIO:
                    # Host side unplugged or rebooting: the gadget tty
                    # stays readable with EIO, so back off instead of
                    # spinning a core.
                    time.sleep(0.1)
                    continue
                raise
            if data:
                count[fd] += len(data)
                lost[fd] += write_bounded(peer[fd], data)
        now = time.monotonic()
        if now - last >= 10:
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), " ".join(
                "%s=%d%s" % (os.path.basename(name[f]), count[f],
                             "(-%d)" % lost[f] if lost[f] else "")
                for f in fds)))
            sys.stderr.flush()
            last = now


if __name__ == "__main__":
    main()
