# Creality Hi (F018): pick pressure advance from a strain-gauge PA sweep
# with the printer's own Creality analysis program.
#
# Stock Hi firmware runs a 31-step PA sweep over the chute before a print
# (AiStrainGaugeFlowPaDetectRun_EmptyPrint, captured on a Hi 2026-11-14):
# READ_PRES_PA logs the raw strain gauge about every 100 ms to
# /tmp/saved_whole_stress.csv ("id,stress,time"), then master-server runs
#   /usr/bin/pa_compute --path=<Auto_strain_gauge_pa_compute_config.json>
#                       --material_name=<name>
# which prints "End Position = <pa>". The program only knows the materials in
# that JSON (Hyper PLA, Hyper PETG, Hyper ABS); any other name fails, which is
# what happens with ordinary PLA on stock. Its answer is the JSON's paStrat +
# round x paStep, while the sweep it analyses always runs 0.010 + round x
# 0.002, so stock's Hyper PLA answer is one step low.
#
# HI_PA_COMPUTE takes a pa_pressure_log capture of the same sweep made on
# this firmware (PA_LOG_START SRC=1 ... PA_LOG_STOP NAME=), averages it into
# 100 ms bins like stock's log, writes stock's CSV, runs the printer's own
# pa_compute (it ships with Creality's firmware on every Hi; nothing of
# Creality's is copied into this project) with a material it knows, and turns
# the round it picks back into the PA this sweep really used.
#
#   HI_PA_COMPUTE NAME=<capture> [MATERIAL=PLA|PETG|ABS] [START=0.010]
#                 [STEP=0.002] [COUNT=31] [FAST=9000] [STROKE=40] [SET=0|1]
#
# PA_SWEEP_COMPUTE is the same command under a printer-neutral name (the
# K2 runs the same sweep, PA_SWEEP_CAL). FAST and STROKE are the fast stroke's
# feed (mm/min) and length (mm): the Hi's is 40 mm at F9000. The K2 has
# only ~14 mm clear over its chute, so it runs a scaled copy (shorter
# stroke, speed and accel scaled alike): the extruder sees the same flow
# against time, and the stroke still lasts 0.27 s.
#
# With Klipper on an external host (USB host-link, host/hi) pa_compute is
# still only on the Hi, and it is an ARM program: set remote_host and the
# capture is binned here, the CSV is copied to the Hi's /tmp and pa_compute
# runs there over SSH (key login, no password):
#   [hi_pa_compute]
#   remote_host: root@192.168.3.96
#   ssh_key: ~/.ssh/id_hi_hostlink
# pa_compute answers -1 when it cannot find a PA in the trace; that is
# reported as a failure, not turned into a PA.
#
# METHOD=knee (the default) reads the PA from the sweep without pa_compute:
# in every round's fast stroke the gauge force peaks at the END of the
# stroke while the PA is too low (the pressure is still building) and near
# its START once the PA is enough (it overshoots). The knee is the round
# where that flips, found as the split that best separates late peaks from
# early ones. 18 Nov 2026, black Hyper PETG 250 C: knee 0.066 / 0.068 / 0.068
# over three sweeps where pa_compute answered on two of five (0.068, 0.076).
# CROSS=1 also runs pa_compute and reports it next to the knee. QUIET=1
# records a failure in get_status instead of raising (the automatic blend
# carries on with its other test). The capture's _extruder.csv gives the
# fast strokes' start times.
#
# Copyright (C) 2026 Hark Tech. This file may be distributed under the terms
# of the GNU GPLv3 license.

import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time

PA_COMPUTE = "/usr/bin/pa_compute"
PA_CONFIG = "/etc/sysConfig/defData/Auto_strain_gauge_pa_compute_config.json"
STRESS_CSV = "/tmp/saved_whole_stress.csv"
# Analysis settings of a material pa_compute knows; the round it picks is
# then mapped onto our own sweep, so only the analysis tuning differs.
MATERIALS = {"PLA": "Hyper PLA", "PETG": "Hyper PETG", "ABS": "Hyper ABS"}
RESULT_RE = re.compile(r"End Position\s*=\s*(-?[0-9.]+)")


