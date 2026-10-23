#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""布局审计（绘制调用级）：逐条记录每个文字/卡片的几何包围盒，检测

  1. 文字冲出画布或进入底部字幕带（会被裁掉）；
  2. 文字冲出所在卡片/面板容器的边框（错位、出框）；
  3. 文字互相重叠（压字）；
  4. 卡片内容区越界。

原理：在绘制原语（kit.paste / kit.text_img / kit.rrect / ImageDraw.Draw/text）上插桩。
画在本地图层（芯片、标签框）上的字，记录时挂在图层上，等 kit.paste 贴到主画布时
再按贴图位置换算成主画布坐标——避免局部坐标互相「重叠」的误报。
面板容器的内容区用「不透明像素包围盒」实测（阴影半透明会被排除），不靠猜 pad。

场景按 settled 帧审计（t=99，所有入场动画已结束、元素在最终位置）。

用法：
    python tools/video/layout_audit.py                # 全部场景
    python tools/video/layout_audit.py engine pilot   # 指定场景
退出码：有 violation 为 1，全绿为 0。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "video"))

from PIL import ImageDraw  # noqa: E402

import kit  # noqa: E402

W, H = kit.W, kit.H
CONTENT_H = kit.CONTENT_H
TOL = 8          # 容差（文字图层自带 ~4px 留白）
SAFE = 2         # 画布安全边

_records = []        # 主画布坐标系下的记录
_local = {}          # id(layer) -> [(text, rect)]，画在本地图层上的字
_quiet = [0]


class quiet:
    def __enter__(self):
        _quiet[0] += 1

    def __exit__(self, *a):
        _quiet[0] -= 1


def _rec(kind, text, rect):
    x0, y0, x1, y1 = rect
    _records.append({"kind": kind, "text": str(text)[:60],
                     "rect": (float(x0), float(y0), float(x1), float(y1))})


def _opaque_bbox(layer):
    """图层内容区 = 不透明像素包围盒（排除投影等半透明边缘）。"""
    a = layer.getchannel("A")
    return a.point(lambda v: 255 if v >= 250 else 0).getbbox() or (0, 0, layer.width, layer.height)


# ---------------------------------------------------------------- 插桩 kit 原语

_orig_text_img = kit.text_img
def text_img(text, kind, size, fill, **kw):
    with quiet():
        im = _orig_text_img(text, kind, size, fill, **kw)
    im._djzh_text = str(text)
    return im

_orig_rrect = kit.rrect
def rrect(size, radius, fill, **kw):
    with quiet():
        im = _orig_rrect(size, radius, fill, **kw)
    shadow = kw.get("shadow")
    pad = 0 if not shadow else int(shadow[2] * 3)
    im._djzh_box = (pad, pad, pad + size[0], pad + size[1])
    return im

_orig_paste = kit.paste
def paste(base, layer, xy, alpha=1.0):
    if alpha and not _quiet[0]:
        x, y = int(xy[0]), int(xy[1])
        # 本地图层上画的字 → 换算到主画布坐标
        for text_s, rect in _local.pop(id(layer), []):
            _rec("text", text_s, (rect[0] + x, rect[1] + y, rect[2] + x, rect[3] + y))
        if hasattr(layer, "_djzh_text"):
            _rec("text", layer._djzh_text,
                 (x + 3, y + 3, x + layer.width - 3, y + layer.height - 3))
        elif hasattr(layer, "_djzh_box"):
            l, t, r, b = layer._djzh_box
            _rec("box", "rrect", (x + l, y + t, x + r, y + b))
        elif layer.width >= 200 and layer.height >= 120:
            l, t, r, b = _opaque_bbox(layer)
            _rec("panel", "panel %dx%d" % (layer.width, layer.height),
                 (x + l, y + t, x + r, y + b))
    return _orig_paste(base, layer, xy, alpha)

kit.text_img, kit.rrect, kit.paste = text_img, rrect, paste

