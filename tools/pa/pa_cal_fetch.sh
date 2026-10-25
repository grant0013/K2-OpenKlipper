#!/bin/sh
# Fetch a PA_CAL_RUN log from the K2 and analyse it. Usage: pa_cal_fetch.sh <name>
# K2_SSH=root@<printer> overrides the ssh target; plots need numpy + matplotlib
NAME=${1:-pa_cal_D}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=~/K2_backups/pa_logs
mkdir -p "$OUT"
scp -q "${K2_SSH:-root@k2.local}:/mnt/UDISK/printer_data_mainline/config/pa_logs/${NAME}_*.csv" "$OUT/" || exit 1
${PYTHON:-python3} "$HERE/analyze_pa.py" "$OUT" "$NAME" --plot "$OUT/${NAME}.png"
