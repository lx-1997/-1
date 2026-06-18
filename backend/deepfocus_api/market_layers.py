from __future__ import annotations

import time
from typing import Optional

from .data_sources import list_keyword_provider_policies
from .market_data import fetch_market_quotes, normalize_symbols
from .schemas import MarketDataLayerStatus, MarketDataLayerStatusResponse
from .shared_utils import utc_now_iso
from .tushare_data import normalize_ts_code, tushare_token_configured


async def build_market_data_layer_status(
    *,
    symbol: Optional[str] = None,
    keyword: Optional[str] = None,
) -> MarketDataLayerStatusResponse:
    normalized_symbol = (normalize_symbols([symbol or ""])[0] if symbol else None)
    layers: list[MarketDataLayerStatus] = []

    quote_started = time.perf_counter()
    quote_warnings: list[str] = []
    quote_provider = None
    quote_status = "partial"
    quote_detail = "东方财富优先，新浪/腾讯兜底；港美股再走公开备用源。"
    if normalized_symbol:
        quote_response = await fetch_market_quotes([normalized_symbol])
        quote_warnings = quote_response.warnings
        if quote_response.quotes:
            quote_provider = quote_response.quotes[0].provider_name
            quote_status = "ready"
            quote_detail = f"{normalized_symbol} 当前由 {quote_provider} 返回行情。"
        else:
            quote_status = "error"
            quote_detail = f"{normalized_symbol} 未拿到可用行情。"

    layers.append(
        MarketDataLayerStatus(
            key="free_quote",
            name="免费行情层",
            status=quote_status,  # type: ignore[arg-type]
            providers=["东方财富", "新浪财经", "腾讯行情", "Stooq"],
            primary_provider=quote_provider or "东方财富",
            configured=True,
            trust_level="media",
            coverage="A 股、港股、部分美股公共行情快照",
            detail=quote_detail,
            remediation="如公共源波动，可切换/补充付费行情源或本地数据包。" if quote_status != "ready" else None,
            latency_ms=round((time.perf_counter() - quote_started) * 1000, 1),
            warnings=quote_warnings[:5],
        )
    )

    tushare_ready = tushare_token_configured()
    is_ashare = bool(normalize_ts_code(normalized_symbol or ""))
    layers.append(
        MarketDataLayerStatus(
            key="structured_ashare",
            name="A 股结构化层",
            status="ready" if tushare_ready else "unconfigured",
            providers=["Tushare Pro"],
            primary_provider="Tushare Pro",
            configured=tushare_ready,
            trust_level="official" if tushare_ready else "unknown",
            coverage="日线、估值、换手、基础信息等结构化字段",
            detail=(
                "Tushare Pro token 已配置，可查询 A 股结构化数据。"
                if tushare_ready
                else "未配置 Tushare Pro token，结构化数据层暂不可用。"
            ),
            remediation=(
                "配置 TUSHARE_TOKEN / TUSHARE_PRO_TOKEN / TS_TOKEN 后即可启用。"
                if not tushare_ready
                else ("当前示例代码不是 A 股，查询结构化层请使用 600519.SH / 000001.SZ 这类代码。" if normalized_symbol and not is_ashare else None)
            ),
        )
    )

    policies = list_keyword_provider_policies()
    xueqiu_policy = next((item for item in policies if item.get("provider") == "xueqiu"), {})
    wechat_policy = next((item for item in policies if item.get("provider") == "wechat_public"), {})
    xueqiu_configured = bool(xueqiu_policy.get("configured"))
    sentiment_status = "ready" if xueqiu_configured else "fallback"
    layers.append(
        MarketDataLayerStatus(
            key="sentiment",
            name="舆情线索层",
            status=sentiment_status,
            providers=["雪球", "公众号/搜狗微信"],
            primary_provider="雪球" if xueqiu_configured else "公众号/搜狗微信",
            configured=True,
            trust_level="community",
            coverage="社区讨论、公众号搜索结果、事件线索入库",
            detail=(
                "雪球登录态已配置，雪球优先，遇到风控仍会降级到公众号公开搜索。"
                if xueqiu_configured
                else "雪球未配置登录态，默认以公众号公开搜索为稳定舆情入口，雪球遇到风控会降级。"
            ),
            remediation="配置 DEEPFOCUS_XUEQIU_COOKIE / XUEQIU_TOKEN 可提高雪球命中率。" if not xueqiu_configured else None,
            warnings=[
                f"雪球健康分 {xueqiu_policy.get('health_score', '-')}",
                f"公众号健康分 {wechat_policy.get('health_score', '-')}",
            ],
        )
    )

    return MarketDataLayerStatusResponse(
        generated_at=utc_now_iso(),
        symbol=normalized_symbol,
        keyword=keyword,
        layers=layers,
    )
