# -*- coding: utf-8 -*-
"""数据流水线：采集 → 归一化 → 指纹比对 → 仅在变化时改写看板。

运行：  python3 pipeline.py            # 采集并仅在变化时更新
        python3 pipeline.py --force    # 强制重建（用于改样式/改口径，不写 changelog）

退出码：0 = 正常（无论是否有变化）；1 = 全部数据源失败。
"""

import argparse
import os
import sys

import store
from config import INDICATORS

# ---------------------------------------------------------------------------
# 各模块归一化
# ---------------------------------------------------------------------------


def build_hog_spot():
    from collectors.spot import fetch_national_spot, fetch_spot_history, window_stats

    d = fetch_national_spot()
    cfg = INDICATORS["hog_spot"]

    # 日度历史序列：同发行人同口径，用于 7/30/90 日视角。
    # 该源失败不应影响当日报价展示，故单独降级。
    series, windows, hist_err = [], {}, None
    try:
        series = fetch_spot_history()
        windows = window_stats(series)
    except Exception as e:  # noqa: BLE001
        hist_err = f"{type(e).__name__}: {e}"

    return {
        "id": "hog_spot",
        "name": cfg["name"],
        "unit": cfg["unit"],
        "kind": cfg["kind"],
        "caliber": cfg["caliber"],
        "publisher": cfg["publisher"],
        "sample": cfg["sample"],
        "freq": cfg["freq"],
        "source_url": cfg["source_url"],
        "stat_date": d["stat_date"],
        "publish_date": d["stat_date"],
        "stale": False,
        "headline": {
            "value": d["value"],
            "prev_value": d["prev_value"],
            "prev_label": "前一报价日",
            "change_abs": d["change_abs"],
            "change_pct": d["change_pct"],
            "change_unit": cfg["unit"],
            "extra_chips": [
                {"label": "同比", "value": f"{d['yoy']:+.2f}%" if d.get("yoy") is not None else "—"},
                {"label": "环比", "value": f"{d['mom']:+.2f}%" if d.get("mom") is not None else "—"},
                {"label": "元/斤", "value": f"{d['value'] / 2:.2f}"},
            ],
        },
        "detail": {
            "extras": d["extras"],
            "series": [[r["date"], r["value"]] for r in series],
            "series_range": [series[0]["date"], series[-1]["date"]] if series else None,
            "windows": windows,
            "history_error": hist_err,
        },
        "notes": cfg["note"],
    }


def build_dce_futures():
    from collectors.futures import fetch_dce_hogs

    d = fetch_dce_hogs()
    cfg = INDICATORS["dce_futures"]
    main = next(c for c in d["contracts"] if c["code"] == d["main"])
    ups = sum(1 for c in d["contracts"] if c["change_abs"] > 0)
    downs = sum(1 for c in d["contracts"] if c["change_abs"] < 0)
    if ups and not downs:
        breadth = f"{ups}涨0跌"
    elif downs and not ups:
        breadth = f"0涨{downs}跌"
    else:
        breadth = f"{ups}涨{downs}跌"
    return {
        "id": "dce_futures",
        "name": cfg["name"],
        "unit": cfg["unit"],
        "kind": cfg["kind"],
        "caliber": cfg["caliber"],
        "publisher": cfg["publisher"],
        "sample": f"{len(d['contracts'])} 个活跃合约",
        "freq": cfg["freq"],
        "source_url": cfg["source_url"],
        "stat_date": d["trade_date"],
        "publish_date": d["trade_date"],
        "stale": False,
        "headline": {
            "value": main["close"],
            "prev_value": main["prev_settle"],
            "prev_label": f"主力 {main['code']} 昨结算",
            "change_abs": main["change_abs"],
            "change_pct": main["change_pct"],
            "change_unit": cfg["unit"],
            "extra_chips": [
                {"label": "主力合约", "value": main["code"]},
                {"label": "涨跌家数", "value": breadth},
                {"label": "总持仓", "value": f"{d['total_oi']:,}"},
            ],
        },
        "detail": {
            "contracts": d["contracts"],
            "main": d["main"],
            "total_oi": d["total_oi"],
            "total_vol": d["total_vol"],
            "cross_check": d["cross_check"],
        },
        "notes": cfg["note"],
    }


