from __future__ import annotations

import asyncio
import copy
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from .eastmoney_announcements import EASTMONEY_HEADERS, EASTMONEY_SOURCE_NAME, query_eastmoney_announcements
from .shared_utils import display_value, join_nonempty, parse_date, clean_title, to_float, dedupe, safe_float, short_text, safe_error
from .schemas import (

    ShareholderChangeInterpretRequest,
    ShareholderChangeInterpretResponse,
    ShareholderChangeRecord,
    ShareholderChangeScanRequest,
    ShareholderChangeScanResponse,
)


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn"
CNINFO_SOURCE_NAME = "巨潮资讯网"
HKEX_SOURCE_NAME = "HKEX Disclosure of Interests"
SEC_SOURCE_NAME = "SEC EDGAR Form 4"
REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_CONCURRENT_QUERIES = 4
MAX_PAGES_PER_KEYWORD = 8
PDF_DETAIL_MAX_BYTES = 10 * 1024 * 1024
PDF_DETAIL_MAX_PAGES = 6
PDF_DETAIL_MAX_CHARS = 36_000
A_SHARE_PDF_DETAIL_LIMIT = 50
US_FORM4_XML_DETAIL_LIMIT = 120
US_FORM4_CACHE_TTL_SECONDS = 10 * 60
US_FORM4_STALE_CACHE_SECONDS = 6 * 60 * 60
SEC_REQUEST_DELAY_SECONDS = 0.15
SEC_RETRY_BACKOFF_SECONDS = (2.0, 5.0)
CN_TZ = ZoneInfo("Asia/Shanghai")
US_FORM4_SCAN_CACHE: dict[str, tuple[datetime, ShareholderChangeScanResponse]] = {}

CNINFO_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord=%E5%A2%9E%E6%8C%81",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

HKEX_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "User-Agent": CNINFO_HEADERS["User-Agent"],
}

SEC_HEADERS = {
    "Accept": "application/atom+xml,application/xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": os.getenv("DEEPFOCUS_SEC_USER_AGENT", "DeepFocus research workspace contact@example.com"),
}


