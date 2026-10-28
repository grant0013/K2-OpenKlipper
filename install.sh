#!/bin/sh
# K2 OpenKlipper installer. Run on the K2 as root (SSH in first, see
# docs/INSTALL.md). Installs next to the stock firmware: nothing of Creality's
# is deleted, every file it changes is backed up first, and uninstall.sh puts
# the printer back to stock.
#
#   sh install.sh            install the latest release (downloads it, unless
#                            run from an unpacked copy of this repository)
#   sh install.sh --update   update to the latest release, keeping this
#                            printer's settings (printer-overrides.cfg, the
#                            touchscreen theme, Moonraker and slot data)
#   sh install.sh --check    only check the printer, change nothing
#
# Environment: OKL_REF=<tag or branch> to install something other than the
# latest release, OKL_FORCE=1 to install on a model other than the K2 / K2
# Combo (F021), at your own risk, OKL_K2PLUS_BETA=1 to install the K2 Plus
# (F008) beta config (docs/K2-PLUS-BETA.md), OKL_EXTRUDER=stock|dxc2 to answer the
# extruder question in advance, OKL_CFS=yes|no to fix whether a CFS is used
# (default: found automatically at each start).
set -eu

REPO=grant0013/K2-OpenKlipper
REF=${OKL_REF:-}
BUILD=/mnt/UDISK/k2-mainline-20260729-v62
PDATA=/mnt/UDISK/printer_data_mainline
CONF=$PDATA/config
FLAGS=/mnt/UDISK/.system
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/mnt/UDISK/openklipper-backups/$STAMP
MODE=install
case "${1:-}" in
    --check) MODE=check ;;
    --update) MODE=update ;;
    "") ;;
    *) echo "usage: sh install.sh [--check|--update]"; exit 2 ;;
esac

say() { echo "==> $*"; }
die() { echo "STOPPED: $*" >&2; exit 1; }

# Remove master-server, upgrade-server and Monitor from APP_LIST in Creality's
# /etc/init.d/app (in place; the original is backed up). --check only tests
# that the file has the expected APP_LIST. Prints: ok, already, or an error.
appinit_edit() {
    python3 - "$1" "${2:-}" <<'PY'
import re, sys
path, mode = sys.argv[1], sys.argv[2]
DROP = ("$BIN_PATH/$MASTER_SERVER", "$BIN_PATH/$UPGRADE_SERVER", "$BIN_PATH/$MONITOR")
try:
    text = open(path).read()
except OSError as e:
    print("cannot read %s: %s" % (path, e)); sys.exit(1)
m = re.search(r'^APP_LIST="(.*?)"', text, re.S | re.M)
if not m or "$BIN_PATH/$APP_SERVER" not in m.group(1):
    print("unexpected APP_LIST in %s" % path); sys.exit(1)
body = m.group(1)
if not any(d in body for d in DROP):
    print("already"); sys.exit(0)
if mode == "--check":
    print("ok"); sys.exit(0)
new = body
for d in DROP:
    new = new.replace(" " + d, "").replace(d + " ", "").replace(d, "")
new = re.sub(r"[ \t]+\\\n", " \\\n", new)
text = text[:m.start(1)] + new + text[m.end(1):]
open(path, "w").write(text)
print("ok")
PY
}

# Read option values from a Klipper config file: cfg_values FILE SECTION KEY...
cfg_values() {
    python3 - "$@" <<'PY'
import configparser, sys
path, section, keys = sys.argv[1], sys.argv[2], sys.argv[3:]
cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
try:
    with open(path) as f:
        cp.read_string("\n".join(l for l in f.read().split("\n") if not l.startswith("#*#")))
    print(" ".join(cp.get(section, k).split()[0] if cp.has_option(section, k) else "-" for k in keys))
except Exception:
    print(" ".join("-" for k in keys))
PY
}

# ---------------------------------------------------------------- checks
say "Checking the printer"
[ "$(id -u)" = 0 ] || die "run this as root (ssh root@<printer-ip>)"
grep -q OpenWrt /etc/os-release 2>/dev/null || die "this is not a Creality K2 (no OpenWrt)"
command -v python3 >/dev/null || die "python3 not found"

