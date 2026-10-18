# -*- coding: utf-8 -*-
"""看板生成：把 snapshot.json 渲染为自包含的 web/index.html。

不依赖任何前端框架与 CDN，纯静态文件，可在内网/离线环境长期私有部署。
"""

import csv
import json
import os

import store

KIND_LABEL = {
    "official": ("官方发布", "official"),
    "third_party": ("第三方样本", "thirdparty"),
    "market": ("市场行情", "market"),
}


def esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def fmt_num(v, nd=2):
    if v is None or v == "":
        return "—"
    if isinstance(v, (int, float)):
        if abs(v) >= 1000 and float(v).is_integer():
            return f"{int(v):,}"
        return f"{v:,.{nd}f}".rstrip("0").rstrip(".") if nd else f"{v:,.0f}"
    return str(v)


def cls_of(x):
    if x is None:
        return "flat"
    return "up" if x > 0 else ("down" if x < 0 else "flat")


def arrow(x):
    if x is None:
        return ""
    return "▲" if x > 0 else ("▼" if x < 0 else "＝")


def futures_market_state(trade_date):
    """判断期货数据是「已收盘结算」还是「盘中最新价」，避免把盘中价误标成收盘价。"""
    now = store.now_cst()
    today = now.strftime("%Y-%m-%d")
    if not trade_date:
        return ("数据日期未知", "flat", "")
    if trade_date > today:
        return ("数据日期异常", "warn", "接口返回的交易日晚于本机日期，请核对时区或数据源。")
    if trade_date < today:
        return ("已收盘结算", "closed", "该交易日已收盘，表中为当日收盘价与结算价。")
    t = now.hour * 60 + now.minute
    if t < 9 * 60:
        return ("尚未开盘", "flat", "当日尚未开盘，展示的是上一交易日收盘数据。")
    if t < 15 * 60:
        return (
            "盘中最新价 · 尚未收盘",
            "live",
            "当前处于交易时段（09:00–11:30、13:30–15:00），表中为最新成交价而非当日收盘价；"
            "15:30 巡检会自动替换为真实收盘价与结算价。",
        )
    return ("已收盘结算", "closed", "该交易日已收盘，表中为当日收盘价与结算价。")


