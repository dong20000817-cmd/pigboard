# -*- coding: utf-8 -*-
"""
生猪市场看板 —— 统一口径与数据源登记表

设计原则
--------
1. 每个指标只允许一个"主口径"（primary），口径一旦确定不再切换；
   如需换源，必须新增 caliber 版本号并保留旧源历史，避免序列断裂。
2. 每条数据必须能回答四个问题：谁发布的 / 什么口径 / 统计截止到哪天 / 哪天发布。
3. 冻品库容率天然只有第三方样本口径，必须在看板上显式标注"第三方样本"。

时间一律使用 Asia/Shanghai。
"""

TIMEZONE = "Asia/Shanghai"
PROJECT_NAME = "生猪市场看板"

# ---------------------------------------------------------------------------
# 指标口径登记
# ---------------------------------------------------------------------------
# caliber 字段说明：
#   unit        计量单位（全库统一，禁止混用元/斤与元/公斤）
#   publisher   发布主体
#   sample      样本范围（第三方数据必须写清）
#   freq        更新频率
#   kind        official=官方发布 / third_party=第三方样本 / market=市场行情
#   note        口径备注

INDICATORS = {
    # ---------------- 1. 全国外三元生猪均价（日度） ----------------
    "hog_spot": {
        "name": "全国外三元生猪均价",
        "unit": "元/公斤",
        "caliber": "全国外三元（杜长大）生猪出栏均价，全国综合均价，非省级简单算术平均",
        "publisher": "中国养猪网（玄田数据）",
        "sample": "全国口径综合报价",
        "freq": "日度（含周末与节假日，无报价日顺延）",
        "kind": "market",
        "source_url": "https://xt.yangzhu.vip/manage/datamap/ptype/1/areano/-1.html",
        "api": "POST https://xt.yangzhu.vip/data/getzhujiatoday",
        "compare_rule": "与上一有效报价日对比，涨跌额=当期值-上一有效报价日值",
        "note": "单位为元/公斤；页面同时提供同比、环比。1元/斤=2元/公斤。",
    },

    # ---------------- 2. 大商所生猪期货（日度） ----------------
    "dce_futures": {
        "name": "大商所生猪期货合约收盘价",
        "unit": "元/吨",
        "caliber": "大连商品交易所生猪期货（代码 lh）全部挂牌合约日收盘价与结算价",
        "publisher": "大连商品交易所（行情经东方财富行情接口镜像）",
        "sample": "全部活跃合约：lh2611/lh2701/lh2703/lh2705/lh2707/lh2709（随挂牌滚动）",
        "freq": "每交易日 15:00 收盘后",
        "kind": "market",
        "source_url": "https://futsseapi.eastmoney.com/list/114",
        "api": "GET https://futsseapi.eastmoney.com/list/114?orderBy=dm&sort=asc&pageSize=500&pageIndex=0",
        "compare_rule": "涨跌=当日收盘价-上一交易日结算价；幅度=涨跌/上一交易日结算价",
        "note": "收盘价与结算价是两个概念：交易所日内盈亏按结算价计算，看板同时给出两者。",
    },

    # ---------------- 3. 能繁母猪存栏（官方） ----------------
    "sow_inventory": {
        "name": "能繁母猪存栏",
        "unit": "万头",
        "caliber": "国家统计局季度末全国能繁母猪存栏（季度累计口径）；农业农村部月度环比为辅助校验",
        "publisher": "国家统计局 / 农业农村部",
        "sample": "全国全面统计（非样本）",
        "freq": "季度（统计局） / 月度（农业农村部环比）",
        "kind": "official",
        "source_url": "https://www.stats.gov.cn/",
        "compare_rule": "环比=本期末-上期末；同比=本期末-上年同期",
        "note": "官方数据仅在发布日更新，非发布日不得改动看板该模块。",
    },

    # ---------------- 4. 冻品库容率（第三方样本） ----------------
    "frozen_inventory": {
        "name": "猪肉冻品库容率",
        "unit": "%",
        "caliber": "屠宰企业冻品库存量 / 有效库容。**第三方样本口径**：仅覆盖样本库内屠宰企业，不等于全国库存。",
        "publisher": "上海钢联 Mysteel 农产品 / 卓创资讯 / 涌益咨询（取公开披露值）",
        "sample": "第三方样本企业，具体样本量与省份覆盖以机构当期披露为准",
        "freq": "周度（部分机构月度）",
        "kind": "third_party",
        "source_url": "https://ncp.mysteel.com/",
        "compare_rule": "与上一期披露值对比，单位为百分点（pp）",
        "note": "该指标不存在官方口径。看板必须与能繁母猪等官方数据分区展示，禁止并列为同一可信度。",
        "disclaimer": "第三方样本口径，仅代表样本企业情况，不代表全国冻品库存水平。",
    },

    # ---------------- 5. 淘汰母猪 / 商品猪 价格比 ----------------
    "cull_sow_ratio": {
        "name": "淘汰母猪与商品猪价格比",
        "unit": "%",
        "caliber": "淘汰母猪出栏价 ÷ 同期商品猪（外三元标猪）出栏价 × 100%，两者单位必须同为元/公斤",
        "publisher": "分子：Mysteel/涌益等第三方样本；分母：中国养猪网全国外三元均价",
        "sample": "第三方样本口径（淘汰母猪价）；全国综合（商品猪价）",
        "freq": "随分子可得频率更新（通常周度）",
        "kind": "third_party",
        "source_url": "https://ncp.mysteel.com/",
        "compare_rule": "比值下行=淘汰母猪相对贬值=淘汰强度上升；比值上行=淘汰猪相对抗跌",
        "note": (
            "解读：比值越低，说明淘汰母猪卖不上价、市场淘汰压力越大。"
            "经验区间约 45%~75%，低于 50% 通常对应深度亏损与加速去产能。"
            "本比值由本看板自行计算，非任何机构原发布值。"
        ),
    },
}

# 看板模块顺序
MODULE_ORDER = ["hog_spot", "dce_futures", "sow_inventory", "frozen_inventory", "cull_sow_ratio"]

# 大商所生猪期货合约筛选规则：只保留仍挂牌的活跃合约
DCE_VARIETY_CODE = "lh"
DCE_EM_MARKET = 114  # 东方财富期货市场代码：114 = 大连商品交易所
