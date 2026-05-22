from __future__ import annotations

import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from .eastmoney_announcements import EASTMONEY_HEADERS, EASTMONEY_SOURCE_NAME, query_eastmoney_announcements
from .schemas import (
    CnEarningsDiagnosisAgentStep,
    CnEarningsDiagnosisRequest,
    CnEarningsDiagnosisResponse,
    CnEarningsRecordDetailRequest,
    CnEarningsRecordDetailResponse,
    CnEarningsRecord,
    CnEarningsScanRequest,
    CnEarningsScanResponse,
)


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn"
CNINFO_SOURCE_NAME = "巨潮资讯网"
REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_PAGES_PER_KEYWORD = 8
PDF_DETAIL_MAX_BYTES = 14 * 1024 * 1024
PDF_DETAIL_MAX_PAGES = 12
PDF_DETAIL_MAX_CHARS = 48_000
CN_TZ = ZoneInfo("Asia/Shanghai")

CNINFO_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord=%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def detect_cn_earnings_request(message: str) -> Optional[CnEarningsScanRequest]:
    text = (message or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", "", text)
    has_scope = bool(re.search(r"A股|全A|沪深|上市公司|市场", compact, re.I))
    has_earnings = bool(re.search(r"财报|年报|半年报|季报|一季报|三季报|季度报告|业绩预告|业绩快报|营业收入|净利润|扣非", compact))
    if not has_scope or not has_earnings:
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

    report_types: list[str] = []
    if re.search(r"年报|年度报告", compact):
        report_types.append("annual")
    if re.search(r"半年报|半年度报告", compact):
        report_types.append("semiannual")
    if re.search(r"一季报|第一季度|一季度", compact):
        report_types.append("q1")
    if re.search(r"三季报|第三季度|三季度", compact):
        report_types.append("q3")
    if "业绩预告" in compact:
        report_types.append("forecast")
    if "业绩快报" in compact:
        report_types.append("flash")

    return CnEarningsScanRequest(
        market="A",
        days=max(1, min(days, 180)),
        report_types=_dedupe(report_types),  # type: ignore[arg-type]
        limit=max(1, min(limit, 300)),
    )


async def scan_cn_earnings(request: CnEarningsScanRequest) -> CnEarningsScanResponse:
    start_at, end_at = _date_range(request)
    keywords = _keywords_for_report_types(request.report_types)
    warnings: list[str] = []
    records_by_key: dict[str, CnEarningsRecord] = {}
    raw_total = 0

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=CNINFO_HEADERS, follow_redirects=True) as client:
        for keyword in keywords:
            rows, total, provider_warnings = await _query_cninfo_keyword(
                client=client,
                keyword=keyword,
                start_at=start_at,
                end_at=end_at,
                limit=max(request.limit * 2, 240),
            )
            raw_total += total
            warnings.extend(provider_warnings)
            for row in rows:
                record = _record_from_cninfo_row(row)
                if record is None:
                    continue
                if request.report_types and record.report_type not in set(request.report_types):
                    continue
                key = _dedupe_key(record)
                existing = records_by_key.get(key)
                if existing is None or _record_preference(record) > _record_preference(existing):
                    records_by_key[key] = record

        if not records_by_key:
            rows, total, provider_warnings = await _query_eastmoney_fallback(
                client=client,
                report_types=request.report_types,
                start_at=start_at,
                end_at=end_at,
                limit=max(request.limit * 2, 240),
            )
            raw_total += total
            warnings.extend(provider_warnings)
            if rows:
                warnings.append(f"{CNINFO_SOURCE_NAME} 不可用或无结果，已切换 {EASTMONEY_SOURCE_NAME} 兜底索引。")
            for row in rows:
                record = _record_from_cninfo_row(row)
                if record is None:
                    continue
                if request.report_types and record.report_type not in set(request.report_types):
                    continue
                key = _dedupe_key(record)
                existing = records_by_key.get(key)
                if existing is None or _record_preference(record) > _record_preference(existing):
                    records_by_key[key] = record

        records = sorted(
            records_by_key.values(),
            key=lambda item: (item.announcement_date, _risk_rank(item.risk_level), item.symbol),
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
        warnings.append("巨潮公告检索未返回匹配财报公告；可扩大日期范围或检查公开接口可用性。")
    coverage_note = _coverage_note(detail_attempted, detail_success)

    return CnEarningsScanResponse(
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
        warnings=_dedupe(warnings)[:8],
        coverage_note=coverage_note,
    )


async def diagnose_cn_earnings(
    request: CnEarningsDiagnosisRequest,
    llm_adapter: Any,
) -> CnEarningsDiagnosisResponse:
    record = request.record
    if getattr(llm_adapter, "provider", "mock") == "mock":
        return _fallback_cn_earnings_diagnosis(record, llm_adapter)

    prompt = _build_cn_earnings_diagnosis_prompt(request)
    try:
        data = await llm_adapter.complete_json(
            prompt,
            max_tokens=1800 if request.style == "brief" else 2400,
            timeout_seconds=28,
            retry_schema_hint=(
                "必须返回 overall_score, diagnosis_signal, risk_level, financial_quality, "
                "summary, verdict, agent_steps, positives, concerns, questions, actions, evidence, confidence。"
            ),
        )
        return _normalize_cn_earnings_diagnosis(data, record, llm_adapter)
    except Exception as exc:  # noqa: BLE001 - keep the diagnosis button useful in dev/offline mode
        fallback = _fallback_cn_earnings_diagnosis(record, llm_adapter)
        fallback.concerns = _dedupe([f"云模型暂不可用：{_safe_error(exc)}", *fallback.concerns])[:5]
        fallback.actions = _dedupe(["检查模型配置后重试 AI 诊断", *fallback.actions])[:5]
        fallback.confidence = min(fallback.confidence, 0.52)
        return fallback


async def enrich_cn_earnings_record_detail(
    request: CnEarningsRecordDetailRequest,
) -> CnEarningsRecordDetailResponse:
    record = request.record.model_copy(deep=True)
    warnings: list[str] = []
    if not record.url.lower().endswith(".pdf"):
        record.detail_source = "unavailable"
        warnings.append(f"{record.symbol or record.name} 财报原文不是 PDF，无法抽取明细。")
    else:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=CNINFO_HEADERS, follow_redirects=True) as client:
            try:
                variant = await _find_parseable_report_variant(client, record)
                if variant is not None:
                    _replace_record_with_variant(record, variant)
                detail = await _extract_pdf_detail(client, record)
                _apply_pdf_detail(record, detail)
            except Exception as exc:  # noqa: BLE001
                record.detail_source = "unavailable"
                record.detail_quality = "title_only"
                warnings.append(f"{record.symbol or record.name} 财报 PDF 明细解析失败：{_safe_error(exc)}")

    return CnEarningsRecordDetailResponse(
        generated_at=datetime.now(timezone.utc),
        record=record,
        warnings=_dedupe(warnings),
    )


