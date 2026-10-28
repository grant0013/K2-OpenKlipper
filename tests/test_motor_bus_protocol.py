import importlib
import pathlib
import sys
import unittest

# k2_motor_bus imports Klipper's mcu module and its sibling k2_motor_proto,
# so load it as klippy's extras package (needs Klipper's Python environment).
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))
MODULE = importlib.import_module("extras.k2_motor_bus")


class MotorBusProtocolTest(unittest.TestCase):
    def setUp(self):
        self.bus = MODULE.K2MotorBus.__new__(MODULE.K2MotorBus)

    def test_known_mode_one_frame(self):
        frame = self.bus._build(0x81, [0x00, 0x11, 0x01])
        self.assertEqual(frame.hex(), "f781040011011d")

    def test_known_mode_two_frame(self):
        frame = self.bus._build(0x81, [0x00, 0x11, 0x02])
        self.assertEqual(frame.hex(), "f7810400110214")

    def test_address_is_excluded_from_crc(self):
        first = self.bus._build(0x81, [0x00, 0x11, 0x01])
        second = self.bus._build(0x82, [0x00, 0x11, 0x01])
        self.assertEqual(first[-1], second[-1])
        self.assertEqual(first[:1] + first[2:], second[:1] + second[2:])

    def test_length_counts_payload_and_crc(self):
        frame = self.bus._build(0x01, [0xff, 0x0a])
        self.assertEqual(frame[2], 3)
        self.assertEqual(len(frame), 6)


UID = bytes.fromhex("99763b4e8411b00645353230")


def cfs_reply(addr, cmd, uid=UID):
    # 00 <cmd> <type 1 = CFS> <mode 0 = app> <12-byte unique id>
    return MODULE.proto.build_frame(addr, [0x00, cmd, 0x01, 0x00] + list(uid))


class FakeCfsBus(MODULE.K2MotorBus):
    """address_cfs against a scripted CFS: `addressed` says whether it still
    has its address; `discoverable` whether it answers the 00 a1 broadcast."""

    def __init__(self, addressed, discoverable, uniid=None):
        self.cfs_addr = 1
        self.cfs_uniid = uniid
        self.addressed = addressed
        self.discoverable = discoverable
        self.sent = []

    def _pause(self, delay):
        pass

    def xfer(self, addr, payload, **kw):
        payload = tuple(payload)
        self.sent.append((addr, payload[:2]))
        if addr == self.cfs_addr and payload == (0x00, 0xa2):
            return b'', cfs_reply(1, 0xa2) if self.addressed else b''
        if addr == 0xfe and payload[:2] == (0x00, 0xa1):
            return b'', cfs_reply(0xfe, 0xa1) if self.discoverable else b''
        if addr == 0xfe and payload[:2] == (0x00, 0xa0):
            if bytes(payload[3:]) == UID:
                self.addressed = True
            return b'', b''
        return b'', b''


class CfsAddressingTest(unittest.TestCase):
    def test_addressed_cfs_is_found_and_its_id_read(self):
        bus = FakeCfsBus(addressed=True, discoverable=False)
        self.assertTrue(bus.address_cfs())
        self.assertTrue(bus.cfs_detected)
        self.assertEqual(bus.cfs_found_uniid, UID)
        self.assertEqual(bus.sent, [(1, (0x00, 0xa2))])

    def test_lost_address_is_given_back_from_discover(self):
        bus = FakeCfsBus(addressed=False, discoverable=True)
        self.assertTrue(bus.address_cfs())
        self.assertEqual(bus.cfs_found_uniid, UID)
        self.assertIn((0xfe, (0x00, 0xa0)), bus.sent)

    def test_configured_id_needs_no_discover_answer(self):
        bus = FakeCfsBus(addressed=False, discoverable=False, uniid=UID)
        self.assertTrue(bus.address_cfs())

    def test_wrong_configured_id_falls_back_to_discover(self):
        bus = FakeCfsBus(addressed=False, discoverable=True,
                         uniid=bytes(12 * [0x11]))
        self.assertTrue(bus.address_cfs())
        self.assertEqual(bus.cfs_found_uniid, UID)

    def test_no_cfs(self):
        bus = FakeCfsBus(addressed=False, discoverable=False)
        self.assertFalse(bus.address_cfs())
        self.assertFalse(bus.cfs_detected)

    def test_reply_id_needs_a_cfs_device_type(self):
        bus = FakeCfsBus(addressed=False, discoverable=False)
        frame = MODULE.proto.build_frame(
            0xfe, [0x00, 0xa1, 0x02, 0x00] + list(UID))
        self.assertIsNone(bus._cfs_reply_uniid(frame, 0xa1))
        self.assertEqual(bus._cfs_reply_uniid(cfs_reply(0xfe, 0xa1), 0xa1),
                         UID)


if __name__ == "__main__":
    unittest.main()
