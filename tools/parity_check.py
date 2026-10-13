#!/usr/bin/env python3
"""JS ↔ Python 规则引擎对拍（等价性闸门 + 边界用例 + 物候表覆盖核算）

为什么需要它：仓库里有两套风险引擎实现（前端 `phenology.js`+`app.js assess()` 与回测
`tools/backtest.py`），docs/02 承诺"任一侧规则改动须同步对拍"。本脚本是那句承诺的执行机制，
跑绿才允许提交。三层检查：

1. 真实序列逐日对拍：四个覆盖不同物候期的窗口（默认用仓库内冻结 fixture，离线可跑），
   逐日比较四类风险等级与物候判定，任一天不一致即失败。
2. 边界合成用例：真实数据几乎不会落在 15/30/45mm、连旱 8/15/25 天、连续高温 3/5 天这些
   阈值边界上（评审 A-Q2：只跑实数据会掩盖分叉 bug），故另造合成序列打到每条分支，
   期望值按规则文本单独写死（不取自任何一侧实现），两侧都必须命中。
3. 物候表：两表逐条一致 + 2016–2028 每一天都必须显式落入某个区间（不允许靠兜底分支，
   评审 A-Q10 的闰日洞就是这类问题）。

用法：
  python tools/parity_check.py            # 离线（fixture）+ 合成用例 + 表检查
  python tools/parity_check.py --online   # 真实窗口改为当场联网取数
退出码 0 = 全部通过。
"""
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
LAT, LON = 32.60, 111.42
RISK_KEYS = ("heat", "drought", "cold", "freeze")
LEVEL_KEYS = ("pheno", *RISK_KEYS)

WINDOWS = [
    ("2022-06-01", "2022-09-30", "热害季（≥35℃ 升级链）"),
    ("2021-11-25", "2022-02-28", "越冬期（休眠期干旱抑制 + 冻害）"),
    ("2023-03-01", "2023-06-30", "萌芽—开花—幼果—膨大（物候边界密集）"),
    ("2025-08-21", "2025-11-30", "秋汛至采收（成熟期→休眠期切换）"),
]


MIN_REAL_DAYS = 400   # 断言下限：低于此数说明窗口被跳过或数据源缺段，闸门不得宣布"等价"（评审 A-Q3）
MIN_CASES = 28        # 断言下限：边界用例数，材料里引用的就是这个数