def bin_samples(samples, bin_s=0.1):
    """(print_time, raw) samples -> [(bin_end_print_time, mean_raw)]."""
    out = []
    if not samples:
        return out
    samples = sorted(samples)
    start = samples[0][0]
    acc, n, edge = 0, 0, start + bin_s
    for t, v in samples:
        while t >= edge:
            if n:
                out.append((edge, int(round(acc / n))))
            acc, n, edge = 0, 0, edge + bin_s
        acc += v
        n += 1
    if n:
        out.append((edge, int(round(acc / n))))
    return out


def write_stress_csv(path, binned, wall_end):
    """Stock's layout: id,stress,time (wall clock, one-second resolution)."""
    last = binned[-1][0] if binned else 0.
    with open(path, "w") as f:
        f.write("id,stress,time\n")
        for i, (t, v) in enumerate(binned, 1):
            stamp = time.strftime("%Y/%m/%d %H:%M:%S",
                                  time.localtime(wall_end - (last - t)))
            f.write("%d,%d,%s\n" % (i, v, stamp))


def ssh_argv(host, command, key=None):
    """ssh command line for a non-interactive run on the Hi."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR"]
    if key:
        argv += ["-i", os.path.expanduser(key)]
    return argv + [host, command]


def remote_compute_command(program, config_path, material, csv_path):
    """Shell command run on the Hi: store the CSV from stdin where
    pa_compute reads it, then run pa_compute from /tmp as stock does."""
    return "cat > %s && cd /tmp && %s --path=%s --material_name=%s" % (
        shlex.quote(csv_path), shlex.quote(program),
        shlex.quote(config_path), shlex.quote(material))


def fast_strokes(moves, v_fast, tol=0.15):
    """Start print times of the sweep's fast strokes: extruder moves that
    accelerate up to about v_fast (mm/s of filament). The retract and
    unretract moves (40 mm/s) and the slow strokes fall outside tol."""
    out = []
    for t, move_t, start_v, accel in sorted(moves):
        end_v = start_v + accel * move_t
        if accel > 0. and abs(end_v - v_fast) <= tol * v_fast:
            out.append(t)
    return out


def _median(vals):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else 0.5 * (vals[mid - 1] + vals[mid])


def peak_positions(samples, starts, stroke_t, pre=(0.5, 0.05)):
    """Where in each fast stroke the gauge force peaks, 0 = start, 1 = end.
    samples [(print_time, raw)] sorted; the force is taken against the
    level just before the stroke. The gauge's sign is found from the data
    (extruding pushes it one way on every round)."""
    import bisect
    times = [t for t, v in samples]
    segs = []
    for s in starts:
        a, b = bisect.bisect_left(times, s - pre[0]), bisect.bisect_left(
            times, s - pre[1])
        c, d = bisect.bisect_left(times, s), bisect.bisect_left(
            times, s + stroke_t)
        base = _median([v for t, v in samples[a:b]])
        seg = [v - base for t, v in samples[c:d]] if base is not None else []
        segs.append(seg)
    total = sum(sum(seg) for seg in segs)
    sign = -1. if total < 0 else 1.
    out = []
    for seg in segs:
        if len(seg) < 5:
            out.append(None)
            continue
        vals = [sign * v for v in seg]
        out.append(vals.index(max(vals)) / float(len(vals)))
    return out


def find_knee(positions, late=0.6, early=0.5, near=1.):
    """(first_early_round, errors) splitting the rounds into late peaks
    before and early peaks after with the fewest misfits; a peak between
    early and late counts half either way, a missing round not at all.
    first_early_round 0 = every round early (knee below the sweep), len =
    none early (knee above it). Where the flip is ragged (late and early
    peaks mixed over a few rounds) every split within `near` misfits of the
    best is averaged, so the knee lands mid-way through the mixed rounds
    rather than at their first early one; the round is then fractional."""
    n = len(positions)
    def lateness(p):
        if p is None:
            return None
        return 1. if p >= late else 0. if p < early else 0.5
    lt = [lateness(p) for p in positions]
    costs = [sum(1. - x for x in lt[:k] if x is not None)
             + sum(x for x in lt[k:] if x is not None) for k in range(n + 1)]
    low = min(costs)
    ks = [k for k, c in enumerate(costs) if c <= low + near + 1e-9]
    # an edge split (all early / none early) stays an edge answer
    if 0 in ks and costs[0] <= low + 1e-9:
        return 0, low
    if n in ks and costs[n] <= low + 1e-9:
        return n, low
    ks = [k for k in ks if 0 < k < n] or ks
    return sum(ks) / float(len(ks)), low


def knee_result(positions, start, step, max_errors=None):
    """{'status': ok|low|high|unclear, 'pa', 'round', 'errors'}."""
    valid = [p for p in positions if p is not None]
    n = len(positions)
    if len(valid) < max(8, n // 2):
        return {'status': 'unclear', 'pa': None, 'round': None,
                'errors': None, 'rounds': len(valid)}
    k, errors = find_knee(positions)
    limit = max(2., 0.2 * len(valid)) if max_errors is None else max_errors
    out = {'round': round(k, 2), 'errors': errors, 'rounds': len(valid),
           'pa': round(start + k * step, 4) if 0 < k < n else None}
    # too many misfits: no knee at all, wherever the best split fell
    if errors > limit:
        out['status'] = 'unclear'
    elif k <= 1:
        out['status'] = 'low'
    elif k >= n - 1:
        out['status'] = 'high'
    else:
        out['status'] = 'ok'
    if out['status'] != 'ok':
        out['pa'] = None
    return out


def map_result(value, material_cfg, start, step, count):
    """pa_compute's answer -> (round, PA of our sweep)."""
    strat = float(material_cfg.get("paStrat"))
    mstep = float(material_cfg.get("paStep"))
    rnd = int(round((value - strat) / mstep))
    rnd = max(0, min(count - 1, rnd))
    return rnd, start + rnd * step


