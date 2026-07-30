"""A股 / 港股 / 美股市值前20风险预警雷达。

排名与价量字段来自东方财富公开行情列表；宏观维度复用平台现有宏观仪表盘；
信息维度只使用已经进入 daocaijing 实时消息库的文章、快讯和研报。

这里刻意使用确定性规则，而不是让 LLM 直接给风险分。这样每个分数都能回溯到
明确字段和站内证据，也便于以后用历史回放校准阈值。
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx

from .market_dashboard import fetch_ashare_dashboard, fetch_market_dashboard
from .realtime_messages import list_realtime_messages
from .shared_utils import safe_float, utc_now_iso


MARKET_META = {
    "CN": {
        "label": "A股",
        "currency": "CNY",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    },
    "HK": {
        "label": "港股",
        "currency": "HKD",
        "fs": "m:116+t:3,m:116+t:4,m:116+t:1,m:128+t:3,m:128+t:4,m:128+t:1",
    },
    "US": {
        "label": "美股",
        "currency": "USD",
        "fs": "m:105,m:106,m:107",
    },
}

DIMENSION_WEIGHTS = {
    "macro": 0.22,
    "industry": 0.18,
    "stock": 0.30,
    "flow": 0.15,
    "information": 0.15,
}

RISK_TERMS = (
    "暴跌", "下调", "减持", "处罚", "立案", "调查", "诉讼", "违约", "停产",
    "裁员", "亏损", "预警", "风险", "制裁", "禁令", "召回", "爆雷", "质押",
    "退市", "终止", "失败", "延期", "下滑", "下降", "承压", "不及预期",
    "downgrade", "investigation", "lawsuit", "recall", "sanction", "warning",
)

POSITIVE_TERMS = (
    "上调", "增长", "突破", "中标", "回购", "增持", "超预期",
    "upgrade", "buyback", "beat expectations",
)

# 外部排行不可用且本进程没有成功快照时，只用这份候选池维持页面结构。
# fallback 行不会伪造市值、价格或排名更新时间，前端会明确显示“降级候选池”。
FALLBACK_UNIVERSE: dict[str, list[tuple[str, str]]] = {
    "CN": [
        ("601398", "工商银行"), ("601939", "建设银行"), ("601288", "农业银行"),
        ("600941", "中国移动"), ("601857", "中国石油"), ("601988", "中国银行"),
        ("300750", "宁德时代"), ("600519", "贵州茅台"), ("601628", "中国人寿"),
        ("600028", "中国石化"), ("601318", "中国平安"), ("601088", "中国神华"),
        ("601658", "邮储银行"), ("601166", "兴业银行"), ("600036", "招商银行"),
        ("601899", "紫金矿业"), ("601138", "工业富联"), ("002594", "比亚迪"),
        ("600900", "长江电力"), ("600030", "中信证券"),
    ],
    "HK": [
        ("00700", "腾讯控股"), ("00005", "汇丰控股"), ("00941", "中国移动"),
        ("09988", "阿里巴巴-W"), ("03690", "美团-W"), ("01810", "小米集团-W"),
        ("01299", "友邦保险"), ("00939", "建设银行"), ("01398", "工商银行"),
        ("03988", "中国银行"), ("02318", "中国平安"), ("00883", "中国海洋石油"),
        ("00388", "香港交易所"), ("09618", "京东集团-SW"), ("01088", "中国神华"),
        ("00001", "长和"), ("00016", "新鸿基地产"), ("00175", "吉利汽车"),
        ("02020", "安踏体育"), ("02269", "药明生物"),
    ],
    "US": [
        ("AAPL", "苹果"), ("NVDA", "英伟达"), ("MSFT", "微软"),
        ("GOOGL", "谷歌-A"), ("AMZN", "亚马逊"), ("META", "Meta Platforms"),
        ("AVGO", "博通"), ("TSM", "台积电"), ("BRK.B", "伯克希尔-B"),
        ("TSLA", "特斯拉"), ("WMT", "沃尔玛"), ("LLY", "礼来"),
        ("JPM", "摩根大通"), ("V", "Visa"), ("ORCL", "甲骨文"),
        ("MA", "万事达"), ("XOM", "埃克森美孚"), ("JNJ", "强生"),
        ("NFLX", "奈飞"), ("COST", "好市多"),
    ],
}

ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("Apple", "苹果公司"),
    "NVDA": ("Nvidia", "英伟达"),
    "MSFT": ("Microsoft", "微软"),
    "GOOGL": ("Google", "Alphabet", "谷歌"),
    "GOOG": ("Google", "Alphabet", "谷歌"),
    "AMZN": ("Amazon", "亚马逊"),
    "META": ("Meta", "Facebook"),
    "AVGO": ("Broadcom", "博通"),
    "TSM": ("TSMC", "台积电"),
    "TSLA": ("Tesla", "特斯拉"),
    "BRK.B": ("Berkshire", "伯克希尔"),
    "00700": ("腾讯", "Tencent"),
    "09988": ("阿里巴巴", "Alibaba"),
    "03690": ("美团", "Meituan"),
    "01810": ("小米", "Xiaomi"),
}

_CACHE_TTL_SECONDS = 300.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_last_good_rows: dict[str, list[dict[str, Any]]] = {}


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _risk_level(score: float) -> str:
    if score >= 75:
        return "red"
    if score >= 55:
        return "orange"
    if score >= 35:
        return "yellow"
    return "green"


def _company_key(market: str, symbol: str, name: str) -> str:
    sym = symbol.upper()
    if market == "US":
        if sym in {"GOOG", "GOOGL"}:
            return "alphabet"
        if sym in {"BRK.A", "BRK.B"}:
            return "berkshire"
    cleaned = re.sub(r"[-－](?:A|B|C|R|W|SW)$", "", name.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", "", cleaned).lower()
    return cleaned or sym


def _normalize_row(raw: dict[str, Any], market: str) -> Optional[dict[str, Any]]:
    symbol = str(raw.get("f12") or "").strip().upper()
    name = str(raw.get("f14") or "").strip()
    market_cap = safe_float(raw.get("f20"))
    if not symbol or not name or not market_cap or market_cap <= 0:
        return None
    if market == "HK" and (symbol.startswith("8") or name.endswith("-R")):
        return None
    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "sector": str(raw.get("f100") or "未分类").strip() or "未分类",
        "currency": MARKET_META[market]["currency"],
        "market_cap": market_cap,
        "price": safe_float(raw.get("f2")),
        "change_pct": safe_float(raw.get("f3")),
        "amplitude_pct": safe_float(raw.get("f7")),
        "turnover_pct": safe_float(raw.get("f8")),
        "pe": safe_float(raw.get("f9")),
        "volume_ratio": safe_float(raw.get("f10")),
        "pb": safe_float(raw.get("f23")),
        "change_60d_pct": safe_float(raw.get("f24")),
        "change_ytd_pct": safe_float(raw.get("f25")),
        "main_net_inflow": safe_float(raw.get("f62")),
        "main_net_inflow_pct": safe_float(raw.get("f184")),
        "ranking_provider": "东方财富公开行情",
    }


def _dedupe_market_rows(
    raw_rows: Iterable[dict[str, Any]],
    market: str,
    limit: int,
) -> list[dict[str, Any]]:
    normalized = [row for raw in raw_rows if (row := _normalize_row(raw, market))]
    normalized.sort(key=lambda row: row["market_cap"], reverse=True)
    seen_symbols: set[str] = set()
    seen_companies: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in normalized:
        key = _company_key(market, row["symbol"], row["name"])
        if row["symbol"] in seen_symbols or key in seen_companies:
            continue
        seen_symbols.add(row["symbol"])
        seen_companies.add(key)
        result.append(row)
        if len(result) >= limit:
            break
    return result


async def _fetch_sina_market_rows(market: str, limit: int) -> list[dict[str, Any]]:
    """东财排行短时限流时的独立实时回退。

    新浪公开市场中心当前可稳定提供 A股与美股按总市值排序；港股接口的市值字段已退化为 0，
    因而港股不在这里伪造“实时排行”，继续走成功快照或明确的候选池。
    """
    if market not in {"CN", "US"}:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "http://finance.sina.com.cn/",
    }
    async with httpx.AsyncClient(trust_env=False, timeout=15.0, headers=headers) as client:
        if market == "CN":
            response = await client.get(
                "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                "Market_Center.getHQNodeData",
                params={
                    "page": "1", "num": str(max(80, limit * 4)), "sort": "mktcap",
                    "asc": "0", "node": "hs_a", "symbol": "", "_s_r_a": "page",
                },
            )
            response.raise_for_status()
            payload = response.json()
            rows = []
            for raw in payload if isinstance(payload, list) else []:
                cap_wan = safe_float(raw.get("mktcap"))
                price = safe_float(raw.get("trade"))
                high = safe_float(raw.get("high"))
                low = safe_float(raw.get("low"))
                previous = safe_float(raw.get("settlement"))
                amplitude = (
                    (high - low) / previous * 100
                    if high is not None and low is not None and previous and previous > 0
                    else None
                )
                if not raw.get("code") or not cap_wan or cap_wan <= 0:
                    continue
                rows.append({
                    "symbol": str(raw["code"]),
                    "name": str(raw.get("name") or raw["code"]),
                    "market": "CN",
                    "sector": "全市场大盘股",
                    "currency": "CNY",
                    "market_cap": cap_wan * 10_000,  # 新浪 mktcap 单位为万元
                    "price": price,
                    "change_pct": safe_float(raw.get("changepercent")),
                    "amplitude_pct": amplitude,
                    "turnover_pct": safe_float(raw.get("turnoverratio")),
                    "pe": safe_float(raw.get("per")),
                    "volume_ratio": None,
                    "pb": safe_float(raw.get("pb")),
                    "change_60d_pct": None,
                    "change_ytd_pct": None,
                    "main_net_inflow": None,
                    "main_net_inflow_pct": None,
                    "ranking_provider": "新浪财经市场中心",
                })
        else:
            response = await client.get(
                "http://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
                "var%20riskRadar=/US_CategoryService.getList",
                params={
                    # 该接口 num>40 会静默回落为20行；取40才能在双重股权去重后凑足20家公司。
                    "page": "1", "num": str(min(40, max(30, limit * 2))), "sort": "mktcap",
                    "asc": "0", "market": "", "id": "",
                },
            )
            response.raise_for_status()
            text = response.text
            marker = "var riskRadar=("
            start = text.find(marker)
            end = text.rfind(")")
            if start < 0 or end <= start:
                return []
            payload = json.loads(text[start + len(marker):end])
            rows = []
            for raw in payload.get("data") or []:
                cap = safe_float(raw.get("mktcap"))
                symbol = str(raw.get("symbol") or "").upper()
                if not symbol or not cap or cap <= 0:
                    continue
                amplitude_text = str(raw.get("amplitude") or "").replace("%", "")
                rows.append({
                    "symbol": symbol,
                    "name": str(raw.get("cname") or raw.get("name") or symbol),
                    "market": "US",
                    "sector": str(raw.get("category") or "未分类"),
                    "currency": "USD",
                    "market_cap": cap,
                    "price": safe_float(raw.get("price")),
                    "change_pct": safe_float(raw.get("chg")),
                    "amplitude_pct": safe_float(amplitude_text),
                    "turnover_pct": None,
                    "pe": safe_float(raw.get("pe")),
                    "volume_ratio": None,
                    "pb": None,
                    "change_60d_pct": None,
                    "change_ytd_pct": None,
                    "main_net_inflow": None,
                    "main_net_inflow_pct": None,
                    "ranking_provider": "新浪财经市场中心",
                })
    rows.sort(key=lambda row: row["market_cap"], reverse=True)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = _company_key(market, row["symbol"], row["name"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped


async def _fetch_market_rows(market: str, limit: int) -> tuple[list[dict[str, Any]], str, list[str]]:
    params = {
        "pn": "1",
        "pz": str(max(80, limit * 4)),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f20",
        "fs": MARKET_META[market]["fs"],
        "fields": "f2,f3,f7,f8,f9,f10,f12,f13,f14,f20,f21,f23,f24,f25,f62,f100,f184",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    warnings: list[str] = []
    error: Optional[Exception] = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12.0, headers=headers) as client:
                response = await client.get("https://push2.eastmoney.com/api/qt/clist/get", params=params)
                response.raise_for_status()
                raw_rows = ((response.json().get("data") or {}).get("diff") or [])
                rows = _dedupe_market_rows(raw_rows, market, limit)
                if len(rows) < limit:
                    raise ValueError(f"仅返回 {len(rows)} 家有效公司")
                _last_good_rows[market] = copy.deepcopy(rows)
                return rows, "live", warnings
        except Exception as exc:  # noqa: BLE001 - 排行端点必须可降级
            error = exc
            if attempt == 0:
                await asyncio.sleep(0.15)

    try:
        sina_rows = await _fetch_sina_market_rows(market, limit)
    except Exception:  # noqa: BLE001 - 独立回退失败继续走缓存/候选池
        sina_rows = []
    if len(sina_rows) >= limit:
        warnings.append(f"{MARKET_META[market]['label']}东方财富排行暂不可用，已切换新浪财经实时排行")
        _last_good_rows[market] = copy.deepcopy(sina_rows)
        return sina_rows, "live", warnings

    warnings.append(f"{MARKET_META[market]['label']}市值排行实时源暂不可用：{type(error).__name__ if error else 'unknown'}")
    if market in _last_good_rows:
        return copy.deepcopy(_last_good_rows[market][:limit]), "stale", warnings

    fallback = [
        {
            "symbol": symbol,
            "name": name,
            "market": market,
            "sector": "待行情源恢复",
            "currency": MARKET_META[market]["currency"],
            "market_cap": None,
            "price": None,
            "change_pct": None,
            "amplitude_pct": None,
            "turnover_pct": None,
            "pe": None,
            "volume_ratio": None,
            "pb": None,
            "change_60d_pct": None,
            "change_ytd_pct": None,
            "main_net_inflow": None,
            "main_net_inflow_pct": None,
            "ranking_provider": "降级候选池",
        }
        for symbol, name in FALLBACK_UNIVERSE[market][:limit]
    ]
    return fallback, "fallback", warnings


def _message_text(message: Any) -> str:
    return f"{getattr(message, 'title', '')} {getattr(message, 'content', '')}".strip()


def _matches_company(message: Any, company: dict[str, Any]) -> bool:
    symbol = company["symbol"]
    name = company["name"]
    message_symbol = str(getattr(message, "symbol", "") or "").upper()
    if message_symbol and message_symbol == symbol:
        return True
    text = _message_text(message)
    aliases = [name, *ALIASES.get(symbol, ())]
    if any(alias and alias.lower() in text.lower() for alias in aliases):
        return True
    if company["market"] == "US" and len(symbol) >= 2:
        return bool(re.search(rf"(?<![A-Z]){re.escape(symbol)}(?![A-Z])", text.upper()))
    return False


def _site_signals(messages: list[Any], company: dict[str, Any]) -> list[Any]:
    return [message for message in messages if _matches_company(message, company)][:5]


def _information_risk(messages: list[Any]) -> tuple[float, list[dict[str, Any]]]:
    if not messages:
        return 18.0, []
    score = 20.0
    evidence: list[dict[str, Any]] = []
    severity_points = {"info": 0, "success": -4, "warning": 12, "critical": 24}
    for message in messages:
        text = _message_text(message)
        lower = text.lower()
        risk_hits = sum(1 for term in RISK_TERMS if term.lower() in lower)
        positive_hits = sum(1 for term in POSITIVE_TERMS if term.lower() in lower)
        severity = str(getattr(message, "severity", "info") or "info")
        contribution = severity_points.get(severity, 0) + min(risk_hits, 3) * 8 - min(positive_hits, 2) * 4
        score += contribution
        if risk_hits or severity in {"warning", "critical"}:
            evidence.append({
                "dimension": "information",
                "title": str(getattr(message, "title", "") or "站内信息信号")[:160],
                "detail": str(getattr(message, "content", "") or "")[:220],
                "severity": "critical" if severity == "critical" else "warning",
                "source": str(getattr(message, "source_name", "") or "daocaijing站内信息"),
                "url": getattr(message, "url", None),
                "published_at": getattr(message, "created_at", None),
            })
    return _clip(score), evidence[:3]


def _macro_context(market: str, global_dashboard: dict[str, Any], cn_dashboard: dict[str, Any]) -> tuple[float, str]:
    dashboard = cn_dashboard if market == "CN" else global_dashboard
    score = safe_float(dashboard.get("overall_score"))
    if score is None:
        return 42.0, "宏观指标数据不足"
    # 仪表盘分数越积极，风险分越低；保留 12~88 的边界，避免单一维度一票否决。
    return _clip(100.0 - score, 12.0, 88.0), str(dashboard.get("overall_signal") or "指标分化")


def _score_company(
    company: dict[str, Any],
    *,
    macro_risk: float,
    macro_label: str,
    industry_change: Optional[float],
    messages: list[Any],
    ranking_status: str,
) -> dict[str, Any]:
    change = safe_float(company.get("change_pct"))
    change_60d = safe_float(company.get("change_60d_pct"))
    amplitude = safe_float(company.get("amplitude_pct"))
    turnover = safe_float(company.get("turnover_pct"))
    volume_ratio = safe_float(company.get("volume_ratio"))
    pe = safe_float(company.get("pe"))
    flow_pct = safe_float(company.get("main_net_inflow_pct"))

    industry_risk = 30.0
    if industry_change is not None:
        industry_risk += max(0.0, -industry_change) * 11.0
        industry_risk += max(0.0, industry_change - 4.0) * 2.0

    stock_risk = 28.0
    if change is not None:
        stock_risk += max(0.0, -change) * 9.0
        stock_risk += max(0.0, change - 8.0) * 2.0
    if change_60d is not None:
        stock_risk += max(0.0, -change_60d - 5.0) * 1.5
        stock_risk += max(0.0, change_60d - 35.0) * 0.5
    if amplitude is not None:
        stock_risk += max(0.0, amplitude - 4.0) * 2.2
    if pe is not None and (pe > 60 or pe < 0):
        stock_risk += 8.0

    flow_risk = 26.0
    if flow_pct is not None:
        flow_risk += max(0.0, -flow_pct) * 5.0
        flow_risk -= max(0.0, flow_pct) * 1.5
    if volume_ratio is not None:
        flow_risk += max(0.0, volume_ratio - 1.5) * 8.0
    if turnover is not None:
        flow_risk += max(0.0, turnover - 5.0) * 2.0
    if change is not None and change < -2 and volume_ratio is not None and volume_ratio > 1.2:
        flow_risk += 12.0

    information_risk, evidence = _information_risk(messages)
    dimensions = {
        "macro": round(_clip(macro_risk), 1),
        "industry": round(_clip(industry_risk), 1),
        "stock": round(_clip(stock_risk), 1),
        "flow": round(_clip(flow_risk), 1),
        "information": round(_clip(information_risk), 1),
    }
    risk_score = sum(dimensions[key] * weight for key, weight in DIMENSION_WEIGHTS.items())
    level = _risk_level(risk_score)

    drivers: list[str] = []
    if macro_risk >= 55:
        drivers.append(f"宏观环境：{macro_label}")
    if industry_change is not None and industry_change <= -1:
        drivers.append(f"{company['sector']}同组平均下跌 {abs(industry_change):.1f}%")
    if change is not None and change <= -2:
        drivers.append(f"当日跌幅 {abs(change):.1f}%")
    if change_60d is not None and change_60d <= -10:
        drivers.append(f"近60日回撤 {abs(change_60d):.1f}%")
    if flow_pct is not None and flow_pct <= -3:
        drivers.append(f"主力净流出占比 {abs(flow_pct):.1f}%")
    if evidence:
        drivers.append(f"站内命中 {len(evidence)} 条负面/警示证据")
    if not drivers:
        drivers.append("暂无触发高等级阈值的单项信号")

    confidence = "high"
    if ranking_status != "live" or company.get("price") is None or company.get("market_cap") is None:
        confidence = "low"
    elif change is None or industry_change is None:
        confidence = "medium"

    return {
        **company,
        "risk_score": round(risk_score, 1),
        "risk_level": level,
        "confidence": confidence,
        "dimensions": dimensions,
        "drivers": drivers[:4],
        "evidence": evidence,
        "site_signal_count": len(messages),
        "data_status": ranking_status,
    }


def _market_summary(market: str, companies: list[dict[str, Any]], source_status: str) -> dict[str, Any]:
    counts = {level: sum(1 for company in companies if company["risk_level"] == level)
              for level in ("green", "yellow", "orange", "red")}
    average = sum(company["risk_score"] for company in companies) / len(companies) if companies else 0.0
    return {
        "market": market,
        "label": MARKET_META[market]["label"],
        "company_count": len(companies),
        "average_risk": round(average, 1),
        "counts": counts,
        "source_status": source_status,
    }


async def build_market_risk_radar(
    markets: Iterable[str] = ("CN", "HK", "US"),
    *,
    limit: int = 20,
    force: bool = False,
) -> dict[str, Any]:
    selected = [market.upper() for market in markets if market.upper() in MARKET_META]
    selected = list(dict.fromkeys(selected)) or ["CN", "HK", "US"]
    limit = max(1, min(int(limit), 20))
    cache_key = f"{','.join(selected)}:{limit}"
    cached = _cache.get(cache_key)
    if not force and cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return copy.deepcopy(cached[1])

    async def _safe_dashboard(fetcher: Any) -> dict[str, Any]:
        try:
            return await fetcher()
        except Exception:  # noqa: BLE001 - 宏观源异常时使用“数据不足”中性风险，不阻断60家公司
            return {}

    market_results, global_dashboard, cn_dashboard = await asyncio.gather(
        asyncio.gather(*[_fetch_market_rows(market, limit) for market in selected]),
        _safe_dashboard(fetch_market_dashboard),
        _safe_dashboard(fetch_ashare_dashboard),
    )
    macro_status = "live" if global_dashboard and cn_dashboard else "partial"

    try:
        site_messages = list_realtime_messages(limit=200)
        site_status = "live"
    except Exception:  # noqa: BLE001 - 站内消息库异常不阻断排行
        site_messages = []
        site_status = "unavailable"

    warnings: list[str] = []
    market_payloads: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    all_companies: list[dict[str, Any]] = []

    for market, (rows, source_status, market_warnings) in zip(selected, market_results):
        warnings.extend(market_warnings)
        sector_changes: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            change = safe_float(row.get("change_pct"))
            if change is not None:
                sector_changes[row["sector"]].append(change)
        sector_average = {
            sector: sum(changes) / len(changes)
            for sector, changes in sector_changes.items()
            if changes
        }
        macro_risk, macro_label = _macro_context(market, global_dashboard, cn_dashboard)
        companies: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            matched_messages = _site_signals(site_messages, row)
            scored = _score_company(
                row,
                macro_risk=macro_risk,
                macro_label=macro_label,
                industry_change=sector_average.get(row["sector"]),
                messages=matched_messages,
                ranking_status=source_status,
            )
            scored["rank"] = rank
            companies.append(scored)
        companies.sort(key=lambda company: company["rank"])
        summary = _market_summary(market, companies, source_status)
        summaries.append(summary)
        all_companies.extend(companies)
        market_payloads.append({
            "market": market,
            "label": MARKET_META[market]["label"],
            "currency": MARKET_META[market]["currency"],
            "ranking_source": str(rows[0].get("ranking_provider") if rows else "无可用排行源"),
            "source_status": source_status,
            "macro_label": macro_label,
            "macro_risk": round(macro_risk, 1),
            "companies": companies,
        })

    total_counts = {
        level: sum(1 for company in all_companies if company["risk_level"] == level)
        for level in ("green", "yellow", "orange", "red")
    }
    overall_average = (
        sum(company["risk_score"] for company in all_companies) / len(all_companies)
        if all_companies else 0.0
    )
    degraded_markets = [summary["label"] for summary in summaries if summary["source_status"] != "live"]
    quality_level = "degraded" if degraded_markets or site_status != "live" or macro_status != "live" else "live"
    quality_reasons = []
    if degraded_markets:
        quality_reasons.append(f"{'、'.join(degraded_markets)}排行使用缓存或候选池")
    if site_status != "live":
        quality_reasons.append("站内信息库暂不可用")
    if macro_status != "live":
        quality_reasons.append("部分宏观指标暂不可用，相关维度按中性风险处理")

    result = {
        "generated_at": utc_now_iso(),
        "coverage": {
            "markets": selected,
            "companies": len(all_companies),
            "per_market_limit": limit,
            "basis": "各市场按总市值降序，双重股权/人民币柜台按公司去重",
        },
        "summary": {
            "average_risk": round(overall_average, 1),
            "risk_level": _risk_level(overall_average),
            "counts": total_counts,
            "site_signal_companies": sum(1 for company in all_companies if company["site_signal_count"] > 0),
            "market_summaries": summaries,
        },
        "markets": market_payloads,
        "methodology": {
            "weights": {key: round(value * 100) for key, value in DIMENSION_WEIGHTS.items()},
            "thresholds": {"green": "<35", "yellow": "35-54", "orange": "55-74", "red": "≥75"},
            "explanation": "宏观、行业、个股、资金、站内信息五维确定性评分；预警用于提示复核，不预测必然涨跌。",
        },
        "sources": [
            {"name": "东方财富 / 新浪财经公开行情", "role": "动态市值排名、价格、成交与资金字段", "status": "live" if not degraded_markets else "partial"},
            {"name": "daocaijing宏观仪表盘", "role": "全球与A股宏观风险环境", "status": macro_status},
            {"name": "daocaijing站内信息", "role": "快讯、文章、研报中的公司级风险词与严重度", "status": site_status},
        ],
        "warnings": warnings,
        "data_quality": {
            "level": quality_level,
            "label": "实时综合数据" if quality_level == "live" else "部分数据已降级",
            "detail": "排名与风险信号按5分钟缓存更新；站内信息只计入已入库且可追溯的内容。",
            "reasons": quality_reasons,
        },
        "disclaimer": "本模块用于风险线索筛查和投研复核，不构成证券投资建议，也不替代人工核验。",
    }
    _cache[cache_key] = (time.time(), copy.deepcopy(result))
    return result
