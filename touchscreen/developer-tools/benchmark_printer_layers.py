#!/usr/bin/env python3
"""Compare static and layered native builds with identical offline fixtures.

CPU times cover a complete headless process (startup, updates and final draw).
They are desktop comparisons, not printer responsiveness measurements.
"""
import argparse
import copy
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from demo_snapshot import preview_case

ROOT = Path(__file__).resolve().parents[1]


def run_cpu(binary, fixture, actions, output):
    command = [str(binary), '--assets', str(ROOT/'assets/generated'), '--fixture', str(fixture),
               '--page', 'home', '--actions', str(actions), '--snapshot', str(output)]
    start = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.execv(command[0], command)
    _, status, usage = os.wait4(pid, 0)
    if status:
        raise RuntimeError('Preview failed: '+str(status))
    return {'cpu_ms': 1000*(usage.ru_utime+usage.ru_stime),
            'wall_ms': 1000*(time.perf_counter()-start)}


def proc_sample(pid):
    stat = Path('/proc/%d/stat' % pid).read_text().split()
    cpu = (int(stat[13])+int(stat[14]))/os.sysconf('SC_CLK_TCK')
    memory = {}
    for line in Path('/proc/%d/smaps_rollup' % pid).read_text().splitlines():
        if line.startswith(('Rss:', 'Pss:', 'Private_Dirty:')):
            memory[line.split(':')[0]] = int(line.split()[1])
    return cpu, memory


def idle(binary, fixture, seconds):
    process = subprocess.Popen([str(binary), '--headless', '--assets', str(ROOT/'assets/generated'),
                                '--fixture', str(fixture), '--page', 'home'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(.5)
        before, _ = proc_sample(process.pid)
        start = time.monotonic()
        time.sleep(seconds)
        after, memory = proc_sample(process.pid)
        return {'cpu_percent_one_core': 100*(after-before)/(time.monotonic()-start), **memory}
    finally:
        process.terminate()
        process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, default=ROOT/'out/host/openklipper-screen')
    parser.add_argument('--trials', type=int, default=7)
    parser.add_argument('--steps', type=int, default=400)
    parser.add_argument('--idle-seconds', type=float, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binaries = {'static': args.baseline.resolve(), 'layered': args.candidate.resolve()}
    report = {'scope': 'Local headless 800x480 LVGL; identical fixture and normalized artwork; CPU is process CPU time.',
              'steps': args.steps, 'trials': args.trials, 'workloads': {}, 'idle': {}}
    with tempfile.TemporaryDirectory(prefix='spool-benchmark-') as directory:
        directory = Path(directory)
        data = preview_case('idle')
        fixture = directory/'idle.json'
        fixture.write_text(json.dumps(data))
        for workload in ('telemetry', 'colour_updates', 'navigation'):
            steps = []
            for n in range(args.steps):
                if workload == 'navigation':
                    steps.append('route:filament' if n%2 == 0 else 'route:home')
                    continue
                changes = {'heaters': copy.deepcopy(data['heaters'])}
                changes['heaters'][0]['current'] = 29+n%2
                if workload == 'colour_updates':
                    changes['slots'] = copy.deepcopy(data['slots'])
                    changes['slots'][0]['color'] = '00FFFF' if n%2 else 'FF6A00'
                    changes['status'] = copy.deepcopy(data['status'])
                    changes['status']['hark_cfs_slots']['revision'] = n+1
                steps.append({'snapshot': {'_screen_patch': True, 'session': data['session'],
                                           'set': changes, 'remove': []}})
            actions = directory/'actions.json'
            actions.write_text(json.dumps(steps))
            samples = {key: [] for key in binaries}
            # Alternate order to avoid consistently warming only one build.
            for trial in range(args.trials):
                order = list(binaries) if trial%2 == 0 else list(reversed(binaries))
                for key in order:
                    samples[key].append(run_cpu(binaries[key], fixture, actions, directory/'screen.ppm'))
            report['workloads'][workload] = {
                key: {'median_'+metric: statistics.median(s[metric] for s in values)
                      for metric in values[0]} | {'samples': values}
                for key, values in samples.items()}
        for key, binary in binaries.items():
            samples = [idle(binary, fixture, args.idle_seconds) for _ in range(3)]
            report['idle'][key] = {metric: statistics.median(s[metric] for s in samples)
                                   for metric in samples[0]} | {'samples': samples}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(args.output)


if __name__ == '__main__':
    main()
