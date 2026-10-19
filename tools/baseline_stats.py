#!/usr/bin/env python3
"""十年级基线与定标核算：提示负担、休眠期常驻率、蒸散亏缺口径（含留出检验）

对应评审 C-Q1/C-Q6、A-Q5/A-Q6，也回答"诚实标注之外能力到底怎样"：
1. 提示负担：四类风险各级的年日数（中位数/范围）——材料此前只说"多少算可用"没有基线；
2. 休眠期常驻率（反事实）：证明"降水口径在休眠期沉默"是噪声控制（照判则 89.8% 天数带级）；
3. def30 = 近 30 天 ET0 累计 − 近 30 天累计降水：
   - **分位阈值的唯一生成处**（phenology.js / backtest.py 的 DEF_THRESH 必须与此一致，
     由 tools/parity_check.py 第 3 层断言绑定，评审 A-Q9）；
   - **留出检验**：用 2016–2020 定标，在 2021–2025 评估，避免同一份样本既是考卷又是答案（A-Q6）；
   - **可达性分解**：分别统计降水口径各子条件与蒸散口径的命中次数，防止把"结构不可达"读成"信号稀有"（A-Q6a）；
   - **主导变量检查**：对比各物候期 ET0 与降水的 30 天中位数，说明 def30 主要由降水驱动（A-Q6b）。

缺测语义与运行时一致：30 天窗口内 ET0 有任一缺失的日，一律不参与定标（评审 A-Q5——
早前脚本把缺测 ET0 当 0mm 计入，与线上"缺测即停用蒸散口径"是两套语义）。

数据：tools/fixtures/liangshuihe_2016_2025.json（离线）。
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
RISK_LABEL = {"heat": "高温热害", "drought": "干旱", "cold": "花期/幼果低温", "freeze": "越冬冻害"}
PHENO_IDS = ["dormant", "bud", "bloom", "young", "swell", "ripe"]
PHENO_LABEL = {"dormant": "休眠", "bud": "萌芽/现蕾", "bloom": "开花", "young": "幼果",
               "swell": "膨大", "ripe": "成熟采收"}
CALIB_YEARS = range(2016, 2021)   # 定标段
EVAL_YEARS = range(2021, 2026)    # 留出评估段


def percentiles(vals, qs):
    """线性插值分位（与 numpy.percentile(..., method="linear") 同定义，评审 A-Q4）。

    此前用下标法 v[int(q*n)]，第三方按 numpy 默认口径复算会对不上（开花期 p99 差 0.8mm），
    而材料承诺"可由冻结序列现算"——承诺的必须是别人也能算出来的那个定义。
    """
    v = sorted(vals)
    out = []
    for q in qs:
        pos = (len(v) - 1) * q
        lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
        out.append(v[lo] + (v[hi] - v[lo]) * (pos - lo))
    return out


def load():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return fx["meta"], fx["daily"]


def assess_all(daily):
    return backtest.assess([dict(d) for d in backtest.filter_days(daily)])


def et0_30(daily):
    """每日的 30 天 ET0 累计与是否完整（与运行时同语义）。"""
    et = daily.get("et0_fao_evapotranspiration") or []
    by = {t: v for t, v in zip(daily["time"], et)}
    out = {}
    for d in daily["time"]:
        cur = date.fromisoformat(d)
        vals = [by.get((cur - timedelta(days=k)).isoformat()) for k in range(30)]
        if any(v is None for v in vals):
            out[d] = None
        else:
            out[d] = sum(vals)
    return out


def derive_thresholds(assessed, et30_by_date, years=None):
    """def30 分物候期阈值（轻=p75／中=p90／重=p99），只用 ET0 完整的日（与运行时同语义，评审 A-Q5）。

    重级取 p99 而非 p95：留出检验显示分位阈值跨期不稳（开花期用 2016–20 的 p95 在 2021–25 判到 20% 天数），
    而"重"是用户会据以行动的等级，不经田间水分实测就必须落在极端分位上。
    years=None 表示全段定标（代码内使用的即此表）。
    """
    vals = defaultdict(list)
    dropped = 0
    for d in assessed:
        e = et30_by_date.get(d["date"])
        if e is None or d.get("et30") is None:
            dropped += 1
            continue
        year = date.fromisoformat(d["date"]).year
        if years is not None and year not in years:
            continue
        vals[d["pheno"]].append(e - d["rain30"])
    th = {}
    for pid, v in vals.items():
        p75, p90, p99 = percentiles(v, (.75, .90, .99))
        th[pid] = {"轻": round(p75, 1), "中": round(p90, 1), "重": round(p99, 1)}
    return th, dropped, vals


def def_level(def30, th):
    if def30 >= th["重"]:
        return "重"
    if def30 >= th["中"]:
        return "中"
    if def30 >= th["轻"]:
        return "轻"
    return None


def rain_subhits(assessed):
    """降水口径各子条件按物候期的命中次数——把"结构不可达"和"信号稀有"分开。"""
    c = defaultdict(Counter)
    for d in assessed:
        if d.get("window_days", 0) < 30:
            continue
        pid = d["pheno"]
        if d["dry_streak"] >= 25:
            c[pid]["连旱≥25"] += 1
        if d["rain30"] <= 15:
            c[pid]["雨30≤15"] += 1
        if d["rain30"] <= 30:
            c[pid]["雨30≤30"] += 1
        if d["rain30"] <= 45:
            c[pid]["雨30≤45"] += 1
    return c


def main() -> int:
    if not FIXTURE.exists():
        print(f"缺少 {FIXTURE.name}，请先运行 python tools/fetch_fixture.py")
        return 1
    meta, daily = load()
    assessed = assess_all(daily)
    et30 = et0_30(daily)
    years = sorted({date.fromisoformat(d["date"]).year for d in assessed})
    print(f"数据：{meta['model']}｜请求坐标 {meta['requested_lat']},{meta['requested_lon']} → "
          f"实际格点 {meta['gridpoint_lat']},{meta['gridpoint_lon']}（海拔 {meta['elevation_m']}m）")
    print(f"区间 {meta['start']}~{meta['end']}（{len(assessed)} 天，无缺测日）｜拉取 {meta['pulled_at_utc']}")
    print(f"定标段 {min(CALIB_YEARS)}–{max(CALIB_YEARS)}，留出评估段 {min(EVAL_YEARS)}–{max(EVAL_YEARS)}\n")

    # —— 1) 提示负担 ——
    print("=== 1) 提示负担（各级出现日数／年，现行引擎口径）===")
    for key in ("heat", "drought", "cold", "freeze"):
        parts = []
        for lv in ("重", "中", "轻", "关注"):
            per = [sum(1 for d in assessed if d[f"{key}_level"] == lv
                       and date.fromisoformat(d["date"]).year == y) for y in years]
            if max(per) == 0:
                continue
            parts.append(f"{lv} 中位 {st.median(per):.0f}（{min(per)}–{max(per)}）")
        print(f"  {RISK_LABEL[key]:8} " + "｜".join(parts))
    # 1b) 留出段负担：干旱的蒸散口径阈值只用 2016–2020 定标，2021–2025 才是可引用的负担口径
    print("  ── 1b) 干旱负担只取留出段 2021–2025（定标段 2016–2020 的分位命中是算术恒等式，不作证据）──")
    for lv in ("轻", "中", "重"):
        per = [sum(1 for d in assessed if d["drought_level"] == lv
                   and date.fromisoformat(d["date"]).year == y) for y in EVAL_YEARS]
        print(f"    {lv} 中位 {st.median(per):.0f}（{min(per)}–{max(per)}）")
    # 佐证规则的不变量：降水口径未到"中"（连旱<15 且 雨30>30）的日子，不得出现"重"级
    viol = [d["date"] for d in assessed if d["drought_level"] == "重" and d["pheno"] != "dormant"
            and d.get("dry_streak", 0) < 15 and d.get("rain30", 0) > 30]
    print(f"    佐证规则不变量（降水口径未达「中」却判「重」）违反天数：{len(viol)}"
          + (f" → {viol[:5]}" if viol else "（应为 0）"))

    # —— 2) 休眠期降水口径常驻率（反事实） ——
    dor = [d for d in assessed if d["pheno"] == "dormant"]
    cf = {}
    for d in assessed:
        if d.get("window_days", 0) < 30 or d.get("window_bad", 0):
            continue
        # 判级只有一处实现：backtest.rain_track（材料闸门也调它）——此前这里手抄了五个阈值字面量（评审 A-R10㉒）
        lv = backtest.rain_track(d["dry_streak"], d["rain30"])
        if lv:
            cf[d["date"]] = lv
    in_win = [d for d in dor if d.get("window_days", 0) >= 30 and not d.get("window_bad", 0)]
    n_win = len(in_win)
    hit_win = sum(1 for d in in_win if d["date"] in cf)
    cnt = Counter(cf[d["date"]] for d in in_win if d["date"] in cf)
    any_lv = sum(1 for d in dor if d["date"] in cf)
    print(f"\n=== 2) 休眠期反事实（现行 AND 规则；n={len(dor)} 天，回看窗口完整 {n_win} 天）===")
    print(f"  照判·窗口完整口径（对外引用这个数）：任一级 {hit_win}/{n_win} = {100 * hit_win / n_win:.1f}%"
          f"（轻 {100 * cnt['轻'] / n_win:.1f}%、中 {100 * cnt['中'] / n_win:.1f}%、"
          f"重 {100 * cnt['重'] / n_win:.1f}%）")
    print(f"DORMANT_AND_PCT = {100 * hit_win / n_win:.1f}")
    print(f"  照判·全休眠期口径（含回看不足日）：{any_lv}/{len(dor)} = {100 * any_lv / len(dor):.1f}%"
          " ← 旧稿的 89.8% 是这个口径 **且**用了已退役的 OR 规则（连旱≥8 或 雨30≤45 即判轻）；"
          "用退役规则给现行决策做定量辩护属口径错误，此处一并更正")
    print("  → 现行为：降水口径在休眠期沉默；蒸散口径仍出级（阈值取该期自身分位，样本内常驻率≈25%/10%/1%）。")

    # —— 3) def30 定标 + 留出 ——
    th, dropped, _ = derive_thresholds(assessed, et30, CALIB_YEARS)      # 定标段：代码内使用的即此表
    th_full, _, _ = derive_thresholds(assessed, et30)                    # 全段定标，仅作敏感性对照
    th_calib = th
    print("\n=== 3) def30 蒸散亏缺口径（定标段 2016–2020 的 p75/p90/p99；重级需降水佐证）===")
    print(f"  ET0 窗口不完整而退出定标/评估的天数：{dropped}（本 fixture 四变量均无缺测；29 天为序列起始的回看不足）")
    print(f"  {'物候期':10}{'2016-20定标阈值':>20}{'留出段占比 轻/中/重':>22}{'留出段重级天/年':>18}")
    for pid in PHENO_IDS:
        grp = [d for d in assessed if d["pheno"] == pid
               and date.fromisoformat(d["date"]).year in EVAL_YEARS and et30.get(d["date"]) is not None]
        if not grp or pid not in th_calib:
            continue
        lv = [def_level(et30[d["date"]] - d["rain30"], th_calib[pid]) for d in grp]
        n = len(lv)
        share = {x: 100 * sum(1 for v in lv if v == x) / n for x in ("轻", "中", "重")}
        per_year = [sum(1 for d, v in zip(grp, lv) if v == "重"
                        and date.fromisoformat(d["date"]).year == y) for y in EVAL_YEARS]
        t = th_calib[pid]
        th_txt = "{}/{}/{}".format(t["轻"], t["中"], t["重"])
        sh_txt = "{:.1f}%/{:.1f}%/{:.1f}%".format(share["轻"], share["中"], share["重"])
        hv_txt = "中位 {:.0f}（{}–{}）".format(st.median(per_year), min(per_year), max(per_year))
        print("  {:10}{:>20}{:>22}{:>18}".format(PHENO_LABEL[pid], th_txt, sh_txt, hv_txt))

    print("\n  3a) 恒等式提醒（评审 A-Q6）：按定义，定标样本自身的占比必然≈25%/10%/1%——"
          "那是分位数的算术，不是农学证据。上表给的是**留出段 2021–2025 用 2016–2020 阈值**判出的实际占比，"
          "偏离恒等式的幅度才是信息：偏离越大，说明该口径跨期越不稳，也就越不能凭分位宣称绝对缺水程度。")

    print("\n  3b) 降水口径子条件可达性（十年命中次数，评审 A-Q6a）")
    hits = rain_subhits(assessed)
    for pid in PHENO_IDS:
        if pid in hits:
            print(f"    {PHENO_LABEL[pid]:10} " + " ".join(f"{k} {v}" for k, v in sorted(hits[pid].items())))
    print("    → 膨大期『重』级需 连旱≥25 或 雨30≤15：若命中次数远小于其他条件，"
          "则现行'重级稀有'部分是**结构不可达**而非信号稀少，材料不得据 0 天/年宣称区分力。")

    print("\n  3c) def30 的主导变量（30 天累计的中位数，mm）")
    for pid in PHENO_IDS:
        grp = [d for d in assessed if d["pheno"] == pid and et30.get(d["date"]) is not None]
        if not grp:
            continue
        e = st.median([et30[d["date"]] for d in grp])
        p = st.median([d["rain30"] for d in grp])
        print(f"    {PHENO_LABEL[pid]:10} ET0_30 中位 {e:6.1f}｜降水_30 中位 {p:6.1f}｜def30 中位 {e - p:6.1f}")
    print("    → 各物候期 def30 的差异主要由**降水**项驱动（夏季 ET0 远高于冬季，def30 反而更低），"
          "故 def30 是\"降水亏缺扣掉蒸散需求\"的合成排名量，不等同作物缺水强度；"
          "其增量信息集中在高 ET0 少雨过程（如 2022-08），这正是它相对纯降水口径的实际增益。")

    print("\n  3d) 定标段（2016–2020）阈值 = 代码内 DEF_THRESH（单一事实源）")
    for pid in PHENO_IDS:
        if pid in th:
            print("    {:10} 轻 {:6.1f} / 中 {:6.1f} / 重 {:6.1f}".format(
                PHENO_LABEL[pid], th[pid]["轻"], th[pid]["中"], th[pid]["重"]))
    print("    与 phenology.js / backtest.py 的 DEF_THRESH 一致性由 parity_check.py 第 3 层断言：")
    bad = []
    for pid in PHENO_IDS:
        if pid in th and pid in backtest.DEF_THRESH:
            for lv in ("轻", "中", "重"):
                if abs(th[pid][lv] - backtest.DEF_THRESH[pid][lv]) > 0.05:
                    bad.append(f"{pid}.{lv}：现算 {th[pid][lv]} ≠ 代码 {backtest.DEF_THRESH[pid][lv]}")
    if bad:
        print("    [X] 阈值与代码脱钩：\n      " + "\n      ".join(bad))
        return 1
    print("    [OK] 现算分位与 phenology.js / backtest.py 的 DEF_THRESH 一致（±0.05）")
    print("    对照（全段 2016–2025 定标，若改用它会与留出评估互相污染）："
          + " ".join(f"{PHENO_LABEL[pid]} {th_full[pid]['轻']}/{th_full[pid]['中']}/{th_full[pid]['重']}"
                     for pid in PHENO_IDS if pid in th_full))

    print("\n=== 4) 2022-08-24~28 实况（蒸散口径的增量证据）===")
    for d in assessed:
        if "2022-08-24" <= d["date"] <= "2022-08-28" and d["pheno"] == "swell":
            e = et30.get(d["date"])
            def30 = None if e is None else e - d["rain30"]
            dv = def_level(def30, th["swell"]) if def30 is not None and "swell" in th else None
            print("    {} 连旱{:2}天 雨30={:5.1f} def30={:6.1f}｜现行等级 {}（蒸散分位原始判 {}，佐证规则可能降格）".format(
                d["date"], d["dry_streak"], d["rain30"], def30, d["drought_level"] or "无", dv or "无"))
    print("    → 同一周（2022-08-27 中国气象局在丹江口汇水流域飞机增雨，公开报道），"
          "纯降水口径最高只判到『中』；蒸散分位把 08-24~27 抬到『重』级线以上——"
          "现行规则要求降水口径同时到『中』才放行『重』，可逐日复算实际出的是哪一级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
