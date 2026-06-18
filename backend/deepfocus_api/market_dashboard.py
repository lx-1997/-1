from __future__ import annotations

import asyncio
import csv
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .shared_utils import safe_float, to_float, utc_now_iso

REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
# FRED 免 key CSV 在沙箱内连发会触发挑战页（间歇 HTML/空），单独用更短超时 + 重试 + 限并发兜住。
FRED_TIMEOUT = httpx.Timeout(3.0, connect=2.0)
_FRED_SEMAPHORE = asyncio.Semaphore(4)
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
CACHE_TTL_SECONDS = 300
_cached_result: Optional[dict[str, Any]] = None
_cached_at: float = 0.0

_MACRO_SYMBOLS = {
    "vix": "^VIX",
    "spx": "^GSPC",
    "dxy": "DX-Y.NYB",
    "gold": "GC=F",
    "oil": "CL=F",
    "ten_year_yield": "^TNX",
    "two_year_yield": "2YY=F",
    "bitcoin": "BTC-USD",
    "hyg": "HYG",
    "lqd": "LQD",
    "tlt": "TLT",
    "usd_jpy": "JPY=X",
    "usd_cny": "CNY=X",
    "copper": "HG=F",
    "natgas": "NG=F",
    "spx_vix": "^VIX",
    "aapl": "AAPL",
    "nvda": "NVDA",
    "msft": "MSFT",
}

_FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",
    "unemployment": "UNRATE",
    "industrial_production": "INDPRO",
    "m2_money_supply": "M2SL",
    "sp500_pe": "MULTPL/SP500_PE_RATIO_MONTH",
}

_STOOQ_SYMBOL_MAP = {
    "^GSPC": "^spx",
    "DX-Y.NYB": "dx.f",
    "GC=F": "gc.f",
    "CL=F": "cl.f",
    "BTC-USD": "btc.v",
    "HYG": "hyg.us",
    "LQD": "lqd.us",
}

# 历史上硬编码的 key 已失效（返回 400 api_key is not registered），主路径恒挂。
# 改为可选从环境变量读取；缺失则直接走无 key 的 fredgraph.csv（沙箱内直连可达）。
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# 新浪财经全球行情：沙箱内直连实时可达，替代被 bot/地域封死的 Yahoo/Stooq。
# key -> (sina_code, kind)；kind 决定字段解析格式。
_SINA_GLOBAL = {
    "spx": ("int_sp500", "index"),       # name,price,change,change_pct
    "vix": ("hf_VX", "futures"),         # 外盘期货：current,,bid,ask,high,low,time,prev_settle,open,...
    "gold": ("hf_GC", "futures"),
    "oil": ("hf_CL", "futures"),
    "bitcoin": ("hf_BTC", "futures"),
    "dxy": ("DINIW", "dxy"),             # time,current,...
}
_SINA_USD_CNY = ("fx_susdcny", "fx")     # time,current,...,change_pct(@10),change(@11),...


def _build_indicator(
    key: str,
    name: str,
    category: str,
    value: float,
    unit: str = "",
    change: Optional[float] = None,
    change_pct: Optional[float] = None,
    signal: str = "neutral",
    status: str = "",
    description: str = "",
    historical_context: str = "",
    current_reading: str = "",
    source: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "category": category,
        "value": round(value, 4),
        "unit": unit,
        "change": round(change, 4) if change is not None else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "signal": signal,
        "status": status,
        "description": description,
        "historical_context": historical_context,
        "current_reading": current_reading,
        "source": source,
    }


