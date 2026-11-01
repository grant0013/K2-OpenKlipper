#!/usr/bin/env python3
# Build k2-openklipper-host.zip for a release tag: the release for an
# external Klipper host (docs/EXTERNAL-HOST.md), installed and updated by
# Moonraker's Update Manager ([update_manager k2-openklipper], type: zip).
#
#   python3 tools/release/make_host_zip.py v0.4.3-alpha.1 k2-openklipper-host.zip
#
# The zip is `git archive <tag>` without the K2's ARM C helper (Klipper builds
# its own on the host's first start), plus release_info.json naming the
# project and version for Moonraker. The release workflow attaches it to every
# published release (.github/workflows/host-release-asset.yml).
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import io, json, stat, subprocess, sys, tarfile, zipfile

ASSET_NAME = "k2-openklipper-host.zip"
# Built for the K2's ARM processor; a host builds its own
SKIP = {"klippy/chelper/c_helper.so"}


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: make_host_zip.py <tag> <out.zip>")
    tag, out = sys.argv[1:]
    tar_bytes = subprocess.run(["git", "archive", "--format=tar", tag],
                               check=True, capture_output=True).stdout
    info = {"project_name": "K2-OpenKlipper", "project_owner": "grant0013",
            "version": tag, "asset_name": ASSET_NAME}
    count = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in tf.getmembers():
            if member.name in SKIP or member.name == "release_info.json":
                continue
            if member.isdir():
                continue
            if not member.isfile():
                sys.exit("unsupported archive entry (symlink?): %s"
                         % member.name)
            zi = zipfile.ZipInfo(member.name)
            # Moonraker sets each extracted file's mode from these bits
            zi.external_attr = (stat.S_IFREG | (member.mode & 0o777)) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, tf.extractfile(member).read())
            count += 1
        zi = zipfile.ZipInfo("release_info.json")
        zi.external_attr = (stat.S_IFREG | 0o644) << 16
        zf.writestr(zi, json.dumps(info, indent=4) + "\n")
    print("%s: %d files from %s" % (out, count + 1, tag))


if __name__ == "__main__":
    main()
