from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Optional

from .market_data import fetch_market_quotes
from .options_signal import fetch_options_signals
from .shared_utils import clamp
from .schemas import (
    MarketQuote,
    PremarketMappedAsset,
    PremarketOpportunityResponse,
    PremarketOptionSignal,
    PremarketQuoteSignal,
    PremarketThemeSignal,
)


@dataclass(frozen=True)
class ThemeDefinition:
    key: str
    name: str
    leaders: tuple[str, ...]
    option_watch: tuple[str, ...]
    confirmations: tuple[str, ...]
    mapped_assets: tuple[PremarketMappedAsset, ...]


THEMES: tuple[ThemeDefinition, ...] = (
    ThemeDefinition(
        key="ai_compute",
        name="AI算力/半导体",
        leaders=("NVDA", "AMD", "AVGO", "MU", "SMCI"),
        option_watch=("NVDA", "AMD", "AVGO"),
        confirmations=("QQQ", "SMH", "SOXX", "ASHR"),
        mapped_assets=(
            PremarketMappedAsset(symbol="688981.SH", name="中芯国际", market="CN", role="晶圆制造", reason="美股半导体扩散时，A股核心制造映射。"),
            PremarketMappedAsset(symbol="603986.SH", name="兆易创新", market="CN", role="存储/芯片设计", reason="存储与芯片设计弹性标的。"),
            PremarketMappedAsset(symbol="300308.SZ", name="中际旭创", market="CN", role="光模块", reason="AI算力链高相关方向。"),
            PremarketMappedAsset(symbol="300502.SZ", name="新易盛", market="CN", role="光模块", reason="海外AI资本开支映射。"),
        ),
    ),
    ThemeDefinition(
        key="ev_robotics",
        name="新能源车/机器人",
        leaders=("TSLA", "RIVN", "LI", "NIO", "XPEV"),
        option_watch=("TSLA", "LI", "NIO"),
        confirmations=("QQQ", "KWEB", "ASHR"),
        mapped_assets=(
            PremarketMappedAsset(symbol="300750.SZ", name="宁德时代", market="CN", role="动力电池", reason="新能源车产业链核心权重。"),
            PremarketMappedAsset(symbol="002594.SZ", name="比亚迪", market="CN", role="整车", reason="A股新能源车龙头。"),
            PremarketMappedAsset(symbol="601689.SH", name="拓普集团", market="CN", role="汽车零部件", reason="特斯拉链与机器人零部件映射。"),
            PremarketMappedAsset(symbol="002050.SZ", name="三花智控", market="CN", role="热管理/机器人", reason="汽车热管理与机器人执行器预期。"),
        ),
    ),
    ThemeDefinition(
        key="glp1_biotech",
        name="创新药/减肥药/CXO",
        leaders=("LLY", "NVO", "AMGN", "MRNA", "TMO"),
        option_watch=("LLY", "NVO", "MRNA"),
        confirmations=("XLV", "IBB", "ASHR"),
        mapped_assets=(
            PremarketMappedAsset(symbol="600276.SH", name="恒瑞医药", market="CN", role="创新药", reason="A股创新药核心龙头。"),
            PremarketMappedAsset(symbol="603259.SH", name="药明康德", market="CN", role="CXO", reason="海外医药研发景气映射。"),
            PremarketMappedAsset(symbol="300760.SZ", name="迈瑞医疗", market="CN", role="医疗器械", reason="医疗成长风格映射。"),
            PremarketMappedAsset(symbol="688506.SH", name="百利天恒", market="CN", role="创新药", reason="创新药高弹性方向。"),
        ),
    ),
    ThemeDefinition(
        key="china_internet",
        name="中概/港股互联网",
        leaders=("BABA", "PDD", "JD", "BIDU", "TME"),
        option_watch=("BABA", "PDD", "JD"),
        confirmations=("KWEB", "FXI", "ASHR"),
        mapped_assets=(
            PremarketMappedAsset(symbol="09988.HK", name="阿里巴巴-W", market="HK", role="港股互联网", reason="中概与港股双重映射。"),
            PremarketMappedAsset(symbol="00700.HK", name="腾讯控股", market="HK", role="港股互联网", reason="恒生科技权重。"),
            PremarketMappedAsset(symbol="300418.SZ", name="昆仑万维", market="CN", role="AI应用/互联网", reason="A股互联网与AI应用情绪映射。"),
            PremarketMappedAsset(symbol="002555.SZ", name="三七互娱", market="CN", role="游戏传媒", reason="互联网风险偏好扩散方向。"),
        ),
    ),
    ThemeDefinition(
        key="commodities",
        name="黄金/铜油/资源",
        leaders=("GLD", "SLV", "FCX", "XOM", "COP"),
        option_watch=("GLD", "FCX", "XOM"),
        confirmations=("DBC", "USO", "ASHR"),
        mapped_assets=(
            PremarketMappedAsset(symbol="601899.SH", name="紫金矿业", market="CN", role="铜金", reason="金铜价格和全球资源股映射。"),
            PremarketMappedAsset(symbol="603993.SH", name="洛阳钼业", market="CN", role="铜钴", reason="有色金属弹性方向。"),
            PremarketMappedAsset(symbol="600489.SH", name="中金黄金", market="CN", role="黄金", reason="金价强势时的A股映射。"),
            PremarketMappedAsset(symbol="601857.SH", name="中国石油", market="CN", role="油气", reason="油价与能源股映射。"),
        ),
    ),
    ThemeDefinition(
        key="crypto_risk",
        name="加密资产/高Beta风险偏好",
        leaders=("COIN", "MSTR", "MARA", "RIOT"),
        option_watch=("COIN", "MSTR"),
        confirmations=("QQQ", "KWEB", "ASHR"),
        mapped_assets=(
            PremarketMappedAsset(symbol="300339.SZ", name="润和软件", market="CN", role="高Beta软件", reason="风险偏好扩散观察，不是直接业务映射。"),
            PremarketMappedAsset(symbol="300033.SZ", name="同花顺", market="CN", role="金融科技", reason="风险偏好与交易活跃度映射。"),
            PremarketMappedAsset(symbol="3888.HK", name="金山软件", market="HK", role="港股软件", reason="高Beta科技映射。"),
        ),
    ),
)


