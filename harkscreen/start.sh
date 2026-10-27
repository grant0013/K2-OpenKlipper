#!/bin/sh
# Start Hark Screen on CPU0 (klippy is pinned to CPU1), replacing any copy.
PIDF=/tmp/harkscreen.pid
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
# Creality's screen app draws on the same panel and takes CPU from Klipper.
# It has nothing to show without Creality's master-server, so stop it;
# switch-to-stock.sh and uninstall.sh start it again.
killall display-server 2>/dev/null
cd /tmp
taskset 0x1 nice -n 10 /usr/share/klippy-env/bin/python /mnt/UDISK/harkscreen/harkscreen.py >/tmp/harkscreen.log 2>&1 &
echo $! > $PIDF
echo "harkscreen started, pid $(cat $PIDF)"
