#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染《丹橘知旱》参赛演示片（1920x1080 / 30fps / 中文配音 + 烧字幕）。

流程
  1. piper 逐句合成配音（离线、可复现）→ 量出每段时长
  2. 按「配音时长」排时间线，逐帧用 Pillow 画（不依赖浏览器录制）
  3. 原始帧经管道喂 ffmpeg（libx264 / crf 18 / yuv420p）
  4. 配音按时间线对齐混成一条 44.1k 音轨，最后与画面复用封装

用法
  python tools/make_video.py --still problem 6.5     # 导出单帧检查版式
  python tools/make_video.py --preview               # 出 8 张分镜缩略拼图
  python tools/make_video.py                         # 正式渲染 docs/demo-video.mp4

环境变量：PIPER / PIPER_MODEL 可覆盖默认可执行文件与中文模型路径。
"""
import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "video"))

import kit
from kit import (AMBER, BAND_H, CONTENT_H, GREY, H, MINT, W, WHITE, draw_text, font, paste,
                 span, text_img, wrap)
from scenes import PREVIEW, Scenes

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo-video.mp4"
PIPER = __import__("os").environ.get("PIPER", "piper")
PIPER_MODEL = __import__("os").environ.get("PIPER_MODEL", r"C:\Users\Vladi\.ai-debate-live\tts_models\zh_CN-huayan-medium.onnx")
TTS_ENGINE = __import__("os").environ.get("TTS_ENGINE", "edge")
TTS_VOICE = __import__("os").environ.get("TTS_VOICE", "zh-CN-YunjianNeural")
TTS_RATE = __import__("os").environ.get("TTS_RATE", "+0%")
EDGE_TTS = __import__("os").environ.get("EDGE_TTS", "edge-tts")
FPS = 30

# 分镜脚本：一句一镜，配音即字幕。所有数字均可在仓库内复核（见 scenes.py 顶部说明）。
SHOTS = [
    ("title", "丹橘知旱——丹江口库区柑橘园的气候决策助手。", 5.2),
    ("problem", "丹江口水库是南水北调中线的水源区，库周有三十万亩柑橘、十万果农。2022 年夏天，凉水河镇 35 度以上高温 30 天，极端最高 40.9 度；2025 年汉江又发生 64 年来最密集秋汛，7 次编号洪水。", 17.0),
    ("solution", "我们把决策拆成三步：选园块，识别物候期，算出高温热害、干旱、花期低温、越冬冻害四类风险，再给出能执行的农事建议。", 11.0),
    ("engine", "四类风险逐日判级，阈值全部写在代码里。高温热害按 35、37、39 度三级，连续 35 度满 3 天升中、满 5 天升重；干旱用降水型和蒸散型两套口径取更重的那套，蒸散口径单独最高只到中级，判重级必须有降水口径佐证；不满 30 天或有缺测的日子不判级。", 20.0),
    ("product", "手机浏览器打开就能用。选好园块，看板给出当前物候和四类风险；90 天日历一格一天，红橙黄蓝对应重、中、轻、关注。", 11.0),
    ("export", "农事建议可以导出成可打印文档，风险日历能一次导出 97 天 CSV；手机竖排同样可读，不用装 App，也不用注册。", 11.0),
    ("replay", "页面内置三个回放场景：2022 年夏季热害、2025 年秋汛、2021 年冬寒潮，现场就能点开。", 10.0),
    ("evidence", "用 2022 年真实数据复算：35 度以上 30 天，热害重级 13 天；首个重级提示在 8 月 4 日，比 40.9 度的峰值早 3 天。", 11.5),
    ("ai", "风险由确定性规则引擎逐日计算，阈值全部写在代码里；DeepSeek 只把结果转译成人话，不做数值预测。", 10.0),
    ("verify", "前端引擎和 Python 实现逐日对拍 442 天，加 37 组边界用例、44 个注入变异；六道闸门全绿，结果才写进 GATES.json，代码开源可离线复算。", 12.0),
    ("pilot", "接下来做 90 天小范围试点：与一到两家合作社签一页纸试用与责任约定，成功判据事先写死——周活跃不低于 19 个户周、采纳率不低于十分之五、满意度户均不低于 4 分；样本不足 10 户只报分子分母。现金封顶 3400 元、工时 85 到 95 小时。", 21.0),
    ("safety", "不确定性也写在前头：同一坐标取 4 个格点，2022 年 122 天里有 54 天，也就是 44.3%，四类风险的最高等级不一致；7 天预报误差随天数增大，中级及以下仅供参考。", 17.0),
    ("end", "一个地方，一个气候问题，一个方案。丹橘知旱。", 5.0),
]


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if p.returncode != 0:
        sys.exit("命令失败：%s\n%s" % (" ".join(map(str, cmd))[:220], (p.stderr or p.stdout)[-2000:]))
    return p.stdout


def wav_dur(path):
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


def _piper_to_wav(text, txt, raw, wav):
    txt.write_text(text, encoding="utf-8")
    run([PIPER, "-m", PIPER_MODEL, "-i", str(txt), "-f", str(raw),
         "--sentence-silence", "0.18", "--noise-scale", "0.55", "--noise-w-scale", "0.8"])
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ar", "44100", "-ac", "1", str(wav)])


def _edge_to_wav(text, mp3, wav):
    run([EDGE_TTS, "--voice", TTS_VOICE, "--rate=" + TTS_RATE, "--text", text,
         "--write-media", str(mp3)])
    # Edge 返回的 mp3 首尾常带 ~1s 静音，8 段累计会在换幕处留下 2s 空档；
    # 这里裁到「头 0.05s / 尾 0.15s」，段间停顿交给时间线统一控制。
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
            "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", trim,
         "-ar", "44100", "-ac", "1", str(wav)])


def build_voices(work):
    """合成配音并统一到 44.1k 单声道，返回 [(wav, dur)]。

    默认走 Edge TTS 的神经网络音色；按「引擎+音色+语速+文本」哈希缓存，
    改一句只重跑那一句。Edge 失败（网络/接口变更）时自动回落到 piper，保证能出片。
    """
    import hashlib
    cache = Path(tempfile.gettempdir()) / "djzh_vo_v4"
    cache.mkdir(exist_ok=True)
    out = []
    for i, (_, text, _) in enumerate(SHOTS, start=1):
        key = "%s|%s|%s|%s" % (TTS_ENGINE, TTS_VOICE, TTS_RATE, text)
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        wav = cache / (h + ".wav")
        if not wav.exists():
            txt = work / ("vo%02d.txt" % i)
            raw = work / ("vo%02d.wav" % i)
            done = False
            if TTS_ENGINE == "edge":
                try:
                    _edge_to_wav(text, work / ("vo%02d.mp3" % i), wav)
                    done = True
                except SystemExit as e:
                    print("  ! Edge TTS 失败，回落 piper：%s" % str(e)[:120], flush=True)
            if not done:
                if not Path(PIPER_MODEL).exists():
                    sys.exit("Edge TTS 不可用，且未找到 piper 中文模型：%s" % PIPER_MODEL)
                _piper_to_wav(text, txt, raw, wav)
        out.append((wav, wav_dur(wav)))
    return out


def timeline(voices):
    lead, tail = 0.30, 0.50
    rows, t = [], 0.0
    for (key, text, min_dur), (wav, dur) in zip(SHOTS, voices):
        length = max(min_dur, lead + dur + tail)
        rows.append(dict(key=key, text=text, wav=wav, vo=dur, start=t, dur=length,
                         vo_start=t + lead))
        t += length
    return rows, t


def caption(im, global_t, total, idx, rows):
    """底部字幕带：品牌点 + 字幕 + 进度线。"""
    band = Image.new("RGBA", (W, BAND_H), (7, 31, 21, 255))
    im.alpha_composite(band, (0, CONTENT_H))
    d = ImageDraw.Draw(im)
    d.line([0, CONTENT_H, W, CONTENT_H], fill=(22, 58, 40, 255), width=1)
    d.ellipse([44, CONTENT_H + 49, 56, CONTENT_H + 61], fill=AMBER)
    d.text((66, CONTENT_H + 55), "丹橘知旱", font=font("bold", 16), fill=(146, 184, 160), anchor="lm")

    text = rows[idx]["text"]
    if rows[idx].get("key") == "title":
        text = "AI4Climate 2026 · 气候适应与韧性 · 团队 沧浪舟"
    size = 30
    lines = wrap(text, "reg", size, 1440)
    while len(lines) > 2 and size > 24:
        size -= 2
        lines = wrap(text, "reg", size, 1440)
    lh = int(size * 1.38)
    y0 = CONTENT_H + BAND_H / 2 - (len(lines) - 1) * lh / 2
    for i, line in enumerate(lines):
        d.text((W / 2, y0 + i * lh), line, font=font("reg", size), fill=(242, 240, 231), anchor="mm")

    d.text((W - 44, CONTENT_H + 55), "%02d / %02d" % (idx + 1, len(rows)), font=font("num", 16),
           fill=(110, 146, 124), anchor="rm")
    # 进度线
    d.rectangle([0, H - 3, W * global_t / total, H], fill=AMBER)


def render_frame(sc, rows, idx, local_t, global_t, total):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    body = PREVIEW[rows[idx]["key"]][0](sc, local_t, rows[idx]["dur"])
    im.alpha_composite(body.convert("RGBA"), (0, 0))
    caption(im, global_t, total, idx, rows)
    if total - global_t < 0.55:                      # 片尾 0.55s 整体淡出到黑
        k = max(0.0, (total - global_t) / 0.55)
        im = Image.blend(Image.new("RGBA", im.size, (0, 0, 0, 255)), im, k)
    return im


def preview(sc, work):
    tiles = []
    for key, (fn, dur) in PREVIEW.items():
        body = fn(sc, dur * 0.72, dur)
        im = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        im.alpha_composite(body.convert("RGBA"), (0, 0))
        caption(im, 1, 1, 0, [dict(text="分镜预览：" + key)])
        tiles.append((key, im.resize((640, 360), Image.LANCZOS)))
    sheet = Image.new("RGB", (640 * 2 + 24, 360 * 4 + 40), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, (key, im) in enumerate(tiles):
        x = (i % 2) * 664
        y = (i // 2) * 380
        sheet.paste(im.convert("RGB"), (x, y))
        d.text((x + 8, y + 6), key, font=font("bold", 18), fill=(255, 200, 120))
    p = ROOT / "docs" / "frames" / "v2" / "preview.png"
    sheet.save(p)
    print("分镜预览：%s" % p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--still", nargs=2, metavar=("SCENE", "T"))
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--no-tts", action="store_true", help="复用上次时间线（调试用）")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="djzh-v2-"))
    voices = build_voices(work) if not args.no_tts else [
        (Path(""), d) for _, _, d in [(k, t, m) for k, t, m in SHOTS]]
    rows, total = timeline(voices)
    print("配音总长 %.1fs，成片 %.1fs，%d 帧" % (sum(r["vo"] for r in rows), total, int(total * FPS)))
    sc = Scenes()

    if args.still:
        key, tt = args.still
        idx = next(i for i, r in enumerate(rows) if r["key"] == key)
        im = render_frame(sc, rows, idx, float(tt), rows[idx]["start"] + float(tt), total)
        p = ROOT / "docs" / "frames" / "v2" / ("still_%s.png" % key)
        im.convert("RGB").save(p)
        print("单帧：%s" % p)
        return

    if args.preview:
        preview(sc, work)
        return

    # 画面
    silent = work / "silent.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-g", str(FPS * 2), "-movflags", "+faststart", str(silent)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        for idx, row in enumerate(rows):
            frames = int(round(row["dur"] * FPS))
            for f in range(frames):
                t = f / FPS
                im = render_frame(sc, rows, idx, t, row["start"] + t, total)
                proc.stdin.write(im.convert("RGB").tobytes())
                n += 1
                if n % 300 == 0:
                    print("  %d / %d 帧" % (n, int(total * FPS)), flush=True)
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        rc = proc.wait()
    if rc != 0:
        sys.exit("ffmpeg 渲染失败：\n" + err[-2000:])

    # 音轨
    mix = np.zeros(int((total + 0.5) * 44100), dtype=np.float32)
    for row in rows:
        with wave.open(str(row["wav"]), "rb") as f:
            data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        start = int(row["vo_start"] * 44100)
        mix[start:start + len(data)] += data
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = np.tanh(mix / peak * 1.1) * 0.92
    audio = work / "mix.wav"
    with wave.open(str(audio), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes((mix * 32767).astype(np.int16).tobytes())

    run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-shortest", "-movflags", "+faststart", str(OUT)])
    print("已生成 %s：%.2f 秒，%.1f MB" % (OUT.relative_to(ROOT), wav_dur(audio), OUT.stat().st_size / 1048576))


if __name__ == "__main__":
    main()