def run_node(*args: str, stdin_text: str = "") -> str:
    proc = subprocess.run(
        ["node", str(HERE / "parity_js.mjs"), *args],
        input=stdin_text.encode("utf-8"), capture_output=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError("JS 引擎执行失败：" + proc.stderr.decode("utf-8", "replace")[:800])
    return proc.stdout.decode("utf-8")


def js_levels(days: list[dict]) -> list[dict]:
    return json.loads(run_node(stdin_text=json.dumps(
        [{k: d.get(k) for k in ("date", "tmax", "tmin", "pr", "et")} for d in days])))


def js_tables() -> dict:
    return json.loads(run_node("--tables"))


def py_levels(days: list[dict]) -> list[dict]:
    return [{"date": d["date"], "pheno": d["pheno"], "heat": d["heat_level"],
             "drought": d["drought_level"], "cold": d["cold_level"], "freeze": d["freeze_level"]}
            for d in backtest.assess([dict(x) for x in days])]


def compare(a: list[dict], b: list[dict], keys=LEVEL_KEYS) -> list[str]:
    out = []
    if len(a) != len(b):
        out.append(f"天数不同 {len(a)} vs {len(b)}")
    for x, y in zip(a, b):
        if x["date"] != y["date"]:
            out.append(f"{x['date']} 日期错位 vs {y['date']}")
            continue
        for k in keys:
            if x[k] != y[k]:
                out.append(f"{x['date']} {k}: {x[k] or '无'} vs {y[k] or '无'}")
    return out


# ---------------- 合成序列构造 ----------------
def series(start: str, specs: list[tuple | None]) -> list[dict]:
    """specs 每项 (pr, tmax, tmin[, et])；None 表示该日整体缺测（从序列剔除，用于制造断链）。
    未给 et 的日用例＝ET0 缺失，两套实现都必须自动退回"仅降水口径"。"""
    out, cur = [], date.fromisoformat(start)
    for sp in specs:
        if sp is not None:
            pr, tmax, tmin, *rest = sp
            day = {"date": cur.isoformat(), "tmax": tmax, "tmin": tmin, "pr": pr}
            if rest:
                day["et"] = rest[0]
            out.append(day)
        cur += timedelta(days=1)
    return out


def flat(n: int, pr=10.0, tmax=25.0, tmin=20.0) -> list[tuple]:
    return [(pr, tmax, tmin)] * n


def resolve_range(where: str, days: list[dict]) -> list[int]:
    n = len(days)
    if where == "last":
        return [n - 1]
    if where == "tail6":
        return list(range(n - 6, n))
    if where == "tail2":
        return list(range(n - 2, n))
    if where == "steps":  # 单日台阶用例：跳过插入的凉爽日（tmax=25）
        return [i for i, d in enumerate(days) if d["tmax"] != 25.0]
    raise ValueError(f"未知的用例位置标记: {where}")


# ---------------- 边界合成用例（期望值按规则文本单独写死） ----------------
def cases():
    c = []

    # A) rain30 阈值边界：等级只由 30 天累计降水决定（连旱日须压在 8 以下）
    # 构造要点：有效降水门槛是 1mm，逐日雨量若 <1mm 就算旱日，所以低 rain30 只能用"隔干"模式
    for x, want in [(45.03, None), (44.97, "轻")]:
        c.append((f"rain30 边界 {x} mm（逐日有水）", "drought",
                  series("2023-07-01", flat(60, pr=round(x / 30, 4))), "last", want))
    for x, want in [(30.04, "轻"), (29.97, "中")]:
        a = round(x / 15, 4)  # 60 天 = 30 组（a, 干）；末 30 天含 15 个 a
        c.append((f"rain30 边界 {x} mm（一天干一天湿）", "drought",
                  series("2023-07-01", [(a, 30.0, 20.0), (0.0, 30.0, 20.0)] * 30), "last", want))
    for x, want in [(15.03, "中"), (14.97, "重")]:
        a = round(x / 10, 4)  # 90 天 = 30 组（a, 干, 干）；末 30 天含 10 个 a
        c.append((f"rain30 边界 {x} mm（两天干一天湿）", "drought",
                  series("2023-07-01", [(a, 30.0, 20.0), (0.0, 30.0, 20.0), (0.0, 30.0, 20.0)] * 30),
                  "last", want))

    # B) 连旱日阈值边界：rain30 足够高（窗口内湿日 ≥23 个），等级只由连旱日数决定
    #    注意必须留满 30 天回看（评审 A-Q9：此前只给 22+7 天，末日 dayIndex=28 被窗口闸门拦掉，
    #    测的其实是"序列不足不判级"而不是阈值边界）
    for k, want in [(7, None), (8, "轻"), (15, "中"), (25, "重")]:
        c.append((f"连旱日边界 {k} 天", "drought",
                  series("2023-07-01", flat(30) + [(0.0, 30.0, 20.0)] * k), "last", want))

    # C) 连续高温升级链：第 3 天 轻→中，第 5 天 →重
    c.append(("连续高温升级 1-6 天", "heat",
              series("2023-07-01", flat(30) + [(1.0, 35.2, 20.0)] * 6), "tail6",
              ["轻", "轻", "中", "中", "重", "重"]))

    # D) 单日温度台阶：每个测试日之间插一天凉爽日，使 streak=1，排除升级干扰
    seq = []
    for t in [32.9, 33.2, 34.98, 35.0, 36.99, 37.0, 38.99, 39.0, 40.0]:
        seq += [(10.0, t, 20.0), (10.0, 25.0, 20.0)]
    c.append(("单日温度台阶", "heat", series("2023-07-01", seq[:-1]), "steps",
              [None, "关注", "关注", "轻", "轻", "中", "中", "重", "重"]))

    # E) 断链清零连续计数：缺测一天后 streak 应回到 1 → 轻（而不是中）
    c.append(("断链后连续计数清零", "heat",
              series("2023-07-01", flat(30) + [(1.0, 35.2, 20.0), (1.0, 35.2, 20.0), None, (1.0, 35.2, 20.0)]),
              "last", "轻"))

    # F) 低温跨物候边界逐对核对跳档 ≤1 档（现蕾 4/1-4/9 → 开花 4/10-5/10 → 幼果 5/11-6/20 → 膨大）
    for name, d0, tmin, want in [
        ("现蕾→开花 tmin=2", "2023-04-09", 2.0, ["中", "重"]),
        ("现蕾→开花 tmin=5", "2023-04-09", 5.0, ["关注", "中"]),
        ("现蕾→开花 tmin=0", "2023-04-09", 0.0, ["重", "重"]),
        ("开花→幼果 tmin=2", "2023-05-10", 2.0, ["重", "中"]),
        ("开花→幼果 tmin=5", "2023-05-10", 5.0, ["中", "轻"]),
        ("开花→幼果 tmin=0", "2023-05-10", 0.0, ["重", "重"]),
        ("幼果→膨大 低温风险窗关闭 tmin=5", "2023-06-20", 5.0, ["轻", None]),
    ]:
        c.append((f"低温物候边界 {name}", "cold",
                  series(d0, [(10.0, 25.0, tmin)] * 2), "tail2", want))

    # G) 闰日 2/29 必须显式落入休眠期（评审 A-Q10：曾走兜底被静默判期）
    dor = series("2024-01-31", [(0.0, 12.0, -8.0)] * 30)  # 末日为 2024-02-29
    c.append(("闰日 2024-02-29 休眠期不判干旱", "drought", dor, "last", None))
    c.append(("闰日 2024-02-29 冻害判级生效", "freeze", dor, "last", "重"))

    # H) 回看窗口不满 30 天不判干旱（评审 A-Q5：缺测日使 rain30 偏低→等级虚高）
    gap = series("2023-07-01", [(50.0, 30.0, 20.0)] + [(0.0, 30.0, 20.0)] * 38
                 + [None] + [(0.0, 30.0, 20.0)] * 10)
    c.append(("窗口不满 30 天不判干旱", "drought", gap, "last", None))

    # I) 蒸散口径（def30）分位边界：逐日有水使降水口径恒为"无"，等级只可能来自蒸散口径
    #    膨大期阈值：轻 54.1 / 中 87.4 / 重 122.6 mm（全段十年 p75/p90/p99，见 baseline_stats 第 3 节）
    for x, want in [(54.15, "轻"), (54.05, None), (87.45, "中"), (87.35, "轻"),
                    (122.65, "重"), (122.55, "中")]:
        e = round((300.0 + x) / 30, 6)  # 30 天窗口：rain30=300，et30=300+x
        c.append((f"def30 边界 {x} mm（降水口径不构成等级）", "drought",
                  series("2023-07-01", [(10.0, 30.0, 20.0, e)] * 60), "last", want))

    # J) ET0 缺测时自动退回降水口径（不得凭空造出蒸散等级）
    partly = [(10.0, 30.0, 20.0, 11.805) for _ in range(60)]
    partly[45] = (10.0, 30.0, 20.0, None)
    c.append(("ET0 缺一日则蒸散口径整体退出", "drought", series("2023-07-01", partly), "last", None))

    # K) 休眠期靠蒸散口径出级（降水口径仍沉默）：def30 超休眠期自身 p99 → 重
    e_dor = round((300.0 + 48.6) / 30, 6)
    c.append(("休眠期蒸散口径仍可判重", "drought",
              series("2023-12-01", [(10.0, 30.0, 20.0, e_dor)] * 60), "last", "重"))

    # L) 窗口内含降水缺测日：两侧都必须整体不判级（既不当 0mm 用，也不静默丢日）——评审 A-Q4
    nulls = [(10.0, 30.0, 20.0)] * 40 + [(None, 30.0, 20.0)] + [(10.0, 30.0, 20.0)] * 10
    c.append(("窗口含降水缺测日不判干旱", "drought", series("2023-07-01", nulls), "last", None))

    # M) 开花期蒸散口径封顶"中"（留出检验显示该档分位最不稳，2027 花期对表前不给最高级）
    #    末日必须落在开花期（4/10–5/10）内且回看已满 30 天：起 2023-03-15，55 天 → 末日 2023-05-08
    e_bl = round((300.0 + 140.0) / 30, 6)   # def30=140，远超开花期 p99 102.3，仍只能判"中"
    c.append(("开花期蒸散口径封顶中", "drought",
              series("2023-03-15", [(10.0, 30.0, 20.0, e_bl)] * 55), "last", "中"))
    return c


# ---------------- 物候表检查 ----------------
def matched(table, cur: date):
    m, d = cur.month, cur.day
    for row in table:
        pid, (fm, fd), (tm, td) = row[0], row[1], row[2]
        if fm <= tm:
            hit = (m > fm or (m == fm and d >= fd)) and (m < tm or (m == tm and d <= td))
        else:
            hit = (m > fm or (m == fm and d >= fd)) or (m < tm or (m == tm and d <= td))
        if hit:
            return pid
    return None


def coverage_gaps(table, y0: int, y1: int) -> list[str]:
    gaps, cur, stop = [], date(y0, 1, 1), date(y1, 12, 31)
    while cur <= stop:
        if matched(table, cur) is None:
            gaps.append(cur.isoformat())
        cur += timedelta(days=1)
    return gaps


if hasattr(sys.stdout, "reconfigure"):  # Windows 默认 GBK 控制台降级输出，不崩
    sys.stdout.reconfigure(errors="replace")

def main() -> int:
    use_online = "--online" in sys.argv
    fails: list[str] = []
    total_days = 0
    ran_labels: list[str] = []
    total_days = 0
    if use_online:
        sources = [(s, e, label, backtest.fetch(LAT, LON, s, e)) for s, e, label in WINDOWS]
    else:
        if not FIXTURE.exists():
            print(f"缺少 {FIXTURE.name}，请先运行 python tools/fetch_fixture.py")
            return 1
        fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        meta = fx["meta"]
        print(f"数据源：fixture {FIXTURE.name}（请求坐标 {meta['requested_lat']},{meta['requested_lon']} → "
              f"实际格点 {meta['gridpoint_lat']},{meta['gridpoint_lon']}，拉取于 {meta['pulled_at_utc']}）")
        sources = []
        for s, e, label in WINDOWS:
            d = fx["daily"]
            keep = [i for i, t in enumerate(d["time"]) if s <= t <= e]
            sl = {k: [v[i] for i in keep] for k, v in d.items()}
            sources.append((s, e, label, backtest.filter_days(sl)))

    for s, e, label, days in sources:
        ran_labels.append(f"{s}~{e}")
        if len(days) < 40:
            fails.append(f"窗口 {s}~{e} 只取到 {len(days)} 天，被跳过——按失败处理（评审 A-Q3）")
            print(f"  [FAIL] {s}~{e} 仅 {len(days)} 天，跳过")
            continue
        py, js = py_levels(days), js_levels(days)
        diffs = compare(py, js)
        hits = {k: sum(1 for x in py if x[k]) for k in RISK_KEYS}
        total_days += len(py)
        if diffs:
            fails.extend([f"[{s}~{e}] {x}" for x in diffs[:8]])
        print(f"{s}~{e}｜{label}｜{len(py)} 天｜触发日数 热{hits['heat']}/旱{hits['drought']}"
              f"/低温{hits['cold']}/冻{hits['freeze']}｜不一致 {len(diffs)} 处" + ("" if diffs else "  [OK]"))

    print("\n=== 2) 边界合成用例（两侧都须命中单独写死的期望）===")
    for name, key, days, where, want in cases():
        py, js = py_levels(days), js_levels(days)
        rng = resolve_range(where, days)
        exp = want if isinstance(want, list) else [want]
        bad = []
        for n, i in enumerate(rng):
            if exp[n] != py[i][key]:
                bad.append(f"PY={py[i][key] or '无'} 期望={exp[n] or '无'} @ {py[i]['date']}")
            if exp[n] != js[i][key]:
                bad.append(f"JS={js[i][key] or '无'} 期望={exp[n] or '无'} @ {js[i]['date']}")
        if bad:
            fails.extend([f"{name}: {x}" for x in bad])
        print(f"  {'[OK]  ' if not bad else '[FAIL]'} {name} → {[(v or '无') for v in exp]}")

    print("\n=== 3) 物候表与阈值常量（JS / Python / 现算分位三方绑定）===")
    tab = js_tables()
    js_tab = [(x["id"], x["from"], x["to"]) for x in tab["phenology"]]
    py_tab = [(pid, list(fm), list(td)) for pid, fm, td in backtest.PHENOLOGY]
    if js_tab != py_tab:
        fails.append(f"物候表不一致 JS={js_tab} PY={py_tab}")
        print("  [FAIL] 两表逐条比对不一致")
    else:
        print(f"  [OK]   物候表逐条一致（{len(js_tab)} 条区间）")
    for label, t in (("JS", js_tab), ("PY", py_tab)):
        gaps = coverage_gaps(t, 2016, 2028)
        if gaps:
            fails.append(f"{label} 物候表存在未覆盖日期（如 {gaps[:5]}）")
            print(f"  [FAIL] {label} 表有 {len(gaps)} 天落空（含闰日检查，2016–2028）")
        else:
            print(f"  [OK]   {label} 表 2016–2028 每天均显式命中（含 2/29 闰日）")

    # 阈值常量单一事实源：JS == PY == 用冻结序列现算的 p75/p90/p99（评审 A-Q9）
    if tab["defThresh"] != backtest.DEF_THRESH:
        fails.append(f"DEF_THRESH JS≠PY：JS={tab['defThresh']} PY={backtest.DEF_THRESH}")
        print("  [FAIL] DEF_THRESH 在 JS 与 Python 之间不一致")
    else:
        print(f"  [OK]   DEF_THRESH JS==PY（{len(backtest.DEF_THRESH)} 个物候期）")
    if tab.get("defMaxLevel") != backtest.DEF_MAX_LEVEL:
        fails.append(f"DEF_MAX_LEVEL JS≠PY：JS={tab.get('defMaxLevel')} PY={backtest.DEF_MAX_LEVEL}")
    else:
        print("  [OK]   DEF_MAX_LEVEL JS==PY（蒸散口径封顶规则）")
    try:
        import baseline_stats  # noqa: E402
        daily = json.loads(FIXTURE.read_text(encoding="utf-8"))["daily"]
        th_now, _, _ = baseline_stats.derive_thresholds(
            backtest.assess([dict(d) for d in baseline_stats.backtest.filter_days(daily)]),
            baseline_stats.et0_30(daily))
        bad_th = [f"{pid}.{lv}：代码 {backtest.DEF_THRESH[pid][lv]} ≠ 现算 {th_now[pid][lv]}"
                  for pid in th_now if pid in backtest.DEF_THRESH
                  for lv in ("轻", "中", "重")
                  if abs(th_now[pid][lv] - backtest.DEF_THRESH[pid][lv]) > 0.05]
        if bad_th:
            fails.extend(bad_th)
            print(f"  [FAIL] 阈值常量与现算分位脱钩（{len(bad_th)} 项）——手抄未随定标更新")
        else:
            print("  [OK]   阈值常量 == 冻结序列现算分位（±0.05），单一事实源成立")
    except Exception as exc:  # noqa: BLE001
        fails.append(f"阈值现算校验无法执行：{exc}")
        print(f"  [FAIL] 阈值现算校验异常：{exc}")

    if len(ran_labels) != len(WINDOWS):
        fails.append(f"真实窗口应跑 {len(WINDOWS)} 个，实际 {len(ran_labels)} 个")
    if total_days < MIN_REAL_DAYS:
        fails.append(f"逐日对拍天数 {total_days} 低于断言下限 {MIN_REAL_DAYS}，不得宣布等价")
    if len(cases()) < MIN_CASES:
        fails.append(f"边界用例数 {len(cases())} 低于断言下限 {MIN_CASES}（材料中的用例数需同步）")

    print(f"\n合计：真实序列 {total_days} 天逐日对拍（{len(ran_labels)}/{len(WINDOWS)} 窗口）"
          f"｜边界用例 {len(cases())} 组｜物候表 2 份 + 阈值常量 2 组")
    if fails:
        print(f"发现 {len(fails)} 处问题（前 20 条）：")
        for f in fails[:20]:
            print("  [X] " + f)
        print("\n结论：两套实现分叉、或规则与文档不符 → 禁止提交")
        return 1
    print("结论：JS 与 Python 逐日等价，边界用例全部命中，物候表无洞 —— 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