def format_cn_earnings_skill_response(result: CnEarningsScanResponse) -> str:
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
        lines.extend(["", f"财报明细（前 {min(12, len(result.records))} 条，{parsed_hint}）："])
        for index, record in enumerate(result.records[:12], start=1):
            lines.extend(
                [
                    f"{index}. {record.announcement_date} {record.symbol} {record.name} | {_report_type_label(record.report_type)} | 风险：{record.risk_level}",
                    (
                        f"   营收/同比：{_display_value(record.revenue)} / {_display_value(record.revenue_yoy)}；"
                        f"归母净利/同比：{_display_value(record.net_profit)} / {_display_value(record.net_profit_yoy)}"
                    ),
                    (
                        f"   扣非净利：{_display_value(record.deducted_net_profit)}；"
                        f"EPS：{_display_value(record.eps)}；ROE：{_display_value(record.roe)}；经营现金流：{_display_value(record.operating_cash_flow)}"
                    ),
                    f"   公告：{record.title}",
                    f"   原文：{record.url}",
                ]
            )
            if record.key_takeaways:
                lines.append(f"   看点：{'；'.join(record.key_takeaways[:3])}")
            if record.risk_flags:
                lines.append(f"   风险：{'；'.join(record.risk_flags[:3])}")
            if record.evidence_excerpt:
                lines.append(f"   摘录：{record.evidence_excerpt}")
    else:
        lines.append("未检索到匹配财报公告。")
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
            warnings.append(f"{CNINFO_SOURCE_NAME} 查询“{keyword}”失败：{_safe_error(exc)}")
            break

        total = max(total, int(payload.get("totalAnnouncement") or payload.get("totalRecordNum") or 0))
        page_rows = payload.get("announcements") or []
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(rows) >= limit or len(page_rows) < page_size:
            break

    return rows[:limit], total, warnings


def _record_from_cninfo_row(row: dict[str, Any]) -> Optional[CnEarningsRecord]:
    title = _clean_title(str(row.get("announcementTitle") or row.get("shortTitle") or ""))
    if not _is_financial_report_title(title):
        return None
    symbol = str(row.get("secCode") or "").strip()
    name = _clean_title(str(row.get("secName") or row.get("tileSecName") or ""))
    report_type = _infer_report_type(title)
    fiscal_year, fiscal_period = _infer_fiscal_period(title, report_type)
    announcement_id = str(row.get("announcementId") or "").strip()
    announcement_date = _announcement_date(row.get("announcementTime"))
    source = str(row.get("source") or "cninfo").strip()
    source_name = str(row.get("sourceName") or CNINFO_SOURCE_NAME).strip()
    eastmoney_pdf_url = str(row.get("eastmoney_pdf_url") or "").strip()
    eastmoney_detail_url = str(row.get("eastmoney_detail_url") or "").strip()
    adjunct_url = str(row.get("adjunctUrl") or "").strip()
    url = eastmoney_pdf_url or (f"{CNINFO_STATIC_BASE}/{adjunct_url}" if adjunct_url else "https://www.cninfo.com.cn/")
    risk_flags = _risk_flags_from_text(f"{title} {name}")
    risk_level = _risk_level_from_flags(risk_flags, title)

    record = CnEarningsRecord(
        symbol=symbol,
        name=name,
        announcement_date=announcement_date,
        report_type=report_type,  # type: ignore[arg-type]
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        title=title,
        url=url,
        source=source,
        source_name=source_name,
        announcement_id=announcement_id,
        risk_level=risk_level,  # type: ignore[arg-type]
        risk_flags=risk_flags,
        tags=_tags_for_record(title, report_type, risk_level),
        metadata={
            "org_id": row.get("orgId"),
            "page_column": row.get("pageColumn"),
            "announcement_type": row.get("announcementType"),
            "adjunct_size_kb": row.get("adjunctSize"),
            "columns": row.get("columns") or [],
            "detail_url": eastmoney_detail_url,
        },
    )
    _apply_title_fallback(record)
    return record


