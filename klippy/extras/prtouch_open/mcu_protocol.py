# mcu_protocol.py — wire-level wrappers for the K2 prtouch_v3 MCU command set
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# References:
#   - docs/PROTOCOL.md       — command/response surface
#   - docs/MCU_SYMBOLS.md    — extracted from prtouch_v3.o (DECL_COMMAND_FLAGS strings)
#   - docs/source-reference/prtouch_v3_compile.c (GPL-3 by Creality, the upstream MCU stub)
#
# Design intent: this module is a *thin* wrapper around mcu.MCU.lookup_command()
# and mcu.CommandQueryWrapper(). All non-trivial logic (sampling, contact detection,
# probe-endstop facade) lives in pres.py / step.py / apax.py / wrapper.py.
"""Wire-level MCU command/response wrappers for prtouch_v3.

The K2 MCU firmware exposes 12 commands (host->MCU) and 5 response message
types (MCU->host) for strain-gauge probing, stepper position correlation, and
auto pressure-advance measurement. This module provides Python helpers to
construct CommandQueryWrappers for each, matching the verbatim DECL_COMMAND_FLAGS
strings extracted from prtouch_v3.o.

Wire-protocol typo note: Creality's MCU code uses 'resault' (sic) instead of
'result' in five response message types. We preserve the typo because the
protocol is what the MCU sends; correcting it host-side would break the message
decoder. This is documented in docs/PROTOCOL.md.

Usage:
    from . import mcu_protocol as proto

    # Inside an extras module __init__ that has access to a Klipper MCU object:
    pres_cmds = proto.PresCommands(mcu)
    pres_cmds.config(oid=1, idx=0, swp_pin=PB13, clk_pin=PB14, sdo_pin=PB15)
    pres_cmds.start(oid=1, cfg_regs=60, acq_tick=USEC_PER_SAMPLE,
                    ned_tftr=0, ned_hftr=0, ned_lftr=0,
                    min_hold=3000, max_hold=50000, add_hold=0, lmt_dead=0)
"""

# ---------------------------------------------------------------------------
# Verbatim DECL_COMMAND_FLAGS strings — extracted from prtouch_v3.o
# These MUST match exactly what the MCU firmware registered, byte-for-byte.
# ---------------------------------------------------------------------------

CMD_CONFIG_PRES = (
    "config_prtouch_pres oid=%c idx=%c swp_pin=%u clk_pin=%u sdo_pin=%u"
)
CMD_START_PRES = (
    "start_prtouch_pres oid=%c cfg_regs=%c acq_tick=%u "
    "ned_tftr=%c ned_hftr=%c ned_lftr=%u "
    "min_hold=%i max_hold=%i add_hold=%i lmt_dead=%u"
)
CMD_STOP_PRES = "stop_prtouch_pres oid=%c sta_swap=%c"
CMD_READ_PRES = "read_prtouch_pres oid=%c is_src=%c ch=%c idx=%c len=%c"

CMD_CONFIG_STEP = (
    "config_prtouch_step oid=%c oid_xstp=%u oid_ystp=%u oid_zstp=%u swp_pin=%u"
)
CMD_START_STEP = "start_prtouch_step oid=%c aqc_tick=%u"
CMD_STOP_STEP = "stop_prtouch_step oid=%c"
CMD_READ_STEP = "read_prtouch_step oid=%c idx=%c len=%c"
CMD_CONT_STEP = "cont_prtouch_step oid=%c"

CMD_CONFIG_APAX = "config_prtouch_apax oid=%c oid_estp=%c"
CMD_START_APAX = "start_prtouch_apax oid=%c cfg_regs=%c acq_tick=%u"
CMD_STOP_APAX = "stop_prtouch_apax oid=%c"

# ---------------------------------------------------------------------------
# Response (encoder) message names — keys for register_response()
# ---------------------------------------------------------------------------