_INSIGHT_TEMPLATES = {
    "vix": {
        "description": "CBOE波动率指数，衡量S&P 500未来30天隐含波动率。",
        "historical_context": "长期均值19.5。低于12为极致平静，12-20低波动，20-30中度恐慌，30+高度恐慌，40+恐慌抛售。",
        "interpret": lambda v: (
            "极致平静 — 市场自满，回调风险累积" if v < 12 else
            "低波动 — 市场平静，趋势上行概率大" if v < 16 else
            "正常 — 波动率处于历史均值附近" if v < 22 else
            "中度恐慌 — 市场焦虑上升，注意风险管理" if v < 30 else
            "高度恐慌 — 避险情绪主导，可能出现V型反弹机会" if v < 40 else
            "恐慌抛售 — 极端恐惧，历史上通常是中长期买入良机"
        ),
        "signal": lambda v: "bearish" if 20 <= v < 30 else ("strong_bearish" if v >= 30 else ("bullish" if v < 16 else "neutral")),
        "unit": "",
    },
    "yield_curve": {
        "description": "10年期与2年期美债利差。倒挂（负值）是历史上最可靠的衰退领先指标。",
        "historical_context": "1960年以来，每次2s10s倒挂后6-24个月均出现衰退。倒挂幅度>50bp为深度倒挂。",
        "interpret": lambda v: (
            "深度倒挂 — 衰退信号强烈，通常领先6-18个月" if v < -0.5 else
            "中度倒挂 — 经济放缓预警" if v < -0.1 else
            "轻微倒挂 — 衰退风险上升，但尚未确认" if v < 0 else
            "正常偏平 — 利差恢复中，关注是否持续" if v < 0.5 else
            "正常 — 收益率曲线健康，经济扩张环境" if v < 1.5 else
            "陡峭 — 增长预期强劲，通胀预期上升"
        ),
        "signal": lambda v: "bearish" if v < 0 else ("bullish" if v > 1.0 else "neutral"),
        "unit": "%",
    },
    "ten_year": {
        "description": "美国10年期国债收益率，全球资产定价之锚。",
        "historical_context": "长期均值约4.0%。4.5%+为高利率环境，3.5%以下为低利率。",
        "interpret": lambda v: (
            "极低 — 通缩/衰退预期主导" if v < 2.0 else
            "低利率 — 宽松货币环境，利好成长股估值" if v < 3.5 else
            "适中 — 中性利率区间" if v < 4.5 else
            "偏高 — 压制估值，利好价值股和短久期资产" if v < 5.5 else
            "高利率 — 紧缩压力显著，债市承压"
        ),
        "signal": lambda v: "bearish" if v > 5.0 else ("bullish" if v < 3.5 else "neutral"),
        "unit": "%",
    },
    "dxy": {
        "description": "美元指数，衡量美元对一篮子主要货币的强弱。与新兴市场、大宗商品呈负相关。",
        "historical_context": "100为基准。105+为强美元，压制新兴市场和商品；95以下为弱美元，利好出口与大宗商品。",
        "interpret": lambda v: (
            "极强 — 全球流动性收紧，新兴市场承压" if v > 108 else
            "偏强 — 美元资产吸引资金流入" if v > 103 else
            "中性 — 美元在均衡区间" if v > 97 else
            "偏弱 — 利好大宗商品和新兴市场" if v > 92 else
            "弱势 — 通胀输入风险，利好出口导向型经济体"
        ),
        "signal": lambda v: "bearish" if v > 106 else ("bullish" if v < 97 else "neutral"),
        "unit": "",
    },
    "gold": {
        "description": "黄金期货价格。传统避险资产，与实际利率呈强负相关。",
        "historical_context": "突破$2000后进入新的历史区间。$2500+为历史新高区域。",
        "interpret": lambda v: (
            "历史极值 — 避险需求/通胀对冲极度高涨" if v > 2800 else
            "强势 — 实际利率下行+避险需求驱动" if v > 2400 else
            "偏强 — 地缘风险或降息预期支撑" if v > 2100 else
            "中性 — 金价在均衡区间" if v > 1800 else
            "偏弱 — 风险偏好上升，避险需求减弱"
        ),
        "signal": lambda v: "bearish" if v > 2800 else "neutral",
        "unit": "$",
    },
    "oil": {
        "description": "WTI原油期货。全球经济增长温度计和通胀先行指标。",
        "historical_context": "$60-80为OPEC+舒适区。$90+抑制消费并推升通胀，$50以下反映衰退预期。",
        "interpret": lambda v: (
            "极高 — 能源成本严重压制消费和经济" if v > 95 else
            "偏高 — 通胀压力上升，能源股受益" if v > 80 else
            "适中 — OPEC+舒适区，经济中性" if v > 60 else
            "偏低 — 需求疲弱或供给过剩" if v > 45 else
            "极低 — 衰退信号或供给冲击消退"
        ),
        "signal": lambda v: "bearish" if v > 90 else ("bullish" if 60 <= v <= 80 else "neutral"),
        "unit": "$",
    },
    "bitcoin": {
        "description": "比特币价格。数字黄金和全球风险偏好/流动性先行指标。",
        "historical_context": "与纳指高度相关，对全球M2流动性极其敏感。",
        "interpret": lambda v: (
            "极度亢奋 — 注意过热风险" if v > 100000 else
            "强势 — 风险偏好和流动性充裕" if v > 70000 else
            "中性偏强 — 资金流入加密生态" if v > 50000 else
            "中性偏弱 — 风险偏好回落" if v > 30000 else
            "弱势 — 流动性收紧或风险回避"
        ),
        "signal": lambda v: "bullish" if v > 70000 else "neutral",
        "unit": "$",
    },
    "fed_funds_rate": {
        "description": "美联储联邦基金利率。全球最重要的政策利率。",
        "historical_context": "0-0.25%为零利率，2.5-3.5%为中性利率估计区间，5%+为限制性利率。",
        "interpret": lambda v: (
            "高度限制性 — 压制经济和通胀，降息预期高" if v > 5.0 else
            "偏紧 — 仍高于中性利率估计" if v > 4.0 else
            "接近中性 — 货币政策趋于均衡" if v > 2.5 else
            "偏松 — 刺激经济，支撑估值" if v > 1.0 else
            "零利率 — 极度宽松，泡沫风险上升"
        ),
        "signal": lambda v: "bearish" if v > 5.0 else ("bullish" if v < 3.0 else "neutral"),
        "unit": "%",
    },
    "credit_spread": {
        "description": "ICE BofA 美国高收益债期权调整利差(OAS)。高收益债相对无风险利率的风险溢价，衡量信用市场压力。",
        "historical_context": "正常范围300-500bp。低于300bp为信用乐观，500bp+为信用紧缩，800bp+为信用危机。",
        "interpret": lambda v: (
            "利差极窄 — 信用市场极度乐观，风险定价偏低" if v < 250 else
            "偏窄 — 风险偏好高，企业融资环境宽松" if v < 400 else
            "正常 — 信用市场健康" if v < 550 else
            "走阔 — 信用风险上升，关注违约与再融资压力" if v < 800 else
            "信用危机 — 利差飙升，流动性枯竭，系统性风险"
        ),
        "signal": lambda v: "strong_bearish" if v >= 800 else ("bearish" if v >= 550 else ("bullish" if v < 400 else "neutral")),
        "unit": "bp",
    },
    "real_rate": {
        "description": "10年期通胀保值国债(TIPS)实际收益率。剔除通胀后的真实资金成本，是全球资产估值的根本之锚——实际利率上行直接压缩股票/黄金/长久期资产的估值。",
        "historical_context": "2010s 长期为负至0附近(零成本资金，估值繁荣)。0%为中性，1.5%+开始压制估值，2.5%+为显著限制性。",
        "interpret": lambda v: (
            "负实际利率 — 资金真实成本为负，极力推升估值" if v < 0 else
            "极低实际利率 — 估值强顺风，利好成长/黄金" if v < 0.5 else
            "中性实际利率 — 估值影响中性" if v < 1.5 else
            "偏高实际利率 — 压制估值，利好价值/短久期" if v < 2.5 else
            "高实际利率 — 显著限制性，估值承压、黄金逆风"
        ),
        "signal": lambda v: "bearish" if v > 2.0 else ("bullish" if v < 0.5 else "neutral"),
        "unit": "%",
    },
    "breakeven": {
        "description": "10年期盈亏平衡通胀率(名义10Y - 实际10Y)。市场隐含的未来10年平均通胀预期，前瞻性远胜已实现的CPI/油价。",
        "historical_context": "联储目标约2%。2.0-2.5%为良性锚定，2.8%+为通胀预期脱锚风险，1.5%以下为通缩/衰退担忧。",
        "interpret": lambda v: (
            "通胀预期高企 — 脱锚风险，联储紧缩压力大" if v > 3.0 else
            "通胀预期偏高 — 高于目标，关注黏性" if v > 2.6 else
            "通胀预期锚定 — 2%目标附近，最健康" if v > 2.0 else
            "通胀预期偏低 — 增长动能存疑" if v > 1.5 else
            "通缩预期 — 衰退/通缩风险显现"
        ),
        "signal": lambda v: "bearish" if (v > 2.8 or v < 1.5) else ("bullish" if 2.0 <= v <= 2.5 else "neutral"),
        "unit": "%",
    },
    "put_call_ratio": {
        "description": "CBOE总Put/Call比率。高于1.0为恐慌（看跌>看涨），低于0.6为贪婪。",
        "historical_context": "0.6-0.9为正常区间。>1.0短期看反弹（过度恐慌），<0.5注意反转。",
        "interpret": lambda v: (
            "极度恐慌 — 逆向指标暗示短期反弹" if v > 1.2 else
            "偏恐慌 — 防御情绪上升" if v > 0.9 else
            "正常 — 多空均衡" if v > 0.6 else
            "偏乐观 — 投机情绪上升" if v > 0.45 else
            "极度乐观 — 逆向指标暗示回调风险"
        ),
        "signal": lambda v: "bullish" if v > 1.0 else ("bearish" if v < 0.5 else "neutral"),
        "unit": "",
    },
    "unemployment": {
        "description": "美国失业率。萨姆规则：3个月均值较前12个月低点上升0.5%即衰退信号。",
        "historical_context": "自然失业率估计4.0-4.5%。低于4%为劳动力过热，高于6%为衰退区间。",
        "interpret": lambda v: (
            "极低 — 劳动力市场过热，工资通胀压力" if v < 3.5 else
            "偏低 — 就业强劲，消费支撑经济" if v < 4.5 else
            "正常 — 接近自然失业率" if v < 5.5 else
            "偏高 — 劳动力市场疲软，衰退风险" if v < 7.0 else
            "危机水平 — 深度衰退"
        ),
        "signal": lambda v: "bearish" if v > 5.5 else ("bullish" if v < 4.0 else "neutral"),
        "unit": "%",
    },
    "cpi": {
        "description": "CPI同比。衡量通胀的核心指标，直接影响美联储决策。",
        "historical_context": "美联储目标2%。3%+为高通胀，5%+为严重通胀，0%以下为通缩。",
        "interpret": lambda v: (
            "恶性通胀 — 购买力急剧下降" if v > 8 else
            "高通胀 — 紧缩货币政策持续" if v > 5 else
            "偏高通胀 — 高于联储目标，降息空间受限" if v > 3 else
            "接近目标 — 联储2%目标附近，政策空间灵活" if v > 1.5 else
            "低通胀 — 通缩风险，需宽松政策"
        ),
        "signal": lambda v: "bearish" if v > 4 else ("bullish" if 1.5 <= v <= 2.5 else "neutral"),
        "unit": "%",
    },
    "sp500_ma200": {
        "description": "S&P 500高于200日均线的成分股占比。衡量市场广度的核心指标。",
        "historical_context": "70%+为强势市场，40%以下为弱势。低于30%是极端超卖。",
        "interpret": lambda v: (
            "极度强势 — 全面牛市，可能过热" if v > 85 else
            "偏强 — 市场广度健康，趋势向上" if v > 65 else
            "中性 — 分化市场，选股能力重要" if v > 45 else
            "偏弱 — 广泛下跌，防御为主" if v > 25 else
            "极端弱势 — 恐慌超卖，逆向看反弹"
        ),
        "signal": lambda v: "bullish" if v > 65 else ("bearish" if v < 35 else "neutral"),
        "unit": "%",
    },
    "spx": {
        "description": "S&P 500 指数，美国大盘股核心基准，常用于衡量全球风险资产方向。",
        "historical_context": "指数本身更适合结合波动率、利率、市场广度和估值共同判断。",
        "interpret": lambda v: (
            "强趋势 — 风险偏好较高，关注估值和波动率是否背离" if v > 6500 else
            "高位运行 — 趋势偏强但需控制仓位节奏" if v > 5500 else
            "中性区间 — 方向选择期，等待宏观和盈利确认" if v > 4200 else
            "偏弱区间 — 风险偏好收缩，防御优先"
        ),
        "signal": lambda v: "bullish" if v > 5500 else ("bearish" if v < 4200 else "neutral"),
        "unit": "",
    },
}

