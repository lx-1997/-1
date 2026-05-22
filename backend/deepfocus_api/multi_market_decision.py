from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean

from .schemas import (
    BacktestPlan,
    CandidateOpinion,
    DecisionDependency,
    DecisionModuleStatus,
    MarketRegion,
    MarketStyleSignal,
    MultiMarketDecisionRequest,
    MultiMarketDecisionResponse,
    SectorOpinion,
    StockSnapshot,
)


MARKET_LABELS: dict[str, str] = {
    "CN": "A股",
    "HK": "港股",
    "US": "美股",
    "OTHER": "其他市场",
}

SEED_STOCKS: list[dict[str, object]] = [
    {"symbol": "000001.SZ", "name": "平安银行", "market": "CN", "sector": "银行", "change_percent": 0.8, "community_score": 56},
    {"symbol": "300750.SZ", "name": "宁德时代", "market": "CN", "sector": "新能源", "change_percent": 2.4, "community_score": 72},
    {"symbol": "688981.SH", "name": "中芯国际", "market": "CN", "sector": "半导体", "change_percent": 3.1, "community_score": 78},
    {"symbol": "00700.HK", "name": "腾讯控股", "market": "HK", "sector": "互联网", "change_percent": 1.6, "community_score": 74},
    {"symbol": "03690.HK", "name": "美团", "market": "HK", "sector": "互联网", "change_percent": 0.9, "community_score": 68},
    {"symbol": "09988.HK", "name": "阿里巴巴-W", "market": "HK", "sector": "互联网", "change_percent": -0.4, "community_score": 65},
    {"symbol": "NVDA", "name": "NVIDIA", "market": "US", "sector": "半导体", "change_percent": 2.2, "community_score": 82},
    {"symbol": "MSFT", "name": "Microsoft", "market": "US", "sector": "云计算", "change_percent": 0.7, "community_score": 78},
    {"symbol": "TSLA", "name": "Tesla", "market": "US", "sector": "新能源车", "change_percent": -1.2, "community_score": 70},
]


def build_multi_market_decision(request: MultiMarketDecisionRequest) -> MultiMarketDecisionResponse:
    markets = _normalize_markets(request.markets)
    stocks = _normalize_stocks(request.stocks, markets)
    if not stocks:
        stocks = _seed_stock_snapshots(markets)

    modules = [_module_status(market, request.data_profile) for market in markets]
    styles = [_style_signal(market, stocks) for market in markets]
    style_by_market = {style.market: style for style in styles}

    candidates = _candidate_opinions(stocks, style_by_market, request)
    filtered_candidates = [
        candidate for candidate in candidates
        if candidate.score >= request.min_score or candidate.action == "重点跟踪"
    ]
    if not filtered_candidates:
        filtered_candidates = candidates[: min(8, len(candidates))]

    readiness_score = _readiness_score(modules, request.data_profile)
    warnings = _warnings(request, stocks, markets)
    summary = _summary(markets, styles, filtered_candidates, readiness_score, request)

    return MultiMarketDecisionResponse(
        generated_at=datetime.now(timezone.utc),
        mode=request.mode,
        risk_profile=request.risk_profile,
        readiness_score=readiness_score,
        summary=summary,
        modules=modules,
        dependencies=_dependencies(markets),
        market_styles=styles,
        candidates=filtered_candidates[:18],
        portfolio_actions=_portfolio_actions(styles, filtered_candidates, request),
        backtest_plan=[_backtest_plan(market) for market in markets],
        warnings=warnings,
    )


def _normalize_markets(markets: list[MarketRegion]) -> list[MarketRegion]:
    result: list[MarketRegion] = []
    for market in markets or ["CN", "HK", "US"]:
        value = str(market).upper()
        if value in {"CN", "HK", "US", "OTHER"} and value not in result:
            result.append(value)  # type: ignore[arg-type]
    return result or ["CN", "HK", "US"]


