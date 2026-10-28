# RUN_SAFE: run a G-code macro and always run a cleanup macro after it.
#
#   RUN_SAFE RUN=<macro> [FINALLY=<macro>] [ON_ERROR=raise|report] [K=V ...]
#
# Klipper macros cannot catch errors: a failing command stops the macro, so
# its cleanup (heater off, CFS feed off) never runs. RUN_SAFE runs RUN, then
# FINALLY whether RUN succeeded or not, passing both the remaining
# parameters. ON_ERROR=raise (default) re-raises RUN's error after FINALLY;
# report only prints it, so the macro that called RUN_SAFE carries on (an
# automatic calibration that fails must not fail the filament load around
# it).
#
# Copyright (C) 2026 Grant Harkness (Hark Tech)
#
# This file may be distributed under the terms of the GNU GPLv3 license.

RESERVED = ('RUN', 'FINALLY', 'ON_ERROR')


class HarkSafeRun:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'RUN_SAFE', self.cmd_RUN_SAFE,
            desc="Run a macro and always run a cleanup macro after it")

    def cmd_RUN_SAFE(self, gcmd):
        run = gcmd.get('RUN')
        cleanup = gcmd.get('FINALLY', None)
        on_error = gcmd.get('ON_ERROR', 'raise').lower()
        if on_error not in ('raise', 'report'):
            raise gcmd.error("ON_ERROR must be raise or report")
        args = ' '.join('%s=%s' % (k, v)
                        for k, v in gcmd.get_command_parameters().items()
                        if k.upper() not in RESERVED)
        error = None
        try:
            self.gcode.run_script_from_command(('%s %s' % (run, args)).strip())
        except self.printer.command_error as e:
            error = e
        if cleanup:
            self.gcode.run_script_from_command(
                ('%s %s' % (cleanup, args)).strip())
        if error is not None:
            if on_error == 'raise':
                raise error
            gcmd.respond_info("%s failed: %s" % (run, error))


def load_config(config):
    return HarkSafeRun(config)
