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

# 蒸散亏缺（def30 = 近30天ET0累计 − 近30天累计降水, mm）分物候期阈值（全段 2016–2025 的 p75/p90/p99）。
# 与 phenology.js 的 DEF_THRESH 同表；唯一生成处是 tools/baseline_stats.py:derive_thresholds()，
# 由 parity_check.py 第 3 层断言把三方绑死（评审 A-Q9 的单一事实源问题）。
DEF_THRESH = {
    "dormant": {"轻": 34.0, "中": 40.7, "重": 48.4},
    "bud": {"轻": 43.0, "中": 56.8, "重": 84.0},
    "bloom": {"轻": 55.3, "中": 86.0, "重": 102.3},
    "young": {"轻": 83.5, "中": 102.3, "重": 116.7},
    "swell": {"轻": 54.1, "中": 87.4, "重": 122.6},
    "ripe": {"轻": 31.4, "中": 55.5, "重": 97.5},
}
# 开花期蒸散口径上限"中"（留出检验显示该档分位最不稳，见 docs/02 §5），2027 花期对表前不解
DEF_MAX_LEVEL = {"bloom": "中"}
RANK = {None: 0, "轻": 1, "中": 2, "重": 3}


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
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration"
        "&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        j = json.load(r)
    return filter_days(j["daily"])


def filter_days(d: dict) -> list[dict]:
    """与 JS fetchArchive 一致：三变量全非空才保留该日；ET0 可为 None（缺失时蒸散口径自动退出）。"""
    et = d.get("et0_fao_evapotranspiration") or [None] * len(d["time"])
    days = []
    for i, t in enumerate(d["time"]):
        tmax, tmin, pr = d["temperature_2m_max"][i], d["temperature_2m_min"][i], d["precipitation_sum"][i]
        if tmax is None or tmin is None or pr is None:
            continue
        e = et[i] if i < len(et) else None
        days.append({"date": t, "tmax": tmax, "tmin": tmin, "pr": pr, "et": e})
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

        # —— 热害 ——（与 phenology.js heat 逐分支一致；null 一律不判级）
        streak = streak + 1 if (tmax is not None and tmax >= 35) else 0
        lv = None
        if tmax is not None:
            if tmax >= 39:
                lv = "重"
            elif tmax >= 37:
                lv = "中"
            elif tmax >= 35:
                lv = "轻"
            elif tmax >= 33:
                lv = "关注"
            if streak >= 5 and tmax >= 35:
                lv = "重"
            elif streak >= 3 and tmax >= 35 and lv == "轻":
                lv = "中"

        # —— 干旱 ——（双口径取高者，与 phenology.js drought 逐分支一致；阈值比较一律用原始值）
        dry_streak = dry_streak + 1 if (pr is not None and pr < 1) else 0
        rain30, win, et30, et_missing, bad = 0.0, 0, 0.0, 0, 0
        for j in range(i, -1, -1):
            if (cur - date.fromisoformat(days[j]["date"])).days > 29:
                break
            win += 1
            pj = days[j]
            if pj["pr"] is None or pj["tmax"] is None:  # 窗口内有缺测日 → 不判级（显式缺测语义）
                bad += 1
                continue
            rain30 += pj["pr"]
            e = pj.get("et")
            if e is None:
                et_missing += 1
            else:
                et30 += e
        dl = None
        def30 = None
        if i >= 29 and win >= 30 and bad == 0:
            rl = None
            if pid != "dormant":  # 降水口径在休眠期不判级（照判则 89.8% 天数带级，见 docs/02）
                if dry_streak >= 25 or rain30 <= 15:
                    rl = "重"
                elif dry_streak >= 15 or rain30 <= 30:
                    rl = "中"
                elif dry_streak >= 8 or rain30 <= 45:
                    rl = "轻"
            dv = None
            def30 = None
            if et_missing == 0 and pid in DEF_THRESH:
                def30 = et30 - rain30
                th = DEF_THRESH[pid]
                if def30 >= th["重"]:
                    dv = "重"
                elif def30 >= th["中"]:
                    dv = "中"
                elif def30 >= th["轻"]:
                    dv = "轻"
                cap = DEF_MAX_LEVEL.get(pid)
                if cap and RANK.get(dv, 0) > RANK[cap]:
                    dv = cap
            dl = rl if RANK.get(rl, 0) >= RANK.get(dv, 0) else dv
        d["drought_level"] = dl
        d["drought_why"] = (f"连续{dry_streak}天无有效降水，30天累计{rain30:.1f}mm"
                           f"；def30={'' if def30 is None else f'{def30:.1f}mm'}")
        d.update({"dry_streak": dry_streak, "rain30": rain30, "et30": None if et_missing else et30,
                  "window_days": win, "window_bad": bad})

        # —— 花期/幼果低温（倒春寒）——
        cl = None
        if tmin is not None:
            if pid == "bloom":
                cl = "重" if tmin <= 2 else "中" if tmin <= 5 else "关注" if tmin <= 8 else None
            elif pid == "bud":
                cl = "重" if tmin <= 0 else "中" if tmin <= 2 else "轻" if tmin <= 4 else "关注" if tmin <= 7 else None
            elif pid == "young":
                cl = "重" if tmin <= 0 else "中" if tmin <= 2 else "轻" if tmin <= 5 else None

        # —— 越冬冻害 ——（休眠期，或成熟采收期 ≤-5℃）
        fl = None
        if tmin is not None and (pid == "dormant" or (pid == "ripe" and tmin <= -5)):
            fl = "重" if tmin <= -7 else "中" if tmin <= -5 else "轻" if tmin <= -3 else None

        d.update({
            "pheno": pid, "heat_level": lv, "drought_level": dl, "cold_level": cl, "freeze_level": fl,
            "streak": streak, "dry_streak": dry_streak, "rain30": rain30, "window_days": win,
            "heat_why": f"{tmax}℃", "drought_why": f"连续{dry_streak}天无有效降水，30天累计{rain30:.1f}mm",
        })
        out.append(d)
    return out


