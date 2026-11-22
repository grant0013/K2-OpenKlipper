#!/usr/bin/env python3
"""Bundle a complete committed OpenKlipper version with its compiled native UI."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "K2-OpenKlipper-native"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument(
        "--package", type=Path, default=ROOT / "touchscreen/package-arm"
    )
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", args.ref], text=True
    ).strip()
    manifest = json.loads((args.package / "manifest.json").read_text())
    if manifest["source_commit"] != revision:
        raise SystemExit("Compiled package and source commit do not match")
    for name, expected in manifest["files"].items():
        source = args.package / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise SystemExit("Package checksum mismatch: " + name)
    archive = subprocess.check_output(
        ["git", "-C", str(ROOT), "archive", "--format=tar", revision]
    )
    checksums = {}
    with (
        tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source,
        tarfile.open(args.output, "w:gz") as target,
    ):
        for member in source:
            if member.name.startswith(("harkscreen/", "tools/bootanim/")):
                raise SystemExit("Superseded touchscreen code is still present")
            content = source.extractfile(member).read() if member.isfile() else None
            relative = member.name
            member.name = PREFIX + "/" + relative
            target.addfile(member, io.BytesIO(content) if content is not None else None)
            if content is not None:
                checksums[relative] = hashlib.sha256(content).hexdigest()
        for path in sorted(args.package.rglob("*")):
            if not path.is_file():
                continue
            relative = "touchscreen/package-arm/" + str(path.relative_to(args.package))
            target.add(path, arcname=PREFIX + "/" + relative, recursive=False)
            checksums[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        data = (revision + "\n").encode()
        member = tarfile.TarInfo(PREFIX + "/SOURCE_REVISION")
        member.size = len(data)
        member.mode = 0o644
        target.addfile(member, io.BytesIO(data))
        checksums["SOURCE_REVISION"] = hashlib.sha256(data).hexdigest()
        data = "".join(
            value + "  " + name + "\n" for name, value in sorted(checksums.items())
        ).encode()
        member = tarfile.TarInfo(PREFIX + "/SHA256SUMS")
        member.size = len(data)
        member.mode = 0o644
        target.addfile(member, io.BytesIO(data))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name + ".sha256").write_text(
        digest + "  " + args.output.name + "\n"
    )
    print(
        json.dumps(
            {
                "archive": str(args.output),
                "source_commit": revision,
                "sha256": digest,
                "files": len(checksums),
            }
        )
    )


if __name__ == "__main__":
    main()
