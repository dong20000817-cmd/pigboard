# -*- coding: utf-8 -*-
"""公开渠道自动扫描：冻品库容率、淘汰母猪月度均价。

定位
----
这两项都不存在免费官方接口（第三方样本、多为付费口径）。本模块不猜测、不编造，
只做三件事：
  1) 每天扫描公开文章列表，发现提到「冻品库容率 / 淘汰母猪均价」的新文章；
  2) 仅当文中**明示机构**（卓创 / 钢联 / 涌益）且数值**不是预测值**时提取；
  3) 以 (机构, 统计日) 去重后追加到登记库，逐点保留原文标题与链接。

失败时静默降级：不写文件、不阻断主流程，看板继续用上一期数据。
"""

import calendar
import html
import json
import os
import re
import time
from datetime import date

from collectors.http import request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

# 发现入口：列表页（只抓最近几页，足够覆盖周度/月度披露）
LIST_SOURCES = [
    ("https://news.zhuwang.com.cn/xingyedianping/list-148-{}.html", 6),
    ("https://hangqing.zhuwang.com.cn/hangqingfenxi/list-81-{}.html", 6),
    ("https://www.yangzhu360.com/dianping/index.html", 1),
    ("https://www.yangzhu360.com/dianping/{}.html", 3),
]

PUBLISHERS = {
    "sci": ("sci-sample-slaughter", "卓创资讯 SCI"),
    "mysteel": ("mysteel-key-slaughter", "上海钢联 Mysteel（钢联数据）"),
}

# 数值合理性区间，越界视为解析错误
FROZEN_RANGE = (10.0, 60.0)
CULL_RANGE = (4.0, 15.0)
RATIO_RANGE = (40.0, 95.0)


def _get(url, timeout=10):
    status, raw = request(url, headers=UA, timeout=timeout, retries=1)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    return raw.decode("utf-8", "replace")


def _text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    h = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    return html.unescape(h)


def _publisher_near(txt, idx, radius=260):
    """在数值附近找机构名，避免整篇文章同时提到多家时误判。"""
    seg = txt[max(0, idx - radius): idx + radius]
    best, dist = None, 10 ** 9
    for key, needle in (("sci", "卓创"), ("mysteel", "钢联"), ("mysteel", "Mysteel"), ("yongyi", "涌益")):
        p = seg.find(needle)
        if p >= 0 and p < dist:
            best, dist = key, p
    return best


def _is_forecast(txt, idx, radius=45):
    seg = txt[max(0, idx - radius): idx]
    return bool(re.search(r"预计|预测|或至|有望|料将|目标", seg))


def _month_end(year, month):
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _parse_md(txt, idx, radius=80):
    """在数值附近解析「截至X月X日」「X月份」。返回 (date_str, label) 或 (None, None)。"""
    seg = txt[max(0, idx - radius): idx + radius]
    m = re.search(r"截至\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", seg)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", m.group(0)
    m = re.search(r"截至\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", seg)
    if m:
        return None, ("截至", int(m.group(1)), int(m.group(2)))  # 年份由调用方补
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:份)?(?:平均|均价|末)?", seg)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return _month_end(y, mo), m.group(0)
    m = re.search(r"(\d{1,2})\s*月(?:份)?(?:平均|均价|末)?", seg)
    if m:
        return None, ("月", int(m.group(1)))
    return None, None


def _discover(budget=35):
    """返回 [(url, title, article_date)]，只保留标题含关键词的近期文章。"""
    out, seen = [], set()
    t0 = time.time()
    for tmpl, pages in LIST_SOURCES:
        for p in range(1, pages + 1):
            if time.time() - t0 > budget:
                break
            url = tmpl.format(p) if "{}" in tmpl else tmpl
            try:
                h = _get(url)
            except Exception:
                continue
            for m in re.finditer(r'<a[^>]+href="([^"]+?/(20\d{6})/(\d+)\.html)"[^>]*>(.*?)</a>', h, flags=re.S):
                title = re.sub(r"<[^>]+>", "", m.group(4)).strip()
                if not title or not any(k in title for k in ("冻品", "库容", "淘汰母猪")):
                    continue
                if m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                out.append((m.group(1), html.unescape(title), m.group(2)))
    return out


