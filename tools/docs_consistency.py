#!/usr/bin/env python3
"""材料一致性闸门：关键数字与口径不再靠人记

背景（评审 A-Q8 的工单，也是我上一轮被当面抓到的问题）：一次规则改动要同步 README、docs/01–03
与 PPT 生成脚本五处载体，靠人记必然漏。本脚本把"从冻结序列现算出来的值"与"材料里写的字符串"
逐条对齐，并禁止一批已被否决的旧措辞回潮；任一不符即非零退出，提交前必须跑绿。

三条硬要求（评审 A-Q8 指出的原实现缺陷）：
1. **不留"算了不用"的值**：负担中位数三档（轻/中/重）全部进必现表，不再只核"重"一档；
2. **不写死字面量**：对拍天数、用例组数、变异数、定标年份、负担数一律现算后拼进必现串；
3. **回归断言全核**：HEADLINE 每一条都与现算值比对，不是抽查 5 条。

干旱负担的可引用口径是**留出段 2021–2025**（蒸散阈值只用 2016–2020 定标，定标段内的分位命中
是算术恒等式）；材料若引用十年混算数，就是拿考卷当答案。

用法：python tools/docs_consistency.py
"""
import json
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import backtest            # noqa: E402
import baseline_stats as bs  # noqa: E402
import mutation_test       # noqa: E402
import parity_check        # noqa: E402
import threshold_sensitivity as ts  # noqa: E402

FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
FILES = {
    "README": ROOT / "README.md",
    "docs/01": ROOT / "docs" / "01-项目说明.md",
    "docs/02": ROOT / "docs" / "02-数据与AI说明.md",
    "docs/03": ROOT / "docs" / "03-验证计划.md",
    "PPT脚本": HERE / "make_ppt.py",
    # 报名字段与作品帖的粘贴稿虽在 pre/（gitignore），但它是要粘进官网、评委第一眼读的那段话，
    # 数字漂移最贵就是漂在这里，所以也纳入同一道闸门（评审 B-Q10）
    "报名粘贴稿": ROOT / "pre" / "同步稿-报名字段与作品帖.md",
    # 周历与工时台账是退出触发④的计量件，里面的日期与金额同样是口径载体（评审 B-R8⑤）
    "docs/04": ROOT / "docs" / "04-试点周历与台账.md",
}
GATES = HERE / "GATES.json"
# 报名字段粘贴稿是给官网复制粘贴用的本地文本，被 .gitignore 排除，交付仓库里本就没有它。
# 干净克隆必须能跑出全绿——否则评委第一步复算就"红"，而原因只是少了一份本地文件（评审 A-R11②）。
# 它在场时照常全核，缺席时如实打印"本地不入库载体缺席"，不计为材料漂移。
OPTIONAL_CARRIERS = {"报名粘贴稿"}
PPTX = ROOT / "丹橘知旱-参赛PPT.pptx"
# 引擎源码与线上产物也是"口径载体"：注释里留着旧说法，代码和材料就会各说一套（评审 A-Q6）
SOURCES = {
    "phenology.js": ROOT / "phenology.js",
    "app.js": ROOT / "app.js",
    "backtest.py": HERE / "backtest.py",
    "baseline_stats.py": HERE / "baseline_stats.py",
}
# 全绿记录之后还允许改动的文件：只有文档措辞；引擎/闸门/输入一旦变更，GATES.json 即视为过期
ENGINE_FILES_PREFIX = ("phenology.js", "app.js", "index.html", "style.css",
                       "tools/parity_check.py", "tools/backtest.py",
                       "tools/backtest.py", "tools/parity", "tools/baseline_stats.py",
                       "tools/mutation_test.py", "tools/docs_consistency.py", "tools/check_all.py",
                       "tools/smoke_app.mjs", "tools/fixtures/", "fixtures/")
# 注意：make_ppt.py / grid_sensitivity.py / online_drift.py 属于"材料或测量脚本"，改了不使全绿记录过期，
# 但它们的文本仍受必现/禁现条目核对（PPT 成品与线上实测数字另有绑定）