MODEL=$(head -1 /mnt/UDISK/printer_data/config/printer.cfg 2>/dev/null | tr -dc 'A-Z0-9')
FW=$(fw_printenv version 2>/dev/null | cut -d= -f2)
[ -n "$FW" ] || FW=unknown
INSTALLED=$(cat "$BUILD/VERSION" 2>/dev/null || echo none)
if [ "$INSTALLED" = none ] && [ -d "$BUILD" ]; then
    SHOW_INSTALLED="0.2 or earlier"
else
    SHOW_INSTALLED=$INSTALLED
fi
echo "    model: ${MODEL:-unknown}   Creality firmware: $FW   installed K2 OpenKlipper: $SHOW_INSTALLED"
STAGE_CFG=mainline_stage2.cfg
case "$MODEL" in
    F021) ;;
    F008)
        # K2 Plus: its own config, only on the board layout it was written
        # for (stock config dir F008_CR0CN240319C13 = structure_version 0)
        BOARD=$(get_sn_mac.sh board 2>/dev/null || true)
        STRUCT=$(get_sn_mac.sh structure_version 2>/dev/null || true)
        echo "    K2 Plus board: ${BOARD:-unknown}   structure_version: ${STRUCT:-unknown}"
        case "$BOARD:$STRUCT" in
            CR0CN240110C10:0|CR0CN240319C13:0|CR0CN240319C14:0) ;;
            *) [ "${OKL_FORCE:-0}" = 1 ] || die "this K2 Plus layout ($BOARD, structure $STRUCT) is not the one the beta config was written for; please send the check output to the developer" ;;
        esac
        if [ "$MODE" != check ]; then
            [ "${OKL_K2PLUS_BETA:-0}" = 1 ] || [ "${OKL_FORCE:-0}" = 1 ] || die "the K2 Plus config is a BETA that has not run on a real K2 Plus yet. Read docs/K2-PLUS-BETA.md, then run with OKL_K2PLUS_BETA=1"
            echo "    WARNING: K2 Plus BETA config (untested on a real K2 Plus)"
        fi
        STAGE_CFG=mainline_stage2_k2plus.cfg ;;
    F012)
        [ "${OKL_FORCE:-0}" = 1 ] || die "model $MODEL is untested (developed on the F021 K2 / K2 Combo). OKL_FORCE=1 to try anyway"
        echo "    WARNING: $MODEL is untested, continuing because OKL_FORCE=1" ;;
    *)  [ "${OKL_FORCE:-0}" = 1 ] || die "unknown model '$MODEL' (expected F021)" ;;
esac
case "$FW" in
    1.1.7.0) ;;
    *) echo "    NOTE: tested on Creality firmware 1.1.7.0; yours is $FW" ;;
esac
if [ "$MODE" = update ] && [ "$INSTALLED" = none ] && [ ! -d "$BUILD" ]; then
    die "K2 OpenKlipper is not installed here; run sh install.sh"
fi

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

# CFS: Creality's own config keeps the CFS it addressed (SAVE_CONFIG block
# [auto_addr] mb_addr_table_uniids, one 12-byte id per box). A K2 without a
# CFS has none.
CFS_UNIID=$(python3 - <<'PY'
import re
try:
    text = open("/mnt/UDISK/printer_data/config/printer.cfg").read()
except Exception:
    text = ""
found = ""
m = re.search(r"mb_addr_table_uniids\s*=\s*\n((?:#\*#[ \t]+0x[^\n]*\n)+)", text)
if m:
    for line in m.group(1).splitlines():
        b = re.findall(r"0x([0-9A-Fa-f]{2})", line)
        if len(b) == 12 and any(x != "00" for x in b):
            found = "".join(b).lower()
            break
print(found)
PY
)

