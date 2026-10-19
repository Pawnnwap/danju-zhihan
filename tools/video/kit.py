#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频渲染用的小工具箱：字体、缓动、渐变、圆角卡片、文字精灵。

只依赖 Pillow 与 numpy；所有坐标以 1920x1080 画布为准（内容区高 972，底部 108 为字幕带）。
"""
import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
CONTENT_H = 972
BAND_H = H - CONTENT_H

INK = (10, 38, 26)
GREEN = (14, 82, 51)
GREEN_D = (8, 42, 27)
GREEN_L = (26, 122, 74)
MINT = (207, 230, 216)
PAPER = (247, 245, 238)
PAPER_2 = (238, 235, 224)
AMBER = (240, 138, 32)
AMBER_L = (248, 166, 62)
WHITE = (255, 255, 255)
GREY = (108, 118, 110)

RISK = {
    "heavy": (192, 57, 43),
    "mid": (232, 113, 10),
    "light": (232, 182, 12),
    "watch": (27, 108, 168),
}

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_NUM = r"C:\Windows\Fonts\bahnschrift.ttf"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"


@lru_cache(maxsize=256)
def font(kind, size):
    if kind == "bold":
        return ImageFont.truetype(FONT_BOLD, size, index=0)
    if kind == "reg":
        return ImageFont.truetype(FONT_REG, size, index=0)
    if kind == "mono":
        return ImageFont.truetype(FONT_MONO, size)
    f = ImageFont.truetype(FONT_NUM, size)
    try:
        f.set_variation_by_name("Bold")
    except Exception:
        pass
    return f


def clamp(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v


def ease(t):
    """smoothstep"""
    t = clamp(t)
    return t * t * (3 - 2 * t)


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = clamp(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def span(t, t0, t1):
    """t 在 [t0,t1] 内的进度（带缓动）"""
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return ease((t - t0) / (t1 - t0))


def lerp(a, b, k):
    return a + (b - a) * k


def mix(c1, c2, k):
    k = clamp(k)
    return tuple(int(round(a + (b - a) * k)) for a, b in zip(c1, c2))


def vgrad(size, top, bottom):
    w, h = size
    a = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(3):
        arr[:, :, i] = top[i] + (bottom[i] - top[i]) * a
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def radial_bg(size, inner, outer, cx=0.5, cy=0.42, radius=1.25):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = (xx / w - cx) * (w / h)
    ny = yy / h - cy
    d = np.sqrt(nx * nx + ny * ny) / radius
    d = np.clip(d, 0, 1)[..., None] ** 1.6
    arr = np.array(inner, dtype=np.float32) * (1 - d) + np.array(outer, dtype=np.float32) * d
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def grain(size, amount=6, seed=7):
    w, h = size
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, amount, (h, w, 1)).repeat(3, axis=2)
    return Image.fromarray(np.clip(128 + noise, 0, 255).astype(np.uint8), "RGB")


def add_grain(img, amount=3, seed=7):
    n = grain(img.size, amount, seed).convert(img.mode)
    return Image.blend(img, n, 0.035)


def vignette(img, strength=0.42):
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = xx / w - 0.5
    ny = yy / h - 0.5
    d = np.sqrt(nx * nx + ny * ny) / 0.72
    mask = np.clip(1 - strength * np.clip(d - 0.35, 0, 1) ** 1.5, 0, 1)[..., None]
    arr = np.asarray(img, dtype=np.float32) * mask
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def rrect(size, radius, fill, border=None, width=2, shadow=None):
    """返回带可选投影的 RGBA 圆角矩形（阴影按 radius 外扩）。"""
    w, h = size
    pad = 0 if not shadow else int(shadow[2] * 3)
    im = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    if shadow:
        dx, dy, blur = shadow
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [pad + dx, pad + dy, pad + w + dx, pad + h + dy], radius, fill=(6, 24, 16, shadow[3] if len(shadow) > 3 else 60))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(blur)))
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = [pad, pad, pad + w - 1, pad + h - 1]
    d.rounded_rectangle(box, radius, fill=fill, outline=border, width=width)
    return Image.alpha_composite(im, layer)


def paste(base, layer, xy, alpha=1.0):
    if alpha <= 0:
        return
    if alpha < 1:
        layer = layer.copy()
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer.putalpha(a)
    base.alpha_composite(layer, (int(xy[0]), int(xy[1])))


def text_img(text, kind, size, fill, tracking=0, stroke=0, stroke_fill=None, max_width=None):
    """把一行字渲染成 RGBA 精灵，便于逐帧淡入/位移而不重复排版。"""
    f = font(kind, size)
    if tracking:
        widths = [f.getlength(ch) for ch in text]
        total = sum(widths) + tracking * (len(text) - 1)
        asc, desc = f.getmetrics()
        im = Image.new("RGBA", (int(total) + 8, asc + desc + 8), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        x = 4
        for ch, w in zip(text, widths):
            d.text((x, 4), ch, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
            x += w + tracking
        return im
    l, t, r, b = f.getbbox(text, stroke_width=stroke)
    im = Image.new("RGBA", (r - l + 8, b - t + 8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - l, 4 - t), text, font=f, fill=fill, stroke_width=stroke,
                            stroke_fill=stroke_fill)
    return im


def draw_text(d, xy, text, kind, size, fill, anchor="la", tracking=0, **kw):
    f = font(kind, size)
    if tracking:
        widths = [f.getlength(ch) for ch in text]
        total = sum(widths) + tracking * (len(text) - 1)
        x = xy[0] - (total / 2 if anchor[0] == "m" else total if anchor[0] == "r" else 0)
        for ch, w in zip(text, widths):
            d.text((x, xy[1]), ch, font=f, fill=fill, anchor="l" + anchor[1], **kw)
            x += w + tracking
        return
    d.text(xy, text, font=f, fill=fill, anchor=anchor, **kw)


def wrap(text, kind, size, max_width):
    """按标点优先断行；过长的无标点片段再逐字断，避免把词切成两半。"""
    import re
    f = font(kind, size)
    lines = []
    for para in text.split("\n"):
        tokens = [t for t in re.findall(r"[^，。；：！？、]*[，。；：！？、]?", para) if t]
        cur = ""
        for tok in tokens:
            if f.getlength(cur + tok) <= max_width:
                cur += tok
                continue
            if cur:
                lines.append(cur)
            cur = ""
            if f.getlength(tok) <= max_width:
                cur = tok
                continue
            for ch in tok:
                if f.getlength(cur + ch) > max_width and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
        if cur:
            lines.append(cur)
    return lines


def bar(d, x, y, w, h, color, radius=None):
    if radius:
        d.rounded_rectangle([x, y, x + w, y + h], radius, fill=color)
    else:
        d.rectangle([x, y, x + w, y + h], fill=color)
