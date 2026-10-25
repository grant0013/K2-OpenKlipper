"""Build a K2 boot animation (boot-play format) from a logo.

Frames are drawn landscape (800x480) and turned 90 degrees clockwise into the
panel's 480x800 portrait framebuffer, like Creality's own frames.
Usage: make_bootanim.py LOGO.png OUTDIR [font.ttf] [bg #RRGGBB] [accent #RRGGBB]
"""
import math, os, sys
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

logo_path, out = sys.argv[1], sys.argv[2]
font_path = sys.argv[3] if len(sys.argv) > 3 else None
bg = tuple(int(sys.argv[4].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) \
    if len(sys.argv) > 4 else (2, 4, 10)
accent = tuple(int(sys.argv[5].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) \
    if len(sys.argv) > 5 else (26, 178, 255)
W, H, FPS = 800, 480, 30
LOGO = 340


def soft_logo(path, size):
    """Logo with its square edges faded out, so it sits on any background."""
    im = Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    inset = size * 0.12
    d.ellipse((inset * 0.2, inset * 0.2, size - inset * 0.2, size - inset * 0.2),
              fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size * 0.08))
    out = Image.new("RGBA", (size, size))
    out.paste(im, (0, 0), mask)
    return out


logo = soft_logo(logo_path, LOGO)
font = ImageFont.truetype(font_path, 26) if font_path else \
    ImageFont.load_default()


def frame(scale=1.0, bright=1.0, text_alpha=0.0, sweep=None):
    img = Image.new("RGB", (W, H), bg)
    size = int(LOGO * scale)
    lg = logo.resize((size, size), Image.LANCZOS)
    if bright != 1.0:
        rgb = ImageEnhance.Brightness(lg.convert("RGB")).enhance(bright)
        rgb.putalpha(lg.split()[3])
        lg = rgb
    img.paste(lg, ((W - size) // 2, (H - size) // 2 - 30), lg)
    d = ImageDraw.Draw(img)
    if text_alpha > 0:
        c = tuple(int(bg[i] + (accent[i] - bg[i]) * text_alpha)
                  for i in range(3))
        d.text((W // 2, H - 70), "Starting", font=font, fill=c, anchor="mm")
    if sweep is not None:
        # A thin light travelling along a track under the logo
        y, x0, x1 = H - 40, W // 2 - 160, W // 2 + 160
        d.line((x0, y, x1, y), fill=tuple(int(v * 0.25 + bg[i] * 0.75)
                                          for i, v in enumerate(accent)),
               width=2)
        cx = x0 + (x1 - x0) * sweep
        for r, a in ((30, 0.15), (16, 0.35), (6, 1.0)):
            col = tuple(int(bg[i] + (accent[i] - bg[i]) * a) for i in range(3))
            d.line((max(x0, cx - r), y, min(x1, cx + r), y), fill=col,
                   width=2 if r > 6 else 4)
    return img.transpose(Image.ROTATE_270)


def save(folder, frames):
    os.makedirs(os.path.join(out, folder), exist_ok=True)
    for i, f in enumerate(frames):
        f.save(os.path.join(out, folder, "pic_%03d.jpg" % i), quality=88)


# part0: neon flicker-on and ease in (2 s)
flicker = [0.0, 0.0, 0.6, 0.1, 0.0, 0.8, 0.3, 0.9, 0.5, 1.0, 0.7, 1.0]
part0 = []
for i in range(60):
    t = i / 59.0
    ease = 1 - (1 - t) ** 3
    bright = flicker[i] if i < len(flicker) else min(1.0, 0.7 + 0.3 * ease)
    part0.append(frame(scale=0.88 + 0.12 * ease, bright=max(bright, 0.02),
                       text_alpha=max(0.0, (t - 0.6) / 0.4)))
# part1: gentle glow pulse and a sweeping light, loops seamlessly (2 s)
part1 = []
for i in range(60):
    t = i / 60.0
    pulse = 1.0 + 0.08 * math.sin(2 * math.pi * t)
    part1.append(frame(bright=pulse, text_alpha=1.0,
                       sweep=(math.sin(2 * math.pi * t - math.pi / 2) + 1) / 2))
save("part0", part0)
save("part1", part1)
with open(os.path.join(out, "desc.txt"), "w") as f:
    f.write("480 800 %d\np 1 0 part0\np 0 0 part1\n" % FPS)
# Contact sheet in landscape for checking
sheet = Image.new("RGB", (W * 4 // 2, H * 2 // 2))
for n, f in enumerate([part0[3], part0[20], part0[59], part1[15]]):
    sheet.paste(f.transpose(Image.ROTATE_90).resize((W // 2, H // 2)),
                ((n % 4) * W // 2, 0))
sheet.save(os.path.join(out, "sheet.png"))
print("ok", len(part0), len(part1))
