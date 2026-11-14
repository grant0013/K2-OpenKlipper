# Creality Hi (F018): route the X homing signal onto the probe line.
#
# The Hi's X motor stall signal reaches the nozzle MCU on PB0. The main MCU
# only sees one line from the toolhead, PC7, which normally carries the
# strain-gauge trigger (nozzle PA15 -> main PC7). For X homing the nozzle MCU
# firmware can copy PB0 onto PA15 (its config_ioRemap / operation_ioRemap
# commands), so stepper_x homes on the same !PC7 the probe uses.
#
# Stock does this in its patched homing.py: SET_IOREMAP S=1 just before the
# X homing move, S=0 straight after it, including when homing fails. Here it
# is HI_HOME_X, used by homing_override in place of a bare G28 X. Nothing else
# may run while the remap is on: the probe would read the X stall line.
#
# Copyright (C) 2026 Hark Tech. GPLv3, like Klipper. Behaviour taken from
# Creality's GPL io_remap.py and homing.py for the Hi.
#
# [hi_io_remap]
# mcu: nozzle_mcu       # MCU that does the remap
# src_pin: PB0          # input copied (X stall)
# remap_pin: PA15       # output driven while remapped
# src_pullup: 1         # 1: input pulled up, reads 0 when active
# remap_def: 1          # output level while not remapped
# filter_count: 1       # input must hold this many periods (0 = MCU default 5)
# period_ticks: 0       # poll period in ticks (0 = MCU default, 50 us)

import logging

import mcu


class HiIORemap:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.mcu = mcu.get_printer_mcu(self.printer,
                                       config.get("mcu", "nozzle_mcu"))
        self.src_pin = config.get("src_pin", "PB0")
        self.remap_pin = config.get("remap_pin", "PA15")
        self.src_pullup = config.getint("src_pullup", 1, minval=0, maxval=1)
        self.remap_def = config.getint("remap_def", 1, minval=0, maxval=1)
        self.filter_count = config.getint("filter_count", 1,
                                          minval=0, maxval=255)
        self.period_ticks = config.getint("period_ticks", 0, minval=0)
        self.oid = self.mcu.create_oid()
        self.operation_cmd = None
        self.remapped = False
        self.mcu.register_config_callback(self._build_config)
        self.gcode.register_command(
            "SET_IOREMAP", self.cmd_SET_IOREMAP,
            desc="Hi: S=1 routes the X stall line onto the probe line")
        self.gcode.register_command(
            "HI_HOME_X", self.cmd_HI_HOME_X,
            desc="Hi: home X with the X stall line routed, then restore it")
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)

    def _build_config(self):
        self.mcu.add_config_cmd(
            "config_ioRemap oid=%d src_pin=%s src_pullup=%d remap_pin=%s "
            "remap_def=%d" % (self.oid, self.src_pin, self.src_pullup,
                              self.remap_pin, self.remap_def))
        self.operation_cmd = self.mcu.lookup_query_command(
            "operation_ioRemap oid=%c operation=%c filterNum=%c "
            "periodTicks=%u",
            "query_ioRemap oid=%c sta=%c", oid=self.oid)

    def _handle_ready(self):
        # A firmware restart re-runs config_ioRemap, which starts off. Make
        # the host state say so too.
        self.remapped = False

    def set_remap(self, on):
        params = self.operation_cmd.send(
            [self.oid, 1 if on else 0, self.filter_count, self.period_ticks])
        self.remapped = bool(on)
        logging.info("hi_io_remap: operation=%d reply=%s", int(on), params)
        return params

    def get_status(self, eventtime):
        return {"remapped": self.remapped}

    def cmd_SET_IOREMAP(self, gcmd):
        on = gcmd.get_int("S", 0, minval=0, maxval=1)
        params = self.set_remap(on)
        gcmd.respond_info("IO remap %s (sta=%s)"
                          % ("ON" if on else "off", params.get("sta")))

    def cmd_HI_HOME_X(self, gcmd):
        # Called from inside homing_override, so G28 here is the real homing
        # command, not the override again.
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()
        self.set_remap(True)
        try:
            self.gcode.run_script_from_command("G28 X")
        finally:
            try:
                toolhead.wait_moves()
            finally:
                self.set_remap(False)


def load_config(config):
    return HiIORemap(config)
