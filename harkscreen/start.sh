#!/bin/sh
# Start Hark Screen on CPU0 (klippy is pinned to CPU1), replacing any copy.
PIDF=/tmp/harkscreen.pid
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
cd /tmp
taskset 0x1 nice -n 10 /usr/share/klippy-env/bin/python /mnt/UDISK/harkscreen/harkscreen.py >/tmp/harkscreen.log 2>&1 &
echo $! > $PIDF
echo "harkscreen started, pid $(cat $PIDF)"
