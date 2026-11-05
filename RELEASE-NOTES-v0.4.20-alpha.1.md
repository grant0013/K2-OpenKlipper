# K2 OpenKlipper v0.4.20-alpha.1

**Camera WebRTC mode (H.264): much lighter on the printer's WiFi. Opt-in,
in testing.** MJPEG stays the default; nothing changes unless you switch.

Creality's own camera apps (`cam_app` + `webrtc_local`) have the camera
compress to H.264 and send it over WebRTC, but Mainsail and Fluidd could not
talk to them. harkcam now translates. Measured on a K2 on 5 Nov 2026:

| | MJPEG (harkcam, 10 fps) | WebRTC (H.264) |
| --- | --- | --- |
| Data per viewer | about 820 KB/s (up to 1.5 MB/s in a bright scene) | about 224 KB/s |
| Picture | 1280x720, 10 fps | 1280x720, 15 fps |
| Printer CPU | ffmpeg + harkcam | `webrtc_local` about 4% |

Thanks to nur00 for the idea and the first measurements
([#4](https://github.com/grant0013/K2-OpenKlipper/issues/4)).

## Try it

1. In `harkcam.cfg` (Mainsail: Machine > config files) set `mode: webrtc`,
   then run `sh /mnt/UDISK/harkcam/start.sh` over SSH, or reboot.
2. Mainsail: Settings > Webcams > your chamber camera: **Service**
   "WebRTC (go2rtc)", **URL Stream**
   `http://<printer IP>:8080/api/ws?src=k2`. Fluidd: the same camera type
   and URL.
3. `http://<printer IP>:8080/` is a test page that plays the stream on its
   own.

To go back: `mode: mjpeg`, run `start.sh` again, and set the camera back to
MJPEG-Streamer with `/webcam/?action=stream`.

## Good to know

- **No snapshots in this mode.** The camera sends one format at a time, so
  time-lapse, AI failure detection and anything reading
  `/webcam/?action=snapshot` need `mode: mjpeg`.
- Use the **go2rtc** camera type, not MediaMTX: `webrtc_local` only connects
  when the browser's network candidates come with its offer, and only the
  go2rtc type sends them in time. harkcam collects them for about a second,
  so the picture takes 1-2 s to appear.
- Tested in Mainsail and on the test page on a K2, with the camera apps
  started by hand. The automatic start on the normal chamber-camera port
  (through Creality's own `auto_uvc.sh`) is new: reports welcome, with
  `/tmp/harkcam.log` and
  `/mnt/UDISK/creality/userdata/log/webrtc_local.log` if it does not show.
- Creality's camera apps run on CPU0, away from Klipper's CPU, like harkcam.

## Update

Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1` in the console. Your `harkcam.cfg` and
`printer-overrides.cfg` are kept; the camera stays in MJPEG mode until you
change it.

Everything from v0.4.19-alpha.1 is included. See [CHANGELOG.md](CHANGELOG.md).
