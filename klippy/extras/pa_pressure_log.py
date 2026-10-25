# Log the K2 nozzle strain gauge against commanded extrusion, for
# pressure advance calibration from nozzle force (the method Prusa's
# PATuner, bd_pressure and Bambu's force-sensor models use).
#
# PA_LOG_START                 start streaming the gauge (~1280 Hz)
# PA_LOG_MARK LABEL=<text>     tag the next move's print time
# PA_LOG_STOP NAME=<name>      stop and write <dir>/<name>_{pressure,extruder,marks}.csv
# PA_CAL_ANALYZE NAME=<name> [SLOT=<A-D> SAVE=1]
#                              pick PA from that log (pa_analysis.py, run as a
#                              subprocess so the reactor never stalls); with
#                              SAVE=1 and a result that passes its quality
#                              checks, store it with CFS_SLOT_SET
#
# Pressure samples are timed in Klipper print time (nozzle_mcu clock), the
# same timeline as the extruder moves pulled from motion_report, so the
# analysis can line them up exactly.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os, subprocess, sys
from .prtouch_open import zip as _zip

ANALYSIS = os.path.join(os.path.dirname(__file__), 'pa_analysis.py')

# The MCU fills a 64-sample buffer at 0.78125 ms per sample (50 ms) and only
# hands it over once full, so each window streams a little longer than that.
BUF_LEN = 64


