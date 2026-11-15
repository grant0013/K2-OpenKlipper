"""Two serial byte streams over one USB gadget port.

The Creality Hi has four internal UARTs to relay (main, nozzle and bed MCUs
plus the X/Y motor bus) but its USB device controller only binds three gadget
serial functions, so the bed MCU and the motor bus share the third port.

Frame: 0xA5, channel, length (1-255), header check, payload, checksum
header check = ~(channel + length) & 0xFF
checksum = (channel + length + sum(payload)) & 0xFF

USB bulk transfers are already CRC-checked, so the checks only guard against
starting mid-frame (stale bytes after a reconnect). The header check rejects
a false start at once instead of waiting for up to 255 bytes of a length that
was never sent; a bad frame costs one byte and the decoder looks for the next
0xA5."""

SYNC = 0xA5
MAX_PAYLOAD = 255
CHANNELS = (0, 1)


def encode(channel, data):
    """Frame bytes from one channel; long data is split into several frames."""
    out = bytearray()
    for i in range(0, len(data), MAX_PAYLOAD):
        chunk = data[i:i + MAX_PAYLOAD]
        out.append(SYNC)
        out.append(channel)
        out.append(len(chunk))
        out.append(~(channel + len(chunk)) & 0xFF)
        out += chunk
        out.append((channel + len(chunk) + sum(chunk)) & 0xFF)
    return bytes(out)


class Decoder:
    def __init__(self):
        self.buf = bytearray()
        self.bad = 0    # bytes skipped while hunting for a frame start

    def reset(self):
        self.buf = bytearray()

    def feed(self, data):
        """Add received bytes; return a list of (channel, payload)."""
        self.buf += data
        frames = []
        buf = self.buf
        pos = 0
        while True:
            start = buf.find(SYNC, pos)
            if start < 0:
                self.bad += len(buf) - pos
                pos = len(buf)
                break
            self.bad += start - pos
            pos = start
            if len(buf) - pos < 4:
                break
            channel, length = buf[pos + 1], buf[pos + 2]
            if (channel not in CHANNELS or length == 0
                    or buf[pos + 3] != ~(channel + length) & 0xFF):
                self.bad += 1
                pos += 1
                continue
            end = pos + 4 + length
            if len(buf) <= end:
                break
            payload = bytes(buf[pos + 4:end])
            if (channel + length + sum(payload)) & 0xFF != buf[end]:
                self.bad += 1
                pos += 1
                continue
            frames.append((channel, payload))
            pos = end + 1
        del buf[:pos]
        return frames