def pptx_texts() -> list[tuple[str, str]]:
    """把 .pptx 里的幻灯片文本抽出来当载体扫（评审 A-Q6：只扫生成脚本等于没扫交付物）。"""
    import zipfile
    import re as _re
    if not PPTX.exists():
        return []
    out = []
    with zipfile.ZipFile(PPTX) as z:
        for name in sorted(n for n in z.namelist() if n.startswith("ppt/slides/slide")):
            xml = z.read(name).decode("utf-8", "replace")
            txt = "".join(_re.findall(r"<a:t>(.*?)</a:t>", xml, _re.S))
            out.append((name, txt))
    return out


def gates_check(c: dict) -> list[str]:
    """校验 GATES.json 的"同一棵树全绿"记录是否仍然对得上当前提交。

    严格模式（环境变量 GATES_STRICT=1，由 check_all.py --verify 与 pre-commit 设置）要求记录**当下就与代码同树**；
    非严格模式（正式记录那一跑）允许记录落后于代码，因为这一跑正在把当前树重新验绿并覆盖记录——
    否则改完代码就再也生成不了新记录，机制会把自己锁死（评审 A-Q1 抓到的死锁）。
    """
    import subprocess
    strict = os.environ.get("GATES_STRICT") == "1"
    if not GATES.exists():
        return ["缺 tools/GATES.json：没跑过 check_all.py，材料不得宣称全绿"], 0
    g = json.loads(GATES.read_text(encoding="utf-8"))
    out = []
    n_chk = 0
    if not g.get("all_green"):
        out.append("GATES.json 记录的不是全绿运行")
    n_chk += 1
    if g.get("working_tree_dirty"):
        n_chk += 1
        out.append("GATES.json 是在工作树有未提交改动时写的，记录对不上任何一次提交" if strict else
                   "[提示] 记录写自未提交的工作树（正式记录那一跑的常态，写完即刷新）")
    sha = (g.get("fixture") or {}).get("pulled_at_utc")
    fx_meta = json.loads(FIXTURE.read_text(encoding="utf-8")).get("meta", {})
    if sha != fx_meta.get("pulled_at_utc"):
        out.append(f"GATES.json 记录的输入拉取时间 {sha} ≠ 当前 fixture {fx_meta.get('pulled_at_utc')}")

    def git(*a):
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "").strip()

    n_chk += 4
    rc, head = git("rev-parse", "--short", "HEAD")
    if rc:                      # 变异沙箱/下载的 ZIP 没有 .git：只能跳过，绝不能打印"对得上"（评审 A-Q4）
        out.append("[未核] 取不到 git 记录（沙箱或 ZIP 压缩包）——GATES 记录核对跳过，请用 git clone 后运行")
        return out, 2
    rc, _ = git("merge-base", "--is-ancestor", g["commit"], head)
    if rc and strict:
        out.append(f"GATES.json 记录的 commit {g['commit']} 不是当前 HEAD {head} 的祖先——记录来自另一条线")
    rc, changed = git("diff", "--name-only", f"{g['commit']}..{head}")
    if rc:
        out.append(f"无法比对 {g['commit']}..HEAD 的差异")
    elif strict:
        stale = [f for f in changed.splitlines()
                 if f and f != "tools/GATES.json" and f != "tools/check_all.py"
                 and f.startswith(ENGINE_FILES_PREFIX)]
        if stale:
            out.append(f"全绿记录之后引擎/闸门/输入又被改动（GATES.json 已过期，须重跑 check_all）："
                       + "、".join(stale[:6]))
    return out, n_chk
# 匹配前统一剔除引号与 Markdown 强调符，避免 "" 『』 ** 等写法差异造成假失败
QUOTES = "“”\"『』’'“*"


def norm(s: str) -> str:
    return "".join(ch for ch in s if ch not in QUOTES)


def dormant_and_pct(all_days) -> str:
    """休眠期"若降水口径照判"的常驻率：现行 AND 规则 + 回看窗口完整的分母（与 baseline_stats 同）。"""
    win = [d for d in all_days if d["pheno"] == "dormant"
           and d.get("window_days", 0) >= 30 and not d.get("window_bad", 0)]
    hit = sum(1 for d in win if backtest.rain_track(d["dry_streak"], d["rain30"]) is not None)
    return (f"{100 * hit / len(win):.1f}", len(win))