def detect_shareholder_change_request(message: str) -> Optional[ShareholderChangeScanRequest]:
    text = (message or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", "", text)
    has_change_keyword = bool(
        re.search(r"增减持|增持|减持|持股变动|股份变动|股东变动|内部人交易|insider|form4|form-4|form 4", compact, re.I)
    )
    has_scope_keyword = bool(
        re.search(r"A股|全A|沪深|港股|港交所|香港|H股|美股|美国|SEC|EDGAR|全市场|上市公司|所有|全部|市场", compact, re.I)
    )
    if not has_change_keyword or not has_scope_keyword:
        return None

    market = "A"
    if re.search(r"港股|港交所|香港|H股|HK", compact, re.I):
        market = "HK"
    elif re.search(r"美股|美国|SEC|EDGAR|insider|form4|form-4|form 4", compact, re.I):
        market = "US"

    direction = "all"
    if "减持" in compact and "增持" not in compact and "增减持" not in compact:
        direction = "decrease"
    elif "增持" in compact and "减持" not in compact and "增减持" not in compact:
        direction = "increase"

    days = 30
    day_match = re.search(r"(?:近|最近|过去)(\d{1,2})(?:天|日)", compact)
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

    limit = 80
    limit_match = re.search(r"(?:前|top|Top|TOP)(\d{1,3})", text)
    if limit_match:
        limit = int(limit_match.group(1))

    return ShareholderChangeScanRequest(
        market=market,  # type: ignore[arg-type]
        days=max(1, min(days, 180)),
        direction=direction,  # type: ignore[arg-type]
        limit=max(1, min(limit, 200)),
    )


async def scan_shareholder_changes(request: ShareholderChangeScanRequest) -> ShareholderChangeScanResponse:
    if request.market == "HK":
        return await _scan_hk_shareholder_changes(request)
    if request.market == "US":
        return await _scan_us_shareholder_changes(request)

    start_at, end_at = _date_range(request)
    keywords = _keywords_for_direction(request.direction)
    warnings: list[str] = []
    records_by_id: dict[str, ShareholderChangeRecord] = {}
    raw_total = 0

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=CNINFO_HEADERS, follow_redirects=True) as client:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

        async def query_keyword(keyword: str) -> tuple[list[dict[str, Any]], int, list[str]]:
            async with semaphore:
                return await _query_cninfo_keyword(
                    client=client,
                    keyword=keyword,
                    start_at=start_at,
                    end_at=end_at,
                    limit=max(request.limit * 2, 240),
                )

        # 多方向关键词并发查询（gather 保序，setdefault 仍保留首个关键词命中，语义不变）
        query_results = await asyncio.gather(*(query_keyword(keyword) for keyword in keywords))
        for rows, total, provider_warnings in query_results:
            raw_total += total
            warnings.extend(provider_warnings)
            for row in rows:
                record = _record_from_cninfo_row(row)
                if record is None:
                    continue
                if request.direction == "increase" and record.direction not in {"increase", "mixed"}:
                    continue
                if request.direction == "decrease" and record.direction not in {"decrease", "mixed"}:
                    continue
                key = record.announcement_id or f"{record.symbol}:{record.title}:{record.announcement_date}"
                records_by_id.setdefault(key, record)

        if not records_by_id:
            rows, total, provider_warnings = await _query_eastmoney_fallback(
                client=client,
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
                if request.direction == "increase" and record.direction not in {"increase", "mixed"}:
                    continue
                if request.direction == "decrease" and record.direction not in {"decrease", "mixed"}:
                    continue
                key = record.announcement_id or f"{record.symbol}:{record.title}:{record.announcement_date}"
                records_by_id.setdefault(key, record)

        records = sorted(records_by_id.values(), key=_default_record_sort_key)
        limited_records = records[: request.limit]
        detail_attempted, detail_success = await _enrich_records_with_pdf_details(
            client=client,
            records=limited_records[: min(request.detail_limit, A_SHARE_PDF_DETAIL_LIMIT)],
            warnings=warnings,
        )

    summary = _build_summary(records, request.direction, start_at, end_at, request.market)
    if not records and not warnings:
        warnings.append("巨潮公告检索未返回匹配公告；可扩大日期范围或检查公开接口可用性。")
    coverage_note = _coverage_note(detail_attempted, detail_success)

    return ShareholderChangeScanResponse(
        generated_at=datetime.now(timezone.utc),
        market="A",
        direction=request.direction,
        start_date=start_at.isoformat(),
        end_date=end_at.isoformat(),
        total_found=max(len(records), raw_total),
        returned_count=len(limited_records),
        detail_attempted_count=detail_attempted,
        detail_success_count=detail_success,
        summary=summary,
        records=limited_records,
        warnings=dedupe(warnings)[:8],
        coverage_note=coverage_note,
        skill_invocation=(
            f"shareholder.change.scan({{ market: '{request.market}', window: '{request.days}d', "
            f"direction: '{request.direction}' }})"
        ),
    )


async def interpret_shareholder_change(
    request: ShareholderChangeInterpretRequest,
    llm_adapter: Any,
) -> ShareholderChangeInterpretResponse:
    record = request.record
    if getattr(llm_adapter, "provider", "mock") == "mock":
        return _fallback_shareholder_interpretation(record, llm_adapter)

    prompt = _build_shareholder_interpret_prompt(request)
    try:
        data = await llm_adapter.complete_json(
            prompt,
            max_tokens=1400 if request.style == "brief" else 2000,
            timeout_seconds=24,
            retry_schema_hint=(
                "必须返回 tone, verdict, summary, points, risks, questions, actions, evidence, confidence。"
            ),
        )
        return _normalize_shareholder_interpretation(data, record, llm_adapter)
    except Exception as exc:  # noqa: BLE001
        fallback = _fallback_shareholder_interpretation(record, llm_adapter)
        fallback.risks = dedupe([f"云模型暂不可用：{safe_error(exc)}", *fallback.risks])[:4]
        fallback.actions = dedupe(["检查模型配置后重试 AI 解读", *fallback.actions])[:4]
        fallback.confidence = min(fallback.confidence, 0.5)
        return fallback


def _build_shareholder_interpret_prompt(request: ShareholderChangeInterpretRequest) -> str:
    record = request.record
    payload = {
        "record": record.model_dump(),
        "user_question": request.question or "",
        "style": request.style,
        "instructions": [
            "只能基于输入字段、公告摘录、披露来源和原文链接判断，不得编造行情、估值、目标价或未给出的财务数据。",
            "区分计划、进展、完成/终止；计划不等于已经成交，完成/终止要提示是否足额、价格和原因需核验。",
            "港股/美股披露可能是事后权益披露或链式主体披露，避免把同一权益链路重复计数。",
            "输出面向投研核验，不给买卖建议。",
        ],
    }
    return (
        "你是 DeepFocus 的股东增减持披露解读 Agent。请基于结构化披露字段做审慎解读，"
        "返回严格 JSON object，字段必须为：tone, verdict, summary, points, risks, questions, actions, evidence, confidence。"
        "tone 只能是 positive/watch/risk/neutral；verdict 不超过 32 个中文字符；summary 不超过 90 个中文字符；"
        "points、risks、questions、actions 每个数组最多 4 项，每项不超过 34 个中文字符；"
        "evidence 只放输入中真实出现的字段、摘录或链接；confidence 为 0-1。\n"
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def _normalize_shareholder_interpretation(
    data: dict[str, Any],
    record: ShareholderChangeRecord,
    llm_adapter: Any,
) -> ShareholderChangeInterpretResponse:
    fallback = _fallback_shareholder_interpretation(record, llm_adapter)
    return ShareholderChangeInterpretResponse(
        provider=getattr(llm_adapter, "provider_name", "local"),
        model=getattr(llm_adapter, "model", "shareholder-change-interpret"),
        generated_at=datetime.now(timezone.utc),
        symbol=record.symbol,
        name=record.name,
        title=record.title,
        tone=_safe_interpret_tone(data.get("tone") or data.get("signal"), fallback.tone),
        verdict=short_text(data.get("verdict") or fallback.verdict, 42),
        summary=short_text(data.get("summary") or data.get("interpretation") or fallback.summary, 120),
        points=_safe_text_list(data.get("points") or data.get("positives") or data.get("insights"))[:4] or fallback.points,
        risks=_safe_text_list(data.get("risks") or data.get("concerns"))[:4] or fallback.risks,
        questions=_safe_text_list(data.get("questions") or data.get("watch_questions"))[:4] or fallback.questions,
        actions=_safe_text_list(data.get("actions") or data.get("next_steps"))[:4] or fallback.actions,
        evidence=_safe_text_list(data.get("evidence") or data.get("sources"))[:5] or fallback.evidence,
        confidence=safe_float(data.get("confidence"), default=fallback.confidence, low=0, high=1),
    )


def _fallback_shareholder_interpretation(
    record: ShareholderChangeRecord,
    llm_adapter: Any,
) -> ShareholderChangeInterpretResponse:
    direction_label = {"increase": "增持", "decrease": "减持", "mixed": "增减持", "unknown": "持股变动"}.get(record.direction, "持股变动")
    status_label = {"plan": "计划", "progress": "进展", "completed": "完成/终止", "other": "披露"}.get(record.status, "披露")
    holder = join_nonempty(record.shareholder_names[:3], separator="、") or record.shareholder_hint or record.shareholder_type or "披露主体"
    tone = "positive" if record.direction == "increase" and record.risk_level == "low" else "risk" if record.direction == "decrease" else "watch"
    verdict = "偏正面但需核验" if tone == "positive" else "存在供给压力" if tone == "risk" else "需打开原文核验"
    points = dedupe(
        [
            f"{holder}{direction_label}{record.name or record.symbol}",
            f"披露阶段为{status_label}，不能只按标题判断强度",
            f"比例变化：{record.change_ratio}" if record.change_ratio else "",
            f"涉及股数：{record.change_shares}" if record.change_shares else "",
        ]
    )[:4]
    risks = dedupe(
        [
            "规则兜底结果，不是云模型生成" if getattr(llm_adapter, "provider", "mock") == "mock" else "",
            "金额或价格缺失会降低判断强度" if not record.change_amount and not record.price_range else "",
            "多主体链式披露需避免重复计数" if int((record.metadata or {}).get("collapsed_disclosure_count") or 1) > 1 else "",
            "计划公告不等于实际成交" if record.status == "plan" else "",
        ]
    )[:4]
    questions = dedupe(
        [
            "是否已完成真实成交？" if record.status == "plan" else "是否足额完成或存在终止？",
            "成交价格是否偏离近期市场价格？",
            "资金来源和变动原因是否清楚？",
        ]
    )
    actions = dedupe(
        [
            "打开原文核验数量、均价和金额",
            "检查同一主体是否有连续披露",
            "结合股价区间判断市场冲击",
        ]
    )
    evidence = dedupe([record.title, record.detail_summary, record.evidence_excerpt, record.url])[:5]
    return ShareholderChangeInterpretResponse(
        provider=getattr(llm_adapter, "provider_name", "local-rules"),
        model=getattr(llm_adapter, "model", "shareholder-change-local-rules"),
        generated_at=datetime.now(timezone.utc),
        symbol=record.symbol,
        name=record.name,
        title=record.title,
        tone=tone,  # type: ignore[arg-type]
        verdict=verdict,
        summary=f"{record.name or record.symbol} {direction_label}，阶段为{status_label}，需核验原文关键字段。",
        points=points,
        risks=risks,
        questions=questions,
        actions=actions,
        evidence=evidence,
        confidence=0.45 if getattr(llm_adapter, "provider", "mock") == "mock" else 0.52,
    )


def format_shareholder_change_skill_response(result: ShareholderChangeScanResponse) -> str:
    direction_label = {
        "all": "增减持",
        "increase": "增持",
        "decrease": "减持",
    }.get(result.direction, "增减持")
    market_label = _market_label(result.market)
    lines = [
        f"已调用 skill：{result.skill}",
        f"范围：{market_label} {result.start_date} 至 {result.end_date}，方向：{direction_label}。",
        result.summary,
    ]
    if result.records:
        parsed_hint = (
            f"已解析前 {result.detail_attempted_count} 条 PDF 正文，"
            f"{result.detail_success_count} 条抽取到可用明细。"
            if result.detail_attempted_count
            else "当前仅展示公告索引。"
        )
        lines.extend(["", f"明细（前 {min(12, len(result.records))} 条，{parsed_hint}）："])
        for index, record in enumerate(result.records[:12], start=1):
            direction = {"increase": "增持", "decrease": "减持", "mixed": "增减持", "unknown": "未识别"}[record.direction]
            status = {"plan": "计划", "progress": "进展", "completed": "完成/终止", "other": "其他"}[record.status]
            holder_text = "、".join(record.shareholder_names[:8]) or record.shareholder_hint or record.shareholder_type or "未识别"
            if len(record.shareholder_names) > 8:
                holder_text += "等"
            quantity_text = f"{display_value(record.change_shares)} / {display_value(record.change_ratio)}"
            holding_text = join_nonempty(
                [
                    f"变动前：{record.holding_before}" if record.holding_before else "",
                    f"变动后：{record.holding_after}" if record.holding_after else "",
                ],
                separator="；",
            )
            lines.extend(
                [
                    f"{index}. {record.announcement_date} {record.symbol} {record.name} | {direction}/{status} | 风险：{record.risk_level}",
                    f"   股东/人员：{holder_text}",
                    (
                        f"   数量/比例：{quantity_text}；方式：{display_value(record.change_method)}；"
                        f"期间：{display_value(record.change_period)}"
                    ),
                    (
                        f"   价格/金额：{display_value(record.price_range)} / {display_value(record.change_amount)}；"
                        f"{holding_text or '持股变化：未识别'}"
                    ),
                    f"   公告：{record.title}",
                    f"   原文：{record.url}",
                ]
            )
            if record.change_reason:
                lines.append(f"   原因：{record.change_reason}")
            if record.evidence_excerpt:
                lines.append(f"   摘录：{record.evidence_excerpt}")
    else:
        lines.append("未检索到匹配公告。")
    if result.warnings:
        lines.extend(["", f"提示：{result.warnings[0]}"])
    lines.extend(["", result.coverage_note])
    return "\n".join(lines)


async def _scan_hk_shareholder_changes(request: ShareholderChangeScanRequest) -> ShareholderChangeScanResponse:
    start_at, end_at = _date_range(request)
    warnings: list[str] = []
    records_by_id: dict[str, ShareholderChangeRecord] = {}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=HKEX_HEADERS, follow_redirects=True) as client:
        current = end_at
        while current >= start_at and len(records_by_id) < request.limit * 2:
            for summary_type, shareholder_type in [
                ("C1", "主要股东"),
                ("C2", "董事/高管"),
            ]:
                day_records, day_warnings = await _query_hkex_summary_day(
                    client=client,
                    summary_date=current,
                    summary_type=summary_type,
                    shareholder_type=shareholder_type,
                )
                warnings.extend(day_warnings)
                for record in day_records:
                    if not _matches_requested_direction(record, request.direction):
                        continue
                    records_by_id.setdefault(record.announcement_id or get_record_identity(record), record)
                    if len(records_by_id) >= request.limit * 2:
                        break
            current -= timedelta(days=1)

        records = _collapse_hk_duplicate_disclosure_chains(
            sorted(records_by_id.values(), key=_default_record_sort_key)
        )
        limited_records = records[: request.limit]
        await _enrich_hk_records_with_chinese_names(client=client, records=limited_records)

    if not records and not warnings:
        warnings.append("HKEX DI 每日摘要未返回匹配披露；可扩大日期范围，或检查港交所披露站点可用性。")

    return ShareholderChangeScanResponse(
        provider="hkex-di",
        model="hk-shareholder-change-scan-v1",
        generated_at=datetime.now(timezone.utc),
        market="HK",
        direction=request.direction,
        start_date=start_at.isoformat(),
        end_date=end_at.isoformat(),
        total_found=len(records),
        returned_count=len(limited_records),
        detail_attempted_count=len(limited_records),
        detail_success_count=len(limited_records),
        summary=_build_summary(records, request.direction, start_at, end_at, "HK"),
        records=limited_records,
        warnings=dedupe(warnings)[:8],
        coverage_note=(
            "来源为 HKEX Disclosure of Interests 每日摘要，覆盖主要股东以及董事/高管权益披露；"
            "当前从 HTML 摘要抽取股票、披露主体、事件日期、买卖股数、价格和变动前后持仓，原始 DI 表单链接保留用于核验。"
        ),
        skill_invocation=(
            f"shareholder.change.scan({{ market: 'HK', window: '{request.days}d', "
            f"direction: '{request.direction}' }})"
        ),
    )


async def _scan_us_shareholder_changes(request: ShareholderChangeScanRequest) -> ShareholderChangeScanResponse:
    start_at, end_at = _date_range(request)
    warnings: list[str] = []
    records_by_id: dict[str, ShareholderChangeRecord] = {}
    effective_limit = _effective_us_form4_limit(request)
    cache_key = _us_form4_cache_key(
        start_at=start_at,
        end_at=end_at,
        direction=request.direction,
        limit=effective_limit,
    )
    cached_response = _get_us_form4_cached_response(cache_key)
    if cached_response is not None:
        return cached_response

    target_entries = max(effective_limit * 3, 80)
    if effective_limit < request.limit:
        warnings.append(f"SEC Form 4 XML 逐份解析较慢，本次先抽取前 {effective_limit} 条；可降低窗口或稍后做后台全量任务。")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=SEC_HEADERS, follow_redirects=True) as client:
        entries, provider_warnings = await _query_sec_form4_entries(
            client=client,
            start_at=start_at,
            end_at=end_at,
            limit=target_entries,
        )
        warnings.extend(provider_warnings)
        for entry in entries:
            if len(records_by_id) >= effective_limit:
                break
            record = await _record_from_sec_form4_entry(client, entry, warnings)
            if record is None or not _matches_requested_direction(record, request.direction):
                continue
            records_by_id.setdefault(record.announcement_id or get_record_identity(record), record)

    records = sorted(records_by_id.values(), key=_default_record_sort_key)
    limited_records = records[:effective_limit]
    if not records and not warnings:
        warnings.append("SEC EDGAR 当前 Form 4 feed 未返回匹配披露；可扩大日期范围，或稍后重试。")
    summary = _build_summary(records, request.direction, start_at, end_at, "US")
    if not records and _has_sec_rate_limit_warning(warnings):
        summary = "SEC EDGAR 当前触发 429 限流，本次未能更新 Form 4 数据；请稍后再刷新。"

    response = ShareholderChangeScanResponse(
        provider="sec-edgar",
        model="us-insider-form4-scan-v1",
        generated_at=datetime.now(timezone.utc),
        market="US",
        direction=request.direction,
        start_date=start_at.isoformat(),
        end_date=end_at.isoformat(),
        total_found=len(records),
        returned_count=len(limited_records),
        detail_attempted_count=len(limited_records),
        detail_success_count=sum(1 for record in limited_records if record.detail_quality in {"full", "partial"}),
        summary=summary,
        records=limited_records,
        warnings=dedupe(warnings)[:8],
        coverage_note=(
            "来源为 SEC EDGAR 当前 Form 4 申报 feed；已抓取 Form 4 XML 抽取发行人、内部人、交易代码、股数、价格和交易后持仓。"
            "P/A 通常视作增持或取得权益，S/F/D 通常视作减持或处分权益；复杂期权、税款扣缴和衍生品交易需打开原文核验。"
        ),
        skill_invocation=(
            f"shareholder.change.scan({{ market: 'US', window: '{request.days}d', "
            f"direction: '{request.direction}' }})"
        ),
    )
    if limited_records:
        _store_us_form4_cached_response(cache_key, response)
    elif _has_sec_rate_limit_warning(warnings):
        stale_response = _get_us_form4_stale_response(
            start_at=start_at,
            end_at=end_at,
            direction=request.direction,
            limit=effective_limit,
        )
        if stale_response is not None:
            stale_response.warnings = dedupe(
                [
                    "SEC EDGAR 当前触发 429 限流，已保留最近一次成功结果；请稍后再刷新。",
                    *warnings,
                    *stale_response.warnings,
                ]
            )[:8]
            return stale_response
    return response


