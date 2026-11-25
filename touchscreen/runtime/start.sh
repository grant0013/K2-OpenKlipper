#!/bin/sh
set -eu
ROOT=/mnt/UDISK/openklipper-screen
CONF=${1:-/mnt/UDISK/printer_data_mainline/config/native-screen.cfg}
PYTHON=${OKL_SCREEN_PYTHON:-/usr/share/klippy-env/bin/python}
[ -x "$ROOT/openklipper-screen" ] && [ -r "$ROOT/assets/nav-house-56.png" ]     || { echo "Native touchscreen installation is incomplete" >&2; exit 1; }
for name in openklipper-screen openklipper-screen-service; do
    if [ -f /tmp/$name.pid ]; then
        PID=$(cat /tmp/$name.pid)
        case "$PID" in ''|*[!0-9]*) ;; *) kill "$PID" 2>/dev/null || true ;; esac
    fi
done
killall boot-play display-server 2>/dev/null || true
[ ! -x /usr/bin/screen.sh ] || /usr/bin/screen.sh on >/dev/null 2>&1 || true
nice -n 15 "$PYTHON" "$ROOT/backend/service.py" --config "$CONF" >/tmp/openklipper-screen-service.log 2>&1 &
echo $! >/tmp/openklipper-screen-service.pid
# Parse only the display settings; no printer limits or model constants live here.
"$PYTHON" "$ROOT/launch.py" --config "$CONF" --root "$ROOT" </dev/null >>/tmp/openklipper-screen.log 2>&1 &
echo $! >/tmp/openklipper-screen.pid
echo "Native touchscreen started, pid $(cat /tmp/openklipper-screen.pid)"
