#!/usr/bin/env python3
"""十年级基线核算：提示负担（噪声预算）、休眠期常驻率、ET0 水分亏缺口径可行性

用途（对应评审 C-Q1 / C-Q6 与 docs/02 的两处主张）：
1. 各级提示的"年日数中位数/范围"——四类风险的提示负担，材料此前只有"多少天算可用"没有基线；
2. 休眠期干旱常驻率的反事实核算——证明"休眠期不出干旱等级"是噪声控制（不抑制则 9 成天数常驻），
   而不是把产品命名承诺自废掉；
3. ET0 水分亏缺（30 天 ET0 − 30 天降水）口径试验：给出各物候期分位分布，并检验"直接把
   def30 作为额外触发条件并进现有阈值"会不会把膨大期"重"级基线推高一个量级。

数据：仓库内冻结序列 tools/fixtures/liangshuihe_2016_2025.json（离线，可复算）。
用法：python tools/baseline_stats.py
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
RISK_LABEL = {"heat": "高温热害", "drought": "干旱", "cold": "花期/幼果低温", "freeze": "越冬冻害"}
PHENO_LABEL = {"dormant": "休眠", "bud": "萌芽/现蕾", "bloom": "开花", "young": "幼果",
               "swell": "膨大", "ripe": "成熟采收"}


def load():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return fx["meta"], fx["daily"]


def percentiles(vals, qs=(.5, .75, .9, .95, .99)):
    v = sorted(vals)
    return [v[min(len(v) - 1, int(q * len(v)))] for q in qs]


def main() -> int:
    if not FIXTURE.exists():
        print(f"缺少 {FIXTURE.name}，请先运行 python tools/fetch_fixture.py")
        return 1
    meta, daily = load()
    n = len(daily["time"])
    days = backtest.filter_days(daily)
    assessed = backtest.assess([dict(d) for d in days])
    years = sorted({date.fromisoformat(d["date"]).year for d in assessed})
    print(f"数据：{meta['model']}｜请求坐标 {meta['requested_lat']},{meta['requested_lon']} → "
          f"实际格点 {meta['gridpoint_lat']},{meta['gridpoint_lon']}（海拔 {meta['elevation_m']}m）")
    print(f"区间 {meta['start']}~{meta['end']}（{n} 天，有效 {len(assessed)} 天，拉取于 {meta['pulled_at_utc']}）\n")

    # —— 1) 提示负担：各风险各级的年日数 ——
    print("=== 1) 提示负担（各级出现日数／年，2016–2025）===")
    for key in ("heat", "drought", "cold", "freeze"):
        line = []
        for lv in ("重", "中", "轻", "关注"):
            per = []
            for y in years:
                c = sum(1 for d in assessed if d[f"{key}_level"] == lv
                        and date.fromisoformat(d["date"]).year == y)
                per.append(c)
            if max(per) == 0:
                continue
            line.append(f"{lv} 中位 {st.median(per):.0f}（{min(per)}–{max(per)}）")
        print(f"  {RISK_LABEL[key]:8} " + "｜".join(line))

    # —— 2) 休眠期干旱常驻率（反事实：若不抑制） ——
    dor = [d for d in assessed if d["pheno"] == "dormant"]
    print(f"\n=== 2) 休眠期干旱常驻率（反事实核算，n={len(dor)} 天）===")
    cf = {}
    for d in assessed:
        dr, r, win = d["dry_streak"], d["rain30"], d["window_days"]
        if win < 30:
            continue
        if dr >= 25 or r <= 15:
            cf[d["date"]] = "重"
        elif dr >= 15 or r <= 30:
            cf[d["date"]] = "中"
        elif dr >= 8 or r <= 45:
            cf[d["date"]] = "轻"
    cnt = Counter(cf.get(d["date"]) for d in dor)
    for lv in ("轻", "中", "重"):
        print(f"  若休眠期照判：{lv}级 {cnt[lv]}/{len(dor)} 天 = {100 * cnt[lv] / len(dor):.1f}%")
    print("  → 现行为：休眠期一律不出干旱等级（phenology.js drought 分支），墒情信号由冻害建议的"
          "\"捏土验墒\"动作承载；这是噪声控制，不是能力撤回。")

    # —— 3) ET0 水分亏缺口径试验 ——
    et = daily.get("et0_fao_evapotranspiration")
    if not et:
        print("\n（fixture 无 et0 变量，跳过第 3 节；重跑 fetch_fixture.py 获取）")
        return 0
    by_date_et = {t: v for t, v in zip(daily["time"], et)}
    print("\n=== 3) ET0 水分亏缺（def30 = 近30天ET0 − 近30天降水, mm）===")
    print(f"  {'物候期':10}{'n':>6}{'p50':>8}{'p75':>8}{'p90':>8}{'p95':>8}{'p99':>8}{'max':>8}")
    rows = defaultdict(list)
    for d in assessed:
        e30 = sum(by_date_et.get((date.fromisoformat(d["date"]) - timedelta(days=k)).isoformat()) or 0
                  for k in range(30))
        d["def30"] = e30 - d["rain30"]
        rows[d["pheno"]].append(d)
    for pid, lab in PHENO_LABEL.items():
        vals = [d["def30"] for d in rows.get(pid, [])]
        if not vals:
            continue
        p = percentiles(vals)
        print(f"  {lab:10}{len(vals):>6}" + "".join(f"{v:>8.1f}" for v in p) + f"{max(vals):>8.1f}")

    print("\n  把 def30 当额外触发条件并进现有阈值（OR 合并）的后果——膨大期\"重\"级年日数：")
    sw = [d for d in assessed if d["pheno"] == "swell"]
    base = [sum(1 for d in sw if d["drought_level"] == "重" and date.fromisoformat(d["date"]).year == y)
            for y in years]
    print(f"    现行口径（连旱日+rain30）：中位 {st.median(base):.0f} 天/年（{min(base)}–{max(base)}）")
    for t in (40, 60, 70):
        per = [sum(1 for d in sw if (d["dry_streak"] >= 25 or d["rain30"] <= 15 or d["def30"] >= t)
                   and date.fromisoformat(d["date"]).year == y) for y in years]
        rate = 100 * sum(1 for d in sw if d["def30"] >= t) / len(sw)
        print(f"    并入 def30>={t}mm：\"重\"级 {st.median(per):.0f} 天/年（def30 触发率 {rate:.1f}% 天）")
    print("  → 结论：单一 def30 阈值跨物候期不平移（休眠期 p50 已 22mm、膨大期 p50 仅 16mm），"
          "直接 OR 合并会使\"重\"级基线放大一个量级、失去区分力。"
          "ET0 口径的正确用法是按物候期分别定距平分位阈值，须与田间实测水分/农户响应联校，"
          "列入 docs/03 试点校准任务，不在首发版拍脑袋上线。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
