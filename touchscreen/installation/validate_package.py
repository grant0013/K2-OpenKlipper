#!/usr/bin/env python3
"""Check the shipped ARM package against its source inputs before installation."""

import argparse
import hashlib
import json
import os
import struct
from pathlib import Path


def source_inputs(source, integration_root=None):
    source = Path(source)
    paths = []
    for folder in ("src", "backend", "runtime", "build", "installation", "assets"):
        paths.extend(
            p
            for p in (source / folder).rglob("*")
            if p.is_file()
            and not any(
                part in ("__pycache__", "generated")
                for part in p.relative_to(source).parts
            )
        )
    paths += [
        source / "developer-tools" / name
        for name in (
            "build.sh",
            "build_assets.py",
            "build_printer_layers.py",
            "package_arm.py",
            "package.json",
            "package-lock.json",
        )
    ]
    paths += [source / "LICENSE"]
    integration = Path(integration_root) if integration_root else source.parent
    return {
        "touchscreen/" + p.relative_to(source).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(paths)
    } | {name: hashlib.sha256((integration / name).read_bytes()).hexdigest()
         for name in ("VERSION", "install.sh")}


def fingerprint(inputs):
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate(source, package, integration_root=None):
    source, package = Path(source), Path(package)
    manifest = json.loads((package / "manifest.json").read_text())
    if manifest.get("format") != 1 or manifest.get("target") != "armv7-linux-gnueabihf":
        raise ValueError("Unsupported native touchscreen package")
    inputs = source_inputs(source, integration_root)
    if manifest.get("source_inputs") != inputs or manifest.get(
        "source_fingerprint"
    ) != fingerprint(inputs):
        raise ValueError(
            "Precompiled touchscreen needs rebuilding for these source inputs"
        )
    files = manifest.get("files", {})
    required = {
        "openklipper-screen",
        "VERSION",
        "assets/nav-house-56.png",
        "assets/printer-with-cfs-base.png",
        "assets/printer-spool-1.png",
        "assets/printer-spool-2.png",
        "assets/printer-spool-3.png",
        "assets/printer-spool-4.png",
        "licenses/Touchscreen-LICENSE",
        "licenses/LVGL-LICENSE",
        "licenses/cJSON-LICENSE",
        "licenses/Liberation-LICENSE",
        "licenses/Noto-LICENSE",
        "licenses/Lucide-LICENSE",
    }
    if not required <= files.keys():
        raise ValueError("Incomplete touchscreen runtime package")
    for name, expected in files.items():
        path = package / name
        if (
            not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or package.resolve() not in path.resolve().parents
        ):
            raise ValueError("Invalid package path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Package checksum mismatch: " + name)
    if not os.access(package / "openklipper-screen", os.X_OK):
        raise ValueError("Native touchscreen binary is not executable")
    header = (package / "openklipper-screen").read_bytes()[:20]
    if (
        len(header) < 20
        or header[:6] != b"\x7fELF\x01\x01"
        or struct.unpack_from("<H", header, 18)[0] != 40
    ):
        raise ValueError("Expected a little-endian 32-bit ARM executable")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--integration-root", type=Path, help="K2-OpenKlipper checkout for standalone packaging validation")
    args = parser.parse_args()
    try:
        manifest = validate(args.source, args.package, args.integration_root)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "Native touchscreen package invalid: " + str(error) + "\n")
    print("Precompiled touchscreen verified: " + manifest["source_fingerprint"][:12])


if __name__ == "__main__":
    main()