def _effective_us_form4_limit(request: ShareholderChangeScanRequest) -> int:
    requested_detail_limit = request.detail_limit if request.detail_limit > 0 else request.limit
    return min(request.limit, max(10, requested_detail_limit), US_FORM4_XML_DETAIL_LIMIT)


def _us_form4_cache_key(*, start_at: date, end_at: date, direction: str, limit: int) -> str:
    return f"{start_at.isoformat()}:{end_at.isoformat()}:{direction}:{limit}"


def _copy_scan_response(response: ShareholderChangeScanResponse) -> ShareholderChangeScanResponse:
    return copy.deepcopy(response)


def _get_us_form4_cached_response(cache_key: str) -> Optional[ShareholderChangeScanResponse]:
    cached = US_FORM4_SCAN_CACHE.get(cache_key)
    if cached is None:
        return None
    cached_at, response = cached
    if (datetime.now(timezone.utc) - cached_at).total_seconds() > US_FORM4_CACHE_TTL_SECONDS:
        return None
    copied = _copy_scan_response(response)
    copied.warnings = dedupe(["使用最近 10 分钟内缓存，避免重复触发 SEC 限流。", *copied.warnings])[:8]
    return copied


def _store_us_form4_cached_response(cache_key: str, response: ShareholderChangeScanResponse) -> None:
    US_FORM4_SCAN_CACHE[cache_key] = (datetime.now(timezone.utc), _copy_scan_response(response))
    if len(US_FORM4_SCAN_CACHE) > 24:
        oldest_keys = sorted(US_FORM4_SCAN_CACHE, key=lambda key: US_FORM4_SCAN_CACHE[key][0])[:-24]
        for key in oldest_keys:
            US_FORM4_SCAN_CACHE.pop(key, None)


def _get_us_form4_stale_response(
    *,
    start_at: date,
    end_at: date,
    direction: str,
    limit: int,
) -> Optional[ShareholderChangeScanResponse]:
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, ShareholderChangeScanResponse]] = []
    prefix = f"{start_at.isoformat()}:{end_at.isoformat()}:{direction}:"
    for key, (cached_at, response) in US_FORM4_SCAN_CACHE.items():
        if not key.startswith(prefix):
            continue
        if (now - cached_at).total_seconds() > US_FORM4_STALE_CACHE_SECONDS:
            continue
        if response.returned_count <= 0:
            continue
        candidates.append((cached_at, response))
    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[1].returned_count >= limit, item[1].returned_count, item[0]), reverse=True)
    return _copy_scan_response(candidates[0][1])


def _has_sec_rate_limit_warning(warnings: list[str]) -> bool:
    return any("429" in warning or "Too Many Requests" in warning for warning in warnings)


async def _query_hkex_summary_day(
    *,
    client: httpx.AsyncClient,
    summary_date: date,
    summary_type: str,
    shareholder_type: str,
) -> tuple[list[ShareholderChangeRecord], list[str]]:
    url = f"https://di.hkex.com.hk/di/summary/DSM{summary_date:%Y%m%d}{summary_type}.htm"
    try:
        response = await client.get(url)
        if response.status_code == 404:
            return [], []
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - provider failures should not break the whole skill
        return [], [f"HKEX DI {summary_date.isoformat()} {summary_type} 查询失败：{safe_error(exc)}"]

    records: list[ShareholderChangeRecord] = []
    for row_html in _hkex_summary_rows(response.text):
        record = _record_from_hkex_summary_row(
            row_html=row_html,
            summary_date=summary_date,
            summary_type=summary_type,
            shareholder_type=shareholder_type,
        )
        if record is not None:
            records.append(record)
    return records, []


def _hkex_summary_rows(html_text: str) -> list[str]:
    starts = [match.start() for match in re.finditer(r'<TR\s+bgColor="#(?:cccccc|ffffff)">', html_text, re.I)]
    rows: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html_text)
        rows.append(html_text[start:end])
    return rows


def _record_from_hkex_summary_row(
    *,
    row_html: str,
    summary_date: date,
    summary_type: str,
    shareholder_type: str,
) -> Optional[ShareholderChangeRecord]:
    cells = [_clean_html_cell(cell) for cell in re.findall(r'<TD[^>]*class=["\']txt["\'][^>]*>(.*?)</TD>', row_html, re.I | re.S)]
    if len(cells) < 9:
        return None

    serial, issuer_name, stock_code, share_class, shareholder_name = cells[:5]
    relevant_event_date = _hk_date_to_iso(cells[5])
    raw_reason_code = cells[6]
    raw_shares_involved = cells[7]
    raw_average_price = cells[8]
    reason_code = _clean_hk_reason_code(raw_reason_code)
    share_position_label = _hk_position_marker_label(raw_shares_involved) or _hk_position_marker_label(raw_reason_code)
    shares_involved = _format_hk_share_count(raw_shares_involved)
    average_price = _format_hk_price_text(raw_average_price)
    estimated_amount = _format_hk_estimated_amount(raw_shares_involved, raw_average_price)
    direction, holding_before, holding_after, ratio_change = _infer_hk_direction(cells[9:], reason_code)
    change_method = _format_hk_reason_method(
        reason_code=reason_code,
        direction=direction,
        share_position_label=share_position_label,
    )
    if not serial or not stock_code or not issuer_name:
        return None

    href_match = re.search(r'<a\s+href=["\']([^"\']+)["\']', row_html, re.I)
    url = urljoin("https://di.hkex.com.hk", href_match.group(1)) if href_match else "https://di.hkex.com.hk/di/NSSrchMethod.aspx"
    direction_label = {"increase": "增持", "decrease": "减持", "mixed": "增减持", "unknown": "持股变动"}[direction]
    title = f"{shareholder_name or shareholder_type} {direction_label} {issuer_name} ({stock_code})"
    risk_level = "high" if direction == "decrease" else "low" if direction == "increase" else "medium"
    detail_summary = join_nonempty(
        [
            f"披露主体：{shareholder_name}" if shareholder_name else "",
            f"事件日：{relevant_event_date}" if relevant_event_date else "",
            f"股数：{shares_involved}" if shares_involved else "",
            f"均价：{average_price}" if average_price else "",
            f"估算金额：{estimated_amount}" if estimated_amount else "",
            f"持仓：{holding_before} → {holding_after}" if holding_before or holding_after else "",
        ],
        separator="；",
    )

    return ShareholderChangeRecord(
        symbol=stock_code,
        name=issuer_name,
        announcement_date=summary_date.isoformat(),
        direction=direction,  # type: ignore[arg-type]
        status="completed",
        shareholder_type=shareholder_type,
        shareholder_hint=shareholder_name,
        title=title,
        url=url,
        source="hkex-di",
        source_name=HKEX_SOURCE_NAME,
        announcement_id=serial,
        risk_level=risk_level,  # type: ignore[arg-type]
        tags=dedupe(["港股", direction_label, shareholder_type, share_class, summary_type]),
        shareholder_names=[shareholder_name] if shareholder_name else [],
        change_shares=shares_involved,
        change_ratio=ratio_change,
        change_amount=estimated_amount,
        price_range=average_price,
        change_period=relevant_event_date,
        change_method=change_method,
        holding_before=holding_before,
        holding_after=holding_after,
        detail_summary=detail_summary or title,
        evidence_excerpt=join_nonempty(
            [
                f"序号 {serial}" if serial else "",
                shareholder_name,
                f"披露原因 {reason_code}" if reason_code else "",
                shares_involved,
                f"均价 {average_price}" if average_price else "",
            ],
            separator=" · ",
        ),
        detail_source="html",
        detail_quality="partial",
        metadata={
            "summary_type": summary_type,
            "reason_code": reason_code,
            "reason_code_raw": _compact_hk_disclosure_text(raw_reason_code),
            "share_class": share_class,
            "relevant_event_date": relevant_event_date,
            "share_position": share_position_label,
            "shares_involved_raw": _compact_hk_disclosure_text(raw_shares_involved),
            "average_price_raw": _compact_hk_disclosure_text(raw_average_price),
        },
    )


def _collapse_hk_duplicate_disclosure_chains(records: list[ShareholderChangeRecord]) -> list[ShareholderChangeRecord]:
    grouped: dict[str, ShareholderChangeRecord] = {}
    for record in records:
        key = _hk_disclosure_chain_key(record)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = record
            continue
        _merge_hk_disclosure_chain_record(existing, record)
    return sorted(grouped.values(), key=_default_record_sort_key)


def _hk_disclosure_chain_key(record: ShareholderChangeRecord) -> str:
    reason_code = str((record.metadata or {}).get("reason_code") or record.change_method or "")
    position_parts = [
        record.holding_before,
        record.holding_after,
        record.change_ratio,
    ]
    if any(str(value or "").strip() for value in position_parts):
        key_parts = [
            record.source,
            record.symbol,
            record.announcement_date,
            record.direction,
            record.change_period,
            *position_parts,
            reason_code,
        ]
        return "|".join(_normalize_hk_chain_key_part(value) for value in key_parts)

    key_parts = [
        record.source,
        record.symbol,
        record.announcement_date,
        record.direction,
        record.change_period,
        record.change_shares,
        record.price_range,
        record.holding_before,
        record.holding_after,
        record.change_ratio,
        reason_code,
    ]
    strong_metric_count = sum(1 for value in key_parts[4:] if str(value or "").strip())
    if strong_metric_count < 3:
        return record.announcement_id or get_record_identity(record)
    return "|".join(_normalize_hk_chain_key_part(value) for value in key_parts)


