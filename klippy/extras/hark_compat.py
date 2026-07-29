# hark_compat.py - Creality-environment compat shims for mainline Klipper on the K2
#
# 1) 'shakehands' webhook: Creality's Moonraker fork polls this custom klippy
#    endpoint; without it the mainline klippy log floods with
#    "No registered callback for path 'shakehands'" warnings.
#    Response shape mirrors Creality klippy webhooks._handle_shakehands_request
#    (/usr/share/klipper/klippy/webhooks.py:362 on fw 1.1.6.3).
# 2) HARK_DUMP_DICTS gcode: writes each connected MCU's decompressed data
#    dictionary (JSON) to /mnt/UDISK/mainline-klipper/dicts/<name>.dict for
#    offline protocol analysis (motor opcode decode, batch-mode config checks).
#
# Enable with:  [hark_compat]

import os

DICT_DIR = "/mnt/UDISK/mainline-klipper/dicts"


class HarkCompat:
    def __init__(self, config):
        self.printer = config.get_printer()
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint('shakehands', self._handle_shakehands)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('HARK_DUMP_DICTS', self.cmd_HARK_DUMP_DICTS,
                               desc="Write MCU data dictionaries to disk")

    def _handle_shakehands(self, web_request):
        state_message, state = self.printer.get_state_message()
        web_request.send({
            'state': state,
            'state_message': state_message,
            'shakehands_id': web_request.id,
            'curtime': self.printer.get_reactor().monotonic(),
        })

    def cmd_HARK_DUMP_DICTS(self, gcmd):
        os.makedirs(DICT_DIR, exist_ok=True)
        written = []
        for name, mcu in self.printer.lookup_objects('mcu'):
            serial = getattr(mcu, '_serial', None)
            if serial is None:
                continue
            raw = serial.get_msgparser().get_raw_data_dictionary()
            if isinstance(raw, str):
                raw = raw.encode()
            fname = os.path.join(DICT_DIR,
                                 name.replace(' ', '_') + '.dict')
            with open(fname, 'wb') as f:
                f.write(raw)
            written.append("%s (%d bytes)" % (fname, len(raw)))
        gcmd.respond_info("Dumped: " + ", ".join(written))


def load_config(config):
    return HarkCompat(config)