class HiPACompute:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.program = config.get("program", PA_COMPUTE)
        self.config_path = config.get("pa_config", PA_CONFIG)
        self.csv_path = config.get("stress_csv", STRESS_CSV)
        self.bin_s = config.getfloat("bin_time", 0.1, minval=0.02, maxval=1.)
        self.remote = config.get("remote_host", None)
        self.ssh_key = config.get("ssh_key", None)
        # pa_compute reads the CSV named in its own JSON (stock /tmp path)
        self.remote_csv = config.get("remote_stress_csv", STRESS_CSV)
        self.last = {}
        self.gcode.register_command(
            "HI_PA_COMPUTE", self.cmd_HI_PA_COMPUTE,
            desc="Pick PA from a sweep capture with the Hi's pa_compute")
        self.gcode.register_command(
            "PA_SWEEP_COMPUTE", self.cmd_HI_PA_COMPUTE,
            desc="Pick PA from a strain-gauge PA sweep capture (knee)")

    def _read_capture(self, name):
        log = self.printer.lookup_object("pa_pressure_log", None)
        if log is None:
            raise self.gcode.error("HI_PA_COMPUTE needs [pa_pressure_log]")
        path = os.path.join(log.out_dir, name + "_pressure.csv")
        samples = []
        with open(path) as f:
            next(f)
            for line in f:
                t, v = line.strip().split(",")
                samples.append((float(t), int(v)))
        return samples, path

    def _read_moves(self, name):
        log = self.printer.lookup_object("pa_pressure_log", None)
        path = os.path.join(log.out_dir, name + "_extruder.csv")
        moves = []
        with open(path) as f:
            next(f)
            for line in f:
                t, mt, sv, ac = line.strip().split(",")[:4]
                moves.append((float(t), float(mt), float(sv), float(ac)))
        return moves

    def _exec(self, argv, stdin_path=None, timeout=60.):
        """Run argv without blocking the reactor; return (returncode,
        stdout, stderr). stderr is kept apart (ssh prints warnings there)."""
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
            try:
                proc = subprocess.Popen(argv, cwd="/tmp", stdin=stdin,
                                        stdout=out, stderr=err)
            except OSError as exc:
                raise self.gcode.error("Cannot run %s: %s" % (argv[0], exc))
            finally:
                if stdin_path:
                    stdin.close()
            deadline = self.reactor.monotonic() + timeout
            while proc.poll() is None:
                if self.reactor.monotonic() > deadline:
                    proc.kill()
                    raise self.gcode.error("%s did not finish in %d s"
                                           % (argv[0], timeout))
                self.reactor.pause(self.reactor.monotonic() + 0.2)
            out.seek(0)
            err.seek(0)
            return (proc.returncode, out.read().decode(errors="replace"),
                    err.read().decode(errors="replace"))

    def _ssh(self, command, stdin_path=None):
        return self._exec(ssh_argv(self.remote, command, self.ssh_key),
                          stdin_path)

    def _material_cfg(self, material):
        if self.remote:
            rc, text, err = self._ssh("cat %s"
                                      % (shlex.quote(self.config_path),))
            if rc != 0:
                raise self.gcode.error("Cannot read %s on %s: %s" % (
                    self.config_path, self.remote, err.strip()[-200:]))
        else:
            if not os.access(self.program, os.X_OK):
                raise self.gcode.error(
                    "%s not found: this needs the Creality Hi's own firmware"
                    " files (on an external host set remote_host)"
                    % (self.program,))
            try:
                with open(self.config_path) as f:
                    text = f.read()
            except OSError as exc:
                raise self.gcode.error("Cannot read %s: %s"
                                       % (self.config_path, exc))
        try:
            return json.loads(text)[material]
        except (ValueError, KeyError) as exc:
            raise self.gcode.error("No %s in %s: %s"
                                   % (material, self.config_path, exc))

    def _run(self, material):
        if self.remote:
            rc, out, err = self._ssh(remote_compute_command(
                self.program, self.config_path, material, self.remote_csv),
                stdin_path=self.csv_path)
        else:
            rc, out, err = self._exec([self.program, "--path=%s" % (
                self.config_path,), "--material_name=%s" % (material,)])
        # pa_compute prints its errors (unknown material) on stdout
        return out + err

    def _pa_compute(self, name, material, samples, start, step, count):
        """Creality's pa_compute on the capture: (round, PA) or raises."""
        material_cfg = self._material_cfg(material)
        binned = bin_samples(samples, self.bin_s)
        if len(binned) < count * 10:
            raise self.gcode.error("Capture %s has only %d samples after "
                                   "binning" % (name, len(binned)))
        write_stress_csv(self.csv_path, binned, time.time())
        out = self._run(material)
        logging.info("hi_pa_compute: %s%s %s -> %r", self.program,
                     " on %s" % (self.remote,) if self.remote else "",
                     material, out)
        m = RESULT_RE.search(out)
        if not m:
            raise self.gcode.error("pa_compute gave no result: %s"
                                   % (out.strip()[-300:],))
        value = float(m.group(1))
        if value < 0:
            raise self.gcode.error(
                "pa_compute (%s) found no pressure advance in the %s sweep "
                "(answer %.4f)" % (material, name, value))
        rnd, pa = map_result(value, material_cfg, start, step, count)
        return value, rnd, pa, len(binned)

    def cmd_HI_PA_COMPUTE(self, gcmd):
        name = gcmd.get("NAME")
        key = gcmd.get("MATERIAL", "PLA").upper()
        if key not in MATERIALS:
            raise gcmd.error("MATERIAL must be one of %s (the analysis "
                             "tuning to use)" % (", ".join(MATERIALS),))
        material = MATERIALS[key]
        start = gcmd.get_float("START", 0.010, minval=0.)
        step = gcmd.get_float("STEP", 0.002, above=0.)
        count = gcmd.get_int("COUNT", 31, minval=2)
        fast = gcmd.get_float("FAST", 9000., above=0.)
        stroke = gcmd.get_float("STROKE", 40., above=0.)
        method = gcmd.get("METHOD", "knee").lower()
        if method not in ("knee", "pa_compute"):
            raise gcmd.error("METHOD must be knee or pa_compute")
        cross = gcmd.get_int("CROSS", 0, minval=0, maxval=1)
        quiet = gcmd.get_int("QUIET", 0, minval=0, maxval=1)
        apply_pa = gcmd.get_int("SET", 0, minval=0, maxval=1)
        self.last = {}
        last = {"name": name, "method": method, "start": start,
                "step": step, "count": count, "knee": None,
                "knee_status": None, "pa_compute": None, "pa": None,
                "stamp": round(time.time(), 3)}

        def fail(msg):
            last["error"] = msg
            self.last = last
            if quiet:
                gcmd.respond_info(msg)
                return
            raise gcmd.error(msg)
        try:
            samples, src = self._read_capture(name)
        except OSError as exc:
            return fail("Cannot read the capture %s: %s" % (name, exc))
        last["samples"] = len(samples)
        if method == "knee" or cross:
            try:
                moves = self._read_moves(name)
            except (OSError, ValueError, AttributeError) as exc:
                moves = None
                last["knee_status"] = "no extruder log (%s)" % (exc,)
            if moves is not None:
                stroke_t = stroke / (fast / 60.)
                starts = fast_strokes(moves, 1.853 / stroke_t)
                positions = peak_positions(sorted(samples), starts[:count],
                                           stroke_t)
                k = knee_result(positions, start, step)
                last.update(knee=k["pa"], knee_status=k["status"],
                            knee_round=k["round"], knee_errors=k["errors"],
                            strokes=len(starts))
                logging.info("hi_pa_compute: knee %s positions %s", k,
                             [None if x is None else round(x, 2)
                              for x in positions])
        if method == "pa_compute" or cross:
            try:
                value, rnd, pa, bins = self._pa_compute(
                    name, material, samples, start, step, count)
                last.update(pa_compute=pa, raw=value, round=rnd, bins=bins)
            except self.printer.command_error as exc:
                last["pa_compute_error"] = str(exc)
        chosen = last["knee"] if method == "knee" else last["pa_compute"]
        parts = []
        if method == "knee" or cross:
            parts.append("knee %s" % ("%.4f" % last["knee"]
                                      if last["knee"] is not None else
                                      "none (%s)" % (last["knee_status"],)))
        if method == "pa_compute" or cross:
            parts.append("pa_compute %s" % ("%.4f" % last["pa_compute"]
                                            if last["pa_compute"] is not None
                                            else "none"))
        summary = "PA sweep %s (%.3f-%.3f): %s" % (
            name, start, start + step * (count - 1), ", ".join(parts))
        if chosen is None:
            why = {"low": "the knee is below this sweep: start lower",
                   "high": "the knee is above this sweep: start higher"}.get(
                last.get("knee_status"), "no pressure advance found")
            return fail("%s; %s. PA left as it was." % (summary, why)
                        if method == "knee" else "%s; %s. PA left as it "
                        "was." % (summary, last.get("pa_compute_error", why)))
        last["pa"] = chosen
        self.last = last
        gcmd.respond_info("%s -> PA %.4f" % (summary, chosen))
        if apply_pa:
            self.gcode.run_script_from_command(
                "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % (chosen,))
            gcmd.respond_info("Pressure advance set to %.4f" % (chosen,))

    def get_status(self, eventtime):
        return dict(self.last)


def load_config(config):
    return HiPACompute(config)
