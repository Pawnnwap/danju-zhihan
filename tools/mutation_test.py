#!/usr/bin/env python3
"""闸门自证：变异测试（mutation testing）

对拍与回归闸门凭什么可信？因为它们能被证明"抓得住真实回归"。本脚本把本项目修过的每类
分叉重新注入一份临时副本（不动工作树，避免与并行读取材料的评审互相干扰），逐个跑
`parity_check.py` 与 `backtest.py --check`，要求**每个变异都被至少一道闸门拦下**；
有变异溜过即说明闸门存在盲区，本脚本自身以非零码失败。

用法：python tools/mutation_test.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
FILES = ["phenology.js", "app.js", "index.html", "tools/backtest.py", "tools/parity_check.py",
         "tools/parity_js.mjs", "tools/smoke_app.mjs", "tools/baseline_stats.py",
         "tools/fetch_fixture.py", "tools/fixtures/liangshuihe_2016_2025.json"]

# 每项 = (说明, [(相对文件, 原文, 变异后)])；期望全部被抓到
MUTS = [
    ("M1 只改 JS 侧干旱阈值 45→46", [("phenology.js", "rain30 <= 45", "rain30 <= 46")]),
    ("M2 去掉 JS 降水口径的休眠期沉默", [("phenology.js",
        'if (ctx.pheno.id !== "dormant") {', "if (true) {")]),
    ("M3 只改 JS 热害 37→37.5", [("phenology.js", "t >= 37", "t >= 37.5")]),
    ("M4 JS 物候表退回 to=[2,28]（重现闰日洞）", [("phenology.js", "to: [2, 29]", "to: [2, 28]")]),
    ("M5 JS 幼果期低温 5→4（重现跨期跳两档）", [("phenology.js",
        'if (t <= 5) return { level: "轻", why: `幼果期最低温',
        'if (t <= 4) return { level: "轻", why: `幼果期最低温')]),
    ("M6 JS 恢复『比较前先四舍五入』（原分叉 bug）", [("phenology.js",
        "const dry = ctx.dryStreak, rain30 = ctx.rain30;",
        "const dry = ctx.dryStreak, rain30 = Math.round(ctx.rain30 * 10) / 10;")]),
    ("M7 JS 去掉『窗口不满30天不判级』（原偏置）", [("phenology.js",
        "if (ctx.dayIndex < 29 || ctx.windowDays < 30 || ctx.windowBad > 0) return null;",
        "if (ctx.dayIndex < 29) return null;")]),
    ("M8 只改 Python 侧阈值 15→16", [("tools/backtest.py", "rain30 <= 15", "rain30 <= 16")]),
    ("M9 去掉 Python 侧休眠期降水沉默", [("tools/backtest.py",
        'if pid != "dormant":', "if True:")]),
    ("M10 两侧同步把热害重级阈值 39→39.5", [
        ("phenology.js", "t >= 39", "t >= 39.5"),
        ("tools/backtest.py", "tmax >= 39", "tmax >= 39.5")]),
    ("M11 只改 JS 侧膨大期蒸散中线 87.4→80", [
        ("phenology.js", "中: 87.4", "中: 80.0")]),
    ("M12 把外部性动作的 confirm 标记拆掉（分级退回文档）", [
        ("phenology.js",
         '{ t: "低温来临前傍晚园内熏烟（烟雾防霜）", confirm: true }',
         '"低温来临前傍晚园内熏烟（烟雾防霜）"')]),
    ("M13 让熏烟条目去掉 confirm 但仍留原话", [
        ("phenology.js", '追施速效肥恢复树势，用量按当地推荐配方", confirm: true',
         '追施速效肥恢复树势，用量按当地推荐配方", confirm: false')]),
    ("M14 取消开花期蒸散封顶（把最不稳的一档放回最高级）", [
        ("phenology.js", 'const DEF_MAX_LEVEL = { bloom: "中" };', "const DEF_MAX_LEVEL = {};")]),
    ("M15 只在 JS 侧取消开花期封顶（两侧封顶规则漂移）", [
        ("phenology.js", 'const DEF_MAX_LEVEL = { bloom: "中" };', "const DEF_MAX_LEVEL = {};"),
        ("tools/backtest.py", 'DEF_MAX_LEVEL = {"bloom": "中"}', "DEF_MAX_LEVEL = {}")]),
]


def run(script: list[str]) -> tuple[int, str]:
    r = subprocess.run([sys.executable, *script], cwd=DST, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check_parity():
    return run(["tools/parity_check.py"])


def check_headline():
    return run(["tools/backtest.py", "--fixture", "tools/fixtures/liangshuihe_2016_2025.json",
                "--start", "2022-06-01", "--end", "2022-09-30", "--check"])


def check_smoke():
    r = subprocess.run(["node", "tools/smoke_app.mjs"], cwd=DST, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


if hasattr(sys.stdout, "reconfigure"):  # Windows 默认 GBK 控制台降级输出，不崩
    sys.stdout.reconfigure(errors="replace")

def main() -> int:
    global DST
    DST = Path(tempfile.mkdtemp(prefix="danju_mut_"))
    try:
        for f in FILES:
            d = DST / f
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SRC / f, d)
        backups = {f: (DST / f).read_text(encoding="utf-8") for f in FILES if not f.endswith(".json")}

        pc, po = check_parity()
        hc, ho = check_headline()
        sc, so = check_smoke()
        print(f"基线：对拍 {pc}｜回归 {hc}｜烟测 {sc}")
        if pc or hc or sc:
            print("基线未全绿，先修材料再谈变异测试：\n" + (po or ho or so)[-900:])
            return 1

        print("\n变异打靶（每个变异都必须**真的执行**并被至少一道闸门拦下，否则算盲区）")
        escaped, unexecuted = [], []
        for name, edits in MUTS:
            touched = []
            failed_anchor = None
            for rel, old, new in edits:
                p = DST / rel
                t = p.read_text(encoding="utf-8")
                if old not in t:
                    failed_anchor = rel
                    break
                p.write_text(t.replace(old, new, 1), encoding="utf-8")
                touched.append(rel)
            if failed_anchor:
                for rel in touched:
                    (DST / rel).write_text(backups[rel], encoding="utf-8")
                # 注入点找不到原文＝该变异根本没跑，绝不能计入"已拦下"（评审 A-Q2）
                unexecuted.append(f"{name}（{failed_anchor} 的注入点已失效）")
                print(f"  [未执行] {name}：{failed_anchor} 注入点失效")
                continue
            pc, po = check_parity()
            hc, ho = check_headline()
            sc, so = check_smoke()
            for rel in touched:
                (DST / rel).write_text(backups[rel], encoding="utf-8")
            caught_by = []
            if pc:
                caught_by.append("对拍")
            if hc:
                caught_by.append("回归闸门")
            if sc:
                caught_by.append("烟测")
            detail = next((l.strip() for l in po.splitlines() if l.strip().startswith("[X]")), "")
            if not detail:
                detail = next((l.strip() for l in so.splitlines() if "漏挂" in l or "过少" in l), "")
            if not detail:
                detail = next((l.strip() for l in ho.splitlines() if "实算" in l), "")
            print(f"  {'[拦下]  ' if caught_by else '[溜过!]'} {name} → {'+'.join(caught_by) or '无'}"
                  + (f"｜{detail[:70]}" if detail else ""))
            if not caught_by:
                escaped.append(name)

        ran = len(MUTS) - len(unexecuted)
        print(f"\n结论：执行 {ran}/{len(MUTS)} 个变异，被拦下 {ran - len(escaped)} 个")
        if unexecuted:
            print("  [X] 以下变异未执行（闸门覆盖存疑，不得声称自证通过）：")
            for x in unexecuted:
                print("      " + x)
        if escaped:
            print("  [X] 溜过的变异（真盲区）：" + "、".join(escaped))
        if unexecuted or escaped:
            return 1
        print("  [OK] 全部变异均执行且被拦下——对拍 + 回归 + 烟测三道闸门可信")
        return 0
    finally:
        shutil.rmtree(DST, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
