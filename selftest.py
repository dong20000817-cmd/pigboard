#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：验证看板最重要的那条承诺 ——「没有新数据，就不动看板」。

在**临时副本**中做故障注入，不触碰正式数据。

用法：  python3 selftest.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(cwd, expect_rc=0):
    p = subprocess.run([PY, "pipeline.py"], cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def main():
    tmp = tempfile.mkdtemp(prefix="pigboard-selftest-")
    proj = os.path.join(tmp, "proj")
    os.makedirs(proj)
    for item in os.listdir(ROOT):
        if item in ("data", "web", "__pycache__", "docs", "logs"):
            continue
        src, dst = os.path.join(ROOT, item), os.path.join(proj, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(proj, "data", "registry"))
    for f in os.listdir(os.path.join(ROOT, "data", "registry")):
        shutil.copy2(os.path.join(ROOT, "data", "registry", f), os.path.join(proj, "data", "registry", f))

    idx = os.path.join(proj, "web", "index.html")
    passed, failed = [], []

    def check(name, ok, detail=""):
        (passed if ok else failed).append(name)
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail and not ok else ""))

    print("生猪市场看板 · 自检（临时副本，不影响正式数据）")
    print(f"工作副本：{proj}\n")

    print("[1] 首次采集")
    rc, out = run(proj)
    check("首次运行成功", rc == 0, out[-300:])
    check("生成 web/index.html", os.path.exists(idx))
    check("生成 data/snapshot.json", os.path.exists(os.path.join(proj, "data", "snapshot.json")))
    check("生成变更留痕", os.path.exists(os.path.join(proj, "data", "changelog.jsonl")))

    print("\n[2] 无变化时必须原样不动")
    h1, m1 = sha(idx), os.path.getmtime(idx)
    rc, out = run(proj)
    h2, m2 = sha(idx), os.path.getmtime(idx)
    check("判定为'无新数据'", "无新数据" in out, out[-200:])
    check("看板内容未变", h1 == h2)
    check("看板 mtime 未变", m1 == m2)

    print("\n[3] 数据源故障时必须沿用上一期且继续告警")
    spot = os.path.join(proj, "collectors", "spot.py")
    shutil.copy2(spot, spot + ".bak")
    with open(spot, "w", encoding="utf-8") as f:
        f.write("def fetch_national_spot():\n    raise RuntimeError('selftest 注入故障')\n")
    rc, out = run(proj)
    check("故障时仍正常退出", rc == 0, out[-300:])
    check("看板标出 stale 告警", "stale-banner" in open(idx, encoding="utf-8").read())
    import json

    snap = json.load(open(os.path.join(proj, "data", "snapshot.json"), encoding="utf-8"))
    check("沿用上一期数值", snap["modules"]["hog_spot"]["headline"]["value"] is not None)

    print("\n[4] 故障持续时不得反复改写看板")
    h3 = sha(idx)
    rc, out = run(proj)
    check("第二次故障运行未改写看板", sha(idx) == h3, out[-200:])

    print("\n[5] 数据源恢复")
    shutil.move(spot + ".bak", spot)
    rc, out = run(proj)
    snap = json.load(open(os.path.join(proj, "data", "snapshot.json"), encoding="utf-8"))
    check("恢复采集成功", rc == 0, out[-300:])
    check("stale 告警解除", snap["modules"]["hog_spot"]["stale"] is False)
    h4 = sha(idx)
    rc, out = run(proj)
    check("恢复后重新稳定", "无新数据" in out and sha(idx) == h4, out[-200:])

    print("\n[6] 变更留痕必须准确分类")
    kinds = {}
    for line in open(os.path.join(proj, "data", "changelog.jsonl"), encoding="utf-8"):
        line = line.strip()
        if line:
            k = json.loads(line).get("change_kind", "unknown")
            kinds[k] = kinds.get(k, 0) + 1
    print(f"    留痕分类统计：{kinds}")
    check("存在 first_seen 条目", kinds.get("first_seen", 0) >= 5)
    check("存在 source_error 条目", kinds.get("source_error", 0) >= 1)
    check("存在 source_recovered 条目", kinds.get("source_recovered", 0) >= 1)

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 56}")
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        for f_ in failed:
            print(f"  ❌ {f_}")
        return 1
    print("全部通过：'没有新数据就不动看板' 的不变量成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
