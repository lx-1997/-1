"""个股速判卡（Tear Sheet）的确定性证据校验引擎。

把行情、财报日历、期权链等多源结构化数据，按机构尽调框架逐维度判定为
「信号 + 支撑证据 + 置信度 + 数据可信度」，再综合出 overall verdict。

设计原则：
- 纯函数、确定性、不依赖外网/LLM —— 因此完全可单测，LLM 仅做叙述层增强。
- 每个维度的 data_quality 由数据来源 provider 推断（复用 classify_data_quality），
  缺数据则诚实标 insufficient，而非编造。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .schemas import (
    BriefingResponse,
    CitedSource,
    DataQuality,
    EarningsCalendarEvent,
    MacroRegime,
    MacroReviewResponse,
    MarketQuote,
    OptionsSignal,
    PortfolioReviewResponse,
    TearSheetDimension,
    TearSheetResponse,
    WatchlistSectorBucket,
    WatchlistSummary,
    classify_data_quality,
)

_QUALITY_RANK = {"live": 0, "degraded": 1, "mock": 2}
# 方向性维度决定多空 verdict；其余（催化/规模）是 context，不稀释方向判断。
_DIRECTIONAL_KEYS = {"momentum", "options"}

# 真实数据维度的可溯源出处（github 公开数据集，可点击查看原始数据）
_SP500_SRC = CitedSource(label="标普500 月度指数", provider="github · datasets/s-and-p-500", url="https://github.com/datasets/s-and-p-500", quality="live")
_US10Y_SRC = CitedSource(label="美国10Y 国债收益率", provider="github · datasets", url="https://github.com/datasets/bond-yields-us-10y", quality="live")
_OIL_SRC = CitedSource(label="WTI 原油价格", provider="github · datasets", url="https://github.com/datasets/oil-prices", quality="live")
_GOLD_SRC = CitedSource(label="伦敦金价", provider="github · datasets", url="https://github.com/datasets/gold-prices", quality="live")
_CN_INDEX_SRC = CitedSource(label="A股/港股大盘指数", provider="东方财富", url="https://quote.eastmoney.com/center/", quality="live")
_VIX_SRC = CitedSource(label="VIX 恐慌指数", provider="新浪财经 · VIX 期货", url="https://finance.sina.com.cn/futures/quotes/VX.shtml", quality="live")
_CURVE_SRC = CitedSource(label="美债 2s10s 收益率曲线", provider="美国财政部 · 10Y-2Y", url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates", quality="live")
_CREDIT_SRC = CitedSource(label="ICE BofA 高收益债 OAS", provider="FRED · BAMLH0A0HYM2", url="https://fred.stlouisfed.org/series/BAMLH0A0HYM2", quality="live")
_DXY_SRC = CitedSource(label="美元指数 DXY", provider="新浪财经", url="https://finance.sina.com.cn/money/forex/", quality="live")
_CPI_SRC = CitedSource(label="美国 CPI 同比", provider="FRED/BLS · CPIAUCSL", url="https://fred.stlouisfed.org/series/CPIAUCSL", quality="live")
_UNEMP_SRC = CitedSource(label="美国失业率", provider="FRED/BLS · UNRATE", url="https://fred.stlouisfed.org/series/UNRATE", quality="live")
_REALRATE_SRC = CitedSource(label="10Y 实际利率 (TIPS)", provider="美国财政部 · 实际收益率曲线", url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates", quality="live")
_BREAKEVEN_SRC = CitedSource(label="10Y 通胀预期 (Breakeven)", provider="美国财政部 · 名义-实际", url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates", quality="live")
# 中国宏观轴（东财/新浪直连，沙箱可达）
_CSI300_SRC = CitedSource(label="沪深300", provider="东方财富", url="https://quote.eastmoney.com/zs000300.html", quality="live")
_NORTHBOUND_SRC = CitedSource(label="北向资金净流入", provider="东方财富 · 沪深港通", url="https://data.eastmoney.com/hsgt/index.html", quality="live")
_CNY_SRC = CitedSource(label="美元兑人民币", provider="新浪财经", url="https://finance.sina.com.cn/money/forex/", quality="live")
_CN10Y_SRC = CitedSource(label="中国10年期国债", provider="东方财富", url="https://quote.eastmoney.com/bond/1.1B0300.html", quality="live")

# 期权方向（中文枚举）→（信号, 基准分）
_DIR_MAP: dict[str, tuple[str, int]] = {
    "看多": ("bullish", 55),
    "偏多": ("bullish", 40),
    "震荡偏强": ("bullish", 22),
    "中性": ("neutral", 0),
    "震荡": ("neutral", 0),
    "震荡偏弱": ("bearish", -22),
    "偏空": ("bearish", -40),
    "看跌": ("bearish", -55),
    "看空": ("bearish", -55),
    "不可判定": ("insufficient", 0),
}
_CONVICTION_CONF = {"高": 0.7, "中": 0.5, "低": 0.3}


def _worst_quality(items: list[DataQuality]) -> DataQuality:
    """整体可信度取各维度中最差的一档（mock > degraded > live），保持诚实。"""
    worst: Optional[DataQuality] = None
    for dq in items:
        if worst is None or _QUALITY_RANK.get(dq.level, 0) > _QUALITY_RANK.get(worst.level, 0):
            worst = dq
    return worst or DataQuality()


def _insufficient(key: str, label: str, headline: str) -> TearSheetDimension:
    return TearSheetDimension(
        key=key,
        label=label,
        signal="insufficient",
        headline=headline,
        confidence=0.0,
        data_quality=classify_data_quality("none"),
    )


_FRESH_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m", "%m/%d/%Y", "%Y/%m/%d")


def _freshness(latest_date: str, *, stale_days: int = 45) -> tuple[float, Optional[str]]:
    """按最新数据日期推断时效：返回（置信度系数, 陈旧提示）。

    可靠性核心：把"这条数据有多新"显式反映到置信度上——月度/季度源若冻结，
    不再当 live 呈现。无法解析的日期（如单测里的占位串）视为新鲜不罚。
    """
    dt: Optional[datetime] = None
    for fmt in _FRESH_DATE_FORMATS:
        try:
            dt = datetime.strptime(latest_date, fmt)
            break
        except (ValueError, TypeError):
            continue
    if dt is None:
        return 1.0, None
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - dt).days
    if age <= stale_days:
        return 1.0, None
    if age <= stale_days * 2:
        return 0.7, f"数据截至 {latest_date}，滞后约 {age} 天，时效性下降"
    return 0.5, f"数据截至 {latest_date}，已显著滞后约 {age} 天，仅供趋势参考"


def _apply_freshness(dim: TearSheetDimension, latest_date: str) -> TearSheetDimension:
    """对依赖滞后历史源（github 月度等）的维度按数据新鲜度折减置信度并诚实标注。"""
    if dim.signal == "insufficient":
        return dim
    factor, note = _freshness(latest_date)
    if factor >= 1.0:
        return dim
    return dim.model_copy(update={
        "confidence": round(dim.confidence * factor, 2),
        "evidence": list(dim.evidence) + ([note] if note else []),
    })


def _dim_momentum(quote: Optional[MarketQuote]) -> TearSheetDimension:
    if not quote or quote.change_percent is None:
        return _insufficient("momentum", "价格动量", "缺少实时涨跌数据")
    chg = quote.change_percent
    if chg >= 2:
        signal, tone = "bullish", "偏强"
    elif chg <= -2:
        signal, tone = "bearish", "偏弱"
    else:
        signal, tone = "neutral", "震荡"
    score = max(-100, min(100, int(round(chg * 12))))
    evidence = [f"最新涨跌 {chg:+.2f}%"]
    if quote.high is not None and quote.low is not None and quote.high > quote.low:
        pos = (quote.price - quote.low) / (quote.high - quote.low) * 100
        evidence.append(f"日内位置 {pos:.0f}%（{quote.low:.2f}–{quote.high:.2f}）")
    if quote.volume:
        evidence.append(f"成交量 {quote.volume:,.0f}")
    return TearSheetDimension(
        key="momentum",
        label="价格动量",
        signal=signal,
        score=score,
        headline=f"{tone} · {chg:+.2f}%",
        evidence=evidence,
        confidence=0.7 if quote.is_realtime else 0.45,
        data_quality=classify_data_quality(quote.provider),
    )


def _dim_catalyst(
    events: list[EarningsCalendarEvent],
    nasdaq_eps: Optional[dict] = None,
    eastmoney_fin: Optional[dict] = None,
    symbol: str = "",
) -> TearSheetDimension:
    # 盈利质量优先：Nasdaq 近几季 beat/miss + 下季预测（美股 live，真实基本面方向信号）。
    if nasdaq_eps:
        hist = nasdaq_eps.get("history") or []
        judged = [(e["consensus"], e["earnings"]) for e in hist if e.get("consensus") and e.get("earnings")]
        if len(judged) >= 2:
            total = len(judged)
            beats = sum(1 for c, ea in judged if ea > c)
            rate = beats / total
            nxt = nasdaq_eps.get("next_consensus")
            evidence = [f"近{total}季 {beats}/{total} 超预期"]
            if nxt is not None:
                evidence.append(f"下季预测 EPS {nxt}")
            if rate >= 0.75:
                signal, tone = "bullish", f"盈利持续超预期 · {beats}/{total}"
            elif rate <= 0.25:
                signal, tone = "bearish", f"盈利屡不及预期 · {beats}/{total}"
            else:
                signal, tone = "neutral", f"盈利基本达预期 · {beats}/{total} 超"
            url = (
                f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/earnings"
                if symbol and symbol.isalpha()
                else None
            )
            return TearSheetDimension(
                key="catalyst",
                label="盈利质量 · 财报",
                signal=signal,
                score=max(-100, min(100, int(round((rate - 0.5) * 120)))),
                headline=tone,
                evidence=evidence,
                confidence=0.6,
                data_quality=classify_data_quality("nasdaq"),
                sources=[CitedSource(label="Nasdaq 盈利预测", provider="nasdaq", url=url, quality="live")],
            )

    # A股盈利质量：东财净利/营收同比（live）。净利高增→bullish、下滑→bearish。
    if eastmoney_fin and eastmoney_fin.get("profit_yoy") is not None:
        py = eastmoney_fin["profit_yoy"]
        ry = eastmoney_fin.get("revenue_yoy")
        roe = eastmoney_fin.get("roe")
        evidence = [f"净利同比 {py:+.1f}%"]
        if ry is not None:
            evidence.append(f"营收同比 {ry:+.1f}%")
        if roe is not None:
            evidence.append(f"ROE {roe:.1f}%")
        rd = eastmoney_fin.get("report_date")
        if rd:
            evidence.append(f"报告期 {rd}")
        if py >= 15:
            signal, tone = "bullish", f"净利高增 · {py:+.0f}%"
        elif py <= -5:
            signal, tone = "bearish", f"净利下滑 · {py:+.0f}%"
        else:
            signal, tone = "neutral", f"净利 {py:+.0f}% · 平稳"
        ecode = eastmoney_fin.get("code") or "".join(c for c in symbol if c.isdigit())
        if eastmoney_fin.get("is_hk"):
            url = f"https://quote.eastmoney.com/hk/{ecode}.html" if ecode else None
        elif ecode:
            url = f"https://quote.eastmoney.com/{'sh' if ecode.startswith('6') else 'sz'}{ecode}.html"
        else:
            url = None
        return TearSheetDimension(
            key="catalyst",
            label="盈利质量 · 财报",
            signal=signal,
            score=max(-100, min(100, int(round(py * 2)))),
            headline=tone,
            evidence=evidence,
            confidence=0.6,
            data_quality=classify_data_quality("eastmoney"),
            sources=[CitedSource(label="东方财富 财报", provider="eastmoney", url=url, quality="live")],
        )

    upcoming = [e for e in events if e.days_until_report is not None and e.days_until_report >= 0]
    upcoming.sort(key=lambda e: e.days_until_report if e.days_until_report is not None else 9999)
    if not upcoming:
        return _insufficient("catalyst", "盈利催化", "近端无确认财报安排")
    nxt = upcoming[0]
    days = nxt.days_until_report or 0
    base = f"{days} 天后财报" + (f"（{nxt.report_date}）" if nxt.report_date else "")
    evidence = [base]
    if nxt.eps_estimate is not None:
        evidence.append(f"EPS 预期 {nxt.eps_estimate}")
    if nxt.revenue_estimate is not None:
        evidence.append(f"营收预期 {nxt.revenue_estimate:,.0f}")
    conf = {"confirmed": 0.7, "estimated": 0.5, "pending_provider": 0.3}.get(nxt.confidence, 0.4)
    return TearSheetDimension(
        key="catalyst",
        label="盈利催化",
        signal="neutral",  # 催化是事件驱动的"关注项"，不直接定多空
        score=0,
        headline=("⚡ 临近：" + base) if days <= 14 else base,
        evidence=evidence,
        confidence=conf,
        data_quality=classify_data_quality(nxt.provider),
    )


def _dim_options(
    sig: Optional[OptionsSignal],
    nasdaq_opts: Optional[dict] = None,
    symbol: str = "",
) -> TearSheetDimension:
    # Put/Call 量比优先（nasdaq option-chain，美股 live，真实期权情绪）。越低越看涨。
    if nasdaq_opts and nasdaq_opts.get("pc_volume_ratio") is not None:
        pcv = nasdaq_opts["pc_volume_ratio"]
        pcoi = nasdaq_opts.get("pc_oi_ratio")
        if pcv <= 0.7:
            signal, tone = "bullish", "看涨主导"
        elif pcv >= 1.1:
            signal, tone = "bearish", "看跌主导"
        else:
            signal, tone = "neutral", "多空均衡"
        evidence = [f"Put/Call 量比 {pcv:.2f}"]
        if pcoi is not None:
            evidence.append(f"Put/Call 持仓比 {pcoi:.2f}")
        url = (
            f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/option-chain"
            if symbol and symbol.isalpha()
            else None
        )
        return TearSheetDimension(
            key="options",
            label="期权情绪 · Put/Call",
            signal=signal,
            score=max(-100, min(100, int(round((0.85 - pcv) * 110)))),
            headline=f"{tone} · P/C {pcv:.2f}",
            evidence=evidence,
            confidence=0.6,
            data_quality=classify_data_quality("nasdaq"),
            sources=[CitedSource(label="Nasdaq 期权链", provider="nasdaq", url=url, quality="live")],
        )

    direction = getattr(sig, "direction", None) if sig else None
    if not sig or not direction:
        return _insufficient("options", "期权情绪", "无期权链信号")
    signal, base_score = _DIR_MAP.get(direction, ("neutral", 0))
    if signal == "insufficient":
        return TearSheetDimension(
            key="options",
            label="期权情绪",
            signal="insufficient",
            headline="期权方向不可判定",
            confidence=0.2,
            data_quality=classify_data_quality(sig.provider),
        )
    conviction = getattr(sig, "conviction", "") or ""
    evidence = [f"方向 {direction}" + (f" · 信念 {conviction}" if conviction else "")]
    pcr = getattr(sig, "put_call_volume_ratio", None)
    if pcr is not None:
        evidence.append(f"P/C 量比 {pcr:.2f}")
    iv = getattr(sig, "avg_iv", None)
    if iv is not None:
        evidence.append(f"平均 IV {iv * 100:.1f}%")
    return TearSheetDimension(
        key="options",
        label="期权情绪",
        signal=signal,
        score=base_score,
        headline=f"{direction}" + (f" · 信念{conviction}" if conviction else ""),
        evidence=evidence,
        confidence=_CONVICTION_CONF.get(conviction, 0.4),
        data_quality=classify_data_quality(sig.provider),
    )


def _dim_scale(
    market_cap: Optional[float],
    quote: Optional[MarketQuote],
    cap_provider: Optional[str] = None,
) -> TearSheetDimension:
    if not market_cap or market_cap <= 0:
        return _insufficient("scale", "规模与流动性", "缺少市值数据")
    cap_yi = market_cap / 1e8
    if cap_yi >= 10000:
        tier = "超大盘"
    elif cap_yi >= 1000:
        tier = "大盘"
    elif cap_yi >= 100:
        tier = "中盘"
    else:
        tier = "小盘"
    evidence = [f"市值 {cap_yi:,.0f} 亿"]
    if quote and quote.volume:
        evidence.append(f"成交量 {quote.volume:,.0f}")
    # 市值来自 live 估值源（stockanalysis/东财）→ live；否则跟随 quote provider（google finance degraded）。
    provider = cap_provider or (quote.provider if quote else "none")
    srcs = []
    if cap_provider in ("stockanalysis", "eastmoney", "ifind"):
        label = {"stockanalysis": "Stockanalysis", "eastmoney": "东方财富", "ifind": "同花顺 iFinD"}[cap_provider]
        srcs = [CitedSource(label=f"{label} 市值", provider=cap_provider, quality="live")]
    return TearSheetDimension(
        key="scale",
        label="规模与流动性",
        signal="neutral",  # 规模提供 context，不直接定多空
        score=0,
        headline=f"{tier} · {cap_yi:,.0f} 亿",
        evidence=evidence,
        confidence=0.6,
        data_quality=classify_data_quality(provider),
        sources=srcs,
    )


def _dim_market_context(
    index_history: Optional[list],
    index_name: str = "标普500",
    source: Optional[CitedSource] = None,
    provider_tag: str = "github-sp500",
) -> TearSheetDimension:
    """市场环境维度：用真实大盘指数判定趋势。美股→标普500(github)、A股→沪深300、港股→恒生(东财)。"""
    if not index_history or len(index_history) < 2:
        return _insufficient("market", f"市场环境 · {index_name}", "缺少大盘指数数据")
    dates = [d for d, _ in index_history]
    closes = [c for _, c in index_history]
    latest = closes[-1]
    mom_1m = (latest - closes[-2]) / closes[-2] * 100 if closes[-2] else 0.0
    base3 = closes[-4] if len(closes) >= 4 else closes[0]
    mom_3m = (latest - base3) / base3 * 100 if base3 else 0.0
    peak = max(closes)
    off_peak = (latest - peak) / peak * 100 if peak else 0.0
    if mom_3m >= 3:
        signal, tone = "bullish", "强势"
    elif mom_3m <= -3:
        signal, tone = "bearish", "弱势"
    else:
        signal, tone = "neutral", "震荡"
    return _apply_freshness(TearSheetDimension(
        key="market",
        label=f"市场环境 · {index_name}",
        signal=signal,
        score=max(-100, min(100, int(round(mom_3m * 6)))),
        headline=f"大盘{tone} · 近3月 {mom_3m:+.1f}%",
        evidence=[
            f"{index_name} {latest:,.0f}（{dates[-1]}）",
            f"近1月 {mom_1m:+.1f}% · 近3月 {mom_3m:+.1f}%",
            f"距区间高点 {off_peak:+.1f}%",
        ],
        confidence=0.6,
        data_quality=classify_data_quality(provider_tag),
        sources=[source or _SP500_SRC],
    ), dates[-1])


def _dim_macro(rates_history: Optional[list]) -> TearSheetDimension:
    """宏观利率维度：用真实美10Y 国债收益率（github 数据源）判定估值顺风/逆风。"""
    if not rates_history or len(rates_history) < 2:
        return _insufficient("macro", "宏观利率 · 美10Y", "缺少国债收益率数据")
    dates = [d for d, _ in rates_history]
    rates = [r for _, r in rates_history]
    latest = rates[-1]
    base3 = rates[-4] if len(rates) >= 4 else rates[0]
    chg = latest - base3
    if latest >= 4.5 and chg > 0:
        signal, tone = "bearish", "高位收紧 · 估值逆风"
    elif latest <= 3.0 and chg < 0:
        signal, tone = "bullish", "低位宽松 · 估值顺风"
    else:
        signal, tone = "neutral", "中性"
    return _apply_freshness(TearSheetDimension(
        key="macro",
        label="宏观利率 · 美10Y",
        score=max(-100, min(100, int(round(-(latest - 4.0) * 25)))),
        signal=signal,
        headline=f"10Y {latest:.2f}% · {tone}",
        evidence=[
            f"美10Y国债 {latest:.2f}%（{dates[-1]}）",
            f"近3月 {chg:+.2f} pct",
        ],
        confidence=0.6,
        data_quality=classify_data_quality("github-us10y"),
        sources=[_US10Y_SRC],
    ), dates[-1])


def _build_narrative(name: str, verdict: str, dims: list[TearSheetDimension]) -> str:
    active = [d for d in dims if d.signal != "insufficient"]
    if not active:
        return f"{name}：可用证据不足，暂无法形成速判，请补充实时行情/财报/期权数据后再看。"
    parts = [f"{name} 综合速判：{verdict}。"]
    for d in active:
        parts.append(f"{d.label}——{d.headline}。")
    return " ".join(parts)


def _worst_quality_provider(*providers: Optional[str]) -> str:
    """维度内多源混合时取最差可信度对应的 provider（live>degraded>mock）。

    避免单一 live 字段把整卡虚标 live：估值维度若 PE 是 live 但驱动 signal 的 52周
    位置来自 google finance（degraded），整卡应诚实标 degraded。
    """
    rank = {"live": 2, "degraded": 1, "mock": 0}
    used = [p for p in providers if p]
    if not used:
        return "google-finance"
    return min(used, key=lambda p: rank.get(classify_data_quality(p).level, 1))


def _dim_valuation(
    stats: Optional[dict],
    val_data: Optional[dict] = None,
    quote: Optional[MarketQuote] = None,
) -> TearSheetDimension:
    """估值 / 区间位置维度：P/E + P/B 优先 live 估值源（stockanalysis/东财），52周位置优先 Yahoo 官方（live）。

    作 context（不在 _DIRECTIONAL_KEYS）：接近52周高提示回调风险、接近52周低提示价值区，配合 P/E
    给买方"贵不贵、在区间什么位置"的快读。dq 取实际驱动判定的数据源最差档——PE live 但 52周来自
    google finance（degraded）时整卡仍 degraded，不虚标。
    """
    stats = stats or {}
    val_data = val_data or {}
    # 52周位置：优先 quote 的 Yahoo 官方 52周（live），回退 stats 的 google finance 统计（degraded）。
    q_hi = getattr(quote, "wk52_high", None) if quote else None
    q_lo = getattr(quote, "wk52_low", None) if quote else None
    q_price = getattr(quote, "price", None) if quote else None
    if q_hi and q_lo and q_price:
        price, hi, lo = q_price, q_hi, q_lo
        pos_provider = getattr(quote, "provider", None) or "google-finance"
    else:
        price, hi, lo = stats.get("price"), stats.get("wk52_high"), stats.get("wk52_low")
        pos_provider = "google-finance"
    eps = stats.get("eps")
    # P/E 优先 live 估值源，回退 google finance 统计。
    pe = val_data.get("pe_ratio")
    pe_provider = val_data.get("provider") if pe is not None else None
    if pe is None:
        pe = stats.get("pe_ratio")
        pe_provider = "google-finance" if pe is not None else None
    pb = val_data.get("pb_ratio")
    fwd_pe = val_data.get("forward_pe")
    ps = val_data.get("ps_ratio")
    peg = val_data.get("peg")
    beta = val_data.get("beta")
    div_y = val_data.get("dividend_yield")

    parts: list[str] = []
    evidence: list[str] = []
    used_providers: list[str] = []
    signal = "neutral"
    score = 0
    if price and hi and lo and hi > lo:
        pos = (price - lo) / (hi - lo) * 100
        evidence.append(f"52周位置 {pos:.0f}%（{lo:.2f}–{hi:.2f}）")
        score = int(round((50 - pos) * 1.2))  # 低位正分(价值)、高位负分(回调风险)
        if pos >= 85:
            signal = "bearish"
            parts.append(f"接近52周高 · {pos:.0f}%")
        elif pos <= 15:
            signal = "bullish"
            parts.append(f"接近52周低 · {pos:.0f}%")
        else:
            parts.append(f"区间中部 · {pos:.0f}%")
        used_providers.append(pos_provider)  # 52周位置驱动 signal，其源计入 dq
    if pe:
        evidence.append(f"P/E {pe:.1f}")
        parts.append(f"P/E {pe:.1f}")
        if pe_provider:
            used_providers.append(pe_provider)
    if pb:
        evidence.append(f"P/B {pb:.2f}")
    if eps:
        evidence.append(f"EPS {eps:.2f}")
    if fwd_pe:
        evidence.append(f"预期 P/E {fwd_pe:.1f}")
    if ps:
        evidence.append(f"P/S {ps:.1f}")
    if peg and peg > 0:
        evidence.append(f"PEG {peg:.2f}")
    if div_y:
        evidence.append(f"股息率 {div_y:.2f}%")
    if beta:
        evidence.append(f"Beta {beta:.2f}")
    if not evidence:
        return _insufficient("valuation", "估值 / 区间位置", "估值数据不足")

    # dq 取实际参与判定的数据源最差档（诚实：单一 live 字段不能把整卡提到 live）。
    dq_provider = _worst_quality_provider(*used_providers)
    pos_is_live = bool(price and hi and lo and hi > lo) and classify_data_quality(pos_provider).level == "live"
    srcs: list[CitedSource] = []
    if pos_is_live:
        plabel = {"yahoo-finance": "Yahoo Finance"}.get(pos_provider, pos_provider)
        srcs.append(CitedSource(label=f"{plabel} 52周区间", provider=pos_provider, quality="live"))
    if pe and pe_provider in ("stockanalysis", "eastmoney", "ifind"):
        plabel = {"stockanalysis": "Stockanalysis", "eastmoney": "东方财富", "ifind": "同花顺 iFinD"}[pe_provider]
        srcs.append(CitedSource(label=f"{plabel} 估值", provider=pe_provider, quality="live"))
    if not srcs:
        srcs = [CitedSource(label="Google Finance 估值", provider="google-finance", quality="degraded")]
    return TearSheetDimension(
        key="valuation",
        label="估值 / 区间位置",
        signal=signal,
        score=max(-100, min(100, score)),
        headline=" · ".join(parts) if parts else "估值中性",
        evidence=evidence,
        confidence=0.5,
        data_quality=classify_data_quality(dq_provider),
        sources=srcs,
    )


def _dim_consensus(data: Optional[dict]) -> TearSheetDimension:
    """分析师一致预期：目标价 + 较现价空间 + 评级共识（美股 nasdaq+stockanalysis，live）。context 维度。"""
    data = data or {}
    tp = data.get("target_price")
    upside = data.get("upside_pct")
    rating = data.get("rating")
    n = data.get("analyst_count")
    if not tp and not rating:
        return _insufficient("consensus", "分析师一致预期", "暂无分析师覆盖（非美股或无数据）")
    evidence: list[str] = []
    parts: list[str] = []
    signal = "neutral"
    score = 0
    if tp:
        evidence.append(f"目标价 ${tp:,.2f}")
        if upside is not None:
            evidence.append(f"较现价 {upside:+.1f}%")
            score = max(-100, min(100, int(round(upside * 2))))
            if upside >= 10:
                signal = "bullish"
                parts.append(f"上行空间 {upside:+.1f}%")
            elif upside <= -10:
                signal = "bearish"
                parts.append(f"下行 {upside:+.1f}%")
            else:
                parts.append(f"目标价 {upside:+.1f}%")
    if rating:
        evidence.append(f"评级 {rating}" + (f"（{n} 家）" if n else ""))
        parts.append(str(rating))
        if signal == "neutral":
            if rating in ("Strong Buy", "Buy"):
                signal = "bullish"
            elif rating in ("Sell", "Strong Sell"):
                signal = "bearish"
    return TearSheetDimension(
        key="consensus",
        label="分析师一致预期",
        signal=signal,
        score=score,
        headline=" · ".join(parts) if parts else "一致预期",
        evidence=evidence,
        confidence=0.55,
        data_quality=classify_data_quality("nasdaq"),
        sources=[CitedSource(label="Nasdaq 评级 + Stockanalysis 目标价", provider="nasdaq", quality="live")],
    )


def _dim_fund_flow(data: Optional[dict]) -> TearSheetDimension:
    """资金面 · 主力（A股 live）：主力近5日净流入(push2his) + 近期龙虎榜净额(东财)。context 维度。"""
    data = data or {}
    flow5 = data.get("main_flow_5d")
    lhb = data.get("lhb_net")
    if flow5 is None and lhb is None:
        return _insufficient("fund_flow", "资金面 · 主力", "非A股或无近期资金流数据")
    evidence: list[str] = []
    parts: list[str] = []
    signal = "neutral"
    score = 0
    if flow5 is not None:
        yi = flow5 / 1e8
        days = data.get("flow_days", 5)
        evidence.append(f"主力近{days}日净{'流入' if yi >= 0 else '流出'} {abs(yi):.2f} 亿")
        score = max(-100, min(100, int(round(yi * 8))))
        if yi >= 1:
            signal = "bullish"
            parts.append(f"主力净流入 {yi:.1f}亿")
        elif yi <= -1:
            signal = "bearish"
            parts.append(f"主力净流出 {abs(yi):.1f}亿")
        else:
            parts.append(f"主力 {yi:+.1f}亿")
    if lhb is not None:
        lyi = lhb / 1e8
        evidence.append(f"龙虎榜净额 {lyi:+.2f} 亿（{str(data.get('lhb_date', ''))[:10]}）")
        if not parts:
            parts.append(f"龙虎榜 {lyi:+.1f}亿")
    return TearSheetDimension(
        key="fund_flow",
        label="资金面 · 主力",
        signal=signal,
        score=score,
        headline=" · ".join(parts) if parts else "资金面",
        evidence=evidence,
        confidence=0.5,
        data_quality=classify_data_quality("eastmoney"),
        sources=[CitedSource(label="东方财富 资金流 / 龙虎榜", provider="eastmoney", quality="live")],
    )


def build_tear_sheet(
    *,
    symbol: str,
    name: str = "",
    market_cap: Optional[float] = None,
    currency: str = "USD",
    quote: Optional[MarketQuote] = None,
    earnings_events: Optional[list[EarningsCalendarEvent]] = None,
    options_signal: Optional[OptionsSignal] = None,
    market_index_history: Optional[list] = None,
    rates_history: Optional[list] = None,
    constituent: Optional[dict] = None,
    valuation: Optional[dict] = None,
    valuation_data: Optional[dict] = None,
    consensus_data: Optional[dict] = None,
    fund_flow_data: Optional[dict] = None,
    price_history: Optional[list] = None,
    nasdaq_eps: Optional[dict] = None,
    nasdaq_opts: Optional[dict] = None,
    eastmoney_fin: Optional[dict] = None,
    market_index_name: str = "标普500",
    market_source: Optional[CitedSource] = None,
    market_provider_tag: str = "github-sp500",
    narrative: str = "",
    narrative_provider: str = "",
) -> TearSheetResponse:
    """从已获取的多源数据组装速判卡。纯函数，便于单测。"""
    dims = [
        _dim_momentum(quote),
        _dim_catalyst(earnings_events or [], nasdaq_eps=nasdaq_eps, eastmoney_fin=eastmoney_fin, symbol=symbol),
        _dim_options(options_signal, nasdaq_opts=nasdaq_opts, symbol=symbol),
        _dim_scale(market_cap, quote, (valuation_data or {}).get("provider")),
        _dim_valuation(valuation, valuation_data, quote),
        _dim_consensus(consensus_data),
        _dim_fund_flow(fund_flow_data),
        _dim_market_context(market_index_history, market_index_name, market_source, market_provider_tag),
        _dim_macro(rates_history),
    ]

    directional = [d for d in dims if d.key in _DIRECTIONAL_KEYS and d.signal != "insufficient"]
    informative = [d for d in dims if d.signal != "insufficient"]
    if directional:
        weight = sum(d.confidence for d in directional) or 1.0
        overall_score = int(round(sum(d.score * d.confidence for d in directional) / weight))
        if overall_score >= 25:
            verdict = "重点跟踪"
        elif overall_score <= -25:
            verdict = "谨慎回避"
        else:
            verdict = "中性观察"
    else:
        overall_score = 0
        verdict = "中性观察" if informative else "数据不足"
    confidence = (
        round((len(informative) / len(dims)) * (sum(d.confidence for d in informative) / len(informative)), 2)
        if informative
        else 0.0
    )

    final_narrative = narrative or _build_narrative(name or symbol, verdict, dims)
    if constituent and constituent.get("sector"):
        final_narrative = f"【标普500 成分 · {constituent['sector']}】 " + final_narrative

    return TearSheetResponse(
        symbol=symbol,
        name=name,
        generated_at=datetime.now(timezone.utc),
        price=quote.price if quote else None,
        change_percent=quote.change_percent if quote else None,
        currency=currency,
        overall_verdict=verdict,
        overall_score=overall_score,
        confidence=confidence,
        narrative=final_narrative,
        narrative_provider=narrative_provider or "rule-template",
        dimensions=dims,
        price_series=[{"date": d, "value": v} for d, v in (price_history or [])],
        sp500_series=[{"date": d, "value": v} for d, v in (market_index_history or [])],
        us10y_series=[{"date": d, "value": v} for d, v in (rates_history or [])],
        data_quality=_worst_quality([d.data_quality for d in dims if d.signal != "insufficient"] or [classify_data_quality("none")]),
    )


# ---------------------------------------------------------------------------
# 组合层证据速判（买方视角）：从本地持仓 + 风控摘要判定组合风险。
# ---------------------------------------------------------------------------


def _pf_insufficient(key: str, label: str, headline: str) -> TearSheetDimension:
    return TearSheetDimension(
        key=key, label=label, signal="insufficient", headline=headline,
        confidence=0.0, data_quality=classify_data_quality("none"),
    )


def _dim_concentration(positions: list) -> TearSheetDimension:
    if not positions:
        return _pf_insufficient("concentration", "单一持仓集中度", "无持仓")
    sizes = sorted((float(p.get("position_size_pct") or 0) for p in positions), reverse=True)
    mx = sizes[0] if sizes else 0.0
    top3 = sum(sizes[:3])
    if mx >= 25:
        signal, hl = "bearish", f"最大持仓 {mx:.0f}% · 偏集中"
    elif mx >= 15:
        signal, hl = "neutral", f"最大持仓 {mx:.0f}%"
    else:
        signal, hl = "bullish", f"最大持仓 {mx:.0f}% · 分散良好"
    return TearSheetDimension(
        key="concentration", label="单一持仓集中度", signal=signal,
        score=max(-100, min(100, int(round((20 - mx) * 4)))),
        headline=hl,
        evidence=[f"最大单一持仓 {mx:.0f}%", f"前三持仓合计 {top3:.0f}%", f"共 {len(positions)} 笔持仓"],
        confidence=0.8, data_quality=classify_data_quality("portfolio"),
    )


def _dim_sector(portfolio: dict) -> TearSheetDimension:
    exposure = portfolio.get("sector_exposure") or {}
    if not exposure:
        return _pf_insufficient("sector", "行业敞口", "缺少行业分布数据")
    top_sector, top_pct = max(exposure.items(), key=lambda kv: kv[1])
    top_pct = float(top_pct)
    if top_pct >= 40:
        signal, hl = "bearish", f"{top_sector} {top_pct:.0f}% · 行业过度集中"
    elif top_pct >= 25:
        signal, hl = "neutral", f"{top_sector} {top_pct:.0f}%"
    else:
        signal, hl = "bullish", f"{top_sector} {top_pct:.0f}% · 行业分散"
    ev = [f"{s} {float(p):.0f}%" for s, p in sorted(exposure.items(), key=lambda kv: kv[1], reverse=True)[:4]]
    return TearSheetDimension(
        key="sector", label="行业敞口", signal=signal,
        score=max(-100, min(100, int(round((30 - top_pct) * 3)))),
        headline=hl, evidence=ev, confidence=0.75,
        data_quality=classify_data_quality("portfolio"),
    )


def _dim_pnl(portfolio: dict) -> TearSheetDimension:
    pnl = portfolio.get("total_pnl_pct")
    if pnl is None:
        return _pf_insufficient("pnl", "组合盈亏 / 回撤", "缺少盈亏数据")
    pnl = float(pnl)
    if pnl <= -15:
        signal, hl = "bearish", f"组合回撤 {pnl:.1f}% · 触及风控阈值"
    elif pnl < 0:
        signal, hl = "neutral", f"组合浮亏 {pnl:.1f}%"
    else:
        signal, hl = "bullish", f"组合浮盈 +{pnl:.1f}%"
    return TearSheetDimension(
        key="pnl", label="组合盈亏 / 回撤", signal=signal,
        score=max(-100, min(100, int(round(pnl * 4)))),
        headline=hl, evidence=[f"组合累计盈亏 {pnl:+.1f}%"],
        confidence=0.7, data_quality=classify_data_quality("portfolio"),
    )


def _dim_stop_discipline(positions: list) -> TearSheetDimension:
    if not positions:
        return _pf_insufficient("stop_discipline", "止损纪律", "无持仓")
    with_stop = sum(1 for p in positions if p.get("stop_loss"))
    pct = with_stop / len(positions) * 100
    if pct >= 80:
        signal, hl = "bullish", f"{pct:.0f}% 持仓设有止损 · 纪律良好"
    elif pct >= 50:
        signal, hl = "neutral", f"{pct:.0f}% 持仓设有止损"
    else:
        signal, hl = "bearish", f"仅 {pct:.0f}% 持仓设有止损 · 保护不足"
    return TearSheetDimension(
        key="stop_discipline", label="止损纪律", signal=signal,
        score=max(-100, min(100, int(round((pct - 50) * 2)))),
        headline=hl, evidence=[f"{with_stop}/{len(positions)} 笔持仓设有止损位"],
        confidence=0.8, data_quality=classify_data_quality("portfolio"),
    )


def _pf_narrative(verdict: str, dims: list, positions: list) -> str:
    parts = [f"组合风险速判：{verdict}（{len(positions)} 笔持仓）。"]
    for d in dims:
        if d.signal != "insufficient":
            parts.append(f"{d.label}——{d.headline}。")
    return " ".join(parts)


def build_portfolio_review(
    summary: dict,
    *,
    sp500_history: Optional[list] = None,
    rates_history: Optional[list] = None,
) -> PortfolioReviewResponse:
    """从本地持仓风险摘要 + 真实宏观背景构建组合速判。verdict 仅由持仓风控决定，
    market/利率维度作背景展示（买方看组合所处的市场环境）。纯函数，可单测。"""
    portfolio = summary.get("portfolio") or {}
    positions = summary.get("open_positions") or []
    raw_alerts = summary.get("alerts") or []

    macro_dims = [_dim_market_context(sp500_history), _dim_macro(rates_history)]
    series_kwargs = {
        "sp500_series": [{"date": d, "value": v} for d, v in (sp500_history or [])],
        "us10y_series": [{"date": d, "value": v} for d, v in (rates_history or [])],
    }

    if not positions:
        risk_dims = [
            _pf_insufficient("concentration", "单一持仓集中度", "无持仓"),
            _pf_insufficient("sector", "行业敞口", "无持仓"),
            _pf_insufficient("pnl", "组合盈亏 / 回撤", "无持仓"),
            _pf_insufficient("stop_discipline", "止损纪律", "无持仓"),
        ]
        dims = risk_dims + macro_dims
        return PortfolioReviewResponse(
            generated_at=datetime.now(timezone.utc),
            position_count=0,
            overall_verdict="空仓",
            risk_score=0,
            confidence=0.0,
            narrative="当前没有未平仓持仓。录入持仓后可查看集中度、行业敞口、回撤与止损纪律；下方仍展示真实市场与利率背景。",
            dimensions=dims,
            alerts=[],
            data_quality=_worst_quality([d.data_quality for d in dims if d.signal != "insufficient"] or [classify_data_quality("none")]),
            **series_kwargs,
        )

    risk_dims = [
        _dim_concentration(positions),
        _dim_sector(portfolio),
        _dim_pnl(portfolio),
        _dim_stop_discipline(positions),
    ]
    dims = risk_dims + macro_dims
    risk_informative = [d for d in risk_dims if d.signal != "insufficient"]

    bear = sum(1 for d in risk_informative if d.signal == "bearish")
    bull = sum(1 for d in risk_informative if d.signal == "bullish")
    danger = [a for a in raw_alerts if a.get("level") == "danger"]
    warn = [a for a in raw_alerts if a.get("level") == "warning"]
    risk_score = max(0, min(100, 60 + bull * 12 - bear * 18 - len(danger) * 15 - len(warn) * 6))
    if danger or bear >= 2 or risk_score < 35:
        verdict = "高风险"
    elif warn or bear >= 1 or risk_score < 60:
        verdict = "需关注"
    else:
        verdict = "稳健"

    return PortfolioReviewResponse(
        generated_at=datetime.now(timezone.utc),
        position_count=len(positions),
        total_value=portfolio.get("total_value"),
        total_pnl_pct=portfolio.get("total_pnl_pct"),
        overall_verdict=verdict,
        risk_score=risk_score,
        confidence=round(len(risk_informative) / len(risk_dims), 2),
        narrative=_pf_narrative(verdict, dims, positions),
        dimensions=dims,
        alerts=[a.get("message", "") for a in raw_alerts if a.get("message")],
        data_quality=_worst_quality([d.data_quality for d in dims if d.signal != "insufficient"] or [classify_data_quality("none")]),
        **series_kwargs,
    )


# ---------------------------------------------------------------------------
# 宏观环境速判：市场/利率/通胀/避险，用真实公开数据判定风险偏好。
# 维度信号语义统一为"对风险资产"：bullish=risk-on，bearish=risk-off。
# ---------------------------------------------------------------------------


def _dim_inflation(oil_history: Optional[list]) -> TearSheetDimension:
    if not oil_history or len(oil_history) < 2:
        return _insufficient("inflation", "通胀 · WTI原油", "缺少油价数据")
    prices = [p for _, p in oil_history]
    latest = prices[-1]
    base = prices[0]
    chg = (latest - base) / base * 100 if base else 0.0
    if chg >= 8:
        signal, tone = "bearish", "油价大涨 · 通胀压力"
    elif chg <= -8:
        signal, tone = "bullish", "油价回落 · 通胀缓解"
    else:
        signal, tone = "neutral", "油价平稳"
    return _apply_freshness(TearSheetDimension(
        key="inflation",
        label="通胀 · WTI原油",
        signal=signal,
        score=max(-100, min(100, int(round(-chg * 4)))),
        headline=f"WTI ${latest:.1f} · {tone}",
        evidence=[f"WTI 原油 ${latest:.1f}（{oil_history[-1][0]}）", f"近期 {chg:+.1f}%"],
        confidence=0.55,
        data_quality=classify_data_quality("github-oil"),
        sources=[_OIL_SRC],
    ), oil_history[-1][0])


def _dim_safe_haven(gold_history: Optional[list]) -> TearSheetDimension:
    if not gold_history or len(gold_history) < 2:
        return _insufficient("safe_haven", "避险 · 黄金", "缺少黄金数据")
    prices = [p for _, p in gold_history]
    latest = prices[-1]
    base3 = prices[-4] if len(prices) >= 4 else prices[0]
    chg = (latest - base3) / base3 * 100 if base3 else 0.0
    if chg >= 5:
        signal, tone = "bearish", "黄金走强 · 避险升温"
    elif chg <= -5:
        signal, tone = "bullish", "黄金回落 · 风险偏好"
    else:
        signal, tone = "neutral", "黄金平稳"
    return _apply_freshness(TearSheetDimension(
        key="safe_haven",
        label="避险 · 黄金",
        signal=signal,
        score=max(-100, min(100, int(round(-chg * 4)))),
        headline=f"黄金 ${latest:.0f} · {tone}",
        evidence=[f"黄金 ${latest:.0f}（{gold_history[-1][0]}）", f"近3月 {chg:+.1f}%"],
        confidence=0.55,
        data_quality=classify_data_quality("github-gold"),
        sources=[_GOLD_SRC],
    ), gold_history[-1][0])


# 宏观风险前瞻三件套（波动率/收益率曲线/信用利差）：复用市场看板的 live 读数与已校准阈值，
# 转成速判维度。信号语义与既有宏观维度统一为「对风险资产」：bullish=risk-on，bearish=risk-off。
# 看板信号是 strong_bearish/bearish/bullish/neutral，这里收敛到速判的四值并给方向分。
_DASH_SIGNAL_MAP = {"strong_bearish": "bearish", "bearish": "bearish", "strong_bullish": "bullish", "bullish": "bullish", "neutral": "neutral"}
_DASH_SIGNAL_SCORE = {"strong_bearish": -65, "bearish": -35, "strong_bullish": 65, "bullish": 35, "neutral": 0}
# key →（维度标签, 可溯源出处, 置信度）。信用利差与 VIX 是更强的前瞻压力信号，置信度略高于曲线。
_RISK_DIM_META: dict[str, tuple[str, CitedSource, float]] = {
    "vix": ("波动率 · VIX 恐慌指数", _VIX_SRC, 0.65),
    "yield_curve": ("收益率曲线 · 2s10s 利差", _CURVE_SRC, 0.6),
    "credit_spread": ("信用利差 · 高收益债 OAS", _CREDIT_SRC, 0.65),
    "dxy": ("美元 · DXY 指数", _DXY_SRC, 0.55),
    "cpi": ("通胀 · CPI 同比", _CPI_SRC, 0.6),
    "unemployment": ("就业 · 失业率", _UNEMP_SRC, 0.6),
    "real_rate": ("实际利率 · 10Y TIPS", _REALRATE_SRC, 0.68),  # 估值之锚，置信度最高
    "breakeven": ("通胀预期 · 10Y Breakeven", _BREAKEVEN_SRC, 0.65),
}
# 中国宏观轴维度元数据（信号同样复用 A股看板的「对A股」阈值）。
_CHINA_DIM_META: dict[str, tuple[str, CitedSource, float]] = {
    "csi300": ("中国 · 沪深300", _CSI300_SRC, 0.6),
    "northbound_flow": ("中国 · 北向资金", _NORTHBOUND_SRC, 0.6),
    "usd_cny": ("中国 · 人民币汇率", _CNY_SRC, 0.55),
    "china_bond_10y": ("中国 · 10年期国债", _CN10Y_SRC, 0.6),
}


def _indicator_unavailable(ind: dict) -> bool:
    """看板指标是否不可用：value 缺失/为 0 哨兵，或 status 以「数据」开头。单一判定，供维度与 regime 共用。"""
    value = ind.get("value")
    status = ind.get("status", "") or ""
    return value is None or status.startswith("数据")


def _live_indicator_value(indicator: Optional[dict]) -> Optional[float]:
    """仅当看板指标确为 live 读数时返回其数值，否则 None（避免把"不可用"当 0 读数喂进 regime）。"""
    ind = indicator or {}
    return None if _indicator_unavailable(ind) else ind.get("value")


def _dim_from_dashboard(
    key: str,
    indicator: Optional[dict],
    meta: dict[str, tuple[str, CitedSource, float]] = _RISK_DIM_META,
) -> TearSheetDimension:
    """把市场看板的一个 live 指标转成宏观速判维度（美国风险件套或中国宏观轴共用）。

    信号与解读直接复用看板已校准的阈值（单一事实源，避免在两处各写一套门槛）；
    缺读数（源不可达，value 为空或状态以「数据」开头）则诚实标 insufficient。
    """
    label, source, conf = meta[key]
    ind = indicator or {}
    if not ind or _indicator_unavailable(ind):
        return _insufficient(key, label, "缺少实时读数")
    value = ind.get("value")
    status = ind.get("status", "") or ""
    raw_signal = ind.get("signal", "neutral")
    signal = _DASH_SIGNAL_MAP.get(raw_signal, "neutral")
    score = _DASH_SIGNAL_SCORE.get(raw_signal, 0)
    unit = ind.get("unit", "") or ""
    reading = f"{value:g}{unit}"
    tone = status.split("—")[0].strip() or status  # interpret() 形如「中度恐慌 — …」，取前半作精炼标题
    evidence = [f"{ind.get('name', label)} {reading}（{ind.get('source', '')}）", status]
    change_pct = ind.get("change_pct")
    if change_pct is not None:
        evidence.append(f"日内 {change_pct:+.1f}%")
    return TearSheetDimension(
        key=key,
        label=label,
        signal=signal,
        score=max(-100, min(100, score)),
        headline=f"{reading} · {tone}",
        evidence=evidence,
        confidence=conf,
        data_quality=classify_data_quality("market-dashboard-live"),
        sources=[source],
    )


# ---------------------------------------------------------------------------
# 投资时钟体制：增长轴 × 通胀轴 → 具名宏观体制 + 买方资产配置手册。
# 增长轴用顺周期/金融条件信号（股票动量/收益率曲线/信用利差/波动率/失业率），
# 通胀轴用价格信号（油价动量 + CPI 水平）。两轴均为确定性规则，缺信号诚实降级。
# ---------------------------------------------------------------------------
_GROWTH_KEYS = ("market", "yield_curve", "credit_spread", "vix", "unemployment")
_REGIME_THRESHOLD = 12  # 轴分超过 ±12 才判定方向，避免噪声

# (增长方向, 通胀方向) → (体制名, 操作主张, 超配, 低配)。四角=经典象限，边/心=过渡态。
_REGIME_TABLE: dict[tuple[str, str], tuple[str, str, list[str], list[str]]] = {
    ("扩张", "回落"): ("复苏 · Goldilocks", "增长回升 + 通胀回落，金发姑娘环境，风险资产最甜蜜区，超配成长。",
                       ["成长股", "科技", "非必需消费", "信用债"], ["现金", "防御板块", "能源"]),
    ("扩张", "中性"): ("温和扩张", "增长占优、通胀温和，趋势可延续，顺周期为主。",
                       ["股票(顺周期)", "工业", "金融"], ["长久期国债", "现金"]),
    ("扩张", "升温"): ("过热 · Reflation", "增长与通胀同升，再通胀交易，超配实物资产与价值。",
                       ["周期股", "大宗商品", "能源", "价值股", "通胀挂钩债"], ["长久期债券", "高估值成长"]),
    ("中性", "回落"): ("反通胀软着陆", "通胀回落、增长企稳，利好久期与优质成长，降息预期发酵。",
                       ["优质成长", "久期债券", "REITs"], ["能源", "大宗商品"]),
    ("中性", "中性"): ("均衡 · 方向未明", "两轴均不明朗，方向选择期，均衡配置、控制杠杆、等待确认。",
                       ["均衡配置", "质量因子", "现金缓冲"], ["单边重仓", "高杠杆"]),
    ("中性", "升温"): ("滞胀前兆", "通胀抬头而增长未起，警惕滞胀，向抗通胀 + 防御倾斜。",
                       ["能源", "黄金", "抗通胀债", "必需消费"], ["长久期债", "高估值成长"]),
    ("放缓", "回落"): ("衰退 · Deflation", "增长与通胀齐落，衰退/通缩象限，避险与久期为王。",
                       ["长久期国债", "必需消费", "公用事业", "黄金"], ["周期股", "大宗商品", "高收益债", "小盘"]),
    ("放缓", "中性"): ("增长放缓", "增长走弱、通胀中性，降低 beta、提升质量与防御。",
                       ["防御板块", "质量股", "投资级债"], ["周期股", "高收益债"]),
    ("放缓", "升温"): ("滞胀 · Stagflation", "增长下行 + 通胀上行，最难象限，现金/黄金/能源避险，回避成长与长债。",
                       ["现金", "黄金", "能源", "抗通胀债"], ["成长股", "长久期债券", "非必需消费"]),
}


def _axis_dir(score: Optional[int], up: str, down: str) -> str:
    if score is None or abs(score) < _REGIME_THRESHOLD:
        return "中性"
    return up if score >= _REGIME_THRESHOLD else down


def _wavg(pairs: list[tuple[float, float]]) -> Optional[int]:
    """置信度加权平均，无样本返回 None。"""
    if not pairs:
        return None
    weight = sum(w for _, w in pairs) or 1.0
    return max(-100, min(100, int(round(sum(s * w for s, w in pairs) / weight))))


def build_macro_regime(
    dims: list[TearSheetDimension],
    *,
    cpi_value: Optional[float] = None,
    breakeven_value: Optional[float] = None,
) -> MacroRegime:
    """从已判定的维度合成投资时钟体制（增长×通胀）。纯函数，可单测。

    增长轴 = 顺周期信号(股票/曲线/信用/波动率/就业)的置信度加权分（risk-on 正向 = 增长向上）。
    通胀轴 = **通胀预期 breakeven(前瞻、市场隐含，权重最高)** + 油价动量 + CPI 水平。
    breakeven 是机构判定通胀方向的首选——前瞻性远胜已实现的 CPI/油价。
    任一轴无信号 → 该轴中性；两轴皆空 → 数据不足。
    """
    by_key = {d.key: d for d in dims if d.signal != "insufficient"}

    growth_pairs = [(by_key[k].score, by_key[k].confidence) for k in _GROWTH_KEYS if k in by_key]
    growth_evi = [f"{by_key[k].label}：{by_key[k].headline}" for k in _GROWTH_KEYS if k in by_key]

    infl_pairs: list[tuple[float, float]] = []
    infl_evi: list[str] = []
    if breakeven_value is not None:  # 通胀预期：相对 2.2% 中枢，前瞻信号给最高权重 0.75
        be_score = max(-100, min(100, int(round((breakeven_value - 2.2) * 45))))
        infl_pairs.append((be_score, 0.75))
        infl_evi.append(f"通胀预期 · 10Y Breakeven：{breakeven_value:.2f}%（vs 2.2% 中枢，前瞻）")
    oil = by_key.get("inflation")
    if oil is not None:  # 油价：风险分为负代表油涨→通胀升，故取相反数
        infl_pairs.append((-oil.score, oil.confidence))
        infl_evi.append(f"{oil.label}：{oil.headline}")
    if cpi_value is not None:  # CPI 水平：相对 2.5% 中枢，越高通胀越强
        cpi_score = max(-100, min(100, int(round((cpi_value - 2.5) * 18))))
        infl_pairs.append((cpi_score, 0.6))
        infl_evi.append(f"通胀 · CPI 同比：{cpi_value:.1f}%（vs 2.5% 中枢）")

    growth_score = _wavg(growth_pairs)
    inflation_score = _wavg(infl_pairs)

    if growth_score is None or inflation_score is None:
        return MacroRegime(
            name="数据不足",
            growth_score=growth_score or 0,
            inflation_score=inflation_score or 0,
            playbook="增长或通胀轴可用信号不足，暂无法定位投资时钟象限。",
            confidence=0.0,
            evidence=growth_evi + infl_evi,
        )

    g_dir = _axis_dir(growth_score, "扩张", "放缓")
    i_dir = _axis_dir(inflation_score, "升温", "回落")
    name, playbook, favored, avoided = _REGIME_TABLE[(g_dir, i_dir)]

    # 置信度：信号覆盖度 × 平均维度置信度（增长 5 + 通胀 2 = 满分 7 个信号位）。
    all_pairs = growth_pairs + infl_pairs
    coverage = min(1.0, len(all_pairs) / 7)
    avg_conf = sum(w for _, w in all_pairs) / len(all_pairs)
    confidence = round(coverage * avg_conf, 2)

    return MacroRegime(
        name=name,
        growth_axis=g_dir,
        inflation_axis=i_dir,
        growth_score=growth_score,
        inflation_score=inflation_score,
        playbook=playbook,
        favored=favored,
        avoided=avoided,
        confidence=confidence,
        evidence=growth_evi + infl_evi,
    )


def build_china_macro(china_indicators: Optional[dict]) -> tuple[list[TearSheetDimension], str]:
    """中国宏观轴：沪深300/北向资金/人民币/中国10Y → 维度卡 + 一句话本土环境读。纯函数，可单测。

    与美国 verdict/投资时钟**独立**（不同周期，不混判），复用 A股看板已校准的「对A股」信号。
    """
    ci = china_indicators or {}
    dims = [
        _dim_from_dashboard(k, ci.get(k), meta=_CHINA_DIM_META)
        for k in ("csi300", "northbound_flow", "usd_cny", "china_bond_10y")
    ]
    informative = [d for d in dims if d.signal != "insufficient"]
    if not informative:
        return dims, "中国宏观：实时数据暂不可用（东财/新浪源未达），稍后重试。"
    bull = sum(1 for d in informative if d.signal == "bullish")
    bear = sum(1 for d in informative if d.signal == "bearish")
    tilt = "偏暖（利好A股）" if bull > bear else ("偏冷（A股承压）" if bear > bull else "中性")
    drivers = "；".join(d.headline for d in informative)
    return dims, f"中国宏观{tilt}：{drivers}。"


def build_macro_review(
    *,
    sp500_history: Optional[list] = None,
    rates_history: Optional[list] = None,
    oil_history: Optional[list] = None,
    gold_history: Optional[list] = None,
    risk_indicators: Optional[dict] = None,
    china_indicators: Optional[dict] = None,
) -> MacroReviewResponse:
    """宏观环境速判：多维度加权出风险偏好 verdict。纯函数，可单测。

    维度 = github 月度（市场/利率/通胀/避险）+ 市场看板 live（波动率/曲线/信用利差/美元/CPI/失业率）。
    在 risk-on/off verdict 之外，再合成投资时钟体制（增长×通胀 → 具名象限 + 配置手册）。
    risk_indicators 为 None 时看板维度自动标 insufficient，不污染既有判定（向后兼容）。
    """
    risk = risk_indicators or {}
    dims = [
        _dim_market_context(sp500_history),
        _dim_from_dashboard("vix", risk.get("vix")),
        _dim_from_dashboard("unemployment", risk.get("unemployment")),
        _dim_macro(rates_history),
        _dim_from_dashboard("real_rate", risk.get("real_rate")),
        _dim_from_dashboard("yield_curve", risk.get("yield_curve")),
        _dim_from_dashboard("credit_spread", risk.get("credit_spread")),
        _dim_from_dashboard("dxy", risk.get("dxy")),
        _dim_inflation(oil_history),
        _dim_from_dashboard("breakeven", risk.get("breakeven")),
        _dim_from_dashboard("cpi", risk.get("cpi")),
        _dim_safe_haven(gold_history),
    ]
    informative = [d for d in dims if d.signal != "insufficient"]
    if informative:
        weight = sum(d.confidence for d in informative) or 1.0
        overall_score = int(round(sum(d.score * d.confidence for d in informative) / weight))
        if overall_score >= 20:
            verdict = "风险偏好"
        elif overall_score <= -20:
            verdict = "避险"
        else:
            verdict = "中性"
        confidence = round(len(informative) / len(dims) * (sum(d.confidence for d in informative) / len(informative)), 2)
    else:
        overall_score, verdict, confidence = 0, "数据不足", 0.0

    # 看板用 value=0/status「数据…」表示"该指标不可用"，不能当真实读数喂进 regime
    # （否则 CPI/breakeven 缺失会变成 0% 的假通缩信号）。
    cpi_value = _live_indicator_value(risk.get("cpi"))
    breakeven_value = _live_indicator_value(risk.get("breakeven"))
    regime = build_macro_regime(dims, cpi_value=cpi_value, breakeven_value=breakeven_value)
    china_dims, china_read = build_china_macro(china_indicators)

    parts = [f"宏观环境：{verdict}。"]
    if regime.name != "数据不足":
        parts.append(f"投资时钟：{regime.name}（增长{regime.growth_axis}·通胀{regime.inflation_axis}）——{regime.playbook}")
    for d in informative:
        parts.append(f"{d.label}——{d.headline}。")

    return MacroReviewResponse(
        generated_at=datetime.now(timezone.utc),
        overall_verdict=verdict,
        overall_score=overall_score,
        confidence=confidence,
        regime=regime,
        narrative=" ".join(parts),
        dimensions=dims,
        china_dimensions=china_dims,
        china_read=china_read,
        sp500_series=[{"date": d, "value": v} for d, v in (sp500_history or [])],
        us10y_series=[{"date": d, "value": v} for d, v in (rates_history or [])],
        data_quality=_worst_quality([d.data_quality for d in dims if d.signal != "insufficient"] or [classify_data_quality("none")]),
    )


def build_briefing(macro: MacroReviewResponse, portfolio: PortfolioReviewResponse) -> BriefingResponse:
    """投研晨报：把宏观环境速判 + 组合风险速判聚合成买方晨会一句话 + 一页纸。纯函数，可单测。

    headline 由两个 verdict 的组合确定性生成（市场环境 × 组合风险 → 行动建议），不依赖 LLM。
    """
    mv = macro.overall_verdict
    pv = portfolio.overall_verdict

    macro_clause = {
        "风险偏好": "市场处于风险偏好（risk-on）",
        "中性": "市场情绪中性",
        "避险": "市场转向避险（risk-off）",
        "数据不足": "宏观数据暂不充分",
    }.get(mv, "宏观数据暂不充分")

    if pv == "空仓":
        port_clause = "组合当前空仓"
        if mv == "风险偏好":
            advice = "可关注顺周期方向的建仓机会，建仓时控制单一持仓集中度与止损纪律。"
        elif mv == "避险":
            advice = "适合保持观望或低仓位，等待市场企稳信号再行动。"
        else:
            advice = "可逐步研究候选标的，等待更明确的方向信号。"
    else:
        port_clause = f"组合风险评级「{pv}」"
        if pv == "高风险":
            tail = "市场同步避险，更需收紧防守。" if mv == "避险" else "可结合宏观判断逐步对冲。"
            advice = "优先降低敞口、检查止损与集中度——" + tail
        elif pv == "需关注":
            advice = "留意已亮黄灯的维度，适度对冲或减仓，避免风险累积。"
        else:  # 稳健
            advice = (
                "组合稳健但市场转避险，注意防守与回撤控制。"
                if mv == "避险"
                else "维持纪律，关注宏观变化对持仓的边际影响。"
            )

    headline = f"{macro_clause}，{port_clause}。{advice}"

    return BriefingResponse(
        generated_at=datetime.now(timezone.utc),
        headline=headline,
        macro_verdict=mv,
        portfolio_verdict=pv,
        macro=macro,
        portfolio=portfolio,
        data_quality=_worst_quality([macro.data_quality, portfolio.data_quality]),
    )


# GICS 行业的顺周期 / 防御属性（用于自选股行业暴露 × 大盘环境关联）
_CYCLICAL_SECTORS = {
    "Information Technology", "Consumer Discretionary", "Industrials",
    "Financials", "Materials", "Energy", "Communication Services",
}
_DEFENSIVE_SECTORS = {"Utilities", "Health Care", "Consumer Staples", "Real Estate"}


def build_watchlist_summary(symbol_sectors: dict, macro_verdict: str) -> WatchlistSummary:
    """自选股行业暴露 + 大盘环境关联。symbol_sectors: {symbol: sector|None}（github 成分行业）。
    个股 tick/财报受 403 限，改从"行业暴露 × 大盘风险偏好"切入——用真实 GICS 行业做确定性关联。纯函数，可单测。
    """
    from collections import Counter

    total = len(symbol_sectors)
    covered_items = {s: sec for s, sec in symbol_sectors.items() if sec}
    covered = len(covered_items)
    counts = Counter(covered_items.values())
    sectors = (
        [
            WatchlistSectorBucket(sector=sec, count=n, pct=int(round(n / covered * 100)))
            for sec, n in counts.most_common()
        ]
        if covered
        else []
    )

    if covered == 0:
        note = "自选股暂未匹配到标普500 成分行业（多为非美股或未覆盖标的），暂无法做行业暴露分析。"
        return WatchlistSummary(total=total, covered=0, sectors=[], note=note, data_quality=classify_data_quality("none"))

    cyc = sum(n for sec, n in counts.items() if sec in _CYCLICAL_SECTORS)
    dfn = sum(n for sec, n in counts.items() if sec in _DEFENSIVE_SECTORS)
    cyc_pct = int(round(cyc / covered * 100))
    top = sectors[0].sector

    if macro_verdict == "风险偏好":
        if cyc_pct >= 60:
            note = f"自选股以顺周期行业为主（{cyc_pct}% · 集中于{top}），当前大盘 risk-on，配置与市场偏好一致。"
        elif dfn > cyc:
            note = f"大盘 risk-on，但自选股偏防御（{top}为主），顺周期占比仅 {cyc_pct}%，可能跑输市场，留意错配。"
        else:
            note = f"自选股行业分散（顺周期 {cyc_pct}%，{top}居首），大盘 risk-on 下整体顺风。"
    elif macro_verdict == "避险":
        if dfn >= cyc:
            note = f"大盘 risk-off，自选股防御行业占比高（{top}为主），配置相对稳健。"
        else:
            note = f"自选股以顺周期为主（{cyc_pct}% · {top}），但大盘转避险，注意回撤、考虑减仓或对冲。"
    else:
        note = f"自选股集中于{top}（{sectors[0].pct}%）；大盘中性，按个股基本面取舍。"

    return WatchlistSummary(
        total=total, covered=covered, sectors=sectors, note=note,
        data_quality=classify_data_quality("github-sp500"),
    )
