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
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", SET=1)
        p.cmd_HI_PA_COMPUTE(g)
        self.assertEqual(p.calls, ["Hyper PETG"])
        self.assertAlmostEqual(p.last["pa"], 0.038)
        self.assertEqual(p.gcode.scripts,
                         ["SET_PRESSURE_ADVANCE ADVANCE=0.0380"])
        self.assertTrue(os.path.exists(p.csv_path))
        os.unlink(p.csv_path)

    def test_minus_one_is_a_failure_not_a_pa(self):
        p = make("-1")
        g = FakeGcmd(NAME="hi_pa_sweep", MATERIAL="PETG", SET=1)
        with self.assertRaises(GcodeError) as cm:
            p.cmd_HI_PA_COMPUTE(g)
        self.assertIn("found no pressure advance", str(cm.exception))
        self.assertEqual(p.gcode.scripts, [])
        self.assertEqual(p.last, {})
        os.unlink(p.csv_path)


if __name__ == "__main__":
    unittest.main()
