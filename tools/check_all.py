#!/usr/bin/env python3
"""一键跑全部闸门，并把"哪一次提交在什么输入上全绿"写进 tools/GATES.json

评审 A-Q5 的要件：不能只说"闸门存在"，要能证明**同一份代码 + 同一份冻结输入**上所有闸门
同时为绿。本脚本按固定顺序跑：
  1. tools/parity_check.py      —— JS↔Python 逐日等价（含归因文案、常量三方绑定、边界用例）
  2. tools/backtest.py --check  —— 头条数字回归（2022 区间，样本外）
  3. tools/baseline_stats.py    —— 十年基线/留出/不变量（同时是阈值的唯一生成处）
  4. tools/smoke_app.mjs        —— 前端 load() 全流程 + fail-closed 链路
  5. tools/mutation_test.py     —— 闸门自证（变异必须真的执行并被拦下）
  6. tools/docs_consistency.py  —— 七份载体的数字与口径同步
全部通过才写 GATES.json 并退出 0；任一失败退出 1，且**不覆盖**上一次的成功记录（防止
"改坏了但 GATES.json 还写着绿"）。

用法：python tools/check_all.py          # 全绿才写 GATES.json
      python tools/check_all.py --verify # 只校验当前代码是否绿，不写文件（pre-commit 用）

自指问题说清楚：GATES.json 记录的 commit 是**被验证的那棵树**；把这条记录提交进去会产生下一个
"记录性提交"，它不含代码/材料变更，因此不需要再跑一轮验证。若某次提交改的是代码或材料，
GATES.json 里的 SHA 与它就必然不同——读的人应当以"该 SHA 之后紧跟的记录性提交没有代码差异"为准。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# 重定向/管道下 Python 用 locale 编码（中文 Windows 是 GBK），闸门输出里任何一个替换字符都会让
# print 抛 UnicodeEncodeError——而 pre-commit 与 CI 恰恰是把输出接到管道里的。闸门自己不该成为
# 失败原因：这里强制 UTF-8 并把不可编字符降级为替换，保证"退出码"始终来自被测闸门而不是终端编码。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GATES_FILE = HERE / "GATES.json"
FIXTURE = HERE / "fixtures" / "liangshuihe_2016_2025.json"

GATES = [
    ("parity", ["tools/parity_check.py"], "真实序列 (\\d+) 天", "边界用例 (\\d+) 组"),
    ("headline", ["tools/backtest.py", "--fixture", "tools/fixtures/liangshuihe_2016_2025.json",
                  "--start", "2022-06-01", "--end", "2022-09-30", "--check"], None, None),
    ("baseline", ["tools/baseline_stats.py"], None, None),
    ("smoke", None, None, None),          # node，见下
    ("mutation", ["tools/mutation_test.py"], r"拦下 (\d+)/(\d+) 个变异", None),
    ("docs", ["tools/docs_consistency.py"], "必现 (\\d+) 条", None),
]


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


def run(name: str, cmd) -> tuple[int, str]:
    # 子进程一律强制 UTF-8 输出：中文 Windows 下把 stdout 接到管道时 Python 用 GBK 编码，
    # 父进程再按 UTF-8 解码就成了乱码——于是"声明核查条数"的正则全部匹配不上，闸门被误判为空转
    # （本轮重定向到文件跑全栈就复现了：六闸门实际全绿，check_all 却报 parity/mutation/docs 空转）。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if name == "smoke":
        r = subprocess.run(["node", "tools/smoke_app.mjs"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600, env=env)
    else:
        r = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=2400, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def declared(pat: str, out: str):
    if not pat:
        return None
    m = re.search(pat, out)
    return [int(x) for x in m.groups()] if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="只跑不写（pre-commit 用）")
    args = ap.parse_args()
    # 严格模式：--verify（pre-commit/CI）要求 GATES.json 的记录与当前代码同树；
    # 正式记录那一跑允许记录落后于代码，因为它正在验绿并覆盖记录（否则改完代码就锁死，评审 A-Q1）
    os.environ["GATES_STRICT"] = "1" if args.verify else "0"

    results = {}
    bad = []
    for name, cmd, pat, pat2 in GATES:
        code, out = run(name, cmd)
        results[name] = {"rc": code, "declared": declared(pat, out),
                         "declared2": declared(pat2, out) if pat2 else None}
        tail = "\n".join(out.strip().splitlines()[-3:])
        print(f"[{'OK  ' if code == 0 else 'FAIL'}] {name}" + (f"｜{tail}" if tail else ""))
        if code:
            bad.append(name)
        if pat and code == 0 and results[name]["declared"] is None:
            bad.append(f"{name}（闸门通过但没有声明核查条数，视为空转）")

    sha, branch = git("rev-parse", "--short", "HEAD"), git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = git("status", "--porcelain")
    meta = {}
    if FIXTURE.exists():
        meta = json.loads(FIXTURE.read_text(encoding="utf-8")).get("meta", {})
    record = {
        "ran_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": sha, "branch": branch,
        "working_tree_dirty": bool(dirty),
        "fixture": {"file": FIXTURE.name, "gridpoint": f"{meta.get('gridpoint_lat')},"
                                                   f"{meta.get('gridpoint_lon')}",
                    "range": f"{meta.get('start')}~{meta.get('end')}",
                    "pulled_at_utc": meta.get("pulled_at_utc"), "model": meta.get("model")},
        "gates": results,
        "all_green": not bad,
    }
    if bad:
        print(f"\n未全绿：{bad}")
        print("上一次成功的 GATES.json 保持不变（如有）→ 禁止提交")
        return 1
    if args.verify:
        print(f"\n全绿（--verify 未写文件）：commit {sha}｜fixture 拉取于 "
              f"{record['fixture']['pulled_at_utc']}")
        return 0
    prev = {}
    if GATES_FILE.exists():
        try:
            prev = json.loads(GATES_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = {}
        if prev.get("all_green") and prev.get("commit") == sha and prev != record:
            record["previous_green_run"] = prev.get("ran_at_utc")
    GATES_FILE.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 tools/GATES.json：commit {sha}（工作树{'有' if dirty else '无'}未提交改动）｜"
          f"fixture {record['fixture']['file']} 拉取于 {record['fixture']['pulled_at_utc']}")
    print("README/docs 引用被验证的 commit 短号时，以该文件为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