async def build_premarket_opportunity_radar() -> PremarketOpportunityResponse:
    warnings: list[str] = []
    leader_symbols = _unique(symbol for theme in THEMES for symbol in theme.leaders)
    confirmation_symbols = _unique(symbol for theme in THEMES for symbol in theme.confirmations)
    option_symbols = _unique(symbol for theme in THEMES for symbol in theme.option_watch)[:12]

    # 龙头行情 / 确认行情 / 期权异动 三个独立 I/O 并发拉取
    leader_quotes_response, confirmation_quotes_response, options_response = await asyncio.gather(
        fetch_market_quotes(leader_symbols),
        fetch_market_quotes(confirmation_symbols),
        fetch_options_signals(option_symbols, horizon_days=45, max_expirations=3),
    )
    warnings.extend(leader_quotes_response.warnings)
    warnings.extend(confirmation_quotes_response.warnings)

    quote_by_symbol = {
        quote.symbol.upper(): quote
        for quote in [*leader_quotes_response.quotes, *confirmation_quotes_response.quotes]
    }

    warnings.extend(options_response.warnings)
    option_by_symbol = {signal.symbol.upper(): signal for signal in options_response.signals}

    themes = [
        _score_theme(theme, quote_by_symbol, option_by_symbol)
        for theme in THEMES
    ]
    themes = sorted(themes, key=lambda item: (item.stance == "重点机会", item.opportunity_score - item.risk_score), reverse=True)
    risk_watchlist = sorted(
        [theme for theme in themes if theme.risk_score >= 65 or theme.stance == "风险回避"],
        key=lambda item: item.risk_score,
        reverse=True,
    )[:5]

    regime_score = _regime_score(quote_by_symbol)
    summary = _summary(themes, risk_watchlist, regime_score)
    return PremarketOpportunityResponse(
        generated_at=datetime.now(timezone.utc),
        market_phase=_market_phase_label(),
        regime_score=regime_score,
        summary=summary,
        themes=themes,
        risk_watchlist=risk_watchlist,
        confirmation_checklist=[
            "A股集合竞价同主题龙头是否高开且量能放大，弱于预期则不追。",
            "开盘 15-30 分钟是否站上均价线并有板块扩散，只有单票冲高要降级。",
            "港股/中概/富时A50/人民币若反向走弱，跨市场传导分自动降权。",
            "期权左尾红灯一票否决：即使主题强，也先按风险事件处理。",
        ],
        warnings=_unique(warnings)[:8],
    )


