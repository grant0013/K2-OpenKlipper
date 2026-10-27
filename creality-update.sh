#!/bin/sh
# Get ready for a Creality firmware update without losing your settings.
#
# K2 OpenKlipper switches Creality's updater off, and a Creality update can
# wipe the open stack's folders (one did). This saves your settings to the
# G-code folder, which updates leave alone, then puts the printer back to
# stock so you can update from the touchscreen as normal. Afterwards, run the
# installer again: it finds this backup and restores your settings.
#
#   sh creality-update.sh
set -eu

BUILD=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PDATA=/mnt/UDISK/printer_data_mainline
GCODES=/mnt/UDISK/printer_data/gcodes
OUT="$GCODES/okl-settings-backup-$(date +%Y%m%d-%H%M%S).tar.gz"

say() { echo "==> $*"; }
die() { echo "STOPPED: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run this as root"
STATE=$(python3 -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:7126/printer/objects/query?print_stats',timeout=3))['result']['status']['print_stats']['state'])" 2>/dev/null || echo unknown)
case "$STATE" in
    printing|paused) die "a print is $STATE. Finish or cancel it first" ;;
esac
[ -d "$PDATA/config" ] || die "no K2 OpenKlipper settings found in $PDATA"
[ -d "$GCODES" ] || die "G-code folder $GCODES not found"

say "Saving your settings to $OUT"
tar -czf "$OUT" -C "$PDATA" config database 2>/dev/null || tar -czf "$OUT" -C "$PDATA" config
ls -la "$OUT"
echo "    (also downloadable from the G-code files list in Mainsail)"

say "Putting the printer back to stock"
sh "$BUILD/uninstall.sh"

cat <<EOF

==> Ready for the Creality update.
    1. Reboot the printer, then update the firmware from the touchscreen
       (or Creality's app) as usual.
    2. Afterwards, enable root again if the update turned it off, SSH in and
       run the install command from docs/INSTALL.md. It finds
       $(basename "$OUT")
       and restores your settings.
EOF