def build_sow_inventory():
    reg = store.load_registry("sow_inventory")
    points = {p["stat_date"]: dict(p) for p in reg["points"]}
    auto_note = []

    # 叠加农业农村部官方 XLSX 自动扫描结果：
    # 一旦新季度官方发布，这里会自动带出新数据点 -> 哈希变化 -> 看板更新并留痕。
    try:
        from collectors.moa import fetch_official_series

        official = fetch_official_series()
        added, conflicts = 0, []
        for p in official.get("points", []):
            sd = p["stat_date"]
            if sd in points:
                if points[sd].get("value") != p.get("value"):
                    conflicts.append(
                        {
                            "stat_date": sd,
                            "registry_value": points[sd].get("value"),
                            "official_value": p.get("value"),
                        }
                    )
                continue
            val = p["value"]
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            points[sd] = {
                "id": f"moa-auto-{sd}",
                "period_label": p["period_label"],
                "stat_date": sd,
                "value": val,
                "value_raw": p.get("value_raw"),
                "yoy_pct": p.get("yoy_pct"),
                "yoy_abs": None,
                "mom_pct_official": p.get("mom_pct_official"),
                "mom_abs": None,
                "mom_pct_computed": None,
                "pct_of_benchmark": p.get("pct_of_benchmark"),
                "publisher": p.get("publisher"),
                "publish_date": p.get("publish_date"),
                "source_title": p.get("source_title"),
                "source_url": p.get("source_url"),
                "source_file": p.get("xlsx_url"),
                "kind": "official",
                "confidence": "high",
                "auto": True,
                "note": "由官方《生猪产品数据》XLSX 自动抓取并解析；发布日期取自官方文件名日期段。",
            }
            added += 1
        if added:
            auto_note.append(f"官方自动源新增 {added} 个季度观测")
        if conflicts:
            auto_note.append(f"⚠ {len(conflicts)} 个季度官方值与登记值不一致，需人工复核")
    except Exception as e:  # noqa: BLE001 - 官方源失败不应影响已有登记数据
        auto_note.append(f"官方自动源本次不可用（{type(e).__name__}），使用登记库已有数据：{e}")
        conflicts = []

    pts = sorted(points.values(), key=lambda p: p["stat_date"])
    latest = pts[-1]
    prev = pts[-2] if len(pts) > 1 else None
    cfg = INDICATORS["sow_inventory"]
    return {
        "id": "sow_inventory",
        "name": cfg["name"],
        "unit": cfg["unit"],
        "kind": cfg["kind"],
        "caliber": reg["main_caliber"],
        "publisher": reg["publisher"],
        "sample": cfg["sample"],
        "freq": cfg["freq"],
        "source_url": latest["source_url"],
        "stat_date": latest["stat_date"],
        "publish_date": latest["publish_date"],
        "stale": False,
        "headline": {
            "value": latest["value"],
            "prev_value": prev["value"] if prev else None,
            "prev_label": f"{prev['period_label']}" if prev else "—",
            "change_abs": (latest["value"] - prev["value"]) if prev else None,
            "change_pct": latest.get("mom_pct_official"),
            "change_unit": cfg["unit"],
            "extra_chips": [
                {"label": "同比", "value": f"{latest['yoy_pct']:+.1f}%" if latest.get("yoy_pct") is not None else "—"},
                {"label": "环比(官方)", "value": f"{latest['mom_pct_official']:+.1f}%" if latest.get("mom_pct_official") is not None else "—"},
                {"label": "占保有量", "value": f"{latest['pct_of_benchmark']:.1f}%" if latest.get("pct_of_benchmark") is not None else "—"},
            ],
        },
        "detail": {
            "points": pts,
            "benchmark": reg["benchmark"],
            "release_schedule": reg["release_schedule"],
            "publisher_note": reg["publisher_note"],
            "moa_monthly_rule": reg["moa_monthly_rule"],
            "sample_watch": reg.get("sample_watch"),
            "corrections": reg.get("corrections", []),
            "auto_note": auto_note,
            "conflicts": conflicts,
            "official_index": "https://www.moa.gov.cn/ztzl/szcpxx/jdsj/",
        },
        "notes": cfg["note"],
    }


