# Creality K2 fan tachometer compatibility for upstream Klipper.
#
# The K2 MCU firmware exposes config_fancheck/query_fancheck commands.  The
# Creality master-server polls the custom get_cx_fan_status webhook and pauses
# a print when the hotend ("throat") fan is expected to run but reports zero.
# This module preserves that wire protocol while using upstream Klipper's
# WebRequest callback API.

_FAN_STATUS_RESPONSE = (
    "fan_status oid=%c fan0_speed=%u fan1_speed=%u fan2_speed=%u "
    "fan3_speed=%u fan4_speed=%u")


class FanFeedback:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.print_delay_time = config.getfloat(
            'print_delay_time', 5., above=0.)
        self.current_delay_time = config.getfloat(
            'current_delay_time', 2., above=0.)
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.gcode = self.printer.lookup_object('gcode')
        self.params = []
        self.ptc_fan_speed = {}
        self.cx_fan_status = {
            "fan0_speed": 0,
            "fan1_speed": 0,
            "fan2_speed": 0,
            "fan3_speed": 0,
            "fan4_speed": 0,
        }

        ppins = self.printer.lookup_object('pins')
        main_pins = config.getlist("fan0_pin")
        if not main_pins or len(main_pins) > 5:
            raise config.error("fan0_pin requires between one and five pins")
        main_params = [
            ppins.lookup_pin(pin, can_invert=False, can_pullup=True)
            for pin in main_pins
        ]
        main_mcu = main_params[0]['chip']
        if any(pin['chip'] is not main_mcu for pin in main_params):
            raise config.error("all fan0_pin entries must use the same MCU")
        self._configure_group(0, main_mcu, main_params)

        nozzle_params = [
            ppins.lookup_pin(config.get("fan1_pin"),
                             can_invert=False, can_pullup=True),
            ppins.lookup_pin(config.get("fan2_pin"),
                             can_invert=False, can_pullup=True),
        ]
        nozzle_mcu = nozzle_params[0]['chip']
        if nozzle_params[1]['chip'] is not nozzle_mcu:
            raise config.error("fan1_pin and fan2_pin must use the same MCU")
        self._configure_group(1, nozzle_mcu, nozzle_params)

        self.gcode.register_command(
            "QUERY_FAN_CHECK", self.cmd_QUERY_FAN_CHECK,
            desc="Report Creality-compatible fan tachometer status")
        self.gcode.register_command(
            "QUERY_PTC_FAN_CHECK", self.cmd_QUERY_PTC_FAN_CHECK,
            desc="Report individual PTC fan tachometer status")
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint(
            "get_cx_fan_status", self._handle_get_cx_fan_status)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _configure_group(self, group, mcu, pin_params):
        oid = mcu.create_oid()
        padded = list(pin_params)
        while len(padded) < 5:
            padded.append(pin_params[-1])
        fields = ["config_fancheck oid=%d fan_num=%d"
                  % (oid, len(pin_params))]
        for index, pin in enumerate(padded):
            fields.append("fan%d_pin=%s pull_up%d=%s"
                          % (index, pin["pin"], index, pin["pullup"]))
        mcu.add_config_cmd(" ".join(fields))
        handler = (self._handle_main_result
                   if group == 0 else self._handle_nozzle_result)
        mcu.register_serial_response(handler, _FAN_STATUS_RESPONSE, oid)
        self.params.append((group, mcu, oid, len(pin_params)))

    def _handle_ready(self):
        reactor = self.printer.get_reactor()
        reactor.register_timer(
            self._query_event, reactor.monotonic() + 1.)

    def _query_event(self, eventtime):
        for _group, mcu, oid, fan_count in self.params:
            query_cmd = mcu.lookup_command(
                "query_fancheck oid=%c which_fan=%c", cq=None)
            query_cmd.send([oid, (1 << fan_count) - 1])
        state = self.print_stats.get_status(eventtime).get("state")
        delay = (self.print_delay_time if state == "printing"
                 else self.current_delay_time)
        return eventtime + delay

    def _handle_get_cx_fan_status(self, web_request):
        web_request.send(dict(self.cx_fan_status))

    def _handle_main_result(self, params):
        fan0_speed = params.get("fan0_speed", 0)
        main_count = self.params[0][3]
        if main_count > 1:
            abnormal = False
            for index in range(main_count):
                key = "fan%d_speed" % index
                speed = params.get(key, 0)
                self.ptc_fan_speed[key] = speed
                abnormal |= speed == 0
            if abnormal:
                fan0_speed = 0
        self.cx_fan_status["fan0_speed"] = fan0_speed

    def _handle_nozzle_result(self, params):
        self.cx_fan_status["fan1_speed"] = params.get("fan0_speed", 0)
        self.cx_fan_status["fan2_speed"] = params.get("fan1_speed", 0)

    def cmd_QUERY_FAN_CHECK(self, gcmd):
        gcmd.respond_info("%s" % self.cx_fan_status)

    def cmd_QUERY_PTC_FAN_CHECK(self, gcmd):
        gcmd.respond_info("%s" % self.ptc_fan_speed)

    def get_status(self, eventtime):
        return dict(self.cx_fan_status)


def load_config(config):
    return FanFeedback(config)
