"""Evidence-first stock workspace: public facts plus the caller's visible site news.

Prices are reproducible scenario calculations, never LLM output. Public forecasts
are filtered by year and deduplicated by broker. No entitled data or site content
is kept in a shared response cache. All analytical construction is pure.
"""
from __future__ import annotations

import asyncio
import inspect
import math
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

_PUBLIC_CACHE: dict[str, tuple[float, Any]] = {}
_BJ = timezone(timedelta(hours=8))
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", str(value or ""))).strip()[:limit]


def _synthetic(data: dict) -> bool:
    provider = str(data.get("provider") or "").lower()
    quality = data.get("data_quality") or {}
    level = str(quality.get("level") or "") if isinstance(quality, dict) else str(quality)
    return any(word in provider for word in ("mock", "demo", "synthetic", "sample")) or level.lower() in {"mock", "synthetic", "sample"}


def _dated_day(value: Any, now: datetime) -> Optional[str]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        dated = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    return value if dated <= now.date() else None


def _history_rows(history: Optional[list], now: datetime) -> tuple[list[tuple[str, float]], int]:
    """Keep dated source observations only; never interpolate or invent points."""
    by_date: dict[str, float] = {}
    dropped = 0
    for row in history or []:
        if isinstance(row, dict):
            date, close = row.get("d"), _number(row.get("c"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            date, close = row[0], _number(row[1])
        else:
            dropped += 1
            continue
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or close is None or close <= 0:
            dropped += 1
            continue
        try:
            dated = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            dropped += 1
            continue
        if dated > now.date():
            dropped += 1
            continue
        # A repeated date retains the last valid observation returned by the
        # source. Invalid duplicates cannot overwrite a usable price.
        by_date[date] = close
    return sorted(by_date.items())[-280:], dropped


def normalize_instrument(symbol: str, market: str = "") -> tuple[str, str]:
    sym = (symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,23}", sym):
        raise ValueError("symbol 必须是有效股票代码")
    mk = (market or "").strip().upper()
    inferred = "HK" if sym.endswith(".HK") else "CN" if sym.endswith((".SH", ".SS", ".SZ", ".BJ")) else "CN" if sym.isdigit() and len(sym) == 6 else "HK" if sym.isdigit() else "US"
    if mk and mk not in {"CN", "HK", "US"}:
        raise ValueError("market 仅支持 CN、HK、US")
    mk = mk or inferred
    if mk in {"CN", "HK"}:
        sym = re.sub(r"\.(HK|SH|SS|SZ|BJ)$", "", sym)
        if not sym.isdigit() or (mk == "CN" and len(sym) != 6) or (mk == "HK" and len(sym) > 5):
            raise ValueError("股票代码与市场不匹配")
        sym = sym.zfill(5) if mk == "HK" else sym
    elif sym.isdigit() or sym.endswith((".HK", ".SH", ".SS", ".SZ", ".BJ")):
        raise ValueError("股票代码与市场不匹配")
    return sym, mk


async def _public_json(key: str, url: str, *, refresh: bool = False) -> Any:
    hit = _PUBLIC_CACHE.get(key)
    if not refresh and hit and time.monotonic() - hit[0] < 1800:
        return hit[1]
    async with httpx.AsyncClient(trust_env=False, timeout=8.0) as client:
        response = await client.get(url, headers=_HEADERS)
        response.raise_for_status()
        payload = response.json()
    if isinstance(payload, dict):
        payload["_intelligence_fetched_at"] = datetime.now(_BJ).isoformat(timespec="seconds")
    if len(_PUBLIC_CACHE) >= 256:
        _PUBLIC_CACHE.pop(next(iter(_PUBLIC_CACHE)))
    _PUBLIC_CACHE[key] = (time.monotonic(), payload)
    return payload


async def fetch_company_profile(symbol: str, market: str, *, refresh: bool = False) -> Optional[dict]:
    if market != "CN":
        return None
    exchange = "SH" if symbol.startswith("6") else "BJ" if symbol.startswith(("4", "8", "92")) else "SZ"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={exchange}{symbol}"
    data = await _public_json(f"profile:{symbol}", url, refresh=refresh)
    rows = data.get("jbzl") or []
    if not rows or str(rows[0].get("SECURITY_CODE") or "") != symbol:
        return None
    row = rows[0]
    return {"name": row.get("SECURITY_NAME_ABBR"), "industry": row.get("EM2016"),
            "description": _text(row.get("ORG_PROFILE"), 500), "business_scope": _text(row.get("BUSINESS_SCOPE"), 350),
            "url": url, "as_of": None}


_RATING_LABELS = {"buy": "买入", "outperform": "增持", "neutral": "中性/持有", "underperform": "减持", "sell": "卖出", "other": "其他/未归类"}


def _rating_key(value: Any) -> str:
    text = _text(value, 100).casefold().strip()
    exact = {"buy": "buy", "strong buy": "buy", "outperform": "outperform", "overweight": "outperform",
             "hold": "neutral", "neutral": "neutral", "underperform": "underperform", "underweight": "underperform", "sell": "sell"}
    if text in exact:
        return exact[text]
    for words, key in [(('买入', '强烈推荐'), 'buy'), (('增持', '推荐'), 'outperform'),
                       (('减持',), 'underperform'), (('卖出', '回避'), 'sell'), (('持有', '中性'), 'neutral')]:
        if any(word in text for word in words):
            return key
    return "other"


def parse_broker_ratings(payload: dict, symbol: str, market: str, *, now: datetime) -> dict:
    """Dated latest-per-broker sample, independent of whether its EPS is present."""
    rows = payload.get("data") if market == "CN" else (payload.get("result") or {}).get("data")
    rows = rows if isinstance(rows, list) else []
    cutoff = (now - timedelta(days=180)).date().isoformat()
    parsed = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        code = row.get("stockCode") if market == "CN" else row.get("SECURITY_CODE")
        if str(code or "") != symbol:
            continue
        date = _dated_day(str(row.get("publishDate") if market == "CN" else row.get("PUBLISH_DATE"))[:10], now)
        broker_id = str(row.get("orgCode") if market == "CN" else row.get("ORG_CODE") or "").strip()
        broker = _text(row.get("orgSName") or row.get("orgName") if market == "CN" else row.get("ORG_NAME"), 100)
        if not date or date < cutoff or not broker_id or broker_id == "None":
            continue
        raw = _text(row.get("emRatingName") or row.get("sRatingName") if market == "CN" else row.get("RATING_NAME_NEW") or row.get("RATING_NAME"), 100)
        info = str(row.get("infoCode") or "")
        # HK's public table supplies rating/date/broker but no report URL; do
        # not manufacture an A-share report page and call it the original.
        url = f"https://data.eastmoney.com/report/zw_stock.jshtml?infocode={info}" if market == "CN" and re.fullmatch(r"AP\d+", info) else None
        parsed.append({"broker_id": broker_id, "broker": broker or broker_id, "published_at": date,
                       "rating": raw or "评级未提供", "rating_key": _rating_key(raw), "url": url})
    parsed.sort(key=lambda item: item["published_at"], reverse=True)
    by_broker = {}
    for item in parsed:
        by_broker.setdefault(item["broker_id"], item)
    samples = list(by_broker.values())
    dates = [item["published_at"] for item in samples]
    counts = {key: sum(item["rating_key"] == key for item in samples) for key in _RATING_LABELS}
    return {"samples": samples, "distribution": [{"key": key, "label": label, "count": counts[key]} for key, label in _RATING_LABELS.items()],
            "sample_count": len(samples), "broker_count": len(samples), "report_count": len(parsed),
            "period": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
            "as_of": max(dates) if dates else None, "fetched_at": payload.get("_intelligence_fetched_at"),
            "note": "仅采样公开接口最多 100 条、最近 180 天的记录，每家券商保留最新报告；不是全市场覆盖。评级与 EPS 筛选独立；未提供或未识别评级单列其他，不当作中性。券商评级为第三方观点，不是本平台投资建议。"}


async def fetch_dated_broker_ratings(symbol: str, market: str, *, refresh: bool = False) -> Optional[dict]:
    if market != "HK":
        return None
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_HKF10_INFO_ORGRATING&columns=ALL"
           "&pageSize=100&sortColumns=PUBLISH_DATE&sortTypes=-1"
           f"&filter=(SECUCODE=%22{symbol}.HK%22)")
    return parse_broker_ratings(await _public_json(f"broker-ratings:HK:{symbol}", url, refresh=refresh), symbol, market, now=datetime.now(_BJ))


def parse_annual_forecasts(payload: dict, symbol: str, *, now: datetime) -> Optional[dict]:
    """Report PE is NOT a target multiple; only the dated annual EPS is retained."""
    year = now.year
    if _number(payload.get("currentYear")) != year:
        return None
    cutoff = (now - timedelta(days=180)).date().isoformat()
    seen: set[str] = set()
    reports = []
    rows = sorted((r for r in (payload.get("data") or []) if isinstance(r, dict)),
                  key=lambda r: str(r.get("publishDate") or ""), reverse=True)
    for row in rows:
        published = _dated_day(str(row.get("publishDate") or "")[:10], now)
        eps = _number(row.get("predictThisYearEps"))
        broker = str(row.get("orgCode") or row.get("orgName") or "").strip()
        if (str(row.get("stockCode") or "") != symbol or not broker or broker in seen
                or not published or not published.startswith(str(year)) or not cutoff <= published <= now.date().isoformat()):
            continue
        # The latest report from a broker supersedes its older forecast even when
        # that latest report has withdrawn the EPS estimate.
        seen.add(broker)
        if eps is None:
            continue
        info = str(row.get("infoCode") or "")
        reports.append({"eps": eps, "published_at": published, "broker": row.get("orgSName") or row.get("orgName"),
                        "title": _text(row.get("title"), 160),
                        "url": f"https://data.eastmoney.com/report/zw_stock.jshtml?infocode={info}" if re.fullmatch(r"AP\d+", info) else None})
    broker_ratings = parse_broker_ratings(payload, symbol, "CN", now=now)
    if not reports and not broker_ratings["samples"]:
        return None
    values = [r["eps"] for r in reports]
    return {"year": year, "currency": "CNY", "eps_median": statistics.median(values) if values else None,
            "eps_low": min(values) if values else None, "eps_high": max(values) if values else None, "broker_count": len(values),
            "as_of": max(r["published_at"] for r in reports) if reports else None, "oldest_report": min(r["published_at"] for r in reports) if reports else None,
            "reports": reports, "broker_ratings": broker_ratings}


async def fetch_annual_forecasts(symbol: str, market: str, *, refresh: bool = False) -> Optional[dict]:
    if market != "CN":
        return None
    now = datetime.now(_BJ)
    begin = max(f"{now.year}-01-01", (now - timedelta(days=180)).date().isoformat())
    url = ("https://reportapi.eastmoney.com/report/list?pageSize=100&pageNo=1&qType=0"
           f"&code={symbol}&beginTime={begin}&endTime={now.date().isoformat()}")
    return parse_annual_forecasts(await _public_json(f"forecast:{symbol}:{now.year}", url, refresh=refresh), symbol, now=now)


def parse_fund_flow_history(payload: dict, symbol: str, *, now: datetime) -> list[dict]:
    """Eastmoney daykline: f51 date, f52 main, f53 small, f54 medium,
    f55 large, f56 super-large, f62 unadjusted close. Amounts are CNY yuan.
    Mapping is also visible in Eastmoney's public stock.js history table.
    """
    data = payload.get("data") or {}
    if str(data.get("code") or "") != symbol or _synthetic(payload):
        return []
    by_date = {}
    for line in data.get("klines") or []:
        fields = str(line).split(",")
        date = _dated_day(fields[0], now) if fields else None
        if not date:
            continue
        def number_at(index):
            return _number(fields[index]) if len(fields) > index else None
        close = number_at(11)
        row = {"date": date, "main_net": number_at(1), "small_net": number_at(2), "medium_net": number_at(3),
               "large_net": number_at(4), "super_large_net": number_at(5), "close": close if close is not None and close > 0 else None}
        if any(row[key] is not None for key in ("main_net", "small_net", "medium_net", "large_net", "super_large_net", "close")):
            by_date[date] = row
    return [by_date[date] for date in sorted(by_date)][-280:]


async def fetch_verified_fund_flow(symbol: str, market: str, *, refresh: bool = False) -> Optional[dict]:
    """Public daily history plus the original LAST FIVE observations metric."""
    if market != "CN":
        return None
    from .eastmoney_data import fetch_fund_flow
    from .market_data import _cn_market_prefix

    try:
        baseline = await fetch_fund_flow(symbol, market) or {}
    except Exception:
        baseline = {}
    prefix = _cn_market_prefix(symbol)
    if not prefix:
        return baseline or None
    secid = prefix[0] + symbol
    url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}"
           "&fields1=f1&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&klt=101&lmt=280")
    try:
        payload = await _public_json(f"flow-history:{symbol}:v2", url, refresh=refresh)
        series = parse_fund_flow_history(payload, symbol, now=datetime.now(_BJ))
        if series:
            recent = series[-5:]
            main_complete = all(row["main_net"] is not None for row in recent)
            return {**baseline, "main_flow_5d": sum(row["main_net"] for row in recent) if main_complete else None,
                    "flow_days": len(recent), "flow_start_date": recent[0]["date"], "flow_end_date": recent[-1]["date"],
                    "as_of": payload.get("_intelligence_fetched_at"), "provider": "eastmoney", "unit": "元", "currency": "CNY",
                    "price_currency": "CNY", "price_adjustment": "未复权（资金源收盘）", "series": series}
    except Exception:
        pass
    return baseline or None


