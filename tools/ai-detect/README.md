# AI print-failure watcher (Obico + local vision model)

Watches the K2 chamber camera while a print runs and raises an alert when a
print looks like it is failing. It runs entirely on your own machine, with no
cloud service, and is meant for the external host PC
([../../docs/EXTERNAL-HOST.md](../../docs/EXTERNAL-HOST.md)), not the K2's own
board, which has no CPU to spare.

Two checks, in layers:

1. **Fast check:** [Obico](https://github.com/TheSpaghettiDetective/obico-server)'s
   open failure-detection model scores one full 1280x720 frame every 10 s
   (about 40 ms per frame on an i7-11700). Scores are smoothed and compared
   with a slow per-print baseline, so a constant false positive does not build up.
2. **Second opinion:** a local vision-language model (Qwen3-VL 4B through
   llama.cpp's `llama-server`, CPU only) looks at the same frame and answers in
   fixed JSON: `ok`, `failure` or `unclear`, plus the kind of failure and a short
   reason.
   - An Obico warning is held until the model has looked. If the model agrees,
     the alert carries its reason ("loose filament strands at the base"). If it
     says the print looks fine, the warning is dropped and only logged. If it is
     unsure, or does not answer within 5 minutes, the warning goes out on Obico
     alone, so nothing is lost.
   - The model also takes its own look every 3 minutes, to catch failures Obico
     does not see (a part knocked loose, a blob on the nozzle). It alerts on its
     own only after the same problem on two looks in a row.
   - An "empty bed" verdict never alerts on its own: flat, dark first layers on
     a textured plate can look like an empty bed.

The vision model never pauses a print. Auto-pause (Obico sustained score only)
is off by default: `K2_AUTOPAUSE=1` turns it on.

## Results so far

On 14 saved K2 frames (Obico's own alarm frames from real prints, with its red
boxes drawn on them), the 4B model was right on 12: it confirmed all three
spaghetti failures (0.93 to 0.95), passed all six frames where Obico had boxed
the logo or the toolhead, and called the empty-bed frames correctly. It missed
one real failure, lifted first-layer pads on a dark plate, which it read as an
empty bed. The 8B model was slower (about 50 s a frame against 27 s) and missed
a spaghetti frame, so the 4B is the default. Small sample: treat it as a start.

## Files

| File | What it does |
|---|---|
| `watch.py` | The service loop: Obico scoring, smoothing, the vision-model hand-off, alerts, optional pause |
| `vlm.py` | Asks the vision model about one frame; `vlm.py frame.jpg ...` classifies saved frames |
| `score.py` | Obico score for a live frame or saved images, for testing |
| `k2-detect.service`, `k2-vlm.service` | systemd units (watcher sandboxed with `DynamicUser`) |
| `k2-detect.env.example` | Settings, copy to `/etc/default/k2-detect` |

## Install (outline)

1. Camera: see `host/linux/systemd/k2-camera.service` (ustreamer on
   127.0.0.1:8080, MJPEG passthrough).
2. Obico model: clone obico-server, copy `ml_api/lib` and `ml_api/model` to
   `/opt/obico-server/ml_api/`, download the ONNX weights named in
   `ml_api/model/model-weights.onnx.url` to
   `/model_cache/ml_api/onnx/model-weights.onnx`. Venv `/opt/obico-venv` with
   `onnxruntime opencv-python-headless numpy requests`.
3. Vision model: build llama.cpp (`cmake -B build -DGGML_NATIVE=ON`, targets
   `llama-server`), download `Qwen3VL-4B-Instruct-Q4_K_M.gguf` and
   `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` from `Qwen/Qwen3-VL-4B-Instruct-GGUF`
   (Apache-2.0) and link them as `/opt/vlm/models/model.gguf` and `mmproj.gguf`.
4. Copy `watch.py`, `vlm.py`, `score.py` to `/opt/k2-detect/`, the units to
   `/etc/systemd/system/` (replace `USER`), the env file to
   `/etc/default/k2-detect`, then
   `systemctl enable --now k2-vlm k2-detect`.
5. Keep Klipper safe: the units run on cores 2-7 at the lowest priority; pin
   the K2 Klipper to its own core with `host/linux/systemd/klipper-k2.service.d/cpu.conf`.
   A real print with the watcher and the model running showed no
   "Timer too close" and a load of about 1-2 on 16 threads.

## Alerts

`watch.py` prints every alert to its log. To deliver alerts somewhere, set
`K2_ALERT_SSH=user@host`: it runs `ssh -i $CREDENTIALS_DIRECTORY/alert_key
user@host` and writes `{"title": ..., "body": ...}` to stdin. On the receiving
side, restrict that key in `authorized_keys` with
`restrict,from="<host PC IP>",command="/path/to/your/alert-script"`, so the key
can do nothing but deliver an alert.

## Logs

Under `/var/lib/private/k2-detect/`: `scores.csv` (every Obico frame),
`vlm.csv` (every model verdict, with timing), `vlm/` (the frames the model
judged), `hits/` (Obico frames with boxes) and `hits/raw/` (the same frames
without boxes, for testing models later).
