# zip.py — decoder for the prtouch_v3 ticks/datas wire format
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
"""Decode the resault_prtouch_pres / resault_prtouch_step binary payload.

The K2 MCU sends each sample stream as two byte blobs:

  ticks=%.*s   — encoded per-sample MCU tick stamps (32-bit int, delta-encoded)
  datas=%.*s   — encoded per-sample ADC value (32-bit int, delta-encoded)

The encoding is produced by the MCU's `prtouch_write_zip()` routine. We
mirror its decoder here in pure Python.

Format (reverse-engineered 2026-05-23 by exhaustive black-box probe of the
.so's `unzip_data(self, data, signed=True)` function, then completed on
2026-07-28 from the disassembly of the GPL-covered MCU object:

  Byte 0                         : sample count N (uint8)
  Bytes 1..ceil(N/4)             : packed width selectors, newest group first
  Remaining bytes                : signed little-endian deltas, oldest first

Each selector contains four two-bit width codes. Sample `i` uses bits
`(i % 4) * 2` from selector group `floor(i / 4)`, and its encoded width is
the code plus one (1..4 bytes). Selector groups appear in reverse order in
the blob because the MCU grows its selector header backward while growing
the delta payload forward.

Samples are reconstructed by accumulating signed deltas. This deliberately
matches Creality's host decoder at the signed-overflow edge: `signed=True`
does not clamp the Python accumulator after each addition, while
`signed=False` masks it to uint32. This exactly inverts
`prtouch_write_zip()` for real sensor streams and removes the host-side
binary-wrapper dependency.
"""


def unzip_data(blob, signed=True):
    """Decode a ticks= or datas= binary blob into (count, [values]).

    Args:
        blob   : bytes — the on-wire payload
        signed : bool   — interpret values as signed int (default True)

    Returns:
        (count: int, values: list[int])
    """
    if not blob:
        return 0, []

    count = blob[0]
    if count == 0:
        return 0, []

    selector_count = (count + 3) // 4
    payload_pos = 1 + selector_count
    if len(blob) < payload_pos:
        raise ValueError(
            "truncated PRTouch zip header: count=%d bytes=%d"
            % (count, len(blob))
        )

    values = []
    accumulator = 0
    for sample_index in range(count):
        group = sample_index // 4
        selector_index = selector_count - group
        selector = blob[selector_index]
        shift = (sample_index % 4) * 2
        width = ((selector >> shift) & 0x03) + 1

        end = payload_pos + width
        if end > len(blob):
            raise ValueError(
                "truncated PRTouch zip payload at sample %d: "
                "need %d bytes, have %d"
                % (sample_index, width, len(blob) - payload_pos)
            )
        delta = int.from_bytes(
            blob[payload_pos:end], "little", signed=True
        )
        payload_pos = end
        accumulator += delta
        if not signed:
            accumulator &= 0xffffffff
        values.append(accumulator)

    return count, values