# Klipper's msgproto strict-format check (msgproto.py:316
# "Command format mismatch") compares the FULL format we pass to
# lookup_query_command's `respformat` against what the MCU registered
# via DECL_ENCODER. The bare name is insufficient — supplying just
# "ack_prtouch" raises a format-mismatch against the registered
# "ack_prtouch oid=%c err=%c expar0=%u expar1=%u". So we keep BOTH:
# the full format (for lookup_query_command) and the bare name (for
# register_response which keys by name only).
RESP_ACK = "ack_prtouch oid=%c err=%c expar0=%u expar1=%u"
RESP_PRES = (
    "resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c "
    "ch=%c idx=%c len=%c ticks=%.*s datas=%.*s"
)
RESP_STEP = (
    "resault_prtouch_step oid=%c buf_len=%c idx=%c len=%c "
    "ticks=%.*s datas=%.*s"
)
RESP_STEP_CNT = "resault_prtouch_step_cnt oid=%c cnt_x=%i cnt_y=%i cnt_z=%i"
RESP_APAX = (
    "resault_prtouch_apax oid=%c ch=%c len=%c "
    "ticks=%.*s datas=%.*s espds=%.*s"
)
# Bare names (for register_response lookup, which keys by name only)
RESP_ACK_NAME = "ack_prtouch"
RESP_PRES_NAME = "resault_prtouch_pres"           # 'resault' typo preserved
RESP_STEP_NAME = "resault_prtouch_step"
RESP_STEP_CNT_NAME = "resault_prtouch_step_cnt"
RESP_APAX_NAME = "resault_prtouch_apax"

RESPONSE_FORMATS = {
    RESP_ACK_NAME: RESP_ACK,
    RESP_PRES_NAME: RESP_PRES,
    RESP_STEP_NAME: RESP_STEP,
    RESP_STEP_CNT_NAME: RESP_STEP_CNT,
    RESP_APAX_NAME: RESP_APAX,
}


def register_response(mcu, callback, message, oid=None):
    """Register an MCU response on Creality-fork or upstream Klipper."""
    register = getattr(mcu, "register_serial_response", None)
    if register is not None:
        register(callback, RESPONSE_FORMATS.get(message, message), oid)
    else:
        mcu.register_response(callback, message, oid)


# ---------------------------------------------------------------------------
# Command wrapper classes
# ---------------------------------------------------------------------------

class _CommandSet:
    """Base class — owns the MCU reference and a registered-response map."""

    def __init__(self, mcu_obj):
        self.mcu = mcu_obj
        self._responses = {}
        self._wrappers = {}

    def _wrap(self, key, command_format, response_key=None, response_oid_idx=None):
        """Create and cache a CommandQueryWrapper (or plain CommandWrapper if no response).

        - key: short identifier used to look up the wrapper later (e.g. 'start')
        - command_format: the verbatim CMD_* string
        - response_key: if set, this command expects a response — pass the
          response-name string registered by the MCU (e.g. RESP_PRES)
        - response_oid_idx: which oid field to match on in the response (default 0)
        """
        if response_key is None:
            wrapper = self.mcu.lookup_command(command_format)
        else:
            wrapper = self.mcu.lookup_query_command(
                command_format, response_key, oid=response_oid_idx
            )
        self._wrappers[key] = wrapper
        return wrapper


class PresCommands(_CommandSet):
    """Pressure / strain-gauge sampling commands.

    Configure + lifecycle for the HX711-backed ADC sampling channel(s).
    Issues `config_prtouch_pres`, `start_prtouch_pres`, `stop_prtouch_pres`,
    `read_prtouch_pres`. Receives `ack_prtouch` (config) and `resault_prtouch_pres`
    (sample dumps).

    See docs/PROTOCOL.md for parameter semantics.
    See docs/ATTRS.md "PrtouchPres" for the Klippy-side state structure
    this drives.
    """

    def __init__(self, mcu_obj, oid=None):
        super().__init__(mcu_obj)
        self.cfg = self._wrap("config", CMD_CONFIG_PRES, RESP_ACK, oid)
        self.start = self._wrap("start", CMD_START_PRES, RESP_ACK, oid)
        self.stop = self._wrap("stop", CMD_STOP_PRES, RESP_ACK, oid)
        self.read = self._wrap("read", CMD_READ_PRES, RESP_PRES, oid)
        # Compatibility marker consumed by the diagnostic and host-probe
        # guards. Construction is deferred until the MCU dictionary is ready,
        # so reaching this point means every runtime wrapper resolved.
        self._configured = True

    def config(self, oid, idx, swp_pin, clk_pin, sdo_pin):
        """Allocate a pressure-sensing channel.

        - idx     : channel index (0..3)
        - swp_pin : sample/wait/pause control pin
        - clk_pin : HX711 clock pin
        - sdo_pin : HX711 data-out pin
        """
        return self.cfg.send([oid, idx, swp_pin, clk_pin, sdo_pin])

    def start_sampling(
        self, oid, cfg_regs, acq_tick,
        ned_tftr, ned_hftr, ned_lftr,
        min_hold, max_hold, add_hold, lmt_dead,
    ):
        """Begin sampling.

        - cfg_regs : ADC register configuration byte (Creality default: 60)
        - acq_tick : sample-period in MCU ticks (typically ~0.78125 ms worth)
        - ned_tftr / ned_hftr / ned_lftr : trigger-filter coefficients
        - min_hold / max_hold / add_hold : contact-detection thresholds
        - lmt_dead : dead-band limit
        """
        return self.start.send([
            oid, cfg_regs, acq_tick,
            ned_tftr, ned_hftr, ned_lftr,
            min_hold, max_hold, add_hold, lmt_dead,
        ])

    def stop_sampling(self, oid, sta_swap=0):
        """Stop sampling. sta_swap toggles the internal state-buffer swap."""
        return self.stop.send([oid, sta_swap])

    def read_buffer(self, oid, is_src, ch, idx, length):
        """One-shot snapshot.

        - is_src  : 0 for processed samples, 1 for raw ADC
        - ch      : channel index
        - idx     : starting index into the streaming buffer
        - length  : number of samples to return

        Returns the parsed `resault_prtouch_pres` response.
        """
        return self.read.send([oid, is_src, ch, idx, length])


