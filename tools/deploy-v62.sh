#!/bin/sh
set -eu

ARCH=${1:-/mnt/UDISK/k2-mainline-20260729-v62.tar.gz}
SUMFILE=${ARCH}.sha256
BUILD=/mnt/UDISK/k2-mainline-20260729-v62

[ -f "$ARCH" ] || {
    echo "archive not found: $ARCH"
    exit 1
}
[ -f "$SUMFILE" ] || {
    echo "checksum file not found: $SUMFILE"
    exit 1
}

cd "$(dirname "$ARCH")"
sha256sum -c "$(basename "$SUMFILE")"

# Refuse before changing services or files. A paused print may still be
# recoverable, so it is treated as active.
STATE=$(python3 - <<'PY'
import json
import urllib.request

for port in (7126, 7125):
    try:
        url = (
            "http://127.0.0.1:%d/printer/objects/query?print_stats" % port
        )
        data = json.load(urllib.request.urlopen(url, timeout=3))
        print(data["result"]["status"]["print_stats"]["state"])
        break
    except Exception:
        pass
else:
    print("unknown")
PY
)
case "$STATE" in
    printing|paused)
        echo "REFUSING to deploy: print state = $STATE"
        exit 1
        ;;
esac

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/mnt/UDISK/mainline-backups/pre-v62-release-"$STAMP"
mkdir -p "$BACKUP/moonraker-compat"

cp -a /etc/nginx/nginx.conf "$BACKUP/nginx.conf"
cp -a /usr/share/moonraker/moonraker.conf "$BACKUP/moonraker-stock.conf"
cp -a /usr/share/moonraker/components/update_manager/update_manager.py \
    "$BACKUP/moonraker-compat/update_manager.py.before-v62"
[ ! -e /usr/share/moonraker/.version ] ||
    cp -a /usr/share/moonraker/.version \
        "$BACKUP/moonraker-compat/moonraker.version.before-v62"
[ ! -e /etc/init.d/moonraker-mainline ] ||
    cp -a /etc/init.d/moonraker-mainline \
        "$BACKUP/moonraker-mainline.init"
[ ! -e /mnt/UDISK/printer_data_mainline/config ] ||
    cp -a /mnt/UDISK/printer_data_mainline/config "$BACKUP/config"

if [ -e "$BUILD" ]; then
    mv "$BUILD" "$BACKUP/k2-mainline-20260729-v62.previous"
fi
if [ -e /mnt/UDISK/mainsail-mainline ]; then
    mv /mnt/UDISK/mainsail-mainline \
        "$BACKUP/mainsail-mainline.previous"
fi

tar -xzf "$ARCH" -C /mnt/UDISK
chmod 755 \
    "$BUILD/switch-to-mainline.sh" \
    "$BUILD/switch-to-stock.sh" \
    "$BUILD/system/moonraker-mainline.init"

python3 -m py_compile \
    "$BUILD/system/moonraker-compat/update_manager.py"

mkdir -p \
    /mnt/UDISK/printer_data_mainline/config \
    /mnt/UDISK/printer_data_mainline/logs
cp -a "$BUILD/system/mainsail" /mnt/UDISK/mainsail-mainline
cp "$BUILD/system/moonraker-mainline.conf" \
    /mnt/UDISK/printer_data_mainline/config/moonraker.conf
cp "$BUILD/system/moonraker-mainline.init" \
    /etc/init.d/moonraker-mainline
cp "$BUILD/system/moonraker-compat/update_manager.py" \
    /usr/share/moonraker/components/update_manager/update_manager.py
cp "$BUILD/system/moonraker-compat/.version" \
    /usr/share/moonraker/.version
chmod 755 /etc/init.d/moonraker-mainline

# Validate the replacement before touching live Nginx configuration.
cp "$BUILD/system-nginx.conf" /etc/nginx/nginx.conf.v62-new
if ! nginx -t -c /etc/nginx/nginx.conf.v62-new; then
    rm -f /etc/nginx/nginx.conf.v62-new
    echo "Nginx validation failed; live config was not changed"
    exit 1
fi
mv /etc/nginx/nginx.conf.v62-new /etc/nginx/nginx.conf

/etc/init.d/moonraker-mainline enable
/etc/init.d/moonraker-mainline restart
/etc/init.d/nginx reload

sh "$BUILD/switch-to-mainline.sh" mainline_stage2.cfg

echo "BACKUP=$BACKUP"
echo "Mainsail=http://$(uci -q get network.lan.ipaddr || hostname -I | awk '{print $1}'):4409/"
