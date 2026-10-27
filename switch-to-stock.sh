#!/bin/sh
# Hand the camera back to Creality cam_app/webrtc
[ -f /tmp/harkcam.pid ] && kill $(cat /tmp/harkcam.pid) 2>/dev/null
/etc/init.d/webrtc start 2>/dev/null
# Hand the panel back to the stock UI
[ -f /tmp/harkscreen.pid ] && kill $(cat /tmp/harkscreen.pid) 2>/dev/null
pidof display-server >/dev/null || HOME=/root /usr/bin/display-server >/dev/null 2>&1 &
# Switch K2 back from mainline instance to stock Creality Klipper.
[ -f /var/run/klippy_mainline.pid ] && kill "$(cat /var/run/klippy_mainline.pid)" 2>/dev/null
pkill -f "/mnt/UDISK/.*/klippy/klippy.py" 2>/dev/null
rm -f /var/run/klippy_mainline.pid
sleep 1
rm -f /tmp/klippy_uds
/etc/init.d/klipper start
echo "stock Creality klipper restarted."
echo "NOTE: homing can be flaky after a klipper restart - if G28 fails, full printer reboot."