def build_frozen_inventory():
    reg = store.load_registry("frozen_inventory")
    cfg = INDICATORS["frozen_inventory"]
    primary = reg["series"][0]
    latest = primary["latest"]
    return {
        "id": "frozen_inventory",
        "name": cfg["name"],
        "unit": cfg["unit"],
        "kind": cfg["kind"],
        "caliber": cfg["caliber"],
        "publisher": primary["publisher"],
        "sample": primary["sample"],
        "freq": cfg["freq"],
        "source_url": latest["source_url"],
        "stat_date": latest["stat_date"],
        "publish_date": latest["publish_date"],
        "stale": False,
        "third_party": True,
        "headline": {
            "value": latest["value"],
            "prev_value": None,
            "prev_label": "—",
            "change_abs": None,
            "change_pct": None,
            "change_unit": "个百分点",
            "extra_chips": [
                {"label": "同比", "value": f"{latest['yoy_pp']:+.1f}pp" if latest.get("yoy_pp") is not None else "—"},
                {"label": "口径", "value": "第三方样本"},
                {"label": "频率", "value": "周度"},
            ],
        },
        "detail": {
            "definition": reg["definition"],
            "warning": reg["warning"],
            "series": reg["series"],
            "history": reg.get("history", []),
            "conflicts": reg.get("conflicts", []),
            "known_gaps": reg.get("known_gaps", []),
        },
        "notes": cfg["note"],
        "disclaimer": reg.get("warning"),
    }


def build_cull_sow_ratio():
    reg = store.load_registry("cull_sow_ratio")
    cfg = INDICATORS["cull_sow_ratio"]
    pts = sorted(reg["points"], key=lambda p: p["stat_date"])
    latest = pts[-1]
    return {
        "id": "cull_sow_ratio",
        "name": cfg["name"],
        "unit": cfg["unit"],
        "kind": cfg["kind"],
        "caliber": cfg["caliber"],
        "publisher": latest["publisher"],
        "sample": cfg["sample"],
        "freq": cfg["freq"],
        "source_url": latest["source_url"],
        "stat_date": latest["stat_date"],
        "publish_date": latest["publish_date"],
        "stale": False,
        "third_party": True,
        "headline": {
            "value": latest["ratio_pct"],
            "prev_value": None,
            "prev_label": "—",
            "change_abs": None,
            "change_pct": None,
            "change_unit": "个百分点",
            "extra_chips": [
                {"label": "淘汰母猪", "value": f"{latest['cull_sow_price']:.2f} 元/公斤"},
                {"label": "商品猪", "value": f"{latest['commercial_hog_price']:.2f} 元/公斤"},
                {"label": "口径", "value": "第三方样本"},
            ],
        },
        "detail": {
            "formula": reg["formula"],
            "interpretation": reg["interpretation"],
            "points": pts,
            "trend": reg.get("trend", []),
            "reference_points": reg.get("reference_points", []),
            "supporting": reg.get("supporting", []),
            "disclaimer": reg["disclaimer"],
        },
        "notes": cfg["note"],
    }


BUILDERS = [
    ("hog_spot", build_hog_spot),
    ("dce_futures", build_dce_futures),
    ("sow_inventory", build_sow_inventory),
    ("frozen_inventory", build_frozen_inventory),
    ("cull_sow_ratio", build_cull_sow_ratio),
]

# 计算指纹时必须剔除的易变/自指字段：
#   checked_at / error_detail —— 每次运行都变，不应触发改写
#   value_hash / hash_changed —— 快照自带的元字段，若参与哈希会形成自指，
#                               导致沿用上一期数据时每轮都被误判为"有变化"
VOLATILE_KEYS = {
    "checked_at", "error_detail", "generated_at", "fetched_at",
    "value_hash", "hash_changed",
}


def _stable(module):
    return {k: v for k, v in module.items() if k not in VOLATILE_KEYS}


# ---------------------------------------------------------------------------
# 变化说明（简短原因分析）
# ---------------------------------------------------------------------------

