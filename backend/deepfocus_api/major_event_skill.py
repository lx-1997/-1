from __future__ import annotations

import asyncio
import io
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from .schemas import MajorEventRecord, MajorEventScanRequest, MajorEventScanResponse
from .skill_routing import mentions_specific_stock
from .shared_utils import parse_date, clean_title, display_value, dedupe, safe_error



CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn"
CNINFO_SOURCE_NAME = "巨潮资讯网"
REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_PAGES_PER_KEYWORD = 3
MAX_CONCURRENT_QUERIES = 4
PDF_DETAIL_MAX_BYTES = 14 * 1024 * 1024
PDF_DETAIL_MAX_PAGES = 8
PDF_DETAIL_MAX_CHARS = 48_000
CN_TZ = ZoneInfo("Asia/Shanghai")

CNINFO_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/fulltextSearch",
    "User-Agent": "Mozilla/5.0 DeepFocus/0.1 major-event-skill",
}

EVENT_META: dict[str, dict[str, Any]] = {
    "control_change": {
        "label": "控制权",
        "keywords": ["控制权变更", "实际控制人变更", "控股股东变更", "权益变动报告书"],
    },
    "restructuring": {
        "label": "并购重组",
        "keywords": ["重大资产重组", "发行股份购买资产", "资产收购", "资产出售", "吸收合并"],
    },
    "buyback": {
        "label": "股份回购",
        "keywords": ["回购股份", "股份回购", "回购公司股份"],
    },
    "equity_incentive": {
        "label": "股权激励",
        "keywords": ["股权激励", "限制性股票激励", "股票期权激励", "员工持股计划"],
    },
    "pledge_freeze": {
        "label": "质押冻结",
        "keywords": ["股份质押", "股份冻结", "轮候冻结", "司法拍卖"],
    },
    "litigation_arbitration": {
        "label": "诉讼仲裁",
        "keywords": ["重大诉讼", "重大仲裁", "诉讼进展", "仲裁进展"],
    },
    "regulatory_penalty": {
        "label": "监管处罚",
        "keywords": ["行政处罚", "立案调查", "监管函", "警示函", "纪律处分", "关注函"],
    },
    "st_delisting": {
        "label": "ST/退市",
        "keywords": ["退市风险警示", "其他风险警示", "可能被终止上市", "终止上市"],
    },
    "abnormal_trading": {
        "label": "交易异动",
        "keywords": ["股票交易异常波动", "严重异常波动", "交易风险提示"],
    },
    "dividend": {
        "label": "分红派息",
        "keywords": ["权益分派实施公告", "利润分配预案", "年度权益分派"],
    },
    "financing": {
        "label": "再融资",
        "keywords": ["向特定对象发行股票", "非公开发行股票", "可转换公司债券", "配股"],
    },
    "related_transaction": {
        "label": "关联/担保",
        "keywords": ["关联交易", "对外担保", "资金占用"],
    },
    "other": {
        "label": "其他重大事项",
        "keywords": ["重大事项", "重大合同", "重大投资", "项目中标"],
    },
}

EVENT_ORDER = list(EVENT_META.keys())
DEFAULT_KEYWORDS = [
    "控制权变更",
    "重大资产重组",
    "回购股份",
    "股权激励",
    "股份质押",
    "股份冻结",
    "司法拍卖",
    "重大诉讼",
    "行政处罚",
    "立案调查",
    "监管函",
    "警示函",
    "退市风险警示",
    "股票交易异常波动",
    "权益分派实施公告",
    "向特定对象发行股票",
    "关联交易",
    "对外担保",
    "重大合同",
    "项目中标",
]


