#!/bin/sh
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CONF=${1:-/mnt/UDISK/printer_data_mainline/config}
PROFILE=${2:-}
ROOT=${OKL_SCREEN_ROOT:-/mnt/UDISK/openklipper-screen}
PACKAGE=${OKL_SCREEN_PACKAGE:-$SRC/precompiled/armv7}
if [ ! -x "$PACKAGE/openklipper-screen" ] || [ ! -d "$PACKAGE/assets" ]; then
    echo "Precompiled native touchscreen package missing from this source version." >&2
    exit 1
fi
python3 "$SRC/installation/validate_package.py" --source "$SRC" --package "$PACKAGE"
STAGE=$ROOT.new
[ ! -d "$STAGE" ] || rm -rf "$STAGE"
mkdir -p "$STAGE" "$CONF"
cp -a "$PACKAGE/." "$STAGE/"
cp -a "$SRC/backend" "$STAGE/backend"
cp "$SRC/runtime/start.sh" "$SRC/runtime/launch.py" "$STAGE/"
cp "$SRC/installation/uninstall.sh" "$SRC/installation/uninstall.py" "$STAGE/"
chmod 755 "$STAGE/openklipper-screen" "$STAGE/start.sh" "$STAGE/uninstall.sh"
# Keep the previous complete installation until the new one is verified.
if [ -d "$ROOT" ]; then
    PREVIOUS=$ROOT.previous.$(date +%Y%m%d%H%M%S)
    mv "$ROOT" "$PREVIOUS"
fi
mv "$STAGE" "$ROOT"
case "$PROFILE" in F021) MODEL="Creality K2" ;; F008) MODEL="Creality K2 Plus" ;; F012) MODEL="Creality K2 Pro" ;; *) MODEL="$PROFILE" ;; esac
python3 "$SRC/installation/migrate.py" --config "$CONF/native-screen.cfg" \
    --example "$SRC/runtime/native-screen.cfg.example" --legacy "$CONF/harkscreen.cfg" --model "$MODEL"

# Installation does not restart Klipper or perform printer operations.
echo "Native touchscreen installed. Start with: sh $ROOT/start.sh $CONF/native-screen.cfg"