# Where this printer's settings come from: an existing printer-overrides.cfg is
# kept; a v0.2 install (one edited config) is migrated; otherwise ask.
LEGACY_CFG="$CONF/$(basename "$BUILD").cfg"
# A settings backup made by creality-update.sh before a Creality update
SETTINGS_BACKUP=""
if [ ! -f "$CONF/printer-overrides.cfg" ] && [ ! -f "$LEGACY_CFG" ]; then
    SETTINGS_BACKUP=$(ls -t /mnt/UDISK/printer_data/gcodes/okl-settings-backup-*.tar.gz 2>/dev/null | head -1 || true)
fi
if [ -n "$SETTINGS_BACKUP" ] && tar -tzf "$SETTINGS_BACKUP" 2>/dev/null | grep -q "config/printer-overrides.cfg"; then
    SETTINGS=restore
    echo "    settings: restoring $(basename "$SETTINGS_BACKUP") (made before a Creality update)"
elif [ -f "$CONF/printer-overrides.cfg" ]; then
    SETTINGS=keep
    echo "    settings: keeping printer-overrides.cfg"
elif [ -f "$LEGACY_CFG" ]; then
    SETTINGS=migrate
    CFS=auto
    set -- $(cfg_values "$LEGACY_CFG" k2_cfs cutter_cut_x unload_pull_length)
    CUT_X=$1; PULL=$2
    HOTZ=$(cfg_values "$LEGACY_CFG" "gcode_macro START_PRINT" variable_hot_z_comp)
    [ "$CUT_X" = "-" ] && CUT_X=-7.2
    [ "$PULL" = "-" ] && PULL=10
    echo "    settings: moving your $(basename "$LEGACY_CFG") values into printer-overrides.cfg"
    echo "              (cutter_cut_x $CUT_X, unload_pull_length $PULL, hot_z_comp ${HOTZ})"
else
    SETTINGS=new
    # Extruder: the stock Creality one, or a Phaetus DXC2 (it needs longer
    # post-cut pulls, per Phaetus' K2 instructions, and a deeper cut).
    EXTRUDER=${OKL_EXTRUDER:-}
    if [ -z "$EXTRUDER" ]; then
        ANSWER=""
        if [ -t 0 ] || (: < /dev/tty) 2>/dev/null; then
            echo ""
            echo "    Which extruder is fitted to this printer?"
            echo "      1) Stock Creality extruder (as shipped)"
            echo "      2) Phaetus DXC2 upgrade"
            printf "    Enter 1 or 2 [1]: "
            if [ -t 0 ]; then
                read ANSWER || ANSWER=1
            else
                read ANSWER < /dev/tty || ANSWER=1
            fi
        else
            echo "    No terminal to ask on: assuming the stock extruder (set OKL_EXTRUDER=dxc2 for a DXC2)"
        fi
        case "$ANSWER" in
            2) EXTRUDER=dxc2 ;;
            *) EXTRUDER=stock ;;
        esac
    fi
    case "$EXTRUDER" in
        stock|dxc2) ;;
        *) die "OKL_EXTRUDER must be stock or dxc2" ;;
    esac
    # CFS: auto finds it at startup and falls back to the rear spool holder
    # without one, like stock. OKL_CFS=yes|no fixes it either way.
    case "${OKL_CFS:-auto}" in
        auto) CFS=auto ;;
        yes) CFS=yes ;;
        no) CFS=no ;;
        *) die "OKL_CFS must be yes or no" ;;
    esac
    # The printer's own Creality cutter calibration, if the stock config has one
    STOCK_CUT=$(grep -h "^#\*# cut_pos_x" /mnt/UDISK/printer_data/config/printer.cfg 2>/dev/null | tail -1 | awk '{print $NF}')
    CUT_X=$(python3 - "$EXTRUDER" "${STOCK_CUT:-}" <<'PY'
import sys
extruder, stock = sys.argv[1], sys.argv[2]
try:
    cal = float(stock)
    if not -9.5 <= cal <= -5.5:
        cal = None
except ValueError:
    cal = None
if extruder == "dxc2":
    # At least as deep as the -8.0 a DXC2 needed for clean cuts
    cut = min(cal - 0.8, -8.0) if cal is not None else -8.0
else:
    cut = cal if cal is not None else -7.2
print("%.2f" % max(-10.0, min(-5.0, cut)))
PY
)
    if [ "$EXTRUDER" = dxc2 ]; then PULL=20; else PULL=10; fi
    echo "    extruder: $EXTRUDER   cutter depth X$CUT_X   post-cut pulls 3 x ${PULL} mm + 10 mm${STOCK_CUT:+   (Creality calibration $STOCK_CUT)}"
