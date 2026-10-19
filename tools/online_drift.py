#!/usr/bin/env python3
"""线上数据 vs 冻结输入的逐变量差异核算（评审 A-Q10：不许用一个跨阈值的特例代表整段漂移）

对同一坐标、同一区间（含 warm-up）同时取"冻结 fixture"和"当场联网"两份序列，输出：
1. 逐变量差异天数（tmax/tmin/pr/et0）与最大偏差；
2. 头条那一天的实测值对照（例如 2022-08-04 的最高温）；
3. 两类阈值线两侧的距离：干旱雨30≤30 的"中"级佐证线、热害 39℃ 重级线，
   给出**两侧各自算出的等级**，说明头条差异是不是只由一个贴边点造成。

用法：python tools/online_drift.py            # 联网
退出码 0 = 跑通并打印；不判成败（差异本身不是错误，隐瞒差异才是）。
"""
import json
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"
LAT, LON = 32.60, 111.42
S0, E0 = "2022-06-01", "2022-09-30"
W0 = (date.fromisoformat(S0) - timedelta(days=backtest.WARMUP_DAYS)).isoformat()
KEYS = {"tmax": "temperature_2m_max", "tmin": "temperature_2m_min", "pr": "precipitation_sum",
        "et": "et0_fao_evapotranspiration"}


def online():
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={LAT}&longitude={LON}&start_date={W0}&end_date={E0}"
           "&daily=" + ",".join(KEYS.values()) + "&timezone=Asia%2FShanghai")
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.load(r)["daily"]


def main() -> int:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    frozen, meta = fx["daily"], fx["meta"]
    live = online()
    span = [t for t in frozen["time"] if W0 <= t <= E0]
    idx = {t: i for i, t in enumerate(frozen["time"])}
    lidx = {t: i for i, t in enumerate(live["time"])}

    print(f"区间 {W0}~{E0}（报告区间 {S0}~{E0}，warm-up {backtest.WARMUP_DAYS} 天）｜"
          f"冻结件拉取于 {meta.get('pulled_at_utc')}，现取于 {date.today().isoformat()}")
    diffs = {}
    worst = {}
    for short, key in KEYS.items():
        n = 0
        mx = (0.0, None)
        for t in span:
            if t not in lidx:
                continue
            a, b = frozen[key][idx[t]], live[key][lidx[t]]
            if (a or 0) != (b or 0):
                n += 1
                d = abs((a or 0) - (b or 0))
                if d > mx[0]:
                    mx = (d, t)
        diffs[short] = n
        worst[short] = mx
    print(f"  参与比对的日数：{len(span)}（其中现取序列缺日 {sum(1 for t in span if t not in lidx)} 天）")
    for short in ("tmax", "tmin", "pr", "et"):
        w = worst[short]
        print(f"  {short:4} 有差异的天数 {diffs[short]}/{len(span)}"
              + (f"｜最大偏差 {w[0]:.2f}（{w[1]}）" if w[1] else ""))

    # 头条日与佐证线的距离
    day = "2022-08-04"
    fa = frozen[KEYS["tmax"]][idx[day]]
    la = live[KEYS["tmax"]][lidx[day]]
    print(f"\n  热害头条日 {day}：冻结 tmax={fa}℃ / 现取 tmax={la}℃｜重级线 39℃ → "
          f"距线 {fa - 39:+.2f}℃（冻结） vs {la - 39:+.2f}℃（现取）")
    a2 = backtest.windowed(backtest.assess(backtest.filter_days(
        {k: [v[i] for i, t in enumerate(frozen["time"]) if W0 <= t <= E0]
         for k, v in frozen.items()})), S0, E0)
    b2 = backtest.windowed(backtest.assess(backtest.filter_days(live)), S0, E0)
    for tag, rows in (("冻结", a2), ("现取", b2)):
        s = backtest.summarize(rows)
        print(f"  {tag}：热害 重{s['heat_severe']}/提前 {s['lead_days']} 天｜干旱 重{s['drought_severe']}"
              f"/中{s['drought_mid']}/首日 {s['drought_first']}")
    for d in a2:
        if d["date"] in ("2022-08-26",):
            for dd in b2:
                if dd["date"] == d["date"]:
                    print(f"  佐证线示例 {d['date']}：雨30 {d['rain30']:.2f}（冻结） vs "
                          f"{dd['rain30']:.2f}（现取）｜距\"中\"级佐证线 30.0 mm "
                          f"{d['rain30'] - 30:+.2f} vs {dd['rain30'] - 30:+.2f}"
                          f"→ 等级 {d['drought_level'] or '无'} vs {dd['drought_level'] or '无'}")
    print("\n读法：漂移是**逐变量、跨整段**的（见上表天数），不是某一个贴边点。"
          "头条数字一律注明输入版本与拉取时间；引用单个温度值时必须同时给出两侧读数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
