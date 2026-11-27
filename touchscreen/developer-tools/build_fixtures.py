#!/usr/bin/env python3
"""Refresh preview copies of production artwork and rasterize SVG fixtures."""

import sys
import tempfile
import shutil
import subprocess
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "fixtures/assets"


def render(source, destination):
    subprocess.run(
        [
            "convert",
            "-background",
            "none",
            str(source),
            "-trim",
            "+repage",
            "-strip",
            "PNG32:" + str(destination),
        ],
        check=True,
    )


def main():
    from build_printer_layers import build
    build()
    render(ASSETS / "demo-part.svg", ASSETS / "demo-part.png")
    render(ASSETS / "demo-part-dark.svg", ASSETS / "demo-part-dark.png")
    # Use the same cached 400x286 canvas as the service. Tint masks and the
    # base image must share a coordinate system in desktop and printer builds.
    for source, target in (("printer-with-cfs.png", "demo-printer-combo.png"),
                           ("printer-without-cfs.png", "demo-printer-only.png")):
        sys.path.insert(0, str(ASSETS.parents[2] / "backend"))
        from storage import prepare_image
        with tempfile.TemporaryDirectory(prefix="printer-fixture-") as directory:
            if source == "printer-with-cfs.png":
                prepared = ASSETS.parents[2] / "assets/generated/printer-with-cfs-base.png"
            else:
                prepared = prepare_image(ASSETS.parents[2] / "assets" / source,
                                         directory, "printer", (400, 286), lighten=True)
            shutil.copyfile(prepared, ASSETS / target)


if __name__ == "__main__":
    main()
