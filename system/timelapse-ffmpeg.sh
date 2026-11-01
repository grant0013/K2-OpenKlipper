#!/bin/sh
# ffmpeg for moonraker-timelapse at the lowest priority: the K2 has two slow
# cores, and a video renders while Klipper may already be busy again
exec nice -n 19 /usr/bin/ffmpeg "$@"
