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
# 四类风险的归因文案全部进逐日比对（评审 A-Q4/A-Q8）：等级相同但"为什么"不同，
# 前端展示的归因就是第二套真相——此前只比对干旱，热害两套串并存了两轮没被抓到
LEVEL_KEYS = ("pheno", *RISK_KEYS, "why", "heatWhy", "coldWhy", "freezeWhy")

WINDOWS = [
    ("2022-06-01", "2022-09-30", "热害季（≥35℃ 升级链）"),
    ("2021-11-25", "2022-02-28", "越冬期（休眠期干旱抑制 + 冻害）"),
    ("2023-03-01", "2023-06-30", "萌芽—开花—幼果—膨大（物候边界密集）"),
    ("2025-08-21", "2025-11-30", "秋汛至采收（成熟期→休眠期切换）"),
]


MIN_REAL_DAYS = 400   # 断言下限：低于此数说明窗口被跳过或数据源缺段，闸门不得宣布"等价"（评审 A-Q3）
MIN_CASES = 28        # 断言下限：边界用例数，材料里引用的就是这个数
MIN_SWEEP_DAYS = 1200  # 断言下限：单调性扫描的逐日比对点位数（设计值 42 档 × 31 天 = 1302），扫描被跳过或缩水即失败
# 每个窗口额外向前多取 WARMUP 天再判级，只比对报告区间内的日（评审 A-Q5）：
# 不这么做时窗口头 30 天干旱口径被"回看不满"闸门沉默，对拍看似通过，实际比的是"都不判级"。
WARMUP = backtest.WARMUP_DAYS


def _trim(rows, s):
    """丢掉只存在于 warm-up 前缀里的日（这些日在报告区间外，不参与等价性结论）。"""
    return [x for x in rows if x["date"] >= s]



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
             "drought": d["drought_level"], "why": d["drought_why"],
             "heatWhy": d["heat_why"], "coldWhy": d["cold_why"], "freezeWhy": d["freeze_why"],
             "cold": d["cold_level"], "freeze": d["freeze_level"]}
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
                a_, b_ = x[k] or "无", y[k] or "无"
                if k == "why":  # 归因文案长，只给前 60 字，够定位分歧点
                    a_, b_ = str(a_)[:60], str(b_)[:60]
                out.append(f"{x['date']} {k}: {a_} vs {b_}")
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


# ---------------- 单调性扫描（评审 A-Q2：不能只有人工写死的边界点） ----------------
def _mk_rain(v):
    """v = (逐日雨量, 末尾连旱天数)：造出一条**确实跨过 无/轻/中/重** 的旱情递增轴（评审 A-Q8）。"""
    pr, dry = v
    return {"pr": pr, "tmax": 25.0, "tmin": 20.0, "_dry_tail": dry}


SWEEPS = {
    # key: (风险, 取值序列, 逐日构造函数, 方向, 起点日期, 最少跨过的等级数)
    # 方向指"取值变大时等级该怎么走"；起点日期决定落在哪个物候期
    "旱情递增→干旱": ("drought", [(10.0, 0), (1.6, 0), (1.5, 8), (1.2, 8), (1.0, 15), (0.6, 15), (0.4, 25), (0.2, 30)],
                 _mk_rain, "nondecreasing", "2023-07-01", 4),
    "tmax→热害": ("heat", [25.0, 32.9, 33.0, 34.9, 35.0, 36.9, 37.0, 38.9, 39.0, 41.0],
               lambda v: {"pr": 10.0, "tmax": v, "tmin": 20.0}, "nondecreasing", "2023-07-01", 3),
    "ET0→干旱": ("drought", [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 25.0],
               lambda v: {"pr": 10.0, "tmax": 25.0, "tmin": 20.0, "et": v}, "nondecreasing", "2023-07-01", 3),
    # 低温与冻害的八条线此前从没被扫过一次（评审 A-Q8）
    "tmin→花期低温": ("cold", [0.0, 2.0, 2.1, 5.0, 5.1, 8.0, 8.1, 9.0],
                 lambda v: {"pr": 10.0, "tmax": 25.0, "tmin": v}, "nonincreasing", "2023-04-10", 4),
    "tmin→越冬冻害": ("freeze", [-8.0, -7.1, -7.0, -5.1, -5.0, -3.1, -3.0, 0.0],
                 lambda v: {"pr": 10.0, "tmax": 12.0, "tmin": v}, "nonincreasing", "2023-01-05", 4),
}
SWEEP_DAYS = 31


