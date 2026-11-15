#!/bin/sh
# Start Hark Screen on CPU0 (klippy is pinned to CPU1), replacing any copy.
PIDF=/tmp/harkscreen.pid
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
# Creality's screen app draws on the same panel and takes CPU from Klipper.
# It has nothing to show without Creality's master-server, so stop it;
# switch-to-stock.sh and uninstall.sh start it again.
killall display-server 2>/dev/null
DIR=/mnt/UDISK/harkscreen
APP=$DIR/harkscreen.py
# The Creality Hi's 2.8" panel (240 px wide framebuffer) gets the compact
# 320x240 screen
grep -q '^240,' /sys/class/graphics/fb0/virtual_size 2>/dev/null \
    && APP=$DIR/harkscreen_hi.py
# The Hi's firmware has no taskset
PIN=""
command -v taskset >/dev/null 2>&1 && PIN="taskset 0x1"
cd /tmp
$PIN nice -n 10 /usr/share/klippy-env/bin/python $APP >/tmp/harkscreen.log 2>&1 &
echo $! > $PIDF
echo "harkscreen started ($(basename $APP)), pid $(cat $PIDF)"
