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
        [{k: d[k] for k in ("date", "tmax", "tmin", "pr")} for d in days])))


def js_phenology() -> list[tuple[str, list[int], list[int]]]:
    t = json.loads(run_node("--tables"))
    return [(x["id"], x["from"], x["to"]) for x in t]


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
    """specs 每项 (pr, tmax, tmin)；None 表示该日整体缺测（从序列剔除，用于制造断链）。"""
    out, cur = [], date.fromisoformat(start)
    for sp in specs:
        if sp is not None:
            pr, tmax, tmin = sp
            out.append({"date": cur.isoformat(), "tmax": tmax, "tmin": tmin, "pr": pr})
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
    if where == "tail4":
        return list(range(n - 4, n))
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

    # B) 连旱日阈值边界：rain30=220mm 足够高，等级只由连旱日数决定
    for k, want in [(7, None), (8, "轻"), (15, "中"), (25, "重")]:
        c.append((f"连旱日边界 {k} 天", "drought",
                  series("2023-07-01", flat(22) + [(0.0, 30.0, 20.0)] * k), "last", want))

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


def main() -> int:
    use_online = "--online" in sys.argv
    fails: list[str] = []

    print("=== 1) 真实序列逐日对拍（物候 + 四类风险等级）===")
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
        if len(days) < 40:
            print(f"  [跳过] {s}~{e} 仅 {len(days)} 天")
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

    print("\n=== 3) 物候表（JS 与 Python 镜像一致性 + 全年无洞）===")
    js_tab = js_phenology()
    py_tab = [(pid, list(fm), list(td)) for pid, fm, td in backtest.PHENOLOGY]
    if js_tab != py_tab:
        fails.append(f"物候表不一致 JS={js_tab} PY={py_tab}")
        print("  [FAIL] 两表逐条比对不一致")
    else:
        print(f"  [OK]   两表逐条一致（{len(js_tab)} 条区间）")
    for label, tab in (("JS", js_tab), ("PY", py_tab)):
        gaps = coverage_gaps(tab, 2016, 2028)
        if gaps:
            fails.append(f"{label} 物候表存在未覆盖日期（如 {gaps[:5]}）")
            print(f"  [FAIL] {label} 表有 {len(gaps)} 天落空（含闰日检查，2016–2028）")
        else:
            print(f"  [OK]   {label} 表 2016–2028 每天均显式命中（含 2/29 闰日）")

    print(f"\n合计：真实序列 {total_days} 天逐日对拍｜边界用例 {len(cases())} 组｜物候表 2 份")
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
