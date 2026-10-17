# -*- coding: utf-8 -*-
"""采集 3：农业农村部（五部委联合发布）《生猪产品数据》官方原始表。

数据源：农业农村部生猪专题「月度数据」
  https://www.moa.gov.cn/ztzl/szcpxx/jdsj/{年}/{年}{月:02d}/
每期页面挂一个官方 .xlsx（工作表「发布稿」），是能繁母猪存栏等指标的**正式载体**。

口径要点（官方备注原文）：
  季度末月份能繁母猪存栏量为国家统计局数据；非季度末月份，以国家统计局季度末数据为基数，
  根据农业农村部定点监测的能繁母猪存栏月度环比变化率推算得出。
  → 因此**只有季末月才有能繁母猪绝对值**，非季末月没有该行是正常的，不是抓取失败。

发布节奏：官方页面标注「每年1月20日、4月20日、7月20日、10月20日」。
"""

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

from collectors.http import request

BASE = "https://www.moa.gov.cn/ztzl/szcpxx/jdsj"
HEADERS = {"Referer": "https://www.moa.gov.cn/ztzl/szcpxx/jdsj/"}
CST = timezone(timedelta(hours=8))

# 指标名关键词 → 规范字段名。顺序敏感：先长后短，避免"生猪存栏"误吃"能繁母猪存栏"。
METRIC_RULES = [
    ("能繁母猪存栏", "sow_inventory", "万头"),
    ("生猪存栏", "hog_inventory", "万头"),
    ("生猪出栏", "hog_slaughter", "万头"),
    ("屠宰", "slaughter_volume", "万头"),
    ("猪肉产量", "pork_output", "万吨"),
    ("二元母猪销售价格", "sow_price", "元/公斤"),
    ("仔猪价格", "piglet_price", "元/公斤"),
    ("生猪出场价格", "hog_farmgate_price", "元/公斤"),
    ("白条猪", "wholesale_white_strip", "元/公斤"),
    ("进口猪肉", "import_pork", "万吨"),
    ("出口猪肉", "export_pork", "万吨"),
]

PERIOD_PATTERNS = [
    (r"(\d{4})年(\d)季度末", "quarter_end"),
    (r"(\d{4})年(\d{1,2})月份", "month"),
    (r"(\d{4})年(\d{1,2})月", "month"),
    (r"(\d{4})年上半年", "half_year"),
    (r"(\d{4})年1-(\d{1,2})月", "ytd"),
]


def _now():
    return datetime.now(CST)


def _month_sequence(lookback=14):
    """从当前月开始往前推的 (year, month) 序列。"""
    y, m = _now().year, _now().month
    out = []
    for _ in range(lookback):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _find_xlsx(page_url):
    status, raw = request(page_url, headers=HEADERS, timeout=30, retries=2)
    if status != 200:
        return None
    html = raw.decode("utf-8", "replace")
    m = re.search(r'href=["\'](\.?/?[^"\']+\.xlsx)["\']', html, flags=re.I)
    if not m:
        return None
    href = m.group(1)
    if href.startswith("http"):
        return href
    return page_url.rstrip("/") + "/" + href.lstrip("./")


def _download(url):
    status, raw = request(url, headers=HEADERS, timeout=45, retries=2)
    if status != 200:
        raise RuntimeError(f"下载失败 HTTP {status}: {url}")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return path


