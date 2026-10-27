#!/bin/sh
# Run by k2-reconnect.service when the K2 USB link (re)appears: the K2 rebooted
# or was replugged, so the K2 Klipper here lost its boards. Firmware-restart it
# until it is ready again (the first attempt can land before the boards answer).
M=http://127.0.0.1:7126
state() { curl -s -m 5 $M/printer/info | python3 -c "import sys,json;print(json.load(sys.stdin)[\"result\"][\"state\"])" 2>/dev/null; }
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
echo "K2 Klipper did not come back; needs a look"
exit 1
