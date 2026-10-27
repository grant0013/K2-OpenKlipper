#!/bin/sh
# Put a K2 back to the stock Creality firmware after install.sh. Run on the K2
# as root. Restores every file install.sh replaced, from its backup, and stops
# the open stack starting at boot. Your G-code files are not touched.
#
#   sh uninstall.sh                 restore from the latest install backup
#   sh uninstall.sh <backup-dir>    restore from a specific backup
#   sh uninstall.sh --purge         also delete the open-stack files and its
#                                   settings (printer_data_mainline)
set -eu

BUILD=/mnt/UDISK/k2-mainline-20260729-v62
PDATA=/mnt/UDISK/printer_data_mainline
FLAGS=/mnt/UDISK/.system
PURGE=0
BACKUP=""
for a in "$@"; do
    case "$a" in
        --purge) PURGE=1 ;;
        *) BACKUP=$a ;;
    esac
done
[ -n "$BACKUP" ] || BACKUP=$(cat /mnt/UDISK/openklipper-backups/LATEST 2>/dev/null || true)

say() { echo "==> $*"; }
die() { echo "STOPPED: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run this as root"
[ -n "$BACKUP" ] && [ -f "$BACKUP/app.init" ] || die "no install backup found (expected /mnt/UDISK/openklipper-backups/LATEST)"

STATE=$(python3 -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:7126/printer/objects/query?print_stats',timeout=3))['result']['status']['print_stats']['state'])" 2>/dev/null || echo unknown)
case "$STATE" in
    printing|paused) die "a print is $STATE. Finish or cancel it first" ;;
esac

say "Stopping the open stack"
rm -f "$FLAGS/boot-mainline"
[ -x "$BUILD/switch-to-stock.sh" ] && sh "$BUILD/switch-to-stock.sh" || true
/etc/init.d/moonraker-mainline stop >/dev/null 2>&1 || true
/etc/init.d/moonraker-mainline disable >/dev/null 2>&1 || true

say "Restoring stock files from $BACKUP"
cp "$BACKUP/app.init" /etc/init.d/app
cp "$BACKUP/nginx.conf" /etc/nginx/nginx.conf
[ -f "$BACKUP/update_manager.py" ] && cp "$BACKUP/update_manager.py" /usr/share/moonraker/components/update_manager/update_manager.py
[ -f "$BACKUP/moonraker.version" ] && cp "$BACKUP/moonraker.version" /usr/share/moonraker/.version
[ -f "$BACKUP/moonraker-stock.conf" ] && cp "$BACKUP/moonraker-stock.conf" /usr/share/moonraker/moonraker.conf
# rc.local: drop only our boot hook, keep anything else added since
grep -v -e "boot-mainline" -e "K2 OpenKlipper boot hook" /etc/rc.local > /tmp/rc.local.okl || true
cat /tmp/rc.local.okl > /etc/rc.local
if [ -f "$BACKUP/stock-moonraker-was-enabled" ]; then
    /etc/init.d/moonraker enable >/dev/null 2>&1 || true
    /etc/init.d/moonraker start >/dev/null 2>&1 || true
fi
/etc/init.d/nginx reload >/dev/null 2>&1 || true
/etc/init.d/app restart >/dev/null 2>&1 || true

if [ "$PURGE" = 1 ]; then
    say "Deleting the open-stack files and settings"
    rm -rf "$BUILD" "$PDATA" /mnt/UDISK/mainsail-mainline /mnt/UDISK/harkscreen /mnt/UDISK/harkcam
fi

cat <<EOF

==> Back on the stock firmware. Reboot the printer now (homing can be
    unreliable until it has been restarted): reboot
    Open-stack files are $( [ "$PURGE" = 1 ] && echo deleted || echo "kept in $BUILD (sh install.sh reinstalls)" ).
EOF
