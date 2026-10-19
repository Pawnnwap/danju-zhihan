#!/usr/bin/env python3
"""预报链路与再分析链路的同重叠日偏差：把"页面实际取数≠定标口径"从一句局限变成可复算的数字

动机（评审 A-R12⑥）：def30 的分位线与 rain30 的 15/30/45mm 线全部在 ERA5-Land **再分析**上定标，
而页面把 archive 与 ECMWF **预报**拼成一条序列（app.js mergeSeries，`past_days=6` 回填 + 未来 16 天）。
也就是说"今天屏幕上那条等级"，其 30 天窗口里最后约 6 天来自另一种分布，阈值却是按再分析分位定的。
此前没有任何一行代码统计过两条链路在重叠日的偏差——`online_drift.py` 比的是"今天的 archive
vs 冻结的 archive"，同链路、同变量，问不到这个问题。

本脚本对同一坐标同时取预报（含 past_days）与再分析，在重叠日上比：
1. 逐变量偏差（均值/最大绝对值/超阈日数）；
2. **把重叠日的读数换成预报值后，判级会变几天**（这才是跨分布套用阈值到底伤在哪的量）。

用法：python tools/forecast_vs_archive.py     # 联网 2 次
"""
import argparse
import json
import statistics as st
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

LAT, LON = 32.5835, 111.3764                      # 与冻结件同一个格点
VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum",
        "et0_fao_evapotranspiration"]
KEY = {"temperature_2m_max": "tmax", "temperature_2m_min": "tmin",
       "precipitation_sum": "pr", "et0_fao_evapotranspiration": "et"}
FIXTURE = HERE / "fixtures" / "chain_overlap.json"   # --save 的落点；--verify 离线复算的唯一输入


def verify() -> int:
    """离线复算冻结件：重算逐变量最大偏差与"替换读数后判级变化天数"，与快照逐项断言。
    快照由 --save 生成（含两侧逐日原始读数）；材料引用的 0.1℃/0.0mm/0/7 以这份断言为准（评审 R13②）。"""
    if not FIXTURE.exists():
        print(f"[FAIL] {FIXTURE.name} 不存在：先联网跑一次 python tools/forecast_vs_archive.py --save")
        return 1
    j = json.loads(FIXTURE.read_text(encoding="utf-8"))
    arc, fc = j["archive"], j["forecast"]
    common = sorted(set(arc) & set(fc))
    bad = []
    for v in ("tmax", "tmin", "pr", "et"):
        diffs = [fc[d][v] - arc[d][v] for d in common]
        got = round(max(abs(x) for x in diffs), 2)
        want = j["summary"]["max_abs"][v]
        print(f"  [{'OK' if got == want else 'X '}] {v} 最大绝对偏差: 现算 {got} vs 快照 {want}")
        if got != want:
            bad.append(v)
    def patched(dt):
        row = dict(arc[dt])
        if dt in fc:
            for k in ("tmax", "tmin", "pr", "et"):
                if fc[dt].get(k) is not None:
                    row[k] = fc[dt][k]
        return row
    la = {x["date"]: x for x in backtest.assess([arc[d] for d in sorted(arc)])}
    lb = {x["date"]: x for x in backtest.assess([patched(d) for d in sorted(arc)])}
    for key in ("heat", "drought", "cold", "freeze"):
        ch = sum(1 for d in common
                 if la[d].get(f"{key}_level") != lb[d].get(f"{key}_level"))
        want = j["summary"]["level_changes"][key]
        print(f"  [{'OK' if ch == want else 'X '}] {key} 替换后判级变化: 现算 {ch} vs 快照 {want}")
        if ch != want:
            bad.append(key)
    if bad:
        print(f"[FAIL] {FIXTURE.name} 与现算不符：{bad}——接口或引擎变了，需 --save 重冻结并同步材料")
        return 1
    print(f"[OK] {FIXTURE.name}（拉取于 {j['meta']['fetched_at_utc']}，重叠 {j['meta']['n_overlap']} 天）与离线复算一致；"
          "docs/02 引用的回填段偏差数字以本断言为准")
    return 0
N_OVERLAP = 6                                     # app.js 用 past_days=6 回填
# 预报接口的 past_days 相对"今天"，且不接受 start_date/end_date，所以重叠段只能随运行日走：
# 结果里打印运行日，复算时以同一天的读数为准（评审 A-R12⑥ 要的是有无所测，不是精确到哪一天）
END = date.today()
S0 = (END - timedelta(days=N_OVERLAP + backtest.WARMUP_DAYS + 5)).isoformat()
E0 = END.isoformat()


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def series(kind: str) -> dict:
    daily = ",".join(VARS)
    if kind == "archive":
        url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}"
               f"&start_date={S0}&end_date={E0}&daily={daily}&timezone=Asia%2FShanghai")
    else:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
               f"&daily={daily}&timezone=Asia%2FShanghai"
               f"&past_days={N_OVERLAP}&forecast_days=1")
    j = get(url)
    return {d["date"]: d for d in backtest.filter_days(j["daily"])}