def _extract_frozen(txt, url, title, art_date):
    pts = []
    pats = [
        r"库容率[^。；\n]{0,28}?(\d{2}\.\d{1,2})\s*%",
        r"(\d{2}\.\d{1,2})\s*%[^。；\n]{0,10}(?:的)?(?:冻品)?(?:库容|库存)",
        r"库存[^。；\n]{0,16}?(\d{2}\.\d{1,2})\s*%",
    ]
    for pat in pats:
        for m in re.finditer(pat, txt):
            idx = m.start(1)
            val = float(m.group(1))
            if not (FROZEN_RANGE[0] <= val <= FROZEN_RANGE[1]):
                continue
            if _is_forecast(txt, idx):
                continue
            pk = _publisher_near(txt, idx)
            if pk not in PUBLISHERS:
                continue
            pid, pname = PUBLISHERS[pk]
            ds, label = _parse_md(txt, idx)
            if ds is None:
                if isinstance(label, tuple):
                    # 只有月日：用文章年份补全
                    y = int(art_date[:4])
                    mo, dd = (label[1], label[2]) if label[0] == "截至" else (label[1], 1)
                    ds = f"{y:04d}-{mo:02d}-{dd:02d}"
                else:
                    ds = art_date
            if not (11 <= int(ds[5:7]) <= 12 or 1 <= int(ds[5:7]) <= 12):
                continue
            # 同一篇里同一个 (机构, 日期) 只留一个
            if any(p["stat_date"] == ds and p["publisher_id"] == pid for p in pts):
                continue
            pts.append({
                "publisher_id": pid,
                "publisher": pname,
                "stat_date": ds,
                "period_label": label if isinstance(label, str) else f"{ds[:7]}",
                "value": round(val, 2),
                "publish_date": art_date,
                "source_title": title,
                "source_url": url,
                "note": "自动扫描提取：公开文章原文数值（非预测）。",
                "origin": "auto",
            })
        if pts:
            break
    return pts


def _month_avg_spot():
    import csv
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "history", "hog_spot_daily.csv")
    if not os.path.exists(path):
        return {}
    acc = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d, v = r.get("date"), r.get("value")
            if not d or not v:
                continue
            try:
                acc.setdefault(d[:7], []).append(float(v))
            except ValueError:
                continue
    return {k: round(sum(v) / len(v), 4) for k, v in acc.items()}


def _extract_cull(txt, url, title, art_date, month_avg):
    pts = []
    pats = [
        r"(\d{1,2})\s*月份?[^。；\n]{0,40}?淘汰母猪[^。；\n]{0,30}?均价[为约]?\s*(\d\.\d{1,2})\s*元\s*/\s*公斤",
        r"淘汰母猪(?:月度出栏)?均价[为约]?\s*(\d\.\d{1,2})\s*元\s*/\s*公斤",
    ]
    for pat in pats:
        for m in re.finditer(pat, txt):
            if pat.startswith(r"(\d{1,2})"):
                mo = int(m.group(1))
                val = float(m.group(2))
                idx = m.start(2)
            else:
                val = float(m.group(1))
                idx = m.start(1)
                mo = int(art_date[5:7])
            if not (CULL_RANGE[0] <= val <= CULL_RANGE[1]):
                continue
            if _is_forecast(txt, idx):
                continue
            pk = _publisher_near(txt, idx)
            if pk != "sci":
                continue
            y = int(art_date[:4])
            # 文章若在次月初发布上个月数据（如 8/31 发 8 月），仍按文中的月份
            if mo > int(art_date[5:7]) + 1:
                y -= 1
            key = f"{y:04d}-{mo:02d}"
            hog = month_avg.get(key)
            if not hog:
                continue
            ratio = val / hog * 100
            if not (RATIO_RANGE[0] <= ratio <= RATIO_RANGE[1]):
                continue
            ds = _month_end(y, mo)
            if any(p["stat_date"] == ds for p in pts):
                continue
            pts.append({
                "stat_date": ds,
                "period_label": f"{y}年{mo}月",
                "cull_sow_price": round(val, 2),
                "commercial_hog_price": round(hog, 3),
                "commercial_hog_price_date": ds,
                "ratio_pct": round(ratio, 2),
                "publisher": "卓创资讯（淘汰母猪）/ 中国养猪网（商品猪，月度均价）",
                "publish_date": art_date,
                "source_title": title,
                "source_url": url,
                "note": "自动扫描：卓创淘汰母猪月度均价 ÷ 同月全国外三元月均价（本看板计算）。",
                "origin": "auto",
            })
        if pts:
            break
    return pts


