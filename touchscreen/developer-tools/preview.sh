#!/bin/sh
# Run the actual native UI against read-only demo data. No printer IPC.
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
usage() {
    echo "Usage: $0 SNAPSHOT.json [--page PAGE]"
    echo "       $0 --scenario NAME [--page PAGE]"
    echo "Scenarios: idle heated heating empty-extruder printing error loading homing meshing load unload notice recovery disconnected"
}
if [ "$#" -eq 0 ]; then usage; exit 2; fi
if [ "$1" = "--help" ]; then usage; exit 0; fi
if [ ! -x "$SRC/out/host/openklipper-screen" ]; then
    echo "Build the PC target first: $SRC/developer-tools/build.sh host" >&2
    exit 1
fi
if [ "$1" = "--scenario" ]; then
    [ "$#" -ge 2 ] || { usage; exit 2; }
    DEMO=$(mktemp /tmp/openklipper-preview-XXXXXX.json)
    trap 'rm -f "$DEMO"' EXIT HUP INT TERM
    python3 "$SRC/developer-tools/demo_snapshot.py" "$DEMO" --scenario "$2"
    shift 2
else
    DEMO=$1
    shift
fi
PAGE=home
if [ "$#" -ne 0 ]; then
    [ "$#" -eq 2 ] && [ "$1" = "--page" ] || { usage; exit 2; }
    PAGE=$2
fi
if [ ! -r "$DEMO" ]; then
    echo "Cannot read snapshot: $DEMO" >&2
    exit 1
fi
"$SRC/out/host/openklipper-screen" --assets "$SRC/assets/generated" --fixture "$DEMO" --page "$PAGE"
