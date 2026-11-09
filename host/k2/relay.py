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
short wait instead of stalling the other links. A USB disconnect leaves the
open gadget ports dead for good, so they are closed and opened again until the
host is back; the UARTs stay open throughout."""
import errno, os, select, sys, termios, time, tty

PAIRS = [("/dev/ttyGS0", "/dev/ttyS2"),
         ("/dev/ttyGS1", "/dev/ttyS3"),
         ("/dev/ttyGS2", "/dev/ttyS5")]
BAUD = termios.B230400
WRITE_WAIT = 0.2    # seconds a blocked peer may hold up the loop
HANGUP_READS = 3    # empty reads in a row from a ready port mean USB went away
REOPEN_WAIT = 0.5   # seconds between tries to open a gadget port again


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


def open_gadget(path):
    fd = open_raw(path)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


class Relay:
    def __init__(self, links, opener=open_gadget, out=sys.stderr):
        # links: (gadget path, gadget fd, uart path, uart fd)
        self.opener, self.out = opener, out
        self.peer, self.name, self.gadget = {}, {}, {}
        self.empty, self.count, self.lost = {}, {}, {}
        self.down = {}      # gadget path: [uart fd, next try]
        self.live = set()   # gadget paths that have carried data since opening
        self.order = []
        for gs, a, uart, b in links:
            self.order += [gs, uart]
            self._attach(gs, a, b)
            self.peer[b], self.name[b] = a, uart
            self.count[uart] = self.lost[uart] = 0
            self.count[gs] = self.lost[gs] = 0

    def _attach(self, path, fd, uart_fd):
        self.peer[fd], self.name[fd] = uart_fd, path
        self.peer[uart_fd] = fd
        self.gadget[fd] = path
        self.empty[fd] = 0

    def _hangup(self, fd):
        path, uart_fd = self.gadget.pop(fd), self.peer.pop(fd)
        del self.name[fd], self.empty[fd]
        self.peer[uart_fd] = None
        try:
            os.close(fd)
        except OSError:
            pass
        self.down[path] = [uart_fd, time.monotonic() + REOPEN_WAIT]
        if path in self.live:
            self.live.discard(path)
            self.out.write("%s %s: USB gone, waiting for the host\n"
                           % (time.strftime("%H:%M:%S"), path))

    def _reopen(self):
        now = time.monotonic()
        for path, (uart_fd, retry) in list(self.down.items()):
            if now < retry:
                continue
            try:
                fd = self.opener(path)
            except OSError:
                self.down[path][1] = now + REOPEN_WAIT
                continue
            del self.down[path]
            self._attach(path, fd, uart_fd)

    def pump(self, timeout=1.0):
        self._reopen()
        if self.down:
            timeout = min(timeout, REOPEN_WAIT)
        ready, _, _ = select.select(list(self.peer), [], [], timeout)
        for fd in ready:
            try:
                data = os.read(fd, 4096)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                if e.errno == errno.EIO:
                    if fd in self.gadget:
                        self._hangup(fd)
                    else:
                        time.sleep(0.1)
                    continue
                raise
            if not data:
                # A port with nothing to read does not select as ready
                if fd in self.gadget:
                    self.empty[fd] += 1
                    if self.empty[fd] >= HANGUP_READS:
                        self._hangup(fd)
                continue
            source = self.name[fd]
            if fd in self.gadget:
                self.empty[fd] = 0
                if source not in self.live:
                    self.live.add(source)
                    self.out.write("%s %s: USB up\n"
                                   % (time.strftime("%H:%M:%S"), source))
            self.count[source] += len(data)
            target = self.peer[fd]
            if target is None:
                self.lost[source] += len(data)
            else:
                self.lost[source] += write_bounded(target, data)

    def report(self):
        return " ".join(
            "%s=%d%s" % (os.path.basename(n), self.count[n],
                         "(-%d)" % self.lost[n] if self.lost[n] else "")
            for n in self.order)


def main():
    links = []
    for gs, uart in PAIRS:
        links.append((gs, open_raw(gs), uart, open_raw(uart, BAUD)))
    stale = {}
    for gs, a, uart, b in links:
        stale[gs], stale[uart] = drain(a), drain(b)
    relay = Relay(links)
    sys.stderr.write("relay up: %s; discarded stale bytes %s\n" % (
        ", ".join("%s<->%s" % p for p in PAIRS), stale))
    last = time.monotonic()
    while True:
        relay.pump()
        now = time.monotonic()
        if now - last >= 10:
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"),
                                          relay.report()))
            sys.stderr.flush()
            last = now


if __name__ == "__main__":
    main()