class StepCommands(_CommandSet):
    """Stepper-position-counter commands.

    Used to correlate ADC samples with stepper positions during a probing
    motion. The MCU snapshots step counts at each ADC sample tick.
    """

    def __init__(self, mcu_obj, oid=None):
        super().__init__(mcu_obj)
        self.cfg = self._wrap("config", CMD_CONFIG_STEP, RESP_ACK, oid)
        self.start = self._wrap("start", CMD_START_STEP, RESP_ACK, oid)
        self.stop = self._wrap("stop", CMD_STOP_STEP, RESP_ACK, oid)
        self.read = self._wrap("read", CMD_READ_STEP, RESP_STEP, oid)
        self.cont = self._wrap("cont", CMD_CONT_STEP, RESP_ACK, oid)

    def config(self, oid, oid_xstp, oid_ystp, oid_zstp, swp_pin):
        """Bind step counter to the X/Y/Z stepper OIDs."""
        return self.cfg.send([oid, oid_xstp, oid_ystp, oid_zstp, swp_pin])

    def start_capture(self, oid, acq_tick):
        """Begin step-position capture at acq_tick interval."""
        return self.start.send([oid, acq_tick])

    def stop_capture(self, oid):
        return self.stop.send([oid])

    def read_buffer(self, oid, idx, length):
        """Return captured step-tick samples for the requested window."""
        return self.read.send([oid, idx, length])

    def continue_capture(self, oid):
        """Resume after a pause / overflow."""
        return self.cont.send([oid])


class ApaxCommands(_CommandSet):
    """Auto pressure-advance (APAX) commands.

    Drives Creality's auto-PA measurement routine, the one our K2-AutoPA
    replaces with pulse-decay regression. Kept here for completeness — most
    users will never invoke it, since AUTO_APAX is functionally gated by
    `ned_hold = 50000` (see docs/ATTRS.md `PrtouchApax`).
    """

    def __init__(self, mcu_obj):
        super().__init__(mcu_obj)
        self.cfg = self._wrap("config", CMD_CONFIG_APAX, RESP_ACK)
        self.start = self._wrap("start", CMD_START_APAX, RESP_ACK)
        self.stop = self._wrap("stop", CMD_STOP_APAX, RESP_ACK)
        # Note: APAX responses come back as resault_prtouch_apax, registered
        # separately via mcu.register_response() in apax.py.

    def config(self, oid, oid_estp):
        """Bind APAX to the extruder stepper OID."""
        return self.cfg.send([oid, oid_estp])

    def start_measurement(self, oid, cfg_regs, acq_tick):
        return self.start.send([oid, cfg_regs, acq_tick])

    def stop_measurement(self, oid):
        return self.stop.send([oid])


# ---------------------------------------------------------------------------
# Convenience: register all three command sets at once
# ---------------------------------------------------------------------------

class PrtouchMcuProtocol:
    """Bundle of all three command sets, keyed by subsystem."""

    def __init__(self, mcu_obj):
        self.mcu = mcu_obj
        self.pres = PresCommands(mcu_obj)
        self.step = StepCommands(mcu_obj)
        self.apax = ApaxCommands(mcu_obj)
