#!/bin/sh
# Run by hi-reconnect.service when the Hi's USB host-link (re)appears: the Hi
# was rebooted, replugged, or its start.sh has just run, so the Klipper here
# has lost (or never had) its boards. Firmware-restart it until it is ready;
# the first attempt can land before the boards answer.
M=http://127.0.0.1:${HI_MOONRAKER_PORT:-7126}
state() {
    curl -s -m 5 $M/printer/info | python3 -c \
        "import sys,json;print(json.load(sys.stdin)['result']['state'])" \
        2>/dev/null
}
sleep 8
for attempt in 1 2 3 4; do
    s=$(state)
    echo "attempt $attempt: klipper state=$s"
    [ "$s" = "ready" ] && exit 0
    curl -s -m 10 -X POST $M/printer/firmware_restart > /dev/null
    for i in $(seq 1 15); do
        sleep 2
        s=$(state)
        [ "$s" = "ready" ] && { echo "ready after attempt $attempt"; exit 0; }
        [ "$s" = "error" ] || [ "$s" = "shutdown" ] && break
    done
done
echo "Hi Klipper did not come back; needs a look"
exit 1