def is_stale_only(new, old):
    """判断本次变化是否只是「数据源异常 -> 沿用上一期」的状态翻转，而非数据本身更新。"""
    if not new.get("stale") or not old:
        return False
    nh, oh = new.get("headline", {}), old.get("headline", {})
    return (
        nh.get("value") == oh.get("value")
        and new.get("stat_date") == old.get("stat_date")
    )


def is_recovery(new, old):
    """上一期是异常沿用、本期恢复采集，且数值未变 —— 属于数据源恢复而非数据更新。"""
    if not old or new.get("stale"):
        return False
    if old.get("stale") is not True:
        return False
    return (
        new.get("headline", {}).get("value") == old.get("headline", {}).get("value")
        and new.get("stat_date") == old.get("stat_date")
    )


def describe_change(mid, new, old):
    """生成简短、只陈述事实的变化说明，不编造未经证实的市场原因。"""
    h, oh = new.get("headline", {}), (old or {}).get("headline", {})

    if old is None:
        return f"首次纳入看板：{new['name']} 当前值 {h.get('value')} {new['unit']}。"

    if is_stale_only(new, old):
        return (
            f"数据源异常，看板已沿用上一期数据（统计日 {new.get('stat_date')}，"
            f"值 {h.get('value')} {new['unit']} 未变），仅刷新告警状态：{new.get('error_detail', '')}"
        )

    if is_recovery(new, old):
        return (
            f"数据源已恢复正常采集（统计日 {new.get('stat_date')}，值未变 "
            f"{h.get('value')} {new['unit']}），解除上一期的异常告警。"
        )

    if h.get("value") == oh.get("value") and new.get("stat_date") == old.get("stat_date"):
        return (
            f"{new['name']} 当期值未变（{h.get('value')} {new['unit']}，统计日 {new.get('stat_date')}），"
            f"但明细/历史序列被上游修订或补充，已同步更新。"
        )

    if mid == "hog_spot":
        return (
            f"全国外三元均价 {h['value']} 元/公斤，较前一报价日 "
            f"{h['change_abs']:+.2f} 元（{h['change_pct']:+.2f}%）；"
            f"统计日 {new['stat_date']}。"
        )
    if mid == "dce_futures":
        cs = new["detail"]["contracts"]
        up = sum(1 for c in cs if c["change_abs"] > 0)
        dn = sum(1 for c in cs if c["change_abs"] < 0)
        return (
            f"大商所 {len(cs)} 个活跃生猪合约全部更新：{up} 涨 {dn} 跌。"
            f"主力 {new['detail']['main']} 收 {h['value']} 元/吨，"
            f"较昨结算 {h['change_abs']:+.0f}（{h['change_pct']:+.2f}%）；"
            f"交易日 {new['stat_date']}。"
        )
    if mid == "sow_inventory":
        return (
            f"官方发布新一期能繁母猪存栏：{new['stat_date']}（{new['publish_date']} 发布）"
            f"为 {h['value']} 万头，环比 {h.get('change_pct')}%，同比 {h['extra_chips'][0]['value']}。"
        )
    if mid == "frozen_inventory":
        return (
            f"第三方样本冻品库容率更新为 {h['value']}%，"
            f"机构 {new['publisher']}，统计日 {new['stat_date']}，发布日 {new['publish_date']}。"
        )
    if mid == "cull_sow_ratio":
        return (
            f"淘汰母猪/商品猪价格比更新为 {h['value']}%（"
            f"{new['detail']['points'][-1]['cull_sow_price']} ÷ "
            f"{new['detail']['points'][-1]['commercial_hog_price']}），"
            f"统计日 {new['stat_date']}。"
        )
    return f"{new['name']} 数据更新。"


# ---------------------------------------------------------------------------
# 历史序列落盘
# ---------------------------------------------------------------------------

def _has_data(m):
    """模块是否带有可用数据（首次运行即采集失败的模块不应写入历史）。"""
    return bool(m) and m.get("headline", {}).get("value") is not None and m.get("stat_date")


