#!/bin/sh
# Switch K2 back from mainline instance to stock Creality Klipper.
[ -f /var/run/klippy_mainline.pid ] && kill "$(cat /var/run/klippy_mainline.pid)" 2>/dev/null
pkill -f "/mnt/UDISK/.*/klippy/klippy.py" 2>/dev/null
rm -f /var/run/klippy_mainline.pid
sleep 1
rm -f /tmp/klippy_uds
/etc/init.d/klipper start
echo "stock Creality klipper restarted."
echo "NOTE: homing can be flaky after a klipper restart - if G28 fails, full printer reboot."
