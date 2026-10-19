#!/usr/bin/env python3
"""格点敏感性核算：把"9km 格点≠单园实测"从一句局限变成可复算的数字

动机（docs/02 局限 1 与评审 A-Q9/C-Q1 的共同质疑）：材料一直承认 ERA5-Land 是约 9km 格点、
与具体园块有偏差，但从没量化过"换一个相邻格点，结论会不会变"。本脚本对同一坐标取周边
若干格点（Open-Meteo 会各自落到最近的模型网格点），比较：
1. 2022-06-01~09-30 区间的热害/干旱各级日数；
2. 最远两格点之间判级不一致的天数占比（逐日取四类风险的worst等级比较）。
结果用于 docs/02 局限 1：说明"乡镇尺度背景场"这个定位有多硬，以及哪一侧的风险最依赖格点选择。

用法：python tools/grid_sensitivity.py            # 联网（{len(OFFSETS)} 个请求点，落点去重后 ≥3 个格点）
"""
import argparse
import json
import statistics as st
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE_LAT, BASE_LON = 32.60, 111.42   # 凉水河镇
STEP = 0.10                          # ≈11km：ERA5-Land 网格间距约 0.1°，取 0.05° 时相邻请求点
                                     # 会落回同一个网格单元（评审 A-R13③），"跨过一格"这句在旧取值下不成立
S0, E0 = "2022-06-01", "2022-09-30"
W0 = (date.fromisoformat(S0) - timedelta(days=backtest.WARMUP_DAYS)).isoformat()
OFFSETS = [(0, 0), (STEP, 0), (0, STEP), (-STEP, -STEP)]
FIXTURE = HERE / "fixtures" / "grid_sens_2022.json"   # --save 的落点；--verify 离线复算的唯一输入
RISK_CN = {"heat": "高温热害", "drought": "干旱", "cold": "花期/幼果低温", "freeze": "越冬冻害"}


def verify() -> int:
    """离线复算：冻结件里有逐日判级序列与摘要，重算分歧率/提前量/极差并逐项断言。
    快照必须由 --save 生成（含实际落点、高程、逐日读数），材料里的数字以这份断言为准——
    不再是"作者跑一次、读者只能信"（评审 R13②）。"""
    if not FIXTURE.exists():
        print(f"[FAIL] {FIXTURE.name} 不存在：先联网跑一次 python tools/grid_sensitivity.py --save")
        return 1
    j = json.loads(FIXTURE.read_text(encoding="utf-8"))
    maps, leads, heavy = [], [], []
    for g in j["grids"]:
        rows = g["days"]
        maps.append({d["date"]: d for d in rows})
        leads.append(g["lead_days"])
        heavy.append(g["drought_severe"] + g["heat_severe"])
    common = sorted(set.intersection(*[set(m) for m in maps]))
    n = len(common)
    disagree = sum(1 for dt in common
                   if len({worst(m[dt]) for m in maps}) > 1)
    per_risk = {}
    for dt in common:
        for key in ("heat", "drought", "cold", "freeze"):
            if len({risk_worst(m[dt], key) for m in maps}) > 1:
                per_risk[key] = per_risk.get(key, 0) + 1
    s = j["summary"]
    checks = [
        ("格点数", len(j["grids"]), s["n_grids"]),
        ("重叠天数", n, s["n_days"]),
        ("worst 不一致天数", disagree, s["worst_disagree"]),
        ("重叠日", (date.fromisoformat(common[0]), date.fromisoformat(common[-1])),
         (date.fromisoformat(s["first_day"]), date.fromisoformat(s["last_day"]))),
    ]
    for k in ("heat", "drought", "cold", "freeze"):
        checks.append((f"{RISK_CN[k]}不一致", per_risk.get(k, 0), s["per_risk"][k]))
    checks += [("提前量序列", leads, s["leads"]),
               ("重级日数区间", [min(heavy), max(heavy)], s["heavy_min_max"])]
    bad = [(name, got, want) for name, got, want in checks if got != want]
    for name, got, want in checks:
        mark = "OK" if got == want else "X "
        print(f"  [{mark}] {name}: 现算 {got} vs 快照 {want}")
    if bad:
        print(f"[FAIL] {FIXTURE.name} 与现算不符 {len(bad)} 项——格点数据或引擎变了，需 --save 重冻结并同步材料")
        return 1
    print(f"[OK] {FIXTURE.name}（拉取于 {j['meta']['fetched_at_utc']}）与离线复算一致；"
          "材料中的格点敏感性数字以本断言为准")
    return 0


