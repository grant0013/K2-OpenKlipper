#!/usr/bin/env python3
"""Creality Hi host-link, host side: split the Hi's third USB serial port
(bed MCU + motor bus, framed by mux.py) into two pseudo-terminals that
Klipper opens like ordinary serial ports.

  demux.py <usb port> <channel 0 link> <channel 1 link>
  e.g. demux.py /dev/serial/by-id/usb-..._Hi_hostlink_...-if02-port0 \
       ~/printer_data_hi/comms/hi-bed ~/printer_data_hi/comms/hi-bus

The links are symlinks to the pty slaves, recreated at every start. The
pseudo-terminals stay open while the USB port comes and goes (Hi rebooted,
cable pulled), so Klipper sees a silent line, not a vanished device, and
reconnects on FIRMWARE_RESTART once the Hi is back. One slave fd is kept
open here so the master never reads EIO while Klipper has the port closed;
bytes for a closed port are dropped after a short wait."""
import errno, os, pty, select, sys, termios, time, tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mux  # noqa: E402
from relay import open_raw, write_bounded, REOPEN_WAIT  # noqa: E402

HANGUP_READS = 3


def make_pty(link):
    master, slave = pty.openpty()
    tty.setraw(slave)
    tty.setraw(master)
    os.set_blocking(master, False)
    tmp = link + ".new"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(os.ttyname(slave), tmp)
    os.replace(tmp, link)
    return master, slave


class Demux:
    def __init__(self, usb_path, masters, opener=open_raw, out=sys.stderr):
        self.usb_path, self.opener, self.out = usb_path, opener, out
        self.masters = masters                 # channel -> pty master fd
        self.chan_of = {fd: ch for ch, fd in masters.items()}
        self.usb = None
        self.retry = 0.0
        self.empty = 0
        self.up = False
        self.decoder = mux.Decoder()
        self.count = {"usb": 0, 0: 0, 1: 0}
        self.lost = {"usb": 0, 0: 0, 1: 0}

    def _log(self, msg):
        self.out.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        self.out.flush()

    def _open(self):
        if self.usb is not None or time.monotonic() < self.retry:
            return
        try:
            fd = self.opener(self.usb_path)
            termios.tcflush(fd, termios.TCIOFLUSH)
        except OSError:
            self.retry = time.monotonic() + REOPEN_WAIT
            return
        self.usb, self.empty = fd, 0
        self.decoder.reset()

    def _hangup(self):
        try:
            os.close(self.usb)
        except OSError:
            pass
        self.usb = None
        self.retry = time.monotonic() + REOPEN_WAIT
        if self.up:
            self.up = False
            self._log("USB gone, waiting for the Hi")

    def pump(self, timeout=1.0):
        self._open()
        fds = list(self.chan_of)
        if self.usb is None:
            timeout = min(timeout, REOPEN_WAIT)
        else:
            fds.append(self.usb)
        ready, _, _ = select.select(fds, [], [], timeout)
        for fd in ready:
            if fd != self.usb and fd not in self.chan_of:
                continue
            try:
                data = os.read(fd, 4096)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                if e.errno == errno.EIO and fd == self.usb:
                    self._hangup()
                    continue
                raise
            if fd == self.usb:
                if not data:
                    self.empty += 1
                    if self.empty >= HANGUP_READS:
                        self._hangup()
                    continue
                self.empty = 0
                if not self.up:
                    self.up = True
                    self._log("USB up")
                self.count["usb"] += len(data)
                for ch, payload in self.decoder.feed(data):
                    master = self.masters.get(ch)
                    if master is not None:
                        self.lost[ch] += write_bounded(master, payload)
            else:
                ch = self.chan_of[fd]
                self.count[ch] += len(data)
                if self.usb is None:
                    self.lost[ch] += len(data)
                else:
                    self.lost["usb"] += write_bounded(
                        self.usb, mux.encode(ch, data))

    def report(self):
        parts = ["%s=%d%s" % (k, self.count[k],
                              "(-%d)" % self.lost[k] if self.lost[k] else "")
                 for k in ("usb", 0, 1)]
        if self.decoder.bad:
            parts.append("mux_skipped=%d" % self.decoder.bad)
        return " ".join(parts)


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    usb_path, links = sys.argv[1], sys.argv[2:]
    masters, keep = {}, []
    for ch, link in enumerate(links):
        link = os.path.expanduser(link)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        master, slave = make_pty(link)
        masters[ch] = master
        keep.append(slave)
    d = Demux(usb_path, masters)
    sys.stderr.write("hi demux: %s -> %s\n" % (usb_path, ", ".join(
        "ch%d %s=%s" % (i, links[i], os.ttyname(keep[i]))
        for i in range(len(links)))))
    sys.stderr.flush()
    last = time.monotonic()
    while True:
        d.pump()
        now = time.monotonic()
        if now - last >= 10:
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"),
                                          d.report()))
            sys.stderr.flush()
            last = now


if __name__ == "__main__":
    main()
