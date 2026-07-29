# k2_cfs.py - open CFS (Creality Filament System) driver for
# MAINLINE Klipper on the K2. Shares the RS485 bus with k2_motor_bus.
#
# The CFS is a bus device at addr 0x01 on /dev/ttyS5 (0xf7-framed protocol,
# see k2_motor_bus.py for framing/CRC). Opcode map mined from 6 days of stock
# klippy logs (2026-07-02..07) by correlating Creality's named command log
# lines with the hex frames sent immediately after — see
# scratchpad/mine_cfs_protocol.py and the RUNBOOK.
#
# Separately gated commands arm/disarm normal feed, move through validated
# purge/cutter routes, and reproduce the stock cut, unload, load, and purge
# phases. The phases remain separate until each has been qualified on hardware.
#
# Config:
#   [k2_cfs]
#   #addr: 1
#   #allow_actuation: false
#   #purge_speed: 100
#   #cutter_speed: 100
#
# Gcode:
#   CFS_STATUS            - query + decode everything read-only we know
#   CFS_QUERY CMD=<name>  - fire one named read-only query (see QUERIES)
#   CFS_FEED_START SLOT=D CONFIRM=1
#   CFS_FEED_STOP
#   CFS_PURGE_POSITION CONFIRM=1
#   CFS_PURGE_RETURN CONFIRM=1
#   CFS_CUTTER_POSITION CONFIRM=1  - movement only; does not cut/retract
#   CFS_CUTTER_RETURN CONFIRM=1    - movement only; return to safe position
#   CFS_CUTTER_SENSOR               - read the nozzle-MCU PB9 hall input
#   CFS_UNLOAD                      - hard-blocked; cutting must come first
#   CFS_CUT_UNLOAD SLOT=D CONFIRM=1 - cut, release, then CFS retract
#   CFS_LOAD SLOT=D CONFIRM=1       - stock staged CFS/hotend load handshake
#   CFS_PURGE CONFIRM=1              - stock nominal-140/actual-160mm flush
#   CFS_PRINT_START SLOT=D CONFIRM=1 - select/load/purge initial print slot
#   T0/T1/T2/T3           - arm only the already-selected A/B/C/D slot

