#!/bin/sh
# Hark Cam: take the USB camera from Creality cam_app/webrtc, serve MJPEG on :8080
PIDF=/tmp/harkcam.pid
/etc/init.d/webrtc stop 2>/dev/null
for n in cam_app webrtc_local; do p=$(pidof $n); [ -n "$p" ] && kill $p; done
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
sleep 1
cd /tmp
taskset 0x1 nice -n 10 /usr/bin/python3 /mnt/UDISK/harkcam/harkcam.py 8080 /dev/video0 1280x720 15 >/tmp/harkcam.log 2>&1 &
echo $! > $PIDF
echo "harkcam started, pid $(cat $PIDF)"