HEADLINE = {  # --check 断言的头条数字（改动规则导致数字变化时必须同步全部材料，否则回归闸门失败）
    # 2026-10-13 起：①干旱为"降水型 ∪ 蒸散型取高者"双口径；②回测改为带 35 天 warm-up 喂数，
    # 与产品运行时（前端取 now−100d）一致。此前"重 0/中 3"是无 warm-up 且窗口首月被人为沉默的伪影。
    "heat_days_35": 30, "heat_severe": 13, "peak_tmax": 40.9, "peak_date": "2022-08-07",
    "first_severe": "2022-08-04", "lead_days": 3,
    "drought_first": "2022-06-01", "drought_mid": 54, "drought_severe": 8,
    "longest_dry_streak": 12, "incomplete_window_days": 0,
}


WARMUP_DAYS = 35  # 引擎需要 30 天回看窗口；切片前额外取 35 天，使窗口首日与产品运行时同样"看得见"历史（评审 A-Q1）


def windowed(assessed: list[dict], start: str, end: str) -> list[dict]:
    """只保留报告区间内的日，但等级是在带 warm-up 的完整序列上算出来的。"""
    return [d for d in assessed if start <= d["date"] <= end]


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
        "incomplete_window_days": sum(1 for d in days if d["window_days"] < 30 or d.get("window_bad", 0)),
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
    if source:
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


if hasattr(sys.stdout, "reconfigure"):  # Windows 默认 GBK 控制台降级输出，不崩
    sys.stdout.reconfigure(errors="replace")

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
        w0 = (date.fromisoformat(s0) - timedelta(days=WARMUP_DAYS)).isoformat()
        keep = [i for i, t in enumerate(daily["time"]) if w0 <= t <= e0]
        sl = {k: [v[i] for i in keep] for k, v in daily.items()}
        source = (f"fixture {args.fixture.name}（请求坐标 {meta.get('requested_lat')},{meta.get('requested_lon')} "
                  f"→ 实际格点 {meta.get('gridpoint_lat')},{meta.get('gridpoint_lon')}；"
                  f"报告区间 {s0}~{e0}，另取 warm-up {w0}~{s0}；拉取于 {meta.get('pulled_at_utc')}）")
    else:
        s0, e0 = START, END
        lat = args.lat if args.lat is not None else LAT
        lon = args.lon if args.lon is not None else LON
        w0 = (date.fromisoformat(s0) - timedelta(days=WARMUP_DAYS)).isoformat()
        sl = None
        days_raw = fetch(lat, lon, w0, e0)
        source = f"Open-Meteo Archive（坐标 {lat}, {lon}；报告区间 {s0}~{e0}，warm-up 自 {w0}，当场联网）"

    days = days_raw if sl is None else filter_days(sl)
    assessed = windowed(assess(days), s0, e0)
    print(f"数据源: {source}")
    print(f"报告区间天数: {len(assessed)}（其中 ET0 全程可用 {sum(1 for d in assessed if d.get('et30') is not None)} 天；"
          f"窗口有缺测 {sum(1 for d in assessed if d.get('window_bad'))} 天）")
    s = summarize(assessed)
    report(assessed, s, "")

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
