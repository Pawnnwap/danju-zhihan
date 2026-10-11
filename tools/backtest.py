#!/usr/bin/env python3
"""丹橘知旱 · 风险引擎历史回测（2022 年热害 + 干旱）

与前端 phenology.js/app.js 同阈值规则的 Python 独立实现（热害与干旱两类，
与 JS 的等价性约定见 docs/02；任一侧规则改动须同步对拍）。

- 数据：Open-Meteo Archive API（ERA5-Land 再分析，免密钥）
- 热害输出：≥35℃ 日数、"重"级提示首日、峰值日期与提前天数
- 干旱输出：首个"轻"级以上提示日、"重/中"级天数（连旱日 + 30 天累计降水双指标）
- 与 JS 一致的工程行为：日期断链清零、三变量全非空过滤、30 天日历窗口

用法：python backtest.py [lat] [lon]
默认坐标：丹江口市凉水河镇（32.60, 111.42）
"""
import json
import sys
import urllib.request
from datetime import date, timedelta

LAT, LON = 32.60, 111.42
START, END = "2022-06-01", "2022-09-30"


def fetch(lat: float, lon: float) -> list[dict]:
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={START}&end_date={END}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        j = json.load(r)
    d = j["daily"]
    days = []
    for i, t in enumerate(d["time"]):
        tmax, tmin, pr = d["temperature_2m_max"][i], d["temperature_2m_min"][i], d["precipitation_sum"][i]
        if tmax is None or tmin is None or pr is None:
            continue  # 与 JS fetchArchive 一致：三变量全非空
        days.append({"date": t, "tmax": tmax, "tmin": tmin, "pr": pr})
    return days


def assess(days: list[dict]) -> list[dict]:
    """热害 + 干旱（休眠期降权在本区间不触发）。断链清零与 JS assess 一致。"""
    out = []
    streak = 0
    dry_streak = 0
    for i, d in enumerate(days):
        cur = date.fromisoformat(d["date"])
        if i > 0:
            prev = date.fromisoformat(days[i - 1]["date"])
            if (cur - prev) != timedelta(days=1):
                streak = 0
                dry_streak = 0
        # 热害（与 phenology.js heat 逐分支一致）
        t, s = d["tmax"], None
        streak = streak + 1 if t >= 35 else 0
        if t >= 39:
            lv, why = "重", f"{t}℃≥39℃"
        elif t >= 37:
            lv, why = "中", f"{t}℃≥37℃"
        elif t >= 35:
            lv, why = "轻", f"{t}℃≥35℃"
        elif t >= 33:
            lv, why = "关注", f"{t}℃≥33℃"
        else:
            lv, why = None, ""
        if lv == "轻" and streak >= 3:
            lv, why = "中", f"{t}℃，连续{streak}天≥35℃"
        if (lv in ("轻", "中")) and streak >= 5:
            lv, why = "重", f"{t}℃，连续{streak}天≥35℃"
        d["heat_level"], d["heat_why"], d["streak"] = lv, why, streak
        # 干旱（与 phenology.js drought 一致：连旱日<1mm + 30 天日历窗口累计）
        dry_streak = dry_streak + 1 if d["pr"] < 1 else 0
        rain30, win = 0.0, 0
        for j in range(i, -1, -1):
            if (cur - date.fromisoformat(days[j]["date"])).days > 29:
                break
            rain30 += days[j]["pr"]
            win += 1
        dl = dw = None
        if i >= 29:
            if dry_streak >= 25 or rain30 <= 15:
                dl, dw = "重", f"连续{dry_streak}天无有效降水，30天累计{rain30:.0f}mm"
            elif dry_streak >= 15 or rain30 <= 30:
                dl, dw = "中", f"连续{dry_streak}天无有效降水，30天累计{rain30:.0f}mm"
            elif dry_streak >= 8 or rain30 <= 45:
                dl, dw = "轻", f"连续{dry_streak}天无有效降水，30天累计{rain30:.0f}mm"
        d["drought_level"], d["drought_why"], d["dry_streak"] = dl, dw, dry_streak
        out.append(d)
    return out


def main() -> None:
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else LAT
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else LON
    days = assess(fetch(lat, lon))

    print(f"坐标: ({lat}, {lon})  区间: {START} ~ {END}")

    # —— 热害 ——
    heat = [d for d in days if d["tmax"] >= 35]
    severe = [d for d in days if d["heat_level"] == "重"]
    peak = max(days, key=lambda d: d["tmax"])
    print("\n[热害回测]")
    print(f"≥35℃ 日数: {len(heat)}")
    print(f"热害『重』级日数: {len(severe)}")
    print(f"极端最高温: {peak['tmax']}℃（{peak['date']}）")
    if severe:
        print(f"首个『重』级提示: {severe[0]['date']}（{severe[0]['heat_why']}）")
        lead = (date.fromisoformat(peak["date"]) - date.fromisoformat(severe[0]["date"])).days
        print(f"提示相对峰值提前: {lead} 天")

    # —— 干旱 ——
    print("\n[干旱回测]（连旱日<1mm + 30天日历窗口累计；休眠期降权不适用于本区间）")
    d_first = next((d for d in days if d["drought_level"]), None)
    d_severe = [d for d in days if d["drought_level"] == "重"]
    d_mid = [d for d in days if d["drought_level"] == "中"]
    d_long = max(days, key=lambda d: d["dry_streak"])
    if d_first:
        print(f"首个『轻』级以上干旱提示: {d_first['date']}（{d_first['drought_why']}）")
    print(f"『重』级干旱日数: {len(d_severe)}  『中』级: {len(d_mid)}")
    print(f"最长连续无有效降水: {d_long['dry_streak']} 天（止于 {d_long['date']}）")
    if d_severe:
        print("『重』级明细（前10条）:")
        for d in d_severe[:10]:
            print(f"  {d['date']}  {d['drought_why']}")
    print("\n说明：干旱等级为格点再分析口径；连旱日数对格点降水误差比累计雨量更稳健。"
          "官方灾情对照与阈值校准列入试点任务（docs/03）。")


if __name__ == "__main__":
    main()
