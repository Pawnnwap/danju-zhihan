#!/usr/bin/env python3
"""丹橘知旱 · 风险引擎历史回测（四类风险）

前端 `phenology.js` + `app.js assess()` 的 Python 独立实现，同阈值同工程行为：
- 数据：Open-Meteo Archive API（ERA5-Land 再分析，免密钥）或本地 fixture（离线复算）
- 输出：热害（≥35℃ 日数、"重"级首日与相对峰值提前天数）、干旱（首个"轻"级以上日、各级日数）、
  花期/幼果低温、越冬冻害
- 与 JS 一致的行为：日期断链清零、三变量全非空过滤、30 天日历窗口、窗口不满 30 天不判干旱、
  阈值比较用原始值（取整只进展示文案）
- 等价性由 tools/parity_check.py 逐日对拍强制（含边界合成用例），不靠注释承诺

用法：
  python tools/backtest.py [lat] [lon]                     # 联网，默认凉水河镇 2022-06-01~09-30
  python tools/backtest.py --fixture tools/fixtures/liangshuihe_2022.json
  python tools/backtest.py --fixture ... --check           # 断言头条数字未漂移（回归闸门）
"""
import argparse
import json
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

LAT, LON = 32.60, 111.42
START, END = "2022-06-01", "2022-09-30"

# 物候期镜像（与 phenology.js 的 PHENOLOGY 同表）。干旱规则在休眠期不出等级，
# 本侧必须同样判断，否则冬季窗口的回测结果会与前端分叉。
PHENOLOGY = [
    ("dormant", (11, 25), (2, 29)),
    ("bud", (3, 1), (3, 31)),
    ("bud", (4, 1), (4, 9)),
    ("bloom", (4, 10), (5, 10)),
    ("young", (5, 11), (6, 20)),
    ("swell", (6, 21), (9, 20)),
    ("ripe", (9, 21), (11, 24)),
]

LEVELS = ("重", "中", "轻", "关注")


def pheno_id(cur: date) -> str:
    m, d = cur.month, cur.day
    for pid, (fm, fd), (tm, td) in PHENOLOGY:
        if fm <= tm:
            hit = (m > fm or (m == fm and d >= fd)) and (m < tm or (m == tm and d <= td))
        else:  # 跨年区间
            hit = (m > fm or (m == fm and d >= fd)) or (m < tm or (m == tm and d <= td))
        if hit:
            return pid
    return "dormant"


def fetch(lat: float, lon: float, start: str = START, end: str = END) -> list[dict]:
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        j = json.load(r)
    return filter_days(j["daily"])


def filter_days(d: dict) -> list[dict]:
    """与 JS fetchArchive 一致：三变量全非空才保留该日。"""
    days = []
    for i, t in enumerate(d["time"]):
        tmax, tmin, pr = d["temperature_2m_max"][i], d["temperature_2m_min"][i], d["precipitation_sum"][i]
        if tmax is None or tmin is None or pr is None:
            continue
        days.append({"date": t, "tmax": tmax, "tmin": tmin, "pr": pr})
    return days