_ASHARE_INSIGHT_TEMPLATES = {
    "sse_composite": {
        "description": "上证综合指数，A股核心基准。涵盖上海证券交易所全部上市股票。",
        "historical_context": "3000点为心理关口，2800以下为低估区间，3500以上为过热区间。",
        "interpret": lambda v: (
            "深跌 — 极端低估，历史上对应中长期底部区域" if v < 2800 else
            "低估 — 3000点心理关口下方，逆向布局机会" if v < 3000 else
            "正常 — 3000-3300核心波动区间" if v < 3300 else
            "偏强 — 突破核心区间，趋势向好" if v < 3600 else
            "过热 — 注意回调风险，仓位谨慎"
        ),
        "signal": lambda v: "bullish" if v < 2900 else ("bearish" if v > 3500 else "neutral"),
        "unit": "",
    },
    "csi300": {
        "description": "沪深300指数，A股核心蓝筹基准。涵盖沪深两市规模最大、流动性最好的300只股票。",
        "historical_context": "3500-4000为核心区间。4500+为强势区间，3000以下为低估。",
        "interpret": lambda v: (
            "深度低估 — 蓝筹股系统性低估" if v < 3200 else
            "偏低 — 估值具备吸引力" if v < 3700 else
            "适中 — 核心波动区间内" if v < 4200 else
            "偏强 — 蓝筹估值修复进行中" if v < 4700 else
            "强势 — 蓝筹股可能存在泡沫"
        ),
        "signal": lambda v: "bullish" if v < 3500 else ("bearish" if v > 4500 else "neutral"),
        "unit": "",
    },
    "chinext": {
        "description": "创业板指数，A股成长股/科技股代表。波动率约为上证2倍。",
        "historical_context": "1500-2000为核心区间。2500+为强势，1000以下为极端低估。",
        "interpret": lambda v: (
            "深度低估 — 成长股极端悲观" if v < 1200 else
            "偏低 — 成长股估值压缩" if v < 1600 else
            "正常 — 核心波动区间" if v < 2100 else
            "偏强 — 成长股情绪回暖" if v < 2600 else
            "强势 — 成长股可能过热"
        ),
        "signal": lambda v: "bullish" if v < 1400 else ("bearish" if v > 2500 else "neutral"),
        "unit": "",
    },
    "a_turnover": {
        "description": "沪深两市成交额（亿元）。A股最重要的情绪指标，量能决定行情持续性。",
        "historical_context": "万亿是牛熊分界线。8000亿以下为缩量整理，1.5万亿+为放量攻击，2万亿+为极度亢奋。",
        "interpret": lambda v: (
            "极度缩量 — 地量见地价，底部信号" if v < 5000 else
            "缩量整理 — 市场观望，等待方向选择" if v < 8000 else
            "正常 — 万亿附近，温和交易" if v < 12000 else
            "放量 — 增量资金入场，趋势可能升级" if v < 18000 else
            "极度放量 — 天量见天价，警惕短期见顶"
        ),
        "signal": lambda v: "bullish" if 8000 <= v <= 18000 else ("bearish" if v > 20000 else "neutral"),
        "unit": "亿",
    },
    "northbound_flow": {
        "description": "北向资金当日净流入（亿元）。外资通过沪深港通买卖A股的核心指标。",
        "historical_context": "单日净流入>100亿为强烈看多信号，<50亿为平淡，净流出>50亿为警示。",
        "interpret": lambda v: (
            "大举流入 — 外资强烈看多A股" if v > 100 else
            "温和流入 — 外资维持配置" if v > 30 else
            "小幅流入 — 外资态度中性偏积极" if v > 0 else
            "小幅流出 — 外资减仓，关注持续性" if v > -30 else
            "持续流出 — 外资看空信号，需警惕" if v > -80 else
            "大举流出 — 外资恐慌撤离，重大风险信号"
        ),
        "signal": lambda v: "strong_bullish" if v > 100 else ("bullish" if v > 30 else ("bearish" if v < -50 else ("strong_bearish" if v < -100 else "neutral"))),
        "unit": "亿",
    },
    "margin_balance": {
        "description": "融资余额（亿元）。杠杆资金情绪，反映散户和游资风险偏好。",
        "historical_context": "1.5万亿附近为历史高位。融资余额下降通常领先市场下跌。",
        "interpret": lambda v: (
            "极高 — 杠杆资金极度亢奋，警惕踩踏" if v > 16000 else
            "偏高 — 市场情绪偏热，杠杆风险累积" if v > 14000 else
            "正常 — 杠杆资金温和参与" if v > 12000 else
            "偏低 — 杠杆资金撤离，市场情绪低迷" if v > 10000 else
            "极低 — 极度悲观，往往对应市场底部"
        ),
        "signal": lambda v: "bearish" if v > 15000 else ("bullish" if v < 11000 else "neutral"),
        "unit": "亿",
    },
    "limit_up_count": {
        "description": "涨停家数。A股特有的情绪指标，涨停家数越多市场越亢奋。",
        "historical_context": "100+涨停为强势，50-100正常，30以下为弱势，200+为极度亢奋需警惕。",
        "interpret": lambda v: (
            "极度亢奋 — 市场过热，警惕情绪逆转" if v > 200 else
            "强势 — 赚钱效应扩散，市场情绪高涨" if v > 100 else
            "正常 — 结构性行情" if v > 50 else
            "偏弱 — 赚钱效应差，人气低迷" if v > 20 else
            "极度低迷 — 市场冰点，逆向看反弹"
        ),
        "signal": lambda v: "bullish" if 50 <= v <= 150 else ("bearish" if v > 200 else "neutral"),
        "unit": "家",
    },
    "limit_down_count": {
        "description": "跌停家数。A股恐慌指标，跌停家数越多市场越恐慌。",
        "historical_context": "10家以下正常，10-50家偏弱，50-100家恐慌，100+家极端恐慌。",
        "interpret": lambda v: (
            "正常 — 市场健康" if v < 10 else
            "偏弱 — 局部风险释放" if v < 30 else
            "恐慌 — 系统性风险担忧上升" if v < 80 else
            "高度恐慌 — 踩踏式抛售" if v < 200 else
            "极端恐慌 — 千股跌停级别，历史性底部信号"
        ),
        "signal": lambda v: "bearish" if v > 30 else ("strong_bearish" if v > 100 else ("bullish" if v < 5 else "neutral")),
        "unit": "家",
    },
    "usd_cny": {
        "description": "美元兑人民币汇率。人民币汇率是A股核心外部变量，贬值通常压制A股。",
        "historical_context": "7.0为关键心理关口。7.2+为贬值压力区间，6.5以下为升值区间。",
        "interpret": lambda v: (
            "大幅升值 — 资金回流中国，利好A股" if v < 6.5 else
            "升值 — 人民币偏强，外资流入动力增强" if v < 6.8 else
            "正常 — 7.0附近均衡" if v < 7.1 else
            "贬值 — 资本外流压力，A股承压" if v < 7.3 else
            "大幅贬值 — 汇率风险加剧，外资撤离信号"
        ),
        "signal": lambda v: "bearish" if v > 7.2 else ("bullish" if v < 6.8 else "neutral"),
        "unit": "",
    },
    "china_bond_10y": {
        "description": "中国10年期国债收益率。反映国内流动性和经济增长预期。",
        "historical_context": "2.5%-3.0%为正常区间。2%以下为极度宽松，3.5%以上为紧缩。",
        "interpret": lambda v: (
            "极度宽松 — 流动性充裕，利好成长股" if v < 2.0 else
            "宽松 — 货币政策偏松，估值支撑" if v < 2.5 else
            "中性 — 正常利率区间" if v < 3.0 else
            "偏紧 — 流动性收紧，压制估值" if v < 3.5 else
            "紧缩 — 利率上行，债券、股票双杀风险"
        ),
        "signal": lambda v: "bullish" if v < 2.5 else ("bearish" if v > 3.2 else "neutral"),
        "unit": "%",
    },
}


