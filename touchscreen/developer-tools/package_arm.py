#!/usr/bin/env python3
"""Assemble a versioned ARM runtime package from an already completed build."""

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=ROOT / "build-arm")
    args = parser.parse_args()
    binary = args.build / "openklipper-screen"
    header = binary.read_bytes()[:20]
    if (
        header[:6] != b"\x7fELF\x01\x01"
        or struct.unpack_from("<H", header, 18)[0] != 40
    ):
        raise SystemExit("Expected a 32-bit little-endian ARM executable")
    revision = subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
    ).strip()
    if subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", "HEAD"]).returncode:
        raise SystemExit("Commit tracked source changes before packaging")
    package = ROOT / "package-arm"
    if package.exists():
        shutil.rmtree(package)
    (package / "assets").mkdir(parents=True)
    (package / "licenses").mkdir()
    shutil.copy2(binary, package / binary.name)
    (package / binary.name).chmod(0o755)
    for source in (ROOT / "assets/generated").glob("*.png"):
        shutil.copy2(source, package / "assets" / source.name)
    for source in (ROOT / "assets").glob("*-LICENSE"):
        shutil.copy2(source, package / "licenses" / source.name)
    for source in (args.build / "licenses").glob("*"):
        shutil.copy2(source, package / "licenses" / source.name)
    if (
        not (package / "licenses/LVGL-LICENSE").exists()
        or not (package / "licenses/cJSON-LICENSE").exists()
    ):
        raise SystemExit(
            "Dependency licences missing; reconfigure CMake before packaging"
        )
    (package / "VERSION").write_text(revision + "\n")
    manifest = {
        "source_commit": revision,
        "target": "armv7-linux-gnueabihf",
        "files": {},
    }
    for source in sorted(package.rglob("*")):
        if source.is_file():
            manifest["files"][str(source.relative_to(package))] = hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
    (package / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(package)


if __name__ == "__main__":
    main()
