"""Creality Hi host-link: mux framing, and relay (Hi side) <-> demux (host
side) end to end over pseudo-terminals standing in for the UARTs, the USB
gadget link and Klipper's ports."""
import io, os, pty, sys, time, tty, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host", "hi"))
import mux  # noqa: E402
import relay as hi_relay  # noqa: E402
import demux as hi_demux  # noqa: E402


class MuxTest(unittest.TestCase):
    def test_roundtrip_bytewise(self):
        frames = mux.encode(0, b"\x7e\x01\x02") + mux.encode(1, b"abc")
        d = mux.Decoder()
        got = []
        for b in frames:
            got += d.feed(bytes([b]))
        self.assertEqual(got, [(0, b"\x7e\x01\x02"), (1, b"abc")])
        self.assertEqual(d.bad, 0)

    def test_long_payload_split(self):
        data = bytes(range(256)) * 3
        d = mux.Decoder()
        got = d.feed(mux.encode(1, data))
        self.assertTrue(all(ch == 1 for ch, _ in got))
        self.assertEqual(b"".join(p for _, p in got), data)
        self.assertEqual(len(got), 4)

    def test_payload_with_sync_bytes(self):
        data = b"\xa5\xa5\x00\xa5\x01\x05"
        d = mux.Decoder()
        self.assertEqual(d.feed(mux.encode(0, data)), [(0, data)])

    def test_garbage_before_frame_is_skipped(self):
        d = mux.Decoder()
        got = d.feed(b"\x00\x13\xa5\x07\xa5\x01" + mux.encode(1, b"ok"))
        self.assertEqual(got, [(1, b"ok")])
        self.assertGreater(d.bad, 0)

    def test_bad_checksum_resyncs(self):
        bad = bytearray(mux.encode(0, b"xyz"))
        bad[-1] ^= 0xFF
        d = mux.Decoder()
        got = d.feed(bytes(bad) + mux.encode(0, b"next"))
        self.assertEqual(got, [(0, b"next")])


def raw_pty():
    m, s = pty.openpty()
    tty.setraw(m)
    tty.setraw(s)
    os.set_blocking(m, False)
    os.set_blocking(s, False)
    return m, s


def read_all(fd, want, pumps, limit=2.0):
    out = b""
    deadline = time.monotonic() + limit
    while len(out) < want and time.monotonic() < deadline:
        for p in pumps:
            p(0.01)
        try:
            out += os.read(fd, 4096)
        except BlockingIOError:
            pass
    return out


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        # Hi UARTs: the relay holds the slave, the "MCU" is the master end.
        self.mcu = {}
        uart_names = {}
        uart = {}
        for name in ("S2", "S3", "S4", "S5"):
            m, s = raw_pty()
            self.mcu[name] = m
            uart[name] = s
            uart_names[s] = "/dev/tty" + name
        # USB links: relay writes the gadget end, the host reads the other.
        self.usb = {}
        for gs in ("GS0", "GS1", "GS2"):
            self.usb[gs] = raw_pty()     # (gadget side, host side)
        links = [("/dev/ttyGS0", {None: uart["S2"]}),
                 ("/dev/ttyGS1", {None: uart["S3"]}),
                 ("/dev/ttyGS2", {0: uart["S4"], 1: uart["S5"]})]
        self.relay = hi_relay.Relay(links, uart_names, out=io.StringIO())
        for gs in ("GS0", "GS1", "GS2"):
            self.relay.attach("/dev/tty" + gs, self.usb[gs][0])
        # Host: demux ptys; Klipper holds the slave end.
        self.klipper = {}
        masters = {}
        for ch in (0, 1):
            m, s = raw_pty()
            masters[ch] = m
            self.klipper[ch] = s
        host_usb = self.usb["GS2"][1]
        self.demux = hi_demux.Demux("usb-if02", masters,
                                    opener=lambda p: host_usb,
                                    out=io.StringIO())
        self.pumps = [self.relay.pump, self.demux.pump]

    def test_direct_main_mcu_both_ways(self):
        os.write(self.usb["GS0"][1], b"\x7ehost->main")
        self.assertEqual(read_all(self.mcu["S2"], 11, self.pumps),
                         b"\x7ehost->main")
        os.write(self.mcu["S3"], b"nozzle reply")
        self.assertEqual(read_all(self.usb["GS1"][1], 12, self.pumps),
                         b"nozzle reply")

    def test_bed_and_bus_share_the_third_port(self):
        os.write(self.klipper[0], b"bed cmd")
        os.write(self.klipper[1], b"\x01\x02bus frame")
        self.assertEqual(read_all(self.mcu["S4"], 7, self.pumps), b"bed cmd")
        self.assertEqual(read_all(self.mcu["S5"], 11, self.pumps),
                         b"\x01\x02bus frame")
        os.write(self.mcu["S4"], b"bed answer" * 60)
        os.write(self.mcu["S5"], b"motor ack")
        self.assertEqual(read_all(self.klipper[0], 600, self.pumps),
                         b"bed answer" * 60)
        self.assertEqual(read_all(self.klipper[1], 9, self.pumps),
                         b"motor ack")
        self.assertEqual(self.demux.decoder.bad, 0)


if __name__ == "__main__":
    unittest.main()
