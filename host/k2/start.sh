#!/bin/sh
# K2 host-link boot (10-26): the K2 no longer runs Klipper. Klipper and
# Moonraker run on the host PC (port 7126) and reach the boards over the
# mini-USB port: USB gadget serial ports relayed to the internal UARTs.
# Started from /etc/rc.local when /mnt/UDISK/.system/boot-hostlink exists.
HL=/mnt/UDISK/k2-hostlink
G=/sys/kernel/config/usb_gadget/g1
UDC_NAME=4100000.udc-controller
OTG=/sys/devices/platform/soc@3000000/soc@3000000:usbc0@0/otg_role

echo "$(date) host-link start"

# Nothing on the K2 may hold the UARTs: stock Klipper, its linux MCU, or a
# leftover mainline klippy.
/etc/init.d/klipper stop 2>/dev/null
/etc/init.d/klipper_mcu stop 2>/dev/null
[ -f /var/run/klippy_mainline.pid ] && kill "$(cat /var/run/klippy_mainline.pid)" 2>/dev/null
sleep 2

# The camera that shared USB0 is unplugged; the port is a USB device now.
echo usb_device > $OTG 2>/dev/null

# Gadget: three serial functions, no ADB (FunctionFS will not rebind without
# adbd re-arming it, and SSH covers everything ADB did). Keep the Tina
# strings so the host's /dev/serial/by-id names stay the same.
[ -d $G ] || { echo "no gadget g1"; exit 1; }
echo "" > $G/UDC 2>/dev/null
rm -f $G/configs/c.1/ffs.adb
# Interface numbers on the host follow link order, so always relink 0,1,2:
# host if00 = ttyGS0 = main MCU, if01 = ttyGS1 = nozzle, if02 = ttyGS2 = bus.
rm -f $G/configs/c.1/gser.*
for i in 0 1 2; do
    mkdir -p $G/functions/gser.$i
    ln -s $G/functions/gser.$i $G/configs/c.1/gser.$i
done
echo 0x0525 > $G/idVendor
echo 0xa4a6 > $G/idProduct
echo $UDC_NAME > $G/UDC || echo "UDC bind failed"

# Relay, restarted if it ever exits; on CPU1, which Klipper used to have.
start-stop-daemon -K -p /var/run/k2relay.pid 2>/dev/null
start-stop-daemon -S -b -m -p /var/run/k2relay.pid -N -10 -x /bin/sh -- -c \
    "while true; do taskset 0x2 python3 -u $HL/relay.py >> /tmp/k2relay.log 2>&1; sleep 2; done"

# Touchscreen (talks to the Moonraker on the host PC, set in harkscreen.cfg).
[ -x /mnt/UDISK/harkscreen/start.sh ] && sh /mnt/UDISK/harkscreen/start.sh
echo "$(date) host-link up"