def _normalize_stocks(stocks: list[StockSnapshot], markets: list[MarketRegion]) -> list[StockSnapshot]:
    result: list[StockSnapshot] = []
    for stock in stocks:
        market = _infer_market(stock.symbol, stock.market)
        if market not in markets:
            continue
        result.append(
            StockSnapshot(
                symbol=_normalize_symbol(stock.symbol, market),
                name=stock.name or stock.symbol,
                market=market,
                sector=stock.sector or MARKET_LABELS.get(market, "未分组"),
                marketCap=stock.market_cap,
                currentPrice=stock.current_price,
                changePercent=stock.change_percent,
                description=stock.description,
                focusLevel=stock.focus_level,
                communityScore=stock.community_score,
            )
        )
    return result


def _seed_stock_snapshots(markets: list[MarketRegion]) -> list[StockSnapshot]:
    return [
        StockSnapshot(**item)
        for item in SEED_STOCKS
        if item["market"] in markets
    ]


def _module_status(market: MarketRegion, data_profile: str) -> DecisionModuleStatus:
    if market == "CN":
        blocked = [] if data_profile != "offline" else ["需要导入本地 A 股行情、复权、停牌、涨跌停和行业数据"]
        return DecisionModuleStatus(
            key="cn_a_share",
            name="A股智能选股与回测模块",
            market="CN",
            purpose="市场风格识别、板块轮动、个股候选、大涨前概率排序、A股规则回测",
            data_sources=["AKShare", "Tushare Pro", "RQData", "Wind/Choice 可选"],
            research_engine="Qlib / LightGBM / XGBoost / 本地因子库",
            backtest_engine="RQAlpha",
            execution_engine="vn.py 可选",
            status="ready" if data_profile == "china_stable" else "partial",
            readiness=88 if data_profile == "china_stable" else 68,
            blocked_by=blocked,
            notes=["原生处理 T+1、涨跌停、停牌、复权、ST、退市等 A 股规则。"],
        )
    if market == "HK":
        return DecisionModuleStatus(
            key="hk_equity",
            name="港股风格与交易意见模块",
            market="HK",
            purpose="港股行业强弱、互联网/高股息/医药等板块跟踪、候选股排序",
            data_sources=["Futu OpenD", "LongPort", "Wind/Choice 可选"],
            research_engine="Qlib / vectorbt / 本地因子库",
            backtest_engine="vectorbt / Backtrader",
            execution_engine="Futu OpenAPI / LongPort / IBKR 可选",
            status="partial",
            readiness=76,
            blocked_by=["需要配置富途或长桥本地行情服务，避免依赖 Yahoo/OpenBB"],
            notes=["港股交易单位、费用、印花税和流动性规则独立于 A 股。"],
        )
    if market == "US":
        return DecisionModuleStatus(
            key="us_equity",
            name="美股风格与交易意见模块",
            market="US",
            purpose="美股行业趋势、财报窗口、科技/半导体/ETF 候选排序",
            data_sources=["Futu/LongPort", "Wind/Choice", "本地美股数据包"],
            research_engine="Qlib / vectorbt / Zipline-reloaded 可选",
            backtest_engine="vectorbt / Backtrader / Zipline-reloaded",
            execution_engine="Futu / LongPort / IBKR / Alpaca 可选",
            status="partial",
            readiness=70,
            blocked_by=["中国大陆稳定运行时不要把 Polygon、Alpaca、yfinance 作为硬依赖"],
            notes=["盘前盘后、做空、ETF、财报窗口需要独立风控规则。"],
        )
    return DecisionModuleStatus(
        key="other_market",
        name="其他市场扩展模块",
        market="OTHER",
        purpose="保留扩展入口",
        data_sources=["本地数据包"],
        research_engine="本地因子库",
        backtest_engine="vectorbt",
        status="planned",
        readiness=35,
        blocked_by=["需要定义交易日历、行情格式和交易规则"],
    )