def fetch(lat: float, lon: float) -> list[dict]:
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={W0}&end_date={E0}"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration"
           "&timezone=Asia%2FShanghai")
    with urllib.request.urlopen(url, timeout=60) as r:
        j = json.load(r)
    days = backtest.windowed(backtest.assess(backtest.filter_days(j["daily"])), S0, E0)
    # 高程一并取出（评审 A-R12⑦）：相邻请求点之间的判级差异，有一部分其实只是 Open-Meteo
    # 按高程做的网格订正；不打印 elevation 就分不开"真的换了网格"与"只是海拔订正"
    return days, (j["latitude"], j["longitude"]), j.get("elevation")


def worst(d: dict) -> str:
    """四类风险里最重的一级。"关注"必须有独立权重（评审 A-Q8）：
    旧实现把 关注 与 无 都记 0，于是"一个格点提示接近阈值、另一个沉默"这种真实分歧被抹平，
    格点敏感性被系统性低估。"""
    order = {"重": 3, "中": 2, "轻": 1, "关注": 0}
    present = [d[f"{k}_level"] for k in ("heat", "drought", "cold", "freeze") if d[f"{k}_level"]]
    if not present:
        return "无"
    return max(present, key=lambda x: order[x])


def risk_worst(d: dict, key: str) -> str:
    return d[f"{key}_level"] or "无"