def _format_hk_reason_method(*, reason_code: str, direction: str, share_position_label: str = "") -> str:
    clean_code = _clean_hk_reason_code(reason_code)
    if not clean_code:
        return share_position_label

    compact = re.sub(r"\s+", "", clean_code)
    if compact.startswith("11"):
        action = "取得/增持权益"
    elif compact.startswith("12"):
        action = "处置/减持权益"
    else:
        action = {
            "increase": "增持权益",
            "decrease": "减持权益",
            "mixed": "权益变动",
        }.get(direction, "权益变动")

    return join_nonempty([action, share_position_label, f"代码 {clean_code}"], separator=" · ")


def _normalize_hk_chain_key_part(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _merge_hk_disclosure_chain_record(target: ShareholderChangeRecord, duplicate: ShareholderChangeRecord) -> None:
    target_names = dedupe(
        [
            *list((target.metadata or {}).get("collapsed_holder_names") or []),
            *list(target.shareholder_names or []),
            *list(duplicate.shareholder_names or []),
            duplicate.shareholder_hint,
        ]
    )
    target.shareholder_names = target_names[:16]
    target.shareholder_hint = _format_hk_holder_chain_label(target_names)
    target.shareholder_type = _join_holder_types(target.shareholder_type, duplicate.shareholder_type)
    target.risk_level = target.risk_level if _risk_rank(target.risk_level) >= _risk_rank(duplicate.risk_level) else duplicate.risk_level
    target.tags = dedupe([*target.tags, *duplicate.tags, "合并披露"])

    source_count = int((target.metadata or {}).get("collapsed_disclosure_count") or 1) + 1
    serials = dedupe(
        [
            *list((target.metadata or {}).get("collapsed_serials") or []),
            target.announcement_id,
            duplicate.announcement_id,
        ]
    )
    urls = dedupe(
        [
            *list((target.metadata or {}).get("collapsed_urls") or []),
            target.url,
            duplicate.url,
        ]
    )
    summary_types = dedupe(
        [
            *list((target.metadata or {}).get("collapsed_summary_types") or []),
            str((target.metadata or {}).get("summary_type") or ""),
            str((duplicate.metadata or {}).get("summary_type") or ""),
        ]
    )
    target.metadata = {
        **(target.metadata or {}),
        "collapsed_disclosure_count": source_count,
        "collapsed_holder_names": target_names,
        "collapsed_serials": serials,
        "collapsed_urls": urls[:12],
        "collapsed_summary_types": summary_types,
    }

    direction_label = {"increase": "增持", "decrease": "减持", "mixed": "增减持", "unknown": "持股变动"}[target.direction]
    holder_label = _format_hk_holder_chain_label(target_names)
    target.title = f"{holder_label} {direction_label} {target.name} ({target.symbol})"
    chain_summary = join_nonempty(
        [
            f"合并 {source_count} 条 HKEX DI 链式披露",
            f"披露主体：{holder_label}",
            f"事件日：{target.change_period}" if target.change_period else "",
            f"股数：{target.change_shares}" if target.change_shares else "",
            f"均价：{target.price_range}" if target.price_range else "",
            f"估算金额：{target.change_amount}" if target.change_amount else "",
            f"持仓：{target.holding_before} → {target.holding_after}" if target.holding_before or target.holding_after else "",
        ],
        separator="；",
    )
    target.detail_summary = chain_summary or target.detail_summary
    target.evidence_excerpt = join_nonempty(
        [
            f"{source_count} 条合并披露",
            holder_label,
            str((target.metadata or {}).get("reason_code") or ""),
            target.change_shares,
            target.price_range,
        ],
        separator=" · ",
    )


def _format_hk_holder_chain_label(names: list[str]) -> str:
    clean_names = dedupe([name for name in names if name and not _is_generic_holder_descriptor(name)])
    if not clean_names:
        return "多名披露主体"
    if len(clean_names) == 1:
        return clean_names[0]
    visible = "、".join(clean_names[:4])
    return f"{visible}等{len(clean_names)}名主体" if len(clean_names) > 4 else visible


def _join_holder_types(left: str, right: str) -> str:
    types = dedupe([part for value in (left, right) for part in re.split(r"\s*/\s*|、", value or "")])
    return " / ".join(types)


async def _enrich_hk_records_with_chinese_names(
    *,
    client: httpx.AsyncClient,
    records: list[ShareholderChangeRecord],
) -> None:
    codes = dedupe([record.symbol.zfill(5) for record in records if re.fullmatch(r"\d{1,5}", record.symbol or "")])
    if not codes:
        return
    localized_names = await _fetch_sina_hk_chinese_names(client, codes)
    for record in records:
        localized = localized_names.get(record.symbol.zfill(5))
        if localized:
            _apply_hk_localized_name(record, localized)


async def _fetch_sina_hk_chinese_names(client: httpx.AsyncClient, codes: list[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    for chunk_start in range(0, len(codes), 80):
        chunk = codes[chunk_start : chunk_start + 80]
        provider_codes = [f"hk{code.zfill(5)}" for code in chunk]
        try:
            response = await client.get(
                "https://hq.sinajs.cn/list=" + ",".join(provider_codes),
                headers={
                    "Referer": "https://finance.sina.com.cn",
                    "User-Agent": CNINFO_HEADERS["User-Agent"],
                },
            )
            response.raise_for_status()
        except Exception:
            continue
        for match in re.finditer(r"var hq_str_hk(\d{5})=\"(.*?)\";", response.text, flags=re.S):
            code, raw = match.groups()
            fields = raw.split(",")
            chinese_name = fields[1].strip() if len(fields) > 1 else ""
            if chinese_name and re.search(r"[\u4e00-\u9fff]", chinese_name):
                names[code] = chinese_name
    return names


def _apply_hk_localized_name(record: ShareholderChangeRecord, localized_name: str) -> None:
    localized = _clean_detail_value(localized_name)
    if not localized or not re.search(r"[\u4e00-\u9fff]", localized):
        return
    original_name = record.name
    if original_name == localized:
        return

    record.metadata = {
        **(record.metadata or {}),
        "issuer_name_en": original_name,
        "issuer_name_zh": localized,
    }
    record.name = localized
    if original_name:
        record.title = record.title.replace(original_name, localized)
        record.detail_summary = record.detail_summary.replace(original_name, localized)
        record.evidence_excerpt = record.evidence_excerpt.replace(original_name, localized)


async def _query_sec_form4_entries(
    *,
    client: httpx.AsyncClient,
    start_at: date,
    end_at: date,
    limit: int,
) -> tuple[list[dict[str, str]], list[str]]:
    entries: list[dict[str, str]] = []
    warnings: list[str] = []
    page_size = 100
    for offset in range(0, min(limit * 2, 1000), page_size):
        try:
            response = await _sec_get(
                client,
                "https://www.sec.gov/cgi-bin/browse-edgar",
                params={
                    "action": "getcurrent",
                    "type": "4",
                    "count": str(page_size),
                    "start": str(offset),
                    "output": "atom",
                },
                warnings=warnings,
                label="SEC EDGAR 当前 Form 4 feed",
            )
            if response is None:
                break
            root = ET.fromstring(response.content)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SEC EDGAR 当前 Form 4 feed 查询失败：{safe_error(exc)}")
            break

        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        page_entries = root.findall("atom:entry", namespace)
        if not page_entries:
            break

        old_entry_seen = False
        for entry in page_entries:
            title = entry.findtext("atom:title", default="", namespaces=namespace)
            link_node = entry.find("atom:link", namespace)
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            summary = entry.findtext("atom:summary", default="", namespaces=namespace)
            updated = entry.findtext("atom:updated", default="", namespaces=namespace)
            filing_date = _sec_filing_date(summary, updated)
            if filing_date is None:
                continue
            if filing_date < start_at:
                old_entry_seen = True
                continue
            if filing_date > end_at:
                continue
            entries.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "updated": updated,
                    "filing_date": filing_date.isoformat(),
                }
            )
            if len(entries) >= limit:
                return entries, warnings

        if old_entry_seen:
            break
    return entries, warnings


async def _record_from_sec_form4_entry(
    client: httpx.AsyncClient,
    entry: dict[str, str],
    warnings: list[str],
) -> Optional[ShareholderChangeRecord]:
    index_url = entry.get("link", "")
    filing_date = entry.get("filing_date", "")
    if not index_url or not filing_date:
        return None
    if _has_sec_rate_limit_warning(warnings):
        return _record_from_sec_atom_entry(entry)

    try:
        xml_url = await _sec_primary_xml_url(client, index_url, warnings)
        response = await _sec_get(client, xml_url, warnings=warnings, label="SEC Form 4 XML")
        if response is None:
            return _record_from_sec_atom_entry(entry)
        root = ET.fromstring(response.content)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"SEC Form 4 XML 解析失败：{safe_error(exc)}")
        return _record_from_sec_atom_entry(entry)

    issuer_name = _xml_text(root, "issuer/issuerName") or "Unknown issuer"
    symbol = _xml_text(root, "issuer/issuerTradingSymbol") or _xml_text(root, "issuer/issuerCik") or ""
    period = _xml_text(root, "periodOfReport") or filing_date
    owner_names: list[str] = []
    relationship_labels: list[str] = []
    for owner in root.findall("reportingOwner"):
        owner_name = _xml_text(owner, "reportingOwnerId/rptOwnerName")
        if owner_name:
            owner_names.append(owner_name)
        relationship = owner.find("reportingOwnerRelationship")
        if relationship is not None:
            if _xml_bool(relationship, "isDirector"):
                relationship_labels.append("Director")
            if _xml_bool(relationship, "isOfficer"):
                officer_title = _xml_text(relationship, "officerTitle")
                relationship_labels.append(f"Officer{f' · {officer_title}' if officer_title else ''}")
            if _xml_bool(relationship, "isTenPercentOwner"):
                relationship_labels.append("10% Owner")
            if _xml_bool(relationship, "isOther"):
                other_text = _xml_text(relationship, "otherText")
                relationship_labels.append(other_text or "Other")

    transactions = [_sec_transaction_from_xml(node) for node in root.findall(".//nonDerivativeTransaction")]
    transactions = [transaction for transaction in transactions if transaction["code"]]
    codes = [transaction["code"] for transaction in transactions]
    direction = _sec_direction_from_codes(codes)
    direction_label = {"increase": "增持", "decrease": "减持", "mixed": "增减持", "unknown": "权益变动"}[direction]
    primary_transactions = [
        transaction for transaction in transactions if _sec_code_direction(transaction["code"]) in {direction, "mixed"}
    ] or transactions
    shares_total = sum(value for value in (to_float(transaction["shares"]) for transaction in primary_transactions) if value is not None)
    prices = [value for value in (to_float(transaction["price"]) for transaction in primary_transactions) if value is not None]
    amount_total = 0.0
    for transaction in primary_transactions:
        shares = to_float(transaction["shares"])
        price = to_float(transaction["price"])
        if shares is not None and price is not None:
            amount_total += shares * price

    shareholder_type = " / ".join(dedupe(relationship_labels)) or "Insider"
    risk_level = "high" if direction == "decrease" and relationship_labels else "low" if direction == "increase" else "medium"
    owner_text = "、".join(owner_names[:4]) or _sec_owner_from_title(entry.get("title", ""))
    accession = _sec_accession_from_url(index_url)
    title = f"Form 4: {owner_text or shareholder_type} {direction_label} {issuer_name} ({symbol})"
    price_range = _format_price_range(prices, "$")
    holding_after = _last_nonempty([transaction["holding_after"] for transaction in primary_transactions])
    detail_summary = join_nonempty(
        [
            f"交易代码：{'/'.join(dedupe(codes))}" if codes else "",
            f"股数：{_format_shares(shares_total)}" if shares_total else "",
            f"价格：{price_range}" if price_range else "",
            f"交易后持仓：{holding_after}" if holding_after else "",
        ],
        separator="；",
    )

    return ShareholderChangeRecord(
        symbol=symbol,
        name=issuer_name,
        announcement_date=filing_date,
        direction=direction,  # type: ignore[arg-type]
        status="completed",
        shareholder_type=shareholder_type,
        shareholder_hint=owner_text,
        title=title,
        url=index_url,
        source="sec-edgar",
        source_name=SEC_SOURCE_NAME,
        announcement_id=accession,
        risk_level=risk_level,  # type: ignore[arg-type]
        tags=dedupe(["美股", "Form 4", direction_label, shareholder_type]),
        shareholder_names=owner_names,
        change_shares=_format_shares(shares_total) if shares_total else "",
        change_ratio="",
        change_amount=_format_money(amount_total, "$") if amount_total else "",
        price_range=price_range,
        change_period=period,
        change_method=" / ".join(dedupe(codes)),
        holding_after=holding_after,
        detail_summary=detail_summary or title,
        evidence_excerpt=f"{issuer_name} · {owner_text} · {detail_summary}".strip(" ·"),
        detail_source="xml",
        detail_quality="full" if transactions else "partial",
        metadata={
            "xml_url": xml_url,
            "period_of_report": period,
            "form_type": "4",
            "updated": entry.get("updated", ""),
        },
    )