# Draw(im) 记住目标图层，文字才能挂到图层上
_orig_Draw = ImageDraw.Draw
def Draw(im, *a, **k):
    d = _orig_Draw(im, *a, **k)
    d._djzh_target = im
    return d
ImageDraw.Draw = Draw

_orig_draw = ImageDraw.ImageDraw.text
def _text(self, xy, text_s, *a, **kw):
    if not _quiet[0]:
        kw2 = {k: kw[k] for k in ("font", "anchor", "stroke_width", "stroke_fill", "spacing") if k in kw}
        try:
            bb = self.textbbox(xy, text_s, **kw2)
        except Exception:
            f = kw2.get("font")
            try:
                w, h = f.getlength(str(text_s)), f.size
            except Exception:
                w, h = len(str(text_s)) * 12, 22
            bb = (xy[0], xy[1], xy[0] + w, xy[1] + h)
        target = getattr(self, "_djzh_target", None)
        if target is not None and not hasattr(target, "_djzh_text"):
            _local.setdefault(id(target), []).append((str(text_s), tuple(float(v) for v in bb)))
        else:
            _rec("text", text_s, bb)
    return _orig_draw(self, xy, text_s, *a, **kw)
ImageDraw.ImageDraw.text = _text


# ---------------------------------------------------------------- 加载场景

import scenes  # noqa: E402

_orig_shadowed = scenes.shadowed
def shadowed(layer, blur=34, dy=18, alpha=70):
    with quiet():
        base, pad = _orig_shadowed(layer, blur, dy, alpha)
    base._djzh_pad = pad
    return base, pad
scenes.shadowed = shadowed


def _make_quiet(f):
    def g(*a, **k):
        with quiet():
            return f(*a, **k)
    return g

# 本地画布上画字的构造器：内部坐标不进记录
for _name in ("phone", "browser_window", "callout"):
    _fn = getattr(scenes, _name, None)
    if callable(_fn):
        setattr(scenes, _name, _make_quiet(_fn))

_Scenes = scenes.Scenes
for _name in ("_build_card", "_build_product", "_mark", "_build_chart", "_load_temps"):
    if _name in _Scenes.__dict__:
        _orig = _Scenes.__dict__[_name]
        _fn = getattr(_Scenes, _name)
        _wrapped = _make_quiet(_fn)
        if isinstance(_orig, (staticmethod, classmethod)):
            _wrapped = type(_orig)(_wrapped)
        setattr(_Scenes, _name, _wrapped)


# ---------------------------------------------------------------- 审计规则