def load_visible_platform_messages(symbol: str, message_view: Callable[[Any], Any]) -> list[dict]:
    """Apply the existing account/source/member view BEFORE any summarization."""
    from .realtime_messages import get_realtime_message
    from .symbol_linker import messages_by_symbol

    out = []
    for row in messages_by_symbol(symbol, limit=24):
        record = get_realtime_message(str(row["id"]))
        view = message_view(record) if record is not None else None
        if view is not None:
            out.append(view.model_dump(mode="json"))
    return out


def _freshness(as_of: Any, now: datetime, stale_days: int) -> tuple[str, str]:
    if not as_of:
        return "available", "源未提供数据时点；取数时间不等于数据日期"
    try:
        dated = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        age = (now.date() - dated.date()).days
    except ValueError:
        return "available", "源日期无法校验"
    if age < -1:
        return "stale", "源日期在未来，无法校验"
    if age > stale_days:
        return "stale", f"数据截至 {str(as_of)[:10]}，已滞后 {age} 天"
    return "available", f"数据截至 {str(as_of)[:19]}"


def _attach_platform_research(result: dict, channels: list, *, now: datetime) -> dict:
    """Narrative evidence can change explanations, never scenario arithmetic."""
    from .stock_platform_research import build_platform_research

    research = build_platform_research(channels, symbol=result["symbol"], name=result["name"],
                                       market=result["market"], now=now)
    result["platform_research"] = research
    old_platform_points = {f"{item['published_at'] or '日期未知'} · {item['title']}"
                           for item in result["evidence"] if item["origin"] == "platform"}
    old_platform_ids = {item["id"] for item in result["evidence"] if item["origin"] == "platform"}
    result["evidence"] = [item for item in result["evidence"] if item["origin"] != "platform"]
    result["sources"] = [item for item in result["sources"] if item["id"] not in old_platform_ids]
    result["sources"].extend(research["sources"])
    for document in research["documents"]:
        result["evidence"].append({"id": document["source_id"], "origin": "platform", "kind": document["channel"],
                                   "title": document["title"], "summary": document["excerpt"],
                                   "published_at": document["published_at"], "url": document["url"],
                                   "source": document["source_name"], "document_id": document["id"],
                                   "original_id": document["original_id"], "document_revision": document["revision"],
                                   "body_state": document["body_state"], "role": document["role"]})
    for dimension in result["dimensions"]:
        relevant = [claim for claim in research["claims"] if claim["dimension"] == dimension["key"]]
        dimension["platform_claim_ids"] = [claim["id"] for claim in relevant]
        dimension["source_ids"] = list(dict.fromkeys(
            [sid for sid in dimension["source_ids"] if sid not in old_platform_ids]
            + [sid for claim in relevant for sid in claim["source_ids"]]))
        dimension["evidence"].extend(
            f"平台{'历史资料' if claim['freshness'] == 'historical' else '日期未核验资料' if claim['freshness'] == 'unknown' else '线索'} · {claim['claim']}"
            for claim in relevant[:3])
        if dimension["key"] == "news":
            public = sum(item["origin"] == "public" for item in result["evidence"])
            documents = len(research["documents"])
            dimension["headline"] = f"平台 {documents} 份当前账号可见资料 · 公共资料 {public} 条 · {len(research['claims'])} 条段落主张待复核"
            dimension["metrics"] = [{"label": "可读平台资料", "value": documents, "unit": "份"},
                                     {"label": "段落主张", "value": len(research["claims"]), "unit": "条"},
                                     {"label": "公共证据", "value": public, "unit": "条"}]
            dimension["evidence"] = [point for point in dimension["evidence"]
                                     if point not in old_platform_points]
            if relevant and any(claim["freshness"] == "current" for claim in relevant):
                dimension["signal"] = "neutral"
    claims = {claim["id"]: claim for claim in research["claims"]}
    links = {link["key"]: link for link in research["scenario_links"]}
    for scenario in result["valuation"]["scenarios"]:
        link = links.get(scenario["key"], {})
        support = [claims[cid] for cid in link.get("support_claim_ids", []) if cid in claims]
        counter = [claims[cid] for cid in link.get("counter_claim_ids", []) if cid in claims]
        scenario["platform_claim_ids"] = list(dict.fromkeys(
            link.get("support_claim_ids", []) + link.get("counter_claim_ids", [])))
        scenario["platform_context_claim_ids"] = link.get("context_claim_ids", [])
        scenario["reasons"].extend(f"平台{'压力' if scenario['key'] == 'pessimistic' else '参考'}线索（待复核）：{claim['claim']}" for claim in support[:3])
        scenario["invalidation"].extend(f"平台反向线索需验证：{claim['claim']}；{claim['watch']}" for claim in counter[:2])
        scenario["source_ids"] = list(dict.fromkeys(scenario["source_ids"] + link.get("source_ids", [])))
    # Remove the old title-only platform event scan. New watch lines come from
    # cited paragraphs and remain conditions, without inventing event dates.
    result["catalysts"] = [item for item in result["catalysts"] if not set(item["source_ids"]) & old_platform_ids]
    eligible_ids = {cid for link in research["scenario_links"] for cid in link["support_claim_ids"] + link["counter_claim_ids"]}
    watches, seen_watch = [], set()
    for claim in research["claims"]:
        if claim["id"] not in eligible_ids or claim["watch"] in seen_watch:
            continue
        seen_watch.add(claim["watch"])
        watches.append({"event": f"平台经营验证 · {claim['topic']}", "watch": claim["watch"],
                        "source_ids": claim["source_ids"], "platform_claim_ids": [claim["id"]]})
    result["catalysts"] = watches[:4] + result["catalysts"][:5]
    if research["documents"]:
        result["data_gaps"] = [gap for gap in result["data_gaps"] if not gap.startswith("平台暂无该股")]
    result["data_gaps"] = list(dict.fromkeys(result["data_gaps"] + research["gaps"]))
    coverage = sum(row["read"] > 0 for row in research["coverage"])
    available = sum(dimension["signal"] != "insufficient" for dimension in result["dimensions"])
    result["summary"]["coverage"]["available"] = available
    headline = result["summary"]["headline"].split(" · ")[0]
    result["summary"]["headline"] = f"{headline} · {available}/6 个维度可用 · 平台 {coverage} 类 / {len(research['documents'])} 份可见资料"
    if research["conflicts"]:
        result["summary"]["headline"] += f" · {len(research['conflicts'])} 组正反线索待复核"
    return result


