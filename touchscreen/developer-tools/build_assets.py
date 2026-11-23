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
        (12, 13, 14, 15, 16, 17, 18, 20, 24, 25, 30),
        root / "assets/LiberationSans-Regular.ttf",
    ),
    ("heading", (28,), root / "assets/NotoSans-Regular.ttf"),
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
# Only sizes used by the current layouts are shipped. Keep this inventory in
# sync with icon(), tab, keyboard and button calls when changing the UI.
icons = {
    "droplets": (24,),
    "house": (36,),
    "folder-open": (20, 24),
    "settings": (24,),
    "move": (24,),
    "thermometer": (24,),
    "fan": (24, 28),
    "gauge": (24,),
    "activity": (24,),
    "chevron-right": (16, 28),
    "chevron-down": (20,),
    "arrow-up": (36,),
    "arrow-big-up": (28,),
    "arrow-down": (36,),
    "arrow-left": (28, 36),
    "arrow-right": (16, 24, 36),
    "pause": (28,),
    "play": (28,),
    "square": (28,),
    "skip-object": (28,),
    "history": (28,),
    "lightbulb": (28,),
    "lightbulb-off": (28,),
    "disc-3": (24,),
    "pencil": (16, 24),
    "check": (24, 28),
    "eye": (28,),
    "eye-off": (28,),
    "delete": (28,),
    "trash-2": (28,),
    "lock-keyhole": (24,),
    "lock-keyhole-open": (24,),
    "wifi": (24,),
    "monitor": (24,),
    "video": (24,),
    "hard-drive": (24,),
    "refresh-cw": (28,),
    "zoom-in": (28,),
    "zoom-out": (28,),
    "maximize": (28,),
    "save": (28,),
    "sliders-horizontal": (28,),
    "nozzle": (16, 24, 28),
    "bed": (28,),
    "chamber": (28,),
    "cfs": (24, 28),
    "printer-3d": (44,),
    "extruder": (36,),
}
for icon, sizes in icons.items():
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
    for size in sizes:
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