def read_csv_rows(name):
    path = os.path.join(store.HISTORY, name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def sparkline(points, width=520, height=90, color="#c0392b"):
    """极简 SVG 折线图。points = [(label, value), ...]"""
    pts = [(l, float(v)) for l, v in points if v not in (None, "")]
    if len(pts) < 2:
        return '<div class="spark-empty">历史样本不足（看板按日累积，需 ≥2 个数据点后显示走势）</div>'
    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pad = 8
    step = (width - pad * 2) / (len(pts) - 1)
    coords = []
    for i, (_, v) in enumerate(pts):
        x = pad + i * step
        y = height - pad - (v - lo) / rng * (height - pad * 2)
        coords.append((x, y))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{pad},{height - pad} " + poly + f" {coords[-1][0]:.1f},{height - pad}"
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{color}"/>' for x, y in coords[-1:])
    return f"""<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img">
  <polygon points="{area}" fill="{color}" opacity="0.10"/>
  <polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>
  {dots}
</svg>
<div class="spark-axis"><span>{esc(pts[0][0])}</span><span>低 {fmt_num(lo)} / 高 {fmt_num(hi)}</span><span>{esc(pts[-1][0])}</span></div>"""


def chip(label, value, tone=""):
    return f'<span class="chip {tone}"><b>{esc(label)}</b>{esc(value)}</span>'


# ---------------------------------------------------------------------------
# 各模块卡片
# ---------------------------------------------------------------------------

def card_hog_spot(m):
    h = m["headline"]
    d = m["detail"]
    rows = [
        ("内三元", d["extras"].get("内三元")),
        ("土杂猪", d["extras"].get("土杂猪")),
    ]
    side = "".join(
        f'<div class="mini"><span class="mini-l">{esc(k)}</span>'
        f'<span class="mini-v">{fmt_num(v["value"])}</span>'
        f'<span class="mini-c {cls_of(v["change"])}">{arrow(v["change"])}{fmt_num(abs(v["change"]))}</span></div>'
        for k, v in rows
        if v
    )

    series = d.get("series") or []
    wins = d.get("windows") or {}
    if series:
        rng = d.get("series_range") or [series[0][0], series[-1][0]]
        chart_html = f"""
  <div class="range-bar">
    <div class="range-tabs" role="tablist" aria-label="时间区间">
      <button type="button" data-w="7" role="tab">近 7 日</button>
      <button type="button" data-w="30" role="tab" class="on">近 30 日</button>
      <button type="button" data-w="90" role="tab">近 90 日</button>
    </div>
    <div class="range-hint">点按曲线上的任意一点查看当日价格</div>
  </div>
  <div class="chart-box">
    <svg id="spotChart" viewBox="0 0 820 300" preserveAspectRatio="none" aria-label="外三元均价走势"></svg>
    <div class="chart-tip" id="spotTip" hidden></div>
  </div>
  <div class="range-stats" id="spotStats"></div>
  <p class="chart-src">序列区间 {esc(rng[0])} → {esc(rng[1])}，共 {len(series)} 个日度点位；同期同发行人同口径。</p>"""
    else:
        chart_html = (
            '<div class="spark-empty">历史日线暂不可用'
            + (f'（{esc(d.get("history_error") or "")}）' if d.get("history_error") else "")
            + "；当日报价不受影响，7/30/90 日视角将在历史源恢复后自动出现。</div>"
        )

    return f"""
<section class="card wide">
  <header class="card-h">
    <div><h3>{esc(m['name'])}</h3><span class="badge market">市场行情</span></div>
    <div class="asof">统计日 <b>{esc(m['stat_date'] or '—')}</b></div>
  </header>
  <div class="hero">
    <div class="hero-v">{fmt_num(h['value'])}<span class="unit">{esc(m['unit'])}</span></div>
    <div class="hero-d {cls_of(h['change_abs'])}">
      {arrow(h['change_abs'])} {fmt_num(abs(h['change_abs']))} {esc(m['unit'])}
      <small>（{h['change_pct']:+.2f}%）</small>
    </div>
    <div class="hero-p">较前一报价日 {fmt_num(h['prev_value'])} {esc(m['unit'])}</div>
  </div>
  <div class="chips">{''.join(chip(c['label'], c['value']) for c in h['extra_chips'])}</div>
  {window_chips(wins)}
  {chart_html}
  <div class="mini-row">{side}</div>
  <details class="src">
    <summary>口径与来源</summary>
    <dl>
      <dt>统计口径</dt><dd>{esc(m['caliber'])}</dd>
      <dt>发布主体</dt><dd>{esc(m['publisher'])}</dd>
      <dt>样本范围</dt><dd>{esc(m['sample'])}</dd>
      <dt>更新频率</dt><dd>{esc(m['freq'])}</dd>
      <dt>单日对比</dt><dd>涨跌额 = 当期值 − 上一有效报价日值；幅度 = 涨跌额 ÷ 上期值</dd>
      <dt>区间对比</dt><dd>N 日变化 = 当期值 − N 个日历日前那一报价日的值（序列无断点，即精确 N 日前）</dd>
      <dt>历史序列</dt><dd>{esc(d.get('series_range')[0]) if d.get('series_range') else '—'} → {esc(d.get('series_range')[1]) if d.get('series_range') else '—'}，{len(series)} 个日度点位，取自同一接口的「历史」数据集</dd>
      <dt>数据源</dt><dd><a href="{esc(m['source_url'])}" target="_blank" rel="noreferrer">{esc(m['source_url'])}</a></dd>
      <dt>备注</dt><dd>{esc(m['notes'])}</dd>
    </dl>
  </details>
</section>
<script id="spotData" type="application/json">{json.dumps({'series': series, 'windows': wins}, ensure_ascii=False)}</script>"""


def window_chips(wins):
    """7/30/90 日变化摘要，放在主数字下方。"""
    out = []
    for n in (7, 30, 90):
        w = (wins or {}).get(str(n)) or (wins or {}).get(n)
        if not w or not w.get("available"):
            out.append(
                f'<span class="wchip flat"><b>近{n}日</b><span class="wv">—</span><i>—</i></span>'
            )
            continue
        k = cls_of(w["change_abs"])
        out.append(
            f'<span class="wchip {k}"><b>近{n}日</b>'
            f'<span class="wv">{arrow(w["change_abs"])}{fmt_num(abs(w["change_abs"]))}</span>'
            f'<i>{w["change_pct"]:+.2f}%</i></span>'
        )
    return f'<div class="wchips">{"".join(out)}</div>'


def card_futures(m):
    cs = m["detail"]["contracts"]
    main = m["detail"]["main"]
    state_label, state_cls, state_note = futures_market_state(m.get("stat_date"))
    state_html = (
        f'<div class="mkt-state {state_cls}"><b>{esc(state_label)}</b>'
        + (f'<span>{esc(state_note)}</span>' if state_note else "")
        + "</div>"
    )
    rows = ""
    for c in cs:
        k = cls_of(c["change_abs"])
        rows += (
            f'<tr class="{"is-main" if c["code"] == main else ""}">'
            f'<td class="mono">{c["code"]}</td>'
            f'<td class="num">{fmt_num(c["close"], 0)}</td>'
            f'<td class="num">{fmt_num(c["settle"], 0) if c["settle"] is not None else "—"}</td>'
            f'<td class="num">{fmt_num(c["prev_settle"], 0)}</td>'
            f'<td class="num {k}">{arrow(c["change_abs"])} {fmt_num(abs(c["change_abs"]), 0)}</td>'
            f'<td class="num {k}">{c["change_pct"]:+.2f}%</td>'
            f'<td class="num">{fmt_num(c["volume"], 0)}</td>'
            f'<td class="num">{fmt_num(c["open_interest"], 0)}</td>'
            f"</tr>"
        )
    cc = m["detail"]["cross_check"]
    cc_note = (
        f'双源交叉校验：{cc["matched"]}/{len(cs)} 个合约收盘价一致'
        + (f'，<b class="warn">{len(cc["conflicts"])} 个存在分歧</b>' if cc["conflicts"] else "，无分歧")
    )
    hist = read_csv_rows("dce_futures_daily.csv")
    main_hist = [("{}".format(r["date"]), r["close"]) for r in hist if r["code"] == main][-60:]
    spark = sparkline(main_hist, color="#2c7fb8")
    return f"""
<section class="card wide">
  <header class="card-h">
    <div><h3>{esc(m['name'])}</h3><span class="badge market">市场行情</span></div>
    <div class="asof">交易日 <b>{esc(m['stat_date'] or '—')}</b></div>
  </header>
  <div class="hero compact">
    <div class="hero-v">{fmt_num(m['headline']['value'], 0)}<span class="unit">{esc(m['unit'])}</span></div>
    <div class="hero-d {cls_of(m['headline']['change_abs'])}">
      {arrow(m['headline']['change_abs'])} {fmt_num(abs(m['headline']['change_abs']), 0)}
      <small>（{m['headline']['change_pct']:+.2f}%）</small>
    </div>
    <div class="hero-p">主力合约 {esc(main)}，对比昨结算 {fmt_num(m['headline']['prev_value'], 0)} {esc(m['unit'])}</div>
  </div>
  {state_html}
  <div class="chips">{''.join(chip(c['label'], c['value']) for c in m['headline']['extra_chips'])}</div>
  <div class="tbl-wrap">
    <table class="tbl">
      <thead><tr>
        <th>合约</th><th>{'最新价' if state_cls == 'live' else '收盘价'}</th><th>结算价</th><th>昨结算</th><th>涨跌</th><th>幅度</th><th>成交量</th><th>持仓量</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="spark-wrap">{spark}</div>
  <p class="inline-note">{cc_note}。收盘价用于看盈亏方向，<b>结算价</b>才是交易所划转保证金与计算下一交易日涨跌停板的基准。</p>
  <details class="src">
    <summary>口径与来源</summary>
    <dl>
      <dt>统计口径</dt><dd>{esc(m['caliber'])}</dd>
      <dt>发布主体</dt><dd>{esc(m['publisher'])}</dd>
      <dt>合约范围</dt><dd>{esc(m['sample'])}（按持仓量滚动，摘牌自动剔除）</dd>
      <dt>更新频率</dt><dd>{esc(m['freq'])}</dd>
      <dt>对比规则</dt><dd>涨跌 = 当日收盘价 − 上一交易日结算价；幅度 = 涨跌 ÷ 上一交易日结算价</dd>
      <dt>交叉校验</dt><dd>{esc('、'.join(cc['sources']))}</dd>
      <dt>数据源</dt><dd><a href="{esc(m['source_url'])}" target="_blank" rel="noreferrer">{esc(m['source_url'])}</a></dd>
      <dt>备注</dt><dd>{esc(m['notes'])}</dd>
    </dl>
  </details>
</section>"""


def card_sow(m):
    h = m["headline"]
    bm = m["detail"]["benchmark"]
    pts = m["detail"]["points"]
    pct = pts[-1].get("pct_of_benchmark") or 0
    bar_pct = max(0, min(100, pct / 120 * 100))
    green_lo, green_hi = 92 / 120 * 100, 103 / 120 * 100
    rows = ""
    for p in reversed(pts):
        rows += (
            f'<tr><td>{esc(p["period_label"])}</td>'
            f'<td class="num">{fmt_num(p["value"], 0)}</td>'
            f'<td class="num {"down" if (p.get("mom_pct_official") or 0) < 0 else "up"}">'
            f'{p["mom_pct_official"]:+.1f}%</td>'
            f'<td class="num {"down" if (p.get("yoy_pct") or 0) < 0 else "up"}">'
            f'{p["yoy_pct"]:+.1f}%</td>'
            f'<td class="num">{p.get("pct_of_benchmark", float("nan")):.1f}%</td>'
            f'<td class="mono dim">{esc(p["publish_date"])}</td></tr>'
        )
    nxt = "".join(
        f'<li><b>{esc(x["period"])}</b> · 预计 <span class="mono">{esc(x["expected_date"])}</span> · {esc(x["carrier"])} · '
        f'<span class="pill pending">{esc(x["status"])}</span></li>'
        for x in m["detail"]["release_schedule"]["next_expected"]
    )
    sw = m["detail"].get("sample_watch") or {}
    sw_html = ""
    if sw:
        tw = sw.get("three_way_2026_08", {})
        sw_html = f"""
  <div class="subbox thirdparty">
    <h4>对照：第三方样本口径 <span class="badge thirdparty">非官方</span></h4>
    <p>{esc(sw['name'])} — 样本 {esc(sw['sample_size'])}；{esc(sw['latest_period'])} 存栏
      <b>{fmt_num(sw['latest_value'])} {esc(sw['unit'])}</b>，环比 <b class="down">{sw['mom_pct']:+.2f}%</b>。</p>
    <p class="dim">{esc(sw['note'])}</p>
    {f'<p class="dim">三方规模场单月环比（{esc(tw.get("period",""))}）：卓创 {tw["卓创资讯"]:+.2f}% · 钢联 {tw["上海钢联"]:+.2f}% · 涌益 {tw["涌益咨询"]:+.2f}%。三方样本量不同，仅可同向参考，<b>不可平均、不可拼接</b>。</p>' if tw else ''}
  </div>"""
    corr = "".join(
        f'<li><s>{esc(c["wrong"])}</s> → <b>{esc(c["right"])}</b><br><span class="dim">{esc(c["note"])}</span></li>'
        for c in m["detail"].get("corrections", [])
    )
    corr_html = f'<div class="subbox"><h4>易错说法纠正</h4><ul class="tight">{corr}</ul></div>' if corr else ""
    return f"""
<section class="card wide">
  <header class="card-h">
    <div><h3>{esc(m['name'])}</h3><span class="badge official">官方发布</span></div>
    <div class="asof">统计截止 <b>{esc(m['stat_date'] or '—')}</b> · 发布 <b>{esc(m['publish_date'] or '—')}</b></div>
  </header>
  <div class="hero compact">
    <div class="hero-v">{fmt_num(h['value'], 0)}<span class="unit">{esc(m['unit'])}</span></div>
    <div class="hero-d {cls_of(h['change_abs'])}">{arrow(h['change_abs'])} {fmt_num(h['change_abs'], 0)} {esc(m['unit'])}</div>
    <div class="hero-p">{esc(pts[-1]['period_label'])}，对比 {esc(h['prev_label'])}</div>
  </div>
  <div class="chips">{''.join(chip(c['label'], c['value']) for c in h['extra_chips'])}</div>
  <div class="gauge">
    <div class="gauge-track">
      <div class="gauge-green" style="left:{green_lo:.2f}%;width:{green_hi - green_lo:.2f}%"></div>
      <div class="gauge-mark" style="left:{bar_pct:.2f}%"></div>
    </div>
    <div class="gauge-legend">
      <span>0</span><span>绿色合理区间 92%~103%</span>
      <span>正常保有量 {fmt_num(bm['value'], 0)} {esc(bm['unit'])}</span><span>120%</span>
    </div>
    <p class="inline-note">当前为正常保有量的 <b>{pct:.1f}%</b>，处于<b>绿色合理区间</b>。
      保有量目标由 {esc(bm['document'])}（{esc(bm['publish_date'])}）设定为 {fmt_num(bm['value'], 0)} {esc(bm['unit'])} 左右，
      较 2024 年 2 月的 3900 万头下调。</p>
  </div>
  <div class="tbl-wrap">
    <table class="tbl">
      <thead><tr><th>期末</th><th>存栏(万头)</th><th>环比(官方)</th><th>同比(官方)</th><th>占保有量</th><th>发布日期</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="subbox">
    <h4>下一期官方发布</h4>
    <ul class="tight">{nxt}</ul>
    <p class="dim">{esc(m['detail']['moa_monthly_rule'])}</p>
    <p class="dim">{esc(m['detail']['publisher_note'])}</p>
  </div>
  {sw_html}
  {corr_html}
  <details class="src">
    <summary>口径与来源</summary>
    <dl>
      <dt>统计口径</dt><dd>{esc(m['caliber'])}</dd>
      <dt>发布主体</dt><dd>{esc(m['publisher'])}</dd>
      <dt>样本范围</dt><dd>全国全面统计（非样本），不存在抽样误差口径问题</dd>
      <dt>更新频率</dt><dd>{esc(m['freq'])}</dd>
      <dt>数据源</dt><dd><a href="{esc(m['source_url'])}" target="_blank" rel="noreferrer">{esc(m['source_url'])}</a></dd>
      <dt>备注</dt><dd>{esc(m['notes'])}</dd>
    </dl>
  </details>
</section>"""


def card_frozen(m):
    h = m["headline"]
    hist = m["detail"].get("history") or []
    series_list = m["detail"]["series"]

    # ---- 图表：按机构分组，逐点保留出处，供点选查看 ----
    groups = {}
    for p in hist:
        pid = p.get("publisher_id") or p.get("publisher")
        groups.setdefault(pid, []).append(p)
    colors = {"mysteel-key-slaughter": "#c0392b", "sci-sample-slaughter": "#2c7fb8"}
    names = {"mysteel-key-slaughter": "钢联（重点屠宰企业）", "sci-sample-slaughter": "卓创（样本屠宰企业）"}
    chart_series = []
    for pid, pts in groups.items():
        pts = sorted(pts, key=lambda x: x["stat_date"])
        notes = {
            p["stat_date"]: {
                "publisher": p.get("publisher") or names.get(pid, pid),
                "publish_date": p.get("publish_date"),
                "source_title": p.get("source_title"),
                "source_url": p.get("source_url"),
                "note": p.get("note"),
                "period_label": p.get("period_label"),
            }
            for p in pts
        }
        chart_series.append({
            "id": pid,
            "name": names.get(pid, pid),
            "color": colors.get(pid, "#6b7280"),
            "points": [[p["stat_date"], p["value"]] for p in pts],
            "notes": notes,
        })
    chart_series.sort(key=lambda s: 0 if s["id"] == "mysteel-key-slaughter" else 1)
    chart_json = json.dumps({"unit": "%", "digits": 2, "axisDigits": 1, "series": chart_series}, ensure_ascii=False)
    n_points = sum(len(s["points"]) for s in chart_series)

    # ---- 明细表：每家机构默认折叠，避免整块卡片过长 ----
    series_html = ""
    for s in series_list:
        lookup = {p["stat_date"]: p for p in [s["latest"]] + list(s.get("points", []))}
        hist_pts = [p for p in hist if p.get("publisher_id") == s["id"]]
        if not hist_pts:
            hist_pts = list(lookup.values())
        rows, seen = "", set()
        for p in sorted(hist_pts, key=lambda x: x["stat_date"], reverse=True):
            if p["stat_date"] in seen:
                continue
            seen.add(p["stat_date"])
            meta = lookup.get(p["stat_date"], {})
            rows += (
                f'<tr><td>{esc(p.get("period_label"))}</td>'
                f'<td class="mono dim">{esc(p["stat_date"])}</td>'
                f'<td class="num">{fmt_num(p["value"])}%</td>'
                f'<td class="num">{fmt_num(meta.get("change_pp"), 2) + "pp" if meta.get("change_pp") is not None else "—"}</td>'
                f'<td class="num">{fmt_num(meta.get("yoy_pp"), 1) + "pp" if meta.get("yoy_pp") is not None else "—"}</td>'
                f'<td class="mono dim">{esc(p["publish_date"])}</td></tr>'
            )
        series_html += f"""
  <details class="fold">
    <summary>{esc(s['publisher'].replace('（钢联数据）','').replace(' SCI',''))} · {len(seen)} 个披露点</summary>
    <div class="fold-body">
      <p class="dim">样本：{esc(s['sample'])}</p>
      <div class="tbl-wrap">
        <table class="tbl small">
          <thead><tr><th>期间</th><th>统计日</th><th>库容率</th><th>环比</th><th>同比</th><th>发布日期</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
      <p class="dim">{esc(s['sample_note'])}</p>
    </div>
  </details>"""

    conf = m["detail"].get("conflicts", [])
    conf_html = ""
    if conf:
        conf_html = (
            f'<details class="fold conflict"><summary>⚠ 口径冲突与存疑项（{len(conf)} 项）</summary>'
            '<div class="fold-body"><ul class="tight">'
            + "".join(f'<li><b>{esc(c["issue"])}</b><br>{esc(c["detail"])}<br>'
                      f'<span class="dim">处理方式：{esc(c["handling"])} · '
                      f'<a href="{esc(c["source_url"])}" target="_blank" rel="noreferrer">出处</a></span></li>'
                      for c in conf)
            + '</ul></div></details>'
        )

    gaps = m["detail"].get("known_gaps", [])
    gap_html = ""
    if gaps:
        gap_html = (
            f'<details class="fold gap"><summary>公开信息缺口 · 未找到，不猜测（{len(gaps)} 项）</summary>'
            '<div class="fold-body"><ul class="tight">'
            + "".join(f'<li>{esc(g)}</li>' for g in gaps)
            + '</ul></div></details>'
        )

    return f"""
<section class="card wide">
  <header class="card-h">
    <div><h3>{esc(m['name'])}</h3><span class="badge thirdparty">第三方样本</span></div>
    <div class="asof">统计日 <b>{esc(m['stat_date'] or '—')}</b> · 发布 <b>{esc(m['publish_date'] or '—')}</b></div>
  </header>
  <div class="alert">
    <b>⚠ 口径提示：</b>{esc(m['detail']['warning'])}
  </div>
  <div class="hero compact">
    <div class="hero-v">{fmt_num(h['value'])}<span class="unit">%</span></div>
    <div class="hero-p">当前主口径：{esc(m['publisher'])}（{esc(m['sample'])}）</div>
  </div>
  <div class="chips">{''.join(chip(c['label'], c['value']) for c in h['extra_chips'])}</div>

  <div class="range-bar">
    <div class="range-tabs" role="tablist" aria-label="时间区间">
      <button type="button" data-d="180" role="tab">近 6 个月</button>
      <button type="button" data-d="365" role="tab" class="on">近 12 个月</button>
      <button type="button" data-d="0" role="tab">全部</button>
    </div>
    <div class="range-hint">点按曲线上的任意一点，查看机构、统计日与原文出处</div>
  </div>
  <div class="chart-legend" id="frozenLegend"></div>
  <div class="chart-box">
    <svg id="frozenChart" viewBox="0 0 820 320" preserveAspectRatio="none" aria-label="冻品库容率双口径走势"></svg>
    <div class="chart-tip" id="frozenTip" hidden></div>
  </div>
  <p class="chart-note">共 {n_points} 个公开披露点位。两条线属于不同样本池，只能分别看趋势，<b>禁止拼接、平均或互相比较绝对值</b>；
    相邻点之间以直线连接仅表示方向，不代表期间逐日观测。</p>

  {series_html}
  <p class="inline-note">定义：{esc(m['detail']['definition'])}</p>
  {conf_html}
  {gap_html}
  <details class="src">
    <summary>口径与来源</summary>
    <dl>
      <dt>统计口径</dt><dd>{esc(m['caliber'])}</dd>
      <dt>样本范围</dt><dd>{esc(m['sample'])}</dd>
      <dt>更新频率</dt><dd>{esc(m['freq'])}</dd>
      <dt>对比规则</dt><dd>与上一期披露值对比，单位为百分点（pp）</dd>
      <dt>数据源</dt><dd><a href="{esc(m['source_url'])}" target="_blank" rel="noreferrer">{esc(m['source_url'])}</a></dd>
      <dt>备注</dt><dd>{esc(m['notes'])}</dd>
    </dl>
  </details>
</section>
<script id="frozenData" type="application/json">{chart_json}</script>"""


def card_cull(m):
    h = m["headline"]
    d = m["detail"]
    p = d["points"][-1]
    ratio = h["value"]
    pos = max(0, min(100, ratio / 100 * 100))
    lo, hi = 45, 75
    support = "".join(
        f'<li><b>{esc(s["name"])}</b>（{esc(s["period_label"])}）：'
        f'{esc(s.get("value", s.get("change", "")))} {esc(s.get("unit", ""))} {esc(s.get("change", ""))}'
        f'<br><span class="dim">{esc(s["note"])}</span></li>'
        for s in d["supporting"]
    )
    refs = d.get("reference_points", [])
    ref_html = ""
    if refs:
        ref_html = (
            f'<details class="fold"><summary>其他机构参考值 · 不可拼接（{len(refs)} 项）</summary>'
            '<div class="fold-body">'
            '<p class="dim">以下为跨机构、跨日期的独立观测，仅用于判断比值所处区间，绝不并入主口径序列。</p>'
            '<div class="tbl-wrap"><table class="tbl small">'
            '<thead><tr><th>来源</th><th>统计日</th><th>淘汰母猪</th>'
            '<th>商品猪</th><th>比值</th></tr></thead><tbody>'
            + "".join(
                f'<tr><td>{esc(r["publisher"])}</td><td class="mono dim">{esc(r["stat_date"])}</td>'
                f'<td class="num">{fmt_num(r["cull_sow_price"])}</td>'
                f'<td class="num">{fmt_num(r["commercial_hog_price"])}</td>'
                f'<td class="num">{fmt_num(r["ratio_pct"], 1)}%</td></tr>'
                for r in refs
            )
            + '</tbody></table></div></div></details>'
        )

    # ---- 图表：卓创口径月度自建比值（线） + 钢联主口径当期点（点） ----
    trend = d.get("trend") or []
    trend_pts = [[t["stat_date"], t["ratio_pct"]] for t in trend]
    trend_notes = {
        t["stat_date"]: {
            "publisher": "卓创 / 中国养猪网",
            "publish_date": t.get("publish_date"),
            "source_title": t.get("source_title"),
            "source_url": t.get("source_url"),
            "note": f'淘汰母猪 {t["cull_sow_price"]:.2f} ÷ 商品猪 {t["commercial_hog_price"]:.2f} = {t["ratio_pct"]:.2f}%',
            "period_label": t.get("period_label"),
        }
        for t in trend
    }
    main_note = {
        p["stat_date"]: {
            "publisher": p.get("publisher") or "上海钢联 Mysteel 样本",
            "publish_date": p.get("publish_date"),
            "source_title": p.get("source_title"),
            "source_url": p.get("source_url"),
            "note": f'淘汰母猪 {p["cull_sow_price"]:.2f} ÷ 商品猪 {p["commercial_hog_price"]:.2f} = {p["ratio_pct"]:.2f}%（主口径）',
            "period_label": "当期主口径",
        }
    }
    chart_json = json.dumps({
        "unit": "%",
        "digits": 1,
        "minSpan": 10,
        "series": [
            {"id": "sci-trend", "name": "卓创口径 · 月度自建比值", "color": "#c0392b",
             "points": trend_pts, "notes": trend_notes},
            {"id": "mysteel", "name": "钢联主口径 · 当期", "color": "#2c7fb8",
             "points": [[p["stat_date"], p["ratio_pct"]]], "notes": main_note},
        ],
    }, ensure_ascii=False)

    trend_rows = "".join(
        f'<tr><td>{esc(t.get("period_label"))}</td><td class="mono dim">{esc(t["stat_date"])}</td>'
        f'<td class="num">{fmt_num(t["cull_sow_price"])}</td>'
        f'<td class="num">{fmt_num(t["commercial_hog_price"], 3)}</td>'
        f'<td class="num"><b>{fmt_num(t["ratio_pct"], 1)}%</b></td>'
        f'<td><a href="{esc(t.get("source_url", ""))}" target="_blank" rel="noreferrer">原文</a></td></tr>'
        for t in trend
    )
    trend_html = ""
    if trend:
        trend_html = (
            f'<details class="fold"><summary>月度趋势点位明细（{len(trend)} 项）</summary>'
            '<div class="fold-body"><div class="tbl-wrap"><table class="tbl small">'
            '<thead><tr><th>期间</th><th>统计日</th><th>淘汰母猪</th><th>商品猪</th><th>比值</th><th>出处</th></tr></thead>'
            f'<tbody>{trend_rows}</tbody></table></div>'
            '<p class="dim">分子为卓创淘汰母猪月均价；分母为同期全国外三元月均价（中国养猪网日线按月平均）。'
            '两者样本不同，比值为本看板自建，仅用于看趋势。</p></div></details>'
        )

    return f"""
<section class="card wide">
  <header class="card-h">
    <div><h3>{esc(m['name'])}</h3><span class="badge thirdparty">第三方样本</span></div>
    <div class="asof">统计日 <b>{esc(m['stat_date'] or '—')}</b> · 发布 <b>{esc(m['publish_date'] or '—')}</b></div>
  </header>
  <div class="hero">
    <div class="hero-v">{fmt_num(h['value'], 1)}<span class="unit">%</span></div>
    <div class="hero-p">{fmt_num(p['cull_sow_price'])} 元/公斤（淘汰母猪） ÷ {fmt_num(p['commercial_hog_price'])} 元/公斤（商品猪）</div>
  </div>
  <div class="ratio-bar">
    <div class="ratio-track">
      <div class="ratio-band" style="left:{lo}%;width:{hi - lo}%"></div>
      <div class="ratio-mark" style="left:{pos:.1f}%"></div>
    </div>
    <div class="gauge-legend"><span>0%</span><span>经验区间 45%~75%</span><span>100%</span></div>
  </div>

  <div class="range-bar">
    <div class="range-tabs" role="tablist" aria-label="时间区间">
      <button type="button" data-d="90" role="tab">近 3 个月</button>
      <button type="button" data-d="180" role="tab" class="on">近 6 个月</button>
      <button type="button" data-d="0" role="tab">全部</button>
    </div>
    <div class="range-hint">点按曲线上的任意一点，查看分子、分母与原文出处</div>
  </div>
  <div class="chart-legend" id="ratioLegend"></div>
  <div class="chart-box">
    <svg id="ratioChart" viewBox="0 0 820 320" preserveAspectRatio="none" aria-label="淘汰母猪与商品猪价格比走势"></svg>
    <div class="chart-tip" id="ratioTip" hidden></div>
  </div>
  <p class="chart-note">红线为<b>卓创口径月度自建比值</b>：卓创淘汰母猪月均价 ÷ 同期全国外三元月均价，按月累积；
    蓝点为<b>钢联主口径当期值</b>，两者样本不同，仅同图对照，不合并为一条序列。</p>

  <div class="two-col">
    <div class="subbox">
      <h4>怎么读这个比值</h4>
      <p>{esc(d['interpretation']['low'])}</p>
      <p>{esc(d['interpretation']['high'])}</p>
      <p class="dim">{esc(d['interpretation']['reference_band'])}</p>
      <p class="inline-note">当前 <b>{fmt_num(h['value'], 1)}%</b>，处于经验区间内偏下位置：
        淘汰母猪相对商品猪贬值，淘汰压力偏大。</p>
    </div>
    <div class="subbox">
      <h4>同步信号</h4>
      <ul class="tight">{support}</ul>
    </div>
  </div>
  <div class="alert soft"><b>口径声明：</b>{esc(d['disclaimer'])}</div>
  {trend_html}
  {ref_html}
  <details class="src">
    <summary>口径与来源</summary>
    <dl>
      <dt>计算公式</dt><dd>{esc(d['formula'])}</dd>
      <dt>单位约束</dt><dd>分子分母必须同为元/公斤；若来源以元/斤报出须先 ×2，禁止直接相除</dd>
      <dt>统计口径</dt><dd>{esc(m['caliber'])}</dd>
      <dt>更新频率</dt><dd>{esc(m['freq'])}</dd>
      <dt>数据源</dt><dd><a href="{esc(m['source_url'])}" target="_blank" rel="noreferrer">{esc(m['source_url'])}</a></dd>
      <dt>校验</dt><dd>{esc(p.get('cross_check', '—'))}</dd>
    </dl>
  </details>
</section>
<script id="ratioData" type="application/json">{chart_json}</script>"""


CARD_FN = {
    "hog_spot": card_hog_spot,
    "dce_futures": card_futures,
    "sow_inventory": card_sow,
    "frozen_inventory": card_frozen,
    "cull_sow_ratio": card_cull,
}


SPOT_JS = r"""
(function(){
  var el = document.getElementById('spotData');
  var svg = document.getElementById('spotChart');
  if(!el || !svg) return;
  var D; try { D = JSON.parse(el.textContent); } catch(e){ return; }
  var series = D.series || [];
  if(series.length < 2) return;

  var tip   = document.getElementById('spotTip');
  var box   = svg.parentElement;
  var stats = document.getElementById('spotStats');
  var tabs  = svg.closest('section').querySelectorAll('.range-tabs button');

  var W = 820, H = 300;
  var P = {l:48, r:16, t:18, b:36};
  var pts = [], cur = 30, sel = -1;
  var NS = 'http://www.w3.org/2000/svg';

  function shortDate(d){ return d.slice(5).replace('-', '/'); }
  function money(v){ return (Math.round(v*100)/100).toFixed(2); }
  function sign(v){ return (v>0?'+':'') + money(v); }

  function build(w){
    cur = w;
    var slice = series.slice(-(w+1));
    var vals = slice.map(function(p){ return p[1]; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var rawSpan = hi - lo;
    var yr = rawSpan > 0 ? rawSpan*0.14 : Math.max(hi*0.02, 0.05);
    lo -= yr; hi += yr;
    var iw = W - P.l - P.r, ih = H - P.t - P.b;

    pts = slice.map(function(p, i){
      return {
        d: p[0], v: p[1],
        x: P.l + (slice.length === 1 ? iw/2 : (i/(slice.length-1))*iw),
        y: P.t + (1 - (p[1]-lo)/(hi-lo))*ih
      };
    });

    var path = pts.map(function(q,i){ return (i?'L':'M') + q.x.toFixed(1) + ' ' + q.y.toFixed(1); }).join(' ');
    var area = 'M' + pts[0].x.toFixed(1) + ' ' + (H-P.b) + ' ' +
               pts.map(function(q){ return 'L' + q.x.toFixed(1) + ' ' + q.y.toFixed(1); }).join(' ') +
               ' L' + pts[pts.length-1].x.toFixed(1) + ' ' + (H-P.b) + ' Z';

    var rising = pts[pts.length-1].v >= pts[0].v;
    var col = rising ? '#c0392b' : '#1e8e5a';

    var g = '';
    // 横向网格 + 右侧/左侧刻度
    var ticks = 4;
    for(var i=0;i<=ticks;i++){
      var y = P.t + (i/ticks)*ih;
      var val = hi - (i/ticks)*(hi-lo);
      g += '<line x1="'+P.l+'" y1="'+y.toFixed(1)+'" x2="'+(W-P.r)+'" y2="'+y.toFixed(1)+
           '" stroke="#eef0f3" stroke-width="1"/>';
      g += '<text x="'+(P.l-9)+'" y="'+(y+4).toFixed(1)+'" text-anchor="end" font-size="11.5" fill="#9aa1a9">'+
           money(val)+'</text>';
    }
    // x 轴日期标签（首/中/尾）
    var xlab = [0, Math.floor((pts.length-1)/2), pts.length-1].filter(function(v,i,a){ return a.indexOf(v)===i; });
    xlab.forEach(function(i){
      var anchor = i===0 ? 'start' : (i===pts.length-1 ? 'end' : 'middle');
      g += '<text x="'+pts[i].x.toFixed(1)+'" y="'+(H-12)+'" text-anchor="'+anchor+
           '" font-size="11.5" fill="#9aa1a9">'+shortDate(pts[i].d)+'</text>';
    });

    var last = pts[pts.length-1];
    var html = g
      + '<path d="'+area+'" fill="'+col+'" opacity="0.10"/>'
      + '<path d="'+path+'" fill="none" stroke="'+col+'" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
      + '<circle cx="'+last.x.toFixed(1)+'" cy="'+last.y.toFixed(1)+'" r="4" fill="'+col+'"/>'
      + '<g id="spotGuide" style="display:none">'
      +   '<line id="spotGuideLine" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="#15181d" stroke-width="1.2" stroke-dasharray="4 4" opacity="0.45"/>'
      +   '<circle id="spotDot" r="5.5" fill="#fff" stroke="'+col+'" stroke-width="2.6"/>'
      + '</g>';
    svg.innerHTML = html;
    svg.style.touchAction = 'pan-y';

    // 区间统计
    var a = pts[0], z = last;
    var diff = z.v - a.v;
    var pct = a.v ? (diff/a.v*100) : 0;
    var cls = diff > 0 ? 'up' : (diff < 0 ? 'down' : 'flat');
    var days = pts.length;
    var spanDays = Math.round((new Date(z.d) - new Date(a.d)) / 86400000);
    var note = spanDays >= w ? '' : '（已累积 ' + spanDays + ' 天）';
    stats.innerHTML =
      '<div class="rs"><span class="rs-l">区间起点 '+shortDate(a.d)+note+'</span>'+
        '<span class="rs-v">'+money(a.v)+'</span></div>'+
      '<div class="rs"><span class="rs-l">当前 '+shortDate(z.d)+'</span>'+
        '<span class="rs-v">'+money(z.v)+'</span></div>'+
      '<div class="rs"><span class="rs-l">区间变化</span>'+
        '<span class="rs-v '+cls+'">'+sign(diff)+' 元<br><small style="font-weight:500">'+(pct>=0?'+':'')+pct.toFixed(2)+'%</small></span></div>'+
      '<div class="rs"><span class="rs-l">区间最高 / 最低</span>'+
        '<span class="rs-v">'+money(hi-yr)+' / '+money(lo+yr)+'</span></div>';

    tabs.forEach(function(b){ b.classList.toggle('on', Number(b.dataset.w) === w); });
    sel = -1;
    if(tip) tip.hidden = true;
  }

  function idxFromX(clientX){
    var r = svg.getBoundingClientRect();
    if(!r.width) return -1;
    var vx = (clientX - r.left) / r.width * W;
    var best = 0, bd = Infinity;
    for(var i=0;i<pts.length;i++){
      var dd = Math.abs(pts[i].x - vx);
      if(dd < bd){ bd = dd; best = i; }
    }
    return best;
  }

  function showAt(i){
    if(i < 0 || i >= pts.length) return;
    sel = i;
    var q = pts[i];
    var gl = document.getElementById('spotGuideLine');
    var dot = document.getElementById('spotDot');
    var grp = document.getElementById('spotGuide');
    if(!gl || !dot || !grp) return;
    grp.style.display = '';
    gl.setAttribute('x1', q.x); gl.setAttribute('x2', q.x);
    dot.setAttribute('cx', q.x); dot.setAttribute('cy', q.y);

    var prev = i > 0 ? pts[i-1] : null;
    var chg = prev ? (q.v - prev.v) : null;
    var ccls = chg === null ? '' : (chg > 0 ? 'up' : (chg < 0 ? 'down' : ''));
    var ctxt = chg === null ? '区间首日' :
      ('较前一日 ' + sign(chg) + '（' + ((chg/prev.v)*100>=0?'+':'') + ((chg/prev.v)*100).toFixed(2) + '%）');

    tip.hidden = false;
    tip.innerHTML = '<div class="t-d">' + q.d + '</div>' +
      '<div class="t-v">' + money(q.v) + '<span>元/公斤</span></div>' +
      '<div class="t-c ' + ccls + '">' + ctxt + '</div>';

    var r = svg.getBoundingClientRect();
    var px = q.x / W * r.width;
    var py = q.y / H * r.height;
    // 贴边时调整
    var tw = tip.offsetWidth || 140;
    var minx = tw/2 + 2, maxx = r.width - tw/2 - 2;
    px = Math.max(minx, Math.min(maxx, px));
    tip.style.left = px + 'px';
    tip.style.top  = Math.max(py, 6) + 'px';
  }

  function hide(){
    var grp = document.getElementById('spotGuide');
    if(grp) grp.style.display = 'none';
    if(tip) tip.hidden = true;
    sel = -1;
  }

  var down = false;
  box.addEventListener('pointerdown', function(e){
    down = true;
    try { box.setPointerCapture && box.setPointerCapture(e.pointerId); } catch(_) {}
    showAt(idxFromX(e.clientX));
  });
  box.addEventListener('pointermove', function(e){
    if(e.pointerType === 'touch' && !down) return;
    showAt(idxFromX(e.clientX));
  });
  box.addEventListener('pointerup', function(e){
    down = false;
    if(e.pointerType === 'mouse') return;
    // 触摸抬起后保留提示，直到点别处
  });
  box.addEventListener('pointerleave', function(e){
    if(e.pointerType === 'mouse') hide();
  });
  document.addEventListener('pointerdown', function(e){
    if(!box.contains(e.target)) hide();
  });

  tabs.forEach(function(b){
    b.addEventListener('click', function(){ build(Number(b.dataset.w)); });
  });

  build(30);
})();
"""


MULTI_CHART_JS = r"""
(function(){
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function init(cfg){
    var root=document.getElementById(cfg.dataId);
    var svg=document.getElementById(cfg.svgId);
    var tip=document.getElementById(cfg.tipId);
    var legend=document.getElementById(cfg.legendId);
    if(!root||!svg) return;
    var D; try{D=JSON.parse(root.textContent);}catch(e){return;}
    var all=D.series||[];
    if(!all.length) return;
    var W=820,H=320,P={l:54,r:22,t:18,b:38};
    var vis={}; all.forEach(function(s){vis[s.id]=true;});
    var span=cfg.defaultSpan||0, sel=null, pts=[];
    var box=svg.parentElement;
    var sect=svg.closest('section');

    function short(d){return d.slice(5).replace('-','/');}
    function fmt(v,d){d=(d==null?D.digits:d);var k=Math.pow(10,d);return (Math.round(v*k)/k).toFixed(d);}
    function fmtAxis(v){return fmt(v,D.axisDigits==null?D.digits:D.axisDigits);}
    function maxDate(){var m='';all.forEach(function(s){s.points.forEach(function(p){if(p[0]>m)m=p[0];});});return m;}
    function cutoff(){if(!span)return '';var m=new Date(maxDate());m.setDate(m.getDate()-span);return m.toISOString().slice(0,10);}

    function visibleSeries(){
      var c=cutoff();
      return all.filter(function(s){return vis[s.id];}).map(function(s){
        return {id:s.id,name:s.name,color:s.color,notes:s.notes,
          points:s.points.filter(function(p){return !c||p[0]>=c;})};
      }).filter(function(s){return s.points.length>0;});
    }

    function draw(){
      var ss=visibleSeries();
      if(!ss.length){svg.innerHTML='';if(tip)tip.hidden=true;return;}
      var dates={}; ss.forEach(function(s){s.points.forEach(function(p){dates[p[0]]=1;});});
      var ds=Object.keys(dates).sort();
      var t0=new Date(ds[0]).getTime(), t1=new Date(ds[ds.length-1]).getTime();
      var lo=Infinity,hi=-Infinity;
      ss.forEach(function(s){s.points.forEach(function(p){lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1]);});});
      if(hi-lo<1e-9){hi+=1;lo-=1;}
      var ms=D.minSpan||0;
      if(ms&&hi-lo<ms){var mid=(hi+lo)/2;lo=mid-ms/2;hi=mid+ms/2;}
      var pad=(hi-lo)*0.14; lo-=pad; hi+=pad;
      var iw=W-P.l-P.r, ih=H-P.t-P.b;
      function X(d){return t1===t0?P.l+iw/2:P.l+(new Date(d).getTime()-t0)/(t1-t0)*iw;}
      function Y(v){return P.t+(1-(v-lo)/(hi-lo))*ih;}

      var g='';
      for(var i=0;i<=4;i++){
        var y=P.t+i/4*ih, val=hi-(i/4)*(hi-lo);
        g+='<line x1="'+P.l+'" y1="'+y.toFixed(1)+'" x2="'+(W-P.r)+'" y2="'+y.toFixed(1)+'" stroke="#eef0f3" stroke-width="1"/>';
        g+='<text x="'+(P.l-10)+'" y="'+(y+4).toFixed(1)+'" text-anchor="end" font-size="11.5" fill="#9aa1a9">'+fmtAxis(val)+'</text>';
      }
      var lab=[0,Math.floor((ds.length-1)/2),ds.length-1].filter(function(v,i,a){return a.indexOf(v)===i;});
      lab.forEach(function(i){
        var anchor=i===0?'start':(i===ds.length-1?'end':'middle');
        g+='<text x="'+X(ds[i]).toFixed(1)+'" y="'+(H-14)+'" text-anchor="'+anchor+'" font-size="11.5" fill="#9aa1a9">'+short(ds[i])+'</text>';
      });

      pts=[];
      ss.forEach(function(s){
        var coords=s.points.map(function(p){return {d:p[0],v:p[1],x:X(p[0]),y:Y(p[1]),s:s};});
        coords.forEach(function(c){pts.push(c);});
        if(coords.length>=2){
          var path=coords.map(function(c,i){return (i?'L':'M')+c.x.toFixed(1)+' '+c.y.toFixed(1);}).join(' ');
          g+='<path d="'+path+'" fill="none" stroke="'+s.color+'" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>';
        }
        coords.forEach(function(c){
          var big=s.points.length===1;
          g+='<circle cx="'+c.x.toFixed(1)+'" cy="'+c.y.toFixed(1)+'" r="'+(big?6:3.6)+'" fill="'+s.color+'" stroke="#fff" stroke-width="'+(big?2.2:1.4)+'"/>';
        });
        var last=coords[coords.length-1];
        if(last&&coords.length>=2){
          g+='<text x="'+last.x.toFixed(1)+'" y="'+(last.y-11).toFixed(1)+'" text-anchor="end" font-size="11.5" font-weight="700" fill="'+s.color+'">'+fmt(last.v)+'</text>';
        }
      });
      g+='<g id="'+cfg.svgId+'Guide" style="display:none">'+
         '<line id="'+cfg.svgId+'Line" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="#15181d" stroke-width="1.2" stroke-dasharray="4 4" opacity="0.45"/>'+
         '<circle id="'+cfg.svgId+'Dot" r="5.5" fill="#fff" stroke="#15181d" stroke-width="2.2"/>'+
         '</g>';
      svg.innerHTML=g;
      svg.style.touchAction='pan-y';
      if(sel!==null) show(sel,true);
    }

    function nearest(clientX){
      if(!pts.length)return null;
      var r=svg.getBoundingClientRect();
      var vx=(clientX-r.left)/r.width*W;
      var best=pts[0],bd=Infinity;
      pts.forEach(function(p){var d=Math.abs(p.x-vx);if(d<bd){bd=d;best=p;}});
      return best;
    }

    function show(clientX){
      var q=nearest(clientX); if(!q)return;
      sel=q;
      var gl=document.getElementById(cfg.svgId+'Line');
      var dot=document.getElementById(cfg.svgId+'Dot');
      var grp=document.getElementById(cfg.svgId+'Guide');
      if(gl&&dot&&grp){
        grp.style.display='';
        gl.setAttribute('x1',q.x);gl.setAttribute('x2',q.x);
        dot.setAttribute('cx',q.x);dot.setAttribute('cy',q.y);
        dot.setAttribute('stroke',q.s.color);
      }
      var n=(q.s.notes&&q.s.notes[q.d])||{};
      if(!tip)return;
      tip.hidden=false;
      tip.innerHTML='<div class="t-d">'+esc(q.d)+(n.period_label?' · '+esc(n.period_label):'')+'</div>'+
        '<div class="t-v">'+fmt(q.v)+'<span>'+esc(n.publisher||q.s.name)+'</span></div>'+
        (n.note?'<div class="t-c">'+esc(n.note)+'</div>':'');
      var r=svg.getBoundingClientRect();
      var px=q.x/W*r.width, py=q.y/H*r.height;
      var tw=tip.offsetWidth||150, th=tip.offsetHeight||60;
      px=Math.max(tw/2+2,Math.min(r.width-tw/2-2,px));
      tip.style.left=px+'px';
      tip.style.top=Math.max(py,th+6)+'px';
    }

    function hide(){
      var grp=document.getElementById(cfg.svgId+'Guide');
      if(grp)grp.style.display='none';
      if(tip)tip.hidden=true;
    }

    var down=false;
    box.addEventListener('pointerdown',function(e){down=true;try{box.setPointerCapture&&box.setPointerCapture(e.pointerId);}catch(_){ }show(e.clientX);});
    box.addEventListener('pointermove',function(e){if(e.pointerType==='touch'&&!down)return;show(e.clientX);});
    box.addEventListener('pointerup',function(){down=false;});
    box.addEventListener('pointerleave',function(e){if(e.pointerType==='mouse')hide();});
    document.addEventListener('pointerdown',function(e){if(!box.contains(e.target))hide();});

    function renderLegend(){
      if(!legend)return;
      legend.innerHTML='';
      all.forEach(function(s){
        var b=document.createElement('button');
        b.type='button';
        b.className=vis[s.id]?'':'off';
        b.style.color=s.color;
        b.innerHTML='<span class="sw"></span>'+esc(s.name)+' <span class="cnt">'+s.points.length+'点</span>';
        b.addEventListener('click',function(){vis[s.id]=!vis[s.id];sel=null;renderLegend();draw();});
        legend.appendChild(b);
      });
    }

    var tabs=sect?sect.querySelectorAll('.range-tabs button'):[];
    Array.prototype.forEach.call(tabs,function(b){
      b.addEventListener('click',function(){
        span=Number(b.dataset.d)||0;
        Array.prototype.forEach.call(tabs,function(x){x.classList.toggle('on',x===b);});
        sel=null;hide();draw();
      });
    });

    renderLegend();
    draw();
  }
  window.__initMultiChart=init;
})();
"""


SW_TEMPLATE = r"""
/* 生猪看板 Service Worker
   目标：已「添加到主屏幕」的 iPhone 在 Mac 关机 / 断网时，仍能打开最后一次同步的看板。
   策略：导航 network-first（在线永远拿最新），失败回落缓存；静态资源 stale-while-revalidate。

   ⚠ 全部使用**相对路径**：本站点可能部署在子路径下（如 GitHub Pages 的 /pigboard/），
     若写成 '/xxx' 会解析到域名根，导致预缓存全部 404。 */
const VERSION = '__VERSION__';
const CORE = [
  './',
  './manifest.webmanifest',
  './icons/apple-touch-icon.png',
  './icons/icon-192.png',
  './icons/icon-512.png'
];

self.addEventListener('install', function (e) {
  self.skipWaiting();
  e.waitUntil((async function () {
    const c = await caches.open(VERSION);
    for (const u of CORE) {
      try {
        await c.add(new Request(u, { cache: 'reload' }));
      } catch (_) {
        try { await c.add(u); } catch (__) {}
      }
    }
  })());
});

self.addEventListener('activate', function (e) {
  e.waitUntil((async function () {
    const keys = await caches.keys();
    await Promise.all(keys.filter(function (k) { return k !== VERSION; })
                          .map(function (k) { return caches.delete(k); }));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== location.origin) return;

  if (req.mode === 'navigate') {
    const APP_ROOT = new URL('./', self.registration.scope).href;
    const NET_TIMEOUT = 3000;  // 网络慢时先用缓存秒开，后台再更新

    e.respondWith((async function () {
      const cacheNav = async function (r) {
        try {
          const c = await caches.open(VERSION);
          // 同时按"本次请求 URL"和"应用根"保存，兼容子路径与根路径部署
          try { await c.put(req, r.clone()); } catch (_) {}
          try { await c.put(APP_ROOT, r.clone()); } catch (_) {}
        } catch (_) {}
      };

      let timer;
      try {
        const net = await Promise.race([
          fetch(req),
          new Promise(function (_, rej) {
            timer = setTimeout(function () { rej(new Error('net-timeout')); }, NET_TIMEOUT);
          })
        ]);
        clearTimeout(timer);
        if (net && net.ok) cacheNav(net);
        return net;
      } catch (_) {
        clearTimeout(timer);
        const hit = (await caches.match(req)) || (await caches.match(APP_ROOT));
        if (hit) {
          // 命中缓存：立即返回，同时后台静默更新
          fetch(req).then(cacheNav).catch(function () {});
          return hit;
        }
        try {
          const net = await fetch(req);
          if (net && net.ok) cacheNav(net);
          return net;
        } catch (__) { /* 落到离线页 */ }
        return new Response(
          '<!DOCTYPE html><meta charset="utf-8"><title>离线</title>' +
          '<body style="font:16px/1.7 -apple-system,sans-serif;padding:40px;color:#333">' +
          '<h2>暂时无法连接</h2><p>尚未缓存任何看板内容。请先在有网络时打开一次。</p></body>',
          { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
      }
    })());
    return;
  }

  e.respondWith((async function () {
    // 带查询串的请求（如心跳 ?_=时间戳）不写入缓存，否则每次访问都会新增一条，无限膨胀
    const cacheable = !url.search;
    const hit = await caches.match(req, { ignoreSearch: true });
    if (hit) {
      // 后台更新，不阻塞返回
      fetch(req).then(async function (r) {
        if (r && r.ok && cacheable) {
          const c = await caches.open(VERSION);
          c.put(req, r.clone()).catch(function () {});
        }
      }).catch(function () {});
      return hit;
    }
    try {
      const r = await fetch(req);
      if (r && r.ok && cacheable) {
        const c = await caches.open(VERSION);
        c.put(req, r.clone()).catch(function () {});
      }
      return r;
    } catch (_) {
      return new Response('', { status: 504 });
    }
  })());
});
"""


SW_REG_JS = r"""
(function(){
  // Service Worker 只在安全上下文可用（https 或 localhost）。
  // 经 http://<局域网IP> 或 http://<tailnet> 访问时浏览器会拒绝注册，此处静默跳过。
  var secure = (location.protocol === 'https:') ||
               (location.hostname === 'localhost') || (location.hostname === '127.0.0.1');
  if (!secure || !('serviceWorker' in navigator)) return;
  window.addEventListener('load', function(){
    navigator.serviceWorker.register('sw.js', { scope: './' }).then(function(reg){
      reg.addEventListener('updatefound', function(){
        var nw = reg.installing;
        if (!nw) return;
        nw.addEventListener('statechange', function(){
          if (nw.state === 'installed' && navigator.serviceWorker.controller) {
            // 有新版本已就绪；下次进入生效，避免打断阅读
            console.log('[pigboard] 新版本已缓存，下次打开生效');
          }
        });
      });
    }).catch(function(e){ console.warn('[pigboard] SW 注册失败', e && e.message); });
  });
  // 在线/离线状态提示
  function paint(){
    var el = document.querySelector('.offline-tag');
    if (!el) return;
    el.hidden = navigator.onLine;
  }
  window.addEventListener('online', paint);
  window.addEventListener('offline', paint);
  document.addEventListener('DOMContentLoaded', paint);
})();
"""


CSS = """
*{box-sizing:border-box}
:root{
  --bg:#f5f6f8; --panel:#fff; --ink:#15181d; --ink2:#4a5158; --dim:#878e96;
  --line:#e2e5e9; --line2:#eef0f3;
  --up:#c0392b; --down:#1e8e5a; --flat:#6b7280;
  --official:#1d4ed8; --third:#b45309; --market:#0f766e;
  --warn-bg:#fff8e6; --warn-line:#f0c36d;
  --tp-bg:#fdf6ec;
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.62 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;}
a{color:#1d4ed8;text-decoration:none;word-break:break-all}
a:hover{text-decoration:underline}
.wrap{max-width:1280px;margin:0 auto;padding:28px 20px 72px}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:14px;
  padding-bottom:18px;border-bottom:2px solid var(--ink)}
h1{margin:0;font-size:27px;letter-spacing:-.4px}
.sub{margin:5px 0 0;color:var(--ink2);font-size:13.5px}
.meta{text-align:right;font-size:13px;color:var(--ink2);line-height:1.9}
.meta b{color:var(--ink)}
.stale-banner{margin:18px 0 0;padding:12px 16px;border-radius:10px;background:var(--warn-bg);
  border:1px solid var(--warn-line);font-size:13.5px}
.stale-banner ul{margin:6px 0 0;padding-left:20px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-top:22px}
/* 关键：网格/弹性子项默认 min-width:auto，内部宽表格会把整页撑出横向滚动，故显式归零 */
.grid>*{min-width:0}
.top>div{min-width:0}
.card{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:20px 22px 18px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.card.wide{grid-column:1/-1}
.card.stale{outline:2px solid var(--warn-line);outline-offset:-2px}
.card-h{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;
  padding-bottom:12px;border-bottom:1px solid var(--line2);margin-bottom:16px}
.card-h h3{margin:0;font-size:16.5px;display:inline-block;margin-right:8px}
.asof{font-size:12.5px;color:var(--dim);white-space:nowrap}
.asof b{color:var(--ink2)}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;vertical-align:2px;
  border:1px solid;font-weight:600;letter-spacing:.3px}
.badge.official{color:var(--official);border-color:#c7d2fe;background:#eef2ff}
.badge.thirdparty{color:var(--third);border-color:#fcd9a8;background:#fff7ed}
.badge.market{color:var(--market);border-color:#b8e0da;background:#effcf9}
.hero{display:flex;flex-wrap:wrap;align-items:baseline;gap:14px;margin-bottom:6px}
.hero.compact{margin-bottom:10px}
.hero-v{font-size:44px;font-weight:700;letter-spacing:-1.6px;line-height:1.05;
  font-variant-numeric:tabular-nums}
.hero.compact .hero-v{font-size:36px}
.hero-v .unit{font-size:14px;font-weight:500;color:var(--ink2);margin-left:6px;letter-spacing:0}
.hero-d{font-size:19px;font-weight:650;font-variant-numeric:tabular-nums}
.hero-d small{font-size:14px;font-weight:500}
.hero-p{font-size:13px;color:var(--ink2);width:100%}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--flat)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 4px}
.chip{font-size:12.5px;background:#f4f5f7;border:1px solid var(--line);border-radius:8px;
  padding:4px 10px;color:var(--ink2)}
.chip b{color:var(--dim);font-weight:600;margin-right:6px}
.spark-wrap{margin:14px 0 4px}
.spark{width:100%;height:90px;display:block}
.spark-axis{display:flex;justify-content:space-between;font-size:11.5px;color:var(--dim);margin-top:2px}
.spark-empty{font-size:12.5px;color:var(--dim);padding:22px 0;text-align:center;
  border:1px dashed var(--line);border-radius:10px}
.tbl-wrap{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;
  margin-top:12px;border:1px solid var(--line);border-radius:10px}
.tbl{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;
  min-width:640px}
.tbl.small{min-width:520px}
.tbl th{background:#fafbfc;text-align:right;padding:9px 12px;font-weight:600;color:var(--ink2);
  border-bottom:1px solid var(--line);white-space:nowrap;font-size:12.5px}
.tbl th:first-child,.tbl td:first-child{text-align:left}
.tbl td{padding:9px 12px;border-bottom:1px solid var(--line2);text-align:right;white-space:nowrap}
.tbl tbody tr:last-child td{border-bottom:none}
.tbl tr.is-main{background:#f7fbff}
.tbl tr.is-main td:first-child::after{content:" 主力";font-size:10.5px;color:var(--market);
  border:1px solid #b8e0da;background:#effcf9;border-radius:4px;padding:1px 5px;margin-left:6px}
.num{font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.dim{color:var(--dim)}
.inline-note{font-size:13px;color:var(--ink2);margin:10px 0 0;
  padding:10px 12px;background:#fafbfc;border-left:3px solid var(--line);border-radius:0 8px 8px 0}
.subbox{margin-top:14px;padding:14px 16px;background:#fafbfc;border:1px solid var(--line2);border-radius:10px}
.subbox.thirdparty{background:var(--tp-bg);border-color:#f5e0c3}
.subbox h4{margin:0 0 8px;font-size:13.5px}
.subbox p{margin:5px 0;font-size:13px;color:var(--ink2)}
ul.tight{margin:6px 0 0;padding-left:19px;font-size:13px;color:var(--ink2)}
ul.tight li{margin-bottom:7px}
ul.tight li:last-child{margin-bottom:0}
.alert{margin:0 0 14px;padding:11px 14px;border-radius:9px;background:var(--warn-bg);
  border:1px solid var(--warn-line);font-size:13px;color:#7a4b06}
.alert.soft{background:#f7f9fb;border-color:var(--line);color:var(--ink2)}
.wchips{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:14px 0 2px}
.wchip{display:flex;flex-direction:column;gap:1px;padding:8px 12px;border-radius:9px;
  border:1px solid var(--line);background:#fafbfc;font-variant-numeric:tabular-nums;min-width:0}
.wchip b{font-weight:600;color:var(--dim);font-size:11.5px;letter-spacing:.2px}
.wchip .wv{font-size:16px;font-weight:700;line-height:1.25}
.wchip i{font-style:normal;font-weight:500;font-size:11.5px;opacity:.85}
.wchip.up{border-color:#f3c9c3;background:#fff7f6}
.wchip.down{border-color:#c6e6d5;background:#f3fbf6}
.wchip.flat{color:var(--dim)}
.range-bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
  gap:10px;margin:18px 0 10px}
.range-tabs{display:inline-flex;gap:4px;background:#eef1f4;padding:4px;border-radius:10px}
.range-tabs button{appearance:none;border:0;background:transparent;color:var(--ink2);
  font:inherit;font-size:13px;font-weight:600;padding:7px 15px;border-radius:7px;cursor:pointer;
  transition:background .15s,color .15s}
.range-tabs button.on{background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(16,24,40,.10)}
.range-tabs button:active{transform:scale(.97)}
.range-hint{font-size:12px;color:var(--dim)}
.chart-box{position:relative;margin:2px 0 6px;touch-action:pan-y}
#spotChart{display:block;width:100%;height:300px;overflow:visible}
.chart-tip{position:absolute;pointer-events:none;z-index:5;min-width:132px;max-width:min(320px,80vw);
  background:rgba(21,24,29,.94);color:#fff;border-radius:9px;padding:8px 11px;
  font-size:12.5px;line-height:1.55;box-shadow:0 6px 18px rgba(0,0,0,.22);
  transform:translate(-50%,-108%);white-space:normal;overflow-wrap:anywhere}
.chart-tip .t-d{color:#c9d1d9;font-size:11.5px;font-variant-numeric:tabular-nums}
.chart-tip .t-v{font-size:17px;font-weight:700;font-variant-numeric:tabular-nums}
.chart-tip .t-v span{font-size:11.5px;font-weight:500;color:#c9d1d9;margin-left:3px}
.chart-tip .t-c{font-size:11.5px;font-variant-numeric:tabular-nums}
.chart-tip .t-c.up{color:#ff8f80}
.chart-tip .t-c.down{color:#5fdba0}
.range-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px;
  margin:10px 0 2px}
.rs{background:#fafbfc;border:1px solid var(--line2);border-radius:9px;padding:9px 12px}
.rs-l{display:block;font-size:11.5px;color:var(--dim);margin-bottom:2px}
.rs-v{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums}
.chart-src{font-size:11.5px;color:var(--dim);margin:8px 0 0;line-height:1.6}
@media(max-width:820px){
  #spotChart{height:230px}
  #frozenChart,#ratioChart{height:250px}
  .range-hint{display:none}
  .range-tabs{width:100%;justify-content:space-between}
  .range-tabs button{flex:1;padding:8px 6px;font-size:12.5px}
}
.offline-tag{margin-top:8px;padding:7px 12px;border-radius:8px;font-size:12.5px;
  background:#fff8e6;border:1px solid var(--warn-line);color:#7a4b06;text-align:left}
.mkt-state{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;
  margin:12px 0 0;padding:9px 13px;border-radius:9px;font-size:12.8px;
  background:#f7f9fb;border:1px solid var(--line);color:var(--ink2)}
.mkt-state b{font-size:12.5px;white-space:nowrap}
.mkt-state.live{background:#fff8e6;border-color:var(--warn-line);color:#7a4b06}
.mkt-state.live b{color:#9a5b00}
.mkt-state.closed{background:#f0f9f4;border-color:#c6e6d5;color:#1e6b48}
.mkt-state.closed b{color:#1e6b48}
.mkt-state.warn{background:#fff6f5;border-color:#f3c9c3;color:#9b2c20}
.subbox.conflict{background:#fff6f5;border-color:#f3c9c3}
.subbox.conflict h4{color:#9b2c20}
/* 折叠区：点击标题向下展开，避免长段落平铺 */
details.fold{margin:12px 0 0;border:1px solid var(--line);border-radius:10px;background:#fafbfc;overflow:hidden}
details.fold>summary{cursor:pointer;list-style:none;padding:11px 14px;font-size:13.5px;font-weight:650;
  color:var(--ink);display:flex;align-items:center;gap:8px;user-select:none}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::after{content:"展开";margin-left:auto;font-size:11.5px;font-weight:600;
  color:var(--dim);border:1px solid var(--line);border-radius:999px;padding:1px 8px;background:#fff;
  white-space:nowrap;flex:0 0 auto}
details.fold[open]>summary::after{content:"收起"}
details.fold>summary:hover{background:#f3f5f8}
details.fold>.fold-body{padding:0 14px 13px;border-top:1px solid var(--line2)}
details.fold>.fold-body>ul.tight{margin-top:11px}
details.fold.conflict{background:#fff6f5;border-color:#f3c9c3}
details.fold.conflict>summary{color:#9b2c20}
details.fold.conflict>summary::after{border-color:#f3c9c3;color:#9b2c20}
details.fold.gap{background:#f7f9fb}
.chart-legend{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 2px}
.chart-legend button{display:inline-flex;align-items:center;gap:7px;appearance:none;cursor:pointer;
  font:inherit;font-size:12px;font-weight:600;color:var(--ink2);background:#fff;
  border:1px solid var(--line);border-radius:999px;padding:5px 11px}
.chart-legend button .sw{width:16px;height:3px;border-radius:2px;background:currentColor}
.chart-legend button.off{opacity:.38;filter:grayscale(1)}
.chart-legend button .cnt{color:var(--dim);font-weight:500}
#frozenChart,#ratioChart{display:block;width:100%;height:320px;overflow:visible}
.chart-note{font-size:11.5px;color:var(--dim);margin:6px 0 0;line-height:1.65}
.gauge{margin:16px 0 4px}
.ratio-bar{margin:16px 0 4px}
.gauge-track,.ratio-track{position:relative;height:26px;background:#eceff2;border-radius:7px;overflow:hidden}
.gauge-green,.ratio-band{position:absolute;top:0;bottom:0;background:#cdebd8}
.gauge-mark,.ratio-mark{position:absolute;top:-4px;bottom:-4px;width:3px;background:var(--ink);border-radius:2px}
.ratio-band{background:#ffe6c7}
.gauge-legend{display:flex;justify-content:space-between;font-size:11.5px;color:var(--dim);margin-top:6px;gap:8px}
.gauge-legend span:nth-child(2){color:var(--ink2)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
.two-col>*{min-width:0}
.timeline,.subbox,details.src{min-width:0;overflow-wrap:anywhere}
body{overflow-x:hidden}
.mini-row{display:flex;flex-wrap:wrap;gap:18px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line2)}
.mini{display:flex;align-items:baseline;gap:7px;font-size:13px}
.mini-l{color:var(--dim)}
.mini-v{font-weight:650;font-variant-numeric:tabular-nums}
.mini-c{font-size:12px;font-variant-numeric:tabular-nums}
.pill{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid;font-weight:600}
.pill.pending{color:var(--third);border-color:#fcd9a8;background:#fff7ed}
.warn{color:var(--third)}
details.src{margin-top:14px;border-top:1px dashed var(--line);padding-top:10px}
details.src summary{cursor:pointer;font-size:13px;color:var(--ink2);font-weight:600;list-style:none}
details.src summary::-webkit-details-marker{display:none}
details.src summary::before{content:"▸ ";color:var(--dim)}
details.src[open] summary::before{content:"▾ "}
details.src dl{margin:10px 0 0;display:grid;grid-template-columns:96px 1fr;gap:6px 14px;font-size:12.8px}
details.src dt{color:var(--dim)}
details.src dd{margin:0;color:var(--ink2)}
footer{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);font-size:12.5px;color:var(--dim)}
footer p{margin:6px 0}
.timeline{margin-top:22px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.timeline h2{margin:0 0 6px;font-size:16.5px}
.timeline .hint{font-size:12.5px;color:var(--dim);margin:0 0 14px}
.tl-item{border-left:2px solid var(--line);padding:0 0 16px 16px;position:relative}
.tl-item:last-child{padding-bottom:0}
.tl-item::before{content:"";position:absolute;left:-5px;top:6px;width:8px;height:8px;border-radius:50%;
  background:var(--ink2)}
.tl-ts{font-size:12px;color:var(--dim);font-family:ui-monospace,Menlo,monospace}
.tl-title{font-size:13.5px;font-weight:650;margin:2px 0 3px}
.tl-body{font-size:13px;color:var(--ink2)}
@media(max-width:820px){
  .grid{grid-template-columns:1fr}
  .two-col{grid-template-columns:1fr}
  .hero-v{font-size:34px}
  .meta{text-align:left}
  .wrap{padding:16px 12px 48px}
  .card{padding:16px 14px 14px;border-radius:12px}
  .card-h h3{font-size:15.5px}
  .asof{font-size:11.5px}
  .top{padding-bottom:14px}
  h1{font-size:22px}
  .sub{font-size:12.5px}
  .tbl th,.tbl td{padding:8px 10px;font-size:13px}
  .chip{font-size:12px;padding:3px 8px}
}
/* iOS 安全区（刘海/底部横条） */
@supports(padding:max(0px)){
  .wrap{padding-left:max(12px,env(safe-area-inset-left));
        padding-right:max(12px,env(safe-area-inset-right));
        padding-bottom:max(48px,env(safe-area-inset-bottom))}
}
/* 独立运行时（已添加到主屏幕）顶部更紧凑 */
@media(display-mode:standalone){
  .wrap{padding-top:14px}
}
/* 触摸设备去掉 hover 依赖 */
@media(hover:none){
  details.src summary{padding:6px 0}
}
"""


ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="104" fill="#15181d"/>
<text x="256" y="214" font-size="150" text-anchor="middle" fill="#ffffff"
 font-family="PingFang SC,Hiragino Sans GB,Microsoft YaHei,sans-serif" font-weight="700">生猪</text>
<text x="256" y="358" font-size="132" text-anchor="middle" fill="#4ade80"
 font-family="PingFang SC,Hiragino Sans GB,Microsoft YaHei,sans-serif" font-weight="700">¥</text>
<rect x="96" y="404" width="320" height="14" rx="7" fill="#c0392b"/>
</svg>"""


def _make_service_worker(snapshot):
    """生成带版本号的 Service Worker，使 iPhone 可离线查看最后同步内容。"""
    ver = "v-" + str(snapshot.get("generated_at", ""))[:10].replace("-", "") + "-" + \
          store.value_hash(snapshot.get("modules", {}))[:6]
    out = os.path.join(store.ROOT, "web", "sw.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write(("// 由 build.py 自动生成，请勿手改\n" + SW_TEMPLATE).replace("__VERSION__", ver))
    return ver, out


def _make_icons():
    """生成 PWA 图标（优先 Pillow 绘制，失败则退回 SVG，不影响看板可用性）。"""
    import glob

    out_dir = os.path.join(store.ROOT, "web", "icons")
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, "icon.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(ICON_SVG)

    made = []
    try:
        from PIL import Image, ImageDraw, ImageFont

        def load_font(size):
            for p in (
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Medium.ttc",
                "/System/Library/Fonts/Hiragino Sans GB.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
            ):
                if os.path.exists(p):
                    try:
                        return ImageFont.truetype(p, size)
                    except Exception:
                        continue
            return ImageFont.load_default()

        for size in (180, 192, 512):
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            r = int(size * 0.203)
            d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(21, 24, 29, 255))
            f_big, f_yen = load_font(int(size * 0.30)), load_font(int(size * 0.26))
            d.text((size / 2, size * 0.40), "生猪", font=f_big, fill=(255, 255, 255, 255), anchor="mm")
            d.text((size / 2, size * 0.70), "¥", font=f_yen, fill=(74, 222, 128, 255), anchor="mm")
            d.rounded_rectangle(
                [size * 0.19, size * 0.79, size * 0.81, size * 0.79 + size * 0.028],
                radius=size * 0.014, fill=(192, 57, 43, 255),
            )
            name = "apple-touch-icon.png" if size == 180 else f"icon-{size}.png"
            img.save(os.path.join(out_dir, name))
            made.append(name)
    except Exception as e:  # noqa: BLE001
        made.append(f"（Pillow 生成失败，回退 SVG：{type(e).__name__}）")

    manifest = {
        "name": "生猪市场看板",
        "short_name": "生猪看板",
        "description": "全国外三元均价 · 大商所生猪期货 · 能繁母猪存栏 · 冻品库容率 · 淘汰母猪比价",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f5f6f8",
        "theme_color": "#f5f6f8",
        "lang": "zh-CN",
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    with open(os.path.join(store.ROOT, "web", "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return made


def build_dashboard(snapshot):
    mods = snapshot["modules"]
    order = [m for m in snapshot.get("module_order", list(mods)) if m in mods]
    cards = []
    for mid in order:
        m = mods[mid]
        fn = CARD_FN.get(mid)
        if not fn:
            continue
        html = fn(m)
        if m.get("stale"):
            html = html.replace('class="card', 'class="card stale', 1)
        cards.append(html)

    stale_msgs = []
    for mid, m in mods.items():
        if m.get("stale"):
            stale_msgs.append(
                f'<li><b>{esc(m.get("name", mid))}</b>：本次采集失败，展示的是上一期成功数据'
                f'（统计日 {esc(m.get("stat_date") or "—")}）。错误：<span class="mono">{esc(m.get("error_detail", ""))}</span></li>'
            )
    stale_banner = (
        f'<div class="stale-banner"><b>⚠ 数据源异常告警</b><ul>{"".join(stale_msgs)}</ul></div>'
        if stale_msgs
        else ""
    )

    entries = store.read_changelog(limit=25)[::-1]
    tl = "".join(
        f'<div class="tl-item"><div class="tl-ts">{esc(e["ts"])} · 统计日 {esc(e.get("stat_date") or "—")}</div>'
        f'<div class="tl-title">{esc(e["module_name"])}'
        f' <span class="badge {KIND_LABEL.get(e.get("kind","market"), ("", ""))[1]}">'
        f'{KIND_LABEL.get(e.get("kind","market"), ("市场行情","market"))[0]}</span></div>'
        f'<div class="tl-body">{esc(e["summary"])}</div></div>'
        for e in entries
    )
    if not tl:
        tl = '<p class="dim">尚无变更记录。看板只在数据或官方发布发生变化时写入变更条目。</p>'

    n_official = sum(1 for m in mods.values() if m.get("kind") == "official")
    n_third = sum(1 for m in mods.values() if m.get("kind") == "third_party")
    n_market = sum(1 for m in mods.values() if m.get("kind") == "market")

    payload = json.dumps(
        {"generated_at": snapshot["generated_at"], "modules": {k: {"stat_date": v.get("stat_date"),
         "publish_date": v.get("publish_date"), "kind": v.get("kind")} for k, v in mods.items()}},
        ensure_ascii=False,
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#f5f6f8">
<meta name="color-scheme" content="light">
<!-- iOS 添加到主屏幕后全屏运行 -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="生猪看板">
<meta name="format-detection" content="telephone=no">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="192x192" href="icons/icon-192.png">
<title>生猪市场看板 · 数据核实版</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>生猪市场看板</h1>
      <p class="sub">价格 · 期货 · 产能 · 冻品库存 · 淘汰强度　|　同一口径留痕，仅在数据变化时改写</p>
    </div>
    <div class="meta">
      <div>数据快照生成：<b>{esc(snapshot['generated_at'])}</b></div>
      <div>自动巡检时刻：<span id="next-check">07:45 / 09:30 / 12:30 / 15:30 / 17:30 / 21:30</span></div>
      <div>最后巡检：<span id="last-check">读取中…</span></div>
      <div>模块：官方 {n_official} · 第三方样本 {n_third} · 市场行情 {n_market}</div>
      <div class="offline-tag" hidden>⚠ 离线 · 正在显示最后一次同步的内容</div>
    </div>
  </div>

  {stale_banner}

  <div class="grid">
    {''.join(cards)}
  </div>

  <div class="timeline">
    <h2>变更时间线</h2>
    <p class="hint">每一条都对应一次真实的数据/官方发布变化。没有新数据时不会产生条目，看板文件也不会被改写。</p>
    {tl}
  </div>

  <footer>
    <p><b>使用约定：</b>本看板坚持同一统计口径，每条数据均标注发布主体、统计截止日与发布日期；
      官方口径与第三方样本口径分区展示，不混用、不拼接、不平均。</p>
    <p><b>第三方样本声明：</b>冻品库容率、淘汰母猪价格、样本能繁母猪存栏均为第三方样本口径，
      仅代表样本企业情况，不代表全国水平，不可与官方数据并列比较。</p>
    <p><b>免责：</b>本看板为私有数据核实工具，数据来自公开渠道，可能存在采集延迟或源站改版导致的偏差；
      任何投资决策请以交易所及政府官方原始发布为准。</p>
    <p class="dim">部署方式：本地私有静默部署 · 静态页面无外部依赖 ·
      数据快照 <span class="mono">data/snapshot.json</span> ·
      变更留痕 <span class="mono">data/changelog.jsonl</span></p>
  </footer>
</div>
<script id="board-data" type="application/json">{payload}</script>
<script>{SW_REG_JS}</script>
<script>{SPOT_JS}</script>
<script>{MULTI_CHART_JS}</script>
<script>
(function(){{
  if(!window.__initMultiChart) return;
  window.__initMultiChart({{dataId:'frozenData',svgId:'frozenChart',tipId:'frozenTip',legendId:'frozenLegend',defaultSpan:365}});
  window.__initMultiChart({{dataId:'ratioData',svgId:'ratioChart',tipId:'ratioTip',legendId:'ratioLegend',defaultSpan:180}});
}})();
</script>
<script>
(function(){{
  var el = document.getElementById('last-check');
  var nx = document.getElementById('next-check');
  fetch('data/heartbeat.json?_=' + Date.now(), {{cache:'no-store'}})
    .then(function(r){{ return r.ok ? r.json() : null; }})
    .then(function(h){{
      if(!h) {{ el.textContent = '离线查看（未连接本地服务）'; nx.textContent = '—'; return; }}
      el.textContent = h.last_check + (h.data_changed ? '（本次有数据更新）' : '（本次无变化，未改写看板）');
      if(nx && h.schedule) {{ nx.textContent = h.schedule; }}
    }})
    .catch(function(){{ el.textContent = '离线查看（未连接本地服务）'; nx.textContent = '—'; }});
}})();
</script>
</body>
</html>
"""
    _make_icons()
    _make_service_worker(snapshot)

    # 云端托管（GitHub Pages）时没有本地服务，页面仍需显示"数据截止/最后更新"。
    # 生成一份静态心跳到 web/data/ 下，使相对路径 data/heartbeat.json 在两种部署下都能解析。
    # 只在看板重建时写入，因此不会制造无谓提交。
    try:
        os.makedirs(os.path.join(store.ROOT, "web", "data"), exist_ok=True)
        store.write_json(
            os.path.join(store.ROOT, "web", "data", "heartbeat.json"),
            {
                "last_check": snapshot["generated_at"],
                "data_changed": True,
                "last_data_change": snapshot["generated_at"],
                "schedule": "07:45 / 09:30 / 12:30 / 15:30 / 17:30 / 21:30",
                "mode": "cloud",
                "source_errors": snapshot.get("source_errors", {}),
            },
        )
    except Exception:
        pass
    out = os.path.join(store.ROOT, "web", "index.html")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    snap = store.load_snapshot()
    if not snap:
        raise SystemExit("尚无快照，请先运行 pipeline.py")
    print("已重建:", build_dashboard(snap))