PHENO_ROW = {"dormant": "休眠", "bud": "萌芽/现蕾", "bloom": "开花",
             "young": "幼果", "swell": "膨大", "ripe": "成熟采收"}


def def_threshold_rows(all_days, et30):
    """把 docs/02 的定标表逐格变成现算值（含样本 n），改数不改表即失败（评审 A-Q4/A-Q6）。"""
    th, _dropped, vals = bs.derive_thresholds(all_days, et30, list(bs.CALIB_YEARS))
    rows = {}
    for pid, label in PHENO_ROW.items():
        if pid in th:
            rows[label] = (f"| {label} | {th[pid]['轻']} | {th[pid]['中']} | {th[pid]['重']} "
                           f"| {len(vals[pid])} |")
    return rows


def median_triplet(all_days, seg):
    """干旱各级日数/年：中位数 + 最小–最大（与 baseline_stats 同呈现口径）。"""
    out = {}
    for lv in ("重", "中", "轻"):
        per = sorted(sum(1 for d in all_days if d["drought_level"] == lv
                         and date.fromisoformat(d["date"]).year == y) for y in seg)
        out[lv] = (int(float(f"{statistics.median(per):.0f}")), per[0], per[-1])
    return out


def holdout_rate_ranges(all_days, et30):
    """留出段（2021–2025，用 2016–2020 定标阈值）各物候期蒸散口径命中占比的区间。
    材料里"轻/中/重占比 8–22%/0.3–14%/0.3–6%"这类范围句必须能被现算复现（评审 A-Q7），
    区间按"除开花期外的五个物候期"统计，开花期作为离群值单独给值。"""
    th, _dropped, _vals = bs.derive_thresholds(all_days, et30, list(bs.CALIB_YEARS))
    shares = {}
    for pid in bs.PHENO_IDS:
        grp = [d for d in all_days if d["pheno"] == pid
               and date.fromisoformat(d["date"]).year in bs.EVAL_YEARS
               and et30.get(d["date"]) is not None and d.get("rain30") is not None]
        if not grp or pid not in th:
            continue
        lv = [bs.def_level(et30[d["date"]] - d["rain30"], th[pid]) for d in grp]
        shares[pid] = {x: 100 * sum(1 for v in lv if v == x) / len(lv) for x in ("轻", "中", "重")}
    five = [v for k, v in shares.items() if k != "bloom"]
    rng = {x: f"{min(o[x] for o in five):.1f}–{max(o[x] for o in five):.1f}%" for x in ("轻", "中", "重")}
    return rng, f"{shares.get('bloom', {}).get('重', 0.0):.1f}"


def parity_day_count(times):
    """对拍声明的真实序列天数：与 parity_check 的窗口表逐一对齐（含 warm-up 后仍只计报告区间）。"""
    total = 0
    for s, e, _ in parity_check.WINDOWS:
        total += sum(1 for t in times if s <= t <= e)
    return total


