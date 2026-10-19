#!/usr/bin/env python3
"""阈值贴边敏感性：把"结论离阈值有多远"变成可复算的数（评审 C-Q5/C-Q10）

头条最出风的两个数字都贴着线：首个"重"级由单日 ≥39℃ 触发（2022-08-04 冻结 39.2℃、现取 39.3℃），
唯一的干旱"重"级日压在雨30≤30.0mm 的佐证线（29.90/30.20）。所以必须回答：
- 若格点系统性偏冷 1/2/4℃（与地面站常见的偏差量级同阶），热害"重"级日数与"提前 3 天"还剩几天？
- 若 30 天累计降水整体偏高 0.5/1/2 mm（再分析修订就是这个量级），干旱"重/中"级日数怎么变？

用法：python tools/threshold_sensitivity.py
输出即结论：不需要联网（读仓库内冻结 fixture）。
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
S0, E0 = "2022-06-01", "2022-09-30"
W0 = (date.fromisoformat(S0) - timedelta(days=backtest.WARMUP_DAYS)).isoformat()


def slice_days(daily, lo, hi):
    keep = [i for i, t in enumerate(daily["time"]) if lo <= t <= hi]
    return {k: [v[i] for i in keep] for k, v in daily.items()}


def run(daily, t_off=0.0, p_off=0.0):
    d = {k: list(v) for k, v in daily.items()}
    if t_off:
        for k in ("temperature_2m_max", "temperature_2m_min"):
            d[k] = [None if x is None else round(x + t_off, 2) for x in d[k]]
    if p_off:
        d["precipitation_sum"] = [None if x is None else round(max(0.0, x + p_off), 2)
                                  for x in d["precipitation_sum"]]
    days = backtest.windowed(backtest.assess(backtest.filter_days(d)), S0, E0)
    return backtest.summarize(days)


def main() -> int:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    daily = slice_days(fx["daily"], W0, E0)
    print(f"区间 {S0}~{E0}（warm-up 自 {W0}）｜输入：冻结 fixture，拉取于 {fx['meta'].get('pulled_at_utc')}")

    print("\n=== 1) 温度系统性偏移下的热害头条 ===")
    print(f"{'偏移':>8} {'≥35℃ 日数':>10} {'重级日数':>9} {'首个重级':>12} {'提前天数':>9}")
    for off in (0.0, -1.0, -2.0, -4.0):
        s = run(daily, t_off=off)
        print(f"{off:+7.1f}℃ {s['heat_days_35']:>10} {s['heat_severe']:>9} "
              f"{(s['first_severe'] or '无'):>12} {str(s['lead_days'] if s['lead_days'] is not None else '无'):>9}")

    print("\n=== 2) 降水整体上移下的干旱头条（佐证线 雨30≤30.0mm、重级线 15.0mm）===")
    print(f"{'偏移':>8} {'重级日数':>9} {'中级日数':>9} {'首个轻级以上':>14}")
    for off in (0.0, 0.5, 1.0, 2.0):
        s = run(daily, p_off=off)
        print(f"{off:+7.1f}mm {s['drought_severe']:>9} {s['drought_mid']:>9} "
              f"{(s['drought_first'] or '无'):>14}")

    print("\n=== 3) 贴边距离（现算）===")
    days = backtest.windowed(backtest.assess(backtest.filter_days(
        {k: list(v) for k, v in daily.items()})), S0, E0)
    first = next((d for d in days if d["heat_level"] == "重"), None)
    if first:
        print(f"  首个热害重级 {first['date']}：tmax {first['tmax']}℃，距 39℃ 线 "
              f"{first['tmax'] - 39:+.1f}℃；若格点偏冷 ≥0.3℃，该日改由连续日数升级或不判重")
    for d in days:
        if d["date"] == "2022-08-26":
            print(f"  唯一干旱重级日 {d['date']}：雨30 {d['rain30']:.2f}mm，距佐证线 "
                  f"{d['rain30'] - 30.0:+.2f}mm；def30 {d.get('et30', 0) - d['rain30']:.1f}mm")
    print("\n读法：偏移量取的是再分析/站点常见偏差的同阶数值，不是最坏假设。"
          "凡结论对 ≤0.5 单位的偏移敏感，材料里就必须同时给两侧读数（见 docs/02 §5 漂移披露）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
