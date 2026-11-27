#!/usr/bin/env python3
"""Package an external-host release with the matching K2 touchscreen runtime.

The ARM C helper is omitted so the host builds its own. The panel still runs on
K2 hardware, so its ARM runtime is compiled in CI and included in this archive.

Copyright (C) 2026 Hark Tech
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import io
import json
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSET_NAME = "k2-openklipper-host.zip"
SKIP = {"klippy/chelper/c_helper.so"}
NATIVE_PREFIX = "touchscreen/precompiled/armv7/"
sys.path.insert(0, str(ROOT / "touchscreen/installation"))
from validate_package import validate


def zip_entry(zf, name, data, mode):
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: make_host_zip.py <tag> <out.zip>")
    tag, out = sys.argv[1:]

    def revision(ref):
        return subprocess.check_output(
            ["git", "rev-parse", ref + "^{commit}"], cwd=ROOT, text=True
        ).strip()

    if revision(tag) != revision("HEAD"):
        sys.exit("Check out the selected tag before building its native release")
    package = ROOT / "touchscreen/precompiled/armv7"
    manifest = validate(ROOT / "touchscreen", package)
    tar_bytes = subprocess.check_output(
        ["git", "archive", "--format=tar", tag], cwd=ROOT
    )
    info = {
        "project_name": "K2-OpenKlipper",
        "project_owner": "grant0013",
        "version": tag,
        "asset_name": ASSET_NAME,
    }
    count = 0
    with (
        tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        for member in tf.getmembers():
            if (
                member.name in SKIP
                or member.name == "release_info.json"
                or member.name.startswith(NATIVE_PREFIX)
            ):
                continue
            if member.isdir():
                continue
            if not member.isfile():
                sys.exit("unsupported archive entry: " + member.name)
            zip_entry(zf, member.name, tf.extractfile(member).read(), member.mode)
            count += 1
        for name in sorted(list(manifest["files"]) + ["manifest.json"]):
            path = package / name
            zip_entry(zf, NATIVE_PREFIX + name, path.read_bytes(), path.stat().st_mode)
            count += 1
        zip_entry(
            zf, "release_info.json", (json.dumps(info, indent=4) + "\n").encode(), 0o644
        )
    print(
        "%s: %d files from %s, with compiled native touchscreen" % (out, count + 1, tag)
    )


if __name__ == "__main__":
    main()
