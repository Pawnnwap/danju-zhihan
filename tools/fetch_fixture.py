#!/usr/bin/env python3
"""抓取并冻结参考园块的逐日序列，供离线复算与对拍使用。

评审复算本项目头条数字原本必须当场联网调 Open-Meteo：格点若漂移或 API 改版，
就无法区分差异来自引擎还是数据。本脚本把 10 年输入固化进仓库
（tools/fixtures/liangshuihe_2016_2025.json，含实际命中格点坐标与拉取时间），
使 backtest / parity_check / baseline_stats 三者都能离线跑。

用法：python tools/fetch_fixture.py [--out tools/fixtures/liangshuihe_2016_2025.json]
"""
import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAT, LON = 32.60, 111.42
START, END = "2016-01-01", "2025-12-31"
VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration"
URL = (f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}"
       f"&start_date={START}&end_date={END}&daily={VARS}&timezone=Asia%2FShanghai")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "fixtures" / "liangshuihe_2016_2025.json")
    args = ap.parse_args()
    with urllib.request.urlopen(URL, timeout=60) as r:
        j = json.load(r)
    d = j["daily"]
    out = {
        "meta": {
            "requested_lat": LAT, "requested_lon": LON,
            "gridpoint_lat": round(j["latitude"], 4), "gridpoint_lon": round(j["longitude"], 4),
            "elevation_m": j.get("elevation"),
            "model": "ERA5-Land 再分析（Open-Meteo Archive API，约 9km 格点）",
            "start": START, "end": END, "timezone": "Asia/Shanghai",
            "variables": VARS.split(","),
            "source_url": URL,
            "pulled_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "null 值原样保留（引擎按'三变量全非空'过滤该日）",
        },
        "daily": {k: d[k] for k in ["time", *VARS.split(",")]},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = len(d["time"])
    nulls = {k: sum(1 for v in d[k] if v is None) for k in VARS.split(",")}
    print(f"写入 {args.out.name}：{n} 天，缺测 {nulls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
