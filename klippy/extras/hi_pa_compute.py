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
#                 [STEP=0.002] [COUNT=31] [SET=0|1]
#
# Copyright (C) 2026 Hark Tech. This file may be distributed under the terms
# of the GNU GPLv3 license.

import json
import logging
import os
import re
import subprocess
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
        self.last = {}
        self.gcode.register_command(
            "HI_PA_COMPUTE", self.cmd_HI_PA_COMPUTE,
            desc="Pick PA from a sweep capture with the Hi's pa_compute")

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

    def _run(self, material):
        proc = subprocess.Popen(
            [self.program, "--path=%s" % (self.config_path,),
             "--material_name=%s" % (material,)],
            cwd="/tmp", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = self.reactor.monotonic() + 60.
        while proc.poll() is None:
            if self.reactor.monotonic() > deadline:
                proc.kill()
                raise self.gcode.error("pa_compute did not finish in 60 s")
            self.reactor.pause(self.reactor.monotonic() + 0.2)
        return proc.communicate()[0].decode(errors="replace")

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
        apply_pa = gcmd.get_int("SET", 0, minval=0, maxval=1)
        if not os.access(self.program, os.X_OK):
            raise gcmd.error("%s not found: this needs the Creality Hi's own"
                             " firmware files" % (self.program,))
        try:
            with open(self.config_path) as f:
                material_cfg = json.load(f)[material]
        except (OSError, ValueError, KeyError) as exc:
            raise gcmd.error("Cannot read %s for %s: %s"
                             % (self.config_path, material, exc))
        try:
            samples, src = self._read_capture(name)
        except OSError as exc:
            raise gcmd.error("Cannot read the capture %s: %s" % (name, exc))
        binned = bin_samples(samples, self.bin_s)
        if len(binned) < count * 10:
            raise gcmd.error("Capture %s has only %d samples after binning"
                             % (name, len(binned)))
        write_stress_csv(self.csv_path, binned, time.time())
        out = self._run(material)
        logging.info("hi_pa_compute: %s %s -> %r", self.program, material,
                     out)
        m = RESULT_RE.search(out)
        if not m:
            raise gcmd.error("pa_compute gave no result: %s"
                             % (out.strip()[-300:],))
        value = float(m.group(1))
        rnd, pa = map_result(value, material_cfg, start, step, count)
        self.last = {"name": name, "material": material, "raw": value,
                     "round": rnd, "pa": pa, "samples": len(samples),
                     "bins": len(binned), "stamp": round(time.time(), 3)}
        gcmd.respond_info(
            "HI_PA_COMPUTE %s: %d samples -> %d bins (%s); pa_compute (%s) "
            "says %.4f = round %d of %d -> PA %.4f for this sweep"
            % (name, len(samples), len(binned), self.csv_path, material,
               value, rnd + 1, count, pa))
        if apply_pa:
            self.gcode.run_script_from_command(
                "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % (pa,))
            gcmd.respond_info("Pressure advance set to %.4f" % (pa,))

    def get_status(self, eventtime):
        return dict(self.last)


def load_config(config):
    return HiPACompute(config)