class PAPressureLog:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.period = config.getfloat('period', 0.056, minval=0.052,
                                      maxval=0.2)
        self.out_dir = config.get(
            'output_dir', '/mnt/UDISK/printer_data_mainline/config/pa_logs')
        self.prtouch = self.sampler = self.mcu = self.dtrapq = None
        self.timer = None
        self.src = 0
        self.analysing = False
        self.last_result = {}
        self.reset()
        self.printer.register_event_handler('klippy:ready', self._ready)
        for name, desc in (
                ('PA_LOG_START', "Start logging the nozzle strain gauge"),
                ('PA_LOG_MARK', "Tag the next move in the pressure log"),
                ('PA_LOG_STOP', "Stop logging and write the CSV files"),
                ('PA_CAL_ANALYZE', "Pick pressure advance from a PA log")):
            self.gcode.register_command(
                name, getattr(self, 'cmd_' + name), desc=desc)

    def reset(self):
        self.samples = []       # (print_time, raw)
        self.moves = {}         # print_time -> (move_t, start_v, accel, start_e)
        self.marks = []         # (print_time, label)
        self.streaming = False
        self.stop_requested = self.finished = False
        self.windows = 0
        self.pulled_to = 0.
        self.started_at = 0.

    def _ready(self):
        self.prtouch = self.printer.lookup_object('prtouch_mainline', None)
        if self.prtouch is not None:
            self.sampler = self.prtouch.sampler
            self.mcu = self.prtouch.pres_mcu
        mr = self.printer.lookup_object('motion_report', None)
        if mr is not None:
            self.dtrapq = mr.dtrapqs.get('extruder')

    def _now_print_time(self):
        return self.mcu.estimated_print_time(self.reactor.monotonic())

    def _pull_moves(self):
        if self.dtrapq is None:
            return
        end = self._now_print_time()
        moves, _ = self.dtrapq.extract_trapq(self.pulled_to, end)
        for m in moves:
            self.moves[round(m.print_time, 6)] = (
                m.move_t, m.start_v, m.accel, m.start_x)
        self.pulled_to = max(self.pulled_to, end - 2.)

    def _read_window(self):
        """Read the MCU's filled 64-sample buffer in message-sized chunks.
        Each chunk decodes on its own; samples carry their MCU ticks, and
        unfilled slots come back as zero ticks."""
        oid = self.prtouch.pres_oid
        c64 = self.mcu.clock32_to_clock64
        idx = 0
        while idx < BUF_LEN:
            resp = self.sampler.proto.read.send(
                [oid, self.src, 0, idx, BUF_LEN - idx])
            n = resp.get('len', 0) if isinstance(resp, dict) else 0
            if not n:
                break
            _, vals = _zip.unzip_data(resp.get('datas', b''), signed=True)
            _, ticks = _zip.unzip_data(resp.get('ticks', b''), signed=False)
            for i, (tk, v) in enumerate(zip(ticks, vals)):
                # Unfilled slots have zero ticks; the first sample after
                # a restart reads offset, so it is dropped too
                if tk and idx + i:
                    self.samples.append(
                        (self.mcu.clock_to_print_time(c64(tk & 0xffffffff)),
                         v))
            idx += n
        self.windows += 1

    def _poll(self, eventtime):
        s = self.sampler
        try:
            if self.streaming:
                # Read while still streaming: stopping first empties the
                # buffer (the probe's TAP and TRIGGER_TEST read this way).
                self._read_window()
                s.stop_streaming()
                self.streaming = False
            if self.stop_requested:
                self.finished = True
                return self.reactor.NEVER
            if getattr(self.prtouch, '_probe_active', False):
                # A probe owns the sampler; skip this window.
                return eventtime + self.period
            cfg = self.prtouch.cfg
            s.start_streaming(min_hold=cfg.min_hold, max_hold=cfg.max_hold,
                              add_hold=cfg.add_hold)
            self.streaming = True
            if eventtime - self.started_at > 1.:
                self._pull_moves()
        except Exception:
            logging.exception("pa_pressure_log: poll failed")
            if self.stop_requested:
                self.finished = True
                return self.reactor.NEVER
        # The buffer fills a fixed time after the restart, however long
        # the read took
        return self.reactor.monotonic() + self.period

    def cmd_PA_LOG_START(self, gcmd):
        if self.sampler is None or self.dtrapq is None:
            raise gcmd.error("PA log needs prtouch_mainline and motion_report")
        if self.timer is not None:
            raise gcmd.error("PA log is already running")
        self.reset()
        # SRC=1 logs the raw ADC instead of the MCU's processed samples
        self.src = gcmd.get_int('SRC', 0, minval=0, maxval=1)
        self.started_at = self.reactor.monotonic()
        self.pulled_to = self._now_print_time() - 1.
        self.timer = self.reactor.register_timer(self._poll, self.reactor.NOW)
        gcmd.respond_info("PA log started")

    def cmd_PA_LOG_MARK(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        self.marks.append((toolhead.get_last_move_time(),
                           gcmd.get('LABEL', '')))

    def cmd_PA_LOG_STOP(self, gcmd):
        if self.timer is None:
            raise gcmd.error("PA log is not running")
        self.printer.lookup_object('toolhead').wait_moves()
        # Let the timer finish its window: a stop sent while a read is in
        # flight collides on the MCU link. (Waking the timer early could
        # re-enter it while it is paused mid-read.)
        self.stop_requested = True
        deadline = self.reactor.monotonic() + 2.
        while not self.finished and self.reactor.monotonic() < deadline:
            self.reactor.pause(self.reactor.monotonic() + 0.02)
        self.reactor.unregister_timer(self.timer)
        self.timer = None
        if self.streaming:
            self.sampler.stop_streaming()
            self.streaming = False
        self._pull_moves()
        name = gcmd.get('NAME', 'pa_log')
        os.makedirs(self.out_dir, exist_ok=True)
        base = os.path.join(self.out_dir, name)
        with open(base + '_pressure.csv', 'w') as f:
            f.write("print_time,raw\n")
            for t, v in self.samples:
                f.write("%.6f,%d\n" % (t, v))
        with open(base + '_extruder.csv', 'w') as f:
            f.write("print_time,move_t,start_v,accel,start_e\n")
            for t in sorted(self.moves):
                mt, sv, ac, se = self.moves[t]
                f.write("%.6f,%.6f,%.6f,%.6f,%.6f\n" % (t, mt, sv, ac, se))
        with open(base + '_marks.csv', 'w') as f:
            f.write("print_time,label\n")
            for t, label in self.marks:
                f.write("%.6f,%s\n" % (t, label))
        gcmd.respond_info(
            "PA log: %d pressure samples (%d windows), "
            "%d extruder moves, %d marks -> %s_*"
            % (len(self.samples), self.windows,
               len(self.moves), len(self.marks), base))


    def cmd_PA_CAL_ANALYZE(self, gcmd):
        name = gcmd.get('NAME')
        slot = gcmd.get('SLOT', '').upper()
        save = gcmd.get_int('SAVE', 0, minval=0, maxval=1)
        self.analysing = True
        gcmd.respond_info("PA analysis running for %s..." % name)
        try:
            proc = subprocess.Popen(
                [sys.executable, ANALYSIS, self.out_dir, name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            while proc.poll() is None:
                self.reactor.pause(self.reactor.monotonic() + 0.2)
            out, err = proc.communicate()
            if proc.returncode != 0:
                raise gcmd.error("PA analysis failed: %s"
                                 % err.decode(errors='replace')[-300:])
            res = json.loads(out.decode())
        finally:
            self.analysing = False
        res['name'] = name
        res['slot'] = slot
        res['saved'] = False
        lines = ["PA analysis %s:" % name]
        for row in res.get('table', []):
            lines.append("  PA %.3f  balance %+.3f  over %.2f  under %.2f  "
                         "snr %.0f" % (row['k'], row['balance'], row['over'],
                                       row['under'], row['snr']))
        if res.get('fitted') is not None:
            lines.append("Best PA %.4f (line R2 %s; PATuner rule %s)"
                         % (res['fitted'], res['r2'], res['no_undershoot']))
        if res.get('problems'):
            lines.append("NOT reliable: " + "; ".join(res['problems']))
        if save and slot and res.get('reliable'):
            self.gcode.run_script_from_command(
                "CFS_SLOT_SET SLOT=%s PA=%.4f" % (slot, res['fitted']))
            res['saved'] = True
            lines.append("Saved PA %.4f to slot %s" % (res['fitted'], slot))
        elif save:
            lines.append("Not saved")
        self.last_result = res
        gcmd.respond_info("\n".join(lines))

    def get_status(self, eventtime):
        last = {k: v for k, v in self.last_result.items() if k != 'table'}
        return {'logging': self.timer is not None,
                'analysing': self.analysing, 'last': last}


def load_config(config):
    return PAPressureLog(config)