async def _sec_primary_xml_url(client: httpx.AsyncClient, index_url: str, warnings: list[str]) -> str:
    response = await _sec_get(client, index_url, warnings=warnings, label="SEC Form 4 索引页")
    if response is None:
        raise ValueError("SEC Form 4 索引页暂不可用")
    html_text = response.text
    xml_links = re.findall(r'href=["\']([^"\']+\.xml)["\'][^>]*>', html_text, re.I)
    for href in xml_links:
        if "xslF345" not in href:
            return urljoin("https://www.sec.gov", href)
    if xml_links:
        return urljoin("https://www.sec.gov", xml_links[-1])
    raise ValueError("未找到 Form 4 XML 链接")


async def _sec_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[dict[str, str]] = None,
    warnings: list[str],
    label: str,
) -> Optional[httpx.Response]:
    await asyncio.sleep(SEC_REQUEST_DELAY_SECONDS)
    for attempt in range(len(SEC_RETRY_BACKOFF_SECONDS) + 1):
        try:
            response = await client.get(url, params=params)
        except Exception as exc:  # noqa: BLE001
            if attempt >= len(SEC_RETRY_BACKOFF_SECONDS):
                warnings.append(f"{label} 请求失败：{safe_error(exc)}")
                return None
            await asyncio.sleep(SEC_RETRY_BACKOFF_SECONDS[attempt])
            continue

        if response.status_code == 429:
            if attempt >= len(SEC_RETRY_BACKOFF_SECONDS):
                warnings.append(f"{label} 查询失败：SEC EDGAR 429 Too Many Requests，已触发限流。")
                return None
            await asyncio.sleep(_sec_retry_delay(response, SEC_RETRY_BACKOFF_SECONDS[attempt]))
            continue

        if response.status_code >= 500 and attempt < len(SEC_RETRY_BACKOFF_SECONDS):
            await asyncio.sleep(SEC_RETRY_BACKOFF_SECONDS[attempt])
            continue

        try:
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label} 查询失败：{safe_error(exc)}")
            return None
        return response

    return None


def _sec_retry_delay(response: httpx.Response, fallback_seconds: float) -> float:
    retry_after = response.headers.get("Retry-After", "")
    if retry_after:
        try:
            return min(max(float(retry_after), fallback_seconds), 30.0)
        except ValueError:
            return fallback_seconds
    return fallback_seconds


def _record_from_sec_atom_entry(entry: dict[str, str]) -> Optional[ShareholderChangeRecord]:
    title = entry.get("title", "")
    link = entry.get("link", "")
    filing_date = entry.get("filing_date", "")
    if not title or not link or not filing_date:
        return None
    owner_name = _sec_owner_from_title(title)
    accession = _sec_accession_from_url(link)
    return ShareholderChangeRecord(
        symbol="",
        name=owner_name or "SEC Form 4",
        announcement_date=filing_date,
        direction="unknown",
        status="completed",
        shareholder_type="Insider",
        shareholder_hint=owner_name,
        title=title,
        url=link,
        source="sec-edgar",
        source_name=SEC_SOURCE_NAME,
        announcement_id=accession,
        risk_level="medium",
        tags=["美股", "Form 4", "待核验"],
        shareholder_names=[owner_name] if owner_name else [],
        detail_summary="已命中 SEC Form 4 索引，但 XML 明细暂不可用。",
        evidence_excerpt=entry.get("summary", ""),
        detail_source="unavailable",
        detail_quality="title_only",
        metadata={"updated": entry.get("updated", "")},
    )


def get_record_identity(record: ShareholderChangeRecord) -> str:
    return f"{record.source}:{record.symbol}:{record.announcement_date}:{record.title}"


def _matches_requested_direction(record: ShareholderChangeRecord, direction: str) -> bool:
    if direction == "increase":
        return record.direction in {"increase", "mixed"}
    if direction == "decrease":
        return record.direction in {"decrease", "mixed"}
    return True


async def _enrich_records_with_pdf_details(
    *,
    client: httpx.AsyncClient,
    records: list[ShareholderChangeRecord],
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
        except Exception as exc:  # noqa: BLE001 - keep the skill useful even if one PDF layout fails
            record.detail_source = "unavailable"
            warnings.append(f"{record.symbol or record.name} PDF 明细解析失败：{safe_error(exc)}")
            continue
        _apply_pdf_detail(record, detail)
        if record.detail_quality != "title_only":
            success += 1
    return attempted, success


async def _extract_pdf_detail(client: httpx.AsyncClient, record: ShareholderChangeRecord) -> dict[str, Any]:
    headers = EASTMONEY_HEADERS if record.source == "eastmoney" or "dfcfw.com" in record.url else None
    response = await client.get(record.url, headers=headers)
    response.raise_for_status()
    raw = response.content
    if len(raw) > PDF_DETAIL_MAX_BYTES:
        raise ValueError("PDF 文件超过解析上限")
    text = _extract_pdf_text(raw)
    return _extract_change_detail_from_text(text, record)


def _extract_pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for page in reader.pages[:PDF_DETAIL_MAX_PAGES]:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text)
    return _compact_pdf_text("\n".join(pages))[:PDF_DETAIL_MAX_CHARS]


def _extract_change_detail_from_text(text: str, record: ShareholderChangeRecord) -> dict[str, Any]:
    action = _action_label(record.direction)
    holder_names = _extract_shareholder_names(text)
    change_shares = _extract_change_shares(text, action)
    change_ratio = _extract_change_ratio(text, action)
    change_amount = _extract_change_amount(text, action)
    price_range = _extract_price_range(text, action)
    change_period = _extract_change_period(text, action)
    change_method = _extract_change_method(text, action)
    holding_before = _extract_holding_before(text, action)
    holding_after = _extract_holding_after(text)
    change_reason = _extract_change_reason(text, action)
    evidence_excerpt = _extract_evidence_excerpt(text, action)
    detail_summary = _build_detail_summary(
        holder_names=holder_names,
        change_shares=change_shares,
        change_ratio=change_ratio,
        change_method=change_method,
        change_period=change_period,
        price_range=price_range,
        change_amount=change_amount,
    )
    quality = _detail_quality(
        holder_names=holder_names,
        change_shares=change_shares,
        change_ratio=change_ratio,
        change_method=change_method,
        change_period=change_period,
    )
    return {
        "shareholder_names": holder_names,
        "change_shares": change_shares,
        "change_ratio": change_ratio,
        "change_amount": change_amount,
        "price_range": price_range,
        "change_period": change_period,
        "change_method": change_method,
        "holding_before": holding_before,
        "holding_after": holding_after,
        "change_reason": change_reason,
        "detail_summary": detail_summary,
        "evidence_excerpt": evidence_excerpt,
        "detail_source": "pdf",
        "detail_quality": quality,
    }


def _apply_pdf_detail(record: ShareholderChangeRecord, detail: dict[str, Any]) -> None:
    for field, value in detail.items():
        if field in {"detail_source", "detail_quality"}:
            if value and (value != "title_only" or record.detail_quality == "title_only"):
                setattr(record, field, value)
            continue
        if value:
            setattr(record, field, value)
    _apply_title_fallback(record)


def _compact_pdf_text(text: str) -> str:
    compact = (
        text.replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("", " ")
        .replace("□", " □")
        .replace("√", " √")
    )
    for before, after in {
        "价 格": "价格",
        "先 生": "先生",
        "女 士": "女士",
        "减 持": "减持",
        "增 持": "增持",
    }.items():
        compact = compact.replace(before, after)
    compact = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", r"\1年\2月\3日", compact)
    compact = re.sub(r"\s+", " ", compact)
    return compact.strip()


def _action_label(direction: str) -> str:
    if direction == "increase":
        return "增持"
    if direction == "mixed":
        return "增减持"
    return "减持"


def _extract_shareholder_names(text: str) -> list[str]:
    names: list[str] = []
    table_patterns = [
        r"股东名称\s+(.{2,90}?)\s*(?:股东身份|持股数量|计划(?:增持|减持)|(?:增持|减持)数量|当前持股)",
        r"(?:股东|人员)名称\s+(.{2,90}?)\s*(?:职务|持股数量|计划(?:增持|减持)|(?:增持|减持)数量)",
    ]
    for pattern in table_patterns:
        for match in re.finditer(pattern, text):
            names.extend(_split_holder_names(match.group(1)))

    narrative_patterns = [
        r"收到(?:公司)?(.{2,180}?)出具的?《[^》]{0,30}(?:增持|减持)[^》]{0,30}(?:告知函|计划)",
        r"股东(.{2,160}?)(?:计划|拟|已通过|将根据).{0,20}(?:增持|减持)",
        r"由(?:公司)?(.{2,140}?)(?:增持|减持)(?:公司)?股份",
    ]
    for pattern in narrative_patterns:
        match = re.search(pattern, text)
        if match:
            names.extend(_split_holder_names(match.group(1)))

    return dedupe([name for name in names if _looks_like_holder_name(name)])[:12]


