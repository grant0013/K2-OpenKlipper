#!/bin/sh
# Creality Hi host-link, host PC side: Klipper + Moonraker for the Hi run here
# and reach its boards through the Hi's USB-A port (see host/hi/start.sh for
# the Hi side).
#
#   host.sh setup    config folder ~/printer_data_hi (keeps existing edits)
#   host.sh start    demux + Klipper + Moonraker (port 7126) in the background
#   host.sh stop
#   host.sh status
#
# Needs once, with sudo: the udev rule that gives the Hi's gadget ports a
# usbserial driver (printed by 'setup' when it is missing).
OKL=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
DATA=${HI_DATA:-$HOME/printer_data_hi}
KPY=${KLIPPY_PY:-$HOME/klippy-env/bin/python}
MPY=${MOONRAKER_PY:-$HOME/moonraker-env/bin/python}
MOONRAKER=${MOONRAKER_DIR:-$HOME/moonraker}
BYID=/dev/serial/by-id/usb-Hark_Tech_Creality_Hi_hostlink_20080411
RULE=/etc/udev/rules.d/99-hi-hostlink.rules

running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

case "$1" in
setup)
    mkdir -p "$DATA/config" "$DATA/logs" "$DATA/gcodes" "$DATA/comms"
    {
        echo "# MANAGED BY K2 OpenKlipper host.sh: replaced on every setup."
        echo "# Do not edit; put changes in printer-overrides.cfg."
        echo
        cat "$OKL/config/mainline_stage2_hi.cfg"
    } > "$DATA/config/k2-openklipper.cfg"
    sed "s#@HOME@#$HOME#g" "$OKL/host/hi/hostlink.cfg" > "$DATA/config/hostlink.cfg"
    [ -f "$DATA/config/printer-overrides.cfg" ] || \
        echo "# This printer's own settings (PID, PA, shaper ...)" \
            > "$DATA/config/printer-overrides.cfg"
    [ -f "$DATA/config/printer.cfg" ] || printf '%s\n' \
        "# Creality Hi through the USB host-link." \
        "[include k2-openklipper.cfg]" \
        "[include printer-overrides.cfg]" \
        "[include hostlink.cfg]" > "$DATA/config/printer.cfg"
    [ -f "$DATA/config/moonraker.conf" ] || \
        sed "s#@HOME@#$HOME#g" "$OKL/host/hi/moonraker.conf" \
            > "$DATA/config/moonraker.conf"
    # Key for HI_PA_CAL, which runs Creality's pa_compute on the Hi over SSH
    if [ ! -f "$HOME/.ssh/id_hi_hostlink" ]; then
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        ssh-keygen -q -t ed25519 -N "" -f "$HOME/.ssh/id_hi_hostlink" \
            -C "$(hostname) hi-hostlink"
        echo "NEW key for HI_PA_CAL: append this line to the Hi's /etc/dropbear/authorized_keys:"
        cat "$HOME/.ssh/id_hi_hostlink.pub"
    fi
    echo "config ready in $DATA/config"
    if [ ! -f "$RULE" ]; then
        echo "MISSING udev rule. Run once:"
        echo "  sudo cp $OKL/host/hi/99-hi-hostlink.rules $RULE && sudo udevadm control --reload && sudo udevadm trigger"
    fi
    ;;
start)
    if ! running "$DATA/comms/demux.pid"; then
        nohup python3 -u "$OKL/host/hi/demux.py" "$BYID-if02-port0" \
            "$DATA/comms/hi-bed" "$DATA/comms/hi-bus" \
            >> "$DATA/logs/demux.log" 2>&1 &
        echo $! > "$DATA/comms/demux.pid"
        sleep 1
    fi
    if ! running "$DATA/comms/klippy.pid"; then
        nohup "$KPY" "$OKL/klippy/klippy.py" "$DATA/config/printer.cfg" \
            -l "$DATA/logs/klippy.log" -a "$DATA/comms/klippy.sock" \
            > /dev/null 2>&1 &
        echo $! > "$DATA/comms/klippy.pid"
    fi
    if ! running "$DATA/comms/moonraker.pid"; then
        nohup "$MPY" "$MOONRAKER/moonraker/moonraker.py" -d "$DATA" \
            > /dev/null 2>&1 &
        echo $! > "$DATA/comms/moonraker.pid"
    fi
    "$0" status
    ;;
stop)
    for p in moonraker klippy demux; do
        running "$DATA/comms/$p.pid" && kill "$(cat "$DATA/comms/$p.pid")"
        rm -f "$DATA/comms/$p.pid"
    done
    echo stopped
    ;;
status)
    for p in demux klippy moonraker; do
        if running "$DATA/comms/$p.pid"; then s=running; else s=stopped; fi
        echo "$p: $s"
    done
    ls "$BYID"* 2>/dev/null || echo "Hi USB ports: not present"
    ;;
*)
    sed -n '2,13p' "$0"
    ;;
esac
