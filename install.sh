#!/bin/sh
# K2 OpenKlipper installer. Run on the K2 as root (SSH in first, see
# docs/INSTALL.md). Installs next to the stock firmware: nothing of Creality's
# is deleted, every file it replaces is backed up first, and uninstall.sh puts
# the printer back to stock.
#
#   sh install.sh            install (downloads the release unless run from an
#                            unpacked copy of this repository)
#   sh install.sh --check    only check the printer, change nothing
#
# Environment: OKL_REF=<tag or branch> (default below), OKL_FORCE=1 to install
# on a model other than the K2 / K2 Combo (F021), at your own risk.
set -eu

REPO=grant0013/K2-OpenKlipper
REF=${OKL_REF:-main}
BUILD=/mnt/UDISK/k2-mainline-20260729-v62
PDATA=/mnt/UDISK/printer_data_mainline
FLAGS=/mnt/UDISK/.system
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/mnt/UDISK/openklipper-backups/$STAMP
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

say() { echo "==> $*"; }
die() { echo "STOPPED: $*" >&2; exit 1; }

# ---------------------------------------------------------------- checks
say "Checking the printer"
[ "$(id -u)" = 0 ] || die "run this as root (ssh root@<printer-ip>)"
grep -q OpenWrt /etc/os-release 2>/dev/null || die "this is not a Creality K2 (no OpenWrt)"
command -v python3 >/dev/null || die "python3 not found"

MODEL=$(head -1 /mnt/UDISK/printer_data/config/printer.cfg 2>/dev/null | tr -dc 'A-Z0-9')
VERSION=$(fw_printenv version 2>/dev/null | cut -d= -f2)
[ -n "$VERSION" ] || VERSION=unknown
echo "    model: ${MODEL:-unknown}   Creality firmware: $VERSION"
case "$MODEL" in
    F021) ;;
    F012|F008)
        [ "${OKL_FORCE:-0}" = 1 ] || die "model $MODEL is untested (developed on the F021 K2 / K2 Combo). OKL_FORCE=1 to try anyway"
        echo "    WARNING: $MODEL is untested, continuing because OKL_FORCE=1" ;;
    *)  [ "${OKL_FORCE:-0}" = 1 ] || die "unknown model '$MODEL' (expected F021)" ;;
esac
case "$VERSION" in
    1.1.7.0) ;;
    *) echo "    NOTE: tested on Creality firmware 1.1.7.0; yours is $VERSION" ;;
esac

FREE_MB=$(df -m /mnt/UDISK | awk 'NR==2 {print $4}')
echo "    free space on /mnt/UDISK: ${FREE_MB} MB"
[ "$FREE_MB" -ge 200 ] || die "need at least 200 MB free on /mnt/UDISK (delete old G-code files or timelapses)"

STATE=$(python3 - <<'PY'
import json, urllib.request
for port in (7125, 7126):
    try:
        url = "http://127.0.0.1:%d/printer/objects/query?print_stats" % port
        print(json.load(urllib.request.urlopen(url, timeout=3))["result"]["status"]["print_stats"]["state"])
        break
    except Exception:
        pass
else:
    print("unknown")
PY
)
echo "    print state: $STATE"
case "$STATE" in
    printing|paused) die "a print is $STATE. Finish or cancel it first" ;;
esac

if [ -f "$FLAGS/boot-hostlink" ]; then
    die "this K2 is set up for an external host PC (boot-hostlink). Remove that setup first"
fi

if [ "$CHECK" = 1 ]; then
    say "Checks passed. Nothing was changed (--check)."
    exit 0
fi

# ---------------------------------------------------------------- source
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$HERE/klippy/klippy.py" ] && [ -f "$HERE/config/mainline_stage2.cfg" ]; then
    SRC=$HERE
    say "Installing from $SRC"
else
    say "Downloading $REPO ($REF)"
    TMP=/mnt/UDISK/okl-download
    rm -rf "$TMP"; mkdir -p "$TMP"
    python3 - "$REPO" "$REF" "$TMP/src.tar.gz" <<'PY'
import sys, urllib.request
repo, ref, out = sys.argv[1:4]
url = "https://codeload.github.com/%s/tar.gz/%s" % (repo, ref)
with urllib.request.urlopen(url, timeout=120) as r, open(out, "wb") as f:
    while True:
        b = r.read(65536)
        if not b:
            break
        f.write(b)
print("    downloaded %s" % url)
PY
    tar -xzf "$TMP/src.tar.gz" -C "$TMP"
    SRC=$(find "$TMP" -maxdepth 1 -type d -name 'K2-OpenKlipper-*' | head -1)
    [ -n "$SRC" ] && [ -f "$SRC/klippy/klippy.py" ] || die "download did not unpack as expected"
fi
[ "$SRC" = "$BUILD" ] && die "run install.sh from a download or a copy, not from $BUILD itself"

