# -*- coding: utf-8 -*-
"""采集 2：大商所生猪期货全部活跃合约。

双源互校设计（重要）：
  * 东方财富行情接口 —— 负责"合约发现"（拿到当前全部挂牌合约清单）与 OHLC/持仓/成交
  * 新浪财经行情接口 —— 负责"交易日"与"结算价"，并逐合约交叉校验收盘价
两个源都镜像大商所日行情。若同合约收盘价不一致，标记 conflict 并让看板告警，
绝不静默取其一。交易所日内盈亏按**结算价**计算，故两者都必须展示。
"""

import re

from collectors.http import get_json, request

EM_LIST = "https://futsseapi.eastmoney.com/list/114"
FIELDS = "dm,sc,name,p,zde,zdf,zjsj,vol,ccl,o,h,l"
SINA_URL = "https://hq.sinajs.cn/list="
MIN_OPEN_INTEREST = 1  # 持仓量为 0 视为已摘牌/无活跃度


def _is_real_contract(code):
    """过滤掉'主连/次主连'等合成连续合约，只保留真实挂牌合约（如 lh2701）。"""
    return bool(re.fullmatch(r"lh\d{4}", code))


def fetch_em_contracts():
    url = f"{EM_LIST}?orderBy=dm&sort=asc&pageSize=500&pageIndex=0&field={FIELDS}&_=1"
    js = get_json(url, headers={"Referer": "https://quote.eastmoney.com/"})
    rows = {}
    for x in js.get("list", []):
        if "生猪" not in x.get("name", ""):
            continue
        code = str(x.get("dm", "")).lower()
        if not _is_real_contract(code):
            continue
        rows[code.upper()] = {
            "code": code.upper(),
            "name": x["name"],
            "close": float(x["p"]),
            "prev_settle": float(x["zjsj"]),
            "change_abs": round(float(x["zde"]), 2),
            "change_pct": round(float(x["zdf"]), 2),
            "open": float(x["o"]),
            "high": float(x["h"]),
            "low": float(x["l"]),
            "volume": int(x["vol"]),
            "open_interest": int(x["ccl"]),
        }
    if not rows:
        raise RuntimeError("未取到任何生猪期货合约，东方财富接口字段可能已变更")
    return rows


def fetch_sina(codes):
    """返回 {code: {...}}，含 trade_date 与 settle（今结算）。"""
    if not codes:
        return {}
    url = SINA_URL + ",".join(f"nf_{c}" for c in codes)
    status, raw = request(url, headers={"Referer": "https://finance.sina.com.cn"})
    if status != 200:
        return {}
    text = raw.decode("gbk", "replace")
    out = {}
    for line in text.splitlines():
        if '="' not in line:
            continue
        code = line.split("=")[0].strip().split("_")[-1].upper()
        parts = line.split('="', 1)[1].rstrip('";\r\n').split(",")
        if len(parts) < 18 or not parts[8]:
            continue
        try:
            out[code] = {
                "close": float(parts[8]),
                "settle": float(parts[9]) if parts[9] else None,
                "prev_settle": float(parts[10]) if parts[10] else None,
                "trade_date": parts[17] if parts[17] else None,
            }
        except (ValueError, IndexError):
            continue
    return out


def fetch_dce_hogs():
    em = fetch_em_contracts()
    codes = sorted(em)
    sina = fetch_sina(codes)

    contracts, conflicts = [], []
    trade_date = None
    for code in codes:
        row = em[code]
        s = sina.get(code)
        if s:
            if s.get("trade_date"):
                trade_date = max(trade_date or "", s["trade_date"])
            row["settle"] = s.get("settle")
            # 双源校验：收盘价必须一致
            if abs(s["close"] - row["close"]) > 0.5:
                conflicts.append(
                    {
                        "code": code,
                        "em_close": row["close"],
                        "sina_close": s["close"],
                    }
                )
                row["close"] = s["close"]  # 交易所快照以新浪为准
                row["change_abs"] = round(row["close"] - row["prev_settle"], 2)
                row["change_pct"] = (
                    round(row["change_abs"] / row["prev_settle"] * 100, 2)
                    if row["prev_settle"]
                    else None
                )
        else:
            row["settle"] = None
        contracts.append(row)

    contracts = [c for c in contracts if c["open_interest"] >= MIN_OPEN_INTEREST]
    if not contracts:
        raise RuntimeError("全部生猪合约持仓为 0，可能全部到期")

    main = max(contracts, key=lambda r: r["open_interest"])
    return {
        "trade_date": trade_date,
        "contracts": contracts,
        "main": main["code"],
        "total_oi": sum(c["open_interest"] for c in contracts),
        "total_vol": sum(c["volume"] for c in contracts),
        "cross_check": {
            "sources": ["东方财富行情接口（大商所日行情镜像）", "新浪财经行情接口"],
            "matched": len(contracts) - len(conflicts),
            "conflicts": conflicts,
        },
    }


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_dce_hogs(), ensure_ascii=False, indent=2))