fi

APPINIT=$(appinit_edit /etc/init.d/app --check)
case "$APPINIT" in
    ok|already) ;;
    *) die "$APPINIT (this firmware's /etc/init.d/app is not the one the installer knows)" ;;
esac

if [ -f "$FLAGS/boot-hostlink" ]; then
    die "this K2 is set up for an external host PC (boot-hostlink). Remove that setup first"
fi

# Which version: the latest release unless OKL_REF says otherwise. Users only
# ever get versions that were tested and tagged; main is not installed by
# default.
if [ -z "$REF" ]; then
    REF=$(python3 - "$REPO" <<'PY'
import json, sys, urllib.request
try:
    req = urllib.request.Request("https://api.github.com/repos/%s/releases?per_page=10" % sys.argv[1],
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "k2-openklipper"})
    rel = [r for r in json.load(urllib.request.urlopen(req, timeout=30)) if not r.get("draft")]
    print(rel[0]["tag_name"] if rel else "main")
except Exception:
    print("main")
PY
)
fi
echo "    version to install: $REF"
if [ "$MODE" = update ] && [ -z "${OKL_REF:-}" ]; then
    NEWER=$(python3 - "$REF" "$INSTALLED" <<'PY'
import re, sys
def key(v):
    v = (v or "").strip().lstrip("vV")
    main, _, pre = v.partition("-")
    nums = tuple(int(n) for n in re.findall(r"\d+", main)[:3])
    nums += (0,) * (3 - len(nums))
    if not pre:
        return nums, (9, 0)
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get(re.sub(r"[^a-z]", "", pre.lower()), 0)
    num = re.findall(r"\d+", pre)
    return nums, (rank, int(num[0]) if num else 0)
try:
    print("yes" if key(sys.argv[1]) > key(sys.argv[2]) else "no")
except Exception:
    print("yes")
PY
)
    if [ "$NEWER" != yes ]; then
        say "Already up to date ($INSTALLED; newest release $REF). Nothing to do."
        exit 0
    fi
fi

if [ "$MODE" = check ]; then
    say "Checks passed. Nothing was changed (--check)."
    exit 0
fi

# ---------------------------------------------------------------- source
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$MODE" = install ] && [ "$HERE" != "$BUILD" ] \
        && [ -f "$HERE/klippy/klippy.py" ] && [ -f "$HERE/config/mainline_stage2.cfg" ]; then
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
NEW_VERSION=$(cat "$SRC/VERSION" 2>/dev/null || echo "$REF")

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
# On an update or reinstall the system files are already ours: carry the
# first install's copies of Creality's originals forward, so uninstall.sh
# still restores stock.
PREV=$(cat /mnt/UDISK/openklipper-backups/LATEST 2>/dev/null || true)
if [ "$APPINIT" = already ] && [ -n "$PREV" ] && [ -f "$PREV/app.init" ]; then
    for f in app.init rc.local nginx.conf moonraker-stock.conf update_manager.py moonraker.version stock-moonraker-was-enabled; do
        [ -e "$PREV/$f" ] && cp -a "$PREV/$f" "$BACKUP/$f"
    done
fi
[ -d "$CONF" ] && cp -a "$CONF" "$BACKUP/printer_data_mainline-config"
[ -e "$BUILD" ] && mv "$BUILD" "$BACKUP/build.previous"
for d in mainsail-mainline harkscreen harkcam; do
    [ -e "/mnt/UDISK/$d" ] && mv "/mnt/UDISK/$d" "$BACKUP/$d.previous"
done
echo "$BACKUP" > /mnt/UDISK/openklipper-backups/LATEST