def detect_major_event_request(message: str) -> Optional[MajorEventScanRequest]:
    text = (message or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", "", text)
    # 范围词收紧：去掉裸"市场"(太泛，"市场怎么看/市场情绪"都会误命中)，只认明确的全市场表述。
    has_scope = bool(re.search(r"A股|全A|沪深|两市|上市公司|全市场", compact, re.I))
    has_event = bool(
        re.search(
            r"重大事项|事件中心|公告事件|事件预警|重大公告|风险预警|控制权|重组|回购|股权激励|员工持股|"
            r"质押|冻结|司法拍卖|诉讼|仲裁|行政处罚|立案调查|监管函|警示函|问询函|ST|退市|异常波动|"
            r"分红|权益分派|定增|再融资|关联交易|对外担保|资金占用",
            compact,
            re.I,
        )
    )
    if not has_scope or not has_event:
        return None
    # 锁定了具体个股(6 位代码或已知股票名)⇒ 是"这只股"的问题，不跑全市场扫描，让位给个股研究路径。
    if mentions_specific_stock(text):
        return None

    days = 30
    day_match = re.search(r"(?:近|最近|过去)(\d{1,3})(?:天|日)", compact)
    if day_match:
        days = int(day_match.group(1))
    elif re.search(r"近一周|最近一周|过去一周|上周", compact):
        days = 7
    elif re.search(r"近一个月|最近一个月|过去一个月", compact):
        days = 30
    elif re.search(r"近三个月|最近三个月|过去三个月|近一季|最近一季|过去一季", compact):
        days = 90
    elif re.search(r"近半年|最近半年|过去半年", compact):
        days = 180

    limit = 120
    limit_match = re.search(r"(?:前|top|Top|TOP)(\d{1,3})", text)
    if limit_match:
        limit = int(limit_match.group(1))

    event_types = _event_types_from_text(compact)
    return MajorEventScanRequest(
        market="A",
        days=max(1, min(days, 180)),
        event_types=event_types,  # type: ignore[arg-type]
        limit=max(1, min(limit, 300)),
    )


async def scan_major_events(request: MajorEventScanRequest) -> MajorEventScanResponse:
    start_at, end_at = _date_range(request)
    keywords = _keywords_for_event_types(request.event_types)
    warnings: list[str] = []
    records_by_key: dict[str, MajorEventRecord] = {}
    event_filter = set(request.event_types)
    per_keyword_limit = max(30, min(120 if event_filter else 60, request.limit))

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=CNINFO_HEADERS, follow_redirects=True) as client:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

        async def query_keyword(keyword: str) -> tuple[list[dict[str, Any]], int, list[str]]:
            async with semaphore:
                return await _query_cninfo_keyword(
                    client=client,
                    keyword=keyword,
                    start_at=start_at,
                    end_at=end_at,
                    limit=per_keyword_limit,
                )

        query_results = await asyncio.gather(*(query_keyword(keyword) for keyword in keywords))
        for rows, _total, provider_warnings in query_results:
            warnings.extend(provider_warnings)
            for row in rows:
                record = _record_from_cninfo_row(row)
                if record is None:
                    continue
                if event_filter and record.event_type not in event_filter:
                    continue
                key = record.announcement_id or f"{record.symbol}:{record.title}:{record.announcement_date}"
                existing = records_by_key.get(key)
                if existing is None or _record_preference(record) > _record_preference(existing):
                    records_by_key[key] = record

        records = sorted(
            records_by_key.values(),
            key=lambda item: (item.announcement_date, _risk_rank(item.risk_level), _event_type_rank(item.event_type), item.symbol),
            reverse=True,
        )
        limited_records = records[: request.limit]
        detail_attempted, detail_success = await _enrich_records_with_pdf_details(
            client=client,
            records=limited_records[: request.detail_limit],
            warnings=warnings,
        )

    summary = _build_summary(records, start_at, end_at)
    if not records and not warnings:
        warnings.append("巨潮公告检索未返回匹配重大事项公告；可扩大日期范围或收窄事件类型后重试。")

    return MajorEventScanResponse(
        generated_at=datetime.now(timezone.utc),
        market="A",
        start_date=start_at.isoformat(),
        end_date=end_at.isoformat(),
        total_found=len(records),
        returned_count=len(limited_records),
        detail_attempted_count=detail_attempted,
        detail_success_count=detail_success,
        summary=summary,
        records=limited_records,
        warnings=dedupe(warnings)[:8],
        coverage_note=_coverage_note(detail_attempted, detail_success),
    )


