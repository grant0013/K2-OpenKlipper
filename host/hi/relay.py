#!/usr/bin/env python3
"""Creality Hi host-link relay (runs on the Hi): pump bytes between the USB
gadget serial ports (seen by the external Klipper host) and the internal
UARTs.

  ttyGS0 <-> ttyS2  main MCU                       (host interface 00)
  ttyGS1 <-> ttyS3  nozzle MCU                     (host interface 01)
  ttyGS2 <-> ttyS4  bed MCU      mux channel 0     (host interface 02)
         <-> ttyS5  X/Y motor RS485 bus, channel 1

The Hi's USB device controller binds only three gadget serial functions, so
the bed MCU and the motor bus share ttyGS2 using the framing in mux.py; the
host runs demux.py to split them into two pseudo-terminals again.

As on the K2 relay: raw mode everywhere, stale bytes discarded at start, a
peer that stops reading has its data dropped after a short wait, and a gadget
port that goes dead on a USB disconnect is closed and reopened until the
host is back while the UARTs stay open."""
import errno, os, select, sys, termios, time, tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mux  # noqa: E402

DIRECT = [("/dev/ttyGS0", "/dev/ttyS2"),
          ("/dev/ttyGS1", "/dev/ttyS3")]
MUXED = ("/dev/ttyGS2", {0: "/dev/ttyS4", 1: "/dev/ttyS5"})
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
    """links: list of (gadget path, {channel: uart fd}) where a None channel
    means a plain one-to-one link and channels 0/1 mean a muxed gadget."""

    def __init__(self, links, uart_names, opener=open_gadget, out=sys.stderr):
        self.opener, self.out = opener, out
        self.uart_names = uart_names          # uart fd -> path
        self.links = {}                       # gadget path -> {ch: uart fd}
        self.gfd = {}                         # gadget path -> fd or None
        self.gpath = {}                       # gadget fd -> path
        self.uart_route = {}                  # uart fd -> (gadget path, ch)
        self.decoder = {}                     # gadget path -> mux.Decoder
        self.empty, self.retry = {}, {}
        self.live = set()
        self.count, self.lost = {}, {}
        for path, chans in links:
            self.links[path] = chans
            self.gfd[path] = None
            self.retry[path] = 0.0
            self.count[path] = self.lost[path] = 0
            if None not in chans:
                self.decoder[path] = mux.Decoder()
            for ch, ufd in chans.items():
                self.uart_route[ufd] = (path, ch)
                name = uart_names[ufd]
                self.count[name] = self.lost[name] = 0

    def attach(self, path, fd):
        self.gfd[path] = fd
        self.gpath[fd] = path
        self.empty[fd] = 0
        if path in self.decoder:
            self.decoder[path].reset()

    def _log(self, msg):
        self.out.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        self.out.flush()

    def _hangup(self, fd):
        path = self.gpath.pop(fd)
        del self.empty[fd]
        self.gfd[path] = None
        try:
            os.close(fd)
        except OSError:
            pass
        self.retry[path] = time.monotonic() + REOPEN_WAIT
        if path in self.live:
            self.live.discard(path)
            self._log("%s: USB gone, waiting for the host" % path)

    def _reopen(self):
        now = time.monotonic()
        for path, fd in self.gfd.items():
            if fd is not None or now < self.retry[path]:
                continue
            try:
                fd = self.opener(path)
            except OSError:
                self.retry[path] = now + REOPEN_WAIT
                continue
            self.attach(path, fd)

    def _from_gadget(self, fd, data):
        path = self.gpath[fd]
        self.empty[fd] = 0
        if path not in self.live:
            self.live.add(path)
            self._log("%s: USB up" % path)
        self.count[path] += len(data)
        chans = self.links[path]
        if path in self.decoder:
            for ch, payload in self.decoder[path].feed(data):
                ufd = chans.get(ch)
                if ufd is not None:
                    self.lost[path] += write_bounded(ufd, payload)
        else:
            self.lost[path] += write_bounded(chans[None], data)

    def _from_uart(self, ufd, data):
        name = self.uart_names[ufd]
        self.count[name] += len(data)
        path, ch = self.uart_route[ufd]
        gfd = self.gfd[path]
        if gfd is None:
            self.lost[name] += len(data)
            return
        if ch is not None:
            data = mux.encode(ch, data)
        self.lost[name] += write_bounded(gfd, data)

    def pump(self, timeout=1.0):
        self._reopen()
        if any(fd is None for fd in self.gfd.values()):
            timeout = min(timeout, REOPEN_WAIT)
        fds = list(self.uart_route) + list(self.gpath)
        ready, _, _ = select.select(fds, [], [], timeout)
        for fd in ready:
            if fd not in self.uart_route and fd not in self.gpath:
                continue    # closed earlier in this round
            try:
                data = os.read(fd, 4096)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                if e.errno == errno.EIO:
                    if fd in self.gpath:
                        self._hangup(fd)
                    else:
                        time.sleep(0.1)
                    continue
                raise
            if not data:
                # A port with nothing to read does not select as ready
                if fd in self.gpath:
                    self.empty[fd] += 1
                    if self.empty[fd] >= HANGUP_READS:
                        self._hangup(fd)
                continue
            if fd in self.gpath:
                self._from_gadget(fd, data)
            else:
                self._from_uart(fd, data)

    def report(self):
        parts = []
        for name in sorted(self.count):
            lost = self.lost[name]
            parts.append("%s=%d%s" % (os.path.basename(name), self.count[name],
                                      "(-%d)" % lost if lost else ""))
        bad = sum(d.bad for d in self.decoder.values())
        if bad:
            parts.append("mux_skipped=%d" % bad)
        return " ".join(parts)


def main():
    uart_names, links, stale = {}, [], {}
    for gs, uart in DIRECT:
        ufd = open_raw(uart, BAUD)
        uart_names[ufd] = uart
        links.append((gs, {None: ufd}))
    gs, chans = MUXED
    mfds = {}
    for ch, uart in chans.items():
        ufd = open_raw(uart, BAUD)
        uart_names[ufd] = uart
        mfds[ch] = ufd
    links.append((gs, mfds))
    relay = Relay(links, uart_names)
    for gs, _ in links:
        fd = open_gadget(gs)
        stale[gs] = drain(fd)
        relay.attach(gs, fd)
    for ufd, name in uart_names.items():
        stale[name] = drain(ufd)
    sys.stderr.write("hi relay up: %s; %s <-> mux %s; stale bytes %s\n" % (
        ", ".join("%s<->%s" % p for p in DIRECT), MUXED[0], MUXED[1], stale))
    sys.stderr.flush()
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
