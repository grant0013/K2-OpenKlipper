# Touchscreen (Hark Screen) and camera

With Creality's `master-server` disabled, the stock touchscreen has nothing to
talk to. `harkscreen/harkscreen.py` is a replacement UI that draws straight to
the framebuffer and talks to Moonraker.

## Hark Screen

- Landscape layout (the panel is portrait, 480x800; the UI rotates 90°).
- Pages: status (progress, temperatures, ETA from the slicer's M73), CFS
  slots, controls, machine, Wi-Fi.
- CFS slot sheet: edit the filament record, Load & purge, Purge, Unload, and
  **Calibrate PA** for the loaded slot.
- Wi-Fi selection with an on-screen keyboard, through `wpa_supplicant`'s
  control socket, with rollback if the new network fails.
- Themeable: `harkscreen.cfg` sets the name, logo, colours, font, page order,
  sleep and timezone. Changes are picked up within a second.
- Light on the K2's 2-core CPU: redraws only on change, partial blits for
  animations, pinned to CPU0 at nice 10. About 6% CPU when idle.

`install.sh` sets it up: it copies `harkscreen/` to `/mnt/UDISK/harkscreen/`
and puts `harkscreen.cfg` (from the `.example`) and `klipper-logo.png` in the
Klipper config folder. The switch script starts it (`start.sh`);
`switch-to-stock.sh` stops it.

The default look is Klipper's logo and colours. To change the name, logo,
colours, font or pages, see **[customising the screen](SCREEN-CUSTOMISE.md)**.
`harkscreen/themes/hark-tech.cfg` is a second example theme.

`tools/bootanim/make_bootanim.py` builds a custom boot animation for
`/usr/res/boot-play`. Back up the stock one first.

## Camera (harkcam)

The chamber camera is a USB UVC device (`/dev/video0`, MJPEG/YUYV/H264).
Creality serves it through its own WebRTC apps, which Mainsail cannot use.
`harkcam/harkcam.py` stops those and serves the camera's own MJPEG stream
with no re-encoding:

- `ffmpeg -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 15
  -i /dev/video0 -c:v copy -f mjpeg pipe:1`, split into frames
- `http://<printer>:8080/?action=stream` and `/?action=snapshot`, the same
  URLs as mjpg-streamer, so Mainsail, Obico-style AI detectors and the like
  work as-is
- about 5% CPU

nginx proxy for the Mainsail server block:

```nginx
location /webcam/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

Register the camera in Moonraker (`POST /server/webcams/item` on Creality's
older Moonraker). Mainsail 2.18 hides webcams without an `enabled` field,
which that Moonraker does not send; `system/moonraker-webcam-defaults.patch`
adds the defaults.
