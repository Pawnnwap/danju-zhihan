#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""丹橘知旱 · 演示片分镜渲染（1920x1080 / 30fps）。

所有画面元素都由本文件与 kit.py 现画，UI 截图来自 tools/capture_assets.py 抓取的
docs/frames/v2/*.png（真机 2x，无后期伪造）。数字全部来自仓库内可复算来源：
  · 2022 夏逐日气温 —— tools/fixtures/liangshuihe_2016_2025.json
  · 30 天 / 13 天 / 40.9℃ / 提前 3 天 —— 产品「2022 年夏季热害回放」实拍
  · 442 天 / 37 组 / 44 个 / 6 道闸门 —— README 与 tools/GATES.json
"""
import glob
import json
import math
import tempfile
from pathlib import Path

import sys

from PIL import Image, ImageDraw, ImageFilter

from kit import (AMBER, AMBER_L, BAND_H, CONTENT_H, GREEN, GREEN_D, GREEN_L, GREY, H, INK,
                 MINT, PAPER, PAPER_2, RISK, W, WHITE, add_grain, bar, clamp, draw_text, ease,
                 ease_in_out, ease_out, font, lerp, mix, paste, radial_bg, rrect, span, text_img,
                 vgrad, vignette, wrap)

ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS = ROOT / "docs" / "frames" / "v2"


# ---------------------------------------------------------------- 素材

def load_asset(name, scale=1.0):
    im = Image.open(ASSETS / (name + ".png")).convert("RGBA")
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    return im


def round_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


def rounded(im, radius):
    im = im.convert("RGBA")
    im.putalpha(round_mask(im.size, radius))
    return im


def shadowed(layer, blur=34, dy=18, alpha=70):
    pad = blur * 2
    base = Image.new("RGBA", (layer.width + pad * 2, layer.height + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    box = Image.new("RGBA", layer.size, (0, 0, 0, alpha))
    sh.paste(box, (pad, pad + dy))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    base = Image.alpha_composite(base, sh)
    base.alpha_composite(layer, (pad, pad))
    return base, pad


def browser_window(content, width, chrome_h=46, radius=16, url="pawnnwap.github.io/danju-zhihan"):
    inner_w = width
    scale = inner_w / content.width
    body = content.resize((inner_w, int(content.height * scale)), Image.LANCZOS)
    total_h = chrome_h + body.height
    im = Image.new("RGBA", (inner_w, total_h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, inner_w, chrome_h], fill=(243, 241, 233))
    d.line([0, chrome_h, inner_w, chrome_h], fill=(222, 219, 208), width=1)
    for i, c in enumerate([(224, 100, 90), (232, 184, 74), (111, 191, 115)]):
        d.ellipse([18 + i * 24, chrome_h / 2 - 6, 30 + i * 24, chrome_h / 2 + 6], fill=c)
    pill_w = min(560, inner_w - 220)
    px = (inner_w - pill_w) / 2
    d.rounded_rectangle([px, 11, px + pill_w, chrome_h - 11], 12, fill=(231, 228, 219))
    d.ellipse([px + 14, chrome_h / 2 - 4, px + 22, chrome_h / 2 + 4], fill=(120, 150, 128))
    draw_text(d, (px + 32, chrome_h / 2), url, "reg", 17, (92, 104, 95), anchor="lm")
    im.paste(body, (0, chrome_h))
    return rounded(im, radius)


def phone(content, width=340, bezel=10, radius=52):
    h = int(content.height * width / content.width)
    body = content.resize((width, h), Image.LANCZOS)
    outer = Image.new("RGBA", (width + bezel * 2, h + bezel * 2), (16, 22, 19, 255))
    outer.paste(body, (bezel, bezel))
    d = ImageDraw.Draw(outer)
    d.rounded_rectangle([0, 0, outer.width - 1, outer.height - 1], radius, outline=(38, 44, 40, 255), width=2)
    # 顶部听筒
    d.rounded_rectangle([outer.width / 2 - 34, bezel + 6, outer.width / 2 + 34, bezel + 16], 6, fill=(16, 22, 19, 255))
    return rounded(outer, radius)


def callout(im, target, label_xy, text, alpha=1.0, anchor="left", color=AMBER):
    """在图上画一条带端点圆点的引线 + 药丸标签。"""
    if alpha <= 0:
        return
    d = ImageDraw.Draw(im)
    tw = font("bold", 21).getlength(text)
    pad_x, hgt = 16, 38
    w = tw + pad_x * 2
    lx, ly = label_xy
    if anchor == "left":
        box = [lx, ly, lx + w, ly + hgt]
        start = (lx + w, ly + hgt / 2)
    else:
        box = [lx - w, ly, lx, ly + hgt]
        start = (lx - w, ly + hgt / 2)
    col = tuple(list(color) + [int(255 * alpha)])
    d.line([start, target], fill=col, width=2)
    d.ellipse([target[0] - 5, target[1] - 5, target[0] + 5, target[1] + 5], fill=col)
    layer = Image.new("RGBA", (int(w) + 8, hgt + 8), (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    dl.rounded_rectangle([4, 4, 4 + w, 4 + hgt], hgt / 2, fill=col)
    draw_text(dl, (4 + pad_x, 4 + hgt / 2), text, "bold", 21, (255, 255, 255, int(255 * alpha)), anchor="lm")
    paste(im, layer, (box[0] - 4, box[1] - 4))


# ---------------------------------------------------------------- 背景

def paper_bg():
    im = Image.new("RGB", (W, CONTENT_H), PAPER)
    d = ImageDraw.Draw(im)
    for x in range(0, W, 64):
        d.line([x, 0, x, CONTENT_H], fill=(233, 230, 220), width=1)
    for y in range(0, CONTENT_H, 64):
        d.line([0, y, W, y], fill=(233, 230, 220), width=1)
    # 右下角淡青色色块，压住网格
    d.ellipse([W - 700, CONTENT_H - 420, W + 260, CONTENT_H + 320], fill=(236, 241, 230))
    im = im.filter(ImageFilter.GaussianBlur(0.4))
    return im


def dark_bg():
    im = radial_bg((W, CONTENT_H), (18, 86, 58), (6, 30, 20), cx=0.72, cy=0.18, radius=1.15)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(16):
        r = 380 + i * 92
        d.arc([1460 - r, -260 - r, 1460 + r, -260 + r], 40, 140, fill=(255, 255, 255, 9), width=1)
    im = Image.alpha_composite(im.convert("RGBA"), layer)
    im = vignette(im.convert("RGB"), 0.5).convert("RGBA")
    return add_grain(im, 4).convert("RGBA")


# ---------------------------------------------------------------- 分镜

class Scenes:
    def __init__(self):
        self.bg_paper = paper_bg().convert("RGBA")
        self.bg_dark = dark_bg()
        self.ui_site = load_asset("ui_site")
        self.ui_now = load_asset("ui_now")
        self.ui_calendar = load_asset("ui_calendar")
        self.ui_advice = load_asset("ui_advice")
        self.ui_replay = load_asset("ui_replay")
        self.ui_qa = load_asset("ui_qa")
        self.mobile_top = load_asset("mobile_top")
        self.mobile_now = load_asset("mobile_now")
        self.temps = self._load_temps()
        # —— 逐帧只做拷贝/裁剪的重资产，全部预渲染 ——
        self.mark96 = self._mark(96)
        self.mark76 = self._mark(76)
        self.chart_full = self._build_chart(900, 546)
        self.product = self._build_product()
        self.evidence_card = self._build_card(self.ui_replay.crop((30, 380, 1830, 712)), 1100, 12)
        self.qa_card = self._build_card(self.ui_qa.crop((0, 30, 1840, 655)), 1080, 12)
        # 真机录屏（tools/record_live.py 产出），有就替掉产品幕的静态截图
        self.live_frames = sorted(glob.glob(str(Path(tempfile.gettempdir()) / "livecap" / "frames" / "*.jpg")))

    @staticmethod
    def _build_card(shot, width, radius=12):
        shot = shot.resize((width, int(shot.height * width / shot.width)), Image.LANCZOS)
        return shadowed(rounded(shot, radius), blur=26, dy=14, alpha=50)

    def _build_product(self):
        cards = [self.ui_site, self.ui_now, self.ui_calendar]
        win_w, chrome, view_h = 1200, 44, 620
        scale = win_w / cards[0].width
        gap = 22
        total_h = sum(int(c.height * scale) for c in cards) + gap * (len(cards) - 1) + 40
        canvas = Image.new("RGBA", (win_w, total_h), (247, 245, 238, 255))
        cy = 20
        tops = []
        for c in cards:
            ch = int(c.height * scale)
            canvas.paste(c.resize((win_w, ch), Image.LANCZOS), (0, cy))
            tops.append(cy)
            cy += ch + gap

        chrome_im = Image.new("RGBA", (win_w, chrome), (243, 241, 233, 255))
        wd = ImageDraw.Draw(chrome_im)
        wd.line([0, chrome - 1, win_w, chrome - 1], fill=(222, 219, 208), width=1)
        for i, c in enumerate([(224, 100, 90), (232, 184, 74), (111, 191, 115)]):
            wd.ellipse([18 + i * 24, chrome / 2 - 6, 30 + i * 24, chrome / 2 + 6], fill=c)
        pill_w = 500
        px = (win_w - pill_w) / 2
        wd.rounded_rectangle([px, 11, px + pill_w, chrome - 11], 12, fill=(231, 228, 219))
        wd.ellipse([px + 14, chrome / 2 - 4, px + 22, chrome / 2 + 4], fill=(120, 150, 128))
        draw_text(wd, (px + 32, chrome / 2), "pawnnwap.github.io/danju-zhihan", "reg", 17,
                  (92, 104, 95), anchor="lm")

        mask = round_mask((win_w, chrome + view_h), 16)
        pad = 96
        shadow = Image.new("RGBA", (win_w + pad * 2, chrome + view_h + pad * 2), (0, 0, 0, 0))
        sh = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [pad, pad + 18, pad + win_w, pad + chrome + view_h + 18], 16, fill=(6, 24, 16, 80))
        shadow = sh.filter(ImageFilter.GaussianBlur(34))
        return dict(canvas=canvas, chrome=chrome_im, mask=mask, shadow=shadow, pad=pad,
                    win_w=win_w, view_h=view_h, chrome_h=chrome, total_h=total_h,
                    now_top=tops[1], cal_top=tops[2])

    @staticmethod
    def _load_temps():
        p = ROOT / "tools" / "fixtures" / "liangshuihe_2016_2025.json"
        daily = json.loads(p.read_text(encoding="utf-8"))["daily"]
        rows = [(t, v) for t, v in zip(daily["time"], daily["temperature_2m_max"])
                if "2022-06-01" <= t <= "2022-09-30" and v is not None]
        return rows

    # -- 共用版式 --------------------------------------------------

    def kicker(self, im, num, text, dark=False, t=0.0, t0=0.0):
        p = span(t, t0, t0 + 0.55)
        if p <= 0:
            return
        d = ImageDraw.Draw(im)
        col = AMBER if not dark else AMBER_L
        txt = f"{num}  {text}"
        layer = text_img(txt, "bold", 21, col, tracking=3)
        paste(im, layer, (120, 74 - (1 - p) * 12), p)
        d.line([120, 116, 120 + 54 * p, 116], fill=col, width=3)

    def headline(self, im, text, y=134, size=62, color=INK, t=0.0, t0=0.15, x=120):
        lines = text.split("\n")
        for i, line in enumerate(lines):
            p = span(t, t0 + i * 0.10, t0 + 0.55 + i * 0.10)
            if p <= 0:
                continue
            layer = text_img(line, "bold", size, color)
            paste(im, layer, (x, y + i * (size + 18) - (1 - p) * 22), p)

    def caption_note(self, im, text, y, color=GREY, t=0.0, t0=0.0, size=24, x=120, alpha=1.0):
        p = span(t, t0, t0 + 0.6) * alpha
        if p <= 0:
            return
        layer = text_img(text, "reg", size, color)
        paste(im, layer, (x, y - (1 - p) * 10), p)

    # -- 01 片头 ---------------------------------------------------

    def s_title(self, t, dur):
        im = self.bg_dark.copy()
        d = ImageDraw.Draw(im)
        # 大橙色弧线
        glow = Image.new("RGBA", (760, 760), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for rr, aa in ((360, 10), (280, 12), (200, 14)):
            gd.ellipse([380 - rr, 380 - rr, 380 + rr, 380 + rr], fill=(240, 150, 60, aa))
        glow = glow.filter(ImageFilter.GaussianBlur(70))
        im.alpha_composite(glow, (1180, 260))

        # 首帧即完整可见（平台常拿第一帧当封面），只做位移不上透明度
        p = 1.0
        rise = (1 - span(t, 0.0, 0.7)) * 18
        mark = self.mark96
        paste(im, mark, (120, 226 + rise), 1.0)

        for i, (txt, kind, size, col, y, delay) in enumerate([
            ("丹橘知旱", "bold", 136, WHITE, 330, 0.02),
            ("丹江口库区柑橘园气候决策助手", "bold", 44, MINT, 552, 0.16),
            ("One Place · One Climate Problem · One AI Solution", "bold", 24, (150, 196, 168), 630, 0.28),
            ("AI4Climate 全球青年气候解决方案挑战赛 · 团队 沧浪舟 · 2026", "reg", 22, (140, 180, 156), 752, 0.40),
        ]):
            pp = 1.0
            rise_t = (1 - span(t, delay, delay + 0.7)) * 26
            layer = text_img(txt, kind, size, col, tracking=2 if i == 2 else 0)
            paste(im, layer, (120, y + rise_t), 1.0)

        pp = span(t, 0.30, 0.85)
        d.rectangle([120, 700, 120 + 140 * ease_out(pp), 703], fill=AMBER)
        return im

    def _mark(self, size):
        s = size * 2
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([18, 26, s - 18, s - 8], fill=AMBER)
        d.ellipse([40, 46, s - 46, s - 30], fill=(248, 166, 62))
        d.ellipse([s / 2 - 22, 8, s / 2 + 22, 56], fill=(34, 132, 84))
        return im.resize((size, size), Image.LANCZOS)

    # -- 02 问题 ---------------------------------------------------

    def s_problem(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "01", "真实问题", t=t, t0=0.05)
        self.headline(im, "一个库区，\n一场极端高温", y=150, size=66, t=t, t0=0.18)

        body = "丹江口水库是南水北调中线的水源区。库周 30 万亩柑橘，是 10 万果农的主要收入。"
        p = span(t, 0.55, 1.15)
        for i, line in enumerate(wrap(body, "reg", 27, 660)):
            layer = text_img(line, "reg", 27, (74, 88, 78))
            paste(im, layer, (120, 372 + i * 44 - (1 - p) * 12), p)

        stats = [("30", "万亩", "库周柑橘种植面积"), ("10", "万", "依赖柑橘的果农"),
                 ("44.6", "℃", "2022 十堰竹山，湖北纪录")]
        for i, (num, unit, label) in enumerate(stats):
            pp = span(t, 0.85 + i * 0.14, 1.35 + i * 0.14)
            if pp <= 0:
                continue
            y = 520 + i * 108
            k = ease_out(clamp((t - (0.85 + i * 0.14)) / 0.9))
            val = num if num == "44.6" else str(int(round(int(num) * k)))
            layer = text_img(val, "num", 66, INK)
            paste(im, layer, (120, y - (1 - pp) * 18), pp)
            wnum = font("num", 66).getlength(val)
            draw_text(ImageDraw.Draw(im), (120 + wnum + 8, y + 34), unit, "bold", 24, GREEN, anchor="lb")
            draw_text(ImageDraw.Draw(im), (120 + wnum + 8 + font("bold", 24).getlength(unit) + 22, y + 32),
                      label, "reg", 23, GREY, anchor="lb")

        # 图表
        pp = span(t, 0.5, 1.1)
        if pp > 0:
            rv = span(t, 0.9, 2.6)
            if rv >= 1.0:
                chart = self.chart_full
            else:
                full = self.chart_full
                cut = int(full.width * ease(rv))
                chart = Image.new("RGBA", full.size, (0, 0, 0, 0))
                chart.alpha_composite(full.crop((0, 0, cut, full.height)), (0, 0))
            paste(im, chart, (890, 190 - (1 - pp) * 16), pp)
        self.caption_note(im, "数据：ERA5-Land 再分析 · 格点 32.58°N / 111.38°E · 凉水河镇",
                          905, color=(128, 138, 128), t=t, t0=1.8, size=19, x=920)
        return im

    def _build_chart(self, w, h):
        card, pad = shadowed(rrect((w, h), 20, (255, 255, 255, 255), border=(228, 226, 216, 255), width=1),
                             blur=26, dy=14, alpha=38)
        im = Image.new("RGBA", card.size, (0, 0, 0, 0))
        im.alpha_composite(card, (0, 0))
        ox, oy = pad, pad
        d = ImageDraw.Draw(im)
        d.text((ox + 36, oy + 30), "2022 年夏季 · 逐日最高气温", font=font("bold", 25), fill=INK)
        d.text((ox + 36, oy + 68), "凉水河镇格点，6 月 1 日 — 9 月 30 日", font=font("reg", 19), fill=(132, 142, 132))

        left, right = ox + 58, ox + w - 122
        top, bot = oy + 138, oy + h - 74
        ymin, ymax = 18, 45

        def X(i):
            return left + (right - left) * i / (len(self.temps) - 1)

        def Y(v):
            return bot - (v - ymin) / (ymax - ymin) * (bot - top)

        for gv in (20, 25, 30, 35, 40, 45):
            y = Y(gv)
            d.line([left, y, right, y], fill=(238, 236, 228), width=1)
            d.text((left - 14, y), str(gv), font=font("num", 17), fill=(150, 160, 150), anchor="rm")

        # 阈值线
        for v, col, lab in ((35, (232, 182, 12), "35℃"), (39, (192, 57, 43), "39℃")):
            y = Y(v)
            for x in range(left, right, 12):
                d.line([x, y, x + 6, y], fill=col, width=2)
            d.text((right + 10, y), lab, font=font("bold", 16), fill=col, anchor="lm")

        pts = [(X(i), Y(v)) for i, (_, v) in enumerate(self.temps)]
        # 曲线下方渐变
        mask = Image.new("L", im.size, 0)
        dm = ImageDraw.Draw(mask)
        dm.polygon(pts + [(right, bot), (left, bot)], fill=90)
        grad = vgrad(im.size, (255, 205, 140), (255, 244, 228)).convert("RGBA")
        grad.putalpha(mask.filter(ImageFilter.GaussianBlur(2)))
        im.alpha_composite(grad)
        # 逐段着色
        for i in range(len(pts) - 1):
            v = self.temps[i][1]
            col = (192, 57, 43) if v >= 39 else (232, 113, 10) if v >= 35 else (26, 122, 74)
            d.line([pts[i], pts[i + 1]], fill=col, width=3)
        # 标注
        idx4 = next(i for i, (s, _) in enumerate(self.temps) if s == "2022-08-04")
        idx7 = next(i for i, (s, _) in enumerate(self.temps) if s == "2022-08-07")
        # 08-04：竖直引线拉到曲线下方，避免压住曲线；08-07：放在点上方
        x4, y4 = pts[idx4]
        d.line([x4, y4 + 10, x4, y4 + 64], fill=(232, 113, 10, 190), width=2)
        f4 = font("bold", 18)
        lab4 = "08-04 · 39.2℃"
        w4 = f4.getlength(lab4)
        d.rounded_rectangle([x4 - w4 / 2 - 8, y4 + 66, x4 + w4 / 2 + 8, y4 + 100], 7, fill=(255, 255, 255, 240))
        d.text((x4, y4 + 83), lab4, font=f4, fill=(232, 113, 10), anchor="mm")
        for x, y in ((x4, y4), pts[idx7]):
            d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=WHITE, outline=(192, 57, 43), width=3)
        x7, y7 = pts[idx7]
        f7 = font("bold", 18)
        lab7 = "08-07 · 40.9℃"
        w7 = f7.getlength(lab7)
        d.rounded_rectangle([x7 + 10, y7 - 42, x7 + 10 + w7 + 16, y7 - 8], 7, fill=(255, 255, 255, 240))
        d.text((x7 + 18, y7 - 25), lab7, font=f7, fill=(192, 57, 43), anchor="lm")
        # x 轴
        for idx, lab in ((0, "06-01"), (30, "07-01"), (61, "08-01"), (92, "09-01"), (121, "09-30")):
            x = X(idx)
            d.line([x, bot, x, bot + 5], fill=(200, 198, 188), width=1)
            d.text((x, bot + 12), lab, font=font("num", 16), fill=(150, 160, 150), anchor="ma")

        return im

    # -- 03 方案 ---------------------------------------------------

    def s_solution(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "02", "方案", t=t, t0=0.05)
        self.headline(im, "三步，从天气到农事动作", y=146, size=60, t=t, t0=0.16)
        self.caption_note(im, "不需要装 App，也不需要自己看天气图。", 246, t=t, t0=0.5, size=25)

        steps = [
            ("01", "选园块", ["库区 7 个乡镇预置", "也可输入自定义坐标", "记录灌溉与挂果条件"]),
            ("02", "判物候 · 算风险", ["按柑橘物候规则表", "逐日算四类风险等级", "每条判级都写出依据"]),
            ("03", "给动作 · 可追问", ["建议按等级和物候生成", "标注农技规则来源", "支持自然语言提问"]),
        ]
        y = 360
        # 连接线
        p = span(t, 0.5, 1.4)
        d = ImageDraw.Draw(im)
        d.line([168, y + 36, 168 + (1236 - 168) * ease_in_out(p), y + 36], fill=(206, 214, 200), width=2)

        for i, (num, title, lines) in enumerate(steps):
            x = 120 + i * 540
            pp = span(t, 0.55 + i * 0.18, 1.15 + i * 0.18)
            if pp <= 0:
                continue
            dy = (1 - pp) * 24
            d.ellipse([x, y + dy, x + 72, y + 72 + dy], fill=GREEN if i < 2 else AMBER)
            d.text((x + 36, y + 36 + dy), num, font=font("num", 30), fill=WHITE, anchor="mm")
            layer = text_img(title, "bold", 38, INK)
            paste(im, layer, (x, y + 100 + dy), pp)
            for j, line in enumerate(lines):
                pl = span(t, 0.7 + i * 0.18 + j * 0.06, 1.25 + i * 0.18 + j * 0.06)
                lay = text_img("· " + line, "reg", 24, (84, 98, 88))
                paste(im, lay, (x, y + 168 + j * 42 + dy), pl)

        # 四类风险
        pp = span(t, 1.35, 1.95)
        if pp > 0:
            d.text((120, 700 + (1 - pp) * 14), "四类风险", font=font("bold", 22), fill=GREEN)
            chips = [("高温热害", RISK["heavy"]), ("干旱", RISK["mid"]),
                     ("花期·幼果低温", RISK["watch"]), ("越冬冻害", (60, 110, 170))]
            x = 250
            for i, (name, col) in enumerate(chips):
                pc = span(t, 1.45 + i * 0.1, 1.9 + i * 0.1)
                f = font("bold", 22)
                w = f.getlength(name) + 52
                box = Image.new("RGBA", (int(w), 44), (0, 0, 0, 0))
                dd = ImageDraw.Draw(box)
                dd.rounded_rectangle([0, 0, w - 1, 43], 22, fill=(255, 255, 255, 235), outline=col, width=2)
                dd.ellipse([16, 15, 30, 29], fill=col)
                dd.text((40, 21), name, font=f, fill=INK, anchor="lm")
                paste(im, box, (x, 694 - (1 - pc) * 10), pc)
                x += w + 18

        # 底部引擎说明
        pp = span(t, 1.8, 2.4)
        if pp > 0:
            box = rrect((1680, 96), 14, (236, 243, 234, 255), border=(210, 224, 208, 255), width=1)
            paste(im, box, (120, 780 - (1 - pp) * 12), pp)
            d = ImageDraw.Draw(im)
            d.text((152, 812), "确定性规则引擎", font=font("bold", 24), fill=GREEN)
            d.text((152, 844), "公开气象数据 + 物候表 + 阈值，逐日判级，可离线复算",
                   font=font("reg", 22), fill=(78, 92, 82))
            d.text((1180, 812), "DeepSeek 解释层（可选）", font=font("bold", 24), fill=(120, 92, 40))
            d.text((1180, 844), "只做转译与问答，不参与数值预测", font=font("reg", 22), fill=(78, 92, 82))
            d.text((120, 884), "规则可审计 · 判级可复算 · 建议可追溯", font=font("bold", 22), fill=GREEN)
        return im

    # -- 04 产品 ---------------------------------------------------

    def s_product(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "04", "产品", t=t, t0=0.05)
        self.headline(im, "手机打开即用，一屏看完风险", y=142, size=56, t=t, t0=0.15)

        P = self.product
        win_w, chrome, view_h, total_h = P["win_w"], P["chrome_h"], P["view_h"], P["total_h"]
        wx, wy = 90, 250
        # 落位滚动：停在看板顶部（不落在半行按钮上），落点随卡片实际高度计算
        pan = (P["now_top"] - 20) * ease_in_out(span(t, 2.9, 4.3))
        pan = min(pan, max(0, total_h - view_h))

        pp = span(t, 0.5, 1.15)
        win = P["shadow"].copy()
        body = Image.new("RGBA", (win_w, chrome + view_h), (255, 255, 255, 255))
        body.paste(P["chrome"], (0, 0))
        if self.live_frames:
            idx = min(len(self.live_frames) - 1, max(0, int(t * 30)))
            shot = Image.open(self.live_frames[idx]).convert("RGB")
            shot = shot.resize((win_w, int(win_w * shot.height / shot.width)), Image.LANCZOS)
            top = max(0, (shot.height - view_h) // 2)
            body.paste(shot.crop((0, top, win_w, top + view_h)), (0, chrome))
        else:
            body.paste(P["canvas"].crop((0, int(pan), win_w, int(pan) + view_h)), (0, chrome))
        body.putalpha(P["mask"])
        win.alpha_composite(body, (P["pad"], P["pad"]))
        paste(im, win, (wx - P["pad"] - (1 - pp) * 26, wy - P["pad"] - (1 - pp) * 14), pp)

        # 手机（另一张实拍：看板竖排）
        pp = span(t, 0.3, 0.95)
        if pp > 0:
            if not hasattr(self, "_phone_card"):
                self._phone_card = shadowed(phone(self.mobile_now, width=300), blur=30, dy=18, alpha=85)
            ph, php = self._phone_card
            paste(im, ph, (1372 - php - (1 - pp) * 22, 250 - php - (1 - pp) * 16), pp)

        # 标注：药丸落在卡片标题行的留白处，引线指向对应内容
        if self.live_frames:
            self.caption_note(im, "真机录屏 · 2026-10-19 · pawnnwap.github.io/danju-zhihan",
                              936, color=(128, 138, 128), t=t, t0=1.2, size=19, x=90)
            return im

        c1 = span(t, 4.6, 5.1)
        tgt1 = P["now_top"] + 200
        callout(im, (wx + 520, wy + chrome + tgt1 - int(pan)),
                (wx + 760, wy + chrome + P["now_top"] + 22 - int(pan)), "四类风险", c1, "left")
        c2 = span(t, 7.6, 8.1)
        tgt2 = P["cal_top"] + 130
        callout(im, (wx + 520, wy + chrome + tgt2 - int(pan)),
                (wx + 760, wy + chrome + P["cal_top"] + 22 - int(pan)), "90 天日历", c2, "left")

        self.caption_note(im, "真机截图 · 2026-10-19 · pawnnwap.github.io/danju-zhihan",
                          936, color=(128, 138, 128), t=t, t0=1.2, size=19, x=90)
        return im

    # -- 05 证据 ---------------------------------------------------

    def s_evidence(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "07", "证据", t=t, t0=0.05)
        self.headline(im, "2022 年真实数据，逐日复算", y=140, size=54, t=t, t0=0.15)

        metrics = [("30", "天", "≥35℃ 高温日", INK), ("13", "天", "热害「重」级", (192, 57, 43)),
                   ("40.9", "℃", "极端最高（08-07）", (192, 57, 43)), ("3", "天", "首个重级提示早于峰值", AMBER)]
        for i, (num, unit, label, col) in enumerate(metrics):
            pp = span(t, 0.45 + i * 0.12, 0.95 + i * 0.12)
            if pp <= 0:
                continue
            x = 120 + i * 420
            k = ease_out(clamp((t - (0.45 + i * 0.12)) / 0.8))
            val = num if "." in num else str(int(round(int(num) * k)))
            layer = text_img(val, "num", 92, col)
            paste(im, layer, (x, 246 - (1 - pp) * 16), pp)
            wn = font("num", 92).getlength(val)
            d = ImageDraw.Draw(im)
            d.text((x + wn + 10, 322), unit, font=font("bold", 26), fill=col)
            d.text((x, 360), label, font=font("reg", 22), fill=(96, 108, 98))
            d.line([x, 232, x + 366, 232], fill=(222, 219, 208), width=1)

        # 逐日判级依据（实拍截图）
        pp = span(t, 1.0, 1.6)
        if pp > 0:
            card, pad = self.evidence_card
            paste(im, card, (120 - pad - (1 - pp) * 18, 500 - pad - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            d.text((120, 468), "回放输出的逐日判级依据（产品实拍）", font=font("bold", 21), fill=GREY)

        # 右侧：3 天示意
        pp = span(t, 1.5, 2.1)
        if pp > 0:
            x0 = 1300
            d = ImageDraw.Draw(im)
            d.text((x0, 470), "从「提示」到「峰值」", font=font("bold", 21), fill=GREY)
            pts = [("08-04", "39.2℃", (232, 113, 10), "首个「重」级"),
                   ("08-05", "40.0℃", (232, 113, 10), ""),
                   ("08-06", "40.2℃", (232, 113, 10), ""),
                   ("08-07", "40.9℃", (192, 57, 43), "峰值")]
            xs = [x0 + 30 + i * 112 for i in range(4)]
            ay = 700
            d.line([xs[0], ay, xs[0] + (xs[-1] - xs[0]) * ease_out(pp), ay], fill=(214, 211, 200), width=2)
            for i, (day, temp, col, tag) in enumerate(pts):
                pc = span(t, 1.55 + i * 0.09, 1.9 + i * 0.09)
                if pc <= 0:
                    continue
                x = xs[i]
                d.ellipse([x - 9, ay - 9, x + 9, ay + 9], fill=col)
                d.text((x, ay - 42), day, font=font("num", 19), fill=(90, 102, 92), anchor="ma")
                d.text((x, ay + 24), temp, font=font("bold", 19), fill=col, anchor="ma")
                if tag:
                    d.text((x, ay + 56), tag, font=font("reg", 16), fill=GREY, anchor="ma")
            # 括线
            by = 596
            d.line([xs[0], by, xs[-1], by], fill=AMBER, width=2)
            d.line([xs[0], by, xs[0], by + 12], fill=AMBER, width=2)
            d.line([xs[-1], by, xs[-1], by + 12], fill=AMBER, width=2)
            d.rounded_rectangle([(xs[0] + xs[-1]) / 2 - 58, by - 40, (xs[0] + xs[-1]) / 2 + 58, by - 4],
                                19, fill=AMBER)
            d.text(((xs[0] + xs[-1]) / 2, by - 22), "提前 3 天", font=font("bold", 21), fill=WHITE, anchor="mm")
            d.text((x0, 812), "首个「重」级提示出现在峰值前 3 天", font=font("reg", 19), fill=(108, 120, 110))

        self.caption_note(im, "复算口径：喂数带 35 天 warm-up，与产品运行时一致；详见 docs/03-验证计划.md",
                          905, color=(128, 138, 128), t=t, t0=2.2, size=19)
        return im

    # -- 06 AI 分工 ------------------------------------------------

    def s_ai(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "08", "AI 分工", t=t, t0=0.05)
        self.headline(im, "算，交给规则；说，交给模型", y=140, size=56, t=t, t0=0.15)

        pp = span(t, 0.35, 1.0)
        if pp > 0:
            card, pad = self.qa_card
            paste(im, card, (120 - pad - (1 - pp) * 18, 296 - pad - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            d.text((120, 264), "问答助手 · 内置规则引擎回答（不需要 Key）", font=font("bold", 21), fill=GREY)
            d.text((120, 700), "回答自带数据日期与规则来源；不接入大模型也能用。", font=font("reg", 21), fill=GREY)

        blocks = [("确定性引擎", ["公开气象数据 → 物候规则表 → 分级阈值",
                              "逐日算出四类风险，输出触发依据",
                              "442 天逐日对拍，结果可离线复算"], GREEN),
                  ("DeepSeek（可选）", ["把风险指标、物候期、建议清单作为上下文",
                                        "生成个性化解释，回答必须引用数据日期",
                                        "不做数值预测，不改变判级结果"], (150, 100, 40))]
        for i, (title, lines, col) in enumerate(blocks):
            pb = span(t, 0.7 + i * 0.25, 1.25 + i * 0.25)
            if pb <= 0:
                continue
            y = 320 + i * 250
            d = ImageDraw.Draw(im)
            d.rectangle([1250, y - (1 - pb) * 12, 1254, y + 168 - (1 - pb) * 12], fill=col)
            d.text((1284, y - (1 - pb) * 12), title, font=font("bold", 30), fill=col)
            for j, line in enumerate(lines):
                d.text((1284, y + 50 + j * 36 - (1 - pb) * 12), line, font=font("reg", 21), fill=(84, 98, 88))

        pp = span(t, 1.6, 2.2)
        if pp > 0:
            box = rrect((540, 74), 10, (255, 244, 228, 255), border=(240, 200, 150, 255), width=1)
            paste(im, box, (1250, 744 - (1 - pp) * 12), pp)
            d = ImageDraw.Draw(im)
            d.text((1272, 780), "LLM 不参与数值预测", font=font("bold", 26), fill=(150, 92, 28), anchor="lm")
        return im

    # -- 07 验证 ---------------------------------------------------

    def s_verify(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "09", "可信", t=t, t0=0.05)
        self.headline(im, "六道闸门，全绿才记录", y=140, size=56, t=t, t0=0.15)
        self.caption_note(im, "python tools/check_all.py —— 六道闸门依次跑，任一失败即退出 1，不覆盖上一次成功记录。",
                          250, t=t, t0=0.4, size=22)

        rows = [("442", "天", "前端 JS 与 Python 实现逐日对拍"),
                ("37", "组", "阈值边界合成用例"),
                ("1302", "个", "逐日比对点位（5 条轴单调性扫描）"),
                ("44", "个", "注入变异，全部被闸门拦下"),
                ("6", "道", "闸门全绿，才写入 GATES.json")]
        for i, (num, unit, label) in enumerate(rows):
            pp = span(t, 0.55 + i * 0.13, 1.05 + i * 0.13)
            if pp <= 0:
                continue
            y = 340 + i * 108
            d = ImageDraw.Draw(im)
            d.line([120, y - 14, 1080, y - 14], fill=(224, 221, 210), width=1)
            k = ease_out(clamp((t - (0.55 + i * 0.13)) / 0.7))
            val = str(int(round(int(num) * k)))
            layer = text_img(val, "num", 54, GREEN)
            paste(im, layer, (120, y - (1 - pp) * 12), pp)
            wn = font("num", 54).getlength(val)
            d.text((120 + wn + 8, y + 34), unit, font=font("bold", 21), fill=GREY)
            d.text((300, y + 8), label, font=font("reg", 26), fill=(70, 84, 74))

        pp = span(t, 1.4, 2.0)
        if pp > 0:
            code = ('{\n  "all_green": true,\n  "gates": {\n    "parity":  {"rc": 0},\n'
                    '    "headline":{"rc": 0},\n    "baseline":{"rc": 0},\n    "smoke":   {"rc": 0},\n'
                    '    "mutation":{"rc": 0},\n    "docs":    {"rc": 0}\n  }\n}')
            box = rrect((560, 372), 14, (12, 46, 30, 255), border=(30, 78, 54, 255), width=1)
            paste(im, box, (1160, 330 - (1 - pp) * 12), pp)
            d = ImageDraw.Draw(im)
            d.text((1196, 372), "tools/GATES.json", font=font("mono", 19), fill=(150, 200, 170))
            for j, line in enumerate(code.split("\n")):
                d.text((1196, 408 + j * 20), line, font=font("mono", 17), fill=(206, 230, 216))
            d.ellipse([1188, 344, 1204, 360], fill=(60, 180, 110))
            d.text((1160, 726), "六道闸门 · 全部通过", font=font("bold", 20), fill=GREEN)

        self.caption_note(im, "代码 MIT 开源，输入序列冻结在 tools/fixtures/ 内，不联网也能复算。",
                          905, color=(128, 138, 128), t=t, t0=2.1, size=19)
        return im


    # -- 03 引擎 ---------------------------------------------------
    def s_engine(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "03", "引擎", t=t, t0=0.05)
        self.headline(im, "阈值全部写在代码里", y=146, size=58, t=t, t0=0.15)
        self.caption_note(im, "判级不靠模型感觉，每一条都能在仓库里找到出处。", 252, t=t, t0=0.45, size=24)

        rows = [("高温热害", RISK["heavy"], ["35℃ / 37℃ / 39℃ 三级分级", "连续 ≥35℃ 满 3 天升「中」、满 5 天升「重」"]),
                ("干旱", RISK["mid"], ["降水型：连续无有效降水日 25 / 15 / 8 天", "或 30 天累计 ≤15 / 30mm"]),
                ("花期 · 幼果低温", RISK["watch"], ["开花期 ≤2 / 5 / 8℃ 三档", "只在开花与幼果期触发"]),
                ("越冬冻害", (60, 110, 170), ["休眠期 ≤-3 / -5 / -7℃", "附「距降温 N 天」时效钟"])]
        y = 336
        for i, (name, col, lines) in enumerate(rows):
            pp = span(t, 0.5 + i * 0.15, 1.0 + i * 0.15)
            if pp <= 0:
                continue
            d = ImageDraw.Draw(im)
            d.line([120, y - 16, 1080, y - 16], fill=(224, 221, 210), width=1)
            d.ellipse([120, y + 14, 136, y + 30], fill=col)
            d.text((154, y), name, font=font("bold", 27), fill=INK)
            for j, line in enumerate(lines):
                d.text((154, y + 46 + j * 34), line, font=font("reg", 21), fill=(84, 98, 88))
            y += 132

        # 右侧：干旱双口径
        pp = span(t, 1.15, 1.75)
        if pp > 0:
            box = rrect((600, 486), 16, (255, 255, 255, 255), border=(214, 211, 200, 255), width=1,
                        shadow=(0, 10, 18))
            paste(im, box, (1200, 330 - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            d.text((1240, 356), "干旱：两套口径，取更重的那套", font=font("bold", 22), fill=GREEN)
            cols = [("降水型", GREEN, ["连续无有效降水日", "25 / 15 / 8 天三级", "30 天累计 ≤15 / 30mm"]),
                    ("蒸散型 def30", (150, 100, 40), ["30 天 ET0 − 降水", "阈值按物候期分位定标", "定标段 2016–2020"])]
            for i, (name, col, lines) in enumerate(cols):
                x = 1240 + i * 268
                d.rounded_rectangle([x, 398, x + 244, 570], 12, fill=(250, 249, 244), outline=col, width=2)
                d.text((x + 16, 416), name, font=font("bold", 21), fill=col)
                for j, line in enumerate(lines):
                    d.text((x + 16, 452 + j * 34), line, font=font("reg", 19), fill=(84, 98, 88))
            d.text((1240, 592), "蒸散口径单独最高只到「中」；出「重」须降水口径达「中」作绝对水量佐证。",
                   font=font("reg", 19), fill=(120, 96, 40))
            d.text((1240, 626), "2021–2025 整段留出，不参与定标。", font=font("reg", 19), fill=(108, 120, 110))
            d.text((1240, 668), "回看窗口不满 30 天或含缺测日 → 不判级，写进回答，不猜。",
                   font=font("bold", 19), fill=GREEN)
        return im

    # -- 05 落地 ---------------------------------------------------
    def s_export(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "05", "落地", t=t, t0=0.05)
        self.headline(im, "建议能带走，不锁在屏幕里", y=146, size=56, t=t, t0=0.15)

        pp = span(t, 0.4, 1.05)
        if pp > 0:
            card, pad = self._build_card(self.ui_advice, 900, 14)
            paste(im, card, (120 - pad - (1 - pp) * 18, 306 - pad - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            d.text((120, 268), "农事建议：分条给出，附执行时效与规则来源", font=font("bold", 21), fill=GREY)

        pp = span(t, 0.75, 1.35)
        if pp > 0:
            ph, php = shadowed(phone(self.mobile_now, width=258), blur=28, dy=16, alpha=85)
            paste(im, ph, (1480 - php - (1 - pp) * 22, 286 - php - (1 - pp) * 16), pp)

        chips = ["可打印 / Word", "Markdown 报告", "风险日历 CSV（97 天）", "无需安装 · 无需注册"]
        cx = 120
        for i, txt in enumerate(chips):
            pp = span(t, 1.0 + i * 0.14, 1.5 + i * 0.14)
            if pp <= 0:
                continue
            f = font("bold", 22)
            w = f.getlength(txt) + 44
            box = Image.new("RGBA", (int(w), 46), (0, 0, 0, 0))
            dd = ImageDraw.Draw(box)
            dd.rounded_rectangle([0, 0, w - 1, 45], 23, fill=(255, 255, 255, 235), outline=(210, 224, 208, 255), width=2)
            dd.text((22, 23), txt, font=f, fill=GREEN, anchor="lm")
            paste(im, box, (cx, 900 - (1 - pp) * 10), pp)
            cx += w + 16
        # 角标放在建议卡与手机之间的空隙（1055px 起），不压画面
        self.caption_note(im, "报告与 CSV 均本地生成", 902, color=GREY,
                          t=t, t0=1.6, size=19, x=1072)
        return im

    # -- 06 回放 ---------------------------------------------------
    def s_replay(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "06", "回放", t=t, t0=0.05)
        self.headline(im, "三个回放场景，现场就能点开", y=146, size=54, t=t, t0=0.15)

        scenes_ = [("2022 · 夏季热害", RISK["heavy"], "凉水河格点 ≥35℃ 30 天、热害重级 13 天；首个重级 08-04，比 40.9℃ 峰值早 3 天"),
                   ("2025 · 秋汛", RISK["watch"], "汉江 64 年来最密集秋汛、7 次编号洪水；回放给出降水统计与事件回顾"),
                   ("2021 · 冬寒潮", (60, 110, 170), "越冬冻害判级与「距降温 N 天」时效钟的现场演示")]
        for i, (name, col, desc) in enumerate(scenes_):
            pp = span(t, 0.5 + i * 0.22, 1.05 + i * 0.22)
            if pp <= 0:
                continue
            y = 330 + i * 200
            box = rrect((620, 172), 14, (255, 255, 255, 255), border=(222, 219, 208, 255), width=1)
            paste(im, box, (120, y - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            d.rectangle([120, y, 124, y + 172 - (1 - pp) * 14], fill=col)
            d.text((152, y + 26), name, font=font("bold", 25), fill=INK)
            for j, line in enumerate(wrap(desc, "reg", 20, 540)[:3]):
                d.text((152, y + 74 + j * 30), line, font=font("reg", 20), fill=(84, 98, 88))

        pp = span(t, 0.9, 1.5)
        if pp > 0:
            card, pad = self._build_card(self.ui_replay, 1040, 14)
            paste(im, card, (790 - pad - (1 - pp) * 18, 306 - pad - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            d.text((790, 268), "2022 年夏季热害回放（产品实拍）", font=font("bold", 21), fill=GREY)
        self.caption_note(im, "回放与首页同源：输入序列冻结在 tools/fixtures/，换机器结果一致。",
                          905, color=(128, 138, 128), t=t, t0=1.8, size=19)
        return im

    # -- 08 试点 ---------------------------------------------------
    def s_pilot(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "08", "试点", t=t, t0=0.05)
        self.headline(im, "90 天小范围试点，判据先写死", y=146, size=54, t=t, t0=0.15)
        self.caption_note(im, "与库区 1—2 家合作社 / 乡镇农技站试用，先签一页纸《试用与责任约定》。",
                          250, t=t, t0=0.4, size=22)

        tiles = [("≥19 / 26", "户·周", "周活跃（每周登录并查看）"),
                 ("≥5 / 10", "条", "中重级提示的采纳率"),
                 ("≥4", "分", "满意度户均（5 分制）"),
                 ("n < 10", "只报", "分子分母，不做比例宣传")]
        for i, (num, unit, label) in enumerate(tiles):
            pp = span(t, 0.5 + i * 0.16, 1.05 + i * 0.16)
            if pp <= 0:
                continue
            x = 120 + (i % 2) * 560
            y = 336 + (i // 2) * 190
            box = rrect((520, 160), 14, (255, 255, 255, 255), border=(222, 219, 208, 255), width=1)
            paste(im, box, (x, y - (1 - pp) * 14), pp)
            d = ImageDraw.Draw(im)
            col = GREEN if i < 2 else (150, 100, 40)
            pre = "≥" if num.startswith("≥") else ""
            if pre:
                d.text((x + 28, y + 66), pre, font=font("bold", 46), fill=col)
            d.text((x + 28 + (font("bold", 46).getlength(pre) + 8 if pre else 0), y + 40),
                   num[len(pre):], font=font("num", 56), fill=col)
            d.text((x + 28, y + 108), unit, font=font("bold", 20), fill=GREY)
            d.text((x + 28, y + 130), label, font=font("reg", 20), fill=(84, 98, 88))

        # 90 天节奏
        pp = span(t, 1.2, 1.8)
        if pp > 0:
            d = ImageDraw.Draw(im)
            d.text((1240, 336), "90 天节奏", font=font("bold", 22), fill=GREEN)
            phases = [("第 1–2 周", "签约 · 装园块 · 记基线"), ("第 3–10 周", "试用 · 周报 · 阈值校准"), ("第 11–13 周", "回访 · 复盘 · 出报告")]
            for i, (rng, what) in enumerate(phases):
                y = 386 + i * 96
                d.line([1240, y + 34, 1240 + 480 * ease_out(pp), y + 34], fill=(214, 211, 200), width=2)
                d.ellipse([1240, y + 26, 1252, y + 38], fill=AMBER)
                d.text((1272, y + 12), rng, font=font("bold", 20), fill=INK)
                d.text((1272, y + 44), what, font=font("reg", 19), fill=(84, 98, 88))
            box = rrect((520, 96), 12, (255, 244, 228, 255), border=(240, 200, 150, 255), width=1)
            paste(im, box, (1240, 690 - (1 - pp) * 10), pp)
            d.text((1266, 712), "判失败（事先写死）：周活跃 ≤14/26、或任一户", font=font("reg", 18), fill=(150, 92, 28))
            d.text((1266, 740), "全程未打开 → 判未达核心目标。", font=font("reg", 18), fill=(150, 92, 28))

        self.caption_note(im, "现金封顶 3400 元（无法合规列支自动降 2200）、工时 85–95 小时，均写有超支砍序与退出触发。",
                          905, color=(128, 138, 128), t=t, t0=1.9, size=19)
        return im

    # -- 09 边界 ---------------------------------------------------
    def s_safety(self, t, dur):
        im = self.bg_paper.copy()
        self.kicker(im, "09", "边界", t=t, t0=0.05)
        self.headline(im, "不确定性写在前头", y=146, size=58, t=t, t0=0.15)

        pp = span(t, 0.45, 1.0)
        if pp > 0:
            k = ease_out(clamp((t - 0.45) / 0.9))
            val = "%.1f" % (44.3 * k)
            layer = text_img(val, "num", 128, AMBER)
            paste(im, layer, (120, 330 - (1 - pp) * 18), pp)
            wn = font("num", 128).getlength(val)
            paste(im, text_img("%", "bold", 72, AMBER), (120 + wn + 6, 386 - (1 - pp) * 18), pp)
            d = ImageDraw.Draw(im)
            d.text((120, 500), "同一坐标取 4 个互不相同的格点，", font=font("reg", 24), fill=(70, 84, 74))
            d.text((120, 540), "2022 年 122 天里有 54 天，四类风险的", font=font("reg", 24), fill=(70, 84, 74))
            d.text((120, 580), "最高等级不一致（格点敏感性实测）。", font=font("reg", 24), fill=(70, 84, 74))
            d.text((120, 646), "测试脚本已入库，可自行复算。", font=font("reg", 21), fill=GREY)

        notes = ["7 天预报的误差随天数增大，中级及以下仅供参考",
                 "ERA5-Land 是约 9km 格点再分析，与单园实测存在偏差",
                 "输入序列冻结、代码开源，判级可离线复算"]
        for i, line in enumerate(notes):
            pp = span(t, 0.9 + i * 0.2, 1.4 + i * 0.2)
            if pp <= 0:
                continue
            d = ImageDraw.Draw(im)
            y = 340 + i * 108
            d.line([1180, y - 16, 1800, y - 16], fill=(224, 221, 210), width=1)
            d.text((1180, y), "·  " + line, font=font("reg", 23), fill=(70, 84, 74))

        pp = span(t, 1.5, 2.1)
        if pp > 0:
            box = rrect((1560, 150), 14, (236, 243, 234, 255), border=(210, 224, 208, 255), width=1)
            paste(im, box, (120, 740 - (1 - pp) * 12), pp)
            d = ImageDraw.Draw(im)
            d.text((152, 770), "风险等级用于辅助安排农事，不构成灾害预警，不替代官方气象预警与专业农技指导。",
                   font=font("bold", 23), fill=GREEN)
            d.text((152, 812), "数据：Open-Meteo（ERA5-Land / ECMWF，CC BY 4.0） · 规则来源：湖北省农业农村厅等公开农技资料",
                   font=font("reg", 20), fill=(96, 108, 98))
        return im

    # -- 08 尾卡 ---------------------------------------------------

    def s_end(self, t, dur):
        im = self.bg_dark.copy()
        d = ImageDraw.Draw(im)
        p = span(t, 0.05, 0.7)
        mark = self.mark76
        paste(im, mark, (120, 190 - (1 - p) * 16), p)
        for i, (txt, kind, size, col, y, delay) in enumerate([
            ("丹橘知旱", "bold", 84, WHITE, 296, 0.15),
            ("一个地方，一个气候问题，一个方案。", "reg", 30, MINT, 408, 0.3),
        ]):
            pp = span(t, delay, delay + 0.65)
            paste(im, text_img(txt, kind, size, col), (120, y - (1 - pp) * 16), pp)

        links = [("在线体验", "pawnnwap.github.io/danju-zhihan"),
                 ("代码仓库", "github.com/Pawnnwap/danju-zhihan"),
                 ("作品帖", "deepseek.club/topic/5043")]
        for i, (label, url) in enumerate(links):
            pp = span(t, 0.55 + i * 0.14, 1.0 + i * 0.14)
            if pp <= 0:
                continue
            y = 530 + i * 66
            d.text((120, y + 22), label, font=font("bold", 20), fill=(140, 190, 160), anchor="lm")
            d.text((236, y + 22), url, font=font("mono", 25), fill=WHITE, anchor="lm")

        pp = span(t, 1.0, 1.6)
        if pp > 0:
            d.line([120, 760, 120 + 160 * ease_out(pp), 760], fill=AMBER, width=3)
            d.text((120, 790), "风险等级用于辅助安排农事，不构成灾害预警，不替代官方气象预警与专业农技指导。",
                   font=font("reg", 20), fill=(150, 186, 162))
            d.text((120, 826), "数据：Open-Meteo（ERA5-Land / ECMWF，CC BY 4.0） · 规则来源：湖北省农业农村厅等公开农技资料",
                   font=font("reg", 18), fill=(120, 158, 136))
            d.text((120, 866), "AI4Climate 2026 · 团队 沧浪舟 · 个人参赛", font=font("reg", 18), fill=(120, 158, 136))

        # 右侧二维码（指向线上 Demo）
        pp = span(t, 0.8, 1.4)
        if pp > 0:
            qr = self._qr("https://pawnnwap.github.io/danju-zhihan/", 190)
            if qr:
                paste(im, qr, (1580, 300 - (1 - pp) * 12), pp)
                d.text((1675, 520), "扫码打开", font=font("bold", 18), fill=(150, 190, 165), anchor="ma")
        return im

    @staticmethod
    def _qr(data, size):
        vendor = Path(__file__).resolve().parent / "_vendor"
        if str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        try:
            import qrcode
        except Exception:
            return None
        q = qrcode.QRCode(border=1, box_size=8)
        q.add_data(data)
        q.make(fit=True)
        img = q.make_image(fill_color=(255, 255, 255), back_color=(10, 38, 26)).convert("RGBA")
        return img.resize((size, size), Image.NEAREST)


PREVIEW = {
    "title": (Scenes.s_title, 6.0),
    "problem": (Scenes.s_problem, 13.0),
    "solution": (Scenes.s_solution, 12.0),
    "engine": (Scenes.s_engine, 16.0),
    "product": (Scenes.s_product, 12.0),
    "export": (Scenes.s_export, 13.5),
    "replay": (Scenes.s_replay, 17.0),
    "evidence": (Scenes.s_evidence, 12.5),
    "ai": (Scenes.s_ai, 10.5),
    "verify": (Scenes.s_verify, 12.5),
    "pilot": (Scenes.s_pilot, 16.0),
    "safety": (Scenes.s_safety, 14.0),
    "end": (Scenes.s_end, 5.0),
}