def _split_holder_names(value: str) -> list[str]:
    cleaned = _clean_detail_value(value)
    cleaned = re.sub(
        r"(?:公司|本公司)?(?:控股股东|实际控制人|实控人|董事|监事|高级管理人员|高管|职工代表董事|"
        r"董事长|总经理|副总裁|董事会秘书|核心技术人员|及其一致行动人|其一致行动人|部分|相关|上述|"
        r"近日|分别|先生|女士|合计|拟|计划)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\d+家(?:合伙企业|企业|公司)", "", cleaned)
    pieces = re.split(r"[、,，；;]|\s*和\s*|\s*及\s*", cleaned)
    return [_clean_holder_name(piece) for piece in pieces if _clean_holder_name(piece)]


def _clean_holder_name(value: str) -> str:
    value = _clean_detail_value(value)
    value = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", value)
    value = re.sub(r"（以下简称.*$", "", value)
    value = re.sub(r"）\s*\d+.*$", "）", value)
    value = re.sub(r"\s*\d+$", "", value)
    value = re.sub(r"(先生|女士)$", "", value)
    value = re.sub(r"^(?:和|及|与|由|对|向|收到|关于|股东|人员|名称)", "", value)
    value = re.sub(r"(?:出具|保证|持有|股份来源|股东身份).*$", "", value)
    return value.strip(" ：:，,、；;[]【】")


def _looks_like_holder_name(value: str) -> bool:
    if _is_generic_holder_descriptor(value):
        return False
    if len(value) < 2 or len(value) > 48:
        return False
    if re.fullmatch(r"[0-9,，.]+股", value):
        return False
    if re.search(r"^\d|%|—|股股份|系|不涉|全体|董事会|误导性|真实性|准确性|完整性|上市公司|大股东|公告内容", value):
        return False
    if re.search(
        r"公告|证券代码|总股本|持股|股东身份|一致行动人|自身资金|将根据|职务|高级管理人员|"
        r"减持|增持|计划|数量|比例|价格|期间|方式|来源|公司股份|集中竞价|大宗交易|询价转让|[□√]",
        value,
    ):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z]", value))


def _is_generic_holder_descriptor(value: str) -> bool:
    compact = _clean_detail_value(value).strip(" ：:，,、；;[]【】")
    if not compact:
        return True
    generic_values = {
        "关于",
        "公司",
        "本公司",
        "股东",
        "大股东",
        "公司大股东",
        "人员",
        "身份",
        "股份",
        "已回购股份",
        "拟",
        "间接",
        "首次",
        "兼首次",
        "董事",
        "公司董事",
        "董事长",
        "公司董事长",
        "监事",
        "高管",
        "公司高管",
        "董监高",
        "高级管理人员",
        "公司高级管理人员",
        "部分董事",
        "部分人员",
        "控股股东",
        "公司控股股东",
        "实际控制人",
        "公司实际控制人",
        "控股股东、实际控制人",
        "持股5%以上股东",
        "5%以上股东",
    }
    if compact in generic_values:
        return True
    if re.search(r"[、,，]", compact):
        parts = [part.strip() for part in re.split(r"[、,，]", compact) if part.strip()]
        if parts and all(_is_generic_holder_descriptor(part) for part in parts):
            return True
    if compact.endswith("关于"):
        return True
    role_pattern = (
        r"(?:公司|本公司|原|原合计|部分)?(?:控股股东|实际控制人|实控人|大股东|股东|"
        r"持股5%以上股东|5%以上股东|董事|董事长|监事|高管|高级管理人员|董监高)"
        r"(?:及其一致行动人|的一致行动人|及高级管理人员|权益变动|股份|首次|间接)?"
    )
    if re.fullmatch(role_pattern, compact):
        return True
    has_named_entity_token = bool(re.search(r"有限公司|有限合伙|合伙企业|集团|投资|基金|资产|资本", compact))
    if not has_named_entity_token and re.search(r"权益变动|触及|整数倍|回购|实施结果暨|首次|后续|拟$", compact):
        return True
    return False