class K2CFS:
    # name -> (payload bytes, decoder method name)
    # Payload EXCLUDES addr/len/crc (k2_motor_bus builds the frame).
    # Opcode map mined 2026-07-07 from 6 days / ~205k transactions of stock
    # klippy logs (name<->hex correlation). Read-only set ONLY. Payload
    # EXCLUDES addr/len/crc (k2_motor_bus builds the frame). Actuation opcodes
    # (SET_BOX_MODE ff04, TIGHTEN_UP ff0f, SET_PRE_LOADING ff0d, EXTRUDE/
    # RETRUDE) DELIBERATELY EXCLUDED — phase 3, supervised only.
    QUERIES = {
        # CONFIRMED live (valid-CRC response captured 2026-07-07):
        'BOX_STATE':       ([0x00, 0xa2], '_dec_box_state'),   # uniid table
        'MEASURING':       ([0xff, 0x0a], '_dec_measuring'),   # hub feed encoder
        # Stock box_wrapper communication_get_box_mode, confirmed live on
        # CFS firmware 1.5.0.  This is the authoritative activity mode;
        # MEASURING's trailing selection bytes are not a live mode report.
        'BOX_MODE':        ([0xff, 0x0c], '_dec_box_mode'),
        # BUFFER_STATE ff05 CONFIRMED live 2026-07-07: byte tracks buffer arm
        # 0=slack 1=mid 2=tensioned (cycled as Grant moved the arm). This is
        # THE runout/feed signal for phase 2.
        'BUFFER_STATE':    ([0xff, 0x05], '_dec_buffer'),
        # MINED (frame uniquely correlated to named cmd; raw decode until
        # validated live):
        'REMAIN_LEN':      ([0xff, 0x03, 0x0f], '_dec_remain'),
        'FILAMENT_SENSOR': ([0xff, 0x08, 0x00], '_dec_filament'),
        'HARDWARE_STATUS': ([0xff, 0x15], '_dec_raw'),
        'VERSION_SN':      ([0xff, 0x14], '_dec_version_sn'),
        'MEASURING_WHEEL': ([0xff, 0x0e, 0x01], '_dec_raw'),
    }
    NUM_SLOTS = 4
    SLOT_MASKS = {'A': 0x01, 'B': 0x02, 'C': 0x04, 'D': 0x08}
    MODE_PRINT = 0x00
    MODE_IDLE = 0x01
    BOX_MODE_IDLE = 0x00
    BOX_MODE_PRELOADING = 0x01
    BOX_MODE_PRINTING = 0x02
    BOX_MODE_WRAPPERING = 0x03
    BOX_MODE_ERROR = 0x04
    BOX_MODE_TEST = 0x05
    # SET_BOX_MODE's final byte selects routing behaviour (PRINT/IDLE).
    # GET_BOX_MODE reports the separate load/unload operation engine. Stock
    # logs and a live slot-C start both show that engine returning IDLE once
    # steady print feed is armed; PRINTING is a transient operation state.
    TENSION_ENABLE = 0x00
    TENSION_DISABLE = 0x01

    def __init__(self, config):
        self.printer = config.get_printer()
        self.addr = config.getint('addr', 1, minval=1, maxval=127)
        self.allow_actuation = config.getboolean('allow_actuation', False)
        self.purge_safe_y = config.getfloat('purge_safe_y', 261.)
        self.purge_entry_x = config.getfloat('purge_entry_x', 131.)
        self.purge_gate_x = config.getfloat('purge_gate_x', 127.)
        self.purge_x = config.getfloat('purge_x', 115.)
        self.purge_y = config.getfloat('purge_y', 291.5)
        self.purge_return_x = config.getfloat('purge_return_x', 205.)
        self.purge_position_tolerance = config.getfloat(
            'purge_position_tolerance', 2., minval=0., maxval=10.)
        self.purge_speed = config.getfloat(
            'purge_speed', 100., above=0.)
        self.purge_min_z = config.getfloat('purge_min_z', 5.)
        self.purge_extrude_length = config.getfloat(
            'purge_extrude_length', 140., above=0., maxval=240.)
        self.purge_extrude_speed = config.getfloat(
            'purge_extrude_speed', 6., above=0., maxval=9.58)
        self.purge_chunk_length = config.getfloat(
            'purge_chunk_length', 90., above=0., maxval=90.)
        self.purge_slow_fraction = config.getfloat(
            'purge_slow_fraction', .2, above=0., maxval=.5)
        self.purge_fast_multiplier = config.getfloat(
            'purge_fast_multiplier', 1.2, minval=1., maxval=1.2)
        self.purge_followup_chunk_length = config.getfloat(
            'purge_followup_chunk_length', 70., above=0., maxval=70.)
        self.purge_minimum_actual_length = config.getfloat(
            'purge_minimum_actual_length', 160.,
            minval=160., maxval=160.)
        self.clean_plate_x = config.getfloat('clean_plate_x', 155.)
        self.clean_left_x = config.getfloat('clean_left_x', 127.)
        self.clean_right_x = config.getfloat('clean_right_x', 137.)
        self.clean_circle_left_x = config.getfloat(
            'clean_circle_left_x', 146.)
        self.clean_circle_right_x = config.getfloat(
            'clean_circle_right_x', 154.)
        self.clean_y = config.getfloat('clean_y', 291.5)
        self.clean_speed = config.getfloat(
            'clean_speed', 200., above=0., maxval=200.)
        self.clean_circle_speed = config.getfloat(
            'clean_circle_speed', 166.666667, above=0., maxval=166.666667)
        self.clean_accel = config.getfloat(
            'clean_accel', 5000., above=0., maxval=5000.)
        self.cutter_x = config.getfloat('cutter_x', 10.)
        self.cutter_y = config.getfloat('cutter_y', 150.)
        self.cutter_speed = config.getfloat(
            'cutter_speed', 100., above=0.)
        self.cutter_cut_x = config.getfloat(
            'cutter_cut_x', -7.2, minval=-10., maxval=-5.)
        self.cutter_release_x = config.getfloat(
            'cutter_release_x', -1.7,
            minval=self.cutter_cut_x, maxval=0.)
        self.cutter_ram_speed = config.getfloat(
            'cutter_ram_speed', 500., above=0., maxval=500.)
        self.cutter_release_speed = config.getfloat(
            'cutter_release_speed', 150., above=0., maxval=150.)
        self.cutter_ram_accel = config.getfloat(
            'cutter_ram_accel', 3000., above=0., maxval=3000.)
        self.cutter_retract_length = config.getfloat(
            'cutter_retract_length', 2., above=0., maxval=2.)
        self.cutter_retract_speed = config.getfloat(
            'cutter_retract_speed', 6., above=0., maxval=6.)
        self.cutter_tip_length = config.getfloat(
            'cutter_tip_length', 7., above=0., maxval=7.)
        self.cutter_tip_push_speed = config.getfloat(
            'cutter_tip_push_speed', 9., above=0., maxval=9.)
        self.cutter_tip_pull_speed = config.getfloat(
            'cutter_tip_pull_speed', 5., above=0., maxval=5.)
        self.cutter_switch_pin = config.get(
            'cutter_switch_pin', '!nozzle_mcu:PB9')
        self.unload_fast_pulls = config.getint(
            'unload_fast_pulls', 3, minval=1, maxval=3)
        self.unload_pull_length = config.getfloat(
            'unload_pull_length', 10., above=0., maxval=20.)
        self.unload_pull_speed = config.getfloat(
            'unload_pull_speed', 6., above=0., maxval=6.)
        self.unload_final_length = config.getfloat(
            'unload_final_length', 10., above=0., maxval=10.)
        self.unload_final_speed = config.getfloat(
            'unload_final_speed', 1., above=0., maxval=1.)
        self.unload_rewind_pause_ms = config.getint(
            'unload_rewind_pause_ms', 0, minval=0, maxval=1000)
        self.unload_bus_timeout = config.getfloat(
            'unload_bus_timeout', 30., above=0., maxval=150.)
        self.unload_sensor_timeout = config.getfloat(
            'unload_sensor_timeout', 10., above=0., maxval=15.)
        self.sensor_poll_interval = config.getfloat(
            'sensor_poll_interval', .1, minval=.05, maxval=1.)
        self.load_bus_timeout = config.getfloat(
            'load_bus_timeout', 15., above=0., maxval=30.)
        self.load_timeout = config.getfloat(
            'load_timeout', 90., above=0., maxval=150.)
        self.load_poll_interval = config.getfloat(
            'load_poll_interval', 1., minval=0.1, maxval=5.)
        self.load_engage_length = config.getfloat(
            'load_engage_length', 5., above=0., maxval=5.)
        self.load_engage_speed = config.getfloat(
            'load_engage_speed', 1., above=0., maxval=1.)
        self.load_extrude_length = config.getfloat(
            'load_extrude_length', 9., above=0., maxval=9.)
        self.load_extrude_speed = config.getfloat(
            'load_extrude_speed', 3., above=0., maxval=3.)
        self.load_box_finish_length = config.getint(
            'load_box_finish_length', 3, minval=1, maxval=3)
        self.filament_sensor_name = config.get(
            'filament_sensor_name', 'filament_sensor')
        self.manage_runout_sensor = config.getboolean(
            'manage_runout_sensor', True)
        self.print_start_transaction = False
        self.cutter_switch_state = None
        self.bus = None
        self.status = {'online': None, 'feed_enabled': None}
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons(
            [self.cutter_switch_pin], self._handle_cutter_switch)
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'CFS_STATUS', self.cmd_CFS_STATUS,
            desc="Query CFS state (read-only)")
        self.gcode.register_command(
            'CFS_QUERY', self.cmd_CFS_QUERY,
            desc="Fire one named read-only CFS query")
        self.gcode.register_command(
            'CFS_FEED_START', self.cmd_CFS_FEED_START,
            desc="Arm buffer-controlled CFS feed for loaded filament")
        self.gcode.register_command(
            'CFS_FEED_STOP', self.cmd_CFS_FEED_STOP,
            desc="Disarm buffer-controlled CFS feed")
        self.gcode.register_command(
            'CFS_PURGE_POSITION', self.cmd_CFS_PURGE_POSITION,
            desc="Move to the K2 purge bucket using the stock safe route")
        self.gcode.register_command(
            'CFS_PURGE_RETURN', self.cmd_CFS_PURGE_RETURN,
            desc="Leave the K2 purge bucket using the stock safe route")
        self.gcode.register_command(
            'NOZZLE_WIPE', self.cmd_NOZZLE_WIPE,
            desc="Wipe the nozzle on the K2 rear silicone cleaner")
        self.gcode.register_command(
            'CFS_CUTTER_POSITION', self.cmd_CFS_CUTTER_POSITION,
            desc="Move to the K2 cutter staging point without cutting")
        self.gcode.register_command(
            'CFS_CUTTER_RETURN', self.cmd_CFS_CUTTER_RETURN,
            desc="Leave the K2 cutter staging point without cutting")
        self.gcode.register_command(
            'CFS_CUTTER_SENSOR', self.cmd_CFS_CUTTER_SENSOR,
            desc="Report the K2 cutter hall-sensor logical state")
        self.gcode.register_command(
            'CFS_UNLOAD', self.cmd_CFS_UNLOAD,
            desc="Blocked unsafe standalone CFS rewind")
        self.gcode.register_command(
            'CFS_CUT_UNLOAD', self.cmd_CFS_CUT_UNLOAD,
            desc="Cut and release filament before CFS rewind")
        self.gcode.register_command(
            'CFS_LOAD', self.cmd_CFS_LOAD,
            desc="Load a selected CFS slot using the stock K2 handshake")
        self.gcode.register_command(
            'CFS_PURGE', self.cmd_CFS_PURGE,
            desc="Purge loaded CFS filament using the stock K2 baseline")
        self.gcode.register_command(
            'CFS_PRINT_START', self.cmd_CFS_PRINT_START,
            desc="Select, load, and purge the initial CFS print slot")
        for tool, slot in enumerate(('A', 'B', 'C', 'D')):
            def select_tool(gcmd, selected_slot=slot):
                self._select_tool(gcmd, selected_slot)
            self.gcode.register_command(
                'T%d' % tool, select_tool,
                desc="Arm already-selected CFS slot %s" % slot)

    def _handle_connect(self):
        self.bus = self.printer.lookup_object('k2_motor_bus')

    def get_status(self, eventtime):
        out = dict(self.status)
        out['cutter_switch_state'] = self.cutter_switch_state
        return out

    def _handle_cutter_switch(self, eventtime, state):
        # Preserve the pin resolver's logical state exactly until physical
        # open/pressed semantics have been validated on the K2.
        self.cutter_switch_state = bool(state & 0x01)

    # --- decoders: (payload_bytes_after_len, ) -> dict ---
    @staticmethod
    def _payload(resp):
        # resp = f7 addr len <payload...> crc ; validate shape loosely
        if len(resp) >= 5 and resp[0] == 0xf7:
            ln = resp[2]
            return bytes(resp[3:3 + ln - 1])
        return None

    def _dec_box_state(self, p):
        # observed: 00 a2 01 00 <12-byte uniid table>
        return {'echo': p[:2].hex(), 'flags': p[2:4].hex(),
                'uniid': p[4:16].hex(' ')}

    def _dec_measuring(self, p):
        # observed: 00 0a <c1> <c2> 00 00 00 00 ; c1~0x1d-0x21 c2~0x24-0x2b
        # two counters (buffer feed / measuring wheel encoder). Bytes 6/7
        # retain the selected-slot mask and selection setting.  They do not
        # change when stock's global IDLE command (ff 04 00 01) succeeds, so
        # they must not be used as the live CFS mode.  GET_BOX_MODE (ff 0c)
        # is authoritative for that.
        if len(p) >= 4:
            out = {'c1': p[2], 'c2': p[3], 'raw': p.hex(' ')}
            if len(p) >= 8:
                mask, selection_setting = p[6], p[7]
                slots = [name for name, bit in self.SLOT_MASKS.items()
                         if mask & bit]
                out.update({
                    'active_mask': mask,
                    'active_slots': slots,
                    'active_slot': slots[0] if len(slots) == 1 else None,
                    'selection_setting': selection_setting,
                })
            return out
        return {'raw': p.hex(' ')}

    def _dec_box_mode(self, p):
        # observed live: 00 0c 00 after stock IDLE command.  Creality's own
        # mode table is IDLE=0, PRELOADING=1, PRINTING=2, WRAPPERING=3,
        # ERR=4, TEST=5.
        if len(p) >= 3:
            code = p[2]
            names = {
                self.BOX_MODE_IDLE: 'idle',
                self.BOX_MODE_PRELOADING: 'preloading',
                self.BOX_MODE_PRINTING: 'printing',
                self.BOX_MODE_WRAPPERING: 'wrappering',
                self.BOX_MODE_ERROR: 'error',
                self.BOX_MODE_TEST: 'test',
            }
            return {
                'box_mode_code': code,
                'box_mode': names.get(code, 'unknown'),
                'raw': p.hex(' '),
            }
        return {'raw': p.hex(' ')}

    def _dec_version_sn(self, p):
        # observed: 00 14 <ascii "142" + serial> -> fw 1.4.2, sn ...
        try:
            s = bytes(p[2:]).decode('ascii', 'replace')
        except Exception:
            return {'raw': p.hex(' ')}
        ver = '.'.join(s[:3]) if len(s) >= 3 else s
        return {'firmware': ver, 'sn': s[3:], 'raw_ascii': s}

    def _dec_filament(self, p):
        # observed: 00 08 <mask> ; mask bit i = slot i has filament
        if len(p) >= 3:
            mask = p[2]
            slots = [bool(mask >> i & 1) for i in range(self.NUM_SLOTS)]
            return {'mask': '0x%02x' % mask, 'slots_loaded': slots}
        return {'raw': p.hex(' ')}

    def _dec_buffer(self, p):
        # observed: 00 05 <state> ; state 0=slack 1=mid 2=tensioned
        if len(p) >= 3:
            names = {0: 'slack', 1: 'mid', 2: 'tensioned'}
            return {'buffer_state': p[2], 'buffer': names.get(p[2], '?')}
        return {'raw': p.hex(' ')}

    def _dec_remain(self, p):
        # observed: 00 03 <s0> <s1> <s2> <s3> ; per-slot remaining (0-100)
        vals = list(p[2:2 + self.NUM_SLOTS])
        return {'remain': vals}

    def _dec_raw(self, p):
        return {'raw': p.hex(' ')}

    # --- gcode ---
    def _run_query(self, name):
        payload, dec = self.QUERIES[name]
        frame, resp = self.bus.xfer(self.addr, payload)
        p = self._payload(resp)
        if p is None:
            return {'error': 'no/short response',
                    'resp': resp.hex(' ') if resp else '(none)'}
        return getattr(self, dec)(p)

    def cmd_CFS_STATUS(self, gcmd):
        out = []
        online = False
        results = {}
        for name in self.QUERIES:
            r = self._run_query(name)
            results[name] = r
            if 'error' not in r:
                online = True
            out.append("%s: %s" % (name, r))
        # surface the useful decoded fields to get_status (Fluidd/Moonraker)
        st = {'online': online}
        v = results.get('VERSION_SN', {})
        if 'firmware' in v:
            st['firmware'], st['sn'] = v['firmware'], v['sn']
        f = results.get('FILAMENT_SENSOR', {})
        if 'slots_loaded' in f:
            st['slots_loaded'] = f['slots_loaded']
        rl = results.get('REMAIN_LEN', {})
        if 'remain' in rl:
            st['remain'] = rl['remain']
        b = results.get('BUFFER_STATE', {})
        if 'buffer_state' in b:
            st['buffer_state'], st['buffer'] = b['buffer_state'], b['buffer']
        m = results.get('MEASURING', {})
        for key in ('active_mask', 'active_slots', 'active_slot',
                    'selection_setting'):
            if key in m:
                st[key] = m[key]
        bm = results.get('BOX_MODE', {})
        for key in ('box_mode_code', 'box_mode'):
            if key in bm:
                st[key] = bm[key]
        # Feed arming is a host-issued setting, not the instantaneous activity
        # returned by GET_BOX_MODE. Preserve the locally known state instead
        # of inventing it from MEASURING's stale selection bytes.
        st['feed_enabled'] = self.status.get('feed_enabled')
        self.status = st
        gcmd.respond_info("CFS addr %d\n%s" % (self.addr, "\n".join(out)))

    def cmd_CFS_QUERY(self, gcmd):
        name = gcmd.get('CMD').upper()
        if name not in self.QUERIES:
            raise gcmd.error("Unknown query %s. Known: %s"
                             % (name, ", ".join(self.QUERIES)))
        gcmd.respond_info("%s: %s" % (name, self._run_query(name)))

    def _require_actuation(self, gcmd):
        if not self.allow_actuation:
            raise gcmd.error(
                "CFS actuation is disabled; set allow_actuation: true")

    def _run_actuation(self, name, payload, timeout=0.2):
        frame, resp = self.bus.xfer(
            self.addr, payload, timeout=timeout)
        p = self._payload(resp)
        opcode = payload[1]
        if p is None or len(p) < 2 or p[0] != 0 or p[1] != opcode:
            raise RuntimeError(
                "%s failed: sent %s resp %s"
                % (name, frame.hex(' '),
                   resp.hex(' ') if resp else '(none)'))
        return frame, resp

    def _nozzle_has_filament(self):
        sensor = self.printer.lookup_object(
            'filament_switch_sensor %s' % self.filament_sensor_name, None)
        if sensor is None:
            return None
        return bool(sensor.get_status(0.).get('filament_detected'))

    def _set_runout_sensor_enabled(self, enabled):
        if not self.manage_runout_sensor:
            return
        self.gcode.run_script_from_command(
            "SET_FILAMENT_SENSOR SENSOR=%s ENABLE=%d"
            % (self.filament_sensor_name, int(enabled)))

    def _wait_nozzle_filament(self, expected, timeout):
        reactor = self.printer.get_reactor()
        deadline = reactor.monotonic() + timeout
        while True:
            if self._nozzle_has_filament() is expected:
                return True
            now = reactor.monotonic()
            if now >= deadline:
                return False
            reactor.pause(
                min(deadline, now + self.sensor_poll_interval))

    def _require_idle(self, gcmd):
        # The standalone cut/load/purge commands remain blocked once a print
        # starts. CFS_PRINT_START is the sole exception: it owns a bounded,
        # single-threaded transaction invoked by START_PRINT before model
        # extrusion begins.
        if self.print_start_transaction:
            return
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            return
        state = print_stats.get_status(0.).get('state', '')
        if state in ('printing', 'paused'):
            raise gcmd.error(
                "CFS operation refused while print state is %s" % state)

    def _require_print_start(self, gcmd):
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            raise gcmd.error(
                "CFS_PRINT_START requires virtual_sdcard print state")
        state = print_stats.get_status(0.).get('state', '')
        if state != 'printing':
            raise gcmd.error(
                "CFS_PRINT_START is only available during START_PRINT "
                "(state is %s)" % (state or "unknown"))

    def _move_to_cut_start(self):
        feedrate = self.purge_speed * 60.
        self.gcode.run_script_from_command(
            "G90\n"
            "G1 Y%.3f F%.3f\n"
            "G1 X%.3f F%.3f\n"
            "M400"
            % (self.purge_safe_y, feedrate,
               self.purge_return_x, feedrate))

    def _require_hot_extruder(self, gcmd):
        extruder = self.printer.lookup_object('extruder', None)
        if extruder is None:
            raise gcmd.error("Extruder object is unavailable")
        heater = extruder.get_heater()
        if not heater.can_extrude:
            status = heater.get_status(0.)
            raise gcmd.error(
                "CFS unload requires a preheated extruder "
                "(current %.1fC, target %.1fC)"
                % (status.get('temperature', 0.),
                   status.get('target', 0.)))

    def _best_effort_stop(self, mask):
        try:
            self._run_actuation(
                'TIGHTEN_UP DISABLE',
                [0xff, 0x0f, self.TENSION_DISABLE])
            # Stock BOX_SET_CURRENT_BOX_IDLE_MODE uses a zero selection byte.
            # A slot mask here is accepted at the transport layer but does
            # not transition firmware 1.5.0 to IDLE.
            self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
        except Exception:
            pass

    def _start_feed(self, gcmd, slot, require_confirm, require_selected):
        self._require_actuation(gcmd)
        if (require_confirm
                and gcmd.get_int(
                    'CONFIRM', 0, minval=0, maxval=1) != 1):
            raise gcmd.error(
                "CFS_FEED_START requires CONFIRM=1")
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "No filament at nozzle; loaded-path feed only")

        measuring = self._run_query('MEASURING')
        old_mask = measuring.get('active_mask', 0)
        if require_selected and old_mask != mask:
            raise gcmd.error(
                "T%d requests slot %s but CFS selected mask is 0x%02x; "
                "physical tool change is not implemented"
                % (ord(slot) - ord('A'), slot, old_mask))
        if old_mask not in (0, mask):
            raise gcmd.error(
                "CFS selected mask is 0x%02x; refusing slot %s"
                % (old_mask, slot))

        sent = []
        try:
            frame, _ = self._run_actuation(
                'SET_BOX_MODE PRINT',
                [0xff, 0x04, mask, self.MODE_PRINT])
            sent.append(frame.hex(' '))
            frame, _ = self._run_actuation(
                'TIGHTEN_UP ENABLE',
                [0xff, 0x0f, self.TENSION_ENABLE])
            sent.append(frame.hex(' '))
        except Exception as exc:
            # Best-effort fail-safe: disarm tension and return this slot idle.
            self._best_effort_stop(mask)
            raise gcmd.error(str(exc))

        verified = self._run_query('MEASURING')
        box_mode = self._run_query('BOX_MODE')
        if (verified.get('active_mask') != mask
                or box_mode.get('box_mode_code')
                != self.BOX_MODE_IDLE):
            self._best_effort_stop(mask)
            raise gcmd.error(
                "CFS did not arm steady slot %s feed: selection=%s "
                "operation_mode=%s"
                % (slot, verified, box_mode))
        self.status.update({
            'feed_enabled': True,
            'active_mask': mask,
            'active_slots': [slot],
            'active_slot': slot,
            'box_mode_code': box_mode.get('box_mode_code'),
            'box_mode': box_mode.get('box_mode'),
        })
        gcmd.respond_info(
            "CFS feed armed for slot %s\n%s"
            % (slot, "\n".join(sent)))

    def cmd_CFS_FEED_START(self, gcmd):
        slot = gcmd.get('SLOT').upper()
        self._start_feed(
            gcmd, slot, require_confirm=True, require_selected=False)

    def _select_tool(self, gcmd, slot):
        self._start_feed(
            gcmd, slot, require_confirm=False, require_selected=True)
        gcmd.respond_info(
            "T%d selected already-loaded CFS slot %s"
            % (ord(slot) - ord('A'), slot))

    def cmd_CFS_FEED_STOP(self, gcmd):
        self._require_actuation(gcmd)
        sent = []
        try:
            frame, _ = self._run_actuation(
                'TIGHTEN_UP DISABLE',
                [0xff, 0x0f, self.TENSION_DISABLE])
            sent.append(frame.hex(' '))
            frame, _ = self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
            sent.append(frame.hex(' '))
        except Exception as exc:
            raise gcmd.error(str(exc))
        verified = self._run_query('BOX_MODE')
        if verified.get('box_mode_code') != self.BOX_MODE_IDLE:
            raise gcmd.error(
                "CFS did not enter IDLE mode: %s" % verified)
        self.status.update({
            'feed_enabled': False,
            'box_mode_code': self.BOX_MODE_IDLE,
            'box_mode': 'idle',
        })
        gcmd.respond_info(
            "CFS feed disarmed\n%s" % "\n".join(sent))

    def _require_safe_motion(self, gcmd, command):
        self._require_actuation(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("%s requires CONFIRM=1" % command)
        toolhead = self.printer.lookup_object('toolhead')
        homed = toolhead.get_status(0.).get('homed_axes', '')
        if not all(axis in homed for axis in 'xyz'):
            raise gcmd.error(
                "CFS move requires X, Y, and Z homed")
        position = toolhead.get_position()
        if position[2] < self.purge_min_z:
            raise gcmd.error(
                "CFS move requires Z >= %.3f (current %.3f)"
                % (self.purge_min_z, position[2]))
        return position

    def cmd_CFS_PURGE_POSITION(self, gcmd):
        self._require_safe_motion(gcmd, "CFS_PURGE_POSITION")
        feedrate = self.purge_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
            "G1 X%.3f F%.3f" % (self.purge_entry_x, feedrate),
            "G1 Y%.3f F%.3f" % (self.purge_y, feedrate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_gate_x, self.purge_y, feedrate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_x, self.purge_y, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS purge position reached: X%.3f Y%.3f"
            % (self.purge_x, self.purge_y))

    def _ensure_purge_position(self, gcmd):
        position = self._require_safe_motion(gcmd, "CFS_PURGE_POSITION")
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - self.purge_x) <= tolerance
                and abs(position[1] - self.purge_y) <= tolerance):
            return
        self.cmd_CFS_PURGE_POSITION(gcmd)

    def cmd_CFS_PURGE_RETURN(self, gcmd):
        position = self._require_safe_motion(gcmd, "CFS_PURGE_RETURN")
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - self.purge_x) > tolerance
                or abs(position[1] - self.purge_y) > tolerance):
            raise gcmd.error(
                "CFS purge return requires the toolhead at X%.3f Y%.3f "
                "within %.3fmm (current X%.3f Y%.3f)"
                % (self.purge_x, self.purge_y, tolerance,
                   position[0], position[1]))

        feedrate = self.purge_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
            "G1 X%.3f F%.3f" % (self.purge_return_x, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS purge return reached: X%.3f Y%.3f"
            % (self.purge_return_x, self.purge_safe_y))

    def cmd_NOZZLE_WIPE(self, gcmd):
        self._require_safe_motion(gcmd, "NOZZLE_WIPE")
        travel_rate = self.purge_speed * 60.
        entry_script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, travel_rate),
            "G1 X%.3f F%.3f" % (self.purge_entry_x, travel_rate),
            "G1 Y%.3f F%.3f" % (self.purge_y, travel_rate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_gate_x, self.purge_y, travel_rate),
            "G1 X%.3f Y%.3f F%.3f"
            % (self.purge_x, self.purge_y, travel_rate),
            "M400",
        ))
        toolhead = self.printer.lookup_object('toolhead')
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            raise gcmd.error("Unable to read current acceleration limit")
        saved = False
        accel_changed = False
        try:
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=NOZZLE_WIPE")
            saved = True
            accel_changed = True
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f" % self.clean_accel)
            self.gcode.run_script_from_command(entry_script)
            self.gcode.run_script_from_command(
                self._purge_wipe_script(True))
        except Exception as exc:
            raise gcmd.error("K2 nozzle wipe failed: %s" % exc)
        finally:
            if accel_changed:
                self.gcode.run_script_from_command(
                    "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=NOZZLE_WIPE")
        gcmd.respond_info(
            "K2 nozzle wipe complete; parked at X%.3f Y%.3f"
            % (self.purge_return_x, self.purge_safe_y))

    def _require_position(self, gcmd, position, expected_x, expected_y,
                          description):
        tolerance = self.purge_position_tolerance
        if (abs(position[0] - expected_x) > tolerance
                or abs(position[1] - expected_y) > tolerance):
            raise gcmd.error(
                "%s requires the toolhead at X%.3f Y%.3f within %.3fmm "
                "(current X%.3f Y%.3f)"
                % (description, expected_x, expected_y, tolerance,
                   position[0], position[1]))

    def cmd_CFS_CUTTER_POSITION(self, gcmd):
        position = self._require_safe_motion(
            gcmd, "CFS_CUTTER_POSITION")
        self._require_position(
            gcmd, position, self.purge_return_x, self.purge_safe_y,
            "CFS cutter position")
        feedrate = self.cutter_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.cutter_y, feedrate),
            "G1 X%.3f F%.3f" % (self.cutter_x, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS cutter staging position reached: X%.3f Y%.3f "
            "(movement only; cutter not actuated)"
            % (self.cutter_x, self.cutter_y))

    def cmd_CFS_CUTTER_RETURN(self, gcmd):
        position = self._require_safe_motion(
            gcmd, "CFS_CUTTER_RETURN")
        self._require_position(
            gcmd, position, self.cutter_x, self.cutter_y,
            "CFS cutter return")
        feedrate = self.cutter_speed * 60.
        script = "\n".join((
            "G90",
            "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
            "G1 X%.3f F%.3f" % (self.purge_return_x, feedrate),
            "M400",
        ))
        self.gcode.run_script_from_command(script)
        gcmd.respond_info(
            "CFS cutter return reached: X%.3f Y%.3f"
            % (self.purge_return_x, self.purge_safe_y))

    def cmd_CFS_CUTTER_SENSOR(self, gcmd):
        state = ("unknown" if self.cutter_switch_state is None else
                 str(int(self.cutter_switch_state)))
        gcmd.respond_info(
            "CFS cutter sensor %s logical=%s"
            % (self.cutter_switch_pin, state))

    def _finish_unload(self, mask, report_error=True):
        try:
            return self._run_actuation(
                'RETRUDE FINISH',
                [0xff, 0x11, mask, 0x01],
                timeout=self.unload_bus_timeout)
        except Exception:
            if report_error:
                raise
            return None

    def cmd_CFS_UNLOAD(self, gcmd):
        raise gcmd.error(
            "Standalone CFS_UNLOAD is disabled: Creality requires CUT "
            "before RETRUDE. Use CFS_CUT_UNLOAD.")

    def _cmd_CFS_UNLOAD_AFTER_CUT(self, gcmd):
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_UNLOAD requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        measuring = self._run_query('MEASURING')
        if measuring.get('active_mask') != mask:
            raise gcmd.error(
                "CFS_UNLOAD slot %s requires active mask 0x%02x "
                "(reported 0x%02x)"
                % (slot, mask, measuring.get('active_mask', 0)))
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS unload requires filament at the nozzle sensor")
        self._require_hot_extruder(gcmd)

        saved = False
        finish_sent = False
        try:
            # The current K2 wrapper idles every slot, then issues four
            # RETRUDE stage-0 frames around 3x fast + 1x slow 10mm pulls.
            self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_UNLOAD")
            saved = True
            if self.unload_rewind_pause_ms:
                self.gcode.run_script_from_command(
                    "G4 P%d" % self.unload_rewind_pause_ms)
            for _ in range(self.unload_fast_pulls):
                self._run_actuation(
                    'RETRUDE START',
                    [0xff, 0x11, mask, 0x00],
                    timeout=self.unload_bus_timeout)
                self.gcode.run_script_from_command(
                    "M83\n"
                    "G1 E0.010 F60\n"
                    "G1 E-%.3f F%.3f\n"
                    "M400"
                    % (self.unload_pull_length,
                       self.unload_pull_speed * 60.))
            self._run_actuation(
                'RETRUDE START',
                [0xff, 0x11, mask, 0x00],
                timeout=self.unload_bus_timeout)
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E-%.3f F%.3f\n"
                "M400"
                % (self.unload_final_length,
                   self.unload_final_speed * 60.))
            self._finish_unload(mask)
            finish_sent = True
        except Exception as exc:
            if not finish_sent:
                self._finish_unload(mask, report_error=False)
            raise gcmd.error("CFS unload failed: %s" % exc)
        finally:
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=K2_CFS_UNLOAD")

        # Firmware can acknowledge RETRUDE FINISH before the nozzle-side
        # switch callback reaches Klippy. Give the physical filament time to
        # clear instead of reporting a false failure immediately.
        if not self._wait_nozzle_filament(
                False, self.unload_sensor_timeout):
            raise gcmd.error(
                "CFS unload finished but the nozzle sensor still "
                "detects filament after %.1fs"
                % self.unload_sensor_timeout)
        self.status.update({
            'feed_enabled': False,
            'active_mask': 0,
            'active_slots': [],
            'active_slot': None,
            'box_mode_code': self.BOX_MODE_IDLE,
            'box_mode': 'idle',
            'last_unloaded_slot': slot,
        })
        gcmd.respond_info(
            "CFS slot %s unload complete; nozzle sensor is clear" % slot)

    def cmd_CFS_CUT_UNLOAD(self, gcmd):
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_CUT_UNLOAD requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        position = self._require_safe_motion(gcmd, "CFS_CUT_UNLOAD")
        self._require_position(
            gcmd, position, self.purge_return_x, self.purge_safe_y,
            "CFS cut/unload")
        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        measuring = self._run_query('MEASURING')
        if measuring.get('active_mask') != mask:
            raise gcmd.error(
                "CFS_CUT_UNLOAD slot %s requires active mask 0x%02x "
                "(reported 0x%02x)"
                % (slot, mask, measuring.get('active_mask', 0)))
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS cut/unload requires filament at the nozzle sensor")
        self._require_hot_extruder(gcmd)
        if self.cutter_switch_state is not True:
            raise gcmd.error(
                "CFS cutter is not in its returned state before cutting")

        toolhead = self.printer.lookup_object('toolhead')
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            raise gcmd.error("Unable to read current acceleration limit")
        saved = False
        accel_changed = False
        cut_completed = False
        runout_disabled = False
        try:
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_CUT_UNLOAD")
            saved = True
            # A deliberate unload transitions the same physical switch used
            # for print runout. Mask only its runout handler while retaining
            # physical state reads for completion verification.
            self._set_runout_sensor_enabled(False)
            runout_disabled = self.manage_runout_sensor
            # Exact current-wrapper release sequence: tiny engagement, 2mm
            # retract, cutter stage/ram/release, another 2mm retract, then
            # a 7mm forward/back tip-shaping cycle.
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E0.010 F60\n"
                "G1 E-%.3f F%.3f\n"
                "M400"
                % (self.cutter_retract_length,
                   self.cutter_retract_speed * 60.))
            travel_rate = self.cutter_speed * 60.
            self.gcode.run_script_from_command(
                "G90\n"
                "G1 Y%.3f F%.3f\n"
                "G1 X%.3f F%.3f\n"
                "M400"
                % (self.cutter_y, travel_rate,
                   self.cutter_x, travel_rate))
            if self.cutter_switch_state is not True:
                raise RuntimeError(
                    "cutter did not report returned at staging point")
            accel_changed = True
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f\n"
                "G90\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "M400"
                % (self.cutter_ram_accel,
                   self.cutter_cut_x, self.cutter_y,
                   self.cutter_ram_speed * 60.,
                   self.cutter_release_x, self.cutter_y,
                   self.cutter_release_speed * 60.))
            if self.cutter_switch_state is not True:
                raise RuntimeError(
                    "cutter did not return after ram/release")
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E-%.3f F%.3f\n"
                "G1 E%.3f F%.3f\n"
                "G1 E-%.3f F%.3f\n"
                "M400"
                % (self.cutter_retract_length,
                   self.cutter_retract_speed * 60.,
                   self.cutter_tip_length,
                   self.cutter_tip_push_speed * 60.,
                   self.cutter_tip_length,
                   self.cutter_tip_pull_speed * 60.))
            cut_completed = True
            self.gcode.run_script_from_command(
                "G90\n"
                "G1 Y%.3f F%.3f\n"
                "G1 X%.3f F%.3f\n"
                "G1 Y%.3f F%.3f\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "G1 X%.3f Y%.3f F%.3f\n"
                "M400"
                % (self.purge_safe_y, travel_rate,
                   self.purge_entry_x, travel_rate,
                   self.purge_y, travel_rate,
                   self.purge_gate_x, self.purge_y, travel_rate,
                   self.purge_x, self.purge_y, travel_rate))
            if not cut_completed:
                raise RuntimeError(
                    "internal safety gate: rewind requested before cut")
            self._cmd_CFS_UNLOAD_AFTER_CUT(gcmd)
        except Exception as exc:
            raise gcmd.error("CFS cut/unload failed: %s" % exc)
        finally:
            try:
                if accel_changed:
                    self.gcode.run_script_from_command(
                        "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
                if saved:
                    self.gcode.run_script_from_command(
                        "RESTORE_GCODE_STATE NAME=K2_CFS_CUT_UNLOAD")
            finally:
                if runout_disabled:
                    self._set_runout_sensor_enabled(True)

        self.status['last_cut_unloaded_slot'] = slot
        gcmd.respond_info(
            "CFS slot %s cut and unload complete" % slot)

    def _load_stage(self, name, mask, stage, value=0,
                    report_error=True):
        try:
            return self._run_actuation(
                name,
                [0xff, 0x10, mask, stage, value],
                timeout=self.load_bus_timeout)
        except Exception:
            if report_error:
                raise
            return None

    def cmd_CFS_LOAD(self, gcmd):
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_LOAD requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        mask = self.SLOT_MASKS[slot]

        position = self._require_safe_motion(gcmd, "CFS_LOAD")
        self._require_position(
            gcmd, position, self.purge_x, self.purge_y,
            "CFS load")
        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        measuring = self._run_query('MEASURING')
        old_mask = measuring.get('active_mask', 0)
        if old_mask not in (0, mask):
            raise gcmd.error(
                "CFS selected mask is 0x%02x; refusing load from slot %s"
                % (old_mask, slot))
        if self._nozzle_has_filament() is not False:
            raise gcmd.error(
                "CFS load requires the nozzle filament sensor to be clear")
        self._require_hot_extruder(gcmd)

        saved = False
        try:
            # Current Creality wrapper sequence, reconstructed offline:
            # all-idle -> stage 0 -> stage 4 -> repeated stage 5. Its
            # filament-sensor callback then feeds E+5 and sends stage 6.
            # The callback releases the stage-5 loop; E+9 and stage 7(value=3)
            # follow immediately. Sending another stage 5 after stage 6 is
            # rejected by CFS firmware 1.5.0 as EXTRUDE_ERR8 (state 0x0c).
            self._run_actuation(
                'SET_BOX_MODE IDLE',
                [0xff, 0x04, 0x00, self.MODE_IDLE])
            # Stock issues ff 0f 01 before stage 0, then ff 0f 00 only after
            # selecting the steady print route. Despite the command name,
            # value 1 stops normal buffer tension while the staged load owns
            # the hub motor.
            self._run_actuation(
                'TIGHTEN_UP DISABLE',
                [0xff, 0x0f, self.TENSION_DISABLE])
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_LOAD")
            saved = True
            self._load_stage('EXTRUDE STAGE 0', mask, 0)
            self._load_stage('EXTRUDE STAGE 4', mask, 4)

            reactor = self.printer.get_reactor()
            deadline = reactor.monotonic() + self.load_timeout
            while True:
                try:
                    self._load_stage('EXTRUDE STAGE 5', mask, 5)
                except Exception:
                    # Firmware 1.5.0 may advance while the nozzle-sensor
                    # callback is arriving, then reject the in-flight stage-5
                    # retry with state 0x0c. The asserted physical sensor
                    # makes that response terminal success.
                    if self._nozzle_has_filament() is True:
                        break
                    raise
                if self._nozzle_has_filament() is True:
                    break
                now = reactor.monotonic()
                if now >= deadline:
                    raise RuntimeError(
                        "no filament at nozzle sensor after %.1fs"
                        % self.load_timeout)
                reactor.pause(min(deadline, now + self.load_poll_interval))

            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E%.3f F%.3f\n"
                "M400"
                % (self.load_engage_length,
                   self.load_engage_speed * 60.))
            self._load_stage('EXTRUDE STAGE 6', mask, 6)
            self.gcode.run_script_from_command(
                "M83\n"
                "G1 E%.3f F%.3f\n"
                "M400"
                % (self.load_extrude_length,
                   self.load_extrude_speed * 60.))
            self._load_stage(
                'EXTRUDE STAGE 7', mask, 7,
                value=self.load_box_finish_length)
            self._run_query('BUFFER_STATE')
            self._run_actuation(
                'SET_BOX_MODE PRINT',
                [0xff, 0x04, mask, self.MODE_PRINT])
            self._run_actuation(
                'TIGHTEN_UP ENABLE',
                [0xff, 0x0f, self.TENSION_ENABLE])
        except Exception as exc:
            self._best_effort_stop(mask)
            raise gcmd.error("CFS load failed: %s" % exc)
        finally:
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=K2_CFS_LOAD")

        if self._nozzle_has_filament() is not True:
            self._best_effort_stop(mask)
            raise gcmd.error(
                "CFS load finished but the nozzle sensor is not triggered")
        verified = self._run_query('MEASURING')
        box_mode = self._run_query('BOX_MODE')
        if (verified.get('active_mask') != mask
                or box_mode.get('box_mode_code')
                != self.BOX_MODE_IDLE):
            self._best_effort_stop(mask)
            raise gcmd.error(
                "CFS loaded filament but did not arm steady slot %s feed: "
                "selection=%s operation_mode=%s"
                % (slot, verified, box_mode))
        self.status.update({
            'feed_enabled': True,
            'active_mask': mask,
            'active_slots': [slot],
            'active_slot': slot,
            'box_mode_code': box_mode.get('box_mode_code'),
            'box_mode': box_mode.get('box_mode'),
            'last_loaded_slot': slot,
        })
        gcmd.respond_info(
            "CFS slot %s load complete; nozzle sensor is triggered" % slot)

    def _purge_wipe_script(self, final):
        feedrate = self.clean_speed * 60.
        lines = [
            "G90",
            "G1 X%.3f F%.3f" % (self.clean_plate_x, feedrate),
            "G1 X%.3f F%.3f" % (self.clean_left_x, feedrate),
        ]
        if final:
            circle_rate = self.clean_circle_speed * 60.
            lines.extend([
                "G1 X%.3f Y%.3f F%.3f"
                % (self.clean_circle_left_x, self.clean_y, feedrate),
                "G2 I4 J0 P1 F%.3f" % circle_rate,
                "G1 X%.3f Y%.3f F%.3f"
                % (self.clean_circle_right_x, self.clean_y, circle_rate),
                "G3 I-4 J0 P1 F%.3f" % circle_rate,
            ])
        for unused in range(3):
            lines.extend([
                "G1 X%.3f Y%.3f F%.3f"
                % (self.clean_right_x, self.clean_y, feedrate),
                "G1 X%.3f Y%.3f F%.3f"
                % (self.purge_x, self.purge_y, feedrate),
            ])
        if final:
            lines.extend([
                "G1 X%.3f F%.3f" % (self.clean_right_x, feedrate),
                "G1 Y%.3f F%.3f" % (self.purge_safe_y, feedrate),
                "G1 X%.3f F%.3f" % (self.purge_return_x, feedrate),
            ])
        lines.append("M400")
        return "\n".join(lines)

    def _purge_segments(self, requested_length):
        # The stock wrapper clamps its nominal 140mm baseline to an actual
        # 160mm flush. Offline traces of 140/160/240mm requests produce
        # 90+70, 90+70, and 90+70+80 respectively.
        actual_length = max(
            requested_length, self.purge_minimum_actual_length)
        segments = [min(self.purge_chunk_length, actual_length)]
        remaining = actual_length - segments[0]
        while remaining > 100.:
            chunk = min(self.purge_followup_chunk_length, remaining)
            segments.append(chunk)
            remaining -= chunk
        if remaining > .0001:
            segments.append(remaining)
        return actual_length, segments

    def cmd_CFS_PURGE(self, gcmd):
        self._require_actuation(gcmd)
        self._require_idle(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_PURGE requires CONFIRM=1")

        position = self._require_safe_motion(gcmd, "CFS_PURGE")
        self._require_position(
            gcmd, position, self.purge_x, self.purge_y, "CFS purge")
        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS purge requires filament at the nozzle sensor")
        self._require_hot_extruder(gcmd)

        measuring = self._run_query('MEASURING')
        mask = measuring.get('active_mask', 0)
        if mask not in self.SLOT_MASKS.values():
            raise gcmd.error(
                "CFS purge requires exactly one active slot "
                "(reported mask 0x%02x)" % mask)
        box_mode = self._run_query('BOX_MODE')
        if box_mode.get('box_mode_code') != self.BOX_MODE_IDLE:
            raise gcmd.error(
                "CFS purge requires an idle operation engine "
                "(reported %s)"
                % box_mode.get('box_mode'))

        length = gcmd.get_float(
            'LENGTH', self.purge_extrude_length,
            minval=140., maxval=240.)
        speed = gcmd.get_float(
            'SPEED', self.purge_extrude_speed,
            above=0., maxval=9.58)
        actual_length, segments = self._purge_segments(length)
        scripts = []
        for index, chunk in enumerate(segments):
            final = index == len(segments) - 1
            slow = chunk * self.purge_slow_fraction
            fast = chunk - slow
            fast_multiplier = 1. if final else self.purge_fast_multiplier
            scripts.append(
                "M83\n"
                "G1 E%.3f F%.3f\n"
                "G1 E%.3f F%.3f\n"
                "M400"
                % (slow, speed * 60.,
                   fast, speed * fast_multiplier * 60.))
            scripts.append(self._purge_wipe_script(final))
        toolhead = self.printer.lookup_object('toolhead')
        prior_accel = toolhead.get_status(0.).get('max_accel')
        if prior_accel is None:
            raise gcmd.error("Unable to read current acceleration limit")
        saved = False
        accel_changed = False
        try:
            self.gcode.run_script_from_command(
                "SAVE_GCODE_STATE NAME=K2_CFS_PURGE")
            saved = True
            accel_changed = True
            self.gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.3f" % self.clean_accel)
            for script in scripts:
                self.gcode.run_script_from_command(script)
        except Exception as exc:
            raise gcmd.error("CFS purge failed: %s" % exc)
        finally:
            if accel_changed:
                self.gcode.run_script_from_command(
                    "SET_VELOCITY_LIMIT ACCEL=%.3f" % prior_accel)
            if saved:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=K2_CFS_PURGE")

        if self._nozzle_has_filament() is not True:
            raise gcmd.error(
                "CFS purge finished but the nozzle sensor is clear")
        verified = self._run_query('MEASURING')
        box_mode = self._run_query('BOX_MODE')
        if (verified.get('active_mask') != mask
                or box_mode.get('box_mode_code')
                != self.BOX_MODE_IDLE):
            raise gcmd.error(
                "CFS purge changed selection or left an operation active: "
                "selection=%s operation_mode=%s" % (verified, box_mode))
        self.status.update({
            'last_purge_requested_length': length,
            'last_purge_length': actual_length,
            'last_purge_speed': speed,
        })
        gcmd.respond_info(
            "CFS purge complete: requested %.3fmm, stock-clamped %.3fmm "
            "at %.3fmm/s" % (length, actual_length, speed))

    def cmd_CFS_PRINT_START(self, gcmd):
        self._require_actuation(gcmd)
        self._require_print_start(gcmd)
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("CFS_PRINT_START requires CONFIRM=1")
        slot = gcmd.get('SLOT').upper()
        if slot not in self.SLOT_MASKS:
            raise gcmd.error("SLOT must be one of A, B, C, D")
        if self.print_start_transaction:
            raise gcmd.error("CFS print-start transaction is already active")

        mask = self.SLOT_MASKS[slot]
        self._require_safe_motion(gcmd, "CFS_PRINT_START")
        self._require_hot_extruder(gcmd)
        filament = self._run_query('FILAMENT_SENSOR')
        slots_loaded = filament.get('slots_loaded')
        slot_index = ord(slot) - ord('A')
        if not slots_loaded or not slots_loaded[slot_index]:
            raise gcmd.error("CFS slot %s does not report filament" % slot)
        measuring = self._run_query('MEASURING')
        active_mask = measuring.get('active_mask', 0)
        nozzle_loaded = self._nozzle_has_filament() is True

        mask_to_slot = {
            slot_mask: slot_name
            for slot_name, slot_mask in self.SLOT_MASKS.items()
        }
        self.print_start_transaction = True
        try:
            if nozzle_loaded:
                active_slot = mask_to_slot.get(active_mask)
                if active_slot is None:
                    raise gcmd.error(
                        "Nozzle has filament but CFS active mask is 0x%02x"
                        % active_mask)
                if active_mask != mask:
                    self._move_to_cut_start()
                    unload_gcmd = self.gcode.create_gcode_command(
                        "CFS_CUT_UNLOAD", "",
                        {"SLOT": active_slot, "CONFIRM": "1"})
                    self.cmd_CFS_CUT_UNLOAD(unload_gcmd)
                    self.cmd_CFS_LOAD(gcmd)
                else:
                    self._start_feed(
                        gcmd, slot, require_confirm=False,
                        require_selected=True)
                    self._ensure_purge_position(gcmd)
            else:
                if active_mask:
                    raise gcmd.error(
                        "Nozzle sensor is clear but CFS active mask is 0x%02x"
                        % active_mask)
                self._ensure_purge_position(gcmd)
                self.cmd_CFS_LOAD(gcmd)

            self.cmd_CFS_PURGE(gcmd)
        except Exception:
            self._best_effort_stop(mask)
            self.status.update({
                'feed_enabled': False,
                'box_mode_code': self.BOX_MODE_IDLE,
                'box_mode': 'idle',
            })
            raise
        finally:
            self.print_start_transaction = False

        self.status['last_print_start_slot'] = slot
        gcmd.respond_info(
            "CFS print start complete: slot %s loaded, purged, and armed"
            % slot)

def load_config(config):
    return K2CFS(config)