def main() -> int:
    ap = argparse.ArgumentParser(description="格点敏感性：联网取数 --save 冻结原始读数；--verify 离线复算并断言")
    ap.add_argument("--save", action="store_true",
                    help="联网取数并把逐日判级序列与摘要冻结进 tools/fixtures/grid_sens_2022.json")
    ap.add_argument("--verify", action="store_true",
                    help="离线：只用冻结件重算分歧率/提前量/极差，与快照逐项断言（不联网）")
    args = ap.parse_args()
    if args.verify:
        return verify()
    series = []
    print(f"基准坐标 ({BASE_LAT}, {BASE_LON})，{len(OFFSETS)} 个请求点（按实际落点去重后再比对），"
          f"区间 {S0}~{E0}，warm-up 自 {W0}")
    for dlat, dlon in OFFSETS:
        lat, lon = round(BASE_LAT + dlat, 4), round(BASE_LON + dlon, 4)
        days, grid, elev = fetch(lat, lon)
        s = backtest.summarize(days)
        series.append((lat, lon, grid, days, s, elev))
        print(f"  请求 {lat},{lon} → 实际格点 {grid[0]:.4f},{grid[1]:.4f}（高程 {elev} m）"
              f"｜热害 重{s['heat_severe']}/≥35℃ {s['heat_days_35']}"
              f"｜干旱 重{s['drought_severe']}/中{s['drought_mid']}｜冻害日 {s['freeze_days']}｜低温日 {s['cold_days']}"
              f"｜热害首个重级 {s.get('first_severe')}、峰值 {s.get('peak_date')} → 提前 {s['lead_days']} 天")
    # 去重（评审 A-R13③）：请求点不等于格点。落到同一实际格点的请求必须并掉，
    # 否则分歧率与"提前量极差"都建立在含重复序列的集合上，样本被虚增
    uniq = {}
    for x in series:
        uniq.setdefault((round(x[2][0], 4), round(x[2][1], 4)), x)
    if len(uniq) < len(series):
        print(f"  [去重] 请求 {len(series)} 个点落到 {len(uniq)} 个不同实际格点，后面的比对只用这 {len(uniq)} 个")
        series = list(uniq.values())
    if len(series) < 3:
        print("  [FAIL] 去重后不足 3 个格点，分歧率与极差的口径不成立")
        return 1

    # 按日期对齐而不是按下标（评审 A-Q8）：某格点有缺测日时按下标比会把错位的日当成"分歧"，
    # 分歧率被虚高；只取所有格点都存在的日，并显式报出被剔除的日数
    lens = [len(x[3]) for x in series]
    maps = [{d["date"]: d for d in x[3]} for x in series]
    common = sorted(set.intersection(*[set(m) for m in maps]))   # 动态取交集：去重后可能只剩 3 个格点，
                                                                  # 硬写 maps[0..3] 会 IndexError
    n = len(common)
    disagree = 0
    spread_cases = []
    per_risk = {}
    for i, dt in enumerate(common):
        lv = [worst(m[dt]) for m in maps]
        if len(set(lv)) > 1:
            disagree += 1
            if len(spread_cases) < 5 and "重" in lv and "无" in lv:
                spread_cases.append((dt, lv))
        for key in ("heat", "drought", "cold", "freeze"):
            rlv = [risk_worst(m[dt], key) for m in maps]
            if len(set(rlv)) > 1:
                per_risk[key] = per_risk.get(key, 0) + 1
    if max(lens) - min(lens):
        print(f"  [注意] 格点序列长度不齐（{min(lens)}–{max(lens)} 天），已按日期取交集 {n} 天比对")
    print(f"\n逐日 worst 等级在 {len(series)} 个格点间不一致的天数：{disagree}/{n} = {100 * disagree / n:.1f}%"
          "（worst 含\"关注\"独立一档）")
    print("  分风险不一致日数：" + "｜".join(
        f"{RISK_CN[k]} {per_risk.get(k, 0)}/{n}" for k in ("heat", "drought", "cold", "freeze")))
    if spread_cases:
        print("  跨度最大的样例（同日不同格点从『重』到『无』）：")
        for dt, lv in spread_cases:
            print(f"    {dt} {lv}")
    heavy = [x[4]["drought_severe"] + x[4]["heat_severe"] for x in series]
    print(f"高级别（重）日数在格点间的极差：{max(heavy) - min(heavy)} 天（最少 {min(heavy)}，最多 {max(heavy)}）")
    leads = [x[4]["lead_days"] for x in series]
    print(f"「提前 N 天」在 {len(series)} 个格点上的取值：{'、'.join(str(v) for v in leads)}"
          f"（极差 {max(leads) - min(leads)} 天）——只报单格点的提前量会掩盖这个离散（评审 A-R12⑦）")
    elevs = [x[5] for x in series]
    print(f"各格点的高程：{'、'.join(str(v) for v in elevs)} m"
          "——格点间判级差异里有多少来自 Open-Meteo 的高程订正而非真的换了网格，据此判读")
    if args.save:
        snap = {
            "meta": {
                "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "base": [BASE_LAT, BASE_LON], "step_deg": STEP, "offsets": OFFSETS,
                "window": [S0, E0], "warmup_days": backtest.WARMUP_DAYS,
                "note": "逐日序列是 assess() 之后的判级行；--verify 只用本文件离线复算",
            },
            "grids": [
                {"requested": [x[0], x[1]], "grid": [round(x[2][0], 4), round(x[2][1], 4)],
                 "elevation_m": x[5], "lead_days": x[4]["lead_days"],
                 "heat_severe": x[4]["heat_severe"], "drought_severe": x[4]["drought_severe"],
                 "days": x[3]}
                for x in series
            ],
            "summary": {
                "n_grids": len(series), "n_days": n, "worst_disagree": disagree,
                "first_day": common[0], "last_day": common[-1],
                "per_risk": {k: per_risk.get(k, 0) for k in ("heat", "drought", "cold", "freeze")},
                "leads": leads, "heavy_min_max": [min(heavy), max(heavy)],
            },
        }
        FIXTURE.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        print(f"  [冻结] 逐日读数与摘要已写入 {FIXTURE.name}——此后 python tools/grid_sensitivity.py --verify 离线断言")
    print("\n读法：本工具输出的是乡镇尺度背景场，不是单园实测；格点选择本身可造成上表量级的差异，"
          "因此页面与建议均要求结合园地位置判断（冻害触发即附此声明），且正式版需接乡镇级实况数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
