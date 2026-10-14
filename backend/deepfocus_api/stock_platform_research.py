"""Read already authorized platform documents and build auditable research claims.

No storage, network, LLM, raw cache or permission decisions live here. Paragraphs
are read before selecting citations; business transmission rules are hypotheses,
not a claim that a model or a regulator has verified the source material.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


DIMENSIONS = {
    "business": ("商业模式与竞争力", ("主营", "收入结构", "业务模式", "商业模式", "定价", "产品价格", "出厂价", "批价", "零售价", "零售价格", "售价", "销售单价", "销售价格", "ASP", "渠道", "客户", "竞争", "份额", "品牌", "供应商", "产能", "产品", "库存"), "销量、产品价格或产品结构变化可能影响收入与利润率；具体弹性需用公司财报核验。", "核验细分收入、销量与产品价格、毛利率、渠道库存和客户集中度。"),
    "fundamentals": ("财务与盈利质量", ("营收", "净利", "毛利", "现金流", "应收", "存货", "负债", "资产", "ROE", "扣非", "回款"), "收入、成本与回款变化可能传导至利润和现金转化；需区分报告期及会计口径。", "核验同期间营收、利润率、经营现金流、应收和存货。"),
    "earnings": ("盈利预期与估值", ("预测", "预期", "估值", "EPS", "PE", "股价", "目标价", "目标价格", "评级", "下修", "上修", "盈利", "业绩"), "盈利预测或风险偏好变化可能改变 EPS 与估值倍数；资料观点不直接改变本页计算价格。", "核验预测年度、币种、EPS 修订、稀释和倍数假设是否兑现。"),
    "technical": ("技术趋势", ("均线", "支撑", "阻力", "突破", "回撤", "成交量", "放量", "缩量", "RSI", "技术面"), "价格与成交量提供历史动量线索，不能证明长期商业价值或未来走势。", "用同一日期和复权口径的日线核验趋势及突破是否持续。"),
    "capital": ("资金与股东行为", ("资金", "主力", "增持", "减持", "回购", "股东", "持股", "解禁", "融资", "大单", "超大单"), "交易分类或股东行为可能影响短期供求；不等于机构真实账户资金进出。", "核验成交分类覆盖日期、正式股东公告与股本变动。"),
    "news": ("消息与事件", ("公告", "政策", "监管", "处罚", "关税", "订单", "合同", "投产", "发布", "新品", "风险", "诉讼", "会议", "调研"), "事件需通过实际订单、收入、成本或监管影响传导，标题与观点不等于兑现。", "核验正式公告、实施日期、事件规模和利润影响。"),
}
_POSITIVE = ("增长", "改善", "上调", "超预期", "回暖", "恢复", "提升", "提价", "成本下降", "回购", "增持")
_NEGATIVE = ("下滑", "承压", "下调", "不及预期", "低于预期", "风险", "疲弱", "降价", "成本上升", "减持", "库存上升", "竞争加剧")
_BODY_STATES = {"full", "excerpt", "teaser", "summary_only", "title_only"}
_ROLES = {"fact", "opinion", "analysis", "context"}
_RELATIONS = {"company", "industry", "peer", "upstream", "downstream"}
_STALE_DAYS = {"site_fast_news": 30, "fast_news": 30, "site_articles": 90, "articles": 90,
               "site_research": 180, "stock_research": 180, "recent_research": 180,
               "institution_notes": 90, "celebrity_views": 30, "professional_reports": 365,
               "professional_sources": 365, "owned_research": 180, "shared_sources": 180}
_CHANNEL_LABELS = {
    "site_fast_news": "平台快讯", "site_articles": "平台文章",
    "site_research": "平台本地研报", "recent_research": "投行研报已有解读",
    "institution_notes": "机构纪要", "celebrity_views": "名人观点",
    "professional_reports": "专业财报共享库", "owned_research": "我的已完成研究",
    "shared_sources": "已明确公开的资料库",
}
_TECHNICAL_STRONG = ("均线", "RSI", "技术面")
_TECHNICAL_CONTEXT = ("股价", "收盘", "开盘", "日线", "K线", "K 线", "成交量", "交易量", "技术面")
_TECHNICAL_PRICE_CONTEXT = ("股价", "股票", "收盘", "开盘", "日线", "K线", "K 线")
_COMPANY_CUES = ("营收", "营业收入", "归母", "净利", "净利润", "利润", "毛利", "扣非", "现金流", "经营现金",
                 "回款", "收入", "应收", "存货", "库存", "主营", "业务", "渠道", "产品", "出厂价", "批价",
                 "零售价", "销量", "产能", "订单", "股价", "股票", "股份", "回购", "增持", "减持", "市值",
                 "股东", "持股", "资产", "负债", "ROE", "EPS", "PE", "估值", "评级", "目标价", "目标股价")
_OTHER_SUBJECTS = ("行业", "同行", "同业", "竞争对手", "竞品", "供应商", "上游", "下游", "经销商", "客户",
                   "业务伙伴", "合作伙伴", "主营产品供应商", "产品供应商", "子公司", "参股公司", "集团")
_RECOMMENDATION = re.compile(r"推荐(?:标的|名单|组合|关注)|(?:重点|建议)关注|建议配置|推荐名单|关注名单")


def _normalized_subject_clause(text: str) -> str:
    value = text.strip()
    # Qualifiers may alternate ('预计其供应商...', '2026年该公司...').
    # Strip them repeatedly, then re-check the actual subject; a date alone
    # cannot establish whose financial or operating metric follows it.
    while value:
        stripped = re.sub(r"^(?:本公司|该公司|公司|其)(?:的)?", "", value)
        stripped = re.sub(r"^(?:预计|预测|预期|有望|可能|估计|将于|将|未来|本期|本季|今年|上期|下期|上季|下季|去年|明年|上半年|下半年|当前|近期|最新|同比|环比|并未|已|未|继续)", "", stripped)
        stripped = re.sub(r"^(?:\d{4}年(?:\d{1,2}月)?(?:\d{1,2}日)?|\d{1,2}月(?:\d{1,2}日)?|(?:\d{4}年)?[一二三四1-4]季度)", "", stripped)
        stripped = stripped.lstrip()
        if stripped == value:
            break
        value = stripped
    return value


def _own_company_clause(text: str) -> bool:
    """Recognize a narrow company statement, not a mentioned related party."""
    value = _normalized_subject_clause(text)
    if not value or value.startswith(_OTHER_SUBJECTS) or _RECOMMENDATION.search(value):
        return False
    # E.g. '五粮液净利增长' must not inherit a preceding company's heading.
    if not value.startswith(_COMPANY_CUES) and re.match(r"^[\u4e00-\u9fffA-Za-z]{2,20}(?:净利|利润|营收|股价|现金流)", value):
        return False
    if value.startswith(_COMPANY_CUES):
        return not re.match(r"^(?:业务|主营(?:产品)?|产品)(?:伙伴|供应商|客户|经销商|的供应商)", value)
    if re.match(r"^(?:支持|停止|开展|完成)(?:对)?(?:其|该公司|公司)?(?:的)?(?:供应商|上游|下游|经销商|客户|同行|行业)", value):
        return False
    if re.match(r"^(?:公告|宣布|表示|发布|开展|停止|支持|完成)", value) and _has(value, _OTHER_SUBJECTS):
        # Naming the announcer does not identify the subject of a supplier's
        # or peer's operating metric reported inside the announcement.
        return False
    # A dated delivery update can be the company's own channel statement.
    # Bare dates and forecasts with a named related party do not qualify.
    return bool(re.match(r"^(?:货已至|货已到|发货|到货|按照|公告|宣布|表示|发布|开展|停止|支持|完成)", value))


def _direct_company_span(text: str, symbol: str, name: str) -> Optional[tuple[int, int]]:
    """Verify only an explicit subject in the final, contiguous quote span.

    Company headings are scoped to their own comma clauses. A peer, sector or
    supplier clause cannot acquire the target company's relation. No following
    unanchored sentence or paragraph inherits this subject.
    """
    prefix = re.match(r"^\s*(?:(?:\[[^\]\n]{1,12}\]|【[^】\n]{1,12}】|[①-⑳•●▪◆▶★☆#*]+|\d{1,2}[.、)）]|[-–—])\s*)*", text)
    start = prefix.end() if prefix else 0
    candidates = [value for value in (name, symbol) if value and (value == symbol or len(value) >= 2)]
    for subject in candidates:
        if not text[start:].casefold().startswith(subject.casefold()):
            continue
        end = start + len(subject)
        rest = text[end:]
        if re.fullmatch(r"[A-Za-z0-9.-]+", subject) and re.match(r"[A-Za-z0-9]", rest):
            continue
        rest = re.sub(rf"^\s*[（(]{re.escape(symbol)}(?:\.(?:SH|SZ|BJ|HK|SS))?[）)]", "", rest, flags=re.I)
        offset = len(text) - len(rest)
        heading = re.match(r"^\s*[:：]\s*", rest)
        if heading:
            offset += heading.end()
            rest = rest[heading.end():]
        elif rest.startswith("的"):
            offset += 1
            rest = rest[1:]
        if not _own_company_clause(rest.split("，", 1)[0].split(",", 1)[0]):
            continue
        span_end = len(text)
        for match in re.finditer(r"[，,]([^，,]*)", rest):
            if not _own_company_clause(match.group(1)):
                span_end = offset + match.start()
                break
        return start, span_end
    return None


def _id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:20]}"


def _date(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except (ValueError, TypeError):
        return None


def _text(value: Any, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _has(text: str, terms) -> bool:
    lowered = text.casefold()
    return any(bool(re.search(rf"(?<![a-z]){re.escape(term.casefold())}(?![a-z])", lowered))
               if re.fullmatch(r"[A-Za-z]+", term) else term.casefold() in lowered for term in terms)


def _stance(text: str) -> str:
    # A condition or a negated cue is not an observed improvement/downturn.
    # These labels select hypotheses for review, never a buy/sell conclusion.
    observed = re.sub(r"(?:如果|若|倘若|一旦)[^。！？；;]*(?:[。！？；;]|$)", "", text)
    observed = re.sub(r"(?:未|没有|不|尚未|并未|未见|不能)(?:出现|实现|明显|持续|显著)?(?:增长|改善|恢复|提升|回暖|上调|下滑|下调|减持|增持|回购)", "", observed)
    positive, negative = _has(observed, _POSITIVE), _has(observed, _NEGATIVE)
    return "mixed" if positive and negative else "support" if positive else "counter" if negative else "neutral"


def _paragraphs(text: str):
    for index, match in enumerate(re.finditer(r"[^\n]+", text)):
        raw = match.group()
        start = match.start() + len(raw) - len(raw.lstrip())
        end = match.end() - len(raw) + len(raw.rstrip())
        if end > start:
            yield index + 1, start, end, text[start:end]


def _clause_background(text: str, symbol: str, name: str, background: bool) -> bool:
    value = text.strip().lstrip("#*•●▪◆▶★☆")
    for subject in (name, symbol):
        if subject and value.casefold().startswith(subject.casefold()):
            rest = value[len(subject):]
            if subject == symbol and re.match(r"[A-Za-z0-9]", rest):
                continue
            rest = re.sub(rf"^\s*[（(]{re.escape(symbol)}(?:\.(?:SH|SZ|BJ|HK|SS))?[）)]", "", rest, flags=re.I)
            value = _normalized_subject_clause(rest.lstrip(" :：的"))
            return value.startswith(_OTHER_SUBJECTS) or bool(_RECOMMENDATION.search(value))
    value = _normalized_subject_clause(value)
    if value.startswith(_OTHER_SUBJECTS):
        return True
    if not value.startswith(_COMPANY_CUES) and re.match(
            r"^[\u4e00-\u9fffA-Za-z]{2,20}(?:净利|利润|营收|收入|毛利|现金流|回款|库存|订单|EPS|股价)", value):
        return True
    return background


def _scoped_paragraphs(text: str, *, subject_required: bool, symbol: str, name: str):
    """A body-only stock mention does not assign the rest of an industry list."""
    background = False
    for paragraph, start, end, body in _paragraphs(text):
        if not subject_required:
            # A company title identifies the document, not every peer metric
            # inside it. Keep omitted company subjects until an explicit
            # related-party switch; only an explicit target resets that scope.
            for sentence in re.finditer(r"[^。！？；;]+[。！？；;]?", body):
                run_start, run_end, run_background = None, None, background
                for clause in re.finditer(r"[^，,]+[，,]?", sentence.group()):
                    current = _clause_background(clause.group(), symbol, name, background)
                    absolute = start + sentence.start() + clause.start()
                    if run_start is not None and current != run_background:
                        yield paragraph, run_start, absolute, text[run_start:absolute], run_background
                        run_start = None
                    if run_start is None:
                        run_start, run_background = absolute, current
                    run_end = start + sentence.start() + clause.end()
                    background = current
                if run_start is not None:
                    yield paragraph, run_start, run_end, text[run_start:run_end], run_background
            continue
        for sentence in re.finditer(r"[^。！？；;]+[。！？；;]?", body):
            value = sentence.group()
            symbol_hit = bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", value, re.I))
            if symbol_hit or len(name) >= 2 and name.casefold() in value.casefold():
                yield paragraph, start + sentence.start(), start + sentence.end(), value, False


def _quoted_span(paragraph: str, start: int, terms) -> tuple[int, int]:
    lowered = paragraph.casefold()
    hits = [lowered.find(term.casefold()) for term in terms if term.casefold() in lowered]
    center = min(hits) if hits else 0
    # Keep a real, contiguous sentence around the relevant content, including
    # late paragraphs. This limit affects response quotations, not reading.
    sentence_start = max((paragraph.rfind(mark, 0, center) + 1 for mark in ("。", "！", "？", ";", "；")), default=0)
    endings = [pos + 1 for mark in ("。", "！", "？", ";", "；") if (pos := paragraph.find(mark, center)) >= 0]
    sentence_end = min(endings) if endings else len(paragraph)
    if sentence_end - sentence_start > 240:
        sentence_start = max(sentence_start, center - 70)
        sentence_end = min(sentence_end, sentence_start + 240)
    return start + sentence_start, start + sentence_end


def _technical_span(paragraph: str, start: int) -> Optional[tuple[int, int]]:
    # A market-cap, inventory or sales-volume record is not a K-line breakout.
    # Check the same sentence, rather than inheriting context from a later one.
    terms = DIMENSIONS["technical"][1]
    for sentence in re.finditer(r"[^。！？；;]+[。！？；;]?", paragraph):
        body = sentence.group()
        operating_volume = bool(re.search(
            r"(?:产品|货物|商品|销量|销售|产能|渠道)[^。！？；;]*(?:成交量|交易量)"
            r"|(?:成交量|交易量)[^。！？；;]*\d+(?:\.\d+)?\s*(?:万|亿)?(?:瓶|吨|件|台|辆)", body))
        if operating_volume and not (_has(body, _TECHNICAL_STRONG) or _has(body, _TECHNICAL_PRICE_CONTEXT)):
            continue
        if _has(body, terms) and (_has(body, _TECHNICAL_STRONG) or _has(body, _TECHNICAL_CONTEXT)):
            return _quoted_span(body, start + sentence.start(), terms)
    return None


def _balanced_claims(rows: list[dict], limit: int, document_channels: dict[str, str]) -> list[dict]:
    """Do not let repeated dimensions from one early document fill a scenario."""
    grouped = defaultdict(list)
    for row in sorted(rows, key=lambda row: (str(row["as_of"] or ""), row["transmission_basis"] == "source_explicit"), reverse=True):
        grouped[document_channels.get(row["document_ids"][0], "unknown")].append(row)
    picked, seen_quotes = [], set()
    groups = sorted(grouped.values(), key=lambda group: str(group[0]["as_of"] or ""), reverse=True)
    while any(groups) and len(picked) < limit:
        for group in groups:
            while group:
                row = group.pop(0)
                quotes = tuple(citation["id"] for citation in row["citations"])
                if quotes in seen_quotes:
                    continue
                picked.append(row)
                seen_quotes.add(quotes)
                break
            if len(picked) >= limit:
                break
    return picked


def build_platform_research(channels: Any, *, symbol: str, name: str, market: str,
                            now: Optional[datetime] = None) -> dict:
    """All input text is a request-bound visibility view, never a raw database row."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    coverage, documents, claims, sources, relations = [], [], [], [], []
    gaps, seen_documents, relation_ids = [], set(), set()
    if not isinstance(channels, list):
        channels = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        key = _text(channel.get("channel"), 80) or "unknown"
        label = _text(channel.get("label"), 100) or key
        status = str(channel.get("status") or "empty")
        meta = channel.get("coverage") if isinstance(channel.get("coverage"), dict) else {}
        denied = status in {"access_required", "not_enabled", "denied", "disabled"}
        items = [] if denied else (channel.get("items") or [])
        body_counts, item_dates, channel_source_ids = Counter(), [], []
        read_chars = matched_paragraphs = selected_claims = 0
        read_count = deduped = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or "")
            title = _text(item.get("title"), 200)
            visible = str(item.get("visible_text") or "")
            if not raw_id or not title:
                continue
            safe_metadata = item.get("metadata_safe") if isinstance(item.get("metadata_safe"), dict) else {}
            state = str(item.get("body_state") or "title_only")
            state = state if state in _BODY_STATES else "excerpt"
            if not visible.strip():
                state = "title_only"
            if safe_metadata.get("text_truncated") and state == "full":
                state = "excerpt"
            published = item.get("published_at")
            dated = _date(published)
            if dated and dated > now:
                gaps.append(f"{label}存在未来日期资料，已剔除。")
                continue
            # Only the same storage record is a duplicate. Matching headlines,
            # dates or even copied text do not identify independent articles.
            original_id = str(item.get("original_id") or raw_id.removeprefix(f"{key}:"))
            namespace = "realtime_messages" if key in {"site_fast_news", "site_articles", "site_research"} else key
            fingerprint = _id("record", f"{namespace}:{original_id}")
            if fingerprint in seen_documents:
                deduped += 1
                continue
            seen_documents.add(fingerprint)
            doc_id = _id("platform-doc", f"{namespace}:{original_id}")
            source_id = _id("platform-source", f"{namespace}:{original_id}")
            revision = hashlib.sha256(f"{visible}\n{published or ''}\n{state}".encode()).hexdigest()[:20]
            age = (now - dated).days if dated else None
            freshness = "unknown" if age is None else "historical" if age > _STALE_DAYS.get(key, 90) else "current"
            source_status = "stale" if freshness == "historical" else "available"
            source_name = _text(item.get("source_name"), 100) or label
            role = str(item.get("role") or ("fact" if key in {"site_fast_news", "fast_news"} else "context" if state == "title_only" else "opinion"))
            role = role if role in _ROLES else "context"
            if state in {"teaser", "summary_only", "title_only"} and role == "fact":
                role = "context" if state == "title_only" else "analysis"
            sources.append({"id": source_id, "label": source_name, "url": item.get("url"), "as_of": published,
                            "status": source_status, "freshness": "源日期未提供" if age is None else f"资料截至 {str(published)[:10]}，距本次 {age} 天；来源观点需复核",
                            "fetched_at": now.isoformat(timespec="seconds")})
            channel_source_ids.append(source_id)
            if published:
                item_dates.append(str(published))
            doc_relations = []
            refs = item.get("relation_refs") or []
            if not refs:
                direct = item.get("symbol") == symbol
                refs = [{"kind": "company", "label": name or symbol, "symbol": symbol,
                         "basis": item.get("match_basis") or "collector_entity_match",
                         "verification": "verified" if direct else "context"}]
            for ref in refs:
                if not isinstance(ref, dict) or ref.get("kind") not in _RELATIONS:
                    continue
                kind, basis = ref["kind"], _text(ref.get("basis"), 240)
                verification = str(ref.get("verification") or "context")
                verification = verification if verification in {"verified", "candidate", "context"} else "context"
                # A collector may pass a verified membership/subject relation.
                # A supply relation needs an explicit quotation in visible text.
                if kind in {"upstream", "downstream"} and (not basis or basis not in visible):
                    verification = "candidate"
                rid = _id("platform-relation", f"{kind}:{ref.get('symbol') or ''}:{ref.get('label') or ''}:{verification}")
                doc_relations.append(rid)
                if rid not in relation_ids:
                    relations.append({"id": rid, "kind": kind, "label": _text(ref.get("label"), 100) or kind,
                                      "symbol": ref.get("symbol"), "basis": basis, "verification": verification,
                                      "citation_ids": [], "source_ids": [source_id]})
                    relation_ids.add(rid)
                else:
                    relation = next(row for row in relations if row["id"] == rid)
                    if source_id not in relation["source_ids"]:
                        relation["source_ids"].append(source_id)
            company_refs = [ref for ref in refs if isinstance(ref, dict) and ref.get("kind") == "company"]
            body_only = bool(company_refs) and not any(ref.get("verification") == "verified" for ref in company_refs)
            source_role = str(safe_metadata.get("source_role") or role)
            source_role = source_role if source_role in _ROLES else "context"
            if body_only:
                role = "context"
            body_counts[state] += 1
            read_count += 1
            read_chars += len(visible)
            document = {"id": doc_id, "original_id": original_id, "record_namespace": namespace,
                        "revision": revision, "channel": key, "channel_label": _CHANNEL_LABELS.get(key, label),
                        "relation_ids": doc_relations, "role": role, "title": title,
                        "published_at": published, "body_state": state, "visible_chars": len(visible), "read_chars": len(visible),
                        "source_id": source_id, "source_name": source_name, "excerpt": _text(visible, 220),
                        "url": item.get("url"), "relevant_paragraphs": 0, "selected_claims": 0}
            documents.append(document)
            if state == "title_only" or not visible.strip():
                continue
            doc_candidates = []
            for paragraph, start, end, body, background_subject in _scoped_paragraphs(visible, subject_required=body_only, symbol=symbol, name=name):
                for dimension, (_, terms, rule_transmission, rule_watch) in DIMENSIONS.items():
                    if not _has(body, terms):
                        continue
                    span = _technical_span(body, start) if dimension == "technical" else _quoted_span(body, start, terms)
                    if span is None:
                        continue
                    matched_paragraphs += 1
                    document["relevant_paragraphs"] += 1
                    quote_start, quote_end = span
                    quote = visible[quote_start:quote_end]
                    direct_span = (_direct_company_span(quote, symbol, name)
                                   if not background_subject and state in {"full", "excerpt"} else None)
                    claim_relations = list(doc_relations)
                    if background_subject:
                        target_relations = {relation["id"] for relation in relations
                                            if relation["kind"] == "company" and relation.get("symbol") == symbol
                                            and relation["verification"] == "verified"}
                        claim_relations = [value for value in claim_relations if value not in target_relations]
                    if direct_span:
                        original_start = quote_start
                        quote_start, quote_end = original_start + direct_span[0], original_start + direct_span[1]
                        quote = visible[quote_start:quote_end]
                        # A later peer's metric must not survive truncating the
                        # contiguous quote to the target company's own clauses.
                        if not _has(quote, terms) or dimension == "technical" and _technical_span(quote, quote_start) is None:
                            continue
                    citation_id = _id("platform-citation", f"{doc_id}:{revision}:{paragraph}:{quote_start}:{quote_end}")
                    citation = {"id": citation_id, "source_id": source_id, "document_id": doc_id, "paragraph": paragraph,
                                "document_revision": revision, "quote": quote, "start": quote_start, "end": quote_end}
                    if direct_span:
                        rid = _id("platform-relation", f"paragraph-company:{symbol}:{citation_id}")
                        if rid not in relation_ids:
                            relations.append({"id": rid, "kind": "company", "label": name or symbol, "symbol": symbol,
                                              "basis": "visible_explicit_company_subject: " + _text(quote, 180),
                                              "verification": "verified", "citation_ids": [], "source_ids": [source_id]})
                            relation_ids.add(rid)
                        # Keep the document-wide relation as background, but
                        # do not present two conflicting identities on a claim.
                        context_company_ids = {relation["id"] for relation in relations
                                               if relation["kind"] == "company" and relation.get("symbol") == symbol
                                               and relation["verification"] == "context"}
                        claim_relations = [value for value in claim_relations if value not in context_company_ids] + [rid]
                    explicit_chain = bool(re.search(r"导致|带动|从而|因此|传导|推动|受益|压缩|侵蚀", quote))
                    explicit_watch = bool(re.search(r"关注|核验|验证|需观察|如果|若|风险提示|风险因素", quote))
                    claim_role = "context" if background_subject else "analysis" if state == "summary_only" else source_role if direct_span else role
                    forecast_language = bool(re.search(r"预计|预测|预期|有望|可能|估计|未来|将(?:于|在)|将[^。！？；;，,]{0,16}(?:增长|下降|下滑|提升|改善|实现|达到|扩大|减少|回购|增持|减持|投产|发货)|目标(?:股)?价|forecast|expect|may\b", quote, re.I))
                    if forecast_language and state != "summary_only" and claim_role != "context":
                        claim_role = "opinion"
                    period = safe_metadata.get("period")
                    period = _text(period, 100) if isinstance(period, str) else None
                    report_currency = safe_metadata.get("currency")
                    report_currency = report_currency if report_currency in {"CNY", "HKD", "USD", "EUR"} else None
                    doc_candidates.append({"id": _id("platform-claim", f"{doc_id}:{revision}:{dimension}:{quote_start}:{quote_end}"),
                                           "dimension": dimension, "topic": DIMENSIONS[dimension][0], "claim": _text(quote, 260),
                                           "transmission": _text(quote, 260) if explicit_chain else rule_transmission,
                                           "transmission_basis": "source_explicit" if explicit_chain else "rule_hypothesis",
                                           "watch": _text(quote, 260) if explicit_watch else rule_watch,
                                           "watch_basis": "source_explicit" if explicit_watch else "rule_hypothesis",
                                           "stance": _stance(quote), "role": claim_role, "body_state": state,
                                           "relation_ids": claim_relations, "citations": [citation], "source_ids": [source_id],
                                           "document_ids": [doc_id], "as_of": published, "freshness": freshness,
                                           "period": period, "currency": report_currency,
                                           "needs_review": True, "verification": "derived_summary" if state == "summary_only" else "source_forecast" if forecast_language else "source_reported"})
            # Selection happens only after EVERY visible paragraph is scanned.
            # Balance dimensions so a long valuation paragraph cannot crowd out
            # a late supply-chain, competition or cash-flow statement.
            picked = []
            for dimension in DIMENSIONS:
                candidates = [row for row in doc_candidates if row["dimension"] == dimension]
                candidates.sort(key=lambda row: (row["transmission_basis"] == "source_explicit", row["watch_basis"] == "source_explicit", row["stance"] != "neutral"), reverse=True)
                picked.extend(candidates[:3])
            selected_claims += len(picked)
            document["selected_claims"] = len(picked)
            claims.extend(picked)
            for relation in relations:
                linked = [row["citations"][0]["id"] for row in picked if relation["id"] in row["relation_ids"]]
                if linked:
                    relation["citation_ids"] = list(dict.fromkeys(relation["citation_ids"] + linked))
        complete = bool(meta.get("complete")) and not meta.get("limit_hit")
        days = meta.get("window_days")
        try:
            window_start = (now - timedelta(days=int(days))).isoformat(timespec="seconds") if days is not None else None
        except (ValueError, TypeError, OverflowError):
            window_start = None
        coverage.append({"key": key, "label": label, "status": status, "scanned": 0 if denied else int(meta.get("scanned") or 0),
                         "matched": 0 if denied else int(meta.get("matched") or len(items)), "read": read_count,
                         "deduped": deduped, "complete": complete and not denied, "limit_hit": bool(meta.get("limit_hit")) and not denied,
                         "body_states": dict(body_counts), "read_chars": read_chars, "relevant_paragraphs": matched_paragraphs,
                         "selected_claims": selected_claims, "period": {"from": min(item_dates) if item_dates else None, "to": max(item_dates) if item_dates else None},
                         "window": {"days": days, "from": meta.get("since") or window_start, "to": meta.get("until") or now.isoformat(timespec="seconds")},
                         "source_ids": channel_source_ids, "scope": channel.get("scope") or meta.get("scope") or "request_visible",
                         "reason": "当前账号不可见或该来源未启用" if denied else channel.get("reason") or meta.get("reason") or ""})
        if denied or status in {"partial", "error", "empty"} or not complete:
            gaps.append(f"{label}：{'当前账号不可见或该来源未启用' if denied else '当前无可读资料' if status == 'empty' else '覆盖不完整，不能视为全库扫描完成'}。")
    # Paragraph relations are returned only with selected, resolvable quotes.
    # Reading an unselected paragraph must not leave an orphan verified label.
    relations = [relation for relation in relations
                 if not relation["basis"].startswith("visible_explicit_company_subject: ") or relation["citation_ids"]]
    # Candidate disagreement is anchored to explicit excerpts and dates; do not
    # claim different reporting periods or related-company facts contradict.
    conflicts = []
    verified_company_symbols = {relation["id"]: relation.get("symbol") for relation in relations
                                if relation["kind"] == "company" and relation["verification"] == "verified" and relation.get("symbol")}
    def company_subjects(claim):
        return {verified_company_symbols[rid] for rid in claim["relation_ids"] if rid in verified_company_symbols}
    grouped = defaultdict(list)
    for claim in claims:
        if claim["stance"] in {"support", "counter"}:
            grouped[claim["dimension"]].append(claim)
    for dimension, rows in grouped.items():
        positives = [row for row in rows if row["stance"] == "support"]
        negatives = [row for row in rows if row["stance"] == "counter"]
        for positive in positives:
            counter = next((row for row in negatives if row["document_ids"] != positive["document_ids"]
                            and (set(row["relation_ids"]) & set(positive["relation_ids"])
                                 or company_subjects(row) & company_subjects(positive))), None)
            if counter is None:
                continue
            periods = [positive.get("period"), counter.get("period")]
            different_periods = all(periods) and periods[0] != periods[1]
            scope_known = all(periods) and not different_periods
            if dimension in {"fundamentals", "earnings"}:
                scope_known = scope_known and bool(positive.get("currency")) and positive.get("currency") == counter.get("currency")
            conflict_type = "different_period" if different_periods else "opposing_views" if scope_known else "unverified_comparability"
            conflict_summary = ("资料报告期不同，正反线索可能同时成立；需复核传导与观点更新，不能认定互相矛盾。" if different_periods else
                                "同维度存在正反线索，仍需核对具体业务、指标与事实/预测性质；属于待复核分歧。" if scope_known else
                                "正反线索的报告期、具体指标或币种尚不足以比较，可能分别描述当期事实与未来预期；不认定构成矛盾。")
            conflicts.append({"id": _id("platform-conflict", f"{dimension}:{positive['id']}:{counter['id']}"),
                              "dimension": dimension, "topic": DIMENSIONS[dimension][0], "claim_ids": [positive["id"], counter["id"]],
                              "type": conflict_type, "summary": conflict_summary,
                              "verification": "candidate", "source_ids": list(dict.fromkeys(positive["source_ids"] + counter["source_ids"]))})
            break
    company_relations = {row["id"] for row in relations if row["kind"] == "company" and row["verification"] == "verified"}
    eligible = [row for row in claims if row["freshness"] == "current" and row["body_state"] in {"full", "excerpt"}
                and row["role"] != "context"
                and company_relations.intersection(row["relation_ids"])]
    context_claims = [row for row in claims if row not in eligible]
    support = [row for row in eligible if row["stance"] == "support"]
    counter = [row for row in eligible if row["stance"] in {"counter", "mixed"}]
    document_channels = {document["id"]: document["channel"] for document in documents}
    scenario_links = []
    for key in ("optimistic", "base", "pessimistic"):
        primary = support if key == "optimistic" else counter if key == "pessimistic" else [row for row in eligible if row["stance"] in {"support", "neutral"}]
        reverse = counter if key == "optimistic" else support if key == "pessimistic" else counter
        picked = _balanced_claims(primary, 6, document_channels)
        reversed_picked = _balanced_claims(reverse, 4, document_channels)
        picked_context = _balanced_claims(context_claims, 8, document_channels)
        scenario_links.append({"key": key, "support_claim_ids": [row["id"] for row in picked], "counter_claim_ids": [row["id"] for row in reversed_picked],
                               "context_claim_ids": [row["id"] for row in picked_context],
                               "watch": list(dict.fromkeys(row["watch"] for row in picked + reversed_picked))[:6],
                               "source_ids": list(dict.fromkeys(sid for row in picked + reversed_picked + picked_context for sid in row["source_ids"]))})
    state = "insufficient" if not claims else "partial" if gaps or any(doc["body_state"] != "full" for doc in documents) else "available"
    citation_map = {citation["id"]: citation for claim in claims for citation in claim["citations"]}
    return {"status": state, "generated_at": now.isoformat(timespec="seconds"), "coverage": coverage, "relations": relations,
            "documents": documents, "claims": claims, "citations": list(citation_map.values()),
            "conflicts": conflicts, "scenario_links": scenario_links,
            "gaps": list(dict.fromkeys(gaps)), "sources": sources,
            "method": "段落证据＋经营传导规则，需要人工复核；已逐段扫描本次返回的账号可见正文/片段，覆盖上限与导语/摘要状态逐源标明，不表示全库已读。输出引用为原可见段落。未调用新 LLM；规则传导为研究假设，摘要/导语不冒充全文或正式已披露事实。自然语言数字不参与情景价格计算。"}