def _dependencies(markets: list[MarketRegion]) -> list[DecisionDependency]:
    deps = [
        DecisionDependency(
            name="DuckDB + Parquet",
            role="本地行情、因子、预测结果存储",
            markets=markets,
            required=True,
            mainland_ready=True,
            install_hint="pip install duckdb pyarrow",
            notes="第一版不依赖云数据库，适合中国大陆网络长期自动跑。",
        ),
        DecisionDependency(
            name="Qlib / pyqlib",
            role="多因子、机器学习选股、市场风格自适应",
            markets=[market for market in markets if market in {"CN", "HK", "US"}],
            required=True,
            mainland_ready=True,
            install_hint="pip install pyqlib",
            notes="A 股可先接 cn_data 或自建本地数据集；港股/美股建议转成统一特征格式。",
        ),
        DecisionDependency(
            name="RQAlpha",
            role="A 股专业事件驱动回测",
            markets=[market for market in markets if market == "CN"],
            required="CN" in markets,
            mainland_ready=True,
            install_hint="pip install rqalpha",
            config_keys=["RQDATAC_TOKEN"],
            notes="严肃 A 股回测建议搭配 RQData/Tushare Pro/Wind 之一。",
        ),
        DecisionDependency(
            name="AKShare / Tushare Pro",
            role="A 股免费/轻量数据接入",
            markets=[market for market in markets if market == "CN"],
            required="CN" in markets,
            mainland_ready=True,
            install_hint="pip install akshare tushare",
            config_keys=["TUSHARE_TOKEN"],
            notes="AKShare 适合探索，Tushare/RQData 更适合长期回测数据治理。",
        ),
        DecisionDependency(
            name="Futu OpenD / LongPort",
            role="港股、美股大陆可访问行情与交易连接",
            markets=[market for market in markets if market in {"HK", "US"}],
            required=any(market in {"HK", "US"} for market in markets),
            mainland_ready=True,
            install_hint="pip install futu-api",
            config_keys=["FUTU_OPEND_HOST", "FUTU_OPEND_PORT", "LONGPORT_APP_KEY"],
            notes="港股/美股不要把 yfinance、OpenBB、Polygon 当硬依赖。",
        ),
        DecisionDependency(
            name="LightGBM / XGBoost / scikit-learn",
            role="大涨前候选概率模型、因子动态加权",
            markets=markets,
            required=True,
            mainland_ready=True,
            install_hint="pip install scikit-learn lightgbm xgboost",
            notes="优先做概率排序和回测验证，不输出确定性上涨承诺。",
        ),
        DecisionDependency(
            name="国内大模型 API",
            role="解释层、日报、风险复核",
            markets=markets,
            required=False,
            mainland_ready=True,
            config_keys=["DEEPFOCUS_LLM_PROVIDER", "DEEPFOCUS_LLM_API_KEY", "DEEPFOCUS_LLM_BASE_URL"],
            notes="LLM 只做解释和报告，不直接替代交易规则。",
        ),
    ]
    return [dep for dep in deps if dep.markets or dep.name in {"DuckDB + Parquet", "国内大模型 API"}]


def _style_signal(market: MarketRegion, stocks: list[StockSnapshot]) -> MarketStyleSignal:
    market_stocks = [stock for stock in stocks if _infer_market(stock.symbol, stock.market) == market]
    changes = [stock.change_percent for stock in market_stocks if stock.change_percent is not None]
    avg_change = mean(changes) if changes else 0
    breadth = sum(1 for value in changes if value > 0) / len(changes) if changes else 0.5
    trend_score = int(_clamp(50 + avg_change * 8 + (breadth - 0.5) * 24, 0, 100))
    risk_regime = "进攻" if trend_score >= 66 else "防守" if trend_score <= 42 else "均衡"
    label = _style_label(market, risk_regime, trend_score, market_stocks)
    sectors = _sector_opinions(market_stocks, market)
    avoid = [sector.name for sector in sectors if sector.stance == "回避"][:4]

    factors = ["动量", "成交量", "波动率"]
    if market == "CN":
        factors = ["板块强度", "资金流", "涨跌停情绪", "动量"]
    elif market == "HK":
        factors = ["南向资金", "互联网权重", "估值修复", "流动性"]
    elif market == "US":
        factors = ["财报动量", "利率敏感度", "科技权重", "趋势延续"]

    return MarketStyleSignal(
        market=market,
        label=label,
        trend_score=trend_score,
        risk_regime=risk_regime,  # type: ignore[arg-type]
        dominant_factors=factors,
        sector_opinions=sectors,
        avoid_sectors=avoid,
        rationale=f"{MARKET_LABELS.get(market, market)}样本平均涨跌幅 {avg_change:.2f}%，上涨占比 {breadth:.0%}，当前偏{risk_regime}。",
    )