RANK = {None: 0, "关注": 1, "轻": 2, "中": 3, "重": 4}


def check_sweeps() -> tuple[list[str], int]:
    """单调性＋两侧一致：逐日全比 + 信息量下限（评审 A-Q3/A-Q8）。"""
    fails, points, report = [], 0, []
    sweep_rated = sweep_total = 0
    for kind, (risk, vals, mk, direction, s0, min_levels) in SWEEPS.items():
        per_side = {"PY": [], "JS": []}
        cur = date.fromisoformat(s0)
        for side in ("PY", "JS"):
            for v in vals:
                days = []
                spec = mk(v)
                tail = spec.pop("_dry_tail", 0)
                # 与真实窗口同一条准则：先喂 WARMUP 天预热 30 天回看窗口，只比对报告区间内的日
                # （评审 A-R9①：不这么做，扫描点位里近半是"两侧都因回看不满而沉默"的一致，
                #   数量对得上但没有信息量）
                for k in range(-WARMUP, SWEEP_DAYS):
                    d = {"date": (cur + timedelta(days=k)).isoformat()}
                    d.update(spec)
                    if tail and k >= SWEEP_DAYS - tail:      # 末尾若干天造成旱日（<1mm）
                        d["pr"] = 0.2
                    days.append(d)
                full = py_levels(days) if side == "PY" else js_levels(days)
                kept = [r for r in full if r["date"] >= s0]
                if len(kept) != SWEEP_DAYS:
                    fails.append(f"{kind} 报告区间保留 {len(kept)} 天 ≠ {SWEEP_DAYS} 天，扫描被截断")
                per_side[side].append(kept)
        local = []
        # 每条轴比对**该风险自己的**归因文案：drought→why、heat→heatWhy、cold→coldWhy、freeze→freezeWhy。
        # 只比 why（干旱串）时，热害/低温/冻害三条轴共 26 档 × 31 天＝806 个点位的归因从没比过（评审 A-R8①）
        why_key = {"drought": "why", "heat": "heatWhy", "cold": "coldWhy", "freeze": "freezeWhy"}[risk]
        for ti, (a_rows, b_rows) in enumerate(zip(per_side["PY"], per_side["JS"])):
            if len(a_rows) != len(b_rows):
                local.append(f"{kind} 第 {ti + 1} 档两侧天数不等 {len(a_rows)} vs {len(b_rows)}——"
                             "zip 会静默截断，实跑点位数与材料引用的设计值就此脱钩")
                continue
            for x, y in zip(a_rows, b_rows):
                points += 1
                if x[risk] != y[risk]:
                    local.append(f"{kind} 扫描 {x['date']} 两侧等级不同：{x[risk]} vs {y[risk]}")
                if why_key not in x or why_key not in y:      # 缺键≠相等：字段改名要让闸门当场炸
                    local.append(f"{kind} 扫描 {x['date']} 缺归因字段 {why_key}"
                                 f"（PY {sorted(x)} / JS {sorted(y)}）——比对退化为空对空")
                elif (x[why_key] or "") != (y[why_key] or ""):
                    local.append(f"{kind} 扫描 {x['date']} 两侧归因文案不同（{why_key}）："
                                 f"{x.get(why_key)!r} vs {y.get(why_key)!r}")
        for idx in range(SWEEP_DAYS):
            ranks = [RANK[rows[idx][risk]] for rows in per_side["PY"]]
            if direction == "nondecreasing" and any(b < a for a, b in zip(ranks, ranks[1:])):
                local.append(f"{kind} 第 {idx + 1} 日位倒挂（取值升高等级却降）：{vals} → {ranks}")
            if direction == "nonincreasing" and any(b > a for a, b in zip(ranks, ranks[1:])):
                local.append(f"{kind} 第 {idx + 1} 日位倒挂（取值变缓和等级却升）：{vals} → {ranks}")
        rated = sum(1 for rows in per_side["PY"] for r in rows if r[risk] is not None)
        axis_points = len(vals) * SWEEP_DAYS
        if rated * 2 < axis_points:      # 一半以上点位两侧都没级＝扫描在比空值（评审 A-R9①）
            fails.append(f"{kind} 信息量不足：{axis_points} 个点位里只有 {rated} 个（"
                         f"{100 * rated / axis_points:.0f}%）引擎真的出级")
        sweep_rated += rated
        sweep_total += axis_points
        distinct = {rows[SWEEP_DAYS - 1][risk] for rows in per_side["PY"]}
        if len(distinct) < min_levels:
            local.append(f"{kind} 扫描零信息量：末日只出现 {sorted(str(x or '无') for x in distinct)}，"
                         f"没跨过该轴声明的 {min_levels} 个等级——取值区间选错了，不是规则对了")
        fails.extend(local)
        report.append(f"  {'[OK]  ' if not local else '[FAIL]'} {kind}（{direction}，{len(vals)} 档 × "
                      f"{SWEEP_DAYS} 天，起点 {s0}，归因字段 {why_key}，出级点位 {rated}/{axis_points}）→ 末日等级序 "
                      f"{[rows[SWEEP_DAYS - 1][risk] or '无' for rows in per_side['PY']]}")
    if sweep_total and sweep_rated * 10 < sweep_total * 7:
        fails.append(f"单调性扫描整体信息量不足：{sweep_total} 个点位仅 {sweep_rated} 个出级"
                     f"（{100 * sweep_rated / sweep_total:.0f}%，下限 70%）")
    print(f"  扫描出级点位合计 {sweep_rated}/{sweep_total}（{100 * sweep_rated / max(sweep_total, 1):.0f}%，"
          f"单轴下限 50%、整体下限 70%）")
    design = sum(len(v[1]) for v in SWEEPS.values()) * SWEEP_DAYS
    if points != design:
        fails.append(f"单调性扫描实跑点位 {points} ≠ 设计值 {design}（{len(SWEEPS)} 条轴）——"
                     "材料里印的是设计值，两侧天数不等或扫描缩水时该数就是假的（评审 A-R8③）")
    fails_out = fails
    for line in report:
        print(line)
    return fails_out, points


