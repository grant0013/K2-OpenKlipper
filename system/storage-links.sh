#!/bin/sh
# Point the G-code, time-lapse and log folders at the storage chosen on the
# touchscreen's Settings page ("gcode_storage": "usb" in
# harkscreen-settings.json): a USB drive in the K2's USB port (the K2 mounts
# it at /mnt/exUDISK), or the printer's own eMMC when there is none. Keeps
# frequent writes (uploads, prints, time-lapse videos, Klipper's log) off
# the eMMC. Run by switch-to-mainline.sh before Klipper starts; prints the
# names it re-pointed, so the caller restarts Moonraker if it printed any.
# (OKL_* overrides are for tests)
PDATA=${OKL_PDATA:-/mnt/UDISK/printer_data_mainline}
DATA=${OKL_DATA:-/mnt/UDISK/printer_data}
MOUNTS=${OKL_MOUNTS:-/proc/mounts}
PREFS=$PDATA/config/harkscreen-settings.json
USB=${OKL_USB:-/mnt/exUDISK}

where=internal
# A drive ejected on the touchscreen stays unused until it is unplugged and
# plugged in again (the screen clears "usb_ejected" when it is removed)
if grep -q '"gcode_storage": *"usb"' "$PREFS" 2>/dev/null \
        && ! grep -q '"usb_ejected": *true' "$PREFS" 2>/dev/null \
        && grep -q " $USB " "$MOUNTS"; then
    where=usb
fi

# point NAME USB_TARGET INTERNAL_TARGET
point() {
    link=$PDATA/$1
    if [ "$where" = usb ]; then target=$2; else target=$3; fi
    # First time: a real folder becomes the internal copy
    if [ -d "$link" ] && [ ! -L "$link" ]; then
        [ -e "$3" ] && return 0
        mv "$link" "$3" || return 0
    fi
    mkdir -p "$target" || return 0
    [ "$(readlink "$link" 2>/dev/null)" = "$target" ] && return 0
    ln -sfn "$target" "$link" && echo "$1"
}

point gcodes "$USB/gcodes" "$DATA/gcodes"
point timelapse "$USB/timelapse" "$DATA/timelapse"
point logs "$USB/k2-logs" "$PDATA/logs.internal"
exit 0
