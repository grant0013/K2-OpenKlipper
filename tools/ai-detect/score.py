#!/opt/obico-venv/bin/python
"""Score K2 camera frames with Obico's failure-detection model (CPU, local).
Usage: score.py [image.jpg ...]   (no args = grab a live frame from the K2 harkcam)"""
import sys, time, urllib.request
sys.path.insert(0, "/opt/obico-server/ml_api")
import cv2, numpy as np
from lib.detection_model import load_net, detect

CAM = "http://127.0.0.1:8080/snapshot"
MD = "/opt/obico-server/ml_api/model/"
THRESH = 0.08  # same box threshold Obico's ml_api uses

net = load_net(MD + "model.cfg", MD + "model.meta", "/model_cache/ml_api/onnx/model-weights.onnx")

def frame(src):
    if src:
        return cv2.imread(src), src
    data = urllib.request.urlopen(CAM, timeout=10).read()
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), "live"

for src in (sys.argv[1:] or [None]):
    img, name = frame(src)
    t = time.time()
    dets = detect(net, img, thresh=THRESH)
    ms = (time.time() - t) * 1000
    total = sum(d[1] for d in dets)
    print(f"{name}: {len(dets)} boxes, sum conf {total:.2f}, max {max([d[1] for d in dets], default=0):.2f}, {ms:.0f} ms")
    for label, conf, (x, y, w, h) in dets:
        print(f"   {label} {conf:.2f} at x={x:.0f} y={y:.0f} w={w:.0f} h={h:.0f}")
        cv2.rectangle(img, (int(x - w / 2), int(y - h / 2)), (int(x + w / 2), int(y + h / 2)), (0, 0, 255), 2)
        cv2.putText(img, f"{conf:.2f}", (int(x - w / 2), int(y - h / 2) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite("/tmp/k2-detect-last.jpg", img)