def assess(days: list[dict]) -> list[dict]:
    """四类风险逐日判级（与前端引擎逐分支等价）。"""
    out = []
    streak = 0
    dry_streak = 0
    for i, d in enumerate(days):
        cur = date.fromisoformat(d["date"])
        if i > 0 and (cur - date.fromisoformat(days[i - 1]["date"])) != timedelta(days=1):
            streak = 0
            dry_streak = 0
        tmax, tmin, pr = d["tmax"], d["tmin"], d["pr"]
        pid = pheno_id(cur)

        # —— 热害 ——（与 phenology.js heat 逐分支一致）
        streak = streak + 1 if tmax >= 35 else 0
        if tmax >= 39:
            lv = "重"
        elif tmax >= 37:
            lv = "中"
        elif tmax >= 35:
            lv = "轻"
        elif tmax >= 33:
            lv = "关注"
        else:
            lv = None
        if streak >= 5 and tmax >= 35:
            lv = "重"
        elif streak >= 3 and tmax >= 35 and lv == "轻":
            lv = "中"

        # —— 干旱 ——（连旱日 <1mm + 30 天日历窗口累计；阈值比较用原始值）
        dry_streak = dry_streak + 1 if pr < 1 else 0
        rain30, win = 0.0, 0
        for j in range(i, -1, -1):
            if (cur - date.fromisoformat(days[j]["date"])).days > 29:
                break
            rain30 += days[j]["pr"]
            win += 1
        dl = None
        if i >= 29 and win >= 30 and pid != "dormant":
            if dry_streak >= 25 or rain30 <= 15:
                dl = "重"
            elif dry_streak >= 15 or rain30 <= 30:
                dl = "中"
            elif dry_streak >= 8 or rain30 <= 45:
                dl = "轻"

        # —— 花期/幼果低温（倒春寒）——
        cl = None
        if pid == "bloom":
            cl = "重" if tmin <= 2 else "中" if tmin <= 5 else "关注" if tmin <= 8 else None
        elif pid == "bud":
            cl = "重" if tmin <= 0 else "中" if tmin <= 2 else "轻" if tmin <= 4 else "关注" if tmin <= 7 else None
        elif pid == "young":
            cl = "重" if tmin <= 0 else "中" if tmin <= 2 else "轻" if tmin <= 5 else None

        # —— 越冬冻害 ——（休眠期，或成熟采收期 ≤-5℃）
        fl = None
        if pid == "dormant" or (pid == "ripe" and tmin <= -5):
            fl = "重" if tmin <= -7 else "中" if tmin <= -5 else "轻" if tmin <= -3 else None

        d.update({
            "pheno": pid, "heat_level": lv, "drought_level": dl, "cold_level": cl, "freeze_level": fl,
            "streak": streak, "dry_streak": dry_streak, "rain30": rain30, "window_days": win,
            "heat_why": f"{tmax}℃", "drought_why": f"连续{dry_streak}天无有效降水，30天累计{rain30:.1f}mm",
        })
        out.append(d)
    return out


HEADLINE = {  # --check 断言的头条数字（改动规则导致数字变化时必须同步全部材料，否则回归闸门失败）
    "heat_days_35": 30, "heat_severe": 13, "peak_tmax": 40.9, "peak_date": "2022-08-07",
    "first_severe": "2022-08-04", "lead_days": 3, "drought_mid": 3, "drought_severe": 0,
    "longest_dry_streak": 12,
}


def summarize(days: list[dict]) -> dict:
    heat = [d for d in days if d["tmax"] >= 35]
    severe = [d for d in days if d["heat_level"] == "重"]
    peak = max(days, key=lambda d: d["tmax"])
    first_severe = severe[0] if severe else None
    d_mid = sum(1 for d in days if d["drought_level"] == "中")
    d_severe = sum(1 for d in days if d["drought_level"] == "重")
    d_first = next((d for d in days if d["drought_level"]), None)
    d_long = max(days, key=lambda d: d["dry_streak"])
    return {
        "heat_days_35": len(heat),
        "heat_severe": len(severe),
        "peak_tmax": peak["tmax"],
        "peak_date": peak["date"],
        "first_severe": first_severe["date"] if first_severe else None,
        "lead_days": (date.fromisoformat(peak["date"]) - date.fromisoformat(first_severe["date"])).days
                     if first_severe else None,
        "drought_first": d_first["date"] if d_first else None,
        "drought_mid": d_mid,
        "drought_severe": d_severe,
        "longest_dry_streak": d_long["dry_streak"],
        # "重"级归因：单日 ≥39℃ 触发 vs 连续日数升级触发（评审 A-Q3：不得把时效记在升级机制头上，除非它真的先出现）
        "heat_severe_streak": sum(1 for d in days if d["heat_level"] == "重" and d["tmax"] < 39),
        "first_severe_streak": next((d["date"] for d in days if d["heat_level"] == "重" and d["tmax"] < 39), None),
        "first_severe_trigger": (None if not first_severe else
                                 "连续日数升级" if first_severe["tmax"] < 39 else "单日 ≥39℃"),
        "cold_days": sum(1 for d in days if d["cold_level"]),
        "freeze_days": sum(1 for d in days if d["freeze_level"]),
        "incomplete_window_days": sum(1 for d in days if d["window_days"] < 30),
    }