# ---------------------------------------------------------------- backup
say "Backing up to $BACKUP"
mkdir -p "$BACKUP"
cp -a /etc/init.d/app "$BACKUP/app.init"
cp -a /etc/rc.local "$BACKUP/rc.local"
cp -a /etc/nginx/nginx.conf "$BACKUP/nginx.conf"
cp -a /usr/share/moonraker/moonraker.conf "$BACKUP/moonraker-stock.conf" 2>/dev/null || true
cp -a /usr/share/moonraker/components/update_manager/update_manager.py "$BACKUP/update_manager.py" 2>/dev/null || true
cp -a /usr/share/moonraker/.version "$BACKUP/moonraker.version" 2>/dev/null || true
[ -e /etc/init.d/moonraker-mainline ] && cp -a /etc/init.d/moonraker-mainline "$BACKUP/moonraker-mainline.init"
/etc/init.d/moonraker enabled 2>/dev/null && touch "$BACKUP/stock-moonraker-was-enabled" || true
[ -d "$PDATA/config" ] && cp -a "$PDATA/config" "$BACKUP/printer_data_mainline-config"
[ -e "$BUILD" ] && mv "$BUILD" "$BACKUP/build.previous"
for d in mainsail-mainline harkscreen harkcam; do
    [ -e "/mnt/UDISK/$d" ] && mv "/mnt/UDISK/$d" "$BACKUP/$d.previous"
done
echo "$BACKUP" > /mnt/UDISK/openklipper-backups/LATEST

# ---------------------------------------------------------------- files
say "Installing the open stack to $BUILD"
cp -a "$SRC" "$BUILD"
chmod 755 "$BUILD/switch-to-mainline.sh" "$BUILD/switch-to-stock.sh" \
    "$BUILD/system/moonraker-mainline.init" "$BUILD/uninstall.sh" 2>/dev/null || true
python3 -m py_compile "$BUILD/system/moonraker-compat/update_manager.py"

mkdir -p "$PDATA/config" "$PDATA/logs" "$FLAGS"
cp -a "$BUILD/system/mainsail" /mnt/UDISK/mainsail-mainline
[ -f "$PDATA/config/moonraker.conf" ] || cp "$BUILD/system/moonraker-mainline.conf" "$PDATA/config/moonraker.conf"
cp "$BUILD/system/moonraker-mainline.init" /etc/init.d/moonraker-mainline
chmod 755 /etc/init.d/moonraker-mainline
cp "$BUILD/system/moonraker-compat/update_manager.py" /usr/share/moonraker/components/update_manager/update_manager.py
cp "$BUILD/system/moonraker-compat/.version" /usr/share/moonraker/.version

# Touchscreen and camera
cp -a "$BUILD/harkscreen" /mnt/UDISK/harkscreen
# Default look: Klipper logo and colours. An existing harkscreen.cfg (your own
# theme) is kept. See docs/SCREEN-CUSTOMISE.md.
if [ ! -f "$PDATA/config/harkscreen.cfg" ]; then
    cp "$BUILD/harkscreen/harkscreen.cfg.example" "$PDATA/config/harkscreen.cfg"
    cp "$BUILD/harkscreen/klipper-logo.png" "$PDATA/config/klipper-logo.png"
fi
cp -a "$BUILD/harkcam" /mnt/UDISK/harkcam
chmod 755 /mnt/UDISK/harkscreen/start.sh /mnt/UDISK/harkcam/start.sh

# Nginx: validate the new config before it goes live
cp "$BUILD/system-nginx.conf" /etc/nginx/nginx.conf.okl-new
if ! nginx -t -c /etc/nginx/nginx.conf.okl-new >/dev/null 2>&1; then
    rm -f /etc/nginx/nginx.conf.okl-new
    die "the new nginx config failed its test; nothing live was changed except files in $BUILD (backup in $BACKUP)"
fi
mv /etc/nginx/nginx.conf.okl-new /etc/nginx/nginx.conf

# Creality's master-server and Monitor would fight Klipper for the printer, and
# upgrade-server would let an OTA wipe this install.
cp "$BUILD/system/app.init.mainline" /etc/init.d/app
chmod 755 /etc/init.d/app

# Start the open stack 30 s after every boot while the flag file exists.
HOOK="[ -f $FLAGS/boot-mainline ] && (sleep 30; sh $BUILD/switch-to-mainline.sh mainline_stage2.cfg > /tmp/mainline-boot.log 2>&1) &"
if ! grep -q "boot-mainline" /etc/rc.local; then
    awk -v hook="$HOOK" '/^exit 0/ && !done {print "# K2 OpenKlipper boot hook (uninstall.sh removes it)"; print hook; print ""; done=1} {print}' /etc/rc.local > /tmp/rc.local.okl
    grep -q "boot-mainline" /tmp/rc.local.okl || { echo "# K2 OpenKlipper boot hook (uninstall.sh removes it)"; echo "$HOOK"; } >> /tmp/rc.local.okl
    cat /tmp/rc.local.okl > /etc/rc.local
fi
rm -f "$FLAGS/boot-mainline.off"
touch "$FLAGS/boot-mainline"

# ---------------------------------------------------------------- start
say "Starting"
killall -9 master-server Monitor upgrade-server 2>/dev/null || true
/etc/init.d/moonraker stop >/dev/null 2>&1 || true
/etc/init.d/moonraker disable 2>/dev/null || true
/etc/init.d/moonraker-mainline enable
/etc/init.d/moonraker-mainline restart
/etc/init.d/nginx reload
sh "$BUILD/switch-to-mainline.sh" mainline_stage2.cfg

IP=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')
[ -n "$IP" ] || IP=$(ip -4 addr show eth0 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')
cat <<EOF

==> Done.
    Mainsail:   http://${IP:-<printer-ip>}:4409/
    Backup:     $BACKUP
    Undo:       sh $BUILD/uninstall.sh
    Next:       set up OrcaSlicer and do a first print (docs/INSTALL.md, steps 6 and 7)
EOF