def format_major_event_skill_response(result: MajorEventScanResponse) -> str:
    lines = [
        f"已调用 skill：{result.skill}",
        f"范围：A 股 {result.start_date} 至 {result.end_date}。",
        result.summary,
    ]
    if result.records:
        parsed_hint = (
            f"已解析前 {result.detail_attempted_count} 条 PDF 正文，"
            f"{result.detail_success_count} 条抽取到可用明细。"
            if result.detail_attempted_count
            else "当前仅展示公告索引。"
        )
        lines.extend(["", f"重大事项（前 {min(12, len(result.records))} 条，{parsed_hint}）："])
        for index, record in enumerate(result.records[:12], start=1):
            lines.extend(
                [
                    (
                        f"{index}. {record.announcement_date} {record.symbol} {record.name} | "
                        f"{_event_type_label(record.event_type)}/{_status_label(record.status)} | "
                        f"风险：{record.risk_level} | 影响：{_impact_label(record.impact)}"
                    ),
                    f"   事项：{record.title}",
                    (
                        f"   主体：{display_value(record.subject)}；金额/比例："
                        f"{display_value(record.amount)} / {display_value(record.share_ratio)}"
                    ),
                    f"   进展/期限：{display_value(record.progress)} / {display_value(record.deadline)}",
                    f"   原文：{record.url}",
                ]
            )
            if record.key_points:
                lines.append(f"   看点：{'；'.join(record.key_points[:3])}")
            if record.risk_flags:
                lines.append(f"   风险：{'；'.join(record.risk_flags[:3])}")
            if record.evidence_excerpt:
                lines.append(f"   摘录：{record.evidence_excerpt}")
    else:
        lines.append("未检索到匹配重大事项公告。")
    if result.warnings:
        lines.extend(["", f"提示：{result.warnings[0]}"])
    lines.extend(["", result.coverage_note])
    return "\n".join(lines)


