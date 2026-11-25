#!/usr/bin/env python3
"""Launch the native process using installation-specific panel settings."""

import argparse
import configparser
import os
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--config", required=True)
p.add_argument("--root", required=True)
a = p.parse_args()
c = configparser.ConfigParser()
c.read(a.config)
args = [
    "nice",
    "-n",
    "10",
    str(Path(a.root) / "openklipper-screen"),
    "--framebuffer",
    "--input",
    c.get("screen", "touch_device", fallback="/dev/input/event0"),
    "--rotate",
    c.get("screen", "rotate", fallback="90"),
    "--assets",
    str(Path(a.root) / "assets"),
]
for key in ("touch_max_x", "touch_max_y"):
    args += [
        "--" + key.replace("_", "-"),
        c.get("screen", key, fallback="480" if key.endswith("x") else "800"),
    ]
for key in ("touch_swap_xy", "touch_invert_x", "touch_invert_y"):
    if c.getboolean("screen", key, fallback=False):
        args.append("--" + key.replace("_", "-"))
Path("/tmp/openklipper-screen.pid").write_text(str(os.getpid()))
log = os.open(
    "/tmp/openklipper-screen.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
)
os.dup2(log, 1)
os.dup2(log, 2)
os.close(log)
os.execvp(args[0], args)
