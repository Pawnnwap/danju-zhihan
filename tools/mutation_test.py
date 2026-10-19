#!/usr/bin/env python3
"""闸门自证：变异测试（mutation testing）

对拍与回归闸门凭什么可信？因为它们能被证明"抓得住真实回归"。本脚本把本项目修过的每类
分叉重新注入一份临时副本（不动工作树，避免与并行读取材料的评审互相干扰），逐个跑
`parity_check.py` 与 `backtest.py --check`，要求**每个变异都被至少一道闸门拦下**；
有变异溜过即说明闸门存在盲区，本脚本自身以非零码失败。

用法：python tools/mutation_test.py
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
FILES = ["phenology.js", "app.js", "index.html", "README.md",
         "docs/01-项目说明.md", "docs/02-数据与AI说明.md", "docs/03-验证计划.md",
         "docs/04-试点周历与台账.md",
         "tools/backtest.py", "tools/parity_check.py", "tools/mutation_test.py",
         "tools/parity_js.mjs", "tools/smoke_app.mjs", "tools/advice_counts.mjs", "tools/baseline_stats.py",
         "tools/docs_consistency.py", "tools/threshold_sensitivity.py", "tools/make_ppt.py",
         "tools/fetch_fixture.py", "tools/check_all.py", "tools/grid_sensitivity.py",
         "tools/online_drift.py",
         "tools/GATES.json", "pre/同步稿-报名字段与作品帖.md", "丹橘知旱-参赛PPT.pptx",
         "tools/fixtures/liangshuihe_2016_2025.json"]

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
    ("M11 只改 JS 侧膨大期蒸散中线 88.7→80", [
        ("phenology.js", "中: 88.7", "中: 80.0")]),
    ("M12 把外部性动作的 confirm 标记拆掉（分级退回文档）", [
        ("phenology.js", '农技员核对）", confirm: true }', '农技员核对）" }')]),
    ("M13 让熏烟条目去掉 confirm 但仍留原话", [
        ("phenology.js", '追施速效肥恢复树势，用量按当地推荐配方", confirm: true',
         '追施速效肥恢复树势，用量按当地推荐配方", confirm: false')]),
    ("M14 取消蒸散口径的单独出级上限（相对分位又能宣布最高响应）", [
        ("phenology.js", 'const DEF_SOLO_CAP = "中";', 'const DEF_SOLO_CAP = "重";')]),
    ("M15 只在 JS 侧取消该上限（两侧佐证规则漂移）", [
        ("phenology.js", 'const DEF_SOLO_CAP = "中";', 'const DEF_SOLO_CAP = "重";'),
        ("tools/backtest.py", 'DEF_SOLO_CAP = "中"', 'DEF_SOLO_CAP = "重"')]),
    # 评审 A-Q7：闸门自己也是被测对象——拆产品链路、拆闸门强度都必须被抓到
    ("M16 拆掉 app.js 里进入 LLM 门控的待确认清单", [
        ("app.js", "if (a.confirm && !isConfirmed(a.t)) out.push(a.t);",
         "if (false && a.confirm) out.push(a.t);")]),
    ("M17 把对拍的边界用例下限调到 0（闸门空转）", [
        ("tools/parity_check.py", "MIN_CASES = 28", "MIN_CASES = 0")]),
    ("M18 从回归闸门里删掉一条头条断言", [
        ("tools/backtest.py", '"longest_dry_streak": 12, "incomplete_window_days": 0,',
         '"incomplete_window_days": 0,')]),
    ("M19 材料数字不回改就提交（README 对拍天数改旧值）", [
        ("README.md", "442 天", "400 天")]),
    ("M22 把远期预报封顶阈值 8 天改成 16 天（产品级安全装置失效）", [
        ("app.js", "const FAR_LEAD_DAYS = 8;", "const FAR_LEAD_DAYS = 16;")]),
    ("M23 让无灌溉园块重新收到灌溉动作（田间可执行性闸门拆掉）", [
        ("app.js", '(x.irr && irrCond() === "no" ? "无灌溉" :', "(false ? \"无灌溉\" :")]),
    ("M24 让 LLM 事后一致性核对恒返回空（越权数字不再被标注）", [
        ("app.js", "  const problems = [];", "  return [];  const problems = [];")]),
    ("M25 拆掉挂果记录过滤（已采收完的园块重新收到抢收动作）", [
        ("app.js", "(x.fruit && fruitCond() === \"no\" ? \"已采收完\" : null)", "null")]),
    ("M26 取消冻害时效钟的拼接（3–5 天灌水规则重新没有时钟）", [
        ("app.js", "riskId === \"freeze\" && level ?", "false ?")]),
    ("M27 让导出报告正文变成空壳（三出口之一失去内容）", [
        ("app.js", "  return html;", "  return \"\";")]),
    ("M28 取消 irr 显式覆盖（「无水源时拉水浇根」被关键词误判成需灌溉动作而删掉）", [
        ("phenology.js", "irr: it.irr === undefined ? NEEDS_WATER.test(it.t) : !!it.irr",
         "irr: NEEDS_WATER.test(it.t)")]),
    ("M29 挂果情况未记录时仍把抢收这类不可逆动作排进可执行段", [
        ("app.js", 'const fruitAsk = (x) => x.fruit && fruitCond() === "unknown";',
         "const fruitAsk = () => false;")]),
    ("M30 预报取不到时被冻害钟说成「这些天没有降温」（缺数据冒充缺风险）", [
        ("app.js", 'if (r.reason === "noForecast")', "if (false)")]),
    ("M31 把越冬冻害扫描轴的取值改窄（信息量断言与它引用的变量必须真的能失败）", [
        ("tools/parity_check.py", "[-8.0, -7.1, -7.0, -5.1, -5.0, -3.1, -3.0, 0.0]",
         "[-8.0, -8.0, -8.0, -8.0, -8.0, -8.0, -8.0, -8.0]")]),
    ("M32 让「清除本机记录」按钮不删任何东西（材料承诺的撤回方式落空）", [
        ("app.js", "kill.forEach((k) => localStorage.removeItem(k));", ";")]),
    ("M35 把 JS 侧热害归因字段改名（两侧同缺键时 .get() 会让比对退化成空对空）", [
        ("tools/parity_js.mjs", "heatWhy: d.risks.heat ? d.risks.heat.why : null,",
         "heatWhyRenamed: d.risks.heat ? d.risks.heat.why : null,")]),
    ("M36 取消扫描的 warm-up 喂数（回到一半点位在比空值）", [
        ("tools/parity_check.py", "for k in range(-WARMUP, SWEEP_DAYS):", "for k in range(SWEEP_DAYS):")]),
    ("M37 让挂果记录永不过期（陈旧输入继续门控抢收这类不可逆动作）", [
        ("app.js", "const FRUIT_STALE_DAYS = 90;", "const FRUIT_STALE_DAYS = 100000;")]),
    ("M38 只改 Python 侧降水口径阈值（验证 rain_track 单一实现确实在判定链上）", [
        ("tools/backtest.py", "if dry_streak >= 25 or rain30 <= 15:", "if dry_streak >= 26 or rain30 <= 15:")]),
    ("M34 把待确认标签统一成一个（既需当地确认又需记录挂果的动作只剩半句提示）", [
        ("app.js", "const waitTagOf = (x) => (x.waitTags || []).map((t) => (t === \"fruit\" ? FRUIT_ASK_TAG : CONFIRM_TAG)).join(\"\");",
         "const waitTagOf = () => CONFIRM_TAG;")]),
    ("M33 让上下文里的数字不再进入非测量白名单（引用自家依据反被判越权）", [
        ("app.js", '  addStrings({ actions: ctx.actions, risks: ctx.today.risks, pheno: ctx.today.pheno, gate: ctx.gate }, "x", misc);',
         "  addStrings({ actions: [] }, \"x\");")]),
    # 本轮新增：限定词层与越权动作/日期白名单各有一条打在闸门自己的盲区上
    ("M41 取消对未确认动作的越权核对（模型把熏烟写成可执行建议也零命中）", [
        ("app.js", "      if (!(/确认|咨询|农技|主管部门|不适用/.test(bare) && !NEG.test(bare))) {",
         "      if (false) {")]),
    ("M42 让串内日期数字重新混进白名单（1–31 与年份全量放行，小整数用量抓不到）", [
        ("app.js", '      const clean = o.replace(/\\d{4}-\\d{2}-\\d{2}/g, " ");', "      const clean = o;")]),
    ("M43 取消连日同级的去重提示（第 20 天照样发 24–48 小时紧急指令）", [
        ("app.js", 'const timing = timingOf(riskId, level || "中") + (level ? streakTail(riskId, level) : "");',
         'const timing = timingOf(riskId, level || "中");')]),
    ("M44 把 LLM 用量白名单退回单一 misc（时效串的 4/12 与 verify 串的 9 重新变成合法用量数字）", [
        ("app.js", 'const set = measure ? tempRain : (DOSE_UNITS.has(mt[2]) ? adviceNums : misc);',
         'const set = measure ? tempRain : misc;')]),
    ("M21 把单调性扫描天数下限调到 0（扫描可被静默跳过）", [
        ("tools/parity_check.py", "MIN_SWEEP_DAYS = 1200", "MIN_SWEEP_DAYS = 0")]),
    ("M20 页脚/报告的定标段标签与脚本年份区间脱钩", [
        ("phenology.js", 'const DEF_CALIB_LABEL = "2016–2020";', 'const DEF_CALIB_LABEL = "2016–2025";')]),
    ("M40 取消 load() 里的幂等绑定（清除按钮与确认复选框在真机全废，四道闸门原本无感）", [
        ("app.js", "  wireLocalControls(); wireDelegates();", "  void 0;")]),
    ("M39 把 GATES 记录核对整体关掉（自证机制不再约束「全绿记录」）", [
        ("tools/docs_consistency.py", "    gate_problems, n_gate_chk = gates_check(c)",
         "    gate_problems, n_gate_chk = [], 0")]),
]


def run(script: list[str]) -> tuple[int, str]:
    # MUTATION_SANDBOX=1：临时副本没有 .git，docs 闸门里的"记录核对"在沙箱内按跳过处理
    # （它在这一跑的职责是核材料数字，不是核 git 记录；主树的严格模式不受影响，见 docs/02 §2）
    r = subprocess.run([sys.executable, *script], cwd=DST, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900,
                       # 与 check_all.run() 同一条纪律：子闸门接管道时 Python 用 locale（GBK）编码输出，
                       # 父进程按 UTF-8 解码就成了乱码，强度指纹的正则全部匹配不上 → 被判"没声明强度"。
                       # 单独跑 mutation_test.py（不经 check_all）时同样要注入。
                       env={**os.environ, "MUTATION_SANDBOX": "1", "PYTHONIOENCODING": "utf-8"})
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check_parity():
    return run(["tools/parity_check.py"])


def check_headline():
    return run(["tools/backtest.py", "--fixture", "tools/fixtures/liangshuihe_2016_2025.json",
                "--start", "2022-06-01", "--end", "2022-09-30", "--check"])


def check_smoke():
    r = subprocess.run(["node", "tools/smoke_app.mjs"], cwd=DST, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check_docs():
    return run(["tools/docs_consistency.py"])


FP_KEYS = ("对拍天数", "边界用例组数", "用例下限", "实跑天数下限", "单调扫描点位", "单调扫描下限",
           "回归断言条数", "外部性标记条数", "材料检查条数", "记录核对项数")
# 绝对下限（评审 A-Q3）：只比"相对基线下降"时，把绑定与材料一起改弱就能自证通过——
# 基线取自被测树本身，所以必须有一组与树无关的地板值
FP_FLOORS = {"对拍天数": 400, "边界用例组数": 37, "用例下限": 28, "实跑天数下限": 400,
             "单调扫描点位": 1200, "单调扫描下限": 1200, "回归断言条数": 11,
             "外部性标记条数": 15, "材料检查条数": 50,
             # 沙箱副本没有 .git，docs 闸门在沙箱里真实能做的记录核对只有 2 项（全绿标记与输入时间戳），
             # 地板值取这个诚实下限；M39 把整段关掉会掉到 0，由地板值与相对下降双重拦截
             "记录核对项数": 2}


def gates():
    """四道闸门一起跑，返回 (退出码 dict, 输出 dict, 声明强度指纹)。

    指纹解决评审 A-Q7 的盲区：把闸门本身改弱（用例下限调 0、断言条目删掉、材料数字不回改）
    时退出码可能依然是 0，但它"声明自己核了多少"的数字会掉——那就按拦下计数下降处理。
    """
    import re
    pc, po = check_parity()
    hc, ho = check_headline()
    sc, so = check_smoke()
    dc, do = check_docs()
    outs = {"parity": po, "headline": ho, "smoke": so, "docs": do}
    codes = {"对拍": pc, "回归闸门": hc, "烟测": sc, "材料一致性": dc}

    def grab(pat, s):
        m = re.search(pat, s)
        return int(m.group(1)) if m else -1
    par_src = (DST / "tools" / "parity_check.py").read_text(encoding="utf-8")
    fp = {
        "对拍天数": grab(r"真实序列 (\d+) 天", po),
        "边界用例组数": grab(r"边界用例 (\d+) 组", po),
        # 闸门自己声明的"下限"也算强度：只调断言下限、不改实跑条数时，计数不会变（M17 就是这么溜过的）
        "用例下限": grab(r"MIN_CASES = (\d+)", par_src),
        "实跑天数下限": grab(r"MIN_REAL_DAYS = (\d+)", par_src),
        "单调扫描点位": grab(r"单调性扫描 (\d+) 个逐日比对点位", po),
        "单调扫描下限": grab(r"MIN_SWEEP_DAYS = (\d+)", par_src),
        "回归断言条数": len(re.findall(r"[a-z_0-9]+=", ho.split("一致")[-1])) if "一致" in ho else -1,
        "外部性标记条数": grab(r"确认标记：(\d+)/", so),
        "材料检查条数": grab(r"必现 (\d+) 条", do),
        "记录核对项数": grab(r"全绿记录核对 (\d+) 项", do),
    }
    missing = [k for k in FP_KEYS if k not in fp]
    extra = [k for k in fp if k not in FP_KEYS]
    if missing or extra:
        raise RuntimeError(f"强度指纹键集与 FP_KEYS 脱钩（缺 {missing}／多 {extra}）——"
                           "新增或改名的指纹必须同步写进材料与本清单")
    return codes, outs, fp


def fp_drop(base: dict, now: dict) -> list[str]:
    return [f"{k} {base[k]}→{now[k]}" for k in base
            if now.get(k, -1) >= 0 and base[k] >= 0 and now[k] < base[k]]


if hasattr(sys.stdout, "reconfigure"):  # Windows 默认 GBK 控制台降级输出，不崩
    sys.stdout.reconfigure(errors="replace")

def preflight() -> list[str]:
    """跑四道闸门之前先自证变异清单本身是活的：注入点存在、且变异后的 JS 仍能解析。

    教训（评审 A-R8）：注入点失效过去要等整轮 20 多分钟跑到那条才暴露；而把一条动作的
    `confirm: true` 整段换成半截字符串会产生**语法错误**，闸门因解析失败而"拦下"，
    那是假证据——变异必须在语义上真的可被拦。这里两条都当场检查，且只读源文件、不写工作树。
    """
    bad = []
    for name, edits in MUTS:
        for rel, old, new in edits:
            src = SRC / rel
            if not src.exists():
                bad.append(f"{name}：文件不存在 {rel}")
                continue
            text = src.read_text(encoding="utf-8")
            if old not in text:
                bad.append(f"{name}：{rel} 的注入点已失效（找不到逐字原文）")
                continue
            if rel.endswith(".py"):
                try:
                    ast.parse(text.replace(old, new, 1))
                except SyntaxError as exc:
                    bad.append(f"{name}：变异后的 {rel} 语法不通（会以解析失败冒充拦下）：{exc.msg}")
                continue
            if rel.endswith((".js", ".mjs")):
                fd, tp = tempfile.mkstemp(suffix=".js")
                os.close(fd)          # mkstemp 返回的句柄不关掉，Windows 下 node 读不到也删不掉
                tmp = Path(tp)
                try:
                    tmp.write_text(text.replace(old, new, 1), encoding="utf-8")
                    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=120)
                    if r.returncode:
                        bad.append(f"{name}：变异后的 {rel} 无法解析（会以语法错误冒充拦下）："
                                   + (r.stderr or "").strip().splitlines()[-1][:120])
                finally:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
    return bad


def main() -> int:
    global DST
    DST = Path(tempfile.mkdtemp(prefix="danju_mut_"))
    try:
        dead = preflight()
        print(f"预检 {len(MUTS)} 条变异的注入点与变异后语法：")
        if dead:
            for d in dead:
                print("  [X] " + d)
            print("结论：变异清单自身失效，闸门覆盖无从谈起 → 先修清单再跑")
            return 1
        print(f"  [OK] {len(MUTS)} 条全部可注入且变异后 JS 仍可解析")
        for f in FILES:
            d = DST / f
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SRC / f, d)
        TEXT_OK = lambda f: not f.endswith((".json", ".pptx"))
        backups = {f: (DST / f).read_text(encoding="utf-8") for f in FILES if TEXT_OK(f)}

        base_codes, base_outs, base_fp = gates()
        print("基线：" + "｜".join(f"{k} {v}" for k, v in base_codes.items()))
        print("基线声明强度：" + "｜".join(f"{k}={v}" for k, v in base_fp.items()))
        below = [f"{k}={base_fp[k]}<地板 {FP_FLOORS[k]}" for k in base_fp
                 if k in FP_FLOORS and base_fp[k] < FP_FLOORS[k]]
        if any(base_codes.values()) or min(base_fp.values()) < 0 or below:
            print("基线未全绿（或有闸门没声明自己的强度），先修材料再谈变异测试：\n"
                  + "\n".join(base_outs.values())[-900:])
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
            codes, outs, fp = gates()
            for rel in touched:
                (DST / rel).write_text(backups[rel], encoding="utf-8")
            caught_by = [k for k, v in codes.items() if v]
            drops = fp_drop(base_fp, fp) + [f"{k} {fp[k]}<地板 {FP_FLOORS[k]}" for k in fp
                                           if k in FP_FLOORS and fp[k] < FP_FLOORS[k]]
            if drops:
                caught_by.append("声明强度下降(" + ",".join(drops) + ")")
            detail = next((l.strip() for l in outs["parity"].splitlines()
                           if l.strip().startswith("[X]")), "")
            if not detail:
                detail = next((l.strip() for l in outs["docs"].splitlines()
                               if l.strip().startswith("[X]")), "")
            if not detail:
                detail = next((l.strip() for l in outs["smoke"].splitlines()
                               if "漏挂" in l or "过少" in l or "FAIL" in l), "")
            if not detail:
                detail = next((l.strip() for l in outs["headline"].splitlines() if "实算" in l), "")
            print(f"  {'[拦下]  ' if caught_by else '[溜过!]'} {name} → {'+'.join(caught_by) or '无'}"
                  + (f"｜{detail[:70]}" if detail else ""))
            if not caught_by:
                escaped.append(name)

        ran = len(MUTS) - len(unexecuted)
        print(f"\n结论：执行 {ran}/{len(MUTS)}、拦下 {ran - len(escaped)}/{len(MUTS)} 个变异")
        if unexecuted:
            print("  [X] 以下变异未执行（闸门覆盖存疑，不得声称自证通过）：")
            for x in unexecuted:
                print("      " + x)
        if escaped:
            print("  [X] 溜过的变异（真盲区）：" + "、".join(escaped))
        if unexecuted or escaped:
            return 1
        print("  [OK] 全部变异均执行且被拦下——对拍 + 回归 + 烟测 + 材料一致性四道闸门可信")
        return 0
    finally:
        shutil.rmtree(DST, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