# ---------------------------------------------------------------- files
say "Installing K2 OpenKlipper $NEW_VERSION to $BUILD"
cp -a "$SRC" "$BUILD"
echo "$NEW_VERSION" > "$BUILD/VERSION"
chmod 755 "$BUILD/switch-to-mainline.sh" "$BUILD/switch-to-stock.sh" "$BUILD/install.sh" "$BUILD/creality-update.sh" \
    "$BUILD/system/moonraker-mainline.init" "$BUILD/uninstall.sh" 2>/dev/null || true
python3 -m py_compile "$BUILD/system/moonraker-compat/update_manager.py"

mkdir -p "$CONF" "$PDATA/logs" "$FLAGS"
# Share the stock G-code library. This must exist before Moonraker first
# starts, or Moonraker creates an empty gcodes folder of its own.
if [ -d "$PDATA/gcodes" ] && [ ! -L "$PDATA/gcodes" ] && [ -z "$(ls -A "$PDATA/gcodes")" ]; then
    rmdir "$PDATA/gcodes"
fi
[ -e "$PDATA/gcodes" ] || ln -s /mnt/UDISK/printer_data/gcodes "$PDATA/gcodes"
cp -a "$BUILD/system/mainsail" /mnt/UDISK/mainsail-mainline
[ -f "$CONF/moonraker.conf" ] || cp "$BUILD/system/moonraker-mainline.conf" "$CONF/moonraker.conf"
cp "$BUILD/system/moonraker-mainline.init" /etc/init.d/moonraker-mainline
chmod 755 /etc/init.d/moonraker-mainline
cp "$BUILD/system/moonraker-compat/update_manager.py" /usr/share/moonraker/components/update_manager/update_manager.py
cp "$BUILD/system/moonraker-compat/.version" /usr/share/moonraker/.version

# Printer configuration: k2-openklipper.cfg (managed, written by the switch
# script on every start) plus printer-overrides.cfg (this printer's settings,
# never touched by updates).
case "$SETTINGS" in
    keep) ;;
    restore)
        tar -xzf "$SETTINGS_BACKUP" -C "$PDATA"
        rm -f "$CONF/k2-openklipper.cfg"
        echo "    printer config: settings restored from $(basename "$SETTINGS_BACKUP")"
        ;;
    new|migrate)
        case "$CFS" in yes) FITTED=True ;; no) FITTED=False ;; *) FITTED=auto ;; esac
        sed -e "s/__CUT_X__/$CUT_X/" -e "s/__PULL__/$PULL/" \
            -e "s/__CFS_FITTED__/$FITTED/" -e "s/__CFS_UNIID__/$CFS_UNIID/" \
            "$BUILD/config/printer-overrides.cfg.example" > "$CONF/printer-overrides.cfg"
        if [ "$SETTINGS" = migrate ]; then
            if [ "${HOTZ:-"-"}" != "-" ]; then
                printf '\n# First-layer correction carried over from your previous config\n[gcode_macro START_PRINT]\nvariable_hot_z_comp: %s\n' "$HOTZ" >> "$CONF/printer-overrides.cfg"
            fi
            mv "$LEGACY_CFG" "$LEGACY_CFG.pre-v0.3"
            echo "    your old config is kept as $(basename "$LEGACY_CFG").pre-v0.3: copy any other"
            echo "    settings you changed into printer-overrides.cfg"
        fi
        echo "    printer config: printer-overrides.cfg written (cutter_cut_x $CUT_X, unload_pull_length $PULL, CFS $CFS)"
        ;;
esac

# Touchscreen and camera
cp -a "$BUILD/harkscreen" /mnt/UDISK/harkscreen
# Default look: Klipper logo and colours. An existing harkscreen.cfg (your own
# theme) is kept. See docs/SCREEN-CUSTOMISE.md.
if [ ! -f "$CONF/harkscreen.cfg" ]; then
    cp "$BUILD/harkscreen/harkscreen.cfg.example" "$CONF/harkscreen.cfg"
    cp "$BUILD/harkscreen/klipper-logo.png" "$CONF/klipper-logo.png"
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
RESULT=$(appinit_edit /etc/init.d/app)
case "$RESULT" in
    ok) echo "    /etc/init.d/app: master-server, Monitor and upgrade-server removed from APP_LIST" ;;
    already) echo "    /etc/init.d/app: master-server, Monitor and upgrade-server already off" ;;
    *) die "editing /etc/init.d/app failed: $RESULT (backup in $BACKUP)" ;;
