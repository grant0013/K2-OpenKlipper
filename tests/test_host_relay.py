"""The K2 host-link relay must survive the USB side going away and coming
back, with the UART side left open. Socket pairs stand in for the ports: a
closed peer reads empty forever, as a hung-up gadget tty does."""
import importlib.util
import io
import os
import socket
import time
import unittest

PATH = os.path.join(os.path.dirname(__file__), '..', 'host', 'k2', 'relay.py')
spec = importlib.util.spec_from_file_location('k2_relay', PATH)
relay_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay_mod)


class RelayReconnect(unittest.TestCase):
    def setUp(self):
        self.socks = []
        self.next_port = []
        self.opened = 0
        self.host, gadget = self.pair()
        self.mcu, uart = self.pair()
        self.out = io.StringIO()
        relay_mod.REOPEN_WAIT = 0.01
        self.relay = relay_mod.Relay(
            [('/dev/ttyGS0', gadget, '/dev/ttyS2', uart)],
            opener=self.opener, out=self.out)
        self.uart = uart

    def tearDown(self):
        for s in self.socks:
            s.close()

    def pair(self):
        a, b = socket.socketpair()
        self.socks += [a, b]
        return a, b.detach()

    def opener(self, path):
        if not self.next_port:
            raise OSError(5, 'not there yet')
        self.opened += 1
        return self.next_port.pop(0)

    def pump(self, times=1):
        for _ in range(times):
            self.relay.pump(timeout=0.01)

    def read(self, sock):
        sock.settimeout(1)
        return sock.recv(100)

    def test_passes_data_both_ways(self):
        self.host.send(b'to mcu')
        self.pump()
        self.assertEqual(self.read(self.mcu), b'to mcu')
        self.mcu.send(b'to host')
        self.pump()
        self.assertEqual(self.read(self.host), b'to host')

    def test_reopens_after_usb_goes_and_data_flows_again(self):
        self.host.send(b'x')
        self.pump()
        self.read(self.mcu)
        self.host.close()
        self.pump(relay_mod.HANGUP_READS + 1)
        self.assertEqual(len(self.relay.down), 1)
        self.assertIn('USB gone', self.out.getvalue())
        # the boards keep talking while the host is away: dropped, no crash
        self.mcu.send(b'nobody listening')
        self.pump()
        self.assertEqual(self.relay.lost['/dev/ttyS2'], 16)
        # the host comes back
        self.host, gadget = self.pair()
        self.next_port.append(gadget)
        time.sleep(0.02)
        self.pump(2)
        self.assertEqual(self.opened, 1)
        self.assertFalse(self.relay.down)
        self.host.send(b'hello again')
        self.pump()
        self.assertEqual(self.read(self.mcu), b'hello again')
        self.mcu.send(b'reply')
        self.pump()
        self.assertEqual(self.read(self.host), b'reply')

    def test_one_empty_read_is_not_a_hangup(self):
        fd = next(iter(self.relay.gadget))
        self.relay.empty[fd] = relay_mod.HANGUP_READS - 2
        self.host.send(b'x')
        self.pump()
        self.assertEqual(self.relay.empty[fd], 0)
        self.assertFalse(self.relay.down)

    def test_keeps_retrying_while_the_port_is_absent(self):
        self.host.close()
        self.pump(relay_mod.HANGUP_READS + 1)
        time.sleep(0.02)
        self.pump(3)
        self.assertEqual(len(self.relay.down), 1)
        self.assertEqual(self.opened, 0)


if __name__ == '__main__':
    unittest.main()