def _parse_publish_sheet(path):
    """解析『发布稿』工作表，返回 (标题, 发布单位, 指标行列表)。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb["发布稿"] if "发布稿" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()

    title = publisher = None
    header_idx = None
    for i, r in enumerate(rows[:12]):
        joined = " ".join(str(c) for c in r if c is not None)
        if title is None and "生猪产品数据" in joined:
            title = joined.strip()
        if publisher is None and "农业农村部" in joined and "发展改革委" in joined:
            publisher = joined.strip()
        if header_idx is None and "指标" in joined and "数值" in joined:
            header_idx = i
    if header_idx is None:
        return title, publisher, []

    cols = {}
    for j, v in enumerate(rows[header_idx]):
        if v:
            cols.setdefault(str(v).strip(), j)

    def cell(r, key):
        j = cols.get(key)
        return r[j] if j is not None and j < len(r) else None

    items = []
    for r in rows[header_idx + 1:]:
        name = cell(r, "指标")
        if not name:
            continue
        items.append(
            {
                "category": (str(cell(r, "指标分类")).strip() if cell(r, "指标分类") else None),
                "name": str(name).strip(),
                "value_raw": cell(r, "数值"),
                "mom": cell(r, "环比"),
                "yoy": cell(r, "同比"),
            }
        )
    return title, publisher, items


def _publish_date_from_url(url):
    """官方 XLSX 文件名形如 P020260723514354158918.xlsx，其中 20260723 即发布日期。"""
    m = re.search(r"P0(\d{4})(\d{2})(\d{2})", url or "")
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    except ValueError:
        pass
    return None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def _classify(name):
    """返回 (metric_field, unit) 或 (None, None)。长关键词优先匹配。"""
    for kw, field, unit in sorted(METRIC_RULES, key=lambda x: -len(x[0])):
        if kw in name:
            return field, unit
    return None, None


def _period_of(name):
    for pat, kind in PERIOD_PATTERNS:
        m = re.search(pat, name)
        if m:
            g = m.groups()
            if kind == "quarter_end":
                return {"kind": kind, "label": m.group(0), "year": int(g[0]), "quarter": int(g[1])}
            if kind == "month":
                return {"kind": kind, "label": m.group(0), "year": int(g[0]), "month": int(g[1])}
            if kind == "half_year":
                return {"kind": kind, "label": m.group(0), "year": int(g[0])}
            if kind == "ytd":
                return {"kind": kind, "label": m.group(0), "year": int(g[0]), "month": int(g[1])}
    return {"kind": "unknown", "label": None}


def fetch_latest(lookback=14):
    """返回最近一期已发布的官方《生猪产品数据》。"""
    for y, m in _month_sequence(lookback):
        page = f"{BASE}/{y}/{y}{m:02d}/"
        try:
            xlsx = _find_xlsx(page)
        except Exception:
            continue
        if not xlsx:
            continue
        path = _download(xlsx)
        try:
            title, publisher, items = _parse_publish_sheet(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if not items:
            continue

        normalized = []
        for it in items:
            field, unit = _classify(it["name"])
            normalized.append(
                {
                    "name": it["name"],
                    "category": it["category"],
                    "metric": field,
                    "unit": unit,
                    "period": _period_of(it["name"]),
                    "value": _num(it["value_raw"]),
                    "value_raw": it["value_raw"],
                    "mom": _num(it["mom"]),
                    "yoy": _num(it["yoy"]),
                }
            )

        sow = next(
            (n for n in normalized if n["metric"] == "sow_inventory" and "季度末" in n["name"]),
            None,
        )
        return {
            "year": y,
            "month": m,
            "period_label": f"{y}年{m}月",
            "title": title,
            "publisher": publisher,
            "publish_url": page,
            "xlsx_url": xlsx,
            "items": normalized,
            "sow_inventory": sow,
        }

    raise RuntimeError("未找到任何已发布的《生猪产品数据》官方表")


QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache", "moa_series.json"
)


def _quarter_stat_date(period):
    if period.get("kind") == "quarter_end" and period.get("quarter") in QUARTER_END:
        m, d = QUARTER_END[period["quarter"]]
        return f"{period['year']:04d}-{m:02d}-{d:02d}"
    return None


def _parse_period_from_xlsx(year, month):
    """月份页 -> 该期数据的正式发布日（从页面/xlsx 文件名推断不出，用官方节奏兜底）。"""
    return f"{year}年{month}月"


def fetch_official_series(months_back=20, ttl_hours=12, force=False):
    """
    回溯扫描官方各月《生猪产品数据》，抽出全部「季度末能繁母猪存栏」观测。
    结果带 TTL 缓存，避免每次巡检都重扫 20 个月页。

    返回 {"points": [...], "scanned": n, "cache": "hit|miss", "latest_period": "..."}
    """
    import json as _json
    import time

    if not force and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cached = _json.load(f)
            if time.time() - cached.get("_cached_at", 0) < ttl_hours * 3600:
                cached["cache"] = "hit"
                return cached
        except Exception:
            pass

    points = []
    scanned = 0
    for y, m in _month_sequence(months_back):
        page = f"{BASE}/{y}/{y}{m:02d}/"
        try:
            xlsx = _find_xlsx(page)
            if not xlsx:
                continue
            path = _download(xlsx)
            try:
                title, publisher, items = _parse_publish_sheet(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception:
            continue
        if not items:
            continue
        scanned += 1
        for it in items:
            field, unit = _classify(it["name"])
            if field != "sow_inventory" or "能繁母猪" not in it["name"]:
                continue
            period = _period_of(it["name"])
            stat_date = _quarter_stat_date(period)
            if not stat_date:
                continue
            raw = it["value_raw"]
            pct = None
            mm = re.search(r"(\d+(?:\.\d+)?)\s*%", str(raw))
            if mm:
                pct = float(mm.group(1))
            points.append(
                {
                    "stat_date": stat_date,
                    "period_label": period["label"],
                    "value": _num(raw),
                    "value_raw": raw,
                    "mom_pct_official": round(_num(it["mom"]) * 100, 2) if _num(it["mom"]) is not None else None,
                    "yoy_pct": round(_num(it["yoy"]) * 100, 2) if _num(it["yoy"]) is not None else None,
                    "pct_of_benchmark": pct,
                    "publisher": publisher or "农业农村部等五部委联合发布",
                    "publish_date": _publish_date_from_url(xlsx),
                    "source_title": title,
                    "source_url": page,
                    "xlsx_url": xlsx,
                    "auto": True,
                    "kind": "official",
                }
            )

    # 去重（同 stat_date 取扫描到的最先一期）并按日期排序
    seen, uniq = set(), []
    for p in sorted(points, key=lambda x: x["stat_date"]):
        if p["stat_date"] in seen:
            continue
        seen.add(p["stat_date"])
        uniq.append(p)

    out = {
        "points": uniq,
        "scanned": scanned,
        "latest_period": uniq[-1]["period_label"] if uniq else None,
        "cache": "miss",
        "_cached_at": time.time(),
    }
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        _json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    import json

    d = fetch_latest()
    print(json.dumps({k: v for k, v in d.items() if k != "items"}, ensure_ascii=False, indent=2))
    print("\n--- 生产类 ---")
    for it in d["items"]:
        if it["category"] == "生产":
            print(f"  {it['name']}: {it['value_raw']}  环比={it['mom']} 同比={it['yoy']}")
    print("\n--- 官方季度序列扫描 ---")
    s = fetch_official_series(months_back=20, force=True)
    print(f"扫描到 {s['scanned']} 期，能繁母猪观测 {len(s['points'])} 个：")
    for p in s["points"]:
        print(f"  {p['period_label']} ({p['stat_date']}): {p['value']} 万头  环比={p['mom_pct_official']}  同比={p['yoy_pct']}")
