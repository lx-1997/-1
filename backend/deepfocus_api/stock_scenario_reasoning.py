"""Request-bound, deterministic explanations for existing stock scenarios.

This module reads the already authorized intelligence response. It never loads
documents, calls a model/network, changes prices or infers an EPS from prose.
Conditions and business transmission rules are explicitly research hypotheses.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Optional


_DIMENSIONS = {
    "business": "商业模式与竞争力", "fundamentals": "财务与盈利质量",
    "earnings": "盈利预期与估值", "technical": "技术趋势与交易确认",
    "capital": "资金与股东行为", "news": "消息与事件兑现",
}
_FACTORS = {"optimistic": (1.15, 1.2), "base": (1.0, 1.0), "pessimistic": (0.85, 0.8)}
_TRANSMISSIONS = {
    "business": "经营传导假设：需求、销量×价格及产品结构先影响收入；竞争、成本与费用再影响利润率，最后才可能改变年度 EPS。公司概况不能单独证明护城河或提价空间。",
    "fundamentals": "经营传导假设：营收与利润率决定利润，回款、应收及存货检验现金转化；EPS 还受股本与非经常性损益影响。现金流比值不直接乘到 EPS，也不把累计报告期 EPS 年化。",
    "earnings": "估值传导假设：同年度、同币种 EPS 修订改变盈利基准；风险溢价、利率与可比估值影响 PE。公开预测、评级和目标价均为第三方观点；买入机构占比不是情景概率。",
    "technical": "交易确认假设：同一来源、同币种及可核验复权的日线与成交量用于判断趋势、突破和回撤；这只影响交易时机与确认程度，不机械改变长期 EPS、PE 或情景价格。",
    "capital": "供求传导假设：带交易日期的成交分类净额用于交叉检查量价；正式回购/持仓公告才可验证股东行为。大单统计不是机构或暗池真实账户资金，也不机械改变长期 EPS 或估值。",
    "news": "事件传导假设：资讯或纪要先核对主体、公告与实施日期，再检查订单、收入、成本或监管影响，最后验证利润兑现。标题热度、观点数量与事件已发生不等于 EPS 上修。",
}
_CONDITIONS = {
    "optimistic": {
        "business": "需求、成交价格或产品结构改善能够兑现到公司收入，且竞争或成本没有抵消改善。",
        "fundamentals": "同期间收入、利润率与回款共同改善，新增利润具有现金支撑；不能仅用一项好数据推定全年盈利增加。",
        "earnings": "年度盈利基准上调与估值扩张需要同时兑现；固定 EPS +15%、PE +20% 是待验证假设，资料数量不能证明这两个幅度。",
        "technical": "同口径日线趋势与量价提供持续确认，突破后回踩能守住；技术走强本身不能证明年度盈利上调。",
        "capital": "最新有日期的资金统计与量价同向、持续，若涉及回购或持仓还需正式公告；单日净流入不能证明估值扩张。",
        "news": "公司相关利好有正式依据、规模与时间，并能在订单、利润或成本中兑现；历史已完成事件不充当未来催化。",
    },
    "base": {
        "business": "主要业务的需求、定价和竞争结构维持，资料中的改善与压力均未改变已采用的年度盈利基准。",
        "fundamentals": "已披露报告期的利润与现金质量足以维持基准，但下一报告期尚需核验；不假设利润自动加速。",
        "earnings": "年度 EPS 采样中位数与当前 TTM PE 作为参考保持，未假定独立公允倍数；EPS 或风险溢价变化均需要重算。",
        "technical": "趋势分歧、区间震荡或尚未持续确认时，将技术面用于观察而非提高基准股价；仍需防范趋势转弱。",
        "capital": "资金供求没有经日期和量价核验的持续改变；资金缺失不解释为持平，也不默认机构维持仓位。",
        "news": "已知事件与平台观点仍待兑现，尚不足以改变盈利或估值基准；未见资料不等于没有公司事件。",
    },
    "pessimistic": {
        "business": "需求、售价、份额或产品结构承压，并经收入或利润率验证；不能把同行或供应商压力直接归到本公司。",
        "fundamentals": "利润下滑或现金转化恶化持续，且无法被成本下降、回款改善或非经常性因素解释。",
        "earnings": "年度盈利下修与风险溢价上升需要同时发生；固定 EPS -15%、PE -20% 是压力试算，不是最大亏损或尾部边界。",
        "technical": "同口径日线出现持续趋势走弱或关键区间失守，并以成交量交叉验证；不能据此直接下修长期盈利。",
        "capital": "经交易日期核验的净流出与量价疲弱持续，股东减持等行为另有公告证据；分类净额不代表某家机构退出。",
        "news": "公司相关风险有明确主体、实施时间和经济影响，能传导为收入损失、成本增加或估值折价。",
    },
}
_WATCH = {
    "business": "核验细分收入、销量/售价、毛利率、渠道库存、客户集中度和竞争份额；缺少其中数据时不宣称竞争力已增强。",
    "fundamentals": "核验下一份财报的同期间营收、归母/扣非利润、经营现金流、应收与存货；累计 EPS 不简单乘 4。",
    "earnings": "追踪同年度、同币种、每券商最新 EPS 修订，检查股本稀释、TTM PE 时点与估值历史/同业比较。",
    "technical": "重新核验日线日期、币种、复权声明和成交量，观察均线、历史价格带与趋势持续性；价格带不是未来支撑承诺。",
    "capital": "核验每日资金覆盖结束日、末尾五条记录是否完整，再与同源量价、正式回购/股东公告交叉检查。",
    "news": "核验原文主体、公告日期、实施时间、实际规模与财报兑现；机构纪要和媒体报道仍需公司公告交叉验证。",
}
_INVALIDATION = {
    "optimistic": {
        "business": "若收入/销量或利润率没有兑现业务改善，商业驱动不能支撑乐观盈利假设。",
        "fundamentals": "若利润下滑持续或回款/现金质量恶化，EPS +15% 的乐观假设需要重算。",
        "earnings": "若年度 EPS 被下修或市场倍数不能扩张，乐观价格的两个必要假设不能同时成立。",
        "technical": "若突破缺乏量价支持或趋势回落，交易确认失效；不单凭技术回落否定公司的长期价值。",
        "capital": "若可核验资金持续流出且量价走弱，资金确认失效；缺失资金不能充当乐观证据。",
        "news": "若利好延期、规模缩减或未传导至收入/成本/利润，不能继续作为乐观兑现依据。",
    },
    "base": {
        "business": "若业务结构、需求或竞争格局发生可验证变化，维持基准的条件失效。",
        "fundamentals": "若同期间利润或现金转化明显偏离基准，当前参考需要重算。",
        "earnings": "若 EPS 修订、稀释、利率或风险溢价改变盈利基础/倍数，中性参考需要重算。",
        "technical": "若同口径趋势与成交量确认突破或失守，区间观察需要更新，不把旧价格带继续当有效确认。",
        "capital": "若资金/股东行为出现可核验持续变化，中性资金条件需要更新。",
        "news": "若正式事件的兑现规模改变盈利或风险溢价，中性事件条件失效。",
    },
    "pessimistic": {
        "business": "若需求、售价或份额持续改善并反映到利润，业务压力假设需要重算。",
        "fundamentals": "若利润和同期间现金转化持续改善，盈利下修压力需要重新评估。",
        "earnings": "若年度 EPS 上修且风险溢价回落，盈利下修与 PE 压缩不能同时作为压力基础。",
        "technical": "若同口径趋势与量价持续恢复，技术压力确认失效；超卖单独不保证反弹。",
        "capital": "若有日期的资金与量价持续改善，资金压力需更新；单日流入不证明恢复。",
        "news": "若风险未实施、影响低于原假设或已被经营改善抵消，不能继续作为悲观兑现依据。",
    },
}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 650) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _rows(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _date(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T| |$)", value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _source_status(source: Optional[dict], *, generated: Any) -> str:
    if not source or source.get("status") in {"missing", "insufficient", "error"}:
        return "missing"
    date, current = _date(source.get("as_of")), _date(generated)
    if source.get("status") == "stale" or date and current and date[:10] > current[:10]:
        return "stale"
    if source.get("status") != "available" or not date:
        return "unknown"
    return "available"


def _observation(identifier: str, label: str, statement: str, *, value: Any = None, unit: str = "",
                 source_ids=(), sources: dict, generated: Any, as_of: Any = None,
                 origin: str = "public", role: str = "fact", direction: str = "neutral",
                 platform_claim_ids=(), status: Optional[str] = None) -> dict:
    ids = _unique(sid for sid in source_ids if sid in sources)
    statuses = [_source_status(sources[sid], generated=generated) for sid in ids]
    state = status or ("stale" if "stale" in statuses else "missing" if not statuses or "missing" in statuses
                       else "unknown" if "unknown" in statuses else "available")
    date = _date(as_of) or (_date(sources[ids[0]].get("as_of")) if len(ids) == 1 else None)
    if "stale" in statuses or date and _date(generated) and date[:10] > str(generated)[:10]:
        state = "stale"
    if state == "available" and not date:
        state = "unknown"
    if state != "available" or role == "context":
        direction = "unknown"
    return {"id": identifier, "label": label, "statement": _text(statement), "value": value,
            "unit": unit, "as_of": date, "status": state, "origin": origin, "role": role,
            "direction": direction, "source_ids": ids, "platform_claim_ids": _unique(platform_claim_ids)}


def _metric_sources(key: str, label: str) -> list[str]:
    if key == "fundamentals":
        if "现金流/" in label:
            return ["financials", "cashflow"]
        if "应收/" in label or "存货/" in label:
            return ["financials", "balance"]
        return ["valuation"] if "PE" in label or label == "PB" else ["financials"]
    if key == "earnings":
        return ["nasdaq"] if "季度" in label or "近季" in label else ["consensus"] if "目标价" in label else ["forecast"]
    return ["history"] if key == "technical" else ["flow"] if key == "capital" else []


def _metric_direction(label: str, value: Any) -> str:
    number = _number(value)
    if number is None:
        return "neutral"
    if label in {"营收同比", "归母净利同比"}:
        return "support" if number > 0 else "counter" if number < 0 else "neutral"
    if label == "经营现金流/归母净利":
        return "support" if number >= 1 else "counter" if number < .7 else "neutral"
    return "neutral"


def _verified_claims(result: dict, sources: dict) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    from .stock_platform_research import _direct_company_span, _own_company_clause

    research = result.get("platform_research") or {}
    documents = {row.get("id"): row for row in _rows(research.get("documents"))}
    citations = {row.get("id"): row for row in _rows(research.get("citations"))}
    relations = {row.get("id"): row for row in _rows(research.get("relations"))}
    eligible, background = {key: [] for key in _DIMENSIONS}, {key: [] for key in _DIMENSIONS}
    for claim in _rows(research.get("claims")):
        key, cid = claim.get("dimension"), claim.get("id")
        if key not in eligible or not isinstance(cid, str):
            continue
        # Revalidate binding against this response, not a similarly named or
        # newer document. Never quote an orphan/cross-document candidate.
        refs = _rows(claim.get("citations"))
        valid = (bool(refs)
                 and set(claim.get("source_ids") or []) == {ref.get("source_id") for ref in refs}
                 and set(claim.get("document_ids") or []) == {ref.get("document_id") for ref in refs}
                 and len(refs) == 1 and _text(claim.get("claim"), 260) == _text(refs[0].get("quote"), 260))
        for ref in refs:
            document, registered = documents.get(ref.get("document_id")), citations.get(ref.get("id"))
            valid = valid and bool(document and registered and registered == ref
                and ref.get("document_id") in (claim.get("document_ids") or [])
                and ref.get("source_id") in (claim.get("source_ids") or [])
                and ref.get("source_id") == document.get("source_id") and ref.get("source_id") in sources
                and ref.get("document_revision") == document.get("revision")
                and isinstance(ref.get("quote"), str) and ref.get("quote").strip()
                and isinstance(ref.get("start"), int) and isinstance(ref.get("end"), int)
                and ref["end"] - ref["start"] == len(ref["quote"]) and ref["start"] >= 0)
        if not valid:
            continue
        company = any(relations.get(rid, {}).get("kind") == "company"
                      and relations[rid].get("verification") == "verified"
                      and relations[rid].get("symbol") == result.get("symbol")
                      for rid in claim.get("relation_ids") or [])
        # A stock-matched heading does not lend its subject to a peer's metric
        # inside the paragraph. Require the actual quoted operating clauses to
        # retain the target subject, not merely a document-level relation.
        own_quotes = True
        for ref in refs:
            quote = ref["quote"].strip()
            direct_span = _direct_company_span(quote, str(result.get("symbol") or ""), str(result.get("name") or ""))
            if direct_span:
                own_quotes = own_quotes and direct_span[0] == 0 and direct_span[1] == len(quote)
            else:
                clauses = [part.strip() for part in re.split(r"[，,。！？；;]", quote) if part.strip()]
                own_quotes = own_quotes and bool(clauses) and _own_company_clause(clauses[0]) and all(
                    _own_company_clause(clause) or bool(re.match(r"^(?:需关注|关注|核验|验证|需观察|风险提示|风险因素)", clause))
                    for clause in clauses[1:])
        current = _date(claim.get("as_of")) and claim.get("freshness") == "current"
        bound_bodies = all(documents[ref["document_id"]].get("body_state") in {"full", "excerpt"} for ref in refs)
        usable = (company and own_quotes and current and claim.get("body_state") in {"full", "excerpt"} and bound_bodies
                  and claim.get("role") in {"fact", "opinion", "analysis"}
                  and all(sid in sources and _source_status(sources[sid], generated=result.get("generated_at")) == "available"
                          for sid in claim.get("source_ids") or []) and bool(claim.get("source_ids")))
        (eligible if usable else background)[key].append(claim)
    for group in (eligible, background):
        for key, claims in group.items():
            # Keep opposing observations and diverse documents. A document
            # repeated across dimensions does not become independent support.
            claims.sort(key=lambda row: (str(row.get("as_of") or ""), row.get("transmission_basis") == "source_explicit"), reverse=True)
            seen, unique_claims = set(), []
            for claim in claims:
                quote_ids = tuple(ref["id"] for ref in claim["citations"])
                if quote_ids not in seen:
                    seen.add(quote_ids)
                    unique_claims.append(claim)
            group[key] = unique_claims
    return eligible, background


def _public_observed(result: dict, dimension: dict, sources: dict) -> list[dict]:
    key, name, generated = dimension["key"], result.get("name") or result.get("symbol"), result.get("generated_at")
    out = []
    if key == "business":
        profile = sources.get("profile")
        if profile and profile.get("status") != "missing":
            out.append(_observation("business:profile", "公司业务资料", f"{name} · {dimension.get('headline') or '公司概况'}；公司简介不单独证明定价权。",
                                    source_ids=["profile"], sources=sources, generated=generated, role="context"))
            details = result.get("company_profile") or {}
            for field, label in [("description", "公开公司业务说明"), ("business_scope", "公开经营范围")]:
                if _text(details.get(field)):
                    out.append(_observation("business:" + field, label,
                                            f"{name} · {_text(details[field], 550)}；业务介绍不单独证明客户留存、市占率或护城河。",
                                            source_ids=details.get("source_ids") or ["profile"], sources=sources,
                                            generated=generated, as_of=details.get("as_of"), role="context"))
    for index, metric in enumerate(_rows(dimension.get("metrics"))):
        label, value, unit = _text(metric.get("label"), 100), metric.get("value"), _text(metric.get("unit"), 40)
        if not label or key in {"capital", "news"}:
            continue
        ids = _metric_sources(key, label)
        dates = [_date(sources.get(sid, {}).get("as_of")) for sid in ids]
        date = dates[0] if dates and all(value == dates[0] for value in dates) else None
        direction = _metric_direction(label, value)
        role = "opinion" if key == "earnings" else "fact"
        status = "missing" if value is None else None
        if len(ids) > 1 and (not date or any(not value for value in dates)):
            status = "unknown"
        if "未核验" in unit:
            status = "unknown"
        if key == "technical":
            technical = result.get("technical") or {}
            date = technical.get("as_of")
            if not technical.get("series_currency") or technical.get("series_currency") != result.get("currency") or "未核验" in str(technical.get("adjustment") or ""):
                status = "unknown"
            direction = {"bullish": "support", "bearish": "counter", "neutral": "neutral"}.get(dimension.get("signal"), "unknown")
        statement = f"{name} · {label} {value}{(' ' + unit) if unit else ''}" if value is not None else f"{name} · {label}：本次未提供该指标。"
        if key == "earnings":
            statement += "；第三方预测/观点，尚未兑现" if "目标价" not in label else "；仅第三方参考，未参与情景计算"
        out.append(_observation(f"{key}:metric:{index}", label, statement, value=value, unit=unit, source_ids=ids,
                                sources=sources, generated=generated, as_of=date, direction=direction, role=role, status=status))
    if key == "capital":
        capital = result.get("capital") or {}
        series = _rows(capital.get("series"))[-5:]
        complete = bool(series) and all(_number(row.get("main_net")) is not None and _date(row.get("date")) for row in series)
        if complete:
            amount = round(sum(_number(row["main_net"]) for row in series), 4)
            dates = [row["date"] for row in series]
            state = ("stale" if capital.get("status") == "stale" else "missing" if capital.get("status") != "available"
                     else "unknown" if capital.get("currency") not in {"CNY", "HKD", "USD"} or capital.get("amount_unit") != "元" else None)
            out.append(_observation("capital:recent-main", f"末尾 {len(series)} 条交易日主力分类净额",
                                    f"{name} · {min(dates)}–{max(dates)}，主力成交分类净额 {amount:+,.2f} 元（{capital.get('currency') or '币种未核验'}）；不是机构账户资金。",
                                    value=amount, unit=f"{capital.get('currency') or '币种未核验'} 元", source_ids=capital.get("source_ids") or ["flow"],
                                    sources=sources, generated=generated, as_of=max(dates), status=state,
                                    direction="support" if amount > 0 else "counter" if amount < 0 else "neutral"))
        else:
            # Preserve the old scalar as undated evidence; never invent daily
            # records or reinterpret an unavailable series as zero flow.
            for metric in _rows(dimension.get("metrics"))[:2]:
                value = metric.get("value")
                statement = (f"{name} · {metric.get('label')} {value} {metric.get('unit') or ''}；覆盖交易日期/每日记录未核验。"
                             if value is not None else f"{name} · {metric.get('label')}：本次未提供资金数据；覆盖交易日期/每日记录未核验。")
                out.append(_observation("capital:unverified:" + _text(metric.get("label"), 100), _text(metric.get("label"), 100),
                                        statement, value=value, unit=metric.get("unit") or "", source_ids=["flow"], sources=sources,
                                        generated=generated, status="missing" if value is None else "unknown"))
    if key == "earnings":
        ratings = result.get("broker_ratings") or {}
        if ratings.get("sample_count"):
            counts = [f"{row.get('label')} {row.get('count')} 家" for row in _rows(ratings.get("distribution")) if _number(row.get("count"))]
            out.append(_observation("earnings:ratings", "公开券商评级样本", f"{name} · {'、'.join(counts)}；每券商最新评级，机构家数不是盈利幅度或概率。",
                                    value=ratings.get("sample_count"), unit="家", source_ids=ratings.get("source_ids") or ["broker-ratings"],
                                    sources=sources, generated=generated, as_of=ratings.get("as_of"), role="opinion"))
    if key == "news":
        for item in [row for row in _rows(result.get("evidence")) if row.get("origin") == "public" and row.get("kind") != "券商研报"][:3]:
            out.append(_observation("news:headline:" + str(item.get("id")), "公共资讯线索", f"{item.get('title')}；标题/摘要不直接证明经济影响，待核对正式原文。",
                                    source_ids=[item.get("id")], sources=sources, generated=generated, as_of=item.get("published_at"), role="context"))
    return out


def _claim_observation(claim: dict, *, sources: dict, result: dict, background: bool = False) -> dict:
    role = claim.get("role") if claim.get("role") in {"fact", "opinion", "analysis", "context"} else "context"
    label = "历史/摘要/未核公司背景" if background else "已报道原文（待交叉核验）" if role == "fact" else "平台观点/分析（待复核）"
    date = _date(claim.get("as_of"))
    state = "stale" if claim.get("freshness") == "historical" else "unknown" if background else None
    stance = claim.get("stance")
    return _observation(claim["id"], label, claim.get("claim") or "", source_ids=claim.get("source_ids") or [],
                        sources=sources, generated=result.get("generated_at"), as_of=date, origin="platform",
                        role="context" if background else role, direction=stance if stance in {"support", "counter", "neutral"} else "unknown",
                        platform_claim_ids=[claim["id"]], status=state)


def _assessment(observed: list[dict], scenario: str) -> str:
    usable = [row for row in observed if row["status"] == "available" and row["role"] != "context"]
    directions = {row["direction"] for row in usable if row["direction"] != "unknown"}
    if not directions:
        return "unknown"
    if scenario == "base":
        # Growth/downturn observations require checking the forecast baseline;
        # neither by itself proves the median EPS remains appropriate.
        return "mixed" if directions & {"support", "counter"} else "unknown"
    positive, negative = ("support", "counter") if scenario == "optimistic" else ("counter", "support")
    return "mixed" if positive in directions and negative in directions else "support" if positive in directions else "counter" if negative in directions else "unknown"


def _numeric_bridge(result: dict, scenario: dict, sources: dict) -> dict:
    valuation = result.get("valuation") or {}
    anchor, mode = valuation.get("anchor") or {}, valuation.get("status") or "insufficient"
    mode = mode if mode in {"ready", "sensitivity"} else "insufficient"
    eps_factor, pe_factor = _FACTORS.get(scenario.get("key"), (1.0, 1.0))
    price, eps, pe = _number(anchor.get("price")), _number(anchor.get("forecast_eps")), _number(anchor.get("pe_ttm"))
    base = eps * pe if mode == "ready" and eps is not None and pe is not None else price if mode == "sensitivity" else None
    ids = _unique(sid for sid in valuation.get("source_ids") or [] if sid in sources)
    gaps = []
    for sid in ids:
        status = _source_status(sources[sid], generated=result.get("generated_at"))
        if status != "available":
            gaps.append(f"{sources[sid].get('label') or sid}：{'数据已滞后' if status == 'stale' else '数据日期未核验' if status == 'unknown' else '本次未获得有效数据'}；取数时间不代替数据时点。")
    if mode == "sensitivity":
        gaps.append("未获得可用于估值的同年度、同币种、至少两家去重机构的有效年度 EPS；不展示推造年度 EPS，当前股价校准不是独立估值。")
    elif mode == "insufficient":
        gaps.append("行情、同币种正值 TTM PE 或有效基准未齐，保留空价格；不强行套用盈利估值。")
    metrics = next((row.get("metrics") or [] for row in _rows(result.get("dimensions")) if row.get("key") == "earnings"), [])
    metric_map = {row.get("label"): row for row in _rows(metrics)}
    low, high = metric_map.get("EPS 低值", {}), metric_map.get("EPS 高值", {})
    broker_count = _number(metric_map.get("去重券商样本", {}).get("value"))
    sample_usable = (mode == "ready" and broker_count is not None and broker_count >= 2
                     and str(anchor.get("forecast_year")) == str(result.get("generated_at") or "")[:4]
                     and low.get("unit") == high.get("unit") == result.get("currency")
                     and _source_status(sources.get("forecast"), generated=result.get("generated_at")) == "available")
    sample_low, sample_high = (_number(low.get("value")), _number(high.get("value"))) if sample_usable else (None, None)
    if sample_low is None or sample_high is None or sample_low <= 0 or sample_high < sample_low or eps is None or not sample_low <= eps <= sample_high:
        sample_low = sample_high = None
    scenario_eps = eps * eps_factor if mode == "ready" and eps is not None else None
    sample_relation = "unknown" if scenario_eps is None or sample_low is None or sample_high is None else "above" if scenario_eps > sample_high else "below" if scenario_eps < sample_low else "within"
    forecast_sample = {"year": anchor.get("forecast_year") if mode == "ready" else None, "currency": result.get("currency") or "",
                       "eps_low": sample_low, "eps_median": eps if sample_low is not None else None, "eps_high": sample_high,
                       "broker_count": broker_count if sample_usable else None, "as_of": _date(sources.get("forecast", {}).get("as_of")),
                       "source_ids": ["forecast"] if "forecast" in sources else [], "scenario_vs_sample": sample_relation,
                       "note": "低/高值为本次去重券商采样的年度 EPS 范围，不是概率、置信区间或保证边界。固定情景因子与该样本独立，超出样本范围不表示已有机构给出该预测。"}
    note = "EPS ±15%、PE ±20% 为固定敏感性假设；价格来自原有数值基准，平台原文不提供这些幅度。未使用技术指标、资金流、评级票数或原文数字重估价格；未给概率、公允价值或最大亏损承诺。"
    return {"mode": mode, "price": scenario.get("price"), "currency": result.get("currency") or "",
            "reference_price": price, "reference_as_of": _date((result.get("quote") or {}).get("as_of")),
            "forecast_year": anchor.get("forecast_year") if mode == "ready" else None,
            "base_eps": eps if mode == "ready" else None, "base_pe_ttm": pe,
            "eps_factor": eps_factor, "pe_factor": pe_factor,
            "scenario_eps": round(scenario_eps, 6) if scenario_eps is not None else None,
            "scenario_pe_ttm": round(pe * pe_factor, 6) if mode != "insufficient" and pe is not None and pe > 0 else None,
            "base_price": round(base, 6) if base is not None else None,
            "eps_effect": round(base * (eps_factor - 1), 6) if base is not None else None,
            "pe_effect": round(base * (pe_factor - 1), 6) if base is not None else None,
            "combined_effect": round(base * (eps_factor - 1) * (pe_factor - 1), 6) if base is not None else None,
            "upside_pct": scenario.get("upside_pct"), "source_ids": ids,
            "forecast_sample": forecast_sample, "formula": valuation.get("formula") or "数据不足", "note": note, "gaps": gaps}


def enrich_stock_scenario_reasoning(result: dict) -> dict:
    """Add auditable explanation fields in place and return the same response.

    Must run after ``_attach_platform_research``. This is not an entitlement
    gate: every platform document must already be the caller's visible view.
    """
    if not isinstance(result, dict):
        return result
    sources = {row.get("id"): row for row in _rows(result.get("sources")) if isinstance(row.get("id"), str)}
    dimensions = {row.get("key"): row for row in _rows(result.get("dimensions"))}
    eligible, background = _verified_claims(result, sources)
    observations = {key: _public_observed(result, dimensions.get(key) or {"key": key}, sources) for key in _DIMENSIONS}
    name = _text(result.get("name") or result.get("symbol"), 100)
    method = "平台授权原文＋公共指标的证据链；观测、第三方观点、经营传导假设与待兑现条件分开。技术与资金仅作交易交叉验证；固定价格试算保持不变。"
    general_limits = ["只使用本次账号已获授权且已返回的研报、纪要、资讯与公共数据；来源数量不等于独立证据强度或全库覆盖。",
                      "当前公司原文仍需复核；摘要、历史、日期未知及未核验公司主体资料仅为背景，不参与方向判断。",
                      "没有接入的数据保留缺口；估值历史分位、同业可比、稀释、净负债与事件日历不足时，不能把固定倍数当独立公允估值。"]
    all_current = [claim for group in eligible.values() for claim in group]
    tensions, priority_watch = [], []
    for key, label in _DIMENSIONS.items():
        for row in observations[key]:
            if row["status"] == "available" and row["direction"] == "counter":
                tensions.append(f"{row['statement']}（{row['as_of'][:10]}），与乐观盈利改善条件存在需验证的缺口。")
        for conflict in _rows((result.get("platform_research") or {}).get("conflicts")):
            if conflict.get("dimension") == key:
                tensions.append(f"{label}：{_text(conflict.get('summary'))}")
        if not observations[key] and not eligible[key]:
            priority_watch.append(f"{label}：{_WATCH[key]}")
    for scenario in _rows((result.get("valuation") or {}).get("scenarios")):
        key = scenario.get("key")
        if key not in _FACTORS:
            continue
        bridge = _numeric_bridge(result, scenario, sources)
        drivers = []
        for dim, label in _DIMENSIONS.items():
            claims = eligible[dim]
            supporting_stances = {"counter", "mixed"} if key == "pessimistic" else {"support", "neutral"} if key == "base" else {"support"}
            support = [claim for claim in claims if claim.get("stance") in supporting_stances][:3]
            counter = [claim for claim in claims if claim.get("stance") in ({"support"} if key == "pessimistic" else {"counter", "mixed"})][:3]
            neutral = [claim for claim in claims if claim.get("stance") == "neutral" and claim not in support][:1]
            context = background[dim][:2]
            selected = support + counter + neutral
            observed = list(observations[dim]) + [_claim_observation(claim, sources=sources, result=result) for claim in selected]
            observed += [_claim_observation(claim, sources=sources, result=result, background=True) for claim in context]
            if not observed:
                observed = [_observation(f"{dim}:missing", "尚缺可核验观测", f"{name} · 本次缺少{label}的可核验指标或当前公司原文，不推定中性或无风险。",
                                         sources=sources, generated=result.get("generated_at"), status="missing", role="context")]
            anchor_observation = next((row for row in observed if dim == "fundamentals" and row["status"] == "available" and row["label"] == "归母净利同比"), None)
            if anchor_observation is None:
                anchor_observation = next((row for row in observed if row["status"] == "available" and row["role"] == "fact" and row["direction"] in {"support", "counter"}), None)
            if anchor_observation is None:
                anchor_observation = next((row for row in observed if row["status"] == "available" and row["role"] != "context"), None)
            context_line = f"已见{anchor_observation['as_of'][:10]}资料：{anchor_observation['statement']}。" if anchor_observation else "本次尚缺可核验的当前观测。"
            condition = f"{name}：{context_line}兑现条件：{_CONDITIONS[key][dim]}"
            if dim == "fundamentals" and key == "optimistic":
                negatives = [row for row in observed if row["status"] == "available" and row["label"] == "归母净利同比" and (_number(row["value"]) or 0) < 0]
                if negatives:
                    condition += "当前已披露利润同比为负，与全年 EPS +15% 并不一致，需要验证后续恢复、基数、利润率及股本影响；不能据此断言已经恢复。"
            if dim == "earnings" and bridge["scenario_eps"] is not None:
                condition += f"本情景要求 {bridge['forecast_year']} 年 EPS {bridge['scenario_eps']:.2f} {bridge['currency']}、PE {bridge['scenario_pe_ttm']:.2f} 倍；它们来自固定因子，不是平台原文给出的预测。"
                sample = bridge["forecast_sample"]
                if sample["eps_low"] is not None:
                    relation = {"above": "高于", "below": "低于", "within": "位于"}[sample["scenario_vs_sample"]]
                    condition += f"情景 EPS {relation}本次 {int(sample['broker_count'])} 家去重券商采样范围 {sample['eps_low']:.2f}–{sample['eps_high']:.2f} {sample['currency']}；需用后续预测修订/财报验证，范围不代表概率。"
            explicit = next((claim for claim in selected if claim.get("transmission_basis") == "source_explicit"), None)
            transmission = (f"平台原文传导（{'观点/分析' if explicit.get('role') != 'fact' else '已报道线索'}，待复核）：{_text(explicit.get('transmission'), 280)}。" if explicit else "") + _TRANSMISSIONS[dim]
            limitations = []
            if any(row["status"] == "unknown" for row in observed):
                limitations.append("部分数据日期、币种、交易覆盖或来源身份未核验；对应观测仅展示，不作当前方向证据。")
            if any(row["status"] == "stale" for row in observed):
                limitations.append("陈旧资料仅保留历史背景，不能当作当前催化或趋势确认。")
            if context:
                limitations.append("背景资料的摘要/正文状态、公司主体或时效不符合方向证据条件，未升级为支持/反证。")
            if dim == "technical" and "未核验" in str((result.get("technical") or {}).get("adjustment") or ""):
                limitations.append("日线复权未核验，分红/拆股可能影响均线、RSI 与价格带；当前不能作已核验交易确认。")
            if dim == "technical":
                limitations.append("本次接入序列为日线收盘价，未接入成交量；不能认定已出现放量突破或量价确认。")
            if dim == "capital" and not (result.get("capital") or {}).get("series"):
                limitations.append("每日资金记录缺失；旧五日标量的日期未核验时，不生成净流入/净流出的当前结论。")
            if not claims:
                limitations.append("该维度未获得符合条件的当前公司平台原文；公共指标不能冒充平台独有论据。")
            watches = [_WATCH[dim]] + [_text(claim.get("watch")) for claim in selected if claim.get("watch")]
            invalidation = [_INVALIDATION[key][dim]]
            if anchor_observation:
                invalidation.append(f"下一同口径披露/更新须复核现有观测“{anchor_observation['statement']}”（{anchor_observation['as_of'][:10]}）；若方向或适用期间改变，本条依据重新评估。")
            for claim in counter[:2]:
                invalidation.append(f"反向原文待核验：{claim['claim']}；{claim.get('watch') or _WATCH[dim]}")
            drivers.append({"key": dim, "label": label, "assessment": "mixed" if any(claim.get("stance") == "mixed" for claim in selected) else _assessment(observed, key), "observed": observed,
                            "condition": condition, "transmission": transmission,
                            "source_ids": _unique(sid for row in observed for sid in row["source_ids"]),
                            "platform_claim_ids": _unique(claim["id"] for claim in support),
                            "counter_claim_ids": _unique(claim["id"] for claim in counter),
                            "context_claim_ids": _unique(claim["id"] for claim in context),
                            "watch": _unique(watches), "invalidation": _unique(invalidation), "limitations": _unique(limitations)})
        company_line = next((row for driver in drivers for row in driver["observed"]
                             if row["origin"] == "platform" and row["role"] != "context" and row["status"] == "available"), None)
        fact_line = next((row for driver in drivers if driver["key"] == "fundamentals" for row in driver["observed"]
                         if row["status"] == "available" and row["label"] == "归母净利同比"), None)
        sentence = "；".join(("平台观点/分析：" if row["origin"] == "platform" and row["role"] != "fact" else "平台已报道线索：" if row["origin"] == "platform" else "")
                            + row["statement"].removeprefix(f"{name} · ").rstrip("。！？；;，,") for row in (fact_line, company_line) if row)
        needed = "盈利上修与倍数扩张仍需同时验证" if key == "optimistic" else "维持年度盈利与估值基准仍需验证" if key == "base" else "盈利下修与倍数压缩是待验证压力条件"
        headline = f"{name}：{sentence + '；' if sentence else '尚缺当前公司直接依据；'}{needed}。"
        if company_line is None:
            headline += "平台当前公司直接论据仍有缺口。"
        if bridge["mode"] == "insufficient":
            headline += "定价输入不足，未计算价格。"
        cash_line = next((row for driver in drivers if driver["key"] == "fundamentals" for row in driver["observed"]
                          if row["status"] == "available" and row["label"] == "经营现金流/归母净利"), None)
        fact_summary = (f"已披露净利同比 {fact_line['value']:+.2f}%" if fact_line and _number(fact_line.get("value")) is not None else "当前盈利观测仍需补齐")
        if key == "base" and cash_line and _number(cash_line.get("value")) is not None:
            fact_summary = f"同报告期现金流/净利 {cash_line['value']:.2f} 倍"
        numeric_summary = (f"EPS {bridge['scenario_eps']:.2f} × PE {bridge['scenario_pe_ttm']:.2f}"
                           if bridge["scenario_eps"] is not None and bridge["scenario_pe_ttm"] is not None
                           else f"盈利 {(bridge['eps_factor'] - 1) * 100:+.0f}%、倍数 {(bridge['pe_factor'] - 1) * 100:+.0f}% 的压力条件")
        sample_note = {"above": "，高于采样 EPS 上沿", "below": "，低于采样 EPS 下沿"}.get(bridge["forecast_sample"]["scenario_vs_sample"], "")
        card_summary = (f"{numeric_summary}{sample_note}；{fact_summary}，{'需验证盈利恢复与估值扩张' if key == 'optimistic' else '维持年度基准仍待财报核验' if key == 'base' else '盈利下修与倍数压缩须同时发生'}。"
                        if bridge["mode"] != "insufficient" else "定价输入缺失，暂不估价；先核对公司经营、年度盈利与同币种估值依据。")
        # Trading confirmation alone cannot label a long-term price scenario
        # as supported when operating/earnings evidence is absent.
        assessment = {driver["assessment"] for driver in drivers if driver["key"] not in {"technical", "capital"}}
        scenario["reasoning"] = {"version": 1, "headline": _text(headline), "card_summary": _text(card_summary, 160),
                                 "assessment": "mixed" if "mixed" in assessment or {"support", "counter"} <= assessment else "support" if "support" in assessment else "counter" if "counter" in assessment else "unknown",
                                 "numeric_bridge": bridge, "drivers": drivers,
                                 "source_ids": _unique(bridge["source_ids"] + [sid for driver in drivers for sid in driver["source_ids"]]),
                                 "platform_claim_ids": _unique(cid for driver in drivers for cid in driver["platform_claim_ids"]),
                                 "counter_claim_ids": _unique(cid for driver in drivers for cid in driver["counter_claim_ids"]),
                                 "context_claim_ids": _unique(cid for driver in drivers for cid in driver["context_claim_ids"]),
                                 "watch": _unique(line for driver in drivers for line in driver["watch"])[:12],
                                 "invalidation": _unique(line for driver in drivers for line in driver["invalidation"]),
                                 "limitations": _unique(general_limits + bridge["gaps"])}
    result["scenario_reasoning"] = {"version": 1, "method": method,
                                    "coverage": {"dimensions": 6, "observed_dimensions": sum(any(row["status"] == "available" and row["role"] != "context" for row in observations[key]) or bool(eligible[key]) for key in _DIMENSIONS),
                                                 "current_company_claims": len(_unique(claim["id"] for claim in all_current)),
                                                 "background_claims": len(_unique(claim["id"] for group in background.values() for claim in group))},
                                    "source_ids": _unique(sid for scenario in _rows((result.get("valuation") or {}).get("scenarios")) for sid in (scenario.get("reasoning") or {}).get("source_ids") or []),
                                    "key_tensions": _unique(tensions)[:8], "priority_watch": _unique(priority_watch)[:6],
                                    "limitations": general_limits}
    return result