# ---------------- 归因文案里的"诚实限定词"（两侧一致，且不许被悄悄删掉）----------------
# 这些短语是本项目对"格点未联校"的显式声明；删掉任何一句都不会改变等级，
# 因此没有断言就会被无声改掉（评审 A-R11⑧⑨）。
WORDING_CASES = [
    # (说明, 末日, 逐日模板(日期,值), 风险键, 期望等级, 必含子串, 禁含子串)
    # 必含子串一律带数字本身：只钉"超休眠期自身 p90 门槛"这种纯短语，把门槛从 p90 改回 p75
    # 照样通过——而材料明令禁止"超 p75 即轻"（评审 A-R12④）
    ("休眠期蒸散只出轻级、门槛取自身 p90 并印真实分位档", "2023-01-20",
     lambda d: {"tmax": 12.0, "tmin": -2.0, "pr": 1.0, "et": 3.0},
     "drought", "轻",
     f'超休眠期自身 p90 门槛 {backtest.DEF_THRESH["dormant"]["中"]:.1f} mm',
     ["超该物候期定标分位轻级线"]),
    ("休眠期冻害附微地形背景场声明", "2023-01-05",
     lambda d: {"tmax": 8.0, "tmin": -4.0, "pr": 6.0, "et": 2.0},
     "freeze", "轻", "乡镇尺度背景场", []),
    ("花期低温用相关而非因果措辞", "2023-04-15",
     lambda d: {"tmax": 18.0, "tmin": 1.0, "pr": 8.0, "et": 4.0},
     "cold", "重", "落花风险高（本地未联校）", []),
    ("膨大期热害把风险落在日灼与落果、不外推产量", "2023-07-10",
     lambda d: {"tmax": 36.0, "tmin": 25.0, "pr": 8.0, "et": 6.0},
     "heat", "重", "日灼与落果风险高（本地产量响应未联校）", ["减产", "绝收"]),
    ("膨大期干旱只声明影响单果重与果径", "2023-07-10",
     lambda d: {"tmax": 30.0, "tmin": 22.0, "pr": 1.0, "et": 8.0},
     "drought", "重", "膨大期缺水主要影响单果重与果径（本地产量响应未联校）", ["导致减产"]),
]
MIN_WORDING_CASES = 5   # 与清单实数相等：下限取 4 就等于允许先悄悄删掉一条（评审 A-R13⑩）