def report(days: list[dict], s: dict, source: str) -> None:
    print(f"数据源: {source}")
    print("\n[热害回测]")
    print(f"≥35℃ 日数: {s['heat_days_35']}")
    print(f"热害『重』级日数: {s['heat_severe']}")
    print(f"极端最高温: {s['peak_tmax']}℃（{s['peak_date']}）")
    if s["first_severe"]:
        d0 = next(d for d in days if d["heat_level"] == "重")
        print(f"首个『重』级提示: {s['first_severe']}（{d0['heat_why']}，当日连续 {d0['streak']} 天 ≥35℃）")
        print(f"首个『重』级触发方式: {s['first_severe_trigger']}")
        print(f"提示相对峰值提前: {s['lead_days']} 天")
        print(f"『重』级中由连续日数升级产生（当日 <39℃）: {s['heat_severe_streak']} 天"
              + (f"，最早 {s['first_severe_streak']}" if s["first_severe_streak"] else "（无）"))
    print("\n[干旱回测]（连旱日<1mm + 30天日历窗口累计；休眠期与窗口不满 30 天不判级）")
    if s["drought_first"]:
        print(f"首个『轻』级以上干旱提示: {s['drought_first']}")
        print(f"  {next(d for d in days if d['drought_level'])['drought_why']}")
    print(f"『重』级干旱日数: {s['drought_severe']}  『中』级: {s['drought_mid']}")
    print(f"最长连续无有效降水: {s['longest_dry_streak']} 天")
    print(f"回看窗口不满 30 天的日数（不判干旱）: {s['incomplete_window_days']}")
    print("\n[低温与冻害]（Python 侧同类实现，供对拍与基线核算）")
    print(f"花期/幼果低温触发日数: {s['cold_days']}  越冬冻害触发日数: {s['freeze_days']}")
    print("\n说明：干旱等级为格点再分析口径；连旱日数对格点降水误差比累计雨量更稳健。"
          "官方灾情对照与阈值校准列入试点任务（docs/03）。")


def main() -> int:
    ap = argparse.ArgumentParser(description="丹橘知旱历史回测")
    ap.add_argument("lat", type=float, nargs="?", default=None)
    ap.add_argument("lon", type=float, nargs="?", default=None)
    ap.add_argument("--fixture", type=Path, default=None,
                    help="本地冻结输入序列（JSON: {meta, daily:{time,...}}），离线复算用")
    ap.add_argument("--start", default=None, help="配 --fixture 使用的区间起点")
    ap.add_argument("--end", default=None, help="配 --fixture 使用的区间终点")
    ap.add_argument("--check", action="store_true", help="断言头条数字与 HEADLINE 一致（回归闸门）")
    args = ap.parse_args()

    if args.fixture:
        j = json.loads(args.fixture.read_text(encoding="utf-8"))
        meta = j.get("meta", {})
        daily = j["daily"]
        s0, e0 = args.start or meta.get("start"), args.end or meta.get("end")
        keep = [i for i, t in enumerate(daily["time"]) if s0 <= t <= e0]
        daily = {k: [v[i] for i in keep] for k, v in daily.items()}
        days = filter_days(daily)
        source = (f"fixture {args.fixture.name}（请求坐标 {meta.get('requested_lat')},{meta.get('requested_lon')} "
                  f"→ 实际格点 {meta.get('gridpoint_lat')},{meta.get('gridpoint_lon')}；"
                  f"{s0}~{e0}；拉取于 {meta.get('pulled_at_utc')}；共 {len(days)} 天）")
    else:
        lat = args.lat if args.lat is not None else LAT
        lon = args.lon if args.lon is not None else LON
        days = fetch(lat, lon)
        source = f"Open-Meteo Archive（坐标 {lat}, {lon}；{START}~{END}，当场联网）"

    assessed = assess(days)
    s = summarize(assessed)
    report(assessed, s, source)

    if args.check:
        bad = {k: (s[k], v) for k, v in HEADLINE.items() if s[k] != v}
        if bad:
            print("\n[CHECK FAILED] 头条数字与已发布材料不一致：")
            for k, (got, want) in bad.items():
                print(f"  {k}: 实算 {got} ≠ 材料 {want}")
            return 1
        print("\n[CHECK OK] 头条数字与材料一致（无需联网）：" +
              "、".join(f"{k}={v}" for k, v in HEADLINE.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
