"""hi_pa_compute: Creality's pa_compute on the Hi, locally or over SSH from
an external host (USB host-link)."""
import os
import pathlib
import shlex
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import hi_pa_compute as hpc  # noqa: E402

PETG = {"gaussSigma": 2.0, "paStrat": 0.034, "paStep": 0.002,
        "paPeriodicity": 31}


class GcodeError(Exception):
    pass


class FakeGcmd:
    def __init__(self, **params):
        self.params = params
        self.replies = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.replies.append(msg)

    def error(self, msg):
        return GcodeError(msg)


def make(answer, remote="root@hi"):
    p = hpc.HiPACompute.__new__(hpc.HiPACompute)
    p.gcode = types.SimpleNamespace(error=GcodeError, scripts=[])
    p.gcode.run_script_from_command = p.gcode.scripts.append
    p.program, p.config_path = hpc.PA_COMPUTE, hpc.PA_CONFIG
    p.csv_path = tempfile.mktemp(suffix=".csv")
    p.bin_s, p.remote, p.ssh_key = 0.1, remote, "~/.ssh/id_hi_hostlink"
    p.remote_csv, p.last = hpc.STRESS_CSV, {}
    # 31 rounds x ~3.3 s of 1 kHz-ish samples
    samples = [(i * 0.01, 738800 + (i % 50)) for i in range(31 * 330)]
    p._read_capture = lambda name: (samples, "capture.csv")
    p._material_cfg = lambda material: PETG
    p.calls = []
    p.printer = types.SimpleNamespace(command_error=GcodeError)

    def run(material):
        p.calls.append(material)
        return "End Position = %s\nversion = 20250811\n" % (answer,)
    p._run = run
    return p


class HelpersTest(unittest.TestCase):
    def test_ssh_argv_is_batch_mode_with_key(self):
        argv = hpc.ssh_argv("root@192.168.3.96", "true", "~/.ssh/k")
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[argv.index("-i") + 1],
                         os.path.expanduser("~/.ssh/k"))
        self.assertEqual(argv[-2:], ["root@192.168.3.96", "true"])

    def test_remote_command_quotes_material_with_space(self):
        cmd = hpc.remote_compute_command(hpc.PA_COMPUTE, hpc.PA_CONFIG,
                                         "Hyper PETG", hpc.STRESS_CSV)
        words = shlex.split(cmd)
        self.assertEqual(words[:3], ["cat", ">", hpc.STRESS_CSV])
        self.assertIn("--material_name=Hyper PETG", words)
        self.assertIn("--path=" + hpc.PA_CONFIG, words)

    def test_map_result_uses_our_sweep(self):
        # pa_compute's PETG answer 0.062 = round 14 -> 0.010 + 14 x 0.002
        self.assertEqual(hpc.map_result(0.062, PETG, 0.010, 0.002, 31),
                         (14, 0.010 + 14 * 0.002))


class CommandTest(unittest.TestCase):
    def test_result_applied_when_set(self):
        p = make("0.062")
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", SET=1,
                     METHOD="pa_compute")
        p.cmd_HI_PA_COMPUTE(g)
        self.assertEqual(p.calls, ["Hyper PETG"])
        self.assertAlmostEqual(p.last["pa"], 0.038)
        self.assertEqual(p.gcode.scripts,
                         ["SET_PRESSURE_ADVANCE ADVANCE=0.0380"])
        self.assertTrue(os.path.exists(p.csv_path))
        os.unlink(p.csv_path)

    def test_minus_one_is_a_failure_not_a_pa(self):
        p = make("-1")
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", SET=1,
                     METHOD="pa_compute")
        with self.assertRaises(GcodeError) as cm:
            p.cmd_HI_PA_COMPUTE(g)
        self.assertIn("found no pressure advance", str(cm.exception))
        self.assertEqual(p.gcode.scripts, [])
        self.assertIsNone(p.last["pa"])
        os.unlink(p.csv_path)


# --- the knee: where the fast stroke's force peaks ------------------------
STROKE = 40. / 150.          # F9000 fast stroke, s
V_FAST = 1.853 / STROKE      # its filament speed, mm/s


