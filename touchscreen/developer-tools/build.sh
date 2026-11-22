#!/bin/sh
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=${1:-host}
case "$TARGET" in host|arm) ;; *) echo "Usage: $0 host|arm" >&2; exit 2 ;; esac
npm ci --ignore-scripts --prefix "$SRC/developer-tools"
python3 "$SRC/developer-tools/build_assets.py" --dependencies "$SRC/developer-tools"
if [ "$TARGET" = arm ]; then
    cmake -S "$SRC" -B "$SRC/build-arm" -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=cmake/arm-linux.cmake
else
    cmake -S "$SRC" -B "$SRC/build-host" -DCMAKE_BUILD_TYPE=Release
fi
cmake --build "$SRC/build-$TARGET" --parallel "${JOBS:-4}"
if [ "$TARGET" = arm ]; then
    python3 "$SRC/developer-tools/package_arm.py" --build "$SRC/build-arm"
fi
