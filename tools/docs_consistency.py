#!/usr/bin/env python3
"""材料一致性闸门：关键数字与口径不再靠人记

背景（评审 A-Q8 的工单，也是我上一轮被当面抓到的问题）：一次规则改动要同步 README、docs/01–03
与 PPT 生成脚本四处载体，靠人记必然漏。本脚本把"从冻结序列现算出来的值"与"材料里写的字符串"
逐条对齐，并禁止一批已被否决的旧措辞回潮；任一不符即非零退出，提交前必须跑绿。

用法：python tools/docs_consistency.py
"""
import json
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import backtest  # noqa: E402
import mutation_test  # noqa: E402
import parity_check  # noqa: E402

FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
FILES = {
    "README": ROOT / "README.md",
    "docs/01": ROOT / "docs" / "01-项目说明.md",
    "docs/02": ROOT / "docs" / "02-数据与AI说明.md",
    "docs/03": ROOT / "docs" / "03-验证计划.md",
    "PPT脚本": HERE / "make_ppt.py",
}
# 匹配前统一剔除引号类字符，避免 "" 『』 等写法差异造成假失败
QUOTES = "“”\"『』’'“"


def norm(s: str) -> str:
    return "".join(ch for ch in s if ch not in QUOTES)


def canonical() -> dict:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    daily = fx["daily"]
    s0, e0 = "2022-06-01", "2022-09-30"
    w0 = (date.fromisoformat(s0) - timedelta(days=backtest.WARMUP_DAYS)).isoformat()
    keep = [i for i, t in enumerate(daily["time"]) if w0 <= t <= e0]
    sl = {k: [v[i] for i in keep] for k, v in daily.items()}
    assessed = backtest.windowed(backtest.assess(backtest.filter_days(sl)), s0, e0)
    hd = backtest.summarize(assessed)

    all_days = backtest.assess([dict(d) for d in backtest.filter_days(daily)])
    years = sorted({date.fromisoformat(d["date"]).year for d in all_days})
    burden = {}
    for lv in ("重", "中", "轻"):
        per = sorted(sum(1 for d in all_days if d["drought_level"] == lv
                         and date.fromisoformat(d["date"]).year == y) for y in years)
        # 与 baseline_stats 同口径：statistics.median 后按 .0f 呈现，避免两个工具给出不同中位数
        burden[lv] = (int(float(f"{statistics.median(per):.0f}")), per[0], per[-1])
    return {
        "h_sev": hd["heat_severe"], "lead": hd["lead_days"],
        "d_sev": hd["drought_severe"], "d_mid": hd["drought_mid"], "d_first": hd["drought_first"],
        "b_sev": burden["重"][0], "b_sev_rng": f"{burden['重'][1]}–{burden['重'][2]}",
        "b_mid": burden["中"][0], "b_light": burden["轻"][0],
        "n_cases": len(parity_check.cases()), "n_mut": len(mutation_test.MUTS),
    }


# (文件, 必须出现的规范化子串, 说明)
MUST = [
    ("README", "重{d_sev} 天／中{d_mid} 天", "README 干旱双口径数字"),
    ("README", "提前 {lead} 天", "README 热害时效"),
    ("README", "442 天", "README 对拍天数"),
    ("README", "{n_cases} 组阈值边界合成用例", "README 边界用例数"),
    ("docs/01", "p75/p90/p99", "docs/01 蒸散口径定标说明"),
    ("docs/02", "重{d_sev} 天、中{d_mid} 天", "docs/02 回测干旱数字"),
    ("docs/02", "首个提示 {d_first}", "docs/02 干旱首日"),
    ("docs/02", "| 干旱（双口径取高者） | {b_sev}（{b_sev_rng}）", "docs/02 干旱负担中位"),
    ("docs/02", "89.8%", "docs/02 休眠期常驻率"),
    ("docs/02", "{n_cases} 组边界合成用例", "docs/02 用例数"),
    ("docs/02", "执行 15/15、拦下 15/15", "docs/02 变异自证结论"),
    ("docs/03", "重{d_sev} 天／中{d_mid} 天", "docs/03 已完成验证的干旱数字"),
    ("docs/03", "442 天逐日比对", "docs/03 对拍天数"),
    ("docs/03", "{n_cases} 组阈值边界合成用例", "docs/03 用例数"),
    ("docs/03", "轻 69／中 43／重 10", "docs/03 提示负担三个口径不得混用的基线数"),
    ("PPT脚本", "重{d_sev} 天／中{d_mid} 天", "PPT 干旱数字"),
    ("PPT脚本", "{n_cases} 组边界", "PPT 用例数"),
]

# 已被否决、不得回潮的措辞（任何材料里出现即失败）
BANNED = [
    "中」3 天", "重」0 天", "中3 天", "重0 天", "23 组", "10/10", "第 7 节",
    "首发版不上", "不在首发版", "换物候表即可支持", "数百元级/园块", "投保/理赔辅助凭证",
    "一律不出干旱等级", "休眠期不出干旱等级", "8（1–17）", "50（28–69）",
    "17（5–43）", "36（27–72）", "68（55–109）", "轻 68", "约 50 天/年",
    "同一套规则有 Python 独立实现交叉验证", "10 个变异",
]


def main() -> int:
    c = canonical()
    texts = {k: norm(p.read_text(encoding="utf-8")) for k, p in FILES.items()}
    print("现算权威值：干旱 重{d_sev}/中{d_mid}（首日 {d_first}）、热害 重{h_sev} 提前{lead} 天、"
          "干旱负担中位 重{b_sev}（{b_sev_rng}）、边界用例 {n_cases} 组、变异 {n_mut} 个".format(**c))
    problems = []

    for fname, pat, desc in MUST:
        needle = norm(pat).format(**c)
        if needle not in texts[fname]:
            problems.append(f"[缺] {fname}：{desc} → 材料中找不到「{needle}」")
    for fname, text in texts.items():
        for bad in BANNED:
            b = norm(bad)
            if b and b in text:
                problems.append(f"[禁] {fname}：出现已否决措辞「{bad}」")

    for k in ("heat_severe", "drought_severe", "drought_mid", "drought_first", "lead_days"):
        want = {"heat_severe": c["h_sev"], "drought_severe": c["d_sev"], "drought_mid": c["d_mid"],
                "drought_first": c["d_first"], "lead_days": c["lead"]}[k]
        if backtest.HEADLINE[k] != want:
            problems.append(f"[断言漂移] backtest.HEADLINE.{k}={backtest.HEADLINE[k]} ≠ 现算 {want}")

    print(f"\n检查项：必现 {len(MUST)} 条｜禁现 {len(BANNED)} 条｜回归断言核对 5 条")
    if problems:
        print(f"发现 {len(problems)} 处不一致：")
        for p in problems:
            print("  [X] " + p)
        print("\n结论：材料数字/口径与现算值不同步 → 禁止提交")
        return 1
    print("结论：五份材料的关键数字与口径均与现算值一致，且无被否决措辞回潮 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
