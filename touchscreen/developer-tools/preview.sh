#!/bin/sh
# Run the native UI with synthetic or recorded fixture data; no printer IPC.
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
usage() {
    echo "Usage: $0 --case NAME [--page PAGE] [--snapshot FILE.ppm]"
    echo "       $0 --scenario NAME [--page PAGE] [--snapshot FILE.ppm]"
    echo "       $0 SNAPSHOT.json [--page PAGE] [--snapshot FILE.ppm]"
    echo "       $0 --list"
}
MODE=
VALUE=
PAGE=
OUTPUT=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help) usage; exit 0 ;;
        --list) python3 "$SRC/developer-tools/demo_snapshot.py" --list; exit 0 ;;
        --case|--scenario)
            [ "$#" -ge 2 ] && [ -z "$MODE" ] || { usage >&2; exit 2; }
            MODE=$1; VALUE=$2; shift 2 ;;
        --page|--snapshot)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            if [ "$1" = "--page" ]; then PAGE=$2; else OUTPUT=$2; fi
            shift 2 ;;
        --*) usage >&2; exit 2 ;;
        *)
            [ -z "$MODE" ] || { usage >&2; exit 2; }
            MODE=file; VALUE=$1; shift ;;
    esac
done
[ -n "$MODE" ] || { usage >&2; exit 2; }
if [ ! -x "$SRC/out/host/openklipper-screen" ]; then
    echo "Build the PC target first: sh $SRC/developer-tools/build.sh host" >&2
    exit 1
fi
WORK=$(mktemp -d /tmp/openklipper-preview-XXXXXX)
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
if [ "$MODE" = file ]; then
    DEMO=$VALUE
else
    DEMO=$WORK/snapshot.json
    python3 "$SRC/developer-tools/demo_snapshot.py" "$DEMO" "$MODE" "$VALUE"
fi
[ -r "$DEMO" ] || { echo "Cannot read snapshot: $DEMO" >&2; exit 1; }
DEFAULT_PAGE=$(python3 - "$DEMO" "$WORK/actions.json" <<'PY'
import json
import sys
from pathlib import Path
snapshot = json.loads(Path(sys.argv[1]).read_text())
preview = snapshot.get("preview", {})
Path(sys.argv[2]).write_text(json.dumps(preview.get("actions", [])))
print(preview.get("page", "home"))
PY
)
PAGE=${PAGE:-$DEFAULT_PAGE}
set -- "$SRC/out/host/openklipper-screen" --assets "$SRC/assets/generated" \
    --fixture "$DEMO" --page "$PAGE" --actions "$WORK/actions.json"
if [ -n "$OUTPUT" ]; then
    mkdir -p -- "$(dirname -- "$OUTPUT")"
    set -- "$@" --snapshot "$OUTPUT"
fi
"$@"
