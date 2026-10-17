# -*- coding: utf-8 -*-
"""采集 1：全国外三元生猪均价（中国养猪网 / 玄田数据）。"""

from collectors.http import post_json

API = "https://xt.yangzhu.vip/data/getzhujiatoday"
HISTORY_API = "https://xt.yangzhu.vip/data/getzhujiahitsdata"
REFERER = "https://xt.yangzhu.vip/manage/datamap/ptype/1/areano/-1.html"


def fetch_national_spot():
    """
    返回 dict：
      stat_date   统计日期（报价日）
      value       全国外三元均价，元/公斤
      prev_value  上一有效报价日价格（由涨跌额反推）
      change_abs  涨跌额，元/公斤
      change_pct  涨跌幅，%
      yoy / mom   同比 / 环比，%
      extras      同一接口的玉米、豆粕、猪粮比、内三元、土杂猪（用于交叉参考）
    """
    js = post_json(
        API,
        {"ptype": 1, "areano": -1},
        headers={"Referer": REFERER, "X-Requested-With": "XMLHttpRequest"},
    )
    if js.get("code") != 200:
        raise RuntimeError(f"接口返回异常: {js.get('msg')}")

    d = js["data"]
    cur = float(d["pigprice"])
    chg = float(d["pigprice_ud"])
    prev = round(cur - chg, 4)
    pct = round(chg / prev * 100, 2) if prev else None

    return {
        "stat_date": d["pricedate"],
        "value": cur,
        "prev_value": prev,
        "change_abs": round(chg, 2),
        "change_pct": pct,
        "yoy": d.get("pigprice_tb"),
        "mom": d.get("pigprice_hb"),
        "extras": {
            "内三元": {"value": float(d["pig_in"]), "change": float(d["pig_in_ud"])},
            "土杂猪": {"value": float(d["pig_local"]), "change": float(d["pig_local_ud"])},
            "玉米": {"value": float(d["maizeprice"]), "change": float(d["maizeprice_ud"]), "unit": "元/吨"},
            "豆粕": {"value": float(d["bean"]), "change": float(d["bean_ud"]), "unit": "元/吨"},
            "猪粮比": {"value": d.get("pigfoodratio")},
        },
    }


def fetch_spot_history():
    """
    同发行人（中国养猪网）同口径的**日度**全国外三元均价历史，由官方页面「历史」标签所用接口返回。

    POST /data/getzhujiahitsdata  {ptype:1, areano:-1, datetype:0}
    datetype=0 返回近 12 个月的连续日线（约 365 条）；其他 datetype 需付费会员。

    返回 [{"date": "YYYY-MM-DD", "value": float, "source": "..."}]，按日期升序。
    已实测：该序列覆盖完整、无断点，且与 /getzhujiatoday 当日值逐日一致。
    """
    js = post_json(
        HISTORY_API,
        {"ptype": 1, "areano": -1, "datetype": 0},
        headers={"Referer": REFERER, "X-Requested-With": "XMLHttpRequest"},
    )
    if js.get("code") != 200:
        raise RuntimeError(f"历史接口返回异常: {js.get('msg')}")

    rows, seen = [], set()
    for x in js.get("data") or []:
        d = str(x.get("pricedate") or "").strip()
        if not d or d in seen:
            continue
        try:
            v = float(x.get("pigprice"))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        seen.add(d)
        rows.append({"date": d, "value": v})

    rows.sort(key=lambda r: r["date"])
    if len(rows) < 30:
        raise RuntimeError(f"历史序列过短（{len(rows)} 条），疑似接口变更")
    return rows


def window_stats(series, windows=(7, 30, 90)):
    """
    计算各窗口的区间变化。series: 升序 [{date, value}]。

    口径：N 日变化 = 最新报价 − N 个日历日前（含当日）的那一日报价；
    若该日无报价，则取该日之前最近一个有报价日（序列本身无断点，通常为精确 N 日前）。
    """
    from datetime import date as _date

    if not series:
        return {}

    pts = [(r["date"], r["value"]) for r in series]
    by_date = dict(pts)
    last_date_s, last_val = pts[-1]
    last_date = _date.fromisoformat(last_date_s)

    out = {}
    for n in windows:
        target = last_date.fromordinal(last_date.toordinal() - n)
        target_s = target.isoformat()
        base_date, base_val = None, None
        if target_s in by_date:
            base_date, base_val = target_s, by_date[target_s]
        else:
            earlier = [d for d, _ in pts if d <= target_s]
            if earlier:
                base_date = earlier[-1]
                base_val = by_date[base_date]
        if base_val is None:
            out[n] = {
                "window": n,
                "available": False,
                "reason": f"序列仅回溯到 {pts[0][0]}，不足 {n} 日",
            }
            continue

        span = pts[[d for d, _ in pts].index(base_date) + 1:]
        hi = max(v for _, v in span)
        lo = min(v for _, v in span)
        diff = round(last_val - base_val, 4)
        out[n] = {
            "window": n,
            "available": True,
            "start_date": base_date,
            "start_value": base_val,
            "end_date": last_date_s,
            "end_value": last_val,
            "change_abs": diff,
            "change_pct": round(diff / base_val * 100, 2) if base_val else None,
            "high": hi,
            "low": lo,
            "points": len(span),
        }
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_national_spot(), ensure_ascii=False, indent=2))
    h = fetch_spot_history()
    print(f"\n历史 {len(h)} 条: {h[0]['date']} → {h[-1]['date']}")
    print(json.dumps(window_stats(h), ensure_ascii=False, indent=2))
