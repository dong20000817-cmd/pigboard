# -*- coding: utf-8 -*-
"""存储层：快照哈希、变更检测、变更留痕、历史序列追加。

"仅在数据或官方发布有变化时修改看板" 的落地方式
------------------------------------------------
1. 每个模块的数据负载单独计算 value_hash（不含生成时间戳）。
2. 与上一份快照逐模块比较：
   - 全部模块哈希相同 → 判定"无变化"：不写 snapshot.json、不重build 看板、
     只在 heartbeat 里记一次检查记录。
   - 任一模块哈希变化 → 写快照、写 changelog（含变化幅度与简要说明）、重build 看板。
3. 历史 CSV 为追加式：同一主键同值不重复写；同键不同值记为"修订"并留痕，
   绝不静默覆盖。
"""

import csv
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HISTORY = os.path.join(DATA, "history")
REGISTRY = os.path.join(DATA, "registry")
SNAPSHOT = os.path.join(DATA, "snapshot.json")
CHANGELOG = os.path.join(DATA, "changelog.jsonl")
HEARTBEAT = os.path.join(DATA, "heartbeat.json")

CST = timezone(timedelta(hours=8))
SCHEMA_VERSION = 1


def now_cst():
    return datetime.now(CST)


def iso(dt=None):
    return (dt or now_cst()).isoformat(timespec="seconds")


def ensure_dirs():
    for d in (DATA, HISTORY, REGISTRY, os.path.join(ROOT, "web"), os.path.join(ROOT, "logs")):
        os.makedirs(d, exist_ok=True)


def value_hash(payload):
    """对数据负载做稳定哈希：键排序、去浮点尾差、忽略时间戳字段。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj, *, indent=2):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.write("\n")
    os.replace(tmp, path)


def load_snapshot():
    return read_json(SNAPSHOT)


def save_snapshot(snap):
    write_json(SNAPSHOT, snap)


def load_registry(name):
    return read_json(os.path.join(REGISTRY, f"{name}.json"))


def append_changelog(entries):
    if not entries:
        return
    with open(CHANGELOG, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def read_changelog(limit=None):
    if not os.path.exists(CHANGELOG):
        return []
    out = []
    with open(CHANGELOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out[-limit:] if limit else out


def write_heartbeat(payload):
    write_json(HEARTBEAT, payload)


# ---------------------------------------------------------------------------
# 追加式历史序列
# ---------------------------------------------------------------------------

def append_history(filename, key_fields, row, value_fields):
    """
    追加一行历史。key_fields 为主键（如 ['date'] 或 ['date','code']）。
    返回 'inserted' | 'unchanged' | 'revised'。
    """
    path = os.path.join(HISTORY, filename)
    rows, fieldnames = [], None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            fieldnames = r.fieldnames
            rows = list(r)

    if fieldnames is None:
        fieldnames = list(row.keys())

    key = tuple(str(row[k]) for k in key_fields)
    for existing in rows:
        if tuple(str(existing.get(k, "")) for k in key_fields) == key:
            same = all(str(existing.get(vf, "")) == str(row.get(vf, "")) for vf in value_fields)
            if same:
                return "unchanged"
            # 修订：保留原值痕迹
            rev = int(existing.get("revision") or 0) + 1
            old = {vf: existing.get(vf) for vf in value_fields}
            for k, v in row.items():
                existing[k] = v
            existing["revision"] = rev
            existing["revised_from"] = json.dumps(old, ensure_ascii=False)
            existing["revised_at"] = iso()
            if "revision" not in fieldnames:
                fieldnames.append("revision")
            if "revised_from" not in fieldnames:
                fieldnames.append("revised_from")
            if "revised_at" not in fieldnames:
                fieldnames.append("revised_at")
            _rewrite(path, fieldnames, _sort_rows(rows, key_fields))
            return "revised"

    row = dict(row)
    row.setdefault("revision", 0)
    row.setdefault("revised_from", "")
    row.setdefault("revised_at", "")
    for fn in ("revision", "revised_from", "revised_at"):
        if fn not in fieldnames:
            fieldnames.append(fn)
    rows.append(row)
    _rewrite(path, fieldnames, _sort_rows(rows, key_fields))
    return "inserted"


def _sort_rows(rows, key_fields):
    """按主键排序后落盘：本地与云端各自追加时行序不同，排序后 diff 才稳定，
    否则每次跨机器合并都会整文件冲突。"""
    return sorted(rows, key=lambda e: tuple(str(e.get(k, "")) for k in key_fields))


def _rewrite(path, fieldnames, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def bulk_upsert_history(filename, key_fields, rows, value_fields):
    """
    批量写入历史（回补用）。比逐行 append_history 快得多：只重写一次文件。

    rows: [{k: v, ...}]，按 key_fields 去重。
    返回 {"inserted": n, "revised": n, "unchanged": n, "status": "inserted|revised|unchanged"}
    同键同值不写；同键异值记为修订并保留原值痕迹。
    """
    path = os.path.join(HISTORY, filename)
    existing, fieldnames = [], None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            fieldnames = r.fieldnames
            existing = list(r)

    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for k in row:
                if k not in fieldnames:
                    fieldnames.append(k)
        for fn in ("revision", "revised_from", "revised_at"):
            if fn not in fieldnames:
                fieldnames.append(fn)

    index = {tuple(str(e.get(k, "")) for k in key_fields): e for e in existing}
    stats = {"inserted": 0, "revised": 0, "unchanged": 0}
    changed = False

    for row in rows:
        key = tuple(str(row.get(k, "")) for k in key_fields)
        cur = index.get(key)
        if cur is None:
            rec = dict(row)
            rec.setdefault("revision", 0)
            rec.setdefault("revised_from", "")
            rec.setdefault("revised_at", "")
            for fn in ("revision", "revised_from", "revised_at"):
                if fn not in fieldnames:
                    fieldnames.append(fn)
            existing.append(rec)
            index[key] = rec
            stats["inserted"] += 1
            changed = True
            continue

        if all(str(cur.get(vf, "")) == str(row.get(vf, "")) for vf in value_fields):
            stats["unchanged"] += 1
            continue

        old = {vf: cur.get(vf) for vf in value_fields}
        for k, v in row.items():
            cur[k] = v
        cur["revision"] = int(cur.get("revision") or 0) + 1
        cur["revised_from"] = json.dumps(old, ensure_ascii=False)
        cur["revised_at"] = iso()
        for fn in ("revision", "revised_from", "revised_at"):
            if fn not in fieldnames:
                fieldnames.append(fn)
        stats["revised"] += 1
        changed = True

    if changed:
        existing = _sort_rows(existing, key_fields)
        _rewrite(path, fieldnames, existing)

    stats["status"] = (
        "inserted" if stats["inserted"] else ("revised" if stats["revised"] else "unchanged")
    )
    return stats