def check_wording() -> tuple[list[str], int]:
    fails, ok = [], 0
    if len(WORDING_CASES) < MIN_WORDING_CASES:
        fails.append(f"限定词用例只剩 {len(WORDING_CASES)} 组，低于下限 {MIN_WORDING_CASES}——"
                     "这一层不许靠删清单来通过")
    for label, end, mk, key, want_lv, needle, banned in WORDING_CASES:
        days, cur = [], date.fromisoformat(end) - timedelta(days=44)
        for i in range(45):
            d = {"date": (cur + timedelta(days=i)).isoformat()}
            d.update(mk(d))
            days.append(d)
        py, js = py_levels(days)[-1], js_levels(days)[-1]
        mine = py["why"] if key == "drought" else py[key + "Why"]
        theirs = js["why"] if key == "drought" else js[key + "Why"]
        if mine != theirs:
            fails.append(f"{label}：两侧归因文案不同 {mine!r} vs {theirs!r}")
        elif not mine or needle not in mine:
            fails.append(f"{label}：{key} 归因里缺少限定词「{needle}」（当前：{mine!r}）")
        elif next((b for b in banned if b in mine), None):
            fails.append(f"{label}：{key} 归因里出现被限定的因果断言「{banned}」之一（当前：{mine!r}）")
        elif py[key] != want_lv:
            fails.append(f"{label}：{key} 等级不是 {want_lv}（实为 {py[key]}），"
                         "用例失效——限定词断言不能靠空用例通过")
        else:
            ok += 1
            print(f"  [OK]   {label}（{key}={want_lv}，含「{needle}」）")
    return fails, ok


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

    # A) rain30 阈值边界（连旱日压在 8 以下）：新规则下"只是累计偏低、没连着旱"不构成轻级
    #    （评审 C-Q2：季风区萌芽/成熟期 rain30≤45 命中率高，单凭它报轻＝常驻噪声）
    for x, want in [(45.03, None), (44.97, None)]:
        c.append((f"rain30 边界 {x} mm（连旱日 0，不构成轻级）", "drought",
                  series("2023-07-01", flat(60, pr=round(x / 30, 4))), "last", want))
    for x, want in [(30.04, None), (29.97, "中")]:
        a = round(x / 15, 4)  # 60 天 = 30 组（a, 干）；末 30 天含 15 个 a
        c.append((f"rain30 边界 {x} mm（一天干一天湿）", "drought",
                  series("2023-07-01", [(a, 30.0, 20.0), (0.0, 30.0, 20.0)] * 30), "last", want))
    for x, want in [(15.03, "中"), (14.97, "重")]:
        a = round(x / 10, 4)  # 90 天 = 30 组（a, 干, 干）；末 30 天含 10 个 a
        c.append((f"rain30 边界 {x} mm（两天干一天湿）", "drought",
                  series("2023-07-01", [(a, 30.0, 20.0), (0.0, 30.0, 20.0), (0.0, 30.0, 20.0)] * 30),
                  "last", want))

    # A2) 轻级的双条件：连旱满 8 天 **且** 30 天累计 ≤45mm 才成立（只动 rain30 一个自由度）
    #     构造：30 个湿日 + 8 个干日 → 末日窗口 = 22 湿 + 8 干，dry_streak=8、rain30=22a
    for x, want in [(44.97, "轻"), (45.03, None)]:
        a = round(x / 22, 4)
        c.append((f"轻级双条件 rain30={x} 且连旱 8 天", "drought",
                  series("2023-07-01", [(a, 30.0, 20.0)] * 30 + [(0.0, 30.0, 20.0)] * 8),
                  "last", want))

    # B) 连旱日阈值边界（窗口内湿日雨量足量，等级只由连旱日数决定）
    #    注意必须留满 30 天回看（评审 A-Q9：此前只给 22+7 天，末日 dayIndex=28 被窗口闸门拦掉，
    #    测的其实是"序列不足不判级"而不是阈值边界）
    #    k=8 在 rain30=220 时不构成轻级（双条件），需到 15 天才由连旱日单独升为中
    for k, want in [(7, None), (8, None), (15, "中"), (25, "重")]:
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
    #    膨大期定标段（2016–2020）阈值：轻 54.2 / 中 88.7 / 重 120.9 mm（见 baseline_stats 第 3 节）
    for x, want in [(54.25, "轻"), (54.15, None), (88.75, "中"), (88.65, "轻"),
                    (120.95, "中"), (120.85, "中")]:
        e = round((300.0 + x) / 30, 6)  # 30 天窗口：rain30=300，et30=300+x
        tag = "无降水佐证，达重级线也封顶中" if x >= 120.9 else "降水口径不构成等级"
        c.append((f"def30 边界 {x} mm（{tag}）", "drought",
                  series("2023-07-01", [(10.0, 30.0, 20.0, e)] * 60), "last", want))

    # I2) 佐证规则的两面：同样超膨大期重级线，降水口径到"中"才放行"重"，未到则封顶"中"
    for r, want in [(30.0, "重"), (31.0, "中")]:
        et = round((r + 122.0) / 30, 6)   # def30=122 > 120.9；r=30 触发"中"（雨30≤30），r=31 不触发
        pr = round(r / 30, 6)
        c.append((f"def30 超 p99 且雨30={r} mm（降水佐证判定）", "drought",
                  series("2023-07-01", [(pr, 30.0, 20.0, et)] * 60), "last", want))

    # J) ET0 缺测时自动退回降水口径（不得凭空造出蒸散等级）
    partly = [(10.0, 30.0, 20.0, 11.805) for _ in range(60)]
    partly[45] = (10.0, 30.0, 20.0, None)
    c.append(("ET0 缺一日则蒸散口径整体退出", "drought", series("2023-07-01", partly), "last", None))

    # K) 休眠期靠蒸散口径出级（降水口径仍沉默）：def30 超休眠期定标段 p99 45.9，
    #    但该期没有降水口径可作佐证 → 按规则封顶"中"，冬季不再出现"重"级干旱
    e_dor = round((300.0 + 48.6) / 30, 6)
    c.append(("休眠期蒸散口径达 p99 也只到轻", "drought",
              series("2023-12-01", [(10.0, 30.0, 20.0, e_dor)] * 60), "last", "轻"))

    # L) 窗口内含降水缺测日：两侧都必须整体不判级（既不当 0mm 用，也不静默丢日）——评审 A-Q4
    nulls = [(10.0, 30.0, 20.0)] * 40 + [(None, 30.0, 20.0)] + [(10.0, 30.0, 20.0)] * 10
    c.append(("窗口含降水缺测日不判干旱", "drought", series("2023-07-01", nulls), "last", None))

    # M) 开花期同样受全局佐证规则约束（该档留出段 p99 命中率 19.4%，最不稳）
    #    末日必须落在开花期（4/10–5/10）内且回看已满 30 天：起 2023-03-15，55 天 → 末日 2023-05-08
    e_bl = round((300.0 + 140.0) / 30, 6)   # def30=140，远超开花期定标段 p99 82.0，仍只能判"中"
    c.append(("开花期无降水佐证封顶中", "drought",
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
    warm = {}
    if use_online:
        sources = []
        for s, e, label in WINDOWS:
            ws = (date.fromisoformat(s) - timedelta(days=WARMUP)).isoformat()
            warm[(s, e)] = ws
            sources.append((s, e, label, backtest.fetch(LAT, LON, ws, e)))
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
            ws = (date.fromisoformat(s) - timedelta(days=WARMUP)).isoformat()
            warm[(s, e)] = ws
            d = fx["daily"]
            keep = [i for i, t in enumerate(d["time"]) if ws <= t <= e]
            sl = {k: [v[i] for i in keep] for k, v in d.items()}
            sources.append((s, e, label, backtest.filter_days(sl)))

    for s, e, label, days in sources:
        ran_labels.append(f"{s}~{e}")
        if len(days) < 40:
            fails.append(f"窗口 {s}~{e} 只取到 {len(days)} 天，被跳过——按失败处理（评审 A-Q3）")
            print(f"  [FAIL] {s}~{e} 仅 {len(days)} 天，跳过")
            continue
        # 判级在带 warm-up 的完整序列上做，比对只取报告区间（评审 A-Q5：否则窗口头一个月
        # 两侧都被"回看不满 30 天"闸门沉默，那部分"一致"没有任何信息量）
        py = _trim(py_levels(days), s)
        js = _trim(js_levels(days), s)
        diffs = compare(py, js)
        hits = {k: sum(1 for x in py if x[k]) for k in RISK_KEYS}
        why_hits = sum(1 for x in py if x["why"])
        total_days += len(py)
        if diffs:
            fails.extend([f"[{s}~{e}] {x}" for x in diffs[:8]])
        print(f"{s}~{e}｜{label}｜{len(py)} 天（另取 warm-up 自 {warm[(s, e)]}）｜"
              f"触发日数 热{hits['heat']}/旱{hits['drought']}/低温{hits['cold']}/冻{hits['freeze']}"
              f"｜归因文案 {why_hits} 天｜不一致 {len(diffs)} 处" + ("" if diffs else "  [OK]"))

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
    js_tab = [(x["id"], x["name"], x["from"], x["to"]) for x in tab["phenology"]]
    py_tab = [(pid, nm, list(fm), list(td)) for pid, nm, fm, td in backtest.PHENOLOGY]
    if js_tab != py_tab:
        fails.append(f"物候表不一致 JS={js_tab} PY={py_tab}")
        print("  [FAIL] 两表逐条比对不一致")
    else:
        print(f"  [OK]   物候表逐条一致（{len(js_tab)} 条区间）")
    for label, t in (("JS", js_tab), ("PY", py_tab)):
        gaps = coverage_gaps([(x[0], x[2], x[3]) for x in t], 2016, 2028)
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
    if tab.get("defSoloCap") != backtest.DEF_SOLO_CAP:
        fails.append(f"DEF_SOLO_CAP JS≠PY：JS={tab.get('defSoloCap')} PY={backtest.DEF_SOLO_CAP}")
        print("  [FAIL] DEF_SOLO_CAP（蒸散口径单独出级上限）JS≠PY")
    else:
        print(f"  [OK]   DEF_SOLO_CAP JS==PY（= \"{backtest.DEF_SOLO_CAP}\"，蒸散口径需降水佐证规则）")
    if tab.get("defCapDormant") != backtest.DEF_CAP_DORMANT:
        fails.append(f"DEF_CAP_DORMANT JS≠PY：JS={tab.get('defCapDormant')} PY={backtest.DEF_CAP_DORMANT}")
        print("  [FAIL] DEF_CAP_DORMANT（休眠期蒸散口径上限）JS≠PY")
    else:
        print(f"  [OK]   DEF_CAP_DORMANT JS==PY（= \"{backtest.DEF_CAP_DORMANT}\"，休眠期只到轻级）")
    # 规则版本号与定标段/留出段标签：页脚、导出报告、材料里引用的是同一串
    for key, attr in (("ruleVersion", "RULE_VERSION"), ("defCalibLabel", "DEF_CALIB_LABEL"),
                      ("holdoutLabel", "HOLDOUT_LABEL")):
        if tab.get(key) != getattr(backtest, attr):
            fails.append(f"{attr} JS≠PY：JS={tab.get(key)} PY={getattr(backtest, attr)}")
            print(f"  [FAIL] {attr} JS≠PY")
    try:
        import baseline_stats as _bs
        n_before = len(fails)
        want_calib = f"{min(_bs.CALIB_YEARS)}–{max(_bs.CALIB_YEARS)}"
        want_hold = f"{min(_bs.EVAL_YEARS)}–{max(_bs.EVAL_YEARS)}"
        if backtest.DEF_CALIB_LABEL != want_calib:
            fails.append(f"定标段标签与 baseline_stats.CALIB_YEARS 不符：{backtest.DEF_CALIB_LABEL} ≠ {want_calib}")
        if backtest.HOLDOUT_LABEL != want_hold:
            fails.append(f"留出段标签与 baseline_stats.EVAL_YEARS 不符：{backtest.HOLDOUT_LABEL} ≠ {want_hold}")
        if len(fails) == n_before:
            print(f"  [OK]   规则版本 JS==PY，定标段/留出段标签与 baseline_stats 年份区间一致"
                  f"（{backtest.DEF_CALIB_LABEL} / {backtest.HOLDOUT_LABEL}）")
    except Exception as exc:  # noqa: BLE001
        fails.append(f"定标年份绑定检查无法执行：{exc}")
    try:
        import baseline_stats  # noqa: E402
        daily = json.loads(FIXTURE.read_text(encoding="utf-8"))["daily"]
        th_now, _, _ = baseline_stats.derive_thresholds(
            backtest.assess([dict(d) for d in baseline_stats.backtest.filter_days(daily)]),
            baseline_stats.et0_30(daily), list(baseline_stats.CALIB_YEARS))
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

    print("\n=== 4) 单调性扫描（逐级升温/增雨/增 ET0，两侧同步且不越级倒挂）===")
    sweep_fails, sweep_points = check_sweeps()
    fails.extend(sweep_fails)

    print("\n=== 4b) 归因文案里的诚实限定词（两侧一致，且不许被悄悄删掉）===")
    wording_fails, wording_ok = check_wording()
    fails.extend(wording_fails)
    if wording_ok != len(WORDING_CASES):
        fails.append(f"限定词用例只有 {wording_ok}/{len(WORDING_CASES)} 条通过")

    if len(ran_labels) != len(WINDOWS):
        fails.append(f"真实窗口应跑 {len(WINDOWS)} 个，实际 {len(ran_labels)} 个")
    if total_days < MIN_REAL_DAYS:
        fails.append(f"逐日对拍天数 {total_days} 低于断言下限 {MIN_REAL_DAYS}，不得宣布等价")
    if len(cases()) < MIN_CASES:
        fails.append(f"边界用例数 {len(cases())} 低于断言下限 {MIN_CASES}（材料中的用例数需同步）")
    if sweep_points < MIN_SWEEP_DAYS:
        fails.append(f"单调性扫描比对点位 {sweep_points} 低于断言下限 {MIN_SWEEP_DAYS}，不得宣布通过")

    print(f"\n合计：真实序列 {total_days} 天逐日对拍（{len(ran_labels)}/{len(WINDOWS)} 窗口）"
          f"｜边界用例 {len(cases())} 组｜单调性扫描 {sweep_points} 个逐日比对点位"
          f"｜限定词用例 {wording_ok}/{len(WORDING_CASES)} 组"
          f"｜物候表 2 份 + 阈值常量 2 组")
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