def sweep(knee_round, rounds=31, sign=1., ragged=()):
    """A synthetic sweep: per round a slow ~1.5 s stretch, then the fast
    stroke whose force keeps rising to its end before knee_round and
    overshoots early from it on. Returns (samples, moves)."""
    samples, moves, t = [], [], 0.
    for r in range(rounds):
        moves.append((t + 0.1, 0.1, 0., 6.9 / 0.1 * 0.1))   # slow ramp-up
        for i in range(150):                                 # slow stretch
            samples.append((t + i * 0.01, int(sign * 1000)))
        t += 1.5
        moves.append((t, 0.05, 0., V_FAST / 0.05))           # fast accel
        moves.append((t - 0.4, 0.01, 40., -4000.))           # unretract
        early = r >= knee_round
        if r in ragged:
            early = not early
        for i in range(int(STROKE / 0.005)):
            x = i * 0.005 / STROKE
            f = (5000 * (1.3 - x) if early and x > 0.15 else
                 5000 * x / 0.15 * 1.3 if early else 5000 * x)
            samples.append((t + i * 0.005, int(sign * (1000 + f))))
        t += STROKE + 1.0
    return samples, [(a, b, c, d) for a, b, c, d in moves]


class KneeTest(unittest.TestCase):
    def knee(self, samples, moves, start=0.010, step=0.002, rounds=31):
        starts = hpc.fast_strokes(moves, V_FAST)
        self.assertEqual(len(starts), rounds)
        pos = hpc.peak_positions(sorted(samples), starts, STROKE)
        return hpc.knee_result(pos, start, step)

    def test_clean_flip(self):
        r = self.knee(*sweep(20))
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["pa"], 0.010 + 20 * 0.002)

    def test_gauge_reading_the_other_way(self):
        r = self.knee(*sweep(12, sign=-1.))
        self.assertAlmostEqual(r["pa"], 0.010 + 12 * 0.002)

    def test_ragged_flip_lands_mid_way(self):
        # late/early mixed over rounds 14-17 (as shifted sweep 2, 18 Nov)
        r = self.knee(*sweep(15, ragged=(15, 17)))
        self.assertEqual(r["status"], "ok")
        self.assertTrue(0.010 + 14 * 0.002 <= r["pa"] <= 0.010 + 18 * 0.002)

    def test_knee_past_either_end(self):
        self.assertEqual(self.knee(*sweep(40))["status"], "high")
        self.assertEqual(self.knee(*sweep(0))["status"], "low")

    def test_no_clear_flip(self):
        positions = [0.9, 0.1] * 15 + [0.9]
        self.assertEqual(hpc.knee_result(positions, 0.01, 0.002)["status"],
                         "unclear")

    def test_fast_strokes_skip_retracts_and_slow_strokes(self):
        moves = [(0., 0.05, 0., V_FAST / 0.05), (1., 0.01, 0., 4000.),
                 (2., 0.1, 0., 6.9), (3., 0.05, 0., V_FAST / 0.05)]
        self.assertEqual(hpc.fast_strokes(moves, V_FAST), [0., 3.])


class KneeCommandTest(unittest.TestCase):
    def make(self, knee_round, answer="-1"):
        p = make(answer)
        samples, moves = sweep(knee_round)
        p._read_capture = lambda name: (samples, "capture.csv")
        p._read_moves = lambda name: moves
        return p

    def test_knee_is_the_default(self):
        p = self.make(20)
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", SET=1)
        p.cmd_HI_PA_COMPUTE(g)
        self.assertAlmostEqual(p.last["pa"], 0.050)
        self.assertEqual(p.calls, [])            # no pa_compute run
        self.assertEqual(p.gcode.scripts,
                         ["SET_PRESSURE_ADVANCE ADVANCE=0.0500"])

    def test_cross_check_failing_does_not_fail_the_knee(self):
        p = self.make(20, answer="-1")
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", CROSS=1)
        p.cmd_HI_PA_COMPUTE(g)
        self.assertAlmostEqual(p.last["pa"], 0.050)
        self.assertIsNone(p.last["pa_compute"])
        self.assertIn("pa_compute none", g.replies[-1])
        if os.path.exists(p.csv_path):
            os.unlink(p.csv_path)

    def test_quiet_records_a_miss_instead_of_raising(self):
        p = self.make(40)
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", QUIET=1)
        p.cmd_HI_PA_COMPUTE(g)
        self.assertIsNone(p.last["pa"])
        self.assertEqual(p.last["knee_status"], "high")
        self.assertIn("start higher", g.replies[-1])

    def test_a_miss_raises_without_quiet(self):
        p = self.make(0)
        with self.assertRaises(GcodeError) as cm:
            p.cmd_HI_PA_COMPUTE(FakeGcmd(NAME="hi_pa_sweep"))
        self.assertIn("start lower", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
