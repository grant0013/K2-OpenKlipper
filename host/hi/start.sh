#!/bin/sh
# Creality Hi host-link: Klipper runs on an external PC and reaches the Hi's
# boards through the USB-A port (USB0 in device mode). Three gadget serial
# ports: main MCU, nozzle MCU, and bed MCU + motor bus multiplexed (the
# controller will not bind a fourth). Runtime only: a reboot brings the Hi
# back up on stock. Stop with: sh start.sh stop
#
# ⚠ The Hi's USB-A port drives 5 V all the time: use a cable with the
# 5 V wire cut (or that pin taped) so the Hi and the PC don't feed each other.
HL=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
G=/sys/kernel/config/usb_gadget/g1
UDC_NAME=4100000.udc-controller
OTG=/sys/devices/platform/soc@3000000/soc@3000000:usbc0@0/otg_role
PID=/var/run/hirelay.pid

gadget_off() {
    echo "" > $G/UDC 2>/dev/null
    rm -f $G/configs/c.1/gser.*
    echo usb_host > $OTG 2>/dev/null
}

if [ "$1" = stop ]; then
    start-stop-daemon -K -p $PID 2>/dev/null
    pkill -f "$HL/relay.py" 2>/dev/null
    gadget_off
    echo "host-link stopped; reboot for stock, or run switch-to-mainline"
    exit 0
fi

echo "$(date) Hi host-link start"

# Nothing on the Hi may hold the UARTs or restart the stock stack: Creality's
# watchdog and apps, stock Klipper, its linux MCU, a mainline klippy.
killall Monitor master-server upgrade-server display-server 2>/dev/null
/etc/init.d/klipper stop >/dev/null 2>&1
/etc/init.d/klipper_mcu stop >/dev/null 2>&1
[ -f /var/run/klippy_mainline.pid ] && kill "$(cat /var/run/klippy_mainline.pid)" 2>/dev/null
sleep 2

# Gadget: three serial functions (no ADB: this kernel has no FunctionFS).
# Host interface numbers follow link order: if00 main, if01 nozzle,
# if02 bed + motor bus.
[ -d $G ] || { echo "no gadget g1"; exit 1; }
echo "" > $G/UDC 2>/dev/null
rm -f $G/configs/c.1/ffs.adb $G/configs/c.1/gser.*
for i in 0 1 2; do
    mkdir -p $G/functions/gser.$i
    ln -s $G/functions/gser.$i $G/configs/c.1/gser.$i
done
echo 0x0525 > $G/idVendor
echo 0xa4a6 > $G/idProduct
echo "Hark Tech" > $G/strings/0x409/manufacturer
echo "Creality Hi hostlink" > $G/strings/0x409/product
echo usb_device > $OTG
echo $UDC_NAME > $G/UDC || echo "UDC bind failed"

# Relay, restarted if it ever exits.
start-stop-daemon -K -p $PID 2>/dev/null
start-stop-daemon -S -b -m -p $PID -N -10 -x /bin/sh -- -c \
    "while true; do python3 -u $HL/relay.py >> /tmp/hirelay.log 2>&1; sleep 2; done"
sleep 2
echo "$(date) Hi host-link up: udc $(cat /sys/class/udc/*/state)"
tail -2 /tmp/hirelay.log
