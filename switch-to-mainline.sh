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
/etc/init.d/klipper stop >/dev/null 2>&1 || true
sleep 2
[ -f /var/run/klippy_mainline.pid ] \
    && kill "$(cat /var/run/klippy_mainline.pid)" 2>/dev/null
sleep 1
rm -f /tmp/klippy_uds

mkdir -p "$PDATA/config" "$PDATA/logs"

# Keep the running configuration inside Moonraker's visible config root.  Seed
# each build once so later edits made in Mainsail are preserved across restarts.
BUILD_NAME=$(basename "$MAINLINE")
CONF="$PDATA/config"
if [ -f "$CONF/printer-overrides.cfg" ]; then
    # Managed layout (v0.3+): the open-stack config is refreshed from this
    # build on every start, so updates reach it; the printer's own settings
    # live in printer-overrides.cfg, which is loaded last and wins.
    {
        echo "# MANAGED BY K2 OpenKlipper: replaced from the installed build on every"
        echo "# start and update. Do not edit; put changes in printer-overrides.cfg."
        echo
        cat "$CFG_SOURCE"
    } > "$CONF/k2-openklipper.cfg"
    NOTE="# k2-openklipper.cfg is managed (replaced on update); edit printer-overrides.cfg."
    POINTER="[include k2-openklipper.cfg]
[include printer-overrides.cfg]"
else
    # Legacy layout (v0.2 installs): one seeded copy, edited directly.
    BUILD_CFG="$CONF/${BUILD_NAME}.cfg"
    if [ ! -f "$BUILD_CFG" ]; then
        cp "$CFG_SOURCE" "$BUILD_CFG"
    fi
    NOTE="# Edit ${BUILD_NAME}.cfg in Mainsail."
    POINTER="[include ${BUILD_NAME}.cfg]"
fi
# Keep any SAVE_CONFIG block Klipper appended to printer.cfg.
SAVED=$(sed -n '/^#\*# <-* SAVE_CONFIG -*>/,$p' "$CONF/printer.cfg" 2>/dev/null)
{
    echo "# Active open-stack configuration."
    echo "$NOTE"
    echo "$POINTER"
    if [ -n "$SAVED" ]; then
        echo
        echo "$SAVED"
    fi
} > "$CONF/printer.cfg"
CFG="$CONF/printer.cfg"

# Share the existing uploaded G-code library without duplicating it.
if [ ! -e "$PDATA/gcodes" ]; then
    ln -s /mnt/UDISK/printer_data/gcodes "$PDATA/gcodes"
fi
# This ARMv7 build ships a cross-compiled helper because the K2 has no local
# compiler. Package transfers can make C sources newer than the helper and
# trigger an impossible on-printer rebuild, so mark the validated helper fresh.
# The K2 has no RTC: at boot the clock reads 2020 until NTP syncs, so stamp
# the helper with its newest source's time rather than "now".
NEWEST_SRC=$(ls -t "$MAINLINE"/klippy/chelper/*.c "$MAINLINE"/klippy/chelper/*.h 2>/dev/null | head -1)
[ -f "$MAINLINE/klippy/chelper/c_helper.so" ] && [ -n "$NEWEST_SRC" ] \
    && touch -r "$NEWEST_SRC" "$MAINLINE/klippy/chelper/c_helper.so"
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
# Hark Screen touchscreen UI (takes the panel over from the boot animation)
[ -x /mnt/UDISK/harkscreen/start.sh ] && sh /mnt/UDISK/harkscreen/start.sh
# Hark Cam: USB camera as MJPEG on :8080 (Mainsail /webcam/), replacing cam_app/webrtc
[ -x /mnt/UDISK/harkcam/start.sh ] && sh /mnt/UDISK/harkcam/start.sh