def _parse_sina_fields(kind: str, f: list[str]) -> dict[str, Any]:
    """把一行 sina 行情按品种格式解析成 {price, change, change_pct}。"""
    try:
        if kind == "index" and len(f) >= 4:           # name,price,change,change_pct
            return {"price": to_float(f[1]), "change": to_float(f[2]), "change_pct": to_float(f[3])}
        if kind == "futures" and len(f) >= 9:          # current,,bid,ask,high,low,time,prev_settle,open
            cur = to_float(f[0])
            settle = to_float(f[7])
            change = (cur - settle) if (cur is not None and settle is not None) else None
            change_pct = (change / settle * 100) if (change is not None and settle) else None
            return {"price": cur, "change": change, "change_pct": change_pct}
        if kind == "dxy" and len(f) >= 2:              # time,current,...
            return {"price": to_float(f[1]), "change": None, "change_pct": None}
        if kind == "fx" and len(f) >= 2:               # time,current,...,change_pct@10,change@11
            change_pct = to_float(f[10]) if len(f) > 10 else None
            change = to_float(f[11]) if len(f) > 11 else None
            return {"price": to_float(f[1]), "change": change, "change_pct": change_pct}
    except Exception:
        pass
    return {}


async def _fetch_sina_quotes(codes: list[str]) -> dict[str, list[str]]:
    """一次拉取多个 sina 行情，返回 {sina_code: [raw_fields...]}。GBK 编码，仅数值字段可靠。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    out: dict[str, list[str]] = {}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_SINA_HEADERS, trust_env=False) as client:
            resp = await client.get("https://hq.sinajs.cn/list=" + ",".join(codes))
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            if "hq_str_" not in line or '="' not in line:
                continue
            code = line.split("hq_str_", 1)[1].split("=", 1)[0]
            payload = line.split('="', 1)[1].rstrip(";").rstrip('"')
            if payload:
                out[code] = payload.split(",")
    except Exception:
        pass
    return out


async def _fetch_yahoo_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    headers = {"User-Agent": "DeepFocus/0.2 market-dashboard"}
    result: dict[str, dict[str, Any]] = {}
    try:
        joined = ",".join(symbols)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers, trust_env=False) as client:
            resp = await client.get(
                "https://query1.finance.yahoo.com/v7/finance/quote",
                params={"symbols": joined, "fields": "regularMarketPrice,regularMarketChange,regularMarketChangePercent,regularMarketPreviousClose,fiftyDayAverage,twoHundredDayAverage,shortName,longName"},
            )
            resp.raise_for_status()
            data = resp.json()
            for quote in data.get("quoteResponse", {}).get("result", []):
                sym = quote.get("symbol", "")
                result[sym] = {
                    "price": to_float(quote.get("regularMarketPrice")),
                    "change": to_float(quote.get("regularMarketChange")),
                    "change_pct": to_float(quote.get("regularMarketChangePercent")),
                    "name": quote.get("shortName") or quote.get("longName") or sym,
                    "ma50": to_float(quote.get("fiftyDayAverage")),
                    "ma200": to_float(quote.get("twoHundredDayAverage")),
                }
    except Exception:
        pass
    fallback = await _fetch_stooq_quotes([sym for sym in symbols if sym not in result])
    result.update(fallback)
    return result


async def _fetch_stooq_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    mapped = {symbol: _STOOQ_SYMBOL_MAP[symbol] for symbol in symbols if symbol in _STOOQ_SYMBOL_MAP}
    if not mapped:
        return {}
    result: dict[str, dict[str, Any]] = {}

    async def fetch_one(original_symbol: str, stooq_symbol: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = await client.get(
                    "https://stooq.com/q/l/",
                    params={"s": stooq_symbol, "f": "sd2t2ohlcvn", "h": "", "e": "csv"},
                )
                resp.raise_for_status()
            rows = list(csv.DictReader(resp.text.splitlines()))
            if not rows:
                return
            row = rows[0]
            close = to_float(row.get("Close"))
            if close is None:
                return
            open_price = to_float(row.get("Open"))
            change = close - open_price if open_price is not None else None
            change_pct = (change / open_price * 100) if change is not None and open_price else None
            result[original_symbol] = {
                "price": close,
                "change": change,
                "change_pct": change_pct,
                "name": row.get("Name") or original_symbol,
            }
        except Exception:
            return

    await asyncio.gather(*(fetch_one(original, mapped_symbol) for original, mapped_symbol in mapped.items()))
    return result


async def _fetch_fred_series(series_id: str, months: int = 3) -> Optional[float]:
    # 仅当显式配置了有效 key 时才走官方 JSON API；否则直接用无 key 的 CSV（沙箱内可达）。
    if FRED_API_KEY:
        try:
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=months * 32)
            params = {
                "series_id": series_id,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "observation_start": start_date.strftime("%Y-%m-%d"),
                "observation_end": end_date.strftime("%Y-%m-%d"),
                "sort_order": "desc",
                "limit": 3,
            }
            async with httpx.AsyncClient(timeout=FRED_TIMEOUT, trust_env=False) as client:
                resp = await client.get(FRED_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
                for obs in data.get("observations", []):
                    val = to_float(obs.get("value"))
                    if val is not None:
                        return val
        except Exception:
            pass
    return await _fetch_fred_csv_latest(series_id)


async def _fetch_treasury_yields() -> dict[str, Optional[float]]:
    """美国财政部每日国债到期收益率(par yield)。沙箱内直连可达且稳定，作为美债 10Y/2Y 的可靠主源
    （FRED 在本沙箱会封 IP）。CSV 按日期降序，首个数据行即最新交易日。"""
    year = datetime.now(timezone.utc).year
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&_format=csv"
    )
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text
            if "Date" not in text:
                raise ValueError("non-csv response")
            rows = list(csv.DictReader(text.splitlines()))
            if not rows:
                raise ValueError("empty")
            latest = rows[0]  # 降序，首行最新
            return {
                "ten_year": to_float(latest.get("10 Yr")),
                "two_year": to_float(latest.get("2 Yr")),
            }
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.8)
                continue
    return {"ten_year": None, "two_year": None}


async def _fetch_treasury_real_yields() -> dict[str, Optional[float]]:
    """美国财政部每日**实际**收益率曲线(TIPS)。10Y 实际利率是全球估值之锚；
    沙箱直连可达(FRED 在本沙箱封 IP)，CSV 降序首行最新。TIPS 从 5Y 起，故只取 5/10Y。"""
    year = datetime.now(timezone.utc).year
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_real_yield_curve&_format=csv"
    )
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text
            if "Date" not in text:
                raise ValueError("non-csv response")
            rows = list(csv.DictReader(text.splitlines()))
            if not rows:
                raise ValueError("empty")
            latest = rows[0]
            return {
                "real_10y": to_float(latest.get("10 YR")),
                "real_5y": to_float(latest.get("5 YR")),
            }
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.8)
                continue
    return {"real_10y": None, "real_5y": None}


async def _fetch_nyfed_effr() -> Optional[float]:
    """纽约联储有效联邦基金利率(EFFR)。沙箱直连可达且稳定，作为联邦基金利率的可靠主源
    （FRED 在本沙箱会封 IP）。"""
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json")
                resp.raise_for_status()
                data = resp.json()
            rates = data.get("refRates") or []
            if rates:
                val = to_float(rates[0].get("percentRate"))
                if val is not None:
                    return val
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.6)
                continue
    return None


async def _fetch_fred_csv_latest(series_id: str, months_ago: int = 0) -> Optional[float]:
    """无 key 拉 FRED CSV 最新值。沙箱内连发会被挑战页拦截（返回 HTML），故 trust_env=False +
    限并发(信号量) + 一次重试 + 校验确为 CSV，最大化命中率。"""
    target_date = None
    if months_ago > 0:
        target_date = (datetime.now(timezone.utc) - timedelta(days=months_ago * 32)).date()

    async with _FRED_SEMAPHORE:
        try:
            async with httpx.AsyncClient(timeout=FRED_TIMEOUT, trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(
                    "https://fred.stlouisfed.org/graph/fredgraph.csv",
                    params={"id": series_id},
                )
                resp.raise_for_status()
                text = resp.text
            # 挑战/错误页是 HTML，没有 CSV 表头，判失败（沙箱内 FRED 会间歇性拦截，由 300s 缓存兜住）
            if "observation_date" not in text and series_id not in text:
                return None
            for row in reversed(list(csv.DictReader(text.splitlines()))):
                if target_date is not None:
                    raw_date = row.get("observation_date") or row.get("DATE") or row.get("date")
                    if not raw_date:
                        continue
                    try:
                        row_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if row_date > target_date:
                        continue
                val = to_float(row.get(series_id))
                if val is not None:
                    return val
        except Exception:
            pass
    return None


async def _fetch_cboe_put_call() -> Optional[float]:
    try:
        headers = {"User-Agent": "DeepFocus/0.2"}
        # 短超时：CBOE 在沙箱内常不可达，避免拖慢整个看板冷取（用 FRED 同款 3.5s 上限）。
        async with httpx.AsyncClient(timeout=FRED_TIMEOUT, headers=headers, trust_env=False) as client:
            resp = await client.get("https://cdn.cboe.com/api/global/delayed_quotes/aggregate_totals/_data.json")
            resp.raise_for_status()
            data = resp.json()
            total_put = 0.0
            total_call = 0.0
            for item in data if isinstance(data, list) else []:
                ratio = _parse_put_call_entry(item)
                if ratio:
                    total_put += ratio[0]
                    total_call += ratio[1]
            if total_call > 0:
                return round(total_put / total_call, 4)
    except Exception:
        pass
    return None


def _parse_put_call_entry(item: Any) -> Optional[tuple[float, float]]:
    try:
        put_vol = to_float(item.get("put_volume") or item.get("puts_volume"))
        call_vol = to_float(item.get("call_volume") or item.get("calls_volume"))
        if put_vol is not None and call_vol is not None and call_vol > 0:
            return (put_vol, call_vol)
    except Exception:
        pass
    return None


async def fetch_market_dashboard(force: bool = False) -> dict[str, Any]:
    global _cached_result, _cached_at
    now_ts = datetime.now(timezone.utc).timestamp()
    if not force and _cached_result and (now_ts - _cached_at) < CACHE_TTL_SECONDS:
        return _cached_result

    sina_codes = [code for code, _ in _SINA_GLOBAL.values()]
    # 利率(10Y/2Y/EFFR)走财政部+纽约联储可靠主源；FRED 仅保留无替代源的 CPI/失业率/信用OAS，
    # 不再为利率重复打 FRED——既省冷取时延，也避免把 FRED 限额耗在已有更优源的指标上、
    # 拖累真正只能靠 FRED 的指标。
    (
        sina_raw,
        treasury,
        treasury_real,
        effr,
        cpi_val,
        prev_cpi,
        unemployment_val,
        credit_oas,
        put_call,
    ) = await asyncio.gather(
        _fetch_sina_quotes(sina_codes),
        _fetch_treasury_yields(),
        _fetch_treasury_real_yields(),
        _fetch_nyfed_effr(),
        _fetch_fred_series("CPIAUCSL"),
        _fetch_fred_csv_latest("CPIAUCSL", months_ago=12),
        _fetch_fred_series("UNRATE"),
        _fetch_fred_csv_latest("BAMLH0A0HYM2"),
        _fetch_cboe_put_call(),
    )

    # 美债 10Y/2Y：美国财政部直连（沙箱可达且稳定）。
    ten_year_val = safe_float(treasury.get("ten_year"))
    two_year_val = safe_float(treasury.get("two_year"))
    # 10Y 实际利率(TIPS) + 通胀预期 breakeven = 名义 - 实际（前瞻、市场隐含）。
    real_10y_val = safe_float(treasury_real.get("real_10y"))
    breakeven_10y = (
        round(ten_year_val - real_10y_val, 2)
        if ten_year_val is not None and real_10y_val is not None
        else None
    )
    # 联邦基金利率：纽约联储 EFFR。
    fed_rate = effr

    def _sg(key: str) -> dict[str, Any]:
        code, kind = _SINA_GLOBAL[key]
        raw = sina_raw.get(code)
        return _parse_sina_fields(kind, raw) if raw else {}

    vix_q = _sg("vix")
    spx_q = _sg("spx")
    dxy_q = _sg("dxy")
    gold_q = _sg("gold")
    oil_q = _sg("oil")
    btc_q = _sg("bitcoin")

    vix_val = safe_float(vix_q.get("price"))
    spx_price = safe_float(spx_q.get("price"))
    dxy_val = safe_float(dxy_q.get("price"))
    gold_val = safe_float(gold_q.get("price"))
    oil_val = safe_float(oil_q.get("price"))
    btc_val = safe_float(btc_q.get("price"))

    cpi_yoy: Optional[float] = None
    if cpi_val is not None and prev_cpi is not None and prev_cpi > 0:
        cpi_yoy = round((cpi_val / prev_cpi - 1) * 100, 2)

    yield_curve = (
        round(ten_year_val - two_year_val, 2)
        if ten_year_val is not None and two_year_val is not None
        else None
    )

    # 信用利差：用真实 ICE BofA 美国高收益债期权调整利差(FRED BAMLH0A0HYM2，单位 %)换算成 bp，
    # 替代旧版写死的 8.0%/5.2% 伪算法。
    credit_spread_bp: Optional[float] = None
    if credit_oas is not None:
        credit_spread_bp = round(credit_oas * 100)

    # 市场广度(>200日均线占比)与标普500 P/E 暂无沙箱内可达的免费真实源，保持诚实「数据暂不可用」。
    ma200_pct: Optional[float] = None
    sp500_pe_from_fred: Optional[float] = None

    categories: dict[str, list[dict[str, Any]]] = {
        "volatility": [],
        "interest_rate": [],
        "currency_commodity": [],
        "sentiment_risk": [],
        "macro": [],
        "breadth": [],
    }

    def _make(name: str, key: str, category: str, value: Optional[float], template: dict, change: Optional[float] = None, change_pct: Optional[float] = None, source: str = "Yahoo Finance / Stooq") -> dict[str, Any]:
        if value is None:
            return _build_indicator(key, name, category, 0, signal="neutral", status="数据暂不可用", source=source)
        interp = template.get("interpret", lambda v: "")(value)
        sig_fn = template.get("signal", lambda v: "neutral")
        sig = sig_fn(value)
        return _build_indicator(
            key=key, name=name, category=category,
            value=value,
            unit=template.get("unit", ""),
            change=change, change_pct=change_pct,
            signal=sig,
            status=interp,
            description=template.get("description", ""),
            historical_context=template.get("historical_context", ""),
            current_reading=interp,
            source=source,
        )

    categories["volatility"].append(_make("VIX 恐慌指数", "vix", "volatility", vix_val, _INSIGHT_TEMPLATES["vix"],
                                             change=safe_float(vix_q.get("change")), change_pct=safe_float(vix_q.get("change_pct")),
                                             source="新浪财经 (VIX 期货)"))
    categories["volatility"].append(_make("Put/Call 比率", "put_call_ratio", "volatility", put_call, _INSIGHT_TEMPLATES["put_call_ratio"],
                                             source="CBOE"))

    categories["interest_rate"].append(_make("10年期美债收益率", "ten_year", "interest_rate", ten_year_val, _INSIGHT_TEMPLATES["ten_year"],
                                                source="美国财政部"))
    categories["interest_rate"].append(_make("2s10s 收益率曲线", "yield_curve", "interest_rate", yield_curve, _INSIGHT_TEMPLATES["yield_curve"],
                                             source="美国财政部 (10Y-2Y)"))
    categories["interest_rate"].append(_make("联邦基金利率", "fed_funds_rate", "interest_rate", fed_rate, _INSIGHT_TEMPLATES["fed_funds_rate"],
                                                source="纽约联储 EFFR"))
    categories["interest_rate"].append(_make("信用利差 (高收益债 OAS)", "credit_spread", "interest_rate", credit_spread_bp, _INSIGHT_TEMPLATES["credit_spread"],
                                                source="FRED (BAMLH0A0HYM2)"))
    categories["interest_rate"].append(_make("10年期实际利率 (TIPS)", "real_rate", "interest_rate", real_10y_val, _INSIGHT_TEMPLATES["real_rate"],
                                                source="美国财政部 (实际收益率曲线)"))
    categories["interest_rate"].append(_make("通胀预期 (10Y Breakeven)", "breakeven", "interest_rate", breakeven_10y, _INSIGHT_TEMPLATES["breakeven"],
                                                source="美国财政部 (名义-实际)"))

    categories["currency_commodity"].append(_make("美元指数 DXY", "dxy", "currency_commodity", dxy_val, _INSIGHT_TEMPLATES["dxy"],
                                                     change_pct=safe_float(dxy_q.get("change_pct")), source="新浪财经"))
    categories["currency_commodity"].append(_make("黄金", "gold", "currency_commodity", gold_val, _INSIGHT_TEMPLATES["gold"],
                                                     change=safe_float(gold_q.get("change")), change_pct=safe_float(gold_q.get("change_pct")), source="新浪财经 (COMEX)"))
    categories["currency_commodity"].append(_make("WTI 原油", "oil", "currency_commodity", oil_val, _INSIGHT_TEMPLATES["oil"],
                                                     change=safe_float(oil_q.get("change")), change_pct=safe_float(oil_q.get("change_pct")), source="新浪财经 (NYMEX)"))
    categories["currency_commodity"].append(_make("比特币", "bitcoin", "currency_commodity", btc_val, _INSIGHT_TEMPLATES["bitcoin"],
                                                     change=safe_float(btc_q.get("change")), change_pct=safe_float(btc_q.get("change_pct")), source="新浪财经"))

    categories["macro"].append(_make("CPI 同比", "cpi", "macro", cpi_yoy, _INSIGHT_TEMPLATES["cpi"], source="FRED/BLS (CPIAUCSL)"))
    categories["macro"].append(_make("失业率", "unemployment", "macro", unemployment_val, _INSIGHT_TEMPLATES["unemployment"],
                                        source="FRED/BLS (UNRATE)"))
    categories["macro"].append(_make("标普500 P/E", "sp500_pe", "macro", sp500_pe_from_fred, _INSIGHT_TEMPLATES.get("sp500_pe", {}),
                                        source="Multpl"))

    categories["breadth"].append(_make("S&P 500 高于200日均线", "sp500_ma200", "breadth", ma200_pct, _INSIGHT_TEMPLATES["sp500_ma200"],
                                       source="市场广度数据源"))
    categories["breadth"].append(_make("标普500", "spx", "breadth", spx_price,
                                        _INSIGHT_TEMPLATES["spx"],
                                        change=safe_float(spx_q.get("change")), change_pct=safe_float(spx_q.get("change_pct")), source="新浪财经"))

    overall_signal = _compute_overall_signal(categories)
    total = sum(len(v) for v in categories.values())
    # 「可用」= 状态不是「数据暂不可用」（不以「数据」开头）。中性信号但有真实读数也算可用。
    available = sum(1 for v in categories.values() for ind in v if not ind.get("status", "").startswith("数据"))

    result = {
        "generated_at": utc_now_iso(),
        "overall_signal": overall_signal["label"],
        "overall_score": overall_signal["score"],
        "overall_summary": overall_signal["summary"],
        "total_indicators": total,
        "available_indicators": available,
        "categories": [
            {
                "key": cat_key,
                "name": _category_name(cat_key),
                "indicators": indicators,
            }
            for cat_key, indicators in categories.items()
        ],
    }

    _cached_result = result
    _cached_at = now_ts
    return result


# 宏观速判复用的看板指标：前瞻风险三件套(VIX/2s10s/信用利差) + 增长/通胀轴所需
# (失业率=增长锚、CPI=真实通胀、美元=全球流动性)。信号阈值与解读全部沿用看板（单一事实源），
# 不产生额外外网请求（看板自带 300s 缓存）。
_MACRO_RISK_KEYS = ("vix", "yield_curve", "credit_spread", "dxy", "cpi", "unemployment", "real_rate", "breakeven")


async def get_macro_risk_indicators() -> dict[str, dict[str, Any]]:
    """从市场看板提取宏观速判所需的 live 读数（_MACRO_RISK_KEYS），按 key 返回。

    含前瞻风险三件套(VIX/2s10s/信用利差) + 增长通胀轴(失业率/CPI/美元)。
    供宏观环境速判（tear_sheet.build_macro_review）复用，避免重复打 FRED/财政部/新浪等源。
    任一不可达则该 key 缺失，由速判侧诚实标 insufficient。
    """
    try:
        dash = await fetch_market_dashboard()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for cat in dash.get("categories", []):
        for ind in cat.get("indicators", []):
            if ind.get("key") in _MACRO_RISK_KEYS:
                out[ind["key"]] = ind
    return out


# 中国宏观四件套：沪深300(本土增长) / 北向资金(外资流向) / 人民币(汇率外部变量) / 中国10Y(国内流动性)。
# 复用 A股看板的 live 读数与对A股信号，给宏观速判补上本土轴；源为东财/新浪直连（沙箱可达），自带 120s 缓存。
_CHINA_MACRO_KEYS = ("csi300", "northbound_flow", "usd_cny", "china_bond_10y")


async def get_china_macro_indicators() -> dict[str, dict[str, Any]]:
    """从 A股看板提取中国宏观四件套的 live 读数，按 key 返回。

    供宏观环境速判的中国轴复用，避免重复打东财/新浪。任一不可达则该 key 缺失，由速判侧诚实标 insufficient。
    """
    try:
        dash = await fetch_ashare_dashboard()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for cat in dash.get("categories", []):
        for ind in cat.get("indicators", []):
            if ind.get("key") in _CHINA_MACRO_KEYS:
                out[ind["key"]] = ind
    return out


_ashare_cached_result: Optional[dict[str, Any]] = None
_ashare_cached_at: float = 0.0
_ASHARE_CACHE_SECONDS = 120

_ASHARE_YAHOO_SYMBOLS = {
    "sse": "000001.SS",
    "csi300": "000300.SS",
    "chinext": "399006.SZ",
    "usd_cny": "CNY=X",
}

EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


async def _fetch_eastmoney_ashare_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {
        "sse": None, "csi300": None, "chinext": None,
        "turnover": None, "limit_up": None, "limit_down": None,
    }
    try:
        secids = "1.000001,1.000300,0.399006"
        fields = "f2,f3,f4,f12,f14,f47,f50,f51,f104,f105,f106"
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=EASTMONEY_HEADERS, trust_env=False) as client:
            resp = await client.get(
                "https://push2.eastmoney.com/api/qt/ulist.np",
                params={"fltt": "2", "secids": secids, "fields": fields},
            )
            resp.raise_for_status()
            data = resp.json()
            items = (data.get("data") or {}).get("diff") or []
            for item in items:
                code = item.get("f12", "")
                result_item = {
                    "price": to_float(item.get("f2")),
                    "change_pct": to_float(item.get("f3")),
                    "change": to_float(item.get("f4")),
                    "name": item.get("f14", ""),
                    "turnover": to_float(item.get("f47")),
                    "volume": to_float(item.get("f50")),
                    "limit_up": to_float(item.get("f104")),
                    "limit_down": to_float(item.get("f105")),
                }
                if code == "000001":
                    result["sse"] = result_item
                elif code == "000300":
                    result["csi300"] = result_item
                elif code == "399006":
                    result["chinext"] = result_item

            sse = result.get("sse") or {}
            csi = result.get("csi300") or {}
            chinext = result.get("chinext") or {}
            sse_turnover = safe_float(sse.get("turnover"), 0)
            csi_turnover = safe_float(csi.get("turnover"), 0)
            chinext_turnover = safe_float(chinext.get("turnover"), 0)

            if sse_turnover > 0:
                result["turnover"] = round(sse_turnover / 1e8, 2)
            elif csi_turnover > 0:
                result["turnover"] = round(csi_turnover / 1e8, 2)

            sse_up = safe_float(sse.get("limit_up"), 0)
            sse_down = safe_float(sse.get("limit_down"), 0)
            csi_up = safe_float(csi.get("limit_up"), 0)
            csi_down = safe_float(csi.get("limit_down"), 0)
            result["limit_up"] = int(sse_up + csi_up)
            result["limit_down"] = int(sse_down + csi_down)
    except Exception:
        pass
    if not any(result.get(key) for key in ("sse", "csi300", "chinext")):
        tencent_snapshot = await _fetch_tencent_ashare_snapshot()
        for key, value in tencent_snapshot.items():
            if value is not None:
                result[key] = value
    return result


async def _fetch_tencent_ashare_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {"sse": None, "csi300": None, "chinext": None}
    code_map = {
        "sh000001": "sse",
        "sh000300": "csi300",
        "sz399006": "chinext",
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"Referer": "https://stockapp.finance.qq.com/"}, trust_env=False) as client:
            resp = await client.get("https://qt.gtimg.cn/q=sh000001,sh000300,sz399006")
            resp.raise_for_status()
        for raw_line in resp.text.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            code = line.split("=", 1)[0].replace("v_", "")
            key = code_map.get(code)
            if not key:
                continue
            payload = line.split("=", 1)[1].strip().strip('";')
            parts = payload.split("~")
            if len(parts) < 33:
                continue
            result[key] = {
                "price": to_float(parts[3]),
                "change": to_float(parts[31]),
                "change_pct": to_float(parts[32]),
                "name": parts[1],
                "turnover": None,
                "volume": to_float(parts[6]),
                "limit_up": None,
                "limit_down": None,
            }
    except Exception:
        pass
    return result


async def _fetch_eastmoney_northbound() -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=EASTMONEY_HEADERS, trust_env=False) as client:
            resp = await client.get(
                "https://push2.eastmoney.com/api/qt/kamt.kline/get",
                params={
                    "fields1": "f1,f3",
                    "fields2": "f51,f52,f53,f54",
                    "klt": "101",
                    "lmt": "1",
                    "secid": "1.000300",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            klines = (data.get("data") or {}).get("klines") or []
            if klines:
                parts = klines[-1].split(",")
                if len(parts) >= 4:
                    net_flow = to_float(parts[1])
                    if net_flow is not None:
                        return round(net_flow / 1e4, 2)
    except Exception:
        pass
    return None


async def _fetch_eastmoney_margin_balance() -> Optional[float]:
    try:
        now = datetime.now(timezone.utc)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=EASTMONEY_HEADERS, trust_env=False) as client:
            resp = await client.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={
                    "sortColumns": "TRADE_DATE",
                    "sortTypes": "-1",
                    "pageSize": "1",
                    "pageNumber": "1",
                    "reportName": "RPT_MARGIN_MARKETSUMMARY",
                    "columns": "TOTAL_MARGIN",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            records = (data.get("result") or {}).get("data") or []
            if records:
                total_margin = to_float(records[0].get("TOTAL_MARGIN"))
                if total_margin is not None:
                    return round(total_margin / 1e8, 2)
    except Exception:
        pass
    return None


async def _fetch_china_bond_yield() -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=EASTMONEY_HEADERS, trust_env=False) as client:
            resp = await client.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "secid": "1.1B0300",
                    "fields": "f43,f44,f45,f46,f57,f58",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            item = (data.get("data") or {})
            price = to_float(item.get("f43"))
            if price is not None:
                return price
    except Exception:
        pass
    return None


async def fetch_ashare_dashboard(force: bool = False) -> dict[str, Any]:
    global _ashare_cached_result, _ashare_cached_at
    now_ts = datetime.now(timezone.utc).timestamp()
    if not force and _ashare_cached_result and (now_ts - _ashare_cached_at) < _ASHARE_CACHE_SECONDS:
        return _ashare_cached_result

    yahoo_symbols = list(_ASHARE_YAHOO_SYMBOLS.values())
    (
        yahoo_quotes,
        em_snapshot,
        northbound,
        margin_balance,
        china_bond,
        usd_cny_from_fred,
        sina_fx_raw,
    ) = await asyncio.gather(
        _fetch_yahoo_quotes(yahoo_symbols),
        _fetch_eastmoney_ashare_snapshot(),
        _fetch_eastmoney_northbound(),
        _fetch_eastmoney_margin_balance(),
        _fetch_china_bond_yield(),
        _fetch_fred_series("DEXCHUS"),
        _fetch_sina_quotes([_SINA_USD_CNY[0]]),
    )

    sse_yahoo = yahoo_quotes.get("000001.SS", {})
    csi300_yahoo = yahoo_quotes.get("000300.SS", {})
    chinext_yahoo = yahoo_quotes.get("399006.SZ", {})
    usd_cny_yahoo = yahoo_quotes.get("CNY=X", {})
    # 美元人民币优先用新浪即期汇率（沙箱内实时可达），Yahoo/FRED 仅作回退。
    usd_cny_sina = safe_float(_parse_sina_fields(_SINA_USD_CNY[1], sina_fx_raw.get(_SINA_USD_CNY[0]) or []).get("price"))

    sse_em = em_snapshot.get("sse") or {}
    csi300_em = em_snapshot.get("csi300") or {}
    chinext_em = em_snapshot.get("chinext") or {}

    sse_price = safe_float(sse_em.get("price"))
    if sse_price is None:
        sse_price = safe_float(sse_yahoo.get("price"))
    sse_change_pct = safe_float(sse_em.get("change_pct")) or safe_float(sse_yahoo.get("change_pct"))
    csi300_price = safe_float(csi300_em.get("price"))
    if csi300_price is None:
        csi300_price = safe_float(csi300_yahoo.get("price"))
    csi300_change_pct = safe_float(csi300_em.get("change_pct")) or safe_float(csi300_yahoo.get("change_pct"))
    chinext_price = safe_float(chinext_em.get("price"))
    if chinext_price is None:
        chinext_price = safe_float(chinext_yahoo.get("price"))
    chinext_change_pct = safe_float(chinext_em.get("change_pct")) or safe_float(chinext_yahoo.get("change_pct"))
    usd_cny_val = usd_cny_sina
    if usd_cny_val is None:
        usd_cny_val = safe_float(usd_cny_yahoo.get("price"))
    if usd_cny_val is None:
        usd_cny_val = usd_cny_from_fred

    turnover = em_snapshot.get("turnover")
    limit_up = em_snapshot.get("limit_up")
    limit_down = em_snapshot.get("limit_down")

    def _make(name: str, key: str, value: Optional[float], template: dict, change_pct: Optional[float] = None, source: str = "东方财富 / 腾讯 / Yahoo") -> dict[str, Any]:
        if value is None or value == 0:
            return _build_indicator(key, name, "a_share", 0, signal="neutral", status="数据暂不可用", source=source)
        interp = template.get("interpret", lambda v: "")(value)
        sig_fn = template.get("signal", lambda v: "neutral")
        sig = sig_fn(value)
        return _build_indicator(
            key=key, name=name, category="a_share",
            value=value,
            unit=template.get("unit", ""),
            change=None, change_pct=change_pct,
            signal=sig,
            status=interp,
            description=template.get("description", ""),
            historical_context=template.get("historical_context", ""),
            current_reading=interp,
            source=source,
        )

    indicators = []
    T = _ASHARE_INSIGHT_TEMPLATES

    indicators.append(_make("上证指数", "sse_composite", sse_price, T["sse_composite"],
                               change_pct=sse_change_pct))
    indicators.append(_make("沪深300", "csi300", csi300_price, T["csi300"],
                               change_pct=csi300_change_pct))
    indicators.append(_make("创业板指", "chinext", chinext_price, T["chinext"],
                               change_pct=chinext_change_pct))
    indicators.append(_make("两市成交额", "a_turnover", turnover, T["a_turnover"],
                               source="东方财富"))
    indicators.append(_make("北向资金净流入", "northbound_flow", northbound, T["northbound_flow"],
                               source="东方财富"))
    indicators.append(_make("融资余额", "margin_balance", margin_balance, T["margin_balance"],
                               source="东方财富"))
    indicators.append(_make("涨停家数", "limit_up_count", limit_up, T["limit_up_count"],
                               source="东方财富"))
    indicators.append(_make("跌停家数", "limit_down_count", limit_down, T["limit_down_count"],
                               source="东方财富"))
    indicators.append(_make("人民币汇率", "usd_cny", usd_cny_val, T["usd_cny"],
                               source="新浪财经 / FRED"))
    indicators.append(_make("中国10年期国债", "china_bond_10y", china_bond, T["china_bond_10y"],
                               source="东方财富"))

    categories = {"a_share": indicators}
    overall = _compute_overall_signal(categories)
    available = sum(1 for ind in indicators if ind.get("status", "").startswith("数据") is False)

    result = {
        "generated_at": utc_now_iso(),
        "overall_signal": overall["label"],
        "overall_score": overall["score"],
        "overall_summary": overall["summary"],
        "total_indicators": len(indicators),
        "available_indicators": available,
        "categories": [
            {
                "key": "a_share",
                "name": "A股核心指标",
                "indicators": indicators,
            }
        ],
    }

    _ashare_cached_result = result
    _ashare_cached_at = now_ts
    return result


def _category_name(key: str) -> str:
    return {
        "volatility": "波动率与恐慌",
        "interest_rate": "利率与信用",
        "currency_commodity": "汇率与大宗商品",
        "sentiment_risk": "情绪与风险偏好",
        "macro": "宏观经济",
        "breadth": "市场广度与指数",
    }.get(key, key)


def _compute_overall_signal(categories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    signals = {
        "strong_bullish": 2,
        "bullish": 1,
        "neutral": 0,
        "bearish": -1,
        "strong_bearish": -2,
    }
    total_score = 0
    count = 0
    for indicators in categories.values():
        for ind in indicators:
            sig = ind.get("signal", "neutral")
            if sig in signals:
                total_score += signals[sig]
                count += 1

    if count == 0:
        return {"label": "数据不足", "score": 50, "summary": "暂无足够数据评估市场状态。"}

    avg = total_score / count if count > 0 else 0
    normalized = int(max(0, min(100, 50 + avg * 25)))

    # 合规：仅客观描述「指标聚合状态」，不做大盘涨跌方向判断、不含仓位/操作建议
    # （避免无证券投资咨询资质对公众"唱多唱空"或给出操作指引）。
    if avg > 0.8:
        label = "指标普遍积极"
        summary = "多数经典指标当前处于积极区间。"
    elif avg > 0.3:
        label = "指标偏积极"
        summary = "指标整体偏积极，部分存在分歧。"
    elif avg > -0.3:
        label = "指标分化"
        summary = "指标多空分化，未形成一致方向。"
    elif avg > -0.8:
        label = "指标偏谨慎"
        summary = "多数指标当前处于偏谨慎区间。"
    else:
        label = "指标普遍谨慎"
        summary = "多数经典指标当前处于谨慎区间。"

    return {"label": label, "score": normalized, "summary": summary}