def _sector_opinions(stocks: list[StockSnapshot], market: MarketRegion) -> list[SectorOpinion]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for stock in stocks:
        sector = stock.sector or MARKET_LABELS.get(market, "未分组")
        grouped[sector].append(stock.change_percent or 0)

    if not grouped:
        fallback = {
            "CN": ["半导体", "机器人", "高股息"],
            "HK": ["互联网", "高股息", "医药"],
            "US": ["半导体", "云计算", "AI应用"],
        }.get(market, ["综合"])
        return [
            SectorOpinion(name=name, score=58, stance="中性", rationale="等待本地行情和行业数据补齐。")
            for name in fallback
        ]

    opinions: list[SectorOpinion] = []
    for sector, values in grouped.items():
        sector_avg = mean(values)
        score = int(_clamp(55 + sector_avg * 8 + min(len(values), 4) * 2, 0, 100))
        stance = "强势" if score >= 68 else "回避" if score <= 42 else "中性"
        opinions.append(
            SectorOpinion(
                name=sector,
                score=score,
                stance=stance,  # type: ignore[arg-type]
                rationale=f"样本涨跌幅均值 {sector_avg:.2f}%，样本数 {len(values)}。",
            )
        )
    return sorted(opinions, key=lambda item: item.score, reverse=True)[:6]


def _candidate_opinions(
    stocks: list[StockSnapshot],
    styles: dict[MarketRegion, MarketStyleSignal],
    request: MultiMarketDecisionRequest,
) -> list[CandidateOpinion]:
    candidates: list[CandidateOpinion] = []
    risk_penalty = {"保守": 8, "稳健": 4, "进取": 0, "专业": -2}.get(request.risk_profile, 4)

    for stock in stocks:
        market = _infer_market(stock.symbol, stock.market)
        if market not in styles:
            continue
        style = styles[market]
        sector_score = next(
            (sector.score for sector in style.sector_opinions if sector.name == (stock.sector or "")),
            55,
        )
        change = stock.change_percent or 0
        community = stock.community_score if stock.community_score is not None else 55
        focus_bonus = {"high": 8, "medium": 4, "low": 0}.get(str(stock.focus_level or "").lower(), 2)
        momentum = _clamp(change, -8, 8) * 3
        style_bonus = (style.trend_score - 50) * 0.32
        sector_bonus = (sector_score - 50) * 0.28
        score = int(round(_clamp(52 + momentum + style_bonus + sector_bonus + (community - 50) * 0.18 + focus_bonus - risk_penalty, 0, 100)))
        action = "重点跟踪" if score >= 72 else "观察" if score >= 52 else "回避"
        probability = round(_clamp(0.18 + (score - 50) * 0.006 + max(change, 0) * 0.012, 0.05, 0.78), 2)
        sector_name = stock.sector or MARKET_LABELS.get(market, "未分组")
        candidates.append(
            CandidateOpinion(
                symbol=stock.symbol,
                name=stock.name,
                market=market,  # type: ignore[arg-type]
                sector=sector_name,
                action=action,  # type: ignore[arg-type]
                score=score,
                surge_probability=probability,
                expected_horizon=request.horizon,
                evidence=[
                    f"{style.label}，市场趋势分 {style.trend_score}",
                    f"{sector_name}板块评分 {sector_score}",
                    f"个股近端涨跌幅 {change:.2f}%，关注热度 {community:.0f}",
                ],
                risk_flags=_risk_flags(stock, style),
                invalidation=_invalidation_rules(market, request.horizon),
            )
        )
    return sorted(candidates, key=lambda item: (item.action == "重点跟踪", item.score, item.surge_probability), reverse=True)