async def _query_cninfo_keyword(
    *,
    client: httpx.AsyncClient,
    keyword: str,
    start_at: date,
    end_at: date,
    limit: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    total = 0
    page_size = min(30, max(10, limit))
    max_pages = min(MAX_PAGES_PER_KEYWORD, max(1, (limit + page_size - 1) // page_size))

    for page_num in range(1, max_pages + 1):
        form = {
            "pageNum": str(page_num),
            "pageSize": str(page_size),
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": keyword,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{start_at.isoformat()}~{end_at.isoformat()}",
            "sortName": "time",
            "sortType": "desc",
            "isHLtitle": "true",
        }
        try:
            response = await client.post(CNINFO_QUERY_URL, data=form)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{CNINFO_SOURCE_NAME} 查询“{keyword}”失败：{safe_error(exc)}")
            break

        total = max(total, int(payload.get("totalAnnouncement") or payload.get("totalRecordNum") or 0))
        page_rows = payload.get("announcements") or []
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(rows) >= limit or len(page_rows) < page_size:
            break

    return rows[:limit], total, warnings


def _record_from_cninfo_row(row: dict[str, Any]) -> Optional[MajorEventRecord]:
    title = clean_title(str(row.get("announcementTitle") or row.get("shortTitle") or ""))
    if not _is_major_event_title(title):
        return None
    symbol = str(row.get("secCode") or "").strip()
    name = clean_title(str(row.get("secName") or row.get("tileSecName") or ""))
    announcement_id = str(row.get("announcementId") or "").strip()
    announcement_date = _announcement_date(row.get("announcementTime"))
    adjunct_url = str(row.get("adjunctUrl") or "").strip()
    url = f"{CNINFO_STATIC_BASE}/{adjunct_url}" if adjunct_url else "https://www.cninfo.com.cn/"
    event_type = _infer_event_type(title)
    status = _infer_status(title)
    risk_flags = _risk_flags_from_text(title, event_type)
    risk_level = _risk_level_from_flags(risk_flags, event_type, title)
    tags = dedupe([_event_type_label(event_type), *risk_flags, _status_label(status)])[:8]

    return MajorEventRecord(
        symbol=symbol,
        name=name,
        announcement_date=announcement_date,
        event_type=event_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        impact=_infer_impact(event_type, risk_flags, title),  # type: ignore[arg-type]
        risk_level=risk_level,  # type: ignore[arg-type]
        title=title,
        url=url,
        source="cninfo",
        source_name=CNINFO_SOURCE_NAME,
        announcement_id=announcement_id,
        risk_flags=risk_flags,
        tags=tags,
        action_items=_action_items(event_type, risk_flags),
        metadata={
            "page_column": row.get("pageColumn"),
            "adjunct_type": row.get("adjunctType"),
            "event_type_label": _event_type_label(event_type),
        },
    )


async def _enrich_records_with_pdf_details(
    *,
    client: httpx.AsyncClient,
    records: list[MajorEventRecord],
    warnings: list[str],
) -> tuple[int, int]:
    attempted = 0
    success = 0
    for record in records:
        if not record.url.lower().endswith(".pdf"):
            continue
        attempted += 1
        try:
            detail = await _extract_pdf_detail(client, record)
        except Exception as exc:  # noqa: BLE001
            record.detail_source = "unavailable"
            warnings.append(f"{record.symbol or record.name} PDF 明细解析失败：{safe_error(exc)}")
            continue
        _apply_pdf_detail(record, detail)
        if record.detail_quality != "title_only":
            success += 1
    return attempted, success


async def _extract_pdf_detail(client: httpx.AsyncClient, record: MajorEventRecord) -> dict[str, Any]:
    response = await client.get(record.url)
    response.raise_for_status()
    raw = response.content
    if len(raw) > PDF_DETAIL_MAX_BYTES:
        raise ValueError("PDF 文件超过解析上限")
    text = _extract_pdf_text(raw)
    return _extract_event_detail_from_text(text, record)


def _extract_pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for page in reader.pages[:PDF_DETAIL_MAX_PAGES]:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text)
    return _compact_pdf_text("\n".join(pages))[:PDF_DETAIL_MAX_CHARS]


def _extract_event_detail_from_text(text: str, record: MajorEventRecord) -> dict[str, Any]:
    compact = _compact_pdf_text(text)
    subject = _extract_subject(compact, record)
    amount = _extract_amount(compact, record.event_type)
    share_ratio = _extract_share_ratio(compact, record.event_type)
    progress = _extract_progress(compact, record.event_type)
    deadline = _extract_deadline(compact)
    key_points = _extract_key_points(compact, record.event_type)
    risk_flags = dedupe([*record.risk_flags, *_risk_flags_from_text(compact, record.event_type)])[:8]
    action_items = _action_items(record.event_type, risk_flags)
    evidence_excerpt = _extract_evidence_excerpt(compact, record.event_type)
    detail_summary = _build_detail_summary(
        subject=subject,
        amount=amount,
        share_ratio=share_ratio,
        progress=progress,
        deadline=deadline,
        key_points=key_points,
    )
    quality = _detail_quality(
        subject=subject,
        amount=amount,
        share_ratio=share_ratio,
        progress=progress,
        key_points=key_points,
    )

    return {
        "subject": subject,
        "amount": amount,
        "share_ratio": share_ratio,
        "progress": progress,
        "deadline": deadline,
        "detail_summary": detail_summary,
        "key_points": key_points,
        "risk_flags": risk_flags,
        "action_items": action_items,
        "evidence_excerpt": evidence_excerpt,
        "risk_level": _risk_level_from_flags(risk_flags, record.event_type, compact),
        "impact": _infer_impact(record.event_type, risk_flags, compact),
        "detail_source": "pdf",
        "detail_quality": quality,
    }


def _apply_pdf_detail(record: MajorEventRecord, detail: dict[str, Any]) -> None:
    for field, value in detail.items():
        setattr(record, field, value)


def _extract_subject(text: str, record: MajorEventRecord) -> str:
    patterns_by_type = {
        "control_change": [
            r"(?:控股股东|实际控制人|受让方|转让方)\s*[:：]?\s*([^。；;，,]{2,80})",
            r"(?:控制权|表决权).{0,80}?变更为([^。；;，,]{2,80})",
        ],
        "restructuring": [
            r"(?:交易对方|标的资产|交易标的)\s*[:：]?\s*([^。；;]{2,120})",
            r"(?:拟|计划)(?:购买|出售|收购)([^。；;]{2,120})",
        ],
        "buyback": [
            r"(?:回购用途|股份用途)\s*[:：]?\s*([^。；;]{2,100})",
        ],
        "pledge_freeze": [
            r"(?:股东名称|出质人|被冻结人|拍卖股东)\s*[:：]?\s*([^。；;，,]{2,80})",
            r"股东([^。；;]{2,80}?)(?:持有|所持|质押|冻结)",
        ],
        "litigation_arbitration": [
            r"(?:原告|申请人|被告|被申请人)\s*[:：]?\s*([^。；;，,]{2,100})",
            r"(?:诉讼|仲裁)(?:事项|案件).{0,40}?([^。；;]{2,120})",
        ],
        "regulatory_penalty": [
            r"(?:处罚对象|被立案调查对象|监管对象|当事人)\s*[:：]?\s*([^。；;，,]{2,100})",
            r"(?:收到|因)([^。；;]{2,100}?)(?:行政处罚|立案|监管函|警示函|问询函)",
        ],
        "equity_incentive": [
            r"(?:激励对象|参与对象)\s*[:：]?\s*([^。；;]{2,100})",
        ],
        "related_transaction": [
            r"(?:关联方|担保对象|被担保人|交易对方)\s*[:：]?\s*([^。；;，,]{2,100})",
        ],
    }
    patterns = patterns_by_type.get(record.event_type, []) + [
        r"(?:合同对方|中标项目|项目名称|交易对方|主体)\s*[:：]?\s*([^。；;]{2,120})",
    ]
    return _first_match(text, patterns)


def _extract_amount(text: str, event_type: str) -> str:
    amount_patterns = [
        r"(?:交易价格|交易金额|转让价款|成交金额|合同金额|中标金额|投资金额|担保金额|涉案金额|罚款金额|处罚金额|募集资金总额|回购资金总额|回购金额)\s*[:：]?\s*((?:人民币)?\s*[0-9][0-9,，.]*\s*(?:万|亿)?元)",
        r"(?:不超过|不低于|不少于|总额为|金额为|合计)\s*((?:人民币)?\s*[0-9][0-9,，.]*\s*(?:万|亿)?元)",
    ]
    if event_type == "dividend":
        amount_patterns.insert(0, r"每(?:10|十)股(?:派发现金红利|派息)\s*([0-9.]+\s*元)")
    if event_type == "buyback":
        amount_patterns.insert(0, r"回购(?:资金)?总额(?:不低于|不超过|为|：|:)?\s*((?:人民币)?\s*[0-9][0-9,，.]*\s*(?:万|亿)?元)")
    return _first_match(text, amount_patterns)


def _extract_share_ratio(text: str, event_type: str) -> str:
    patterns = [
        r"(?:持股比例|表决权比例|股权比例|股份比例|占公司总股本(?:的)?比例)\s*[:：]?\s*([0-9.]+%)",
        r"(?:占|达到|不超过|不低于).{0,20}(?:总股本|股份总数|表决权|股权).{0,20}?([0-9.]+%)",
    ]
    if event_type == "dividend":
        patterns.insert(0, r"(?:每10股|每十股)\s*(?:派|转|送)[^。；;]{0,60}")
    return _first_match(text, patterns)


def _extract_progress(text: str, event_type: str) -> str:
    keywords = [
        "实施完毕",
        "获得通过",
        "签署",
        "收到",
        "立案",
        "处罚",
        "监管函",
        "问询函",
        "进展",
        "终止",
        "撤销",
        "完成",
        "尚需",
        "存在不确定性",
    ]
    if event_type == "abnormal_trading":
        keywords = ["异常波动", "严重异常波动", "交易风险提示", *keywords]
    sentence = _sentence_with_keywords(text, keywords)
    return _trim_excerpt(sentence, limit=120)


def _extract_deadline(text: str) -> str:
    patterns = [
        r"(?:期限|有效期|实施期限|锁定期|承诺期限|回复期限)\s*[:：]?\s*([^。；;]{2,80})",
        r"(20\d{2}年\d{1,2}月\d{1,2}日(?:前|后|起|至|到|止)[^。；;]{0,50})",
        r"(自[^。；;]{2,90}?(?:个月|个交易日|日内|工作日内))",
    ]
    return _first_match(text, patterns)


def _extract_key_points(text: str, event_type: str) -> list[str]:
    event_keywords = _keywords_for_record_type(event_type)
    generic_keywords = ["重要内容提示", "风险提示", "尚需", "不确定性", "影响", "承诺", "审议通过", "实施"]
    sentences = _split_sentences(text)
    points: list[str] = []
    for sentence in sentences:
        if len(points) >= 5:
            break
        if len(sentence) < 8 or len(sentence) > 180:
            continue
        if any(keyword in sentence for keyword in [*event_keywords, *generic_keywords]):
            points.append(_trim_detail(sentence))
    return dedupe(points)[:5]


def _extract_evidence_excerpt(text: str, event_type: str) -> str:
    keywords = ["重要内容提示", *_keywords_for_record_type(event_type), "风险提示", "尚需", "不确定性"]
    for keyword in keywords:
        index = text.find(keyword)
        if index >= 0:
            start = max(0, index - 24)
            return _trim_excerpt(text[start : index + 260])
    return ""


def _build_detail_summary(
    *,
    subject: str,
    amount: str,
    share_ratio: str,
    progress: str,
    deadline: str,
    key_points: list[str],
) -> str:
    parts = [
        f"主体：{subject}" if subject else "",
        f"金额：{amount}" if amount else "",
        f"比例：{share_ratio}" if share_ratio else "",
        f"进展：{progress}" if progress else "",
        f"期限：{deadline}" if deadline else "",
        f"看点：{key_points[0]}" if key_points else "",
    ]
    return "；".join(part for part in parts if part)


def _detail_quality(
    *,
    subject: str,
    amount: str,
    share_ratio: str,
    progress: str,
    key_points: list[str],
) -> str:
    filled = sum(bool(value) for value in [subject, amount, share_ratio, progress]) + int(bool(key_points))
    if filled >= 3:
        return "full"
    if filled:
        return "partial"
    return "title_only"


def _is_major_event_title(title: str) -> bool:
    if re.search(r"年度报告|季度报告|半年度报告|业绩预告|业绩快报|审计报告|募集说明书", title):
        return False
    if re.search(r"增持|减持", title) and not re.search(r"回购|员工持股|股权激励|控制权|权益变动", title):
        return False
    event_type = _infer_event_type(title)
    if event_type != "other":
        return True
    return bool(re.search(r"重大事项|重大合同|重大投资|项目中标|签订.{0,12}合同|重要事项", title))


def _infer_event_type(text: str) -> str:
    if re.search(r"审核问询函|注册问询|发行审核", text) and re.search(r"可转债|可转换公司债券|向特定对象|非公开发行|配股|发行股票", text):
        return "financing"
    patterns = [
        ("regulatory_penalty", r"行政处罚|立案调查|监管函|警示函|关注函|(?:年报|监管|交易所)问询函|纪律处分|公开谴责|监管措施"),
        ("st_delisting", r"退市风险警示|其他风险警示|终止上市|暂停上市|撤销.*风险警示|实施.*风险警示"),
        ("litigation_arbitration", r"重大诉讼|重大仲裁|诉讼|仲裁"),
        ("pledge_freeze", r"股份质押|股权质押|股份冻结|股权冻结|轮候冻结|司法拍卖|司法冻结"),
        ("control_change", r"控制权变更|实际控制人变更|控股股东变更|权益变动报告书|表决权委托"),
        ("restructuring", r"重大资产重组|发行股份购买资产|资产收购|资产出售|吸收合并|重组预案|并购"),
        ("buyback", r"回购股份|股份回购|回购公司股份"),
        ("equity_incentive", r"股权激励|限制性股票激励|股票期权激励|员工持股计划"),
        ("abnormal_trading", r"股票交易异常波动|严重异常波动|交易风险提示|异动公告"),
        ("dividend", r"权益分派|利润分配|现金分红|分红派息"),
        ("financing", r"向特定对象发行股票|非公开发行股票|可转换公司债券|可转债|配股|定增|再融资"),
        ("related_transaction", r"关联交易|对外担保|资金占用|违规担保"),
    ]
    for event_type, pattern in patterns:
        if re.search(pattern, text, re.I):
            return event_type
    return "other"


def _infer_status(text: str) -> str:
    if re.search(r"风险提示|异常波动|立案|处罚|冻结|退市风险|终止上市|问询函|监管函|警示函", text):
        return "risk"
    if re.search(r"完成|实施完毕|结果|终止|撤销|解除|回复", text):
        return "completed"
    if re.search(r"进展|提示性|补充|修订|调整|延期|更新", text):
        return "progress"
    if re.search(r"预案|计划|首次|关于|签署|收到|审议", text):
        return "new"
    return "other"


def _risk_flags_from_text(text: str, event_type: str) -> list[str]:
    checks = [
        (r"立案调查|行政处罚|监管函|警示函|关注函|(?:年报|监管|交易所)问询函|纪律处分|公开谴责|监管措施", "监管风险"),
        (r"退市|终止上市|退市风险警示|其他风险警示", "退市/ST风险"),
        (r"冻结|轮候冻结|司法拍卖|司法冻结|查封", "股权司法风险"),
        (r"质押|平仓|违约|被动减持", "质押/平仓风险"),
        (r"诉讼|仲裁|赔偿|涉案", "诉讼仲裁风险"),
        (r"异常波动|严重异常波动|交易风险提示", "交易异动风险"),
        (r"终止|中止|失败|未获通过|不予受理|撤回", "事项终止风险"),
        (r"资金占用|违规担保|对外担保|连带责任", "担保占用风险"),
        (r"不确定性|尚需|无法保证|可能导致", "不确定性风险"),
    ]
    flags = [label for pattern, label in checks if re.search(pattern, text, re.I)]
    if event_type in {"restructuring", "control_change", "financing"} and "不确定性风险" not in flags:
        flags.append("不确定性风险")
    return dedupe(flags)


def _risk_level_from_flags(flags: list[str], event_type: str, text: str) -> str:
    high_tokens = {
        "监管风险",
        "退市/ST风险",
        "股权司法风险",
        "质押/平仓风险",
        "诉讼仲裁风险",
        "交易异动风险",
        "担保占用风险",
    }
    if high_tokens.intersection(flags):
        return "high"
    if re.search(r"重大|不确定性|尚需|可能导致|控制权|重组|再融资|关联交易", text):
        return "medium"
    if event_type in {"restructuring", "control_change", "financing", "related_transaction", "other"}:
        return "medium"
    return "low"


def _infer_impact(event_type: str, flags: list[str], text: str) -> str:
    if {"监管风险", "退市/ST风险", "股权司法风险", "诉讼仲裁风险", "交易异动风险", "担保占用风险"}.intersection(flags):
        return "negative"
    if re.search(r"终止|失败|撤回|未获通过", text):
        return "negative"
    if event_type in {"buyback", "dividend", "equity_incentive"}:
        return "positive"
    if event_type in {"restructuring", "control_change", "financing", "related_transaction", "pledge_freeze"}:
        return "mixed"
    return "unknown"


def _action_items(event_type: str, flags: list[str]) -> list[str]:
    items: list[str] = []
    if flags:
        items.append("核验公告原文和后续交易所问询/回复")
    if event_type in {"restructuring", "control_change", "financing"}:
        items.append("跟踪股东大会、交易所审核和监管批复节点")
    if event_type in {"regulatory_penalty", "st_delisting"}:
        items.append("复核是否触发 ST、退市、索赔或审计意见变化")
    if event_type in {"pledge_freeze", "litigation_arbitration", "related_transaction"}:
        items.append("评估现金流、控制权稳定性和潜在负债")
    if event_type in {"buyback", "dividend", "equity_incentive"}:
        items.append("核对实施进度、资金来源和股份注销/授予条件")
    return dedupe(items or ["纳入自选事件流，等待后续公告确认"])


def _build_summary(records: list[MajorEventRecord], start_at: date, end_at: date) -> str:
    if not records:
        return f"{start_at.isoformat()} 至 {end_at.isoformat()} 未命中 A 股重大事项公告。"
    high_count = sum(record.risk_level == "high" for record in records)
    negative_count = sum(record.impact == "negative" for record in records)
    type_counts: dict[str, int] = {}
    for record in records:
        type_counts[record.event_type] = type_counts.get(record.event_type, 0) + 1
    top_types = sorted(type_counts.items(), key=lambda item: item[1], reverse=True)[:4]
    type_text = "、".join(f"{_event_type_label(event_type)}{count}条" for event_type, count in top_types)
    return (
        f"{start_at.isoformat()} 至 {end_at.isoformat()} 命中 {len(records)} 条 A 股重大事项公告，"
        f"高风险 {high_count} 条，负面影响 {negative_count} 条；主要集中在 {type_text}。"
    )


def _keywords_for_event_types(event_types: list[str]) -> list[str]:
    if not event_types:
        return DEFAULT_KEYWORDS
    selected = event_types or EVENT_ORDER
    keywords: list[str] = []
    for event_type in selected:
        meta = EVENT_META.get(event_type)
        if not meta:
            continue
        keywords.extend(meta["keywords"])
    return dedupe(keywords)


def _event_types_from_text(text: str) -> list[str]:
    matched: list[str] = []
    for event_type in EVENT_ORDER:
        if any(keyword in text for keyword in EVENT_META[event_type]["keywords"]):
            matched.append(event_type)
    if "重组" in text and "restructuring" not in matched:
        matched.append("restructuring")
    if "回购" in text and "buyback" not in matched:
        matched.append("buyback")
    if "退市" in text and "st_delisting" not in matched:
        matched.append("st_delisting")
    if "处罚" in text and "regulatory_penalty" not in matched:
        matched.append("regulatory_penalty")
    return dedupe(matched)


def _keywords_for_record_type(event_type: str) -> list[str]:
    return list(EVENT_META.get(event_type, EVENT_META["other"])["keywords"])


def _event_type_label(event_type: str) -> str:
    return str(EVENT_META.get(event_type, EVENT_META["other"])["label"])


def _status_label(status: str) -> str:
    return {
        "new": "新披露",
        "progress": "进展",
        "completed": "完成/终止",
        "risk": "风险",
        "other": "其他",
    }.get(status, status)


def _impact_label(impact: str) -> str:
    return {
        "positive": "偏正面",
        "neutral": "中性",
        "negative": "偏负面",
        "mixed": "待验证",
        "unknown": "未识别",
    }.get(impact, impact)


def _record_preference(record: MajorEventRecord) -> int:
    score = _risk_rank(record.risk_level) * 10 + _event_type_rank(record.event_type)
    if record.url.lower().endswith(".pdf"):
        score += 2
    if record.status == "risk":
        score += 2
    return score


def _event_type_rank(event_type: str) -> int:
    weights = {
        "regulatory_penalty": 12,
        "st_delisting": 11,
        "pledge_freeze": 10,
        "litigation_arbitration": 9,
        "abnormal_trading": 8,
        "control_change": 7,
        "restructuring": 6,
        "financing": 5,
        "related_transaction": 4,
        "buyback": 3,
        "equity_incentive": 2,
        "dividend": 1,
        "other": 0,
    }
    return weights.get(event_type, 0)


def _risk_rank(level: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(level, 0)


def _date_range(request: MajorEventScanRequest) -> tuple[date, date]:
    end_at = parse_date(request.end_date) or datetime.now(CN_TZ).date()
    start_at = parse_date(request.start_date) or (end_at - timedelta(days=request.days - 1))
    if start_at > end_at:
        start_at, end_at = end_at, start_at
    return start_at, end_at
def _announcement_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=CN_TZ).date().isoformat()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            return _announcement_date(int(text))
        parsed = parse_date(text)
        if parsed:
            return parsed.isoformat()
    return datetime.now(CN_TZ).date().isoformat()
def _compact_pdf_text(text: str) -> str:
    compact = (
        text.replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("", " ")
        .replace("√", " √")
        .replace("□", " □")
    )
    compact = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", r"\1年\2月\3日", compact)
    compact = re.sub(r"\s+", " ", compact)
    return compact.strip()


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _trim_detail(match.group(1))
    return ""


def _sentence_with_keywords(text: str, keywords: list[str]) -> str:
    for sentence in _split_sentences(text):
        if any(keyword in sentence for keyword in keywords):
            return sentence
    return ""


def _split_sentences(text: str) -> list[str]:
    return [_trim_detail(part) for part in re.split(r"[。；;]\s*", text) if _trim_detail(part)]


def _trim_detail(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*[:：]\s*", "：", value)
    value = value.replace(" ,", ",").replace(", ", ",")
    return value.strip(" ：:，,；;。")


def _trim_excerpt(value: str, limit: int = 220) -> str:
    compact = _trim_detail(value)
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."
def _coverage_note(detail_attempted: int, detail_success: int) -> str:
    if detail_attempted:
        return (
            f"来源为巨潮资讯网公告检索；本次尝试解析前 {detail_attempted} 条 PDF，"
            f"{detail_success} 条抽取到结构化明细。空字段代表公告未披露或版式未识别，原文链接保留用于核验。"
        )
    return "来源为巨潮资讯网公告检索；当前仅使用公告索引和标题分类，原文链接保留用于核验。"