def audit(scene_key, t=99.0):
    global _records, _local
    _records, _local = [], {}
    fn = getattr(_Scenes, "s_" + scene_key, None)
    if fn is None:
        raise KeyError("没有这个场景: " + scene_key)
    im = fn(_Scenes(), t, 20.0)   # t=99：动画结束，元素在最终位置

    # 画面空白检测：内容被遮没/淡出到纯色 = 观众什么也看不见
    import numpy as np
    a = np.asarray(im.convert("RGB"))
    if a.std() < 2.5 or int((a.astype(int).sum(axis=2) > 30).sum()) < 20000:
        _rec("text", "!! 画面空白 !!", (0, 0, 1, 1))

    # 主画布上直接画的字（没经过 paste）按绝对坐标入账；其余残留是没贴出的图层，丢弃
    for text_s, rect in _local.pop(id(im), []):
        _rec("text", text_s, rect)
    _local.clear()

    viols, warns = [], []
    texts = [r for r in _records if r["kind"] == "text"]
    boxes = [r for r in _records if r["kind"] in ("box", "panel")]

    for r in texts:
        if r["text"] == "!! 画面空白 !!":
            viols.append("画面空白（std<2.5，内容不可见）")
    for r in texts + boxes:
        x0, y0, x1, y1 = r["rect"]
        tag = "%s「%s」" % (r["kind"], r["text"])
        if x0 < -SAFE or x1 > W + SAFE or y0 < -SAFE or y1 > H + SAFE:
            viols.append("%s 越出画布 [%d,%d,%d,%d]" % (tag, x0, y0, x1, y1))
        elif r["kind"] == "text" and y1 > CONTENT_H + 4:
            viols.append("%s 压进字幕带（y1=%d > %d）" % (tag, y1, CONTENT_H))

    for t in texts:
        tx0, ty0, tx1, ty1 = t["rect"]
        tw, th = tx1 - tx0, ty1 - ty0
        if tw <= 0 or th <= 0:
            continue
        for c in boxes:
            cx0, cy0, cx1, cy1 = c["rect"]
            ox = min(tx1, cx1) - max(tx0, cx0)
            oy = min(ty1, cy1) - max(ty0, cy0)
            if ox > 0.3 * tw and oy > 0.3 * th:      # 文字大体落在容器区域内
                over = []
                if tx1 > cx1 + TOL:
                    over.append("右溢 %dpx" % (tx1 - cx1))
                if tx0 < cx0 - TOL:
                    over.append("左溢 %dpx" % (cx0 - tx0))
                if ty1 > cy1 + TOL:
                    over.append("下溢 %dpx" % (ty1 - cy1))
                if ty0 < cy0 - TOL:
                    over.append("上溢 %dpx" % (cy0 - ty0))
                if over:
                    viols.append("文字「%s」冲出容器（容器 %s [%d,%d,%d,%d]）：%s"
                                 % (t["text"], c["text"], cx0, cy0, cx1, cy1, "、".join(over)))

    for i, a in enumerate(texts):
        ax0, ay0, ax1, ay1 = a["rect"]
        for b in texts[i + 1:]:
            bx0, by0, bx1, by1 = b["rect"]
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox <= 0 or oy <= 0:
                continue
            area = ox * oy
            small = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
            if (small > 0 and area > 0.35 * small) or (ox >= 20 and oy >= 8):
                viols.append("文字重叠「%s」×「%s」（重叠 %d×%d）" % (a["text"], b["text"], ox, oy))
            elif ox >= 12 and oy >= 2:
                warns.append("文字相接「%s」×「%s」（重叠 %d×%d）" % (a["text"], b["text"], ox, oy))
    return viols, warns, len(texts), len(boxes)


TS = tuple(round(0.15 * i, 2) for i in range(1, 41)) + (99.0,)   # 0.15–6.0s 密集采样 + 最终态


def audit_subtitles():
    """字幕带几何：按 make_video.caption 的排版规则复算每条字幕的占位。"""
    import ast
    from kit import wrap, font as _font, BAND_H, CONTENT_H as CH, W as WW
    src = (HERE.parent / "make_video.py").read_text(encoding="utf-8")
    shots = None
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SHOTS":
            shots = ast.literal_eval(node.value)
    if not shots:
        return ["字幕检查：没找到 SHOTS"]
    viols = []
    for key, text, _mn in shots:
        t = text if key != "title" else "AI4Climate 2026 · 气候适应与韧性 · 团队 沧浪舟"
        size = 30
        lines = wrap(t, "reg", size, 1440)
        while len(lines) > 2 and size > 24:
            size -= 2
            lines = wrap(t, "reg", size, 1440)
        lh = int(size * 1.38)
        top = CH + BAND_H / 2 - (len(lines) - 1) * lh / 2 - size / 2
        bottom = CH + BAND_H / 2 + (len(lines) - 1) * lh / 2 + size / 2
        f = _font("reg", size)
        for ln in lines:
            if f.getlength(ln) > 1440 + 1:
                viols.append("字幕 %s 行宽超 1440：%r" % (key, ln[:24]))
        if top < CH + 2 or bottom > kit.H - 2:
            viols.append("字幕 %s 排不下：size=%d 行数=%d 顶=%.0f 底=%.0f（带 %d–%d）"
                         % (key, size, len(lines), top, bottom, CH, kit.H))
    return viols