def scan():
    """返回 {'frozen': [...], 'cull': [...]}；任何异常都吞掉，返回空结果。"""
    result = {"frozen": [], "cull": [], "scanned": 0, "errors": []}
    try:
        arts = _discover()
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"discover: {type(e).__name__}")
        return result
    result["scanned"] = len(arts)
    mavg = _month_avg_spot()
    t0 = time.time()
    for url, title, art_date in arts:
        if time.time() - t0 > 40:
            break
        try:
            txt = _text(_get(url))
        except Exception:
            continue
        iso_date = f"{art_date[:4]}-{art_date[4:6]}-{art_date[6:]}"
        if any(k in title for k in ("冻品", "库容")):
            try:
                result["frozen"].extend(_extract_frozen(txt, url, title, iso_date))
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"frozen: {type(e).__name__}")
        if "淘汰母猪" in title or "淘汰母猪" in txt:
            try:
                result["cull"].extend(_extract_cull(txt, url, title, iso_date, mavg))
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"cull: {type(e).__name__}")
    # 去重
    for k in ("frozen", "cull"):
        ded, seen = [], set()
        for p in result[k]:
            key = (p.get("publisher_id"), p["stat_date"]) if k == "frozen" else p["stat_date"]
            if key in seen:
                continue
            seen.add(key)
            ded.append(p)
        result[k] = ded
    return result


def merge_into_registry(found):
    """把新点位并入登记库。返回 {'frozen_added': n, 'cull_added': n}。"""
    import store
    stats = {"frozen_added": 0, "cull_added": 0}
    if found.get("frozen"):
        reg = store.load_registry("frozen_inventory")
        hist = reg.setdefault("history", [])
        have = {(p.get("publisher_id"), p["stat_date"]) for p in hist}
        for p in found["frozen"]:
            if (p["publisher_id"], p["stat_date"]) in have:
                continue
            hist.append(p)
            have.add((p["publisher_id"], p["stat_date"]))
            stats["frozen_added"] += 1
        if stats["frozen_added"]:
            hist.sort(key=lambda x: (x["stat_date"], x.get("publisher_id") or ""))
            # 同步 headline：若新点晚于该机构 latest，则滚动 latest
            by_id = {}
            for p in hist:
                by_id.setdefault(p.get("publisher_id"), []).append(p)
            for s in reg.get("series", []):
                pts = sorted(by_id.get(s["id"], []), key=lambda x: x["stat_date"])
                if pts and pts[-1]["stat_date"] > s["latest"]["stat_date"]:
                    old = dict(s["latest"])
                    s.setdefault("points", []).append(old)
                    s["latest"] = {k: pts[-1].get(k) for k in
                                   ("period_label", "stat_date", "value", "publish_date", "source_title", "source_url", "note")}
                    s["latest"].setdefault("change_pp", None)
                    s["latest"].setdefault("yoy_pp", None)
            reg.setdefault("auto_update", {})["last_scan"] = store.iso()
            reg["auto_update"]["last_result"] = stats["frozen_added"]
            store.write_json(os.path.join(store.REGISTRY, "frozen_inventory.json"), reg)

    if found.get("cull"):
        reg = store.load_registry("cull_sow_ratio")
        trend = reg.setdefault("trend", [])
        have = {p["stat_date"] for p in trend}
        for p in found["cull"]:
            if p["stat_date"] in have:
                continue
            trend.append(p)
            have.add(p["stat_date"])
            stats["cull_added"] += 1
        if stats["cull_added"]:
            trend.sort(key=lambda x: x["stat_date"])
            reg.setdefault("auto_update", {})["last_scan"] = store.iso()
            reg["auto_update"]["last_result"] = stats["cull_added"]
            store.write_json(os.path.join(store.REGISTRY, "cull_sow_ratio.json"), reg)
    return stats


def run():
    try:
        found = scan()
        stats = merge_into_registry(found)
        return {"ok": True, **stats, "scanned": found.get("scanned", 0),
                "found_frozen": len(found.get("frozen", [])),
                "found_cull": len(found.get("cull", [])),
                "errors": found.get("errors", [])[:5]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    print(json.dumps(scan(), ensure_ascii=False, indent=2))