def canonical() -> dict:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    daily = fx["daily"]
    times = daily["time"]
    s0, e0 = "2022-06-01", "2022-09-30"
    w0 = (date.fromisoformat(s0) - timedelta(days=backtest.WARMUP_DAYS)).isoformat()
    keep = [i for i, t in enumerate(times) if w0 <= t <= e0]
    sl = {k: [v[i] for i in keep] for k, v in daily.items()}
    assessed = backtest.windowed(backtest.assess(backtest.filter_days(sl)), s0, e0)
    hd = backtest.summarize(assessed)

    all_days = backtest.assess([dict(d) for d in backtest.filter_days(daily)])
    sens_daily = {k: [v[i] for i, t in enumerate(times) if ts.W0 <= t <= ts.E0]
                  for k, v in daily.items()}
    s_m2 = ts.run(sens_daily, t_off=-2.0)
    s_p05 = ts.run(sens_daily, p_off=0.5)
    hold = median_triplet(all_days, bs.EVAL_YEARS)
    calib_lo, calib_hi = min(bs.CALIB_YEARS), max(bs.CALIB_YEARS)
    eval_lo, eval_hi = min(bs.EVAL_YEARS), max(bs.EVAL_YEARS)
    et30 = bs.et0_30(daily)
    import subprocess
    try:
        ac = json.loads(subprocess.run(["node", "tools/advice_counts.mjs"], cwd=ROOT,
                                       capture_output=True, timeout=60).stdout.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        ac = {"confirm": -1, "total": -1}
        print(f"[warn] 建议库条目数现算失败（{exc}）")
    rows = def_threshold_rows(all_days, et30)
    hr, bloom_sev = holdout_rate_ranges(all_days, et30)
    c = {
        "row_dormant": rows["休眠"], "row_bud": rows["萌芽/现蕾"], "row_bloom": rows["开花"],
        "row_young": rows["幼果"], "row_swell": rows["膨大"], "row_ripe": rows["成熟采收"],
        "h_sev": hd["heat_severe"], "lead": hd["lead_days"],
        "d_sev": hd["drought_severe"], "d_mid": hd["drought_mid"], "d_first": hd["drought_first"],
        "b_sev": hold["重"][0], "b_sev_rng": f"{hold['重'][1]}–{hold['重'][2]}",
        "b_mid": hold["中"][0], "b_mid_rng": f"{hold['中'][1]}–{hold['中'][2]}",
        "b_light": hold["轻"][0], "b_light_rng": f"{hold['轻'][1]}–{hold['轻'][2]}",
        "n_cases": len(parity_check.cases()), "n_mut": len(mutation_test.MUTS),
        "n_sweep": sum(len(v[1]) for v in parity_check.SWEEPS.values()) * parity_check.SWEEP_DAYS,
        "n_sweep_axes": len(parity_check.SWEEPS),
        "n_sweep_tiers": sum(len(v[1]) for v in parity_check.SWEEPS.values()),
        "sweep_days": parity_check.SWEEP_DAYS, "sweep_floor": parity_check.MIN_SWEEP_DAYS,
        "n_fp": len(mutation_test.FP_KEYS),
        "n_days": parity_day_count(times),
        "calib": f"{calib_lo}–{calib_hi}", "holdout": f"{eval_lo}–{eval_hi}",
        "def_cap": backtest.DEF_SOLO_CAP,
        "n_confirm": ac["confirm"], "n_advice": ac["total"],
        "sev_m2": s_m2["heat_severe"], "lead_m2": s_m2["lead_days"],
        "sev_p05": s_p05["drought_severe"], "mid_p05": s_p05["drought_mid"],
        "first_p05": s_p05["drought_first"][5:],
        "dorm_pct": dormant_and_pct(all_days)[0], "dorm_n": dormant_and_pct(all_days)[1],
        "hr_light": hr["轻"], "hr_mid": hr["中"], "hr_sev": hr["重"], "bloom_sev": bloom_sev,
        "clock_branches": (ROOT / "app.js").read_text(encoding="utf-8").count("冻害时效钟："),
    }
    # 格点敏感性与回填段偏差：以冻结件为准（脚本 --save 生成、--verify 与本闸门共用同一份数据）。
    # 缺冻结件（如全新克隆还没跑过 --save）时跳过对应必现项，而不是编一个假的权威值。
    gf = ROOT / "tools" / "fixtures" / "grid_sens_2022.json"
    cf = ROOT / "tools" / "fixtures" / "chain_overlap.json"
    if gf.exists():
        gj = json.loads(gf.read_text(encoding="utf-8"))["summary"]
        c["g_dis"], c["g_n"] = gj["worst_disagree"], gj["n_days"]
        c["g_pct"] = f"{gj['worst_disagree'] / gj['n_days'] * 100:.1f}"
        c["g_heat"], c["g_drought"] = gj["per_risk"]["heat"], gj["per_risk"]["drought"]
        c["g_leads"] = "、".join(str(x) for x in gj["leads"])
        c["g_spread"] = max(gj["leads"]) - min(gj["leads"])
        c["g_hmin"], c["g_hmax"] = gj["heavy_min_max"]
        c["g_hspread"] = c["g_hmax"] - c["g_hmin"]
    if cf.exists():
        cj = json.loads(cf.read_text(encoding="utf-8"))
        c["c_tmax"] = cj["summary"]["max_abs"]["tmax"]
        c["c_lv"] = sum(cj["summary"]["level_changes"].values())
        c["c_n"] = cj["meta"]["n_overlap"]
    return c


# (文件, 必须出现的规范化子串, 说明)——串里的 {占位} 全部来自 canonical() 现算值
MUST = [
    ("README", "重{d_sev} 天／中{d_mid} 天", "README 干旱双口径数字"),
    ("README", "提前 {lead} 天", "README 热害时效"),
    ("README", "{n_days} 天", "README 对拍天数"),
    ("README", "{n_cases} 组阈值边界合成用例", "README 边界用例数"),
    ("README", "python tools/check_all.py", "README 一键闸门命令"),
    ("README", "tools/GATES.json", "README 全绿记录文件"),
    ("docs/02", "GATES.json", "docs/02 提交前机制"),
    ("docs/02", "ruleStamp()", "docs/02 页面口径串"),
    ("docs/01", "p75/p90/p99", "docs/01 蒸散口径定标说明"),
    ("docs/02", "重{d_sev} 天、中{d_mid} 天", "docs/02 回测干旱数字"),
    ("docs/02", "首个提示 {d_first}", "docs/02 干旱首日"),
    ("docs/02", "| 干旱（留出段 {holdout}，双口径取高者） | {b_sev}（{b_sev_rng}） "
                "| {b_mid}（{b_mid_rng}） | {b_light}（{b_light_rng}） | — |",
     "docs/02 干旱负担三档中位（留出段，逐档核对）"),
    ("docs/02", "{dorm_pct}%（现行 AND 规则、回看窗口完整的 {dorm_n} 天口径", "docs/02 休眠期常驻率"),
    ("docs/02", "轻 {hr_light}／中 {hr_mid}／重 {hr_sev}", "docs/02 留出段分位命中占比区间（现算）"),
    ("docs/02", "\"重\"级占比 {bloom_sev}%（单年最高 30 天）", "docs/02 开花期留出失准值（现算）"),
    ("docs/02", "{n_cases} 组边界合成用例", "docs/02 用例数"),
    ("docs/02", "{n_sweep} 个逐日比对点位", "docs/02 单调性扫描点位数"),
    ("docs/02", "{n_sweep_axes} 条轴、每档 {sweep_days} 天", "docs/02 扫描轴数与档长"),
    ("docs/02", "点位低于 {sweep_floor} 即判失败", "docs/02 扫描点位断言下限"),
    ("README", "{n_sweep} 个逐日比对点位", "README 单调性扫描点位数"),
    ("docs/02", "分支共 {clock_branches} 条", "docs/02 冻害时效钟分支数（现算）"),
    ("docs/02", "强度指纹 {n_fp} 项", "docs/02 闸门强度指纹项数"),
    ("docs/02", "另有四类变异是往闸门自己身上打的", "docs/02 闸门自削变异类别"),
    ("docs/01", "{n_mut} 个注入变异", "docs/01 变异自证计数"),
    ("docs/02", "{n_mut}/{n_mut} 变异自证", "docs/02 能力矩阵变异计数"),
    ("PPT脚本", "{n_mut} 个注入变异", "PPT 变异计数"),
    ("报名粘贴稿", "{n_mut} 个注入变异", "报名字段稿变异计数"),
    ("报名粘贴稿", "重{d_sev} 天／中{d_mid} 天", "报名字段稿干旱数字"),
    ("报名粘贴稿", "{n_cases} 组阈值边界合成用例", "报名字段稿用例数"),
    ("docs/03", "{n_sweep} 个逐日比对点位", "docs/03 单调性扫描点位数"),
    ("docs/03", "执行 {n_mut}/{n_mut}、拦下 {n_mut}/{n_mut}", "docs/03 变异自证结论"),
    ("docs/02", "执行 {n_mut}/{n_mut}、拦下 {n_mut}/{n_mut}", "docs/02 变异自证结论"),
    ("docs/02", "{calib} 定标", "docs/02 蒸散阈值定标段"),
    ("docs/02", "{n_confirm}/{n_advice} 条", "docs/02 建议库外部性条目数"),
    ("docs/02", "偏冷 2.0℃ → 重级 {sev_m2} 天、提前 {lead_m2} 天", "docs/02 热害偏移敏感性"),
    ("docs/02", "上移 0.5mm（再分析一次修订就是这个量级）→ \"重\"级 {sev_p05} 天、\"中\"级 {mid_p05} 天、首个提示推迟到 {first_p05}",
     "docs/02 干旱偏移敏感性"),
    ("docs/02", "封顶{def_cap}", "docs/02 蒸散口径单独出级上限"),
    ("docs/02", "{row_dormant}", "docs/02 定标表·休眠行"),
    ("docs/02", "{row_bud}", "docs/02 定标表·萌芽现蕾行"),
    ("docs/02", "{row_bloom}", "docs/02 定标表·开花行"),
    ("docs/02", "{row_young}", "docs/02 定标表·幼果行"),
    ("docs/02", "{row_swell}", "docs/02 定标表·膨大行"),
    ("docs/02", "{row_ripe}", "docs/02 定标表·成熟采收行"),
    ("docs/02", 'method="linear"', "docs/02 分位估计量声明"),
    ("docs/03", "重{d_sev} 天／中{d_mid} 天", "docs/03 已完成验证的干旱数字"),
    ("docs/03", "{n_days} 天逐日比对", "docs/03 对拍天数"),
    ("docs/03", "{n_cases} 组阈值边界合成用例", "docs/03 用例数"),
    ("docs/03", "轻 {b_light}／中 {b_mid}／重 {b_sev}（{holdout} 留出段）",
     "docs/03 提示负担三个口径不得混用的基线数"),
    ("PPT脚本", "重{d_sev} 天／中{d_mid} 天", "PPT 干旱数字"),
    ("PPT脚本", "{n_cases} 组边界", "PPT 用例数"),
    # 工时与现金封顶这类"计划数"没有脚本能现算，但至少不许各写一份（评审 B-Q3/B-Q10）
    ("docs/03", "85–95 小时", "docs/03 工时封顶"),
    ("README", "85–95 小时", "README 工时封顶"),
    ("报名粘贴稿", "85–95 小时", "报名字段稿工时口径"),
    ("docs/03", "3400 元", "docs/03 现金封顶"),
    ("README", "3400 元", "README 现金封顶"),
    ("报名粘贴稿", "3400 元", "报名字段稿现金封顶"),
    ("docs/03", "2200 元", "docs/03 补偿无法合规列支时的降档额"),
    ("README", "2200 元", "README 降档额"),
    ("报名粘贴稿", "2200 元", "报名字段稿降档额"),
    ("docs/04", "3400 元", "docs/04 现金封顶"),
    ("docs/04", "2200 元", "docs/04 降档额"),
    ("docs/04", "2026-11-02", "docs/04 周历第 1 周起点"),
    ("README", "{n_mut} 个注入变异", "README 变异自证计数"),
]

# 这两组必现项只在冻结件在场时启用：它们把 docs/02 引用的"跑出来的数"绑到 tools/fixtures 的快照上，
# 快照由脚本 --save 生成、可 --verify 离线复算——材料不再引用"作者跑一次、读者只能信"的数字（评审 R13②）
if (ROOT / "tools" / "fixtures" / "grid_sens_2022.json").exists():
    MUST += [
        ("docs/02", "{g_dis}/{g_n}＝{g_pct}%", "docs/02 格点 worst 不一致率（冻结件现算）"),
        ("docs/02", "热害 {g_heat}/{g_n}、干旱 {g_drought}/{g_n}", "docs/02 分风险不一致（冻结件现算）"),
        ("docs/02", "{g_leads}（极差 {g_spread} 天）", "docs/02 提前量序列与极差（冻结件现算）"),
        ("docs/02", "{g_hspread} 天（{g_hmin}–{g_hmax}）", "docs/02 重级日数极差（冻结件现算）"),
    ]
if (ROOT / "tools" / "fixtures" / "chain_overlap.json").exists():
    MUST += [
        ("docs/02", "最大绝对偏差 {c_tmax}℃", "docs/02 回填段温度偏差（冻结件现算）"),
        ("docs/02", "替换读数后 {c_lv}/{c_n} 天判级变化", "docs/02 回填段判级变化（冻结件现算）"),
    ]

# 已被否决、不得回潮的措辞（任何材料里出现即失败）
BANNED = [
    "中」3 天", "重」0 天", "中3 天", "重0 天", "23 组", "33 组", "35 组", "10/10", "第 7 节",
    "首发版不上", "不在首发版", "换物候表即可支持", "数百元级/园块", "投保/理赔辅助凭证",
    "一律不出干旱等级", "休眠期不出干旱等级", "8（1–17）", "50（28–69）",
    "17（5–43）", "36（27–72）", "68（55–109）", "轻 68", "约 50 天/年",
    "重 8 天", "中 54 天", "轻 43／中 44／重 10", "轻 69", "中 43", "重 10",
    "全段 2016–2025 的 p75", "十年分位", "102.3", "122.6", "87.4", "3200 元", "三道闸门", "51.6%", "7–27",
    "DEF_MAX_LEVEL", "开花期蒸散口径封顶", "123–138mm 判", "442/33/15", "2016–2020 定标的分位用于 2021–2025",
    "同一套规则有 Python 独立实现交叉验证", "10 个变异", "双闸门",
    # 计数换版后旧值不得残留在任何载体（本轮：变异 24→27、扫描点位 837→1302、工时口径回改）
    "24/24", "24 个注入变异", "24 个变异", "837 个",
    # 计数换版后旧值不得残留在任何载体（本轮 24→27→30→33→34→38）
    "27/27", "30/30", "33/33", "34/34", "38/38",
    *[f"{k} 个注入变异" for k in (24, 27, 30, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43)],
    "40/40", "41/41", "42/42", "43/43", "18/26 户·周", "9/13 户·周",
    *[f"{k} 个变异" for k in (24, 27, 30, 33, 34, 37, 38, 39, 40)],
    "只看土壤水分亏缺", "若树上仍挂果", "32%—45%",
    # 本轮被代码/事实推翻的旧串（回潮即失败）
    "当前预报 10 天内无再次触发日", "烟测对四种分支逐一断言", "每提示日并发动作 4 条",
    "0.5–6 小时", "const irrBlocked", "比对点位低于 800",
    "§9", "§10",  # docs/03 只有 §1–§8：悬空章节号不得出现在任何载体

]


def main() -> int:
    c = canonical()
    texts = {}
    missing = []
    absent = set()
    for k, p in FILES.items():
        if not p.exists():
            if k in OPTIONAL_CARRIERS:      # 本地不入库的载体在干净克隆里缺席，属正常
                absent.add(k)
                texts[k] = ""
                continue
            missing.append(f"[缺载体] {k}：{(p.relative_to(ROOT))} 不存在，该载体的数字无人核对")
            texts[k] = ""
            continue
        texts[k] = norm(p.read_text(encoding="utf-8"))
    print("现算权威值：干旱 重{d_sev}/中{d_mid}（首日 {d_first}）、热害 重{h_sev} 提前{lead} 天、"
          "干旱负担中位（{holdout} 留出段）重{b_sev}（{b_sev_rng}）/中{b_mid}/轻{b_light}、"
          "边界用例 {n_cases} 组、变异 {n_mut} 个、对拍 {n_days} 天、定标段 {calib}".format(**c))
    problems = []

    for fname, pat, desc in MUST:
        if fname in absent:      # 缺席的本地不入库载体不参与必现核对（在场时一条不减）
            continue
        needle = norm(pat).format(**c)
        if needle not in texts[fname]:
            problems.append(f"[缺] {fname}：{desc} → 材料中找不到「{needle}」")
    for fname, text in texts.items():
        for bad in BANNED:
            b = norm(bad)
            if b and b in text:
                problems.append(f"[禁] {fname}：出现已否决措辞「{bad}」")
    # 引擎注释也扫（只扫禁现措辞：注释里的旧口径同样会误导读代码的评审）
    for fname, p in SOURCES.items():
        if not p.exists():
            problems.append(f"[缺载体] 源码 {fname} 不存在，注释口径无人核对")
            continue
        stext = norm(p.read_text(encoding="utf-8"))
        for bad in BANNED:
            b = norm(bad)
            if b and b in stext:
                problems.append(f"[禁·源码] {fname}：注释里出现已否决措辞「{bad}」")
    # 三条 PPT 成品针一律由现算值拼出：写死字面量会让"改用例数就必须先改闸门"变成锁死（评审 A-R8⑩）
    for label, needle in (("PPT成品", f'{c["n_cases"]} 组边界'), ("PPT成品", f'{c["n_mut"]} 个注入变异'),
                          ("PPT成品", f'重{c["d_sev"]} 天／中{c["d_mid"]} 天')):
        joined = norm("".join(x[1] for x in pptx_texts()))
        if not joined:
            problems.append("[缺载体] PPT成品：丹橘知旱-参赛PPT.pptx 不存在或无文本，交付物无人核对")
            break
        if needle not in joined:
            problems.append(f"[缺] PPT成品：幻灯片文本里找不到「{needle}」——脚本改了但没重新生成 pptx？")
    for extra in pptx_texts():
        for bad in BANNED:
            if norm(bad) and norm(bad) in norm(extra[1]):
                problems.append(f"[禁·PPT成品] {extra[0]}：出现已否决措辞「{bad}」")

    n_hl = 0
    for k, want in (("heat_severe", c["h_sev"]), ("drought_severe", c["d_sev"]),
                    ("drought_mid", c["d_mid"]), ("drought_first", c["d_first"]),
                    ("lead_days", c["lead"])):
        if backtest.HEADLINE[k] != want:
            problems.append(f"[断言漂移] backtest.HEADLINE.{k}={backtest.HEADLINE[k]} ≠ 现算 {want}")
    for k, v in backtest.HEADLINE.items():
        n_hl += 1
        if v is None:
            problems.append(f"[断言空转] backtest.HEADLINE.{k} 为 None，等于没断言")

    gate_problems, n_gate_chk = gates_check(c)
    # "[未核]"＝取不到 git 记录。变异沙箱（MUTATION_SANDBOX=1）是临时副本、本来就没有 .git，
    # 那一跑的 docs 闸门不是来核 git 记录的，按跳过处理；非严格模式同样只作提示。
    # 严格模式在主树里缺 .git 就是真问题——不许把豁免写进主树（评审 A-Q4 的反例）。
    _strict = os.environ.get("GATES_STRICT") == "1"
    _skip_git = os.environ.get("MUTATION_SANDBOX") == "1"
    problems.extend(p for p in gate_problems if not p.startswith("[提示]")
                    and not (p.startswith("[未核]") and (_skip_git or not _strict)))
    notes = [p for p in gate_problems if p.startswith("[提示]") or p.startswith("[未核]")]

    n_skip = sum(1 for m in MUST if m[0] in absent)
    print(f"\n检查项：必现 {len(MUST)} 条（实核 {len(MUST) - n_skip} 条，缺席载体跳过 {n_skip} 条）｜禁现 {len(BANNED)} 条｜回归断言逐条核对 {n_hl} 条｜"
          f"载体 {len(FILES)} 份（在场 {len(FILES) - len(absent)} 份）｜全绿记录核对 {n_gate_chk} 项"
          + ("（严格模式：pre-commit/CI）" if os.environ.get("GATES_STRICT") == "1" else "（记录跑）"))
    for note in notes:
        print(note)
    if not gate_problems:
        print("[OK] GATES.json 与当前提交对得上（全绿、干净树、祖先关系、记录后未改引擎/闸门/输入）")
    if problems:
        print(f"发现 {len(problems)} 处不一致：")
        for p in problems:
            print("  [X] " + p)
        print("\n结论：材料数字/口径与现算值不同步 → 禁止提交")
        return 1
    print(f"结论：{len(FILES)} 份载体加闸门脚本注释的关键数字与口径均与现算值一致，且无被否决措辞回潮 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