esac

# Start the open stack 30 s after every boot while the flag file exists.
HOOK="[ -f $FLAGS/boot-mainline ] && (sleep 30; sh $BUILD/switch-to-mainline.sh $STAGE_CFG > /tmp/mainline-boot.log 2>&1) &"
if ! grep -q "boot-mainline" /etc/rc.local; then
    awk -v hook="$HOOK" '/^exit 0/ && !done {print "# K2 OpenKlipper boot hook (uninstall.sh removes it)"; print hook; print ""; done=1} {print}' /etc/rc.local > /tmp/rc.local.okl
    grep -q "boot-mainline" /tmp/rc.local.okl || { echo "# K2 OpenKlipper boot hook (uninstall.sh removes it)"; echo "$HOOK"; } >> /tmp/rc.local.okl
    cat /tmp/rc.local.okl > /etc/rc.local
fi
# An existing hook keeps its line; make sure it starts this model's config
sed -i "/boot-mainline/s/mainline_stage2[a-z0-9_]*\.cfg/$STAGE_CFG/" /etc/rc.local
rm -f "$FLAGS/boot-mainline.off"
touch "$FLAGS/boot-mainline"

# ---------------------------------------------------------------- start
say "Starting"
killall -9 master-server Monitor upgrade-server 2>/dev/null || true
/etc/init.d/moonraker stop >/dev/null 2>&1 || true
/etc/init.d/moonraker disable >/dev/null 2>&1 || true
/etc/init.d/moonraker-mainline enable >/dev/null 2>&1
/etc/init.d/moonraker-mainline restart >/dev/null 2>&1
/etc/init.d/nginx reload >/dev/null 2>&1
sh "$BUILD/switch-to-mainline.sh" "$STAGE_CFG"

# Wait for Klipper. If stock Klipper left the main board shut down (it had
# only just restarted), Klipper reports "MCU ... shutdown": a firmware
# restart clears it.
say "Waiting for Klipper and the motors"
READY=$(python3 - <<'PY'
import json, time, urllib.request
M = "http://127.0.0.1:7126"
def get(path):
    return json.load(urllib.request.urlopen(M + path, timeout=5))["result"]
restarts = 0
deadline = time.time() + 240
while time.time() < deadline:
    try:
        state = get("/printer/info")["state"]
    except Exception:
        time.sleep(3)
        continue
    if state == "ready":
        try:
            if get("/printer/objects/query?k2_motor_bus")["status"]["k2_motor_bus"]["motors_ready"]:
                print("ready")
                break
        except Exception:
            pass
    elif state in ("error", "shutdown") and restarts < 3:
        restarts += 1
        try:
            urllib.request.urlopen(urllib.request.Request(
                M + "/printer/firmware_restart", data=b"", method="POST"), timeout=10)
        except Exception:
            pass
        time.sleep(15)
        continue
    time.sleep(3)
else:
    print("not-ready")
PY
)
if [ "$READY" != ready ]; then
    echo "    Klipper is not ready yet. Power the printer off and on, then open"
    echo "    Mainsail; if it still shows an error, see Troubleshooting in docs/INSTALL.md."
fi

IP=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')
[ -n "$IP" ] || IP=$(ip -4 addr show eth0 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')
cat <<EOF

==> Done: K2 OpenKlipper $NEW_VERSION.
    Mainsail:   http://${IP:-<printer-ip>}:4409/
    Backup:     $BACKUP
    Undo:       sh $BUILD/uninstall.sh
    Next:       set up OrcaSlicer and do a first print (docs/INSTALL.md, steps 6 and 7)
EOF