def main() -> int:
    ap = argparse.ArgumentParser(description="回填段两链路偏差：--save 联网冻结原始读数；--verify 离线断言")
    ap.add_argument("--save", action="store_true",
                    help="联网取数并把两侧重叠日读数与摘要冻结进 tools/fixtures/chain_overlap.json")
    ap.add_argument("--verify", action="store_true",
                    help="离线：只用冻结件重算最大偏差与判级变化，与快照逐项断言（不联网）")
    args = ap.parse_args()
    if args.verify:
        return verify()
    arc, fc = series("archive"), series("forecast")
    common = sorted(set(arc) & set(fc))
    print(f"格点 {LAT},{LON}｜再分析 {len(arc)} 天 vs 预报（含 past_days={N_OVERLAP}）{len(fc)} 天"
          f"｜重叠 {len(common)} 天：{common[0]}~{common[-1]}")
    if len(common) < N_OVERLAP:
        print("[FAIL] 重叠日不足，接口口径变了，本脚本需同步")
        return 1

    print("\n1) 重叠日逐变量偏差（预报 − 再分析）")
    for v in VARS:
        diffs = [fc[d][KEY[v]] - arc[d][KEY[v]] for d in common
                 if fc[d].get(KEY[v]) is not None and arc[d].get(KEY[v]) is not None]
        if not diffs:
            continue
        print(f"  {v:34s} 均值 {st.fmean(diffs):+6.2f}｜最大绝对 {max(abs(x) for x in diffs):5.2f}"
              f"｜|Δ|>0.5 有 {sum(1 for x in diffs if abs(x) > 0.5)}/{len(diffs)} 天"
              f"｜|Δ|>1.0 有 {sum(1 for x in diffs if abs(x) > 1.0)}/{len(diffs)} 天")

    # 2) 跨分布套用阈值伤在哪：把重叠日读数换成预报值，重算判级，数等级变了几天
    #    先原样打出两侧读数，避免"偏差≈0"其实是两个接口返回了同一份数（读者可自行核验）
    print("\n0) 重叠日原始读数（再分析 | 预报）")
    for d in common:
        print(f"  {d}  tmax {arc[d]['tmax']:5.1f} | {fc[d]['tmax']:5.1f}"
              f"  tmin {arc[d]['tmin']:5.1f} | {fc[d]['tmin']:5.1f}"
              f"  pr {arc[d]['pr']:5.1f} | {fc[d]['pr']:5.1f}"
              f"  et {arc[d]['et']:5.1f} | {fc[d]['et']:5.1f}")

    def patched(dt: str) -> dict:
        row = dict(arc[dt])
        if dt in fc:
            for v in VARS:
                if fc[dt].get(KEY[v]) is not None:
                    row[KEY[v]] = fc[dt][KEY[v]]
        return row

    merged = [patched(d) for d in sorted(arc)]
    # 两条序列已经过 filter_days 规整，这里不再重复过滤（重复过滤会把行列表当成 API 原始字典）
    la = {x["date"]: x for x in backtest.assess([arc[d] for d in sorted(arc)])}
    lb = {x["date"]: x for x in backtest.assess(merged)}
    print(f"\n2) 用预报值替换重叠日读数后，判级变化（窗口 {S0}~{E0}，含 {backtest.WARMUP_DAYS} 天 warm-up）")
    for key in ("heat", "drought", "cold", "freeze"):
        ch = [(d, la[d].get(f"{key}_level"), lb[d].get(f"{key}_level")) for d in common
              if la[d].get(f"{key}_level") != lb[d].get(f"{key}_level")]
        print(f"  {key:8s} 重叠 {len(common)} 天里有 {len(ch)} 天等级不同"
              + ("：" + "、".join(f"{d} {a or '无'}→{b or '无'}" for d, a, b in ch) if ch else ""))
    if args.save:
        def _maxabs(v):
            return round(max(abs(fc[d][v] - arc[d][v]) for d in common), 2)

        def _lvl(key):
            return sum(1 for d in common if la[d].get(f"{key}_level") != lb[d].get(f"{key}_level"))

        snap = {
            "meta": {"fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "lat": LAT, "lon": LON, "n_overlap": len(common),
                     "first": common[0], "last": common[-1],
                     "note": "archive/forecast 均为 filter_days 规整后的逐日行；--verify 只用本文件离线复算"},
            "archive": {d: arc[d] for d in common},
            "forecast": {d: fc[d] for d in common},
            "summary": {"max_abs": {v: _maxabs(v) for v in ("tmax", "tmin", "pr", "et")},
                        "level_changes": {k: _lvl(k) for k in ("heat", "drought", "cold", "freeze")}},
        }
        FIXTURE.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        print(f"  [冻结] 两侧重叠日原始读数与摘要已写入 {FIXTURE.name}——此后 python tools/forecast_vs_archive.py --verify 离线断言")
    print("\n读法：定标只发生在再分析侧，所以这两条链路的差异**不是噪声而是口径**——"
          "页面把重叠日当再分析用（mergeSeries 不做分布订正），上表就是这一步的代价。"
          "试点核对项里必须包含「预报日与实际到达该日时的再分析值是否一致」，否则"
          "对外只该说『预报段为参考提示』。（本脚本不写进闸门：接口一变动就该重跑，且结论随日期更新。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