def record_history(snapshot):
    results = {}
    mods = snapshot["modules"]

    if _has_data(mods.get("hog_spot")):
        m = mods["hog_spot"]
        h = m["headline"]
        series = m.get("detail", {}).get("series") or []
        rows = []
        prev_v = None
        for d, v in series:
            chg = round(v - prev_v, 4) if prev_v is not None else None
            rows.append(
                {
                    "date": d,
                    "value": v,
                    "prev_value": prev_v if prev_v is not None else "",
                    "change_abs": chg if chg is not None else "",
                    "change_pct": (round(chg / prev_v * 100, 2) if prev_v else ""),
                    "unit": m["unit"],
                    "source": m["publisher"],
                    "collected_at": snapshot["generated_at"],
                }
            )
            prev_v = v

        # 兜底：历史源不可用时至少保底当日点位
        if not rows:
            rows = [
                {
                    "date": m["stat_date"],
                    "value": h["value"],
                    "prev_value": h["prev_value"],
                    "change_abs": h["change_abs"],
                    "change_pct": h["change_pct"],
                    "unit": m["unit"],
                    "source": m["publisher"],
                    "collected_at": snapshot["generated_at"],
                }
            ]

        st = store.bulk_upsert_history(
            "hog_spot_daily.csv",
            ["date"],
            rows,
            ["value", "prev_value", "change_abs", "change_pct"],
        )
        results["hog_spot"] = st["status"]
        if st["revised"]:
            results["hog_spot_revised"] = st["revised"]

    if _has_data(mods.get("dce_futures")):
        m = mods["dce_futures"]
        stats = []
        for c in m["detail"]["contracts"]:
            stats.append(
                store.append_history(
                    "dce_futures_daily.csv",
                    ["date", "code"],
                    {
                        "date": m["stat_date"],
                        "code": c["code"],
                        "close": c["close"],
                        "settle": c["settle"],
                        "prev_settle": c["prev_settle"],
                        "change_abs": c["change_abs"],
                        "change_pct": c["change_pct"],
                        "volume": c["volume"],
                        "open_interest": c["open_interest"],
                        "source": m["publisher"],
                    },
                    ["close", "settle", "change_abs", "change_pct", "volume", "open_interest"],
                )
            )
        results["dce_futures"] = "inserted" if "inserted" in stats else ("revised" if "revised" in stats else "unchanged")

    if _has_data(mods.get("sow_inventory")):
        m = mods["sow_inventory"]
        stats = []
        for p in m["detail"]["points"]:
            stats.append(
                store.append_history(
                    "sow_inventory.csv",
                    ["stat_date"],
                    {
                        "stat_date": p["stat_date"],
                        "period_label": p["period_label"],
                        "value": p["value"],
                        "unit": m["unit"],
                        "yoy_pct": p.get("yoy_pct"),
                        "mom_pct_official": p.get("mom_pct_official"),
                        "mom_pct_computed": p.get("mom_pct_computed"),
                        "pct_of_benchmark": p.get("pct_of_benchmark"),
                        "publisher": p["publisher"],
                        "publish_date": p["publish_date"],
                        "source_url": p["source_url"],
                    },
                    ["value", "yoy_pct", "mom_pct_official"],
                )
            )
        results["sow_inventory"] = "inserted" if "inserted" in stats else ("revised" if "revised" in stats else "unchanged")

    if _has_data(mods.get("frozen_inventory")):
        stats = []
        meta = {s["id"]: s for s in mods["frozen_inventory"]["detail"].get("series", [])}
        hist = mods["frozen_inventory"]["detail"].get("history") or []
        if not hist:
            hist = [dict(p, publisher_id=s["id"]) for s in meta.values()
                    for p in [s["latest"]] + list(s.get("points", []))]
        for p in hist:
            sid = p.get("publisher_id")
            sm = meta.get(sid, {})
            stats.append(
                store.append_history(
                    "frozen_inventory.csv",
                    ["series_id", "stat_date"],
                    {
                        "series_id": sid,
                        "publisher": p.get("publisher") or sm.get("publisher"),
                        "sample": sm.get("sample"),
                        "period_label": p.get("period_label"),
                        "stat_date": p["stat_date"],
                        "value": p["value"],
                        "unit": "%",
                        "publish_date": p["publish_date"],
                        "source_url": p["source_url"],
                    },
                    ["value"],
                )
            )
        results["frozen_inventory"] = "inserted" if "inserted" in stats else ("revised" if "revised" in stats else "unchanged")

    if _has_data(mods.get("cull_sow_ratio")):
        stats = []
        cull_rows, seen_cull = [], set()
        for p in (mods["cull_sow_ratio"]["detail"].get("points", [])
                  + mods["cull_sow_ratio"]["detail"].get("trend", [])):
            if p["stat_date"] in seen_cull:
                continue
            seen_cull.add(p["stat_date"])
            cull_rows.append(p)
        for p in cull_rows:
            stats.append(
                store.append_history(
                    "cull_sow_ratio.csv",
                    ["stat_date"],
                    {
                        "stat_date": p["stat_date"],
                        "cull_sow_price": p["cull_sow_price"],
                        "commercial_hog_price": p["commercial_hog_price"],
                        "commercial_hog_price_date": p.get("commercial_hog_price_date"),
                        "ratio_pct": p["ratio_pct"],
                        "cull_sow_wow_pct": p.get("cull_sow_wow_pct"),
                        "unit": "元/公斤",
                        "publisher": p["publisher"],
                        "publish_date": p["publish_date"],
                        "source_url": p["source_url"],
                    },
                    ["cull_sow_price", "commercial_hog_price", "ratio_pct"],
                )
            )
        results["cull_sow_ratio"] = "inserted" if "inserted" in stats else ("revised" if "revised" in stats else "unchanged")

    return results


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _start_watchdog(seconds):
    """硬超时：SSL 握手可能无视 socket 超时，用守护线程强制退出，避免定时任务永久挂起。"""
    import threading
    import time

    def _kill():
        time.sleep(seconds)
        sys.stderr.write(f"[FATAL] 采集超过 {seconds}s，强制退出（看板保持上一期）\n")
        sys.stderr.flush()
        os._exit(2)

    threading.Thread(target=_kill, daemon=True).start()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重建看板（不写 changelog）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    store.ensure_dirs()
    try:
        _start_watchdog(int(os.environ.get("PIGBOARD_MAX_SECONDS", "300")))
    except ValueError:
        _start_watchdog(300)
    started = store.now_cst()

    # 公开渠道自动扫描：冻品库容率 / 淘汰母猪月度均价。
    # 只在发现「新点位」时改写登记库；失败静默降级，绝不阻断主流程。
    auto_info = None
    if os.environ.get("PIGBOARD_NO_AUTO") == "1":
        auto_info = {"ok": True, "skipped": True}
    else:
        try:
            from collectors import thirdparty_auto
            auto_info = thirdparty_auto.run()
            added = auto_info.get("frozen_added", 0) + auto_info.get("cull_added", 0)
            if added and not args.quiet:
                print(f"  公开渠道自动补充点位：冻品 +{auto_info.get('frozen_added', 0)} / 比价 +{auto_info.get('cull_added', 0)}")
        except Exception as e:  # noqa: BLE001
            auto_info = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    previous = store.load_snapshot() or {"modules": {}}
    prev_mods = previous.get("modules", {})

    modules, errors = {}, {}
    for mid, builder in BUILDERS:
        try:
            modules[mid] = builder()
        except Exception as e:  # noqa: BLE001 - 单源失败不应拖垮整块看板
            errors[mid] = f"{type(e).__name__}: {e}"
            if mid in prev_mods:
                carry = dict(prev_mods[mid])
                carry["stale"] = True
                carry["checked_at"] = store.iso()
                carry["error_detail"] = errors[mid]
                modules[mid] = carry
            else:
                modules[mid] = {
                    "id": mid,
                    "name": INDICATORS[mid]["name"],
                    "unit": INDICATORS[mid]["unit"],
                    "kind": INDICATORS[mid]["kind"],
                    "caliber": INDICATORS[mid]["caliber"],
                    "publisher": INDICATORS[mid]["publisher"],
                    "sample": INDICATORS[mid].get("sample", ""),
                    "freq": INDICATORS[mid]["freq"],
                    "source_url": INDICATORS[mid]["source_url"],
                    "stat_date": None,
                    "publish_date": None,
                    "stale": True,
                    "headline": {"value": None},
                    "detail": {},
                    "notes": INDICATORS[mid]["note"],
                    "error_detail": errors[mid],
                    "checked_at": store.iso(),
                }

    if len(errors) == len(BUILDERS):
        print("[FATAL] 全部数据源失败：")
        for k, v in errors.items():
            print(f"  - {k}: {v}")
        return 1

    # 逐模块指纹比对
    changed = []
    for mid, mod in modules.items():
        new_hash = store.value_hash(_stable(mod))
        old = prev_mods.get(mid)
        old_hash = old.get("value_hash") if old else None
        mod["value_hash"] = new_hash
        mod["hash_changed"] = new_hash != old_hash
        if new_hash != old_hash:
            changed.append(mid)

    snapshot = {
        "schema": store.SCHEMA_VERSION,
        "project": "生猪市场看板",
        "generated_at": store.iso(),
        "module_order": [m for m, _ in BUILDERS],
        "modules": modules,
        "source_errors": errors,
    }

    # 历史序列落盘（先落盘，修订也会被计入变化）
    hist = record_history(snapshot)
    for mid, status in hist.items():
        if status in ("inserted", "revised") and mid not in changed:
            changed.append(mid)
            modules[mid]["hash_changed"] = True

    no_change = not changed and not args.force

    if no_change:
        if not args.quiet:
            print(f"[{store.iso()}] 无新数据，看板不改动。")
            if errors:
                for k, v in errors.items():
                    print(f"  [warn] {k} 采集失败，沿用上一期：{v}")
        store.write_heartbeat(
            {
                "last_check": store.iso(),
                "last_check_duration_s": round((store.now_cst() - started).total_seconds(), 2),
                "data_changed": False,
                "last_data_change": previous.get("generated_at"),
                "snapshot_generated_at": previous.get("generated_at"),
                "source_errors": errors,
                "auto_update": auto_info,
                "history": hist,
                "checked_modules": list(modules.keys()),
                "schedule": "07:45 / 09:30 / 12:30 / 15:30 / 17:30 / 21:30",
            }
        )
        return 0

    # 有变化：写快照 + changelog
    store.save_snapshot(snapshot)

    entries = []
    if not args.force:
        for mid in changed:
            mod = modules[mid]
            raw_old = prev_mods.get(mid)
            old = None
            if raw_old and raw_old.get("value_hash") != mod.get("value_hash"):
                old = raw_old
            entries.append(
                {
                    "ts": snapshot["generated_at"],
                    "module": mid,
                    "module_name": mod["name"],
                    "kind": mod["kind"],
                    "stat_date": mod.get("stat_date"),
                    "publish_date": mod.get("publish_date"),
                    "value": mod.get("headline", {}).get("value"),
                    "unit": mod.get("unit"),
                    "prev_value": (old or {}).get("headline", {}).get("value"),
                    "source": mod.get("publisher"),
                    "source_url": mod.get("source_url"),
                    "change_kind": (
                        "source_error" if is_stale_only(mod, old)
                        else "source_recovered" if is_recovery(mod, old)
                        else "first_seen" if old is None
                        else "data_update"
                    ),
                    "summary": describe_change(mid, mod, old),
                    "analysis": (
                        "口径化差异说明（自动生成，仅陈述数值与已登记的机制性备注，不对市场原因作推断）："
                        + describe_change(mid, mod, old)
                        + (" 备注：" + mod["notes"] if mod.get("notes") else "")
                    ),
                }
            )
        store.append_changelog(entries)

    from build import build_dashboard

    build_dashboard(snapshot)

    store.write_heartbeat(
        {
            "last_check": store.iso(),
            "last_check_duration_s": round((store.now_cst() - started).total_seconds(), 2),
            "data_changed": True,
            "changed_modules": changed,
            "last_data_change": snapshot["generated_at"],
            "snapshot_generated_at": snapshot["generated_at"],
            "source_errors": errors,
            "history": hist,
            "schedule": "07:45 / 09:30 / 12:30 / 15:30 / 17:30 / 21:30",
        }
    )

    if not args.quiet:
        print(f"[{snapshot['generated_at']}] 检测到 {len(changed)} 个模块变化：{', '.join(changed)}")
        for e in entries:
            print("  · " + e["summary"])
        for k, v in errors.items():
            print(f"  [warn] {k} 采集失败，沿用上一期：{v}")
        print("看板已重建：web/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