async def _query_eastmoney_fallback(
    *,
    client: httpx.AsyncClient,
    report_types: list[str],
    start_at: date,
    end_at: date,
    limit: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    node_pairs: list[tuple[str, str]] = []
    types = set(report_types)
    if not types or types & {"annual", "semiannual", "q1", "q3", "correction", "other"}:
        node_pairs.append(("1", "1"))
    if not types or "forecast" in types:
        node_pairs.append(("1", "5"))
    if not types or "flash" in types:
        node_pairs.append(("1", "6"))

    rows_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    total = 0
    per_query_limit = max(80, limit // max(1, len(node_pairs)))
    for f_node, s_node in node_pairs:
        rows, next_total, next_warnings = await query_eastmoney_announcements(
            client=client,
            start_at=start_at,
            end_at=end_at,
            f_node=f_node,
            s_node=s_node,
            limit=per_query_limit,
        )
        total += next_total
        warnings.extend(next_warnings)
        for row in rows:
            title = str(row.get("announcementTitle") or "")
            if not _is_financial_report_title(title):
                continue
            rows_by_id.setdefault(str(row.get("announcementId") or f"{row.get('secCode')}:{title}"), row)
    return list(rows_by_id.values())[:limit], total, warnings


async def _enrich_records_with_pdf_details(
    *,
    client: httpx.AsyncClient,
    records: list[CnEarningsRecord],
    warnings: list[str],
) -> tuple[int, int]:
    attempted = 0
    success = 0
    for record in records:
        if not record.url.lower().endswith(".pdf"):
            continue
        attempted += 1
        try:
            variant = await _find_parseable_report_variant(client, record)
            if variant is not None:
                _replace_record_with_variant(record, variant)
            detail = await _extract_pdf_detail(client, record)
        except Exception as exc:  # noqa: BLE001
            record.detail_source = "unavailable"
            warnings.append(f"{record.symbol or record.name} 财报 PDF 明细解析失败：{_safe_error(exc)}")
            continue
        _apply_pdf_detail(record, detail)
        if record.detail_quality != "title_only":
            success += 1
    return attempted, success


async def _extract_pdf_detail(client: httpx.AsyncClient, record: CnEarningsRecord) -> dict[str, Any]:
    declared_size_kb = _adjunct_size_kb(record)
    if declared_size_kb and declared_size_kb * 1024 > PDF_DETAIL_MAX_BYTES:
        raise ValueError(
            f"PDF 文件超过解析上限（{declared_size_kb / 1024:.1f}MB > {PDF_DETAIL_MAX_BYTES / 1024 / 1024:.0f}MB）"
        )
    headers = EASTMONEY_HEADERS if record.source == "eastmoney" or "dfcfw.com" in record.url else None
    response = await client.get(record.url, headers=headers)
    response.raise_for_status()
    raw = response.content
    if len(raw) > PDF_DETAIL_MAX_BYTES:
        raise ValueError(
            f"PDF 文件超过解析上限（{len(raw) / 1024 / 1024:.1f}MB > {PDF_DETAIL_MAX_BYTES / 1024 / 1024:.0f}MB）"
        )
    text = _extract_pdf_text(raw)
    return _extract_report_detail_from_text(text, record)


async def _find_parseable_report_variant(
    client: httpx.AsyncClient,
    record: CnEarningsRecord,
) -> Optional[CnEarningsRecord]:
    if not re.search(r"图文版|可视化", record.title):
        return None
    declared_size_kb = _adjunct_size_kb(record)
    if not declared_size_kb or declared_size_kb * 1024 <= PDF_DETAIL_MAX_BYTES:
        return None

    end_at = _parse_date(record.announcement_date) or datetime.now(CN_TZ).date()
    start_at = end_at - timedelta(days=75)
    rows, _, _ = await _query_cninfo_keyword(
        client=client,
        keyword=_canonical_report_title(record.title),
        start_at=start_at,
        end_at=end_at,
        limit=40,
    )
    variants = []
    for row in rows:
        variant = _record_from_cninfo_row(row)
        if variant is None:
            continue
        if variant.symbol != record.symbol or variant.fiscal_year != record.fiscal_year or variant.report_type != record.report_type:
            continue
        if re.search(r"图文版|可视化", variant.title):
            continue
        if not variant.url.lower().endswith(".pdf"):
            continue
        variant_size_kb = _adjunct_size_kb(variant)
        if variant_size_kb and variant_size_kb * 1024 > PDF_DETAIL_MAX_BYTES:
            continue
        variants.append(variant)
    if not variants:
        return None
    return max(variants, key=_record_preference)


def _replace_record_with_variant(record: CnEarningsRecord, variant: CnEarningsRecord) -> None:
    original = {
        "announcement_id": record.announcement_id,
        "announcement_date": record.announcement_date,
        "title": record.title,
        "url": record.url,
        "adjunct_size_kb": record.metadata.get("adjunct_size_kb"),
    }
    for field in (
        "announcement_id",
        "announcement_date",
        "title",
        "url",
        "source",
        "source_name",
        "metadata",
        "tags",
    ):
        setattr(record, field, getattr(variant, field))
    record.metadata = {
        **variant.metadata,
        "replaced_visual_variant": original,
    }


def _extract_pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for page in reader.pages[:PDF_DETAIL_MAX_PAGES]:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text)
    return _compact_pdf_text("\n".join(pages))[:PDF_DETAIL_MAX_CHARS]


def _extract_report_detail_from_text(text: str, record: CnEarningsRecord) -> dict[str, Any]:
    revenue, revenue_yoy = _metric_pair(text, "营业收入")
    net_profit, net_profit_yoy = _metric_pair(text, "归属于上市公司股东的净利润")
    deducted_net_profit, deducted_net_profit_yoy = _metric_pair(text, "归属于上市公司股东的扣除非经常性损益的净利润")
    operating_cash_flow, _ = _metric_pair(text, "经营活动产生的现金流量净额")
    total_assets, _ = _metric_pair(text, "总资产")
    eps = _single_metric(text, "基本每股收益")
    roe = _single_metric(text, "加权平均净资产收益率")
    gross_margin = _gross_margin(text)
    risk_flags = _dedupe([*record.risk_flags, *_risk_flags_from_text(text)])
    key_takeaways = _build_key_takeaways(
        revenue=revenue,
        revenue_yoy=revenue_yoy,
        net_profit=net_profit,
        net_profit_yoy=net_profit_yoy,
        eps=eps,
        roe=roe,
        gross_margin=gross_margin,
    )
    evidence_excerpt = _extract_evidence_excerpt(text)
    quality = _detail_quality(
        revenue=revenue,
        net_profit=net_profit,
        eps=eps,
        roe=roe,
        operating_cash_flow=operating_cash_flow,
    )
    return {
        "revenue": revenue,
        "revenue_yoy": revenue_yoy,
        "net_profit": net_profit,
        "net_profit_yoy": net_profit_yoy,
        "deducted_net_profit": deducted_net_profit,
        "deducted_net_profit_yoy": deducted_net_profit_yoy,
        "eps": eps,
        "roe": roe,
        "gross_margin": gross_margin,
        "operating_cash_flow": operating_cash_flow,
        "total_assets": total_assets,
        "key_takeaways": key_takeaways,
        "risk_flags": risk_flags,
        "evidence_excerpt": evidence_excerpt,
        "detail_source": "pdf",
        "detail_quality": quality,
        "risk_level": _risk_level_from_flags(risk_flags, record.title),
    }


def _apply_pdf_detail(record: CnEarningsRecord, detail: dict[str, Any]) -> None:
    for field, value in detail.items():
        if field in {"detail_source", "detail_quality", "risk_level"}:
            if value and (value != "title_only" or record.detail_quality == "title_only"):
                setattr(record, field, value)
            continue
        if value:
            setattr(record, field, value)
    record.tags = _dedupe([*record.tags, *record.risk_flags])
    _apply_title_fallback(record)


def _build_cn_earnings_diagnosis_prompt(request: CnEarningsDiagnosisRequest) -> str:
    record = request.record
    payload = {
        "record": record.model_dump(),
        "user_question": request.question or "",
        "style": request.style,
        "visible_agents": [
            {
                "agent": "EarningsReviewer",
                "role": "核对财报类型、报告期和收入/利润/EPS/ROE变化，只基于输入字段下结论。",
            },
            {
                "agent": "QualityAgent",
                "role": "判断利润质量、经营现金流、毛利率和资产规模，指出可持续性线索。",
            },
            {
                "agent": "RiskForensicsAgent",
                "role": "识别亏损、同比下滑、非标、ST/退市、更正和字段缺失等风险。",
            },
            {
                "agent": "ActionAgent",
                "role": "给出后续核验动作、需要打开原文确认的问题和观察清单。",
            },
        ],
    }
    return (
        "你是 DeepFocus 的 A 股财报 AI 诊断编排器。只能基于输入的结构化字段、PDF 摘录和公告链接诊断，"
        "不得编造估值、目标价、行业排名、交易建议或未给出的历史数据。"
        "如果字段为空，要把它作为证据缺口处理。\n"
        "请模拟 4 个可见 Agent 协作：EarningsReviewer、QualityAgent、RiskForensicsAgent、ActionAgent。"
        "最终返回严格 JSON object，字段必须为："
        "overall_score, diagnosis_signal, risk_level, financial_quality, summary, verdict, agent_steps, "
        "positives, concerns, questions, actions, evidence, confidence。"
        "overall_score 是 0-100 整数；diagnosis_signal 只能是 positive/neutral/negative/watch；"
        "risk_level 只能是 low/medium/high；financial_quality 只能是 strong/stable/mixed/weak/unknown；"
        "agent_steps 每项包含 agent, role, finding, status，status 只能是 done/watch/risk；"
        "summary 不超过 90 个中文字符；verdict 不超过 44 个中文字符；数组最多 4 项，每项不超过 26 个中文字符；"
        "evidence 只放输入中真实出现的指标或原文链接。\n"
        '格式示例：{"overall_score":66,"diagnosis_signal":"watch","risk_level":"medium",'
        '"financial_quality":"mixed","summary":"...","verdict":"...","agent_steps":[{"agent":"EarningsReviewer",'
        '"role":"指标复核","finding":"...","status":"done"}],"positives":["..."],"concerns":["..."],'
        '"questions":["..."],"actions":["..."],"evidence":["..."],"confidence":0.68}\n'
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def _normalize_cn_earnings_diagnosis(
    data: dict[str, Any],
    record: CnEarningsRecord,
    llm_adapter: Any,
) -> CnEarningsDiagnosisResponse:
    score = _safe_int(data.get("overall_score") or data.get("score"), default=50, low=0, high=100)
    signal = _safe_diagnosis_signal(data.get("diagnosis_signal") or data.get("signal"))
    risk_level = _safe_cn_risk(data.get("risk_level"), default=record.risk_level)
    financial_quality = _safe_financial_quality(data.get("financial_quality") or data.get("quality"))
    agent_steps = _safe_agent_steps(data.get("agent_steps") or data.get("agents"))
    if not agent_steps:
        agent_steps = _default_agent_steps(record)

    return CnEarningsDiagnosisResponse(
        provider=getattr(llm_adapter, "provider_name", "local"),
        model=getattr(llm_adapter, "model", "cn-earnings-diagnosis-local"),
        generated_at=datetime.now(timezone.utc),
        symbol=record.symbol,
        name=record.name,
        title=record.title,
        report_type=record.report_type,
        diagnosis_signal=signal,
        risk_level=risk_level,
        financial_quality=financial_quality,
        overall_score=score,
        summary=_short_text(data.get("summary") or data.get("diagnosis") or _fallback_summary(record), 120),
        verdict=_short_text(data.get("verdict") or _fallback_verdict(record), 60),
        agent_steps=agent_steps[:4],
        positives=_safe_text_list(data.get("positives") or data.get("strengths") or data.get("opportunities"))[:4],
        concerns=_safe_text_list(data.get("concerns") or data.get("risks"))[:4],
        questions=_safe_text_list(data.get("questions") or data.get("watch_questions"))[:4],
        actions=_safe_text_list(data.get("actions") or data.get("next_steps"))[:4],
        evidence=_safe_text_list(data.get("evidence") or data.get("sources"))[:5],
        confidence=_safe_float(data.get("confidence"), default=0.66, low=0, high=1),
    )


def _fallback_cn_earnings_diagnosis(
    record: CnEarningsRecord,
    llm_adapter: Any,
) -> CnEarningsDiagnosisResponse:
    positives = _fallback_positives(record)
    concerns = _fallback_concerns(record)
    questions = _fallback_questions(record)
    actions = _fallback_actions(record)
    score = _fallback_score(record, positives, concerns)
    signal = _fallback_signal(record, score, concerns)

    return CnEarningsDiagnosisResponse(
        provider=getattr(llm_adapter, "provider_name", "local-rules"),
        model=getattr(llm_adapter, "model", "cn-earnings-diagnosis-local"),
        generated_at=datetime.now(timezone.utc),
        symbol=record.symbol,
        name=record.name,
        title=record.title,
        report_type=record.report_type,
        diagnosis_signal=signal,
        risk_level=record.risk_level,
        financial_quality=_fallback_financial_quality(record),
        overall_score=score,
        summary=_fallback_summary(record),
        verdict=_fallback_verdict(record),
        agent_steps=_default_agent_steps(record),
        positives=positives,
        concerns=concerns,
        questions=questions,
        actions=actions,
        evidence=_fallback_evidence(record),
        confidence=0.58 if record.detail_quality != "title_only" else 0.42,
    )


def _default_agent_steps(record: CnEarningsRecord) -> list[CnEarningsDiagnosisAgentStep]:
    return [
        CnEarningsDiagnosisAgentStep(
            agent="EarningsReviewer",
            role="指标复核",
            finding=_short_text("; ".join(record.key_takeaways) or _fallback_metric_line(record), 80),
            status="done" if record.detail_quality != "title_only" else "watch",
        ),
        CnEarningsDiagnosisAgentStep(
            agent="QualityAgent",
            role="质量判断",
            finding=_short_text(_quality_finding(record), 80),
            status="done" if record.operating_cash_flow or record.roe or record.gross_margin else "watch",
        ),
        CnEarningsDiagnosisAgentStep(
            agent="RiskForensicsAgent",
            role="风险扫描",
            finding=_short_text("; ".join(record.risk_flags) or "未从标题和已抽取字段识别重大风险词", 80),
            status="risk" if record.risk_level == "high" else "watch" if record.risk_flags else "done",
        ),
        CnEarningsDiagnosisAgentStep(
            agent="ActionAgent",
            role="后续动作",
            finding=_short_text(_fallback_actions(record)[0], 80),
            status="watch",
        ),
    ]


def _fallback_score(record: CnEarningsRecord, positives: list[str], concerns: list[str]) -> int:
    score = 58
    if record.detail_quality == "full":
        score += 10
    elif record.detail_quality == "partial":
        score += 4
    else:
        score -= 8
    if record.risk_level == "high":
        score -= 20
    elif record.risk_level == "low":
        score += 6
    score += min(len(positives), 3) * 4
    score -= min(len(concerns), 4) * 5
    for value in (record.revenue_yoy, record.net_profit_yoy, record.deducted_net_profit_yoy):
        direction = _metric_direction(value)
        if direction > 0:
            score += 3
        elif direction < 0:
            score -= 5
    return max(0, min(100, score))


def _fallback_signal(record: CnEarningsRecord, score: int, concerns: list[str]) -> str:
    if record.risk_level == "high" or score < 42:
        return "negative"
    if concerns or score < 62:
        return "watch"
    if score >= 72:
        return "positive"
    return "neutral"


def _fallback_financial_quality(record: CnEarningsRecord) -> str:
    if record.detail_quality == "title_only":
        return "unknown"
    if record.risk_level == "high":
        return "weak"
    has_cash = bool(record.operating_cash_flow)
    has_profit = bool(record.net_profit)
    if has_cash and has_profit and not record.risk_flags:
        return "stable"
    if has_cash or has_profit or record.roe:
        return "mixed"
    return "unknown"


def _fallback_summary(record: CnEarningsRecord) -> str:
    metric_line = _fallback_metric_line(record)
    risk_line = f"风险标签：{'、'.join(record.risk_flags[:3])}" if record.risk_flags else "暂未识别重大风险标签"
    return _short_text(f"{record.name}{_report_type_label(record.report_type)}已抽取{record.detail_quality}明细，{metric_line}，{risk_line}。", 110)


def _fallback_verdict(record: CnEarningsRecord) -> str:
    if record.risk_level == "high":
        return "先核验风险，再判断业绩质量"
    if record.detail_quality == "title_only":
        return "明细不足，需打开原文复核"
    if record.net_profit or record.revenue:
        return "可进入指标复核和同业对比"
    return "信息有限，保持观察"


def _fallback_metric_line(record: CnEarningsRecord) -> str:
    values = [
        f"营收{record.revenue}" if record.revenue else "",
        f"归母净利{record.net_profit}" if record.net_profit else "",
        f"EPS{record.eps}" if record.eps else "",
        f"ROE{record.roe}" if record.roe else "",
    ]
    return "、".join(item for item in values if item) or "核心财务字段不足"


def _quality_finding(record: CnEarningsRecord) -> str:
    values = [
        f"经营现金流{record.operating_cash_flow}" if record.operating_cash_flow else "",
        f"毛利率{record.gross_margin}" if record.gross_margin else "",
        f"总资产{record.total_assets}" if record.total_assets else "",
    ]
    return "；".join(item for item in values if item) or "现金流、毛利率或资产字段未完整抽取"


def _fallback_positives(record: CnEarningsRecord) -> list[str]:
    positives = list(record.key_takeaways[:3])
    if _metric_direction(record.revenue_yoy) > 0:
        positives.append(f"营业收入同比{record.revenue_yoy}")
    if _metric_direction(record.net_profit_yoy) > 0:
        positives.append(f"归母净利同比{record.net_profit_yoy}")
    if record.detail_quality == "full":
        positives.append("PDF 明细抽取完整")
    return _dedupe(positives)[:4]


def _fallback_concerns(record: CnEarningsRecord) -> list[str]:
    concerns = list(record.risk_flags[:3])
    if _metric_direction(record.revenue_yoy) < 0:
        concerns.append(f"营业收入同比{record.revenue_yoy}")
    if _metric_direction(record.net_profit_yoy) < 0:
        concerns.append(f"归母净利同比{record.net_profit_yoy}")
    if record.detail_quality == "title_only":
        concerns.append("未抽取到 PDF 明细")
    elif record.detail_quality == "partial":
        concerns.append("部分指标仍缺失")
    return _dedupe(concerns)[:4]


def _fallback_questions(record: CnEarningsRecord) -> list[str]:
    questions = [
        "收入与利润变化是否同向",
        "经营现金流能否覆盖利润",
        "扣非净利与归母净利差异来源",
    ]
    if record.risk_flags:
        questions.insert(0, f"风险标签是否被原文确认：{record.risk_flags[0]}")
    if record.detail_quality != "full":
        questions.insert(0, "缺失指标是否在 PDF 后续页披露")
    return _dedupe(questions)[:4]


def _fallback_actions(record: CnEarningsRecord) -> list[str]:
    actions = [
        "打开原文核验关键指标",
        "补充历史同期数据做趋势对比",
        "对比同业 ROE 和现金流质量",
    ]
    if record.risk_level == "high":
        actions.insert(0, "优先核验亏损/非标/ST 风险")
    return _dedupe(actions)[:4]


def _fallback_evidence(record: CnEarningsRecord) -> list[str]:
    evidence = [
        f"公告：{record.title}",
        f"营收：{record.revenue}" if record.revenue else "",
        f"归母净利：{record.net_profit}" if record.net_profit else "",
        f"经营现金流：{record.operating_cash_flow}" if record.operating_cash_flow else "",
        record.evidence_excerpt,
        record.url,
    ]
    return _dedupe([item for item in evidence if item])[:5]


def _metric_direction(value: str) -> int:
    text = (value or "").strip()
    if not text or "不适用" in text:
        return 0
    if re.search(r"下降|减少|-", text):
        return -1
    if re.search(r"增长|增加|\+", text):
        return 1
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    if number > 0:
        return 1
    if number < 0:
        return -1
    return 0


def _safe_agent_steps(value: Any) -> list[CnEarningsDiagnosisAgentStep]:
    if not isinstance(value, list):
        return []
    steps: list[CnEarningsDiagnosisAgentStep] = []
    for item in value:
        if isinstance(item, dict):
            agent = _short_text(item.get("agent") or item.get("name") or "Agent", 32)
            role = _short_text(item.get("role") or item.get("title") or "诊断", 40)
            finding = _short_text(item.get("finding") or item.get("detail") or item.get("summary") or "", 90)
            status = str(item.get("status") or "done")
        else:
            agent = "Agent"
            role = "诊断"
            finding = _short_text(item, 90)
            status = "done"
        if not finding:
            continue
        if status not in {"done", "watch", "risk"}:
            status = "done"
        steps.append(CnEarningsDiagnosisAgentStep(agent=agent, role=role, finding=finding, status=status))  # type: ignore[arg-type]
    return steps


def _safe_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = re.split(r"[；;\n]", value)
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = []
    return _dedupe([_short_text(item, 80) for item in raw_values if str(item or "").strip()])


def _safe_diagnosis_signal(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "positive": "positive",
        "bullish": "positive",
        "neutral": "neutral",
        "negative": "negative",
        "bearish": "negative",
        "watch": "watch",
        "observe": "watch",
    }
    return mapping.get(text, "neutral")


def _safe_cn_risk(value: Any, default: str = "medium") -> str:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high"}:
        return text
    return default if default in {"low", "medium", "high"} else "medium"


def _safe_financial_quality(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"strong", "stable", "mixed", "weak", "unknown"}:
        return text
    return "unknown"


def _safe_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _safe_float(value: Any, *, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _short_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _is_financial_report_title(title: str) -> bool:
    if not re.search(r"年度报告|半年度报告|第一季度报告|第三季度报告|一季度报告|三季度报告|业绩预告|业绩快报|业绩预增|业绩预减", title):
        return False
    if re.search(r"提示性公告|更正公告|关于.*(?:年度报告|半年度报告|季度报告|一季报|三季报).*更正", title):
        return False
    if re.search(r"英文|摘要|取消|已取消|持续督导|核查意见|审计报告|鉴证报告|募集资金|社会责任|ESG|环境、社会|可持续|监管工作函|问询函|回复|业绩说明会|说明会", title):
        return False
    return True


def _infer_report_type(title: str) -> str:
    if "业绩预告" in title or "业绩预增" in title or "业绩预减" in title:
        return "forecast"
    if "业绩快报" in title:
        return "flash"
    if re.search(r"更正|修正|补充", title):
        return "correction"
    if "半年度报告" in title:
        return "semiannual"
    if re.search(r"第一季度报告|一季度报告", title):
        return "q1"
    if re.search(r"第三季度报告|三季度报告", title):
        return "q3"
    if "年度报告" in title:
        return "annual"
    return "other"


def _infer_fiscal_period(title: str, report_type: str) -> tuple[str, str]:
    year_match = re.search(r"(20\d{2})\s*年?", title)
    fiscal_year = year_match.group(1) if year_match else ""
    labels = {
        "annual": "年度",
        "semiannual": "半年度",
        "q1": "第一季度",
        "q3": "第三季度",
        "forecast": "业绩预告",
        "flash": "业绩快报",
        "correction": "更正/修正",
        "other": "财报公告",
    }
    return fiscal_year, labels.get(report_type, "财报公告")


def _keywords_for_report_types(report_types: list[str]) -> list[str]:
    mapping = {
        "annual": ["年度报告"],
        "semiannual": ["半年度报告"],
        "q1": ["第一季度报告"],
        "q3": ["第三季度报告"],
        "forecast": ["业绩预告"],
        "flash": ["业绩快报"],
        "correction": ["更正"],
    }
    if report_types:
        keywords = [keyword for report_type in report_types for keyword in mapping.get(report_type, [])]
        return _dedupe(keywords) or ["年度报告", "半年度报告", "第一季度报告", "第三季度报告", "业绩预告", "业绩快报"]
    return ["年度报告", "半年度报告", "第一季度报告", "第三季度报告", "业绩预告", "业绩快报"]


def _metric_pair(text: str, label: str) -> tuple[str, str]:
    normalized_label = _metric_label_pattern(label)
    unit_suffix = r"(?:（[^）]*）|\([^)]*\))?"
    match = re.search(
        rf"{normalized_label}{unit_suffix}\s*([+-]?\d[\d,，.]*)(?:\s*万元|\s*元|\s*千元)?\s+([+-]?\d[\d,，.]*|不适用|--?)\s+([+-]?\d+(?:\.\d+)?%?|不适用|--?|增加\d+(?:\.\d+)?个百分点|减少\d+(?:\.\d+)?个百分点)",
        text,
    )
    if match:
        return _clean_number(match.group(1)), _clean_percent(match.group(3))
    compact_match = re.search(rf"{normalized_label}.{{0,24}}?([+-]?\d[\d,，.]*\s*(?:万|亿)?元).{{0,36}}?(同比(?:增长|下降|增加|减少)?\s*[+-]?\d+(?:\.\d+)?%)", text)
    if compact_match:
        return _clean_metric_value(compact_match.group(1)), _clean_percent(compact_match.group(2))
    return "", ""


def _single_metric(text: str, label: str) -> str:
    normalized_label = _metric_label_pattern(label)
    match = re.search(rf"{normalized_label}(?:（[^）]*）|\([^)]*\))?\s*([+-]?\d+(?:\.\d+)?%?)", text)
    if match:
        return _clean_metric_value(match.group(1))
    return ""


def _gross_margin(text: str) -> str:
    match = re.search(r"毛利率\s*(?:为|：|:)?\s*([+-]?\d+(?:\.\d+)?%)", text)
    return _clean_metric_value(match.group(1)) if match else ""


def _metric_label_pattern(label: str) -> str:
    chars = [re.escape(char) for char in label]
    return r"\s*".join(chars)


def _build_key_takeaways(
    *,
    revenue: str,
    revenue_yoy: str,
    net_profit: str,
    net_profit_yoy: str,
    eps: str,
    roe: str,
    gross_margin: str,
) -> list[str]:
    takeaways = [
        f"营业收入 {revenue}，同比 {revenue_yoy}" if revenue or revenue_yoy else "",
        f"归母净利润 {net_profit}，同比 {net_profit_yoy}" if net_profit or net_profit_yoy else "",
        f"EPS {eps}" if eps else "",
        f"ROE {roe}" if roe else "",
        f"毛利率 {gross_margin}" if gross_margin else "",
    ]
    return [item for item in takeaways if item][:5]


def _risk_flags_from_text(text: str) -> list[str]:
    flags: list[str] = []
    checks = [
        (r"净亏损|预计亏损|业绩亏损|出现亏损|亏损\d", "亏损"),
        (r"下降|减少|下滑", "同比下降"),
        (r"更正|修正|补充", "更正/修正"),
        (r"非标准|非标|无法表示意见|否定意见", "审计意见风险"),
        (r"退市|终止上市|\*ST|(?:^|[^A-Za-z])ST(?:[^A-Za-z]|$)", "退市/ST风险"),
    ]
    for pattern, label in checks:
        if re.search(pattern, text, re.I):
            flags.append(label)
    return _dedupe(flags)


def _risk_level_from_flags(flags: list[str], title: str) -> str:
    if any(flag in {"亏损", "审计意见风险", "退市/ST风险"} for flag in flags):
        return "high"
    if flags or re.search(r"业绩预告|业绩快报|更正|修正", title):
        return "medium"
    return "low"


def _detail_quality(**metrics: str) -> str:
    filled = sum(bool(value) for value in metrics.values())
    if filled >= 4:
        return "full"
    if filled:
        return "partial"
    return "title_only"


def _extract_evidence_excerpt(text: str) -> str:
    for keyword in ["主要财务数据", "主要会计数据和财务指标", "重要内容提示", "业绩预告", "业绩快报"]:
        index = text.find(keyword)
        if index >= 0:
            start = max(0, index - 24)
            return _trim_excerpt(text[start : index + 260])
    return ""


def _compact_pdf_text(text: str) -> str:
    compact = (
        text.replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("□", " □")
        .replace("√", " √")
    )
    for before, after in {
        "营 业 收 入": "营业收入",
        "净 利 润": "净利润",
        "每 股 收 益": "每股收益",
    }.items():
        compact = compact.replace(before, after)
    compact = re.sub(r"\s+", " ", compact)
    compact = re.sub(r"(?<=\.\d{2})(?=\d{1,3},)", " ", compact)
    return compact.strip()


def _date_range(request: CnEarningsScanRequest) -> tuple[date, date]:
    end_at = _parse_date(request.end_date) or datetime.now(CN_TZ).date()
    start_at = _parse_date(request.start_date) or (end_at - timedelta(days=request.days - 1))
    if start_at > end_at:
        start_at, end_at = end_at, start_at
    return start_at, end_at


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _announcement_date(value: Any) -> str:
    try:
        timestamp = int(value) / 1000
        return datetime.fromtimestamp(timestamp, CN_TZ).date().isoformat()
    except Exception:
        return datetime.now(CN_TZ).date().isoformat()


def _dedupe_key(record: CnEarningsRecord) -> str:
    return f"{record.symbol}:{record.fiscal_year}:{record.report_type}:{_canonical_report_title(record.title)}"


def _record_preference(record: CnEarningsRecord) -> int:
    score = 10
    if "摘要" in record.title:
        score -= 4
    if re.search(r"图文版|可视化", record.title):
        score -= 6
    declared_size_kb = _adjunct_size_kb(record)
    if declared_size_kb and declared_size_kb * 1024 > PDF_DETAIL_MAX_BYTES:
        score -= 5
    if record.url.lower().endswith(".pdf"):
        score += 2
    if record.report_type in {"annual", "semiannual", "q1", "q3"}:
        score += 2
    return score


def _canonical_report_title(title: str) -> str:
    canonical = re.sub(r"[（(]\s*(?:图文版|可视化版)\s*[）)]", "", title)
    canonical = canonical.replace("摘要", "")
    return re.sub(r"\s+", "", canonical)


def _adjunct_size_kb(record: CnEarningsRecord) -> Optional[float]:
    value = record.metadata.get("adjunct_size_kb")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tags_for_record(title: str, report_type: str, risk_level: str) -> list[str]:
    tags = [_report_type_label(report_type), f"{risk_level}风险"]
    if "业绩预告" in title:
        tags.append("预告")
    if "业绩快报" in title:
        tags.append("快报")
    return _dedupe(tags)


def _apply_title_fallback(record: CnEarningsRecord) -> None:
    if not record.key_takeaways:
        period = _join_nonempty([record.fiscal_year, record.fiscal_period], separator="")
        record.key_takeaways = _dedupe(
            [
                f"{period or _report_type_label(record.report_type)}公告已命中",
                "核心财务指标需打开原文核验" if record.detail_quality == "title_only" else "",
            ]
        )
    if not record.evidence_excerpt:
        record.evidence_excerpt = record.title
    if not record.risk_flags:
        record.risk_flags = _risk_flags_from_text(record.title)
    record.tags = _dedupe([*record.tags, *record.risk_flags])


def _build_summary(records: list[CnEarningsRecord], start_at: date, end_at: date) -> str:
    counts = {report_type: 0 for report_type in ["annual", "semiannual", "q1", "q3", "forecast", "flash", "correction", "other"]}
    for record in records:
        counts[record.report_type] = counts.get(record.report_type, 0) + 1
    high_risk = sum(1 for record in records if record.risk_level == "high")
    return (
        f"{start_at.isoformat()} 至 {end_at.isoformat()} 共命中 {len(records)} 条 A 股财报相关公告；"
        f"年报 {counts['annual']} 条、半年报 {counts['semiannual']} 条、一季报 {counts['q1']} 条、"
        f"三季报 {counts['q3']} 条、业绩预告 {counts['forecast']} 条、业绩快报 {counts['flash']} 条，"
        f"高风险 {high_risk} 条。"
    )


def _coverage_note(detail_attempted: int, detail_success: int) -> str:
    if detail_attempted:
        return (
            f"来源为巨潮资讯网公告检索；已对前 {detail_attempted} 条财报公告抓取 PDF 正文抽取字段，"
            f"其中 {detail_success} 条抽取到可用明细。字段为空代表公告未披露或版式未识别，原文链接保留用于核验。"
        )
    return "来源为巨潮资讯网公告检索；当前仅完成公告标题扫描，未抓取 PDF 正文明细。"


def _report_type_label(report_type: str) -> str:
    return {
        "annual": "年报",
        "semiannual": "半年报",
        "q1": "一季报",
        "q3": "三季报",
        "forecast": "业绩预告",
        "flash": "业绩快报",
        "correction": "更正/修正",
        "other": "财报公告",
    }.get(report_type, "财报公告")


def _risk_rank(level: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(level, 0)


def _clean_title(value: str) -> str:
    without_tags = re.sub(r"</?em>", "", value)
    return unescape(without_tags).replace("\u3000", " ").strip()


def _clean_number(value: str) -> str:
    return value.replace("，", ",").strip()


def _clean_percent(value: str) -> str:
    clean = _clean_metric_value(value)
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", clean):
        return f"{clean}%"
    return clean


def _clean_metric_value(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").replace("，", ",")).strip(" ：:，,；;。")


def _trim_excerpt(value: str) -> str:
    excerpt = re.sub(r"\s+", " ", value).strip(" ：:，,；;。")
    if len(excerpt) > 240:
        excerpt = f"{excerpt[:240]}..."
    return excerpt


def _display_value(value: str) -> str:
    return value.strip() if value and value.strip() else "未识别"


def _join_nonempty(values: list[str], *, separator: str) -> str:
    return separator.join(value for value in values if value)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    text = str(exc).strip()
    return text[:240] or exc.__class__.__name__