def _score_theme(
    theme: ThemeDefinition,
    quote_by_symbol: dict[str, MarketQuote],
    option_by_symbol: dict[str, object],
) -> PremarketThemeSignal:
    leader_quotes = [_quote_signal(symbol, quote_by_symbol.get(symbol), "美股主题龙头") for symbol in theme.leaders]
    valid_leaders = [item for item in leader_quotes if item.change_percent is not None]
    leader_changes = [item.change_percent or 0 for item in valid_leaders]
    avg_change = mean(leader_changes) if leader_changes else 0.0
    breadth = sum(1 for value in leader_changes if value > 0.3) / len(leader_changes) if leader_changes else 0.5
    negative_breadth = sum(1 for value in leader_changes if value < -0.8) / len(leader_changes) if leader_changes else 0.0
    us_impulse = int(round(clamp(50 + avg_change * 9 + (breadth - 0.5) * 32, 0, 100)))

    option_signals: list[PremarketOptionSignal] = []
    option_scores: list[int] = []
    tail_risk = 0
    for symbol in theme.option_watch:
        signal = option_by_symbol.get(symbol)
        if not signal:
            continue
        forecast_score = int(getattr(signal, "forecast_score", 50))
        tail_score = int(getattr(signal, "tail_event_risk_score", 0))
        tail_level = str(getattr(signal, "tail_event_risk_level", "绿灯"))
        option_scores.append(forecast_score)
        tail_risk = max(tail_risk, tail_score)
        option_signals.append(
            PremarketOptionSignal(
                symbol=symbol,
                forecast_label=str(getattr(signal, "forecast_label", "不可判定")),
                forecast_score=forecast_score,
                tail_event_risk_level=tail_level,  # type: ignore[arg-type]
                tail_event_risk_score=tail_score,
                summary=str(getattr(signal, "forecast_summary", "")),
            )
        )
    option_confirmation = int(round(mean(option_scores))) if option_scores else 50

    confirmation_quotes = [_quote_signal(symbol, quote_by_symbol.get(symbol), "确认资产") for symbol in theme.confirmations]
    confirmation_changes = [item.change_percent or 0 for item in confirmation_quotes if item.change_percent is not None]
    china_confirmation = int(round(clamp(50 + (mean(confirmation_changes) if confirmation_changes else 0) * 7, 0, 100)))

    market_risk = _market_risk_penalty(quote_by_symbol)
    opportunity = int(round(clamp(us_impulse * 0.52 + option_confirmation * 0.28 + china_confirmation * 0.20 - market_risk, 0, 100)))
    downside_risk = int(round(clamp(35 + max(-avg_change, 0) * 12 + negative_breadth * 25 + tail_risk * 0.28 + market_risk * 1.4, 0, 100)))

    if downside_risk >= 72 and opportunity < 62:
        stance = "风险回避"
    elif opportunity >= 72 and downside_risk < 66 and breadth >= 0.55:
        stance = "重点机会"
    elif opportunity >= 60 and downside_risk < 76:
        stance = "观察机会"
    else:
        stance = "中性观察"
    direction = "下行" if stance == "风险回避" or avg_change <= -1.2 else "上行" if opportunity >= 58 else "震荡"
    confidence = "高" if len(valid_leaders) >= 3 and option_signals and abs(opportunity - 50) >= 16 else "中" if len(valid_leaders) >= 2 else "低"

    evidence = [
        f"美股主题龙头平均涨跌幅 {avg_change:.2f}%，上涨占比 {breadth:.0%}。",
        f"期权确认分 {option_confirmation}/100，左尾最高风险 {tail_risk}/100。",
        f"确认资产分 {china_confirmation}/100，宏观/指数风险扣分 {market_risk:.0f}。",
    ]
    strongest = sorted(valid_leaders, key=lambda item: abs(item.change_percent or 0), reverse=True)[:3]
    if strongest:
        evidence.append("龙头异动：" + "、".join(f"{item.symbol} {item.change_percent:+.2f}%" for item in strongest if item.change_percent is not None))
    if any(item.tail_event_risk_level in {"橙灯", "红灯"} for item in option_signals):
        evidence.append("期权左尾风险未完全解除，必须降级处理。")

    triggers, invalidations = _theme_triggers(theme, stance)
    action = _suggested_action(theme, stance, opportunity, downside_risk)

    return PremarketThemeSignal(
        theme_key=theme.key,
        theme_name=theme.name,
        stance=stance,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        opportunity_score=opportunity,
        risk_score=downside_risk,
        confidence=confidence,  # type: ignore[arg-type]
        us_impulse_score=us_impulse,
        option_confirmation_score=option_confirmation,
        china_confirmation_score=china_confirmation,
        leader_quotes=[*leader_quotes, *confirmation_quotes],
        option_signals=option_signals,
        mapped_assets=list(theme.mapped_assets),
        evidence=evidence,
        triggers=triggers,
        invalidations=invalidations,
        suggested_action=action,
    )


def _quote_signal(symbol: str, quote: Optional[MarketQuote], role: str) -> PremarketQuoteSignal:
    return PremarketQuoteSignal(
        symbol=symbol,
        name=symbol,
        market=_infer_market(symbol),  # type: ignore[arg-type]
        price=quote.price if quote else None,
        change_percent=quote.change_percent if quote else None,
        provider=quote.provider_name if quote else "",
        role=role,
    )


