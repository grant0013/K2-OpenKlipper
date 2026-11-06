#!/bin/sh
# Hark Cam: the chamber camera for Mainsail on :8080 (/webcam/).
# Size, frame rate, viewer cap and mode: printer_data_mainline/config/harkcam.cfg
# (edit it in Mainsail, then run this script again or reboot).
#   mode: mjpeg   harkcam takes the camera from Creality's apps and serves
#                 MJPEG (stream + snapshots: time-lapse, AI watcher)
#   mode: webrtc  Creality's cam_app + webrtc_local send H.264 over WebRTC
#                 (far lighter on WiFi); harkcam translates for Mainsail's
#                 and Fluidd's "WebRTC (go2rtc)" camera type. No snapshots.
PIDF=/tmp/harkcam.pid
CFG=/mnt/UDISK/printer_data_mainline/config/harkcam.cfg
MODE=$(sed -n 's/^[[:space:]]*mode[[:space:]]*:[[:space:]]*\([a-zA-Z]*\).*/\1/p' "$CFG" 2>/dev/null | tail -1 | tr 'A-Z' 'a-z')
# Creality's cloud streamer stays off either way (it needs master-server)
/etc/init.d/webrtc stop 2>/dev/null
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
cam_alive() { [ -n "$(pidof cam_app)" ] && [ -n "$(pidof webrtc_local)" ]; }
cam_720() {
    p=$(pidof cam_app | cut -d' ' -f1)
    [ -n "$p" ] && tr '\0' ' ' </proc/$p/cmdline | grep -q ' -w 1280 '
}
cam_stop() {
    for n in cam_app webrtc_local; do p=$(pidof $n); [ -n "$p" ] && kill $p; done
    n=0
    while [ -n "$(pidof cam_app)" ] && [ $n -lt 5 ]; do sleep 1; n=$((n + 1)); done
    # started again too soon, cam_app exits and the camera stays stuck
    # (uvcvideo: Failed to resubmit video URB) until a reboot
    sleep 3
}
cam_start() {
    # Creality's own start: its crop settings and the version file cam_app
    # needs, as the camera's hotplug does
    [ -z "$(pidof cam_app)" ] && ACTION=add MDEV=$MAIN /usr/bin/auto_uvc.sh >/dev/null 2>&1
    sleep 2
    # It picks 1080p on some models, and every viewer is its own session in
    # webrtc_local: three at 1080p filled the core that serves the WiFi and
    # both MCU serial lines (GitHub issue #4)
    cam_720 && return
    cam_stop
    start-stop-daemon -S -b -m -p /var/run/$MAIN.pid --exec /usr/bin/cam_app \
        -- -i /dev/v4l/by-id/$MAIN -t 0 -w 1280 -h 720 -f 15 -c
    sleep 1
    start-stop-daemon -S -b -m -p /var/run/${MAIN}_webrtc_local.pid \
        --exec /usr/bin/webrtc_local
}
if [ "$MODE" = webrtc ]; then
    # harkcam's ffmpeg has the camera: wait for it to let go
    p=$(pidof ffmpeg)
    if [ -n "$p" ]; then
        kill $p
        n=0
        while [ -n "$(pidof ffmpeg)" ] && [ $n -lt 5 ]; do sleep 1; n=$((n + 1)); done
        sleep 3
    fi
    MAIN=$(ls /dev/v4l/by-id 2>/dev/null | grep '^main-video' | head -1)
    MAIN=${MAIN:-main-video0}
    tries=0
    while [ $tries -lt 4 ]; do
        cam_alive && cam_720 && break
        [ $tries -gt 0 ] && cam_stop
        cam_start
        sleep 5
        tries=$((tries + 1))
    done
    cam_alive && cam_720 || echo "harkcam: cam_app or webrtc_local did not stay up"
    # Klipper has CPU1: keep Creality's camera apps, every thread (new ones
    # inherit it), on CPU0 with the rest of harkcam
    for n in cam_app webrtc_local; do
        for p in $(pidof $n); do
            for t in /proc/$p/task/*; do taskset -p 0x1 ${t##*/} >/dev/null 2>&1; done
        done
    done
else
    for n in cam_app webrtc_local; do p=$(pidof $n); [ -n "$p" ] && kill $p; done
fi
sleep 1
cd /tmp
taskset 0x1 nice -n 10 /usr/bin/python3 /mnt/UDISK/harkcam/harkcam.py 8080 /dev/video0 >/tmp/harkcam.log 2>&1 &
echo $! > $PIDF
echo "harkcam started (${MODE:-mjpeg}), pid $(cat $PIDF)"
