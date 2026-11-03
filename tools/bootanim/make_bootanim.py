#!/usr/bin/env python3
"""Build a K2 boot animation (Creality boot-play format) from a logo.

Frames are drawn landscape (800x480) and turned 90 degrees clockwise into the
panel's 480x800 portrait framebuffer, like Creality's own frames. The result
is a folder with desc.txt, part0/ (played once) and part1/ (looped until the
touchscreen takes over): copy its contents over /usr/res/boot-play, keeping
boot.mp3 (install.sh does this for the shipped one; docs/SCREEN-CUSTOMISE.md).

Usage:
  make_bootanim.py LOGO.png OUTDIR [--font F.ttf] [--bg #RRGGBB]
                   [--accent #RRGGBB] [--title "K2 OpenKlipper"]
                   [--subtitle "harktech.co.uk"] [--seconds 3] [--loop 2.4]

The logo is masked to a soft disc, so a square image with a dark background
(like a round badge on black) sits cleanly on the animation's own background.

Copyright (C) 2026  Hark Tech

This file may be distributed under the terms of the GNU GPLv3 license.
"""
import argparse
import math
import os
import random

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H, FPS = 800, 480, 30
LOGO = 300          # badge diameter at full size
CX, CY = W // 2, H // 2 - 36


def hex_rgb(text):
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    t = max(0., min(1., t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ease_out(t):
    t = max(0., min(1., t))
    return 1 - (1 - t) ** 3


def soft_logo(path, size):
    """Logo with its square edges faded out, so it sits on any background."""
    im = Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    inset = size * 0.03
    d.ellipse((inset, inset, size - inset, size - inset), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size * 0.02))
    out = Image.new("RGBA", (size, size))
    out.paste(im, (0, 0), mask)
    return out


class Builder:
    def __init__(self, args):
        self.bg = hex_rgb(args.bg)
        self.accent = hex_rgb(args.accent)
        self.logo = soft_logo(args.logo, LOGO)
        self.title, self.subtitle = args.title, args.subtitle
        if args.font:
            self.font_t = ImageFont.truetype(args.font, 30)
            self.font_s = ImageFont.truetype(args.font, 20)
        else:
            self.font_t = self.font_s = ImageFont.load_default()
        self.backdrop = self._backdrop()
        self.traces = self._traces()

    # --- static layers ---
    def _backdrop(self):
        """Vignette: a little lighter behind the badge, darker at the edges."""
        small = Image.new("RGB", (80, 48), self.bg)
        d = ImageDraw.Draw(small)
        lift = mix(self.bg, self.accent, 0.10)
        d.ellipse((12, -4, 68, 44), fill=lift)
        return small.filter(ImageFilter.GaussianBlur(12)).resize(
            (W, H), Image.BILINEAR)

    def _traces(self):
        """Circuit traces radiating from the badge: right-angled lines that
        end in a pad. Each one has its own 'light-up' moment."""
        rnd = random.Random(7)
        traces = []
        for k in range(26):
            angle = rnd.uniform(0, 2 * math.pi)
            r0 = LOGO / 2 + 18
            x, y = CX + r0 * math.cos(angle), CY + r0 * math.sin(angle)
            pts = [(x, y)]
            horizontal = rnd.random() < 0.5
            for seg in range(rnd.randint(2, 4)):
                length = rnd.uniform(40, 140)
                if horizontal:
                    x += length * (1 if math.cos(angle) >= 0 else -1)
                else:
                    y += length * (1 if math.sin(angle) >= 0 else -1)
                pts.append((x, y))
                horizontal = not horizontal
                if not (0 < x < W and 0 < y < H):
                    break
            traces.append({"pts": pts, "t": rnd.uniform(0.15, 0.9),
                           "pad": rnd.random() < 0.7})
        return traces

    # --- a frame ---
    def frame(self, scale=1.0, bright=1.0, ring=0.0, ring_glow=0.0,
              traces=0.0, title=0.0, subtitle=0.0, orbit=None, halo=0.0,
              sweep=None):
        img = self.backdrop.copy()
        d = ImageDraw.Draw(img, "RGBA")
        dim = mix(self.bg, self.accent, 0.30)
        lit = mix(self.bg, self.accent, 0.85)
        # Circuit traces light up one by one, then stay
        if traces > 0:
            for tr in self.traces:
                a = ease_out((traces - tr["t"]) / 0.12)
                if a <= 0:
                    continue
                col = mix(dim, lit, 0.4 * a) + (int(255 * min(1., a + .3)),)
                d.line(tr["pts"], fill=col, width=2)
                if tr["pad"]:
                    x, y = tr["pts"][-1]
                    d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=col)
        # Halo behind the badge: the accent, blurred, breathing
        if halo > 0:
            size = int(LOGO * 1.5)
            glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            gd.ellipse((size * .15, size * .15, size * .85, size * .85),
                       fill=self.accent + (int(110 * halo),))
            glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
            img.paste(glow, (CX - size // 2, CY - size // 2), glow)
            d = ImageDraw.Draw(img, "RGBA")
        # The badge
        size = max(2, int(LOGO * scale))
        lg = self.logo.resize((size, size), Image.LANCZOS)
        if bright != 1.0:
            rgb = ImageEnhance.Brightness(lg.convert("RGB")).enhance(bright)
            rgb.putalpha(lg.split()[3])
            lg = rgb
        img.paste(lg, (CX - size // 2, CY - size // 2), lg)
        d = ImageDraw.Draw(img, "RGBA")
        # Ring: a light runs round the rim and leaves a glowing ring behind
        r = size // 2 + 10
        box = (CX - r, CY - r, CX + r, CY + r)
        if ring_glow > 0:
            d.ellipse(box, outline=self.accent + (int(150 * ring_glow),),
                      width=3)
        if 0 < ring <= 1:
            end = -90 + 360 * ring
            for w, a in ((9, 40), (5, 110), (2, 255)):
                d.arc(box, end - 40, end, fill=self.accent + (a,), width=w)
            # the bright head
            hx = CX + r * math.cos(math.radians(end))
            hy = CY + r * math.sin(math.radians(end))
            d.ellipse((hx - 5, hy - 5, hx + 5, hy + 5),
                      fill=(255, 255, 255, 230))
        if orbit is not None:
            ang = math.radians(-90 + 360 * orbit)
            hx, hy = CX + r * math.cos(ang), CY + r * math.sin(ang)
            for rad, a in ((14, 40), (8, 110), (4, 255)):
                d.ellipse((hx - rad, hy - rad, hx + rad, hy + rad),
                          fill=mix(self.accent, (255, 255, 255), .5) + (a,))
        # Text
        ty = CY + LOGO // 2 + 46
        if title > 0:
            d.text((CX, ty), self.title, font=self.font_t,
                   fill=mix(self.bg, (238, 242, 246), title), anchor="mm")
        if subtitle > 0:
            d.text((CX, ty + 36), self.subtitle, font=self.font_s,
                   fill=mix(self.bg, self.accent, subtitle), anchor="mm")
        if sweep is not None:
            y, x0, x1 = H - 30, CX - 150, CX + 150
            d.line((x0, y, x1, y), fill=mix(self.bg, self.accent, .25),
                   width=2)
            cx = x0 + (x1 - x0) * sweep
            for rad, a in ((34, .18), (16, .45), (6, 1.0)):
                d.line((max(x0, cx - rad), y, min(x1, cx + rad), y),
                       fill=mix(self.bg, self.accent, a), width=3)
        return img.transpose(Image.ROTATE_270)

    # --- the two parts ---
    def intro(self, seconds):
        frames = []
        n = int(seconds * FPS)
        flicker = [0.0, 0.0, 0.5, 0.1, 0.0, 0.8, 0.3, 0.9, 0.55, 1.0, 0.75,
                   1.0]
        for i in range(n):
            t = i / (n - 1.)
            grow = ease_out(t / 0.55)
            bright = (flicker[i] if i < len(flicker)
                      else min(1.0, 0.75 + 0.25 * grow))
            ring = (t - 0.10) / 0.45 if 0.10 <= t <= 0.55 else 0.
            frames.append(self.frame(
                scale=0.72 + 0.28 * grow, bright=max(bright, 0.02),
                ring=ring, ring_glow=ease_out((t - 0.50) / 0.2) * 0.8,
                traces=t, title=ease_out((t - 0.45) / 0.3),
                subtitle=ease_out((t - 0.65) / 0.3),
                halo=0.6 * ease_out((t - 0.3) / 0.5)))
        return frames

    def loop(self, seconds):
        frames = []
        n = int(seconds * FPS)
        for i in range(n):
            t = i / float(n)
            breathe = 0.5 + 0.5 * math.sin(2 * math.pi * t - math.pi / 2)
            frames.append(self.frame(
                bright=1.0 + 0.06 * breathe, ring_glow=0.5 + 0.3 * breathe,
                traces=1.0, title=1.0, subtitle=1.0, orbit=t,
                halo=0.45 + 0.35 * breathe, sweep=breathe))
        return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logo")
    ap.add_argument("out")
    ap.add_argument("--font")
    ap.add_argument("--bg", default="#04070f")
    ap.add_argument("--accent", default="#0059DE")
    ap.add_argument("--title", default="K2 OpenKlipper")
    ap.add_argument("--subtitle", default="harktech.co.uk")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--loop", type=float, default=2.4)
    ap.add_argument("--quality", type=int, default=86)
    args = ap.parse_args()
    b = Builder(args)
    parts = {"part0": b.intro(args.seconds), "part1": b.loop(args.loop)}
    for name, frames in parts.items():
        folder = os.path.join(args.out, name)
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            os.remove(os.path.join(folder, f))
        for i, f in enumerate(frames):
            f.save(os.path.join(folder, "pic_%03d.jpg" % i),
                   quality=args.quality, optimize=True)
    with open(os.path.join(args.out, "desc.txt"), "w") as f:
        f.write("480 800 %d\np 1 0 part0\np 0 0 part1\n" % FPS)
    # Contact sheet (landscape) to look at
    picks = [parts["part0"][3], parts["part0"][len(parts["part0"]) // 3],
             parts["part0"][-1], parts["part1"][len(parts["part1"]) // 3]]
    sheet = Image.new("RGB", (W * 2, H * 2))
    for n, f in enumerate(picks):
        sheet.paste(f.transpose(Image.ROTATE_90),
                    ((n % 2) * W, (n // 2) * H))
    sheet.save(os.path.join(args.out, "sheet.png"))
    size = sum(os.path.getsize(os.path.join(args.out, p, f))
               for p in parts for f in os.listdir(os.path.join(args.out, p)))
    print("ok: %d + %d frames, %.1f MB" % (len(parts["part0"]),
                                           len(parts["part1"]), size / 1e6))


if __name__ == "__main__":
    main()
