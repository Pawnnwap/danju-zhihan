#!/usr/bin/env python3
"""丹橘知旱 · 风险引擎历史回测（2022 年夏季热害）

与前端 app.js 同一套阈值规则，用 Python 独立复算，验证提示时效：
- 数据：Open-Meteo Archive API（ERA5-Land 再分析，免密钥）
- 输出：≥35℃ 日数、"重"级提示首日、峰值日期与提前天数

用法：python backtest.py [lat] [lon]
默认坐标：丹江口市凉水河镇（32.60, 111.42）
"""
import json
import sys
import urllib.request
from datetime import date

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
    return [
        {"date": t, "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i], "pr": d["precipitation_sum"][i]}
        for i, t in enumerate(d["time"])
        if d["temperature_2m_max"][i] is not None
    ]


def assess(days: list[dict]) -> list[dict]:
    """与前端 phenology.js/app.js 相同的阈值：35/37/39℃，连续≥35℃ 3/5 天升级。"""
    out = []
    streak = 0
    for i, d in enumerate(days):
        streak = streak + 1 if d["tmax"] >= 35 else 0
        t = d["tmax"]
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
        if lv in ("轻", "中") and streak >= 5:
            lv, why = "重", f"{t}℃，连续{streak}天≥35℃"
        elif lv in ("轻",) and streak >= 3:
            lv, why = "中", f"{t}℃，连续{streak}天≥35℃"
        elif lv in ("中",) and streak >= 5:
            lv, why = "重", f"{t}℃，连续{streak}天≥35℃"
        out.append({**d, "level": lv, "why": why, "streak": streak})
    return out


def main() -> None:
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else LAT
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else LON
    days = assess(fetch(lat, lon))
    heat = [d for d in days if d["tmax"] >= 35]
    severe = [d for d in days if d["level"] == "重"]
    peak = max(days, key=lambda d: d["tmax"])
    print(f"坐标: ({lat}, {lon})  区间: {START} ~ {END}")
    print(f"≥35℃ 日数: {len(heat)}")
    print(f"热害『重』级日数: {len(severe)}")
    print(f"极端最高温: {peak['tmax']}℃（{peak['date']}）")
    if severe:
        print(f"首个『重』级提示: {severe[0]['date']}（{severe[0]['why']}）")
        lead = (date.fromisoformat(peak["date"]) - date.fromisoformat(severe[0]["date"])).days
        print(f"提示相对峰值提前: {lead} 天")
    print("逐日『重』级明细:")
    for d in severe[:15]:
        print(f"  {d['date']}  {d['why']}")


if __name__ == "__main__":
    main()