def _market_risk_penalty(quote_by_symbol: dict[str, MarketQuote]) -> float:
    qqq = _change(quote_by_symbol, "QQQ")
    spy = _change(quote_by_symbol, "SPY")
    ashr = _change(quote_by_symbol, "ASHR")
    kweb = _change(quote_by_symbol, "KWEB")
    penalty = 0.0
    for value in (qqq, spy):
        if value is not None and value < -0.8:
            penalty += abs(value) * 4
    for value in (ashr, kweb):
        if value is not None and value < -1.0:
            penalty += abs(value) * 3
    return clamp(penalty, 0, 14)


def _regime_score(quote_by_symbol: dict[str, MarketQuote]) -> int:
    symbols = ("SPY", "QQQ", "KWEB", "FXI", "ASHR", "SMH")
    changes = [_change(quote_by_symbol, symbol) for symbol in symbols]
    valid = [value for value in changes if value is not None]
    if not valid:
        return 50
    breadth = sum(1 for value in valid if value > 0) / len(valid)
    return int(round(clamp(50 + mean(valid) * 8 + (breadth - 0.5) * 24, 0, 100)))


def _summary(
    themes: list[PremarketThemeSignal],
    risk_watchlist: list[PremarketThemeSignal],
    regime_score: int,
) -> str:
    opportunities = [theme for theme in themes if theme.stance in {"重点机会", "观察机会"}]
    if opportunities:
        top = "、".join(f"{theme.theme_name}({theme.opportunity_score})" for theme in opportunities[:3])
        lead = f"当前跨市场风险偏好 {regime_score}/100，优先观察 {top}。"
    else:
        lead = f"当前跨市场风险偏好 {regime_score}/100，暂无高置信主题机会。"
    if risk_watchlist:
        lead += " 风险优先排查：" + "、".join(f"{item.theme_name}({item.risk_score})" for item in risk_watchlist[:2]) + "。"
    return lead


def _theme_triggers(theme: ThemeDefinition, stance: str) -> tuple[list[str], list[str]]:
    mapped = "、".join(asset.name for asset in theme.mapped_assets[:3])
    if stance == "风险回避":
        return (
            [
                "若A股同主题低开后继续放量走弱，先按风险扩散处理。",
                "只有美股龙头、港股/中概和A股映射同步修复，才解除回避。",
            ],
            [
                "美股龙头隔夜反包且期权左尾降温，风险判断失效。",
                "A股开盘 30 分钟内同主题出现放量修复，回避等级下调。",
            ],
        )
    return (
        [
            f"A股竞价观察 {mapped} 是否高开且量能放大。",
            "开盘 15-30 分钟主题指数和龙头同时站上均价线，才从观察升级到机会。",
            "若港股/中概/富时A50同步走强，传导可信度上调。",
        ],
        [
            "A股映射标的高开低走且跌破开盘均价线，传导失败。",
            "美股龙头盘后/期货转弱，或期权左尾升到橙灯/红灯，机会降级。",
            "人民币、港股或中概明显反向走弱，降低仓位或取消计划。",
        ],
    )


def _suggested_action(theme: ThemeDefinition, stance: str, opportunity: int, risk: int) -> str:
    names = "、".join(asset.name for asset in theme.mapped_assets[:3])
    if stance == "重点机会":
        return f"盘前列为重点机会：先看 {names} 的竞价强度，开盘确认后再小仓试错；机会分 {opportunity}/100，风险分 {risk}/100。"
    if stance == "观察机会":
        return f"列入观察，不抢开盘第一笔；等待 {names} 出现板块扩散和量能确认。"
    if stance == "风险回避":
        return f"先按风险主题处理，避免无确认抄底；若持有 {theme.name} 相关仓位，优先检查止损和仓位暴露。"
    return "当前信号不够集中，保留在观察池，等待美股龙头、确认资产和A股开盘共振。"


def _change(quote_by_symbol: dict[str, MarketQuote], symbol: str) -> Optional[float]:
    quote = quote_by_symbol.get(symbol)
    return quote.change_percent if quote else None


def _infer_market(symbol: str) -> str:
    if symbol.endswith((".SH", ".SZ")):
        return "CN"
    if symbol.endswith(".HK"):
        return "HK"
    return "US"


def _market_phase_label() -> str:
    now = datetime.now(timezone.utc)
    hour = now.hour
    if 13 <= hour <= 20:
        return "美股交易时段/盘前观察"
    if 21 <= hour or hour <= 2:
        return "美股收盘后/亚洲盘前"
    return "亚洲交易时段"


def _unique(items) -> list[str]:
    result: list[str] = []
    for item in items:
        if not item or item in result:
            continue
        result.append(item)
    return result