#!/usr/bin/env python3
"""Rasterize the bundled, project-authored SVG preview artwork."""

import subprocess
import tempfile
import xml.etree.ElementTree as ET
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
    render(ASSETS / "demo-part.svg", ASSETS / "demo-part.png")
    render(ASSETS / "demo-part-dark.svg", ASSETS / "demo-part-dark.png")
    render(ASSETS.parents[2] / "assets/printer.svg", ASSETS / "demo-printer-combo.png")
    render(
        ASSETS.parents[2] / "assets/printer.svg",
        ASSETS.parents[2] / "assets/printer-with-cfs.png",
    )
    tree = ET.parse(ASSETS.parents[2] / "assets/printer.svg")
    root = tree.getroot()
    root.remove(next(child for child in root if child.get("id") == "cfs"))
    with tempfile.TemporaryDirectory(prefix="native-fixture-art-") as directory:
        source = Path(directory) / "printer.svg"
        tree.write(source, encoding="unicode")
        render(source, ASSETS / "demo-printer-only.png")
        render(source, ASSETS.parents[2] / "assets/printer-without-cfs.png")


if __name__ == "__main__":
    main()
