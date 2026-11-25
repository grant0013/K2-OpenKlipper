#!/bin/sh
# Restore upstream K2 OpenKlipper with HarkScreen, retaining printer settings.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$HERE/uninstall.py" "$@"
