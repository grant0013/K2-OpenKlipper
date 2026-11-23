#!/usr/bin/env python3
"""Generate native touchscreen fonts and icons for desktop and printer builds.

Fonts: Liberation Sans (controls), Noto Sans (headings).
Dependencies: lv_font_conv 1.5.3, lucide-static 0.468.0, ImageMagick.
No proprietary editor or device font is required.
"""

import argparse
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--dependencies", type=Path, required=True)
a = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = root / "assets/generated"
out.mkdir(parents=True, exist_ok=True)
# Regeneration must not ship stale assets left by an older UI build.
for pattern in ("*.png", "*.svg", "font_*.c", "*.json"):
    for previous in out.glob(pattern):
        previous.unlink()

for family, sizes, source in (
    (
        "ui",
        (12, 13, 14, 15, 16, 17, 18, 20, 21, 22, 24, 25, 28, 30),
        root / "assets/LiberationSans-Regular.ttf",
    ),
    ("heading", (14, 24, 28), root / "assets/NotoSans-Regular.ttf"),
):
    for size in sizes:
        subprocess.run(
            [
                "node",
                str(a.dependencies / "node_modules/lv_font_conv/lv_font_conv.js"),
                "--font",
                str(source),
                "--size",
                str(size),
                "--bpp",
                "4",
                "--format",
                "lvgl",
                "--range",
                "0x20-0x17F,0x400-0x4FF",
                "--symbols",
                "°±−←→↑↓✓⌫•…×–·—›▾",
                "--no-compress",
                "--lv-font-name",
                "font_" + family + "_" + str(size),
                "-o",
                str(out / ("font_" + family + "_" + str(size) + ".c")),
            ],
            check=True,
        )
for old in out.glob("font_inter_*.c"):
    old.unlink()
icons = (
    "droplets",
    "house",
    "folder-open",
    "settings",
    "move",
    "thermometer",
    "fan",
    "gauge",
    "activity",
    "chevron-left",
    "chevron-right",
    "chevron-down",
    "arrow-up",
    "arrow-big-up",
    "arrow-down",
    "arrow-left",
    "arrow-right",
    "pause",
    "play",
    "square",
    "skip-object",
    "history",
    "lightbulb",
    "lightbulb-off",
    "disc-3",
    "pencil",
    "check",
    "x",
    "eye",
    "eye-off",
    "delete",
    "trash-2",
    "lock-keyhole",
    "lock-keyhole-open",
    "search",
    "wifi",
    "ethernet-port",
    "monitor",
    "video",
    "hard-drive",
    "refresh-cw",
    "power",
    "usb",
    "lock-keyhole",
    "lock-keyhole-open",
    "zoom-in",
    "zoom-out",
    "maximize",
    "triangle-alert",
    "info",
    "download",
    "rotate-ccw",
    "save",
    "sliders-horizontal",
    "loader-circle",
)
for icon in (*icons, "nozzle", "bed", "chamber", "cfs", "printer-3d", "extruder"):
    local = root / "assets" / (icon + ".svg")
    source = (
        local.read_text()
        if local.exists()
        else (
            a.dependencies / "node_modules/lucide-static/icons" / (icon + ".svg")
        ).read_text()
    )
    source = source.replace("currentColor", "#ffffff").replace(
        'stroke-width="2"', 'stroke-width="1.6"'
    )
    svg = out / (icon + ".svg")
    svg.write_text(source)
    for size in (16, 20, 22, 24, 28, 34, 36, 42, 44):
        subprocess.run(
            [
                "convert",
                "-density",
                "192",
                "-background",
                "none",
                str(svg),
                "-resize",
                f"{size}x{size}",
                "PNG32:" + str(out / (icon + f"-{size}.png")),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
shutil.copy2(
    a.dependencies / "node_modules/lucide-static/LICENSE",
    root / "assets/Lucide-LICENSE",
)
print(out)

# Match the 36px homing icon: 1.6 SVG units / 24 * 36 = 2.4 display pixels.
# Preserve the sidebar framing while compensating for its larger 56px size,
# and use the same rasterization pipeline as the homing icon.
for name in ("house", "printer-3d", "folder-open", "settings"):
    svg = ET.fromstring((out / (name + ".svg")).read_text())
    svg.set("stroke-width", str(2.4 * 22 / 56))
    svg.set("width", "56")
    svg.set("height", "56")
    svg.set("viewBox", "1 1 22 22")
    source = out / ("nav-" + name + ".svg")
    ET.ElementTree(svg).write(source, encoding="unicode")
    subprocess.run(
        [
            "convert",
            "-density",
            "192",
            "-background",
            "none",
            str(source),
            "-resize",
            "56x56",
            "PNG32:" + str(out / ("nav-" + name + "-56.png")),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
