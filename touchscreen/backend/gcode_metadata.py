"""Bounded fallback for embedded Creality/Orca G-code metadata and PNG previews."""

import base64
import math
import re
import urllib.parse
import urllib.request

WINDOW = 512 * 1024


def parse(text):
    fields = {}
    for key, value in re.findall(r"(?m)^;\s*([^;=\r\n]+?)\s*=\s*([^\r\n]*)", text):
        fields[key.strip().lower()] = value.strip()
    result = {}
    aliases = {
        "filament_type": ("filament_type", "default_filament_type"),
        "filament_colors": (
            "filament_colour",
            "filament_color",
            "default_filament_colour",
        ),
    }
    for target, keys in aliases.items():
        for key in keys:
            if fields.get(key):
                result[target] = fields[key]
                break
    weight = fields.get("filament used [g]")
    if weight:
        try:
            values = [float(x.strip()) for x in re.split(r"[;,]", weight)]
            if all(math.isfinite(x) and x >= 0 for x in values):
                result.update(
                    filament_weights=values, filament_weight_total=sum(values)
                )
        except ValueError:
            pass
    layers = re.search(
        r"(?im)^;\s*(?:total layer number:|total layers count\s*=)\s*(\d+)", text
    )
    if layers:
        result["layer_count"] = int(layers[1])
    duration = fields.get("estimated printing time (normal mode)", "")
    seconds = sum(
        float(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", duration)
    )
    if seconds:
        result["estimated_time"] = seconds
    images = []
    for match in re.finditer(
        r";\s*thumbnail begin (\d+)x(\d+) \d+\s*\n(.*?)\n;\s*thumbnail end",
        text,
        re.DOTALL,
    ):
        if int(match[1]) * int(match[2]) > 4096 * 4096:
            continue
        payload = re.sub(r"[;\s]", "", match[3])
        try:
            data = base64.b64decode(payload, validate=True)
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                images.append((int(match[1]) * int(match[2]), data))
        except ValueError:
            continue
    return result, max(images, default=(0, None), key=lambda x: x[0])[1]


def read(base, path):
    if path.startswith("/") or any(p in ("", ".", "..") for p in path.split("/")):
        raise ValueError("Invalid G-code path")
    url = base + "/server/files/gcodes/" + urllib.parse.quote(path, safe="/")
    chunks = []
    for byte_range in (f"bytes=0-{WINDOW - 1}", f"bytes=-{WINDOW}"):
        request = urllib.request.Request(url, headers={"Range": byte_range})
        with urllib.request.urlopen(request, timeout=10) as response:
            # A server ignoring Range must not cause a whole-file download.
            data = response.read(WINDOW)
            if chunks and response.status != 206:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
    return parse("\n".join(chunks))