def _backtest_plan(market: MarketRegion) -> BacktestPlan:
    if market == "CN":
        return BacktestPlan(
            market="CN",
            engine="RQAlpha + Qlib",
            data_requirements=["前复权/后复权行情", "停牌与涨跌停", "ST/退市", "行业与概念", "财务 PIT 数据", "指数成分历史"],
            trading_rules=["T+1", "涨跌停不可成交", "停牌不可成交", "手续费/印花税/滑点", "单票和行业仓位上限"],
            metrics=["年化收益", "最大回撤", "Sharpe", "IC/RankIC", "换手率", "命中率", "大涨捕获率"],
            next_step="先跑 5/10/20 日大涨标签回测，再接入滚动训练和因子动态加权。",
        )
    if market == "HK":
        return BacktestPlan(
            market="HK",
            engine="vectorbt / Backtrader",
            data_requirements=["富途/长桥历史行情", "交易单位", "印花税与交易费", "停牌", "港股行业分类"],
            trading_rules=["T+0", "交易单位约束", "印花税", "流动性过滤", "南向资金信号可选"],
            metrics=["收益回撤比", "胜率", "换手率", "滑点敏感性", "板块轮动贡献"],
            next_step="先建立港股独立费用和交易单位模型，再复用统一评分层。",
        )
    if market == "US":
        return BacktestPlan(
            market="US",
            engine="vectorbt / Backtrader / Zipline-reloaded",
            data_requirements=["本地美股日线/分钟线", "财报日历", "拆股分红", "ETF/行业映射", "汇率可选"],
            trading_rules=["盘前盘后独立处理", "做空权限可选", "财报窗口风控", "滑点和成交量约束"],
            metrics=["CAGR", "最大回撤", "Sharpe", "Sortino", "财报窗口胜率", "行业暴露"],
            next_step="用本地数据或富途/长桥数据替代 yfinance，避免境外免费源成为硬依赖。",
        )
    return BacktestPlan(market="OTHER", engine="vectorbt", next_step="先定义市场规则和数据格式。")


def _portfolio_actions(
    styles: list[MarketStyleSignal],
    candidates: list[CandidateOpinion],
    request: MultiMarketDecisionRequest,
) -> list[str]:
    actions = []
    strong_markets = [MARKET_LABELS.get(style.market, style.market) for style in styles if style.risk_regime == "进攻"]
    defensive_markets = [MARKET_LABELS.get(style.market, style.market) for style in styles if style.risk_regime == "防守"]
    top = [item for item in candidates if item.action == "重点跟踪"][:5]

    if strong_markets:
        actions.append(f"{'、'.join(strong_markets)}偏进攻，候选股可进入小仓试错和回测优先队列。")
    if defensive_markets:
        actions.append(f"{'、'.join(defensive_markets)}偏防守，降低追高权重，优先等回测和风控确认。")
    if top:
        actions.append("重点跟踪池：" + "、".join(f"{item.name}({item.symbol})" for item in top))
    actions.append(f"{request.horizon}周期先按概率排序，不做确定性大涨承诺；所有信号必须通过历史回测和失效条件复核。")
    actions.append("LLM 仅生成解释和报告，交易动作由评分、回测、风控模块共同确认。")
    return actions


def _summary(
    markets: list[MarketRegion],
    styles: list[MarketStyleSignal],
    candidates: list[CandidateOpinion],
    readiness: int,
    request: MultiMarketDecisionRequest,
) -> str:
    labels = "、".join(MARKET_LABELS.get(market, market) for market in markets)
    top_styles = "；".join(f"{MARKET_LABELS.get(style.market, style.market)}：{style.label}" for style in styles)
    top_candidates = "、".join(f"{item.name}({item.symbol})" for item in candidates[:4]) or "暂无"
    return (
        f"已生成{labels}分市场独立模块的本地规则版决策快照，系统就绪度 {readiness}/100。"
        f" 当前风格：{top_styles}。{request.horizon}候选优先观察：{top_candidates}。"
        " 下一步应接入本地历史数据并跑大涨标签回测。"
    )


