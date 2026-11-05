#!/bin/sh
# Hark Cam: the chamber camera for Mainsail on :8080 (/webcam/).
# Size, frame rate, viewer cap and mode: printer_data_mainline/config/harkcam.cfg
# (edit it in Mainsail, then run this script again or reboot).
#   mode: mjpeg   harkcam takes the camera from Creality's apps and serves
#                 MJPEG (stream + snapshots: time-lapse, AI watcher)
#   mode: webrtc  Creality's cam_app + webrtc_local send H.264 over WebRTC
#                 (far lighter on WiFi); harkcam translates for Mainsail's
#                 and Fluidd's "WebRTC (MediaMTX)" camera type. No snapshots.
PIDF=/tmp/harkcam.pid
CFG=/mnt/UDISK/printer_data_mainline/config/harkcam.cfg
MODE=$(sed -n 's/^[[:space:]]*mode[[:space:]]*:[[:space:]]*\([a-zA-Z]*\).*/\1/p' "$CFG" 2>/dev/null | tail -1 | tr 'A-Z' 'a-z')
# Creality's cloud streamer stays off either way (it needs master-server)
/etc/init.d/webrtc stop 2>/dev/null
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
if [ "$MODE" = webrtc ]; then
    # Creality's own start for the camera (its crop settings, cam_app,
    # webrtc_local), as the camera's hotplug does, unless already running
    if [ -z "$(pidof cam_app)" ] || [ -z "$(pidof webrtc_local)" ]; then
        DEV=$(ls /dev/v4l/by-id 2>/dev/null | grep '^main-video' | head -1)
        ACTION=add MDEV=${DEV:-main-video0} /usr/bin/auto_uvc.sh >/dev/null 2>&1
    fi
else
    for n in cam_app webrtc_local; do p=$(pidof $n); [ -n "$p" ] && kill $p; done
fi
sleep 1
cd /tmp
taskset 0x1 nice -n 10 /usr/bin/python3 /mnt/UDISK/harkcam/harkcam.py 8080 /dev/video0 >/tmp/harkcam.log 2>&1 &
echo $! > $PIDF
echo "harkcam started (${MODE:-mjpeg}), pid $(cat $PIDF)"