def _extract_change_shares(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    patterns = [
        rf"合计拟{action_pattern}.{{0,40}}?((?:不超过|不低于|不少于)?\s*[0-9][0-9,，.]*\s*(?:万)?股)",
        rf"(?:合计|总计){action_pattern}(?:公司)?股份(?:数量)?(?:合计)?\s*((?:不超过|不低于|不少于)?\s*[0-9][0-9,，.]*\s*(?:万)?股)",
        rf"(?:计划)?{action_pattern}数量\s*((?:不超过|不低于|不少于|累计|合计)?\s*[:：]?\s*[0-9][0-9,，.]*\s*(?:万)?股)",
        rf"(?:计划|拟|合计拟|累计|已通过|本次){action_pattern}.{{0,40}}?((?:不超过|不低于|不少于)?\s*[0-9][0-9,，.]*\s*(?:万)?股)",
        rf"{action_pattern}(?:公司)?股份(?:数量)?(?:合计)?\s*((?:不超过|不低于|不少于)?\s*[0-9][0-9,，.]*\s*(?:万)?股)",
    ]
    return _first_match(text, patterns)


def _extract_change_ratio(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    aggregate_match = re.search(
        rf"合计拟{action_pattern}.{{0,120}}?(不超过|不低于|不少于)?(?:公司)?(?:目前)?总股本(?:的比例|比例|的)?\s*([0-9.]+%)",
        text,
    )
    if aggregate_match:
        return _clean_detail_value(f"{aggregate_match.group(1) or ''}{aggregate_match.group(2)}")
    patterns = [
        rf"合计拟{action_pattern}.{{0,120}}?占(?:公司)?(?:目前)?总股本(?:的比例|比例)?(?:合计)?(?:为|的)?\s*((?:不超过|不低于|不少于)?\s*[0-9.]+%)",
        rf"(?:合计|总计){action_pattern}.{{0,120}}?占(?:截至目前)?(?:公司)?(?:目前)?总股本(?:的比例|比例)?(?:合计)?(?:为|的)?\s*((?:不超过|不低于|不少于)?\s*[0-9.]+%)",
        rf"(?:计划)?{action_pattern}比例\s*((?:不超过|不低于|不少于)?\s*[:：]?\s*[0-9.]+%)",
        rf"(?:计划|拟|合计拟|累计|本次){action_pattern}.{{0,100}}?占(?:截至目前)?(?:公司)?(?:目前)?总股本(?:的比例|比例)?(?:合计)?(?:为|的)?\s*((?:不超过|不低于|不少于)?\s*[0-9.]+%)",
        rf"{action_pattern}(?:公司)?股份.{{0,100}}?占.{{0,24}}?总股本.{{0,24}}?((?:不超过|不低于|不少于)?\s*[0-9.]+%)",
    ]
    return _first_match(text, patterns)


def _extract_change_amount(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    return _first_match(
        text,
        [
            rf"{action_pattern}总金额\s*[:：]?\s*([0-9][0-9,，.]*\s*(?:万)?元)",
            rf"{action_pattern}金额\s*[:：]?\s*([0-9][0-9,，.]*\s*(?:万)?元)",
        ],
    )


def _extract_price_range(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    price = _first_match(
        text,
        [
            rf"{action_pattern}价格区间\s*[:：]?\s*([0-9.]+\s*[~～\-—至]\s*[0-9.]+\s*元/股)",
            rf"{action_pattern}价格\s*(?:将|按|按照)?\s*(市场价格[^。；;，,]{{0,20}}|根据市场价格[^。；;，,]{{0,24}}|按照市场价格决定)",
            rf"{action_pattern}价格\s*[:：]?\s*([^。；;，,]{2,60})",
        ],
    )
    return _clean_price_value(price)


def _extract_change_period(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    date_span = r"20\d{2}年\d{1,2}月\d{1,2}日\s*[~～\-—至到]+\s*20\d{2}年\d{1,2}月\d{1,2}日"
    return _first_match(
        text,
        [
            rf"{action_pattern}期间\s*[:：]?\s*({date_span})",
            rf"[（(]({date_span})[）)]",
            rf"{action_pattern}期间\s*[:：]?\s*(计划?自[^。；;]{{2,90}})",
            rf"计划自本{action_pattern}计划公告之日起([^。；;]{{2,90}})",
            rf"自本公告披露之日起([^。；;]{{2,90}})",
        ],
    )


def _extract_change_method(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    method_context = _first_match(
        text,
        [
            rf"{action_pattern}方式(?:及对应[^。；;]{{0,12}}数量)?\s*[:：]?\s*([^。；;]{{2,140}})",
            rf"通过([^。；;]{{2,100}}?)方式(?:合计)?{action_pattern}",
        ],
    )
    methods = _methods_from_text(method_context or text)
    return "、".join(methods)


def _extract_holding_before(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    before = _first_match(
        text,
        [
            rf"本次{action_pattern}股份计划实施前.{{0,80}}?持有公司股份\s*([0-9][0-9,，]*股).{{0,30}}?占公司总股本(?:的)?\s*([0-9.]+%)",
            r"持股数量\s*([0-9][0-9,，]*股)\s*持股比例\s*([0-9.]+%)",
            r"持有公司股份\s*([0-9][0-9,，]*股).{0,30}?占公司总股本(?:比例为|的)?\s*([0-9.]+%)",
        ],
        keep_groups=True,
    )
    return _format_pair(before)


def _extract_holding_after(text: str) -> str:
    after = _first_match(
        text,
        [
            r"当前持股数量\s*([0-9][0-9,，]*股)\s*当前持股比例\s*([0-9.]+%)",
            r"本次(?:增持|减持)后.{0,80}?持有公司股份\s*([0-9][0-9,，]*股).{0,30}?占公司总股本(?:的)?\s*([0-9.]+%)",
        ],
        keep_groups=True,
    )
    return _format_pair(after)


def _extract_change_reason(text: str, action: str) -> str:
    action_pattern = _action_pattern(action)
    reason = _first_match(
        text,
        [
            rf"(?:拟)?{action_pattern}(?:股份)?原因\s*[:：]?\s*([^。；;]{{2,80}}?)(?:预披露期间|若公司|$)",
            rf"{action_pattern}的原因\s*[:：]?\s*([^。；;]{{2,80}}?)(?:预披露期间|若公司|$)",
            r"由于股东([^。；;]{2,80}需要)",
            r"因([^。；;]{2,80}需求)",
        ],
    )
    return _clean_reason_value(reason)


def _extract_evidence_excerpt(text: str, action: str) -> str:
    keywords = [
        f"{action}计划的主要内容",
        f"{action}计划主要内容",
        f"{action}计划的实施结果",
        f"{action}数量",
        f"拟{action}",
        f"{action}期间",
        "重要内容提示",
    ]
    for keyword in keywords:
        index = text.find(keyword)
        if index >= 0:
            start = max(0, index - 24)
            return _trim_excerpt(text[start : index + 240])
    return ""


def _build_detail_summary(
    *,
    holder_names: list[str],
    change_shares: str,
    change_ratio: str,
    change_method: str,
    change_period: str,
    price_range: str,
    change_amount: str,
) -> str:
    parts = [
        f"股东/人员：{'、'.join(holder_names[:6])}" if holder_names else "",
        f"数量：{change_shares}" if change_shares else "",
        f"比例：{change_ratio}" if change_ratio else "",
        f"方式：{change_method}" if change_method else "",
        f"期间：{change_period}" if change_period else "",
        f"价格：{price_range}" if price_range else "",
        f"金额：{change_amount}" if change_amount else "",
    ]
    return join_nonempty(parts, separator="；")


def _detail_quality(
    *,
    holder_names: list[str],
    change_shares: str,
    change_ratio: str,
    change_method: str,
    change_period: str,
) -> str:
    filled = sum(bool(value) for value in [holder_names, change_shares, change_ratio, change_method, change_period])
    if holder_names and (change_shares or change_ratio) and (change_method or change_period):
        return "full"
    if filled:
        return "partial"
    return "title_only"


def _action_pattern(action: str) -> str:
    return "(?:增持|减持)" if action == "增减持" else action


def _methods_from_text(text: str) -> list[str]:
    method_aliases = [
        ("集中竞价", "集中竞价"),
        ("集合竞价", "集合竞价"),
        ("竞价交易", "竞价交易"),
        ("大宗交易", "大宗交易"),
        ("协议转让", "协议转让"),
        ("询价转让", "询价转让"),
    ]
    methods = [label for token, label in method_aliases if token in text]
    return dedupe(methods)


def _first_match(text: str, patterns: list[str], *, keep_groups: bool = False) -> Any:
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if keep_groups:
            return tuple(_clean_detail_value(group) for group in match.groups() if group)
        return _clean_detail_value(match.group(1))
    return () if keep_groups else ""


def _format_pair(value: Any) -> str:
    if isinstance(value, tuple) and len(value) >= 2:
        return f"{value[0]}，占{value[1]}"
    if isinstance(value, str):
        return value
    return ""


def _clean_detail_value(value: str) -> str:
    value = value.replace("，", ",")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*[:：]\s*", "：", value)
    value = re.sub(r"\s*([~～\-—至到])\s*", r"\1", value)
    value = value.replace(" ,", ",").replace(", ", ",")
    return value.strip(" ：:，,；;。")


def _clean_reason_value(value: str) -> str:
    reason = _clean_detail_value(value)
    reason = re.split(r"预披露期间|若公司|本次|现将|。|；", reason, maxsplit=1)[0]
    return reason.strip(" ：:，,；;。")


def _clean_price_value(value: str) -> str:
    price = _clean_detail_value(value)
    price = re.split(r"若公司|若减持|本次|。|；", price, maxsplit=1)[0]
    return price.strip(" ：:，,；;。")


def _trim_excerpt(value: str) -> str:
    excerpt = _clean_detail_value(value)
    if len(excerpt) > 220:
        excerpt = f"{excerpt[:220]}..."
    return excerpt
def _coverage_note(detail_attempted: int, detail_success: int) -> str:
    if detail_attempted:
        return (
            f"来源为巨潮资讯网公告检索；已对前 {detail_attempted} 条公告抓取 PDF 正文抽取字段，"
            f"其中 {detail_success} 条抽取到可用明细。字段为空代表公告未披露或版式未识别，原文链接保留用于核验。"
        )
    return "来源为巨潮资讯网公告检索；当前仅完成公告标题扫描，未抓取 PDF 正文明细。"


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
        except Exception as exc:  # noqa: BLE001 - skill should return auditable warnings instead of crashing
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


def _record_from_cninfo_row(row: dict[str, Any]) -> Optional[ShareholderChangeRecord]:
    title = clean_title(str(row.get("announcementTitle") or row.get("shortTitle") or ""))
    if not title or not re.search(r"增持|减持|持股变动|股份变动", title):
        return None
    symbol = str(row.get("secCode") or "").strip()
    name = clean_title(str(row.get("secName") or row.get("tileSecName") or ""))
    announcement_id = str(row.get("announcementId") or "").strip()
    announcement_date = _announcement_date(row.get("announcementTime"))
    direction = _infer_direction(title)
    status = _infer_status(title)
    shareholder_type = _infer_shareholder_type(title)
    risk_level = _infer_risk_level(title, direction)
    source = str(row.get("source") or "cninfo").strip()
    source_name = str(row.get("sourceName") or CNINFO_SOURCE_NAME).strip()
    eastmoney_pdf_url = str(row.get("eastmoney_pdf_url") or "").strip()
    eastmoney_detail_url = str(row.get("eastmoney_detail_url") or "").strip()
    adjunct_url = str(row.get("adjunctUrl") or "").strip()
    url = eastmoney_pdf_url or (f"{CNINFO_STATIC_BASE}/{adjunct_url}" if adjunct_url else "https://www.cninfo.com.cn/")

    record = ShareholderChangeRecord(
        symbol=symbol,
        name=name,
        announcement_date=announcement_date,
        direction=direction,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        shareholder_type=shareholder_type,
        shareholder_hint=_shareholder_hint(title, issuer_name=name),
        title=title,
        url=url,
        source=source,
        source_name=source_name,
        announcement_id=announcement_id,
        risk_level=risk_level,  # type: ignore[arg-type]
        tags=_tags_for_record(title, direction, status, shareholder_type),
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
    start_at: date,
    end_at: date,
    limit: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    rows, total, warnings = await query_eastmoney_announcements(
        client=client,
        start_at=start_at,
        end_at=end_at,
        f_node="7",
        s_node="0",
        limit=limit,
    )
    filtered = [
        row
        for row in rows
        if re.search(r"增持|减持|持股变动|股份变动|权益变动", str(row.get("announcementTitle") or ""))
    ]
    return filtered[:limit], total, warnings


def _date_range(request: ShareholderChangeScanRequest) -> tuple[date, date]:
    end_at = parse_date(request.end_date) or datetime.now(CN_TZ).date()
    start_at = parse_date(request.start_date) or (end_at - timedelta(days=request.days - 1))
    if start_at > end_at:
        start_at, end_at = end_at, start_at
    return start_at, end_at
def _keywords_for_direction(direction: str) -> list[str]:
    if direction == "increase":
        return ["增持"]
    if direction == "decrease":
        return ["减持"]
    return ["增持", "减持"]
def _announcement_date(value: Any) -> str:
    try:
        timestamp = int(value) / 1000
        return datetime.fromtimestamp(timestamp, CN_TZ).date().isoformat()
    except Exception:
        return datetime.now(CN_TZ).date().isoformat()


def _infer_direction(title: str) -> str:
    has_increase = "增持" in title
    has_decrease = "减持" in title
    if has_increase and has_decrease:
        return "mixed"
    if has_decrease:
        return "decrease"
    if has_increase:
        return "increase"
    return "unknown"


def _infer_status(title: str) -> str:
    if re.search(r"完成|结果|届满|期限届满|终止|完毕", title):
        return "completed"
    if re.search(r"预披露|计划|拟", title):
        return "plan"
    if re.search(r"进展|实施|时间过半|比例达到|触及|累计", title):
        return "progress"
    return "other"


def _infer_shareholder_type(title: str) -> str:
    if re.search(r"控股股东|实际控制人|实控人", title):
        return "控股股东/实际控制人"
    if re.search(r"董事|监事|高级管理人员|高管|董监高", title):
        return "董监高"
    if re.search(r"5%以上|百分之五以上|持股5%|持股 5%", title):
        return "5%以上股东"
    if "股东" in title:
        return "股东"
    return ""


def _shareholder_hint(title: str, issuer_name: str = "") -> str:
    match = re.search(r"关于(.{0,32}?)(?:增持|减持|持股变动|股份变动)", title)
    if match:
        hint = _clean_holder_name(match.group(1))
        if _is_generic_holder_descriptor(hint) or _looks_like_issuer_title_candidate(hint, issuer_name):
            return ""
        return hint
    return ""


def _infer_risk_level(title: str, direction: str) -> str:
    important_holder = bool(re.search(r"控股股东|实际控制人|实控人|董监高|董事|高级管理人员|5%以上", title))
    if direction == "decrease":
        return "high" if important_holder or re.search(r"预披露|计划", title) else "medium"
    if direction == "mixed":
        return "medium"
    return "low" if not important_holder else "medium"


def _tags_for_record(title: str, direction: str, status: str, shareholder_type: str) -> list[str]:
    labels = {
        "increase": "增持",
        "decrease": "减持",
        "mixed": "增减持",
        "unknown": "持股变动",
    }
    status_labels = {
        "plan": "计划",
        "progress": "进展",
        "completed": "完成/终止",
        "other": "公告",
    }
    tags = [labels.get(direction, "持股变动"), status_labels.get(status, "公告")]
    if shareholder_type:
        tags.append(shareholder_type)
    if "可转债" in title:
        tags.append("可转债")
    return dedupe(tags)


def _apply_title_fallback(record: ShareholderChangeRecord) -> None:
    if not record.shareholder_names:
        record.shareholder_names = _title_holder_names(record.title, issuer_name=record.name)
    if not record.change_method:
        record.change_method = "、".join(_methods_from_text(record.title))
    if not record.change_reason:
        record.change_reason = _title_change_reason(record.title, record.direction)
    if not record.detail_summary:
        record.detail_summary = _title_detail_summary(record)
    if not record.evidence_excerpt:
        record.evidence_excerpt = record.title
    if record.detail_quality == "title_only" and (
        record.shareholder_names or record.shareholder_type or record.change_method or record.change_reason
    ):
        record.detail_quality = "partial"


def _title_holder_names(title: str, issuer_name: str = "") -> list[str]:
    candidates: list[str] = []
    for pattern in [
        r"关于(.{1,36}?)(?:增持|减持|持股变动|股份变动|权益变动)",
        r"(.{1,36}?)(?:增持|减持)股份(?:计划|结果|进展|预披露)",
    ]:
        match = re.search(pattern, title)
        if match:
            candidates.extend(_split_holder_names(match.group(1)))
    return dedupe(
        [
            value
            for value in candidates
            if _looks_like_holder_name(value) and not _looks_like_issuer_title_candidate(value, issuer_name)
        ]
    )[:6]


def _looks_like_issuer_title_candidate(value: str, issuer_name: str) -> bool:
    if not issuer_name:
        return False
    compact_value = re.sub(r"\s+", "", value)
    compact_issuer = re.sub(r"\s+", "", issuer_name).lstrip("*ST")
    if not compact_issuer:
        return False
    return compact_issuer in compact_value


def _title_change_reason(title: str, direction: str) -> str:
    if direction == "increase":
        if re.search(r"基于对公司未来发展|对公司未来发展.*信心|认可公司长期价值", title):
            return "对公司未来发展及长期价值有信心"
    if direction == "decrease":
        if re.search(r"自身资金需求|个人资金需求|资金需求", title):
            return "资金需求"
    return ""


def _title_detail_summary(record: ShareholderChangeRecord) -> str:
    direction = {"increase": "增持", "decrease": "减持", "mixed": "增减持", "unknown": "持股变动"}.get(
        record.direction,
        "持股变动",
    )
    status = {"plan": "计划", "progress": "进展", "completed": "完成/终止", "other": "公告"}.get(record.status, "公告")
    parts = [
        f"{direction}{status}",
        record.shareholder_type,
        f"股东/人员：{'、'.join(record.shareholder_names[:4])}" if record.shareholder_names else record.shareholder_hint,
        f"方式：{record.change_method}" if record.change_method else "",
        "数量、比例、价格和期间需打开原文核验",
    ]
    return join_nonempty(parts, separator="；")


def _clean_html_cell(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " | ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    return " ".join(value.split()).strip()


def _compact_hk_disclosure_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()
    text = re.sub(r"(?:\s*\|\s*)+$", "", text)
    text = re.sub(r"\s*\|\s*", " / ", text)
    text = re.sub(r"(?:\s*/\s*){2,}", " / ", text)
    return text.strip(" /")


def _clean_hk_reason_code(value: str) -> str:
    text = _compact_hk_disclosure_text(value)
    match = re.search(r"\b(\d{4})\b", text)
    return match.group(1) if match else text


def _hk_position_marker_label(value: str) -> str:
    marker_match = re.search(r"\(([LSP])\)", value or "", re.I)
    marker = marker_match.group(1).upper() if marker_match else ""
    return {"L": "好仓", "S": "淡仓", "P": "可供借出的股份"}.get(marker, "")


def _format_hk_share_count(value: str) -> str:
    text = _compact_hk_disclosure_text(value)
    number_match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if not number_match:
        return re.sub(r"\([LSP]\)", "", text, flags=re.I).strip()
    number = number_match.group(0)
    parsed = to_float(number)
    if parsed is None:
        return f"{number} 股"
    return f"{parsed:,.0f} 股"


def _format_hk_price_text(value: str) -> str:
    text = _compact_hk_disclosure_text(value)
    if not text or text in {"-", "--"}:
        return ""
    return text


def _first_hk_number(value: str) -> Optional[float]:
    text = _compact_hk_disclosure_text(value)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    return to_float(match.group(0)) if match else None


def _parse_hk_price(value: str) -> tuple[str, Optional[float]]:
    text = _compact_hk_disclosure_text(value)
    match = re.search(r"\b([A-Z]{3})\b\s*(-?\d+(?:\.\d+)?)", text, re.I)
    if match:
        return match.group(1).upper(), to_float(match.group(2))
    fallback = re.search(r"-?\d+(?:\.\d+)?", text)
    return "HKD", to_float(fallback.group(0)) if fallback else None


def _format_hk_estimated_amount(shares_value: str, price_value: str) -> str:
    shares = _first_hk_number(shares_value)
    currency, price = _parse_hk_price(price_value)
    if shares is None or price is None or shares <= 0 or price <= 0:
        return ""
    return f"约 {currency} {shares * price / 100_000_000:,.2f} 亿"


def _hk_date_to_iso(value: str) -> str:
    text = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _infer_hk_direction(balance_cells: list[str], reason_code: str) -> tuple[str, str, str, str]:
    if len(balance_cells) >= 4:
        half = len(balance_cells) // 2
        previous_positions = _hk_position_pairs(balance_cells[:half])
        present_positions = _hk_position_pairs(balance_cells[half:])
        previous_long = _position_value(previous_positions, "L")
        present_long = _position_value(present_positions, "L")
        holding_before = _format_position_summary(previous_positions)
        holding_after = _format_position_summary(present_positions)
        ratio_before = _position_ratio(previous_positions, "L")
        ratio_after = _position_ratio(present_positions, "L")
        if previous_long is not None and present_long is not None:
            if present_long > previous_long:
                direction = "increase"
            elif present_long < previous_long:
                direction = "decrease"
            else:
                direction = "unknown"
            ratio_change = join_nonempty(
                [
                    f"{ratio_before}%" if ratio_before else "",
                    f"{ratio_after}%" if ratio_after else "",
                ],
                separator=" → ",
            )
            return direction, holding_before, holding_after, ratio_change

    compact_reason = re.sub(r"\s+", "", reason_code or "")
    if compact_reason.startswith("11"):
        return "increase", "", "", ""
    if compact_reason.startswith("12"):
        return "decrease", "", "", ""
    return "unknown", "", "", ""


def _hk_position_pairs(cells: list[str]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    for index in range(0, max(0, len(cells) - 1), 2):
        shares = cells[index]
        ratio = cells[index + 1]
        marker_match = re.search(r"\(([LSP])\)", shares)
        marker = marker_match.group(1) if marker_match else ""
        pairs.append({"marker": marker, "shares": shares, "ratio": ratio})
    return pairs


def _position_value(positions: list[dict[str, str]], marker: str) -> Optional[float]:
    for position in positions:
        if position.get("marker") == marker:
            return _first_hk_number(position.get("shares", ""))
    return None


def _position_ratio(positions: list[dict[str, str]], marker: str) -> str:
    for position in positions:
        if position.get("marker") == marker:
            return position.get("ratio", "")
    return ""


def _format_position_summary(positions: list[dict[str, str]]) -> str:
    return " / ".join(
        join_nonempty(
            [
                _format_hk_share_count(position.get("shares", "")),
                f"{position.get('ratio', '')}%".strip("%") + "%" if position.get("ratio") else "",
                _hk_position_marker_label(position.get("shares", "")),
            ],
            separator=" · ",
        )
        for position in positions
        if position.get("shares")
    )


def _sec_filing_date(summary: str, updated: str) -> Optional[date]:
    filed_match = re.search(r"Filed:\s*</b>\s*(\d{4}-\d{2}-\d{2})", summary or "", re.I)
    if filed_match:
        return parse_date(filed_match.group(1))
    return parse_date(updated)


def _xml_text(node: ET.Element, path: str) -> str:
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _xml_bool(node: ET.Element, path: str) -> bool:
    return _xml_text(node, path).lower() == "true"


def _sec_transaction_from_xml(node: ET.Element) -> dict[str, str]:
    return {
        "code": _xml_text(node, "transactionCoding/transactionCode"),
        "date": _xml_text(node, "transactionDate/value"),
        "shares": _xml_text(node, "transactionAmounts/transactionShares/value"),
        "price": _xml_text(node, "transactionAmounts/transactionPricePerShare/value"),
        "holding_after": _xml_text(node, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
    }


def _sec_code_direction(code: str) -> str:
    clean = (code or "").strip().upper()
    if clean in {"P", "A"}:
        return "increase"
    if clean in {"S", "F", "D"}:
        return "decrease"
    return "unknown"


def _sec_direction_from_codes(codes: list[str]) -> str:
    directions = {_sec_code_direction(code) for code in codes}
    directions.discard("unknown")
    if "increase" in directions and "decrease" in directions:
        return "mixed"
    if "decrease" in directions:
        return "decrease"
    if "increase" in directions:
        return "increase"
    return "unknown"
def _sec_owner_from_title(title: str) -> str:
    match = re.search(r"4\s+-\s+(.+?)\s+\(\d+\)", title or "")
    return match.group(1).strip() if match else ""


def _sec_accession_from_url(url: str) -> str:
    match = re.search(r"/(\d{10}-\d{2}-\d{6})-index\.htm", url or "")
    return match.group(1) if match else ""


def _format_price_range(values: list[float], currency: str = "") -> str:
    if not values:
        return ""
    low = min(values)
    high = max(values)
    if abs(low - high) < 0.000001:
        return f"{currency}{low:,.4f}".rstrip("0").rstrip(".")
    return f"{currency}{low:,.4f} - {currency}{high:,.4f}".replace(".0000", "")


def _last_nonempty(values: list[str]) -> str:
    for value in reversed(values):
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


def _format_shares(value: float) -> str:
    return f"{value:,.0f} 股"


def _format_money(value: float, currency: str = "") -> str:
    return f"{currency}{value:,.2f}"


def _market_label(market: str) -> str:
    return {"A": "A 股", "HK": "港股", "US": "美股"}.get(market, market)


def _build_summary(
    records: list[ShareholderChangeRecord],
    direction: str,
    start_at: date,
    end_at: date,
    market: str = "A",
) -> str:
    increases = sum(1 for record in records if record.direction == "increase")
    decreases = sum(1 for record in records if record.direction == "decrease")
    mixed = sum(1 for record in records if record.direction == "mixed")
    high_risk = sum(1 for record in records if record.risk_level == "high")
    direction_text = {"all": "增减持", "increase": "增持", "decrease": "减持"}.get(direction, "增减持")
    return (
        f"{start_at.isoformat()} 至 {end_at.isoformat()} 共命中 {len(records)} 条 {_market_label(market)}{direction_text}相关披露；"
        f"其中增持 {increases} 条、减持 {decreases} 条、同时涉及增减持 {mixed} 条，高风险 {high_risk} 条。"
    )


def _default_record_sort_key(record: ShareholderChangeRecord) -> tuple[int, str]:
    parsed = parse_date(record.announcement_date) or date.min
    return (-parsed.toordinal(), record.symbol)


def _risk_rank(level: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(level, 0)
def _safe_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_values = value if isinstance(value, list) else [value]
    return dedupe([short_text(item, 90) for item in raw_values if str(item or "").strip()])


def _safe_interpret_tone(value: Any, default: str = "neutral") -> str:
    clean = str(value or "").strip().lower()
    if clean in {"positive", "watch", "risk", "neutral"}:
        return clean
    return default if default in {"positive", "watch", "risk", "neutral"} else "neutral"
