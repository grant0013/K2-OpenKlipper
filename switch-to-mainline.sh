#!/bin/sh
# Switch K2 from stock Creality Klipper to the mainline instance.
# Safe by design: refuses while printing; stock stack untouched on disk.
# Usage: switch-to-mainline.sh [config-file]
#   default config: mainline_printer.cfg (stage 1, sensors only)
#   stage 2 (full corexy): switch-to-mainline.sh mainline_stage2.cfg
MAINLINE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PDATA=/mnt/UDISK/printer_data_mainline
CFG_SOURCE="$MAINLINE/config/${1:-mainline_printer.cfg}"
[ -f "$CFG_SOURCE" ] || { echo "config not found: $CFG_SOURCE"; exit 1; }

STATE=$(python3 -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:7126/printer/objects/query?print_stats',timeout=5))['result']['status']['print_stats']['state'])" 2>/dev/null)
PRINTER_STATE=$(python3 -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:7126/printer/info',timeout=5))['result']['state'])" 2>/dev/null)
if [ "$STATE" = "printing" ] || { [ "$STATE" = "paused" ] && [ "$PRINTER_STATE" != "shutdown" ]; }; then
    echo "REFUSING to switch: print state = $STATE, printer state = $PRINTER_STATE"
    exit 1
fi

echo "stopping stock klipper..."
/etc/init.d/klipper stop
sleep 2
[ -f /var/run/klippy_mainline.pid ] \
    && kill "$(cat /var/run/klippy_mainline.pid)" 2>/dev/null
sleep 1
rm -f /tmp/klippy_uds

mkdir -p "$PDATA/config" "$PDATA/logs"

# Keep the running configuration inside Moonraker's visible config root.  Seed
# each build once so later edits made in Mainsail are preserved across restarts.
BUILD_NAME=$(basename "$MAINLINE")
BUILD_CFG="$PDATA/config/${BUILD_NAME}.cfg"
if [ ! -f "$BUILD_CFG" ]; then
    cp "$CFG_SOURCE" "$BUILD_CFG"
fi
cat >"$PDATA/config/printer.cfg" <<EOF
# Active open-stack configuration.
# Edit ${BUILD_NAME}.cfg in Mainsail; this small pointer makes build rollback
# explicit and keeps every changed setting visible in the configuration editor.
[include ${BUILD_NAME}.cfg]
EOF
CFG="$PDATA/config/printer.cfg"

# Share the existing uploaded G-code library without duplicating it.
if [ ! -e "$PDATA/gcodes" ]; then
    ln -s /mnt/UDISK/printer_data/gcodes "$PDATA/gcodes"
fi
# This ARMv7 build ships a cross-compiled helper because the K2 has no local
# compiler. Package transfers can make C sources newer than the helper and
# trigger an impossible on-printer rebuild, so mark the validated helper fresh.
[ -f "$MAINLINE/klippy/chelper/c_helper.so" ] \
    && touch "$MAINLINE/klippy/chelper/c_helper.so"
echo "starting mainline klippy..."
echo "config: $CFG"
taskset 0x2 /usr/share/klippy-env/bin/python \
    "$MAINLINE/klippy/klippy.py" \
    "$CFG" \
    -l "$PDATA/logs/klippy.log" \
    -a /tmp/klippy_uds \
    >/dev/null 2>&1 &
echo $! > /var/run/klippy_mainline.pid
echo "mainline klippy started, pid $(cat /var/run/klippy_mainline.pid)"
echo "log: $PDATA/logs/klippy.log"
echo "switch back with: sh $MAINLINE/switch-to-stock.sh"