def _warnings(
    request: MultiMarketDecisionRequest,
    stocks: list[StockSnapshot],
    markets: list[MarketRegion],
) -> list[str]:
    warnings: list[str] = []
    if request.data_profile == "china_stable":
        warnings.append("已按中国大陆可稳定访问依赖设计：不把 GitHub、Yahoo Finance、OpenAI、Polygon、Alpaca 作为硬依赖。")
    if not stocks:
        warnings.append("未收到自选股样本，当前仅返回模块配置和示例候选。")
    if any(market in {"HK", "US"} for market in markets):
        warnings.append("港股/美股稳定运行需配置富途、长桥、Wind、Choice 或本地数据包之一。")
    warnings.append("大涨前预测以概率排序表达，不能保证未来上涨。")
    return warnings


def _style_label(
    market: MarketRegion,
    regime: str,
    trend_score: int,
    stocks: list[StockSnapshot],
) -> str:
    sector_counter: dict[str, int] = defaultdict(int)
    for stock in stocks:
        sector_counter[stock.sector or MARKET_LABELS.get(market, "综合")] += 1
    leading_sector = max(sector_counter, key=sector_counter.get) if sector_counter else "综合"
    market_name = MARKET_LABELS.get(market, market)
    if regime == "进攻":
        return f"{market_name}{leading_sector}偏强，趋势延续"
    if regime == "防守":
        return f"{market_name}风险偏好收缩，等待确认"
    return f"{market_name}均衡震荡，板块轮动"


def _risk_flags(stock: StockSnapshot, style: MarketStyleSignal) -> list[str]:
    flags = []
    if (stock.change_percent or 0) > 6:
        flags.append("短线涨幅较高，追高风险上升")
    if (stock.change_percent or 0) < -4:
        flags.append("近端走弱，需要等待止跌")
    if style.risk_regime == "防守":
        flags.append("市场风格偏防守，降低仓位假设")
    if stock.community_score is not None and stock.community_score < 45:
        flags.append("关注热度不足，流动性和催化需复核")
    return flags or ["数据源仍需交叉验证", "回测未完成前不进入实盘"]


def _invalidation_rules(market: MarketRegion, horizon: str) -> list[str]:
    base = ["评分跌破 50", "板块强度跌出前 30%", "放量破位或回撤超预设阈值"]
    if market == "CN":
        base.extend(["触发 ST/退市/停牌过滤", "涨停或跌停导致无法按模型成交"])
    elif market == "HK":
        base.extend(["成交额低于流动性阈值", "交易单位导致仓位偏离过大"])
    elif market == "US":
        base.extend(["财报窗口波动超阈值", "盘前盘后跳空破坏信号"])
    return [f"{horizon}内{rule}" if index == 0 else rule for index, rule in enumerate(base)]


def _readiness_score(modules: list[DecisionModuleStatus], data_profile: str) -> int:
    if not modules:
        return 0
    score = int(round(mean(module.readiness for module in modules)))
    if data_profile == "offline":
        score = max(35, score - 12)
    if data_profile == "mixed":
        score = max(45, score - 5)
    return int(_clamp(score, 0, 100))


def _infer_market(symbol: str, explicit_market: str | None = None) -> str:
    value = (symbol or "").strip().upper()
    market = (explicit_market or "").strip().upper()
    if market in {"CN", "HK", "US", "OTHER"}:
        return market
    base = value.split(".")[0]
    if value.endswith((".SH", ".SZ", ".BJ")) or re.fullmatch(r"\d{6}", base):
        return "CN"
    if value.endswith(".HK") or re.fullmatch(r"\d{5}", base):
        return "HK"
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value.replace(".US", "")):
        return "US"
    return "OTHER"


def _normalize_symbol(symbol: str, market: str) -> str:
    value = (symbol or "").strip().upper()
    if market == "CN" and re.fullmatch(r"\d{6}", value):
        suffix = "SH" if value.startswith("6") else "SZ"
        return f"{value}.{suffix}"
    if market == "HK" and re.fullmatch(r"\d{1,5}", value):
        return f"{value.zfill(5)}.HK"
    return value


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
