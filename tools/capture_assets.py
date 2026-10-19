#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用本机 Edge（Playwright channel=msedge）抓演示片要用的真机截图。

产出到 docs/frames/v2/：
  ui_site / ui_now / ui_calendar / ui_advice / ui_replay / ui_qa  —— 各区块 2x 截图
  mobile_top / mobile_now                                        —— 手机视口 3x 截图
  ui_full                                                        —— 整页参考

前置：pip install playwright && 本机装有 Microsoft Edge。
线上站点会实时向 Open-Meteo 取数，所以「当前」态的数据日期＝抓取当天；
本片字幕与画外音只引用不随日期变化的量（2022 回放、规则与验证条数）。
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "frames" / "v2"
URL = __import__("os").environ.get("CAPTURE_URL", "https://pawnnwap.github.io/danju-zhihan/")
UA = {"width": 1600, "height": 1000}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport=UA, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle", timeout=90000)
        pg.wait_for_selector("#risk-now .risk-item", timeout=60000)
        pg.wait_for_timeout(1500)

        def shot(sel, name):
            el = pg.query_selector(sel)
            el.scroll_into_view_if_needed()
            pg.wait_for_timeout(350)
            el.screenshot(path=str(OUT / (name + ".png")))
            print("  %-12s %s" % (name, el.bounding_box()))

        for sel, name in (("#site-card", "ui_site"), ("#now-card", "ui_now"),
                          ("#timeline-card", "ui_calendar"), ("#advice-card", "ui_advice")):
            shot(sel, name)

        # 2022 年夏季热害回放（片中的 30 天 / 13 天 / 40.9℃ / 提前 3 天 全部来自这里）
        pg.click('[data-replay="2022-heat"]')
        pg.wait_for_timeout(2500)
        shot("#replay-card", "ui_replay")

        # 问答：走内置规则引擎，不发送任何数据，也不需要 Key
        pg.fill("#q", "采摘前要注意什么？")
        pg.click("#ask")
        pg.wait_for_timeout(2500)
        shot("#qa-card", "ui_qa")
        print("  QA:", pg.inner_text("#a")[:120].replace("\n", " | "))

        pg.screenshot(path=str(OUT / "ui_full.png"), full_page=True)
        ctx.close()

        m = browser.new_context(viewport={"width": 414, "height": 896}, device_scale_factor=3,
                                is_mobile=True, has_touch=True)
        mp = m.new_page()
        mp.goto(URL, wait_until="networkidle", timeout=90000)
        mp.wait_for_selector("#risk-now .risk-item", timeout=60000)
        mp.wait_for_timeout(2000)
        mp.screenshot(path=str(OUT / "mobile_top.png"))
        mp.evaluate("document.querySelector('#now-card').scrollIntoView({block:'start'})")
        mp.wait_for_timeout(600)
        mp.screenshot(path=str(OUT / "mobile_now.png"))
        browser.close()
    print("已写入 %s" % OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