def audit_asset_crops():
    """素材裁剪检查：scenes.py 里对 UI 素材的 .crop((l,t,r,b)) 不得把文字拦腰切断。

    判据（列/行墨迹剖面）：「文字状」列 = 墨迹高度适中（≥12 且 ≤带高-4）的列；
    整幅边线/窗口边框墨迹顶格会被高度上限排除。文字状列越过裁剪边线 → 判切断。
    2026-10 校准：ui_replay/ui_qa 旧裁剪（1830/1840）切断文字 → 必须报警；
    修复后裁剪（2072）→ 必须干净。
    """
    import re
    import numpy as np
    src = (HERE / "scenes.py").read_text(encoding="utf-8")
    viols = []
    for m in re.finditer(r"self\.(ui_\w+)\.crop\(\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)\)", src):
        name, l, t, r, b = m.group(1), *map(int, m.groups()[1:])
        img = scenes.load_asset(name)
        a = np.asarray(img.convert("RGB"), dtype=int)
        band = a[t:b, :]
        h = b - t
        bg = np.median(band.reshape(-1, 3), axis=0)
        mask = (np.abs(band - bg).sum(axis=2) > 40)
        cnt = mask.sum(axis=0)
        cand = np.where((cnt >= 12) & (cnt <= min(220, h - 4)))[0]
        if len(cand):
            right = int(cand[cand >= l].max()) if (cand >= l).any() else -1
            left = int(cand[cand <= r].min()) if (cand <= r).any() else 10 ** 9
            if right > r - 4:
                viols.append("%s.crop((%d,%d,%d,%d)) 右侧切断文字（文字到列 %d > 裁剪界 %d）"
                             % (name, l, t, r, b, right, r))
            if left < l:
                viols.append("%s.crop((%d,%d,%d,%d)) 左侧切断文字（文字从列 %d 起 < 裁剪界 %d）"
                             % (name, l, t, r, b, left, l))
        # 上下：仅当裁剪接近全高（卡片式裁剪）才查；带状裁剪上下切断是预期行为
        if h >= 0.85 * img.height:
            rows = np.where(mask.any(axis=1))[0]
            if len(rows):
                top = int(rows.min()) + t
                bot = int(rows.max()) + t
                if top < t:
                    viols.append("%s.crop((%d,%d,%d,%d)) 上方切断内容" % (name, l, t, r, b))
                if bot > b:
                    viols.append("%s.crop((%d,%d,%d,%d)) 下方切断内容" % (name, l, t, r, b))
    return viols


def main():
    keys = sys.argv[1:] or list(scenes.PREVIEW)
    bad = 0
    for k in keys:
        merged, warns, n_t, n_b = [], [], 0, 0
        for t in TS:
            viols, warns_t, n_t, n_b = audit(k, t)
            for v in viols:
                merged.append("t=%.1f  %s" % (t, v))
            for w in warns_t:
                if w not in warns:
                    warns.append(w)
        print("== %s ==  （%d 条文字 / %d 个容器，t∈%s）" % (k, n_t, n_b, TS))
        for v in merged:
            print("   VIOLATION", v)
        for w in warns:
            print("   warning  ", w)
        if not merged and not warns:
            print("   ok")
        if merged:
            bad += 1

    sub = audit_subtitles()
    for v in audit_asset_crops():
        sub.append("素材裁剪 " + v)
        print("   VIOLATION 素材裁剪", v)
    print("== 字幕带 / 素材裁剪 ==")
    for v in sub:
        print("   VIOLATION", v)
        bad += 1
    if not sub:
        print("   ok（%d 条字幕已核）" % ast_shots_count())
    print("\n结果：%d 项违规" % bad)
    return 1 if bad else 0


def ast_shots_count():
    import ast
    src = (HERE.parent / "make_video.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SHOTS":
            return len(ast.literal_eval(node.value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