def build_stock_intelligence(symbol: str, market: str, *, name: str = "", quote: Optional[dict] = None,
                             financials: Optional[dict] = None, valuation: Optional[dict] = None,
                             forecasts: Optional[dict] = None, profile: Optional[dict] = None,
                             history: Optional[list] = None, history_provider: str = "eastmoney",
                             history_adjustment: str = "前复权", history_currency: Optional[str] = None,
                             flow: Optional[dict] = None,
                             news: Optional[list] = None, platform_messages: Optional[list] = None,
                             cashflow: Optional[dict] = None, balance: Optional[dict] = None,
                             nasdaq: Optional[dict] = None, consensus: Optional[dict] = None,
                             broker_ratings: Optional[dict] = None,
                             platform_channels: Optional[list] = None,
                             now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(_BJ)
    generated = now.isoformat(timespec="seconds")
    q, fin, val = quote or {}, financials or {}, valuation or {}
    fc, prof, ff = forecasts or {}, profile or {}, flow or {}
    price = _number(q.get("price"))
    price = price if price is not None and price > 0 and not _synthetic(q) else None
    currency = q.get("currency") or {"CN": "CNY", "HK": "HKD", "US": "USD"}[market]
    exchange = "sh" if symbol.startswith("6") else "bj" if symbol.startswith(("4", "8", "92")) else "sz"
    code_url = f"https://quote.eastmoney.com/{exchange}{symbol}.html" if market == "CN" else f"https://quote.eastmoney.com/hk/{symbol}.html" if market == "HK" else f"https://finance.yahoo.com/quote/{symbol}/"
    sources, evidence, dimensions, gaps, risks, catalysts = [], [], [], [], [], []

    def add_source(key, label, data, url=None, as_of=None, stale_days=10):
        status, freshness = _freshness(as_of, now, stale_days) if data else ("missing", "本次未获取到可用数据")
        sources.append({"id": key, "label": label, "url": url, "as_of": as_of, "status": status,
                        "freshness": freshness, "fetched_at": generated})
        return status

    quote_status = add_source("quote", q.get("provider_name") or q.get("provider") or "公共行情", price, code_url,
                             q.get("market_time") or q.get("as_of"), stale_days=7)
    financial_status = add_source("financials", "东方财富 · 公司财报", fin, code_url, fin.get("report_date"), stale_days=200)
    valuation_url = f"https://stockanalysis.com/stocks/{symbol.lower()}/statistics/" if market == "US" else code_url
    valuation_status = add_source("valuation", "东方财富 · 估值" if market != "US" else "StockAnalysis · 估值", val, valuation_url, val.get("as_of") or val.get("trade_date"), stale_days=10)
    forecast_status = add_source("forecast", "东方财富 · 券商年度盈利预测", fc if _number(fc.get("eps_median")) is not None else None, f"https://data.eastmoney.com/report/{symbol}.html" if market == "CN" else None,
                                 fc.get("as_of"), stale_days=90)
    add_source("profile", "东方财富 · 公司概况", prof, prof.get("url") or code_url, prof.get("as_of"), stale_days=365)
    flow_status = add_source("flow", "东方财富 · 主力资金统计", ff, code_url, ff.get("flow_end_date"), stale_days=7)
    flow_source = sources[-1]
    if ff:
        flow_source["fetched_at"] = ff.get("as_of") or generated
        if not ff.get("flow_end_date"):
            fetch_status, _ = _freshness(ff.get("as_of"), now, 7)
            if fetch_status == "stale":
                flow_status = flow_source["status"] = "stale"
            flow_source["freshness"] = f"取数时间 {ff.get('as_of') or '未知'}；5 日覆盖结束日未提供，不能核验交易时点"
    add_source("nasdaq", "Nasdaq · 季度盈利记录", nasdaq, f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/earnings" if market == "US" else None)
    add_source("consensus", "公开券商目标价汇总", consensus, f"https://stockanalysis.com/stocks/{symbol.lower()}/forecast/" if market == "US" else f"https://data.eastmoney.com/report/{symbol}.html")

    def dimension(key, label, headline, *, signal="neutral", metrics=None, points=None, refs=None):
        dimensions.append({"key": key, "label": label, "signal": signal, "headline": headline,
                           "metrics": metrics or [], "evidence": points or [], "source_ids": refs or []})

    def metric(label, value, unit=""):
        return {"label": label, "value": round(value, 3) if isinstance(value, (float, int)) else value, "unit": unit}

    # Platform records have already passed the exact account/member visibility gate.
    for item in platform_messages or []:
        if not isinstance(item, dict) or not _text(item.get("title")):
            continue
        sid = f"platform:{item.get('id')}"
        evidence.append({"id": sid, "origin": "platform", "kind": item.get("topic") or "平台资料",
                         "title": _text(item.get("title"), 180), "summary": _text(item.get("content"), 180),
                         "published_at": item.get("created_at"), "url": item.get("url"),
                         "source": item.get("source_name") or "稻草财经"})
        add_source(sid, item.get("source_name") or "稻草财经", True, item.get("url"), item.get("created_at"), stale_days=30)
    seen = {re.sub(r"\s+", "", e["title"]).casefold() for e in evidence}
    for i, item in enumerate(news or []):
        title = _text(item.get("title"), 180)
        normalized = re.sub(r"\s+", "", title).casefold()
        if not title or normalized in seen:
            continue
        seen.add(normalized)
        sid = f"news:{i}"
        evidence.append({"id": sid, "origin": "public", "kind": "公开新闻", "title": title,
                         "summary": _text(item.get("summary"), 180), "published_at": item.get("date"),
                         "url": item.get("url"), "source": item.get("source_name") or "东方财富资讯"})
        add_source(sid, item.get("source_name") or "东方财富资讯", True, item.get("url"), item.get("date"), stale_days=30)
    for i, item in enumerate(fc.get("reports", [])[:4]):
        sid = f"report:{i}"
        summary = f"{fc['year']} 年 EPS 预测 {item['eps']:.2f} CNY；券商预测，尚未兑现。"
        evidence.append({"id": sid, "origin": "public", "kind": "券商研报", "title": item["title"],
                         "summary": summary, "published_at": item["published_at"], "url": item["url"], "source": item["broker"] or "公开券商研报"})
        add_source(sid, item["broker"] or "公开券商研报", True, item["url"], item["published_at"], stale_days=90)

    business_points = [p for p in [prof.get("description"), prof.get("business_scope")] if p]
    business_refs = ["profile"] if business_points else []
    business_articles = [e for e in evidence if any(word in e["title"] + e["summary"] for word in ("主营", "商业模式", "竞争格局", "收入结构", "客户", "订单", "产能"))][:3]
    business_points += [f"{e['title']}：{e['summary']}" for e in business_articles]
    business_refs += [e["id"] for e in business_articles]
    dimension("business", "商业模式与竞争力", prof.get("industry") or ("已找到业务相关资料，需核对收入结构" if business_points else "缺少可验证的公司业务资料"),
              signal="neutral" if business_points else "insufficient", points=business_points + (["公司简介与经营范围属于业务事实；护城河需由客户留存、定价权及同业对比验证。"] if business_points else []), refs=business_refs)
    gaps.append("细分业务收入、客户集中度、竞争份额与资本回报率尚未完整接入")

    fin_metrics = [metric(label, value, unit) for label, value, unit in [
        ("营收同比", _number(fin.get("revenue_yoy")), "%"), ("归母净利同比", _number(fin.get("profit_yoy")), "%"),
        ("加权 ROE", _number(fin.get("roe")), "%"), ("毛利率", _number(fin.get("gross_margin")), "%"),
        ("报告期 EPS（累计）", _number(fin.get("eps")), "CNY" if market == "CN" else "财报币种未核验"),
        ("PE TTM", _number(val.get("pe_ratio")), "倍"), ("动态 PE（非 TTM）", _number(val.get("pe_dynamic")), "倍"),
        ("PB", _number(val.get("pb_ratio")), "倍")
    ] if value is not None]
    py = _number(fin.get("profit_yoy"))
    fundamental_points = [f"财报期间 {fin.get('report_date')}；EPS 为本报告期累计数，未简单乘 4 年化。"] if fin.get("report_date") else []
    cf = cashflow or {}
    if cf and cf.get("report_date") == fin.get("report_date"):
        add_source("cashflow", "东方财富 · 公司现金流表", cf, code_url, cf.get("report_date"), stale_days=200)
    if market == "HK" and fin.get("eps") is not None:
        fundamental_points.append("港股财报 EPS 币种尚未由公司公告核验；未采用数据商 CURRENCY 字段参与绝对估值。")
        gaps.append("港股财报金额/EPS 币种与港元报价可能不同，待官方财报与汇率交叉核验")
    ocf, net = _number(cf.get("ocf")), _number(fin.get("net_income"))
    if ocf is not None and net is not None and net > 0 and cf.get("report_date") == fin.get("report_date"):
        fin_metrics.append(metric("经营现金流/归母净利", ocf / net, "倍"))
        fundamental_points.append("经营现金流与归母净利来自同一报告期间，合并现金流与归母口径仍有差异。")
        if ocf < net * 0.7:
            risks.append("同报告期经营现金流低于归母净利，需检查回款与利润现金转化")
    elif cf:
        gaps.append("现金流与盈利报告期不一致，未计算现金含金量")
    bal = balance or {}
    if bal and bal.get("report_date") == fin.get("report_date"):
        add_source("balance", "东方财富 · 公司资产负债表", bal, code_url, bal.get("report_date"), stale_days=200)
        ar, inv, rev = _number(bal.get("accounts_receivable")), _number(bal.get("inventory")), _number(fin.get("revenue"))
        if ar is not None and rev is not None and rev > 0:
            fin_metrics.append(metric("期末应收/报告期营收", ar / rev * 100, "%"))
        if inv is not None and rev is not None and rev > 0:
            fin_metrics.append(metric("期末存货/报告期营收", inv / rev * 100, "%"))
        fundamental_points.append("应收/存货指标为期末余额对累计收入，不能直接当作周转天数。")
    dimension("fundamentals", "财务与盈利质量", f"归母净利同比 {py:+.1f}%" if py is not None else "财务增长与现金质量待补齐",
              signal="insufficient" if financial_status == "stale" else "bullish" if py is not None and py >= 15 else "bearish" if py is not None and py <= -5 else "neutral" if fin_metrics else "insufficient",
              metrics=fin_metrics, points=fundamental_points, refs=[k for k, d in [("financials", fin), ("valuation", val), ("cashflow", cf if cf.get("report_date") == fin.get("report_date") else None), ("balance", bal if bal.get("report_date") == fin.get("report_date") else None)] if d])
    if py is not None and py < 0:
        risks.append(f"最近财报归母净利同比下降 {abs(py):.1f}%，盈利假设须持续验证")
    if financial_status == "stale":
        gaps.append("财报已滞后超过 200 天，历史盈利增长未参与方向结论")

    earnings_metrics, earnings_points, earnings_refs = [], [], []
    has_annual_eps = _number(fc.get("eps_median")) is not None
    if has_annual_eps:
        earnings_metrics = [metric(f"{fc['year']} 年 EPS 中位数", fc.get("eps_median"), fc.get("currency")), metric("去重券商样本", fc.get("broker_count"), "家"),
                            metric("EPS 低值", fc.get("eps_low"), fc.get("currency")), metric("EPS 高值", fc.get("eps_high"), fc.get("currency"))]
        earnings_points = [f"仅保留本年度、最近 180 天每家券商最新的报告；样本期间 {fc.get('oldest_report')}–{fc.get('as_of')}。", "这是采样券商预测，覆盖有限；不是全市场一致预期或已实现利润。"]
        earnings_refs = ["forecast"]
    if nasdaq:
        nxt = _number(nasdaq.get("next_consensus"))
        hist = [(e, _number(e.get("consensus")), _number(e.get("earnings"))) for e in nasdaq.get("history", [])]
        judged = [(c, actual) for _, c, actual in hist if c is not None and actual is not None]
        if nxt is not None:
            earnings_metrics.append(metric("下季度 EPS 预测", nxt, "USD"))
            earnings_points.append("单季度预测不乘 4 代替年度盈利预期。")
        if judged:
            earnings_metrics.append(metric("近季超预期", f"{sum(actual > c for c, actual in judged)}/{len(judged)}", "季"))
        earnings_refs.append("nasdaq")
    if consensus:
        target = _number(consensus.get("avg_target_price") or consensus.get("target_price"))
        if target:
            earnings_metrics.append(metric("券商目标价（第三方观点）", target, consensus.get("currency") or currency))
            earnings_points.append("第三方目标价作为独立观点展示，不直接用最高/最低目标价冒充本平台三情景。")
        earnings_refs.append("consensus")
    dimension("earnings", "盈利预期与估值分歧", f"{fc.get('year')} 年盈利预测 · {fc.get('broker_count')} 家去重样本" if has_annual_eps else "已获取季度/券商观点" if earnings_metrics else "缺少可核验的盈利预测",
              signal="neutral" if earnings_metrics else "insufficient", metrics=earnings_metrics, points=earnings_points, refs=earnings_refs)
    if not has_annual_eps:
        gaps.append("缺少同币种、可核验年度与机构去重的年度 EPS 预测，三情景仅能退化为敏感性分析")

    rows, invalid_history = _history_rows(history, now)
    if _synthetic({"provider": history_provider}):
        rows = []
        gaps.append("历史行情源为示例或模拟数据，未输出历史价格曲线")
    if invalid_history:
        gaps.append(f"已剔除 {invalid_history} 条无效日期、未来日期或非正/非有限收盘记录，未补造缺失交易日")
    source_known = any(tag in history_provider.lower() for tag in ("eastmoney", "东方财富", "yahoo"))
    series_currency = str(history_currency).strip().upper() if history_currency is not None else {"CN": "CNY", "HK": "HKD", "US": "USD"}[market] if source_known else None
    series_currency = series_currency if series_currency in {"CNY", "HKD", "USD"} else None
    if rows and series_currency is None:
        gaps.append("历史日线币种尚未核验，不能与当前报价同轴叠加或直接比较绝对价格")
    elif rows and series_currency != currency:
        gaps.append(f"当前报价币种 {currency} 与历史日线币种 {series_currency} 不一致；未换汇，不能叠加现价或直接比较涨跌/绝对价格")
    history_as_of = rows[-1][0] if rows else None
    history_status = add_source("history", f"{history_provider} · 日线（{history_adjustment}）", rows, code_url, history_as_of, stale_days=10)
    technical = {"range": None, "as_of": history_as_of, "adjustment": history_adjustment,
                 "series": [{"date": date, "close": close} for date, close in rows],
                 "series_currency": series_currency, "source_ids": ["history"] if rows else []}
    technical_points, technical_metrics = [], []
    technical_signal = "insufficient"
    if len(rows) >= 20:
        closes = [v for _, v in rows]
        last, ma20 = closes[-1], statistics.mean(closes[-20:])
        ma60 = statistics.mean(closes[-60:]) if len(closes) >= 60 else None
        changes = [b - a for a, b in zip(closes[-15:-1], closes[-14:])]
        gains = sum(max(x, 0) for x in changes) / 14
        losses = sum(max(-x, 0) for x in changes) / 14
        rsi = 50 if not gains and not losses else 100 if not losses else 100 - 100 / (1 + gains / losses)
        returns = [math.log(b / a) for a, b in zip(closes[-21:-1], closes[-20:])]
        volatility = statistics.stdev(returns) * math.sqrt(252) * 100 if len(returns) >= 2 else None
        low, high = min(closes[-20:]), max(closes[-20:])
        technical["range"] = {"lower": round(low, 4), "upper": round(high, 4), "horizon": "最近 20 个交易日", "method": "历史收盘价格带；不是未来价格预测"}
        technical_metrics = [metric("MA20", ma20, series_currency or "源币种未核验"), metric("RSI14（简单均值）", rsi), metric("年化历史波动率", volatility, "%")]
        if ma60 is not None:
            technical_metrics.insert(1, metric("MA60", ma60, series_currency or "源币种未核验"))
        technical_signal = "bullish" if last > ma20 and (ma60 is None or ma20 > ma60) else "bearish" if last < ma20 and (ma60 is None or ma20 < ma60) else "neutral"
        technical_points = [f"截至 {history_as_of} 最近日线收盘 {last:.2f}；{history_adjustment}，均线/RSI/价格带使用同一序列。",
                            f"最近 20 日收盘区间 {low:.2f}–{high:.2f}，只表示历史位置，突破后需重新计算。"]
        if rsi >= 70:
            risks.append("RSI14 高于 70，短期动量较热，但超买并不等于马上下跌")
        elif rsi <= 30:
            risks.append("RSI14 低于 30，短期动量较弱，超卖不保证反弹")
        if volatility is not None and volatility > 60:
            risks.append(f"近 20 日年化历史波动率约 {volatility:.0f}%，情景区间可能低估尾部波动")
        if history_status == "stale":
            technical_signal = "insufficient"
            gaps.append("日线数据滞后超过 10 天，技术指标仅为历史快照")
    else:
        gaps.append("有效日线不足 20 根，未计算技术指标与历史价格带")
    dimension("technical", "技术位置与趋势", "均线向上" if technical_signal == "bullish" else "均线偏弱" if technical_signal == "bearish" else "均线分歧" if technical_signal == "neutral" else "技术数据不足或滞后",
              signal=technical_signal, metrics=technical_metrics, points=technical_points, refs=["history"] if rows else [])

    main_flow = _number(ff.get("main_flow_5d"))
    dimension("capital", "资金面与筹码", f"近 {ff.get('flow_days') or 5} 日主力统计{'净流入' if main_flow > 0 else '净流出' if main_flow < 0 else '持平'}" if main_flow is not None else "缺少可用资金流数据",
              signal="insufficient" if flow_status == "stale" else "neutral" if ff and not ff.get("flow_end_date") else "bullish" if main_flow is not None and main_flow > 0 else "bearish" if main_flow is not None and main_flow < 0 else "neutral" if ff else "insufficient",
              metrics=([metric("主力净流入", main_flow / 1e8, "亿元")] if main_flow is not None else []) + ([metric("龙虎榜净额", _number(ff.get("lhb_net")) / 1e8, "亿元")] if _number(ff.get("lhb_net")) is not None else []),
              points=(["主力资金为成交分类统计，不能证明特定机构买卖，也不代表全市场资金实际进出。", f"资金交易日期覆盖 {ff.get('flow_start_date')}–{ff.get('flow_end_date')}；取数时间 {ff.get('as_of')}" if ff.get("flow_end_date") else "资金交易日期未核验；取数时间不代替覆盖结束日", f"龙虎榜数据日期 {ff.get('lhb_date')}" if ff.get("lhb_date") else "暂无近期龙虎榜交叉验证"] if ff else []), refs=["flow"] if ff else [])
    if ff and not ff.get("flow_end_date"):
        gaps.append("资金统计覆盖结束日尚未核验，仅展示统计值，不参与方向结论")
    if not ff:
        gaps.append("港美股暂无统一口径资金流/机构持仓；A股资金源失败时如实留空" if market != "CN" else "A股主力资金与龙虎榜未获得有效数据")
    if main_flow is not None and main_flow < 0:
        risks.append("近几日主力成交分类统计为净流出，需与量价表现交叉验证")

    capital_series = []
    if not _synthetic(ff):
        by_date = {}
        for item in ff.get("series") or []:
            if not isinstance(item, dict):
                continue
            date = _dated_day(item.get("date"), now)
            if not date:
                continue
            row = {"date": date, **{key: _number(item.get(key)) for key in ("main_net", "large_net", "super_large_net", "medium_net", "small_net", "close")}}
            if row["close"] is not None and row["close"] <= 0:
                row["close"] = None
            if any(value is not None for key, value in row.items() if key != "date"):
                by_date[date] = row
        capital_series = [by_date[date] for date in sorted(by_date)][-280:]
    capital_dates = [item["date"] for item in capital_series]
    capital_as_of = capital_dates[-1] if capital_dates else None
    capital_status, _ = _freshness(capital_as_of, now, 7) if capital_series else ("insufficient", "没有可用每日记录")
    if not any(item[key] is not None for item in capital_series for key in ("main_net", "large_net", "super_large_net", "medium_net", "small_net")):
        capital_status = "insufficient"
    capital_currency = ff.get("currency") or ("CNY" if market == "CN" and capital_series else None)
    capital_price_currency = ff.get("price_currency") or ("CNY" if market == "CN" and capital_series else None)
    capital_result = {"status": capital_status, "currency": capital_currency, "amount_unit": "元",
                      "price_currency": capital_price_currency, "price_adjustment": ff.get("price_adjustment") or "未复权（资金源收盘）",
                      "as_of": capital_as_of, "fetched_at": ff.get("as_of"),
                      "period": {"from": capital_dates[0] if capital_dates else None, "to": capital_as_of},
                      "series": capital_series, "source_ids": ["flow"],
                      "note": "东方财富按成交单大小与买卖方向分类的每日净额（原始单位元），不是机构、华尔街或暗池账户买卖。主力统计包含大单与超大单，两者不能再次加到主力总额。股价为同一资金源的未复权日线收盘，不混入其他复权口径；缺失项留空。历史覆盖以上游实际返回为准，至多 280 条，不补造日期；五日指标仅统计末尾最多 5 条每日记录。"}
    if not capital_series:
        gaps.append("未获得带交易日期的每日资金流，保留旧五日统计但不生成资金历史柱图")

    rated_input = broker_ratings or fc.get("broker_ratings") or {}
    rated_by_broker = {}
    cutoff = (now - timedelta(days=180)).date().isoformat()
    if not _synthetic(rated_input):
        for item in sorted((row for row in rated_input.get("samples", []) if isinstance(row, dict)), key=lambda row: str(row.get("published_at") or ""), reverse=True)[:100]:
            date = _dated_day(item.get("published_at"), now)
            broker_id = _text(item.get("broker_id"), 100)
            if not date or date < cutoff or not broker_id or broker_id in rated_by_broker:
                continue
            raw_rating = _text(item.get("rating"), 100) or "评级未提供"
            rated_by_broker[broker_id] = {"broker_id": broker_id, "broker": _text(item.get("broker"), 100) or broker_id,
                                           "published_at": date, "rating": raw_rating, "rating_key": _rating_key(raw_rating), "url": item.get("url")}
    rated_samples = list(rated_by_broker.values())
    rated_dates = [item["published_at"] for item in rated_samples]
    ratings_as_of = max(rated_dates) if rated_dates else None
    ratings_source_status = add_source("broker-ratings", "东方财富 · 公开券商评级样本", rated_samples,
                                       f"https://data.eastmoney.com/report/{symbol}.html" if market == "CN" else ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_HKF10_INFO_ORGRATING&columns=ALL&pageSize=100&sortColumns=PUBLISH_DATE&sortTypes=-1" + f"&filter=(SECUCODE=%22{symbol}.HK%22)") if market == "HK" else None,
                                       ratings_as_of, stale_days=90)
    if rated_input.get("fetched_at"):
        sources[-1]["fetched_at"] = rated_input["fetched_at"]
    ratings_known = any(item["rating_key"] != "other" for item in rated_samples)
    ratings_result = {"status": ratings_source_status if ratings_known else "insufficient",
                      "distribution": [{"key": key, "label": label, "count": sum(item["rating_key"] == key for item in rated_samples)} for key, label in _RATING_LABELS.items()],
                      "samples": rated_samples, "sample_count": len(rated_samples), "broker_count": len(rated_samples),
                      "period": {"from": min(rated_dates) if rated_dates else None, "to": ratings_as_of},
                      "as_of": ratings_as_of, "source_ids": ["broker-ratings"],
                      "note": rated_input.get("note") or "仅使用最多 100 条、最近 180 天带发布日期和券商标识的公开报告，每券商保留最新记录；未知或未提供评级单列其他。样本不是全市场覆盖，评级为第三方观点。未用机构家数或平均评级补造分布。"}
    if not ratings_known:
        gaps.append("没有可核验日期、券商和明确评级的样本，未使用评级均值或机构总数补造环图")

    platform_count = sum(e["origin"] == "platform" for e in evidence)
    public_count = sum(e["origin"] == "public" for e in evidence)
    fresh_ids = {s["id"] for s in sources if s["status"] == "available" and s["as_of"]}
    fresh_news = [e for e in evidence if e["id"] in fresh_ids and e["kind"] != "券商研报"]
    dimension("news", "平台信息与公开催化", f"平台 {platform_count} 条 · 公共资料 {public_count} 条{' · 仅历史线索' if evidence and not fresh_news else ''}" if evidence else "暂未找到匹配消息",
              signal="neutral" if fresh_news else "insufficient", metrics=[metric("平台证据", platform_count, "条"), metric("公共证据", public_count, "条")],
              points=[f"{e['published_at'] or '日期未知'} · {e['title']}" for e in evidence[:4]], refs=[e["id"] for e in evidence[:8]])
    if not platform_count:
        gaps.append("平台暂无该股可见匹配资料，无法形成平台独有证据；未用公共新闻代替平台内容")
    if not public_count:
        gaps.append("暂无匹配公开消息/研报，不据此推断公司没有事件")
    if evidence and not fresh_news:
        gaps.append("新闻仅有历史或日期未知资料；未把历史已完成事件列为即将发生的催化")
    event_words = {"财报": ("财报", "业绩", "季报", "年报"), "订单与产能": ("订单", "产能", "项目", "投产"), "政策与监管": ("政策", "监管", "调查", "处罚"), "股本与股东": ("回购", "增持", "减持", "解禁", "分红")}
    for label, words in event_words.items():
        selected = [e for e in fresh_news if any(w in e["title"] for w in words)][:2]
        if selected:
            catalysts.append({"event": label + " · " + selected[0]["title"], "watch": "消息线索待公告/财报核实；检查兑现时间、实际规模与对利润的影响。", "source_ids": [e["id"] for e in selected]})
    catalysts.append({"event": "下一次财报验证", "watch": "核对营收增长、利润率、经营现金流及年度 EPS 预测是否需要修订；具体披露日期未确认。", "source_ids": ["financials", "forecast"] if fc else ["financials"] if fin else []})

    pe = _number(val.get("pe_ratio"))
    eps = _number(fc.get("eps_median"))
    same_currency = not val.get("currency") or val.get("currency") == currency
    forecast_usable = (eps is not None and eps > 0 and fc.get("year") == now.year and fc.get("currency") == currency
                       and forecast_status != "stale" and (_number(fc.get("broker_count")) or 0) >= 2)
    ready = price is not None and pe is not None and pe > 0 and same_currency and quote_status != "stale" and valuation_status != "stale" and not _synthetic(val)
    status = "ready" if ready and forecast_usable else "sensitivity" if ready else "insufficient"
    anchor = {"price": price, "pe_ttm": pe, "forecast_eps": eps if forecast_usable else None, "forecast_year": fc.get("year") if forecast_usable else None}
    method = "券商年度 EPS × 当前 TTM PE 情景假设" if status == "ready" else "当前股价的盈利与估值敏感性" if status == "sensitivity" else "数据不足，暂不计算股价情景"
    valuation_refs = ["quote", "valuation"] + (["forecast"] if status == "ready" else [])
    scenarios = []
    for key, label, eps_factor, multiple_factor in [("optimistic", "乐观", 1.15, 1.2), ("base", "中等", 1.0, 1.0), ("pessimistic", "悲观", 0.85, 0.8)]:
        scenario_price = round((eps * pe if status == "ready" else price) * eps_factor * multiple_factor, 4) if status != "insufficient" else None
        assumptions = ([f"{fc['year']} 年 EPS {eps * eps_factor:.2f} {currency}（预测样本中位数 × {eps_factor:.2f}）", f"PE 假设 {pe * multiple_factor:.2f} 倍（当前 TTM PE × {multiple_factor:.2f}）"] if status == "ready" else
                       [f"当前盈利基础变动 {(eps_factor - 1) * 100:+.0f}%", f"当前估值倍数变动 {(multiple_factor - 1) * 100:+.0f}%", "当前价格校准，不代表独立估值或年度盈利预测"] if status == "sensitivity" else ["需要正值行情、同币种的正值 PE TTM；动态 PE 不替代 TTM"])
        if key == "optimistic":
            reasons = ["假设盈利改善与估值扩张同时发生；需由订单/收入兑现、利润率和现金回款验证。"]
            invalidation = ["盈利预测下修、利润率下降或估值无法扩张时，乐观假设失效。"]
        elif key == "pessimistic":
            reasons = ["压力假设为盈利下修与估值压缩同时发生；不是最大亏损边界。"]
            invalidation = ["若利润/现金流改善并获得持续估值修复，悲观压力假设需要重算。"]
        else:
            reasons = ["采用当前 TTM 估值倍数作为自定义参考，假设市场风险偏好维持。"]
            invalidation = ["财报或宏观/政策改变盈利基础或估值中枢时重新计算。"]
        if py is not None:
            reasons.append(f"已观察事实：最新归母净利同比 {py:+.1f}%；不能据此保证未来增长。")
        if main_flow is not None:
            reasons.append(f"资金交叉证据：近 {ff.get('flow_days') or 5} 日主力统计净流入 {main_flow / 1e8:+.2f} 亿元。")
        if technical_points:
            reasons.append(f"短期技术交叉证据：截至 {history_as_of} {'均线偏强' if technical_signal == 'bullish' else '均线偏弱' if technical_signal == 'bearish' else '趋势分歧/数据滞后'}；不用于生成长期股价。")
        scenarios.append({"key": key, "label": label, "price": scenario_price, "upside_pct": round((scenario_price / price - 1) * 100, 2) if scenario_price is not None and price else None,
                          "assumptions": assumptions, "reasons": reasons, "invalidation": invalidation, "source_ids": valuation_refs + (["financials"] if py is not None else []) + (["flow"] if main_flow is not None else []) + (["history"] if technical_points else [])})
    if status == "insufficient":
        gaps.append("行情、正值 PE TTM 或币种核验不齐，未生成三情景价格；亏损股未强行套用 PE 法")
    if fc and not forecast_usable:
        gaps.append("年度 EPS 样本不足两家、年份/币种不匹配、过期或预测盈利非正，未用于股价情景计算；非正盈利不套 PE 法")
    valuation_result = {"status": status, "method": method, "horizon": "6–12 个月的情景假设；非到期承诺", "anchor": anchor,
                        "formula": "情景价格 = 年度预测 EPS × 盈利因子 × 当前 TTM PE × 倍数因子" if status == "ready" else "情景价格 = 当前股价 × 盈利因子 × 估值因子" if status == "sensitivity" else "数据不足", "scenarios": scenarios,
                        "note": "盈利 ±15%、倍数 ±20% 为本版固定压力假设，不是模型预测、置信区间或公允价值。中等情景为参考假设；无概率权重；尾部风险可超出区间。券商预测/目标价属于第三方观点。", "source_ids": valuation_refs}
    gaps.append("估值历史分位、同业可比、股权稀释、净负债、股东持仓与确切事件日历待补齐")
    risks.append("三情景未计入分红总回报、增发稀释、汇率变化与极端事件，结果随输入和假设变化")
    available = sum(d["signal"] != "insufficient" for d in dimensions)
    bullish = sum(d["signal"] == "bullish" for d in dimensions)
    bearish = sum(d["signal"] == "bearish" for d in dimensions)
    stance = "信息不足" if available < 3 else "证据存在分歧" if bullish and bearish else "已观测证据偏强" if bullish > bearish else "已观测证据偏弱" if bearish > bullish else "等待验证"
    directional = [f"{d['label']}{'偏强' if d['signal'] == 'bullish' else '偏弱'}" for d in dimensions if d["signal"] in {"bullish", "bearish"}]
    crosscheck = "；".join(directional[:3])
    result = {"symbol": symbol, "name": name or prof.get("name") or fin.get("name") or q.get("name") or symbol,
            "market": market, "currency": currency, "generated_at": generated,
            "quote": {"price": price, "change_percent": _number(q.get("change_percent")), "as_of": q.get("market_time") or q.get("as_of"), "source": q.get("provider_name") or q.get("provider") or "无行情"},
            "summary": {"headline": f"{stance}{'：' + crosscheck if crosscheck else ''} · {available}/6 个维度可用 · 平台 {platform_count} 条证据", "stance": stance, "coverage": {"available": available, "total": 6}},
            "valuation": valuation_result, "dimensions": dimensions, "technical": technical, "capital": capital_result,
            "broker_ratings": ratings_result, "evidence": evidence,
            "catalysts": catalysts[:5], "risks": list(dict.fromkeys(risks))[:8], "data_gaps": list(dict.fromkeys(gaps)), "sources": sources}
    if platform_channels is not None:
        return _attach_platform_research(result, platform_channels, now=now)
    return result


async def gather_stock_intelligence(symbol: str, *, name: str = "", market: str = "", refresh: bool = False,
                                    message_view: Callable[[Any], Any],
                                    platform_source_loader: Optional[Callable[[dict], Awaitable[list]]] = None) -> dict:
    """Bounded independent source calls; source outages produce explicit gaps."""
    from .eastmoney_data import (_em_stock_secid, fetch_eastmoney_balance, fetch_eastmoney_cashflow,
                                 fetch_eastmoney_earnings, fetch_eastmoney_kline, fetch_fund_flow)
    from .market_data import _cn_market_prefix, fetch_market_quotes
    from .stock_news import fetch_stock_news
    from .valuation_source import fetch_valuation
    from .yahoo_finance import fetch_yahoo_history
    from .nasdaq_data import fetch_nasdaq_earnings
    from .consensus_source import fetch_analyst_consensus
    from .cn_consensus import fetch_cn_consensus

    sym, mk = normalize_instrument(symbol, market)
    async def safe(awaitable, default=None):
        try:
            return await asyncio.wait_for(awaitable, timeout=12.0)
        except Exception:
            return default

    secid = (_cn_market_prefix(sym)[0] + sym) if mk == "CN" else _em_stock_secid(sym, mk) if mk == "HK" else None
    async def history_fetch():
        # Never mix incompatible adjusted and unadjusted series.
        if secid:
            parameters = inspect.signature(fetch_eastmoney_kline).parameters
            options = {"points": 280}
            if "force_fresh" in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
                options["force_fresh"] = refresh
            return await fetch_eastmoney_kline(secid, **options)
        return await fetch_yahoo_history(sym, mk, range_="1y")

    profile_task = asyncio.create_task(safe(fetch_company_profile(sym, mk, refresh=refresh)))
    async def platform_fetch():
        if platform_source_loader is None:
            return await safe(asyncio.to_thread(load_visible_platform_messages, sym, message_view), [])
        profile_data = await profile_task or {}
        query = {"symbol": sym, "name": profile_data.get("name") or name,
                 "market": mk, "aliases": [name] if name else [],
                 "industry": profile_data.get("industry") or "", "refresh": refresh, "window_days": 365}
        try:
            return await asyncio.wait_for(platform_source_loader(query), timeout=25.0)
        except Exception:
            return [{"channel": "platform_loader", "label": "平台多源取证", "status": "error", "items": [],
                     "coverage": {"scanned": 0, "matched": 0, "returned": 0, "complete": False,
                                  "reason": "取证未完成或超时；未将旧缓存或无权限内容当作成功结果"}}]

    values = await asyncio.gather(
        safe(fetch_market_quotes([f"{sym}.HK" if mk == "HK" else sym])),
        safe(fetch_eastmoney_earnings(sym, mk)) if mk != "US" else safe(asyncio.sleep(0)),
        safe(fetch_valuation(sym, mk)), safe(fetch_annual_forecasts(sym, mk, refresh=refresh)),
        profile_task, safe(history_fetch(), []),
        safe(fetch_verified_fund_flow(sym, mk, refresh=refresh)), safe(fetch_stock_news(sym, mk, limit=8), []),
        platform_fetch(),
        safe(fetch_eastmoney_cashflow(sym, mk)), safe(fetch_eastmoney_balance(sym, mk)),
        safe(fetch_nasdaq_earnings(sym)) if mk == "US" else safe(asyncio.sleep(0)),
        safe(fetch_analyst_consensus(sym, mk)) if mk == "US" else safe(fetch_cn_consensus(sym, mk)),
        safe(fetch_dated_broker_ratings(sym, mk)) if mk == "HK" else safe(asyncio.sleep(0)),
    )
    quotes, fin, val, fc, prof, hist, ff, news, platform, cf, bal, nasdaq, cons, ratings = values
    quote = quotes.quotes[0].model_dump(mode="json") if quotes and quotes.quotes else None
    dated_history, _ = _history_rows(hist, datetime.now(_BJ))
    if quote is None and dated_history:
        # The latest adjusted close is a dated research anchor, never a real-time
        # substitute. Do not fill an unavailable quote from an old or mock series.
        date, close = dated_history[-1]
        status, _ = _freshness(date, datetime.now(_BJ), 7)
        if close is not None and close > 0 and status != "stale":
            quote = {"symbol": sym, "price": close, "currency": {"CN": "CNY", "HK": "HKD", "US": "USD"}[mk],
                     "market_time": date, "provider": "eastmoney-daily-close" if secid else "yahoo-daily-close",
                     "provider_name": "东方财富 · 日线收盘参考（非实时）" if secid else "Yahoo Finance · 日线收盘参考（非实时）"}
    return build_stock_intelligence(sym, mk, name=name, quote=quote, financials=fin, valuation=val, forecasts=fc,
                                    profile=prof, history=hist, history_provider="东方财富" if secid else "Yahoo Finance",
                                    history_adjustment="前复权" if secid else "未复权收盘（拆股/分红需另核验）",
                                    history_currency={"CN": "CNY", "HK": "HKD", "US": "USD"}[mk], flow=ff, news=news,
                                    platform_messages=platform if platform_source_loader is None else None,
                                    platform_channels=platform if platform_source_loader is not None else None,
                                    cashflow=cf, balance=bal, nasdaq=nasdaq, consensus=cons,
                                    broker_ratings=ratings)
