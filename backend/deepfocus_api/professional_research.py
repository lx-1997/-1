from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, status

from .data_sources import get_data_item
from .llm import CloudResearchLLM
from .schemas import (
    ProfessionalCitation,
    ProfessionalEvalCase,
    ProfessionalEvalCaseResult,
    ProfessionalEvalRunRequest,
    ProfessionalEvalRunResponse,
    ProfessionalMetricRecord,
    ProfessionalRagQueryRequest,
    ProfessionalRagQueryResponse,
    ProfessionalReportAnalysisRequest,
    ProfessionalReportAnalysisResponse,
    ProfessionalReportChunkRecord,
    ProfessionalReportIngestRequest,
    ProfessionalReportRecord,
)


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_PRO_RESEARCH_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".professional_research.sqlite3"),
    )
)

MAX_REPORT_TEXT_CHARS = int(os.getenv("DEEPFOCUS_PRO_RESEARCH_MAX_REPORT_CHARS", "200000"))
CHUNK_CHARS = int(os.getenv("DEEPFOCUS_PRO_RESEARCH_CHUNK_CHARS", "1200"))
CHUNK_OVERLAP = int(os.getenv("DEEPFOCUS_PRO_RESEARCH_CHUNK_OVERLAP", "120"))


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    kind: str
    priority: int


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition("revenue", "营业收入", ("营业收入", "营收", "收入合计", "revenue", "net sales"), "amount", 10),
    MetricDefinition("revenue_yoy", "营业收入同比", ("营业收入", "营收", "revenue"), "growth", 11),
    MetricDefinition("net_profit", "归母净利润", ("归母净利润", "净利润", "profit attributable", "net profit"), "amount", 20),
    MetricDefinition(
        "net_profit_yoy",
        "归母净利润同比",
        ("归母净利润", "净利润", "profit attributable", "net profit"),
        "growth",
        21,
    ),
    MetricDefinition(
        "adjusted_net_profit",
        "扣非净利润",
        ("扣非净利润", "扣除非经常性损益", "non-recurring", "adjusted net profit"),
        "amount",
        30,
    ),
    MetricDefinition("gross_margin", "毛利率", ("毛利率", "gross margin"), "ratio", 40),
    MetricDefinition("roe", "ROE", ("加权平均净资产收益率", "净资产收益率", "roe"), "ratio", 50),
    MetricDefinition(
        "operating_cash_flow",
        "经营活动现金流净额",
        ("经营活动产生的现金流量净额", "经营现金流", "operating cash flow", "cash flow from operating"),
        "amount",
        60,
    ),
    MetricDefinition("capex", "资本开支", ("资本开支", "资本性支出", "capital expenditure", "capex"), "amount", 70),
    MetricDefinition("eps", "每股收益", ("基本每股收益", "稀释每股收益", "每股收益", "eps"), "number", 80),
    MetricDefinition("total_assets", "总资产", ("资产总计", "总资产", "total assets"), "amount", 90),
    MetricDefinition("total_liabilities", "总负债", ("负债合计", "总负债", "total liabilities"), "amount", 100),
)

METRIC_BY_KEY = {definition.key: definition for definition in METRIC_DEFINITIONS}
AMOUNT_UNITS = (
    "亿元",
    "亿",
    "万元",
    "万",
    "元",
    "百万元",
    "百万",
    "千元",
    "人民币元",
    "rmb million",
    "rmb bn",
    "million",
    "billion",
)
RISK_TERMS = ("风险", "减值", "商誉", "诉讼", "质押", "监管", "处罚", "现金流", "存货", "应收", "负债", "亏损")
STOP_TOKENS = {
    "这份",
    "份报",
    "报告",
    "告披",
    "披露",
    "露的",
    "的是",
    "多少",
    "是否",
    "有没",
    "没有",
    "什么",
    "这份报",
    "份报告",
    "报告披",
    "告披露",
    "披露的",
    "的是多",
    "是多少",
    "有没有",
    "没有披",
    "披露了",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_professional_research_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS professional_reports (
                id TEXT PRIMARY KEY,
                source_item_id TEXT,
                title TEXT NOT NULL,
                symbol TEXT,
                report_type TEXT NOT NULL,
                period TEXT,
                parser TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS professional_report_chunks (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                page INTEGER,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS professional_financial_metrics (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                symbol TEXT,
                period TEXT,
                metric_key TEXT NOT NULL,
                metric_label TEXT NOT NULL,
                value REAL,
                normalized_value REAL,
                unit TEXT,
                raw_value TEXT NOT NULL,
                source_page INTEGER,
                source_excerpt TEXT NOT NULL,
                confidence REAL NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prof_reports_symbol ON professional_reports(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prof_chunks_report ON professional_report_chunks(report_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prof_metrics_report ON professional_financial_metrics(report_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prof_metrics_symbol ON professional_financial_metrics(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prof_metrics_key ON professional_financial_metrics(metric_key)")
        conn.commit()


def ingest_professional_report_from_item(request: ProfessionalReportIngestRequest) -> ProfessionalReportRecord:
    item = get_data_item(request.data_item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data item not found")
    return ingest_professional_report_text(
        text=item.text,
        title=request.title or item.title,
        symbol=request.symbol or item.symbol,
        report_type=request.report_type,
        period=request.period,
        source_item_id=item.id,
        parser=str(item.metadata.get("parser") or item.source_type),
        metadata={
            "source_name": item.source_name,
            "source_type": item.source_type,
            "source_url": item.url,
            "tags": request.tags or item.tags,
            "data_item_id": item.id,
        },
    )


def ingest_professional_report_text(
    *,
    text: str,
    title: str,
    symbol: Optional[str] = None,
    report_type: str = "other",
    period: Optional[str] = None,
    source_item_id: Optional[str] = None,
    parser: str = "text",
    metadata: Optional[dict[str, Any]] = None,
) -> ProfessionalReportRecord:
    init_professional_research_db()
    cleaned = _clean_text(text)[:MAX_REPORT_TEXT_CHARS]
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="报告文本为空，无法入库。")

    report_id = str(uuid.uuid4())
    timestamp = now_iso()
    inferred_period = period or _infer_period(cleaned) or _infer_period(title)
    normalized_symbol = symbol.strip().upper() if symbol else None
    record = {
        "id": report_id,
        "source_item_id": source_item_id,
        "title": title.strip()[:240] or "未命名财报",
        "symbol": normalized_symbol,
        "report_type": report_type,
        "period": inferred_period,
        "parser": parser or "text",
        "char_count": len(cleaned),
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    chunks = _build_chunks(cleaned, report_id=report_id, title=record["title"], created_at=timestamp)
    metrics = _extract_metrics(cleaned, report_id=report_id, symbol=normalized_symbol, period=inferred_period, created_at=timestamp)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO professional_reports (
                id, source_item_id, title, symbol, report_type, period, parser, char_count,
                metadata_json, created_at, updated_at
            ) VALUES (
                :id, :source_item_id, :title, :symbol, :report_type, :period, :parser, :char_count,
                :metadata_json, :created_at, :updated_at
            )
            """,
            record,
        )
        conn.executemany(
            """
            INSERT INTO professional_report_chunks (
                id, report_id, chunk_index, page, title, text, token_count, metadata_json, created_at
            ) VALUES (
                :id, :report_id, :chunk_index, :page, :title, :text, :token_count, :metadata_json, :created_at
            )
            """,
            chunks,
        )
        conn.executemany(
            """
            INSERT INTO professional_financial_metrics (
                id, report_id, symbol, period, metric_key, metric_label, value, normalized_value,
                unit, raw_value, source_page, source_excerpt, confidence, metadata_json, created_at
            ) VALUES (
                :id, :report_id, :symbol, :period, :metric_key, :metric_label, :value, :normalized_value,
                :unit, :raw_value, :source_page, :source_excerpt, :confidence, :metadata_json, :created_at
            )
            """,
            metrics,
        )
        conn.commit()
    return get_professional_report(report_id)  # type: ignore[return-value]


def list_professional_reports(
    *,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> list[ProfessionalReportRecord]:
    init_professional_research_db()
    clauses: list[str] = []
    values: list[Any] = []
    if symbol:
        clauses.append("report.symbol = ?")
        values.append(symbol.strip().upper())
    values.append(max(1, min(limit, 200)))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                report.*,
                COUNT(DISTINCT chunk.id) AS chunks_count,
                COUNT(DISTINCT metric.id) AS metrics_count
            FROM professional_reports report
            LEFT JOIN professional_report_chunks chunk ON chunk.report_id = report.id
            LEFT JOIN professional_financial_metrics metric ON metric.report_id = report.id
            {where}
            GROUP BY report.id
            ORDER BY report.created_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
    return [_row_to_report(dict(row)) for row in rows]


def get_professional_report(report_id: str) -> Optional[ProfessionalReportRecord]:
    init_professional_research_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                report.*,
                COUNT(DISTINCT chunk.id) AS chunks_count,
                COUNT(DISTINCT metric.id) AS metrics_count
            FROM professional_reports report
            LEFT JOIN professional_report_chunks chunk ON chunk.report_id = report.id
            LEFT JOIN professional_financial_metrics metric ON metric.report_id = report.id
            WHERE report.id = ?
            GROUP BY report.id
            """,
            (report_id,),
        ).fetchone()
    return _row_to_report(dict(row)) if row else None


def list_professional_metrics(
    *,
    report_id: Optional[str] = None,
    symbol: Optional[str] = None,
    metric_key: Optional[str] = None,
    limit: int = 100,
) -> list[ProfessionalMetricRecord]:
    init_professional_research_db()
    clauses: list[str] = []
    values: list[Any] = []
    if report_id:
        clauses.append("report_id = ?")
        values.append(report_id)
    if symbol:
        clauses.append("symbol = ?")
        values.append(symbol.strip().upper())
    if metric_key:
        clauses.append("metric_key = ?")
        values.append(metric_key)
    values.append(max(1, min(limit, 500)))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM professional_financial_metrics
            {where}
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
    return [_row_to_metric(dict(row)) for row in rows]


def list_professional_chunks(report_id: str, limit: int = 100) -> list[ProfessionalReportChunkRecord]:
    init_professional_research_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM professional_report_chunks
            WHERE report_id = ?
            ORDER BY chunk_index ASC
            LIMIT ?
            """,
            (report_id, max(1, min(limit, 1000))),
        ).fetchall()
    return [_row_to_chunk(dict(row)) for row in rows]


async def query_professional_rag(request: ProfessionalRagQueryRequest) -> ProfessionalRagQueryResponse:
    init_professional_research_db()
    citations = _retrieve_citations(
        question=request.question,
        report_id=request.report_id,
        symbol=request.symbol,
        period=request.period,
        top_k=request.top_k,
    )
    metric_citations = [citation for citation in citations if citation.kind == "metric"]
    if not citations:
        return ProfessionalRagQueryResponse(
            answer="我不知道。当前专业财报库没有检索到足够证据，不能编造结论。",
            citations=[],
            metrics=[],
            confidence=0.0,
            missing=["未命中结构化指标或原文片段"],
        )

    local_answer = _local_rag_answer(request.question, citations)
    if request.use_cloud_model:
        llm = CloudResearchLLM()
        if llm.provider != "mock":
            try:
                answer = await _cloud_rag_answer(llm, request.question, citations)
                local_answer = answer or local_answer
            except Exception:
                pass

    return ProfessionalRagQueryResponse(
        answer=local_answer,
        citations=citations,
        metrics=[_citation_to_metric(citation) for citation in metric_citations if _citation_to_metric(citation)],
        confidence=_confidence_from_citations(citations),
        missing=[],
    )


async def analyze_professional_report(
    report_id: str,
    request: ProfessionalReportAnalysisRequest,
) -> ProfessionalReportAnalysisResponse:
    report = get_professional_report(report_id)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Professional report not found")

    metrics = _best_metrics(list_professional_metrics(report_id=report_id, limit=300))
    metric_citations = _metric_citations(metrics)
    risk_citations = _retrieve_citations(
        question=" ".join([request.focus or "", " ".join(RISK_TERMS)]),
        report_id=report_id,
        symbol=report.symbol,
        top_k=5,
        include_metrics=False,
    )
    quality_flags = _quality_flags(metrics)
    risks = _risk_findings(risk_citations)
    follow_up_questions = _follow_up_questions(metrics, risks)
    summary = _analysis_summary(report, metrics, quality_flags, risks)
    citations = [*metric_citations[:8], *risk_citations[:5]]

    if request.use_cloud_model:
        llm = CloudResearchLLM()
        if llm.provider != "mock":
            try:
                cloud_summary = await _cloud_report_summary(llm, report, metrics, quality_flags, risks, citations)
                if cloud_summary:
                    summary = cloud_summary
            except Exception:
                pass

    return ProfessionalReportAnalysisResponse(
        report=report,
        summary=summary,
        key_metrics=metrics,
        quality_flags=quality_flags,
        risks=risks,
        follow_up_questions=follow_up_questions,
        citations=citations,
        confidence=_confidence_from_metrics(metrics, citations),
    )


async def run_professional_eval(request: ProfessionalEvalRunRequest) -> ProfessionalEvalRunResponse:
    init_professional_research_db()
    cases = request.cases or _default_eval_cases(report_id=request.report_id, symbol=request.symbol)
    results: list[ProfessionalEvalCaseResult] = []
    for case in cases:
        response = await query_professional_rag(
            ProfessionalRagQueryRequest(
                question=case.question,
                report_id=request.report_id,
                symbol=request.symbol,
                top_k=request.top_k,
                use_cloud_model=False,
            )
        )
        result = _grade_eval_case(case, response)
        results.append(result)

    total = len(results)
    passed = sum(1 for result in results if result.passed)
    citation_hits = sum(1 for result in results if result.citations_present)
    answer_hits = sum(1 for result in results if result.answer_match)
    refusal_hits = sum(1 for result in results if result.refusal_ok)
    return ProfessionalEvalRunResponse(
        generated_at=now_iso(),
        total=total,
        passed=passed,
        pass_rate=round(passed / total, 4) if total else 0.0,
        citation_rate=round(citation_hits / total, 4) if total else 0.0,
        answer_match_rate=round(answer_hits / total, 4) if total else 0.0,
        refusal_guard_rate=round(refusal_hits / total, 4) if total else 0.0,
        cases=results,
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _build_chunks(text: str, *, report_id: str, title: str, created_at: str) -> list[dict[str, Any]]:
    pages = _split_pages(text)
    chunks: list[dict[str, Any]] = []
    for page, page_text in pages:
        for chunk_text in _window_text(page_text, CHUNK_CHARS, CHUNK_OVERLAP):
            if not chunk_text.strip():
                continue
            chunks.append(
                {
                    "id": str(uuid.uuid4()),
                    "report_id": report_id,
                    "chunk_index": len(chunks),
                    "page": page,
                    "title": f"{title} p.{page}" if page else title,
                    "text": chunk_text.strip(),
                    "token_count": len(_tokenize(chunk_text)),
                    "metadata_json": json.dumps({}, ensure_ascii=False),
                    "created_at": created_at,
                }
            )
    return chunks


def _extract_metrics(
    text: str,
    *,
    report_id: str,
    symbol: Optional[str],
    period: Optional[str],
    created_at: str,
) -> list[dict[str, Any]]:
    pages = _split_pages(text)
    best: dict[str, dict[str, Any]] = {}
    for page, page_text in pages:
        for line in _metric_lines(page_text):
            for metric in _extract_line_metrics(line, page=page, report_id=report_id, symbol=symbol, period=period, created_at=created_at):
                key = metric["metric_key"]
                current = best.get(key)
                if not current or (metric["confidence"], -len(metric["source_excerpt"])) > (
                    current["confidence"],
                    -len(current["source_excerpt"]),
                ):
                    best[key] = metric
    return [best[key] for key in sorted(best, key=lambda item: METRIC_BY_KEY.get(item, MetricDefinition(item, item, (), "", 999)).priority)]


def _extract_line_metrics(
    line: str,
    *,
    page: Optional[int],
    report_id: str,
    symbol: Optional[str],
    period: Optional[str],
    created_at: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    lowered = line.lower()
    for definition in METRIC_DEFINITIONS:
        if not any(alias.lower() in lowered for alias in definition.aliases):
            continue
        if definition.kind == "growth" and not re.search(r"同比|较上年|增长|下降|increase|decrease|yoy", lowered, re.I):
            continue
        if definition.kind != "growth" and re.search(r"同比|较上年|增长率|变动比例|yoy", lowered, re.I):
            amount_match = _first_amount_match(line)
            if not amount_match:
                continue

        parsed = _parse_value_for_definition(line, definition)
        if not parsed:
            continue
        value, normalized, unit, raw_value = parsed
        confidence = _metric_confidence(line, definition, unit)
        if confidence <= 0:
            continue
        results.append(
            {
                "id": str(uuid.uuid4()),
                "report_id": report_id,
                "symbol": symbol,
                "period": period,
                "metric_key": definition.key,
                "metric_label": definition.label,
                "value": value,
                "normalized_value": normalized,
                "unit": unit,
                "raw_value": raw_value,
                "source_page": page,
                "source_excerpt": line[:500],
                "confidence": confidence,
                "metadata_json": json.dumps({"extractor": "regex-v1"}, ensure_ascii=False),
                "created_at": created_at,
            }
        )
    return results


def _parse_value_for_definition(
    line: str,
    definition: MetricDefinition,
) -> Optional[tuple[float, Optional[float], str, str]]:
    section = _metric_section(line, definition)
    if not section:
        return None

    if definition.kind == "ratio":
        match = re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|％|个百分点|pct)", section, re.I)
        if not match:
            return None
        value = _to_float(match.group(1))
        if value is None:
            return None
        unit = match.group(2)
        return value, value / 100 if unit in {"%", "％", "pct"} else value, unit, match.group(0)

    if definition.kind == "growth":
        match = re.search(r"(同比|较上年|yoy|增长|下降|increase|decrease)[^-\d+]{0,12}([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|％|个百分点|pct)", section, re.I)
        if not match:
            match = re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|％|个百分点|pct)", section, re.I)
        if not match:
            return None
        if match.lastindex == 3:
            raw_number = match.group(2)
            unit = match.group(3)
        else:
            raw_number = match.group(1)
            unit = match.group(2)
        value = _to_float(raw_number)
        if value is None:
            return None
        if re.search(r"下降|减少|decrease", section, re.I) and value > 0:
            value = -value
        return value, value / 100 if unit in {"%", "％", "pct"} else value, unit, match.group(0)

    if definition.kind == "number":
        match = re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(元/股|元|rmb|usd)?", section, re.I)
        if not match:
            return None
        value = _to_float(match.group(1))
        if value is None:
            return None
        unit = match.group(2) or ""
        return value, value, unit, match.group(0)

    match = _first_amount_match(section)
    if not match:
        return None
    value = _to_float(match.group(1))
    if value is None:
        return None
    unit = match.group(2).strip()
    return value, _normalize_amount(value, unit), unit, match.group(0)


def _metric_section(line: str, definition: MetricDefinition) -> str:
    lowered = line.lower()
    matches: list[tuple[int, int, str]] = []
    for alias in definition.aliases:
        start = 0
        alias_lower = alias.lower()
        while True:
            index = lowered.find(alias_lower, start)
            if index < 0:
                break
            prefix = lowered[max(0, index - 8) : index]
            if definition.key in {"net_profit", "net_profit_yoy"} and ("扣非" in prefix or "非经常" in prefix):
                start = index + len(alias_lower)
                continue
            matches.append((index, index + len(alias), alias))
            start = index + len(alias_lower)
    if not matches:
        return ""
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    start, alias_end, _alias = matches[0]
    next_positions: list[int] = []
    for other in METRIC_DEFINITIONS:
        if other.key == definition.key:
            continue
        for alias in other.aliases:
            alias_index = lowered.find(alias.lower(), alias_end)
            if alias_index > alias_end:
                next_positions.append(alias_index)
    end = min(next_positions) if next_positions else len(line)
    return line[start:end]


def _first_amount_match(line: str) -> Optional[re.Match[str]]:
    unit_pattern = "|".join(re.escape(unit) for unit in AMOUNT_UNITS)
    return re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(" + unit_pattern + r")", line, re.I)


def _normalize_amount(value: float, unit: str) -> Optional[float]:
    unit_lower = unit.lower()
    if unit_lower in {"亿元", "亿", "rmb bn", "billion"}:
        return value * 100_000_000
    if unit_lower in {"万元", "万"}:
        return value * 10_000
    if unit_lower in {"百万元", "百万", "rmb million", "million"}:
        return value * 1_000_000
    if unit_lower == "千元":
        return value * 1_000
    if unit_lower in {"元", "人民币元"}:
        return value
    return value


def _metric_confidence(line: str, definition: MetricDefinition, unit: str) -> float:
    confidence = 0.62
    if any(alias in line for alias in definition.aliases):
        confidence += 0.12
    if definition.kind in {"ratio", "growth"} and unit in {"%", "％", "pct", "个百分点"}:
        confidence += 0.12
    if definition.kind == "amount" and unit:
        confidence += 0.1
    if "：" in line or ":" in line or "|" in line:
        confidence += 0.04
    if len(line) > 260:
        confidence -= 0.08
    return round(max(0.0, min(confidence, 0.96)), 4)


def _retrieve_citations(
    *,
    question: str,
    report_id: Optional[str],
    symbol: Optional[str],
    period: Optional[str] = None,
    top_k: int = 5,
    include_metrics: bool = True,
) -> list[ProfessionalCitation]:
    query_tokens = _tokenize(question)
    metric_keys = _metric_keys_for_question(question)
    citations: list[tuple[float, ProfessionalCitation]] = []

    if include_metrics:
        metrics = list_professional_metrics(report_id=report_id, symbol=symbol, limit=300)
        for metric in metrics:
            score = _metric_score(metric, question, query_tokens, metric_keys)
            if period and metric.period and period not in metric.period:
                score -= 0.15
            if score > 0:
                citations.append((score, _metric_to_citation(metric, score, len([item for item in citations if item[1].kind == "metric"]) + 1)))

    chunks = _candidate_chunks(report_id=report_id, symbol=symbol, limit=500)
    for chunk in chunks:
        score = _chunk_score(chunk, question, query_tokens)
        if score > 0:
            citations.append((score, _chunk_to_citation(chunk, score, len([item for item in citations if item[1].kind == "chunk"]) + 1)))

    citations.sort(key=lambda item: item[0], reverse=True)
    selected: list[ProfessionalCitation] = []
    seen: set[tuple[str, str]] = set()
    for _score, citation in citations:
        dedupe_key = (citation.kind, citation.source_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        selected.append(citation)
        if len(selected) >= max(1, min(top_k, 12)):
            break
    for index, citation in enumerate(selected, start=1):
        prefix = "M" if citation.kind == "metric" else "C"
        citation.citation_id = f"{prefix}{index}"
    return selected


def _candidate_chunks(
    *,
    report_id: Optional[str],
    symbol: Optional[str],
    limit: int,
) -> list[ProfessionalReportChunkRecord]:
    clauses: list[str] = []
    values: list[Any] = []
    join = ""
    if report_id:
        clauses.append("chunk.report_id = ?")
        values.append(report_id)
    if symbol:
        join = "JOIN professional_reports report ON report.id = chunk.report_id"
        clauses.append("report.symbol = ?")
        values.append(symbol.strip().upper())
    values.append(max(1, min(limit, 1000)))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT chunk.*
            FROM professional_report_chunks chunk
            {join}
            {where}
            ORDER BY chunk.created_at DESC, chunk.chunk_index ASC
            LIMIT ?
            """,
            values,
        ).fetchall()
    return [_row_to_chunk(dict(row)) for row in rows]


def _metric_score(
    metric: ProfessionalMetricRecord,
    question: str,
    query_tokens: list[str],
    metric_keys: set[str],
) -> float:
    definition = METRIC_BY_KEY.get(metric.metric_key)
    score = 0.0
    if metric.metric_key in metric_keys:
        score += 1.8
    if definition and any(alias.lower() in question.lower() for alias in definition.aliases):
        score += 1.2
    haystack = f"{metric.metric_label} {metric.metric_key} {metric.source_excerpt}".lower()
    score += sum(0.08 for token in _meaningful_tokens(query_tokens) if token and token in haystack)
    score += metric.confidence * 0.4
    return score if score >= 0.45 else 0.0


def _chunk_score(chunk: ProfessionalReportChunkRecord, question: str, query_tokens: list[str]) -> float:
    haystack = f"{chunk.title}\n{chunk.text}".lower()
    score = 0.0
    lowered_question = question.lower()
    for term in RISK_TERMS:
        if term in lowered_question and term in haystack:
            score += 0.6
    for token in _meaningful_tokens(query_tokens):
        if not token:
            continue
        count = haystack.count(token)
        if count:
            score += min(0.6, 0.12 * count)
    return score


def _local_rag_answer(question: str, citations: list[ProfessionalCitation]) -> str:
    metric_lines = []
    chunk_lines = []
    for citation in citations:
        ref = f"[{citation.citation_id}]"
        if citation.kind == "metric":
            label = citation.metadata.get("metric_label") or citation.title
            raw_value = citation.metadata.get("raw_value") or citation.text
            period = citation.metadata.get("period")
            metric_lines.append(f"{label}{f'（{period}）' if period else ''}为 {raw_value} {ref}")
        else:
            excerpt = _first_sentence(citation.text)
            page = f"第 {citation.page} 页" if citation.page else "原文片段"
            chunk_lines.append(f"{page}提到：{excerpt} {ref}")

    if not metric_lines and not chunk_lines:
        return "我不知道。当前专业财报库没有足够证据。"
    lines = ["基于已入库的结构化指标和原文证据："]
    lines.extend(f"- {line}" for line in metric_lines[:4])
    lines.extend(f"- {line}" for line in chunk_lines[:3])
    if "吗" in question or "是否" in question:
        lines.append("以上只能说明资料中出现了这些证据；未命中的部分应视为待核验。")
    return "\n".join(lines)


async def _cloud_rag_answer(
    llm: CloudResearchLLM,
    question: str,
    citations: list[ProfessionalCitation],
) -> str:
    context = [
        {
            "id": citation.citation_id,
            "kind": citation.kind,
            "title": citation.title,
            "page": citation.page,
            "text": citation.text[:900],
            "metadata": citation.metadata,
        }
        for citation in citations
    ]
    prompt = (
        "你是金融财报引用型RAG助手。只能基于 citations 回答，不能引入外部知识。"
        "每个事实句必须包含 [M1] 或 [C1] 这种引用；证据不足时说“我不知道”。"
        "返回严格 JSON：answer, used_citations, missing。\n"
        f"问题：{question}\n"
        f"citations：{json.dumps(context, ensure_ascii=False)}"
    )
    data = await llm.complete_json(prompt, max_tokens=1200, timeout_seconds=20, force_json_first=True)
    answer = str(data.get("answer") or "").strip()
    return answer


async def _cloud_report_summary(
    llm: CloudResearchLLM,
    report: ProfessionalReportRecord,
    metrics: list[ProfessionalMetricRecord],
    quality_flags: list[str],
    risks: list[str],
    citations: list[ProfessionalCitation],
) -> str:
    payload = {
        "report": report.model_dump(),
        "metrics": [metric.model_dump() for metric in metrics[:12]],
        "quality_flags": quality_flags,
        "risks": risks,
        "citations": [citation.model_dump() for citation in citations[:10]],
    }
    prompt = (
        "你是专业财报分析Agent。只能基于输入数据总结，不能编造新数字。"
        "输出 JSON 字段 summary；summary 不超过 220 个中文字符，关键数字必须带引用ID。"
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )
    data = await llm.complete_json(prompt, max_tokens=700, timeout_seconds=20, force_json_first=True)
    return str(data.get("summary") or "").strip()


def _best_metrics(metrics: list[ProfessionalMetricRecord]) -> list[ProfessionalMetricRecord]:
    best: dict[str, ProfessionalMetricRecord] = {}
    for metric in metrics:
        current = best.get(metric.metric_key)
        if not current or metric.confidence > current.confidence:
            best[metric.metric_key] = metric
    return sorted(best.values(), key=lambda metric: METRIC_BY_KEY.get(metric.metric_key, MetricDefinition(metric.metric_key, metric.metric_label, (), "", 999)).priority)


def _quality_flags(metrics: list[ProfessionalMetricRecord]) -> list[str]:
    by_key = {metric.metric_key: metric for metric in metrics}
    flags: list[str] = []
    net_profit = by_key.get("net_profit")
    adjusted = by_key.get("adjusted_net_profit")
    ocf = by_key.get("operating_cash_flow")
    gross_margin = by_key.get("gross_margin")

    if net_profit and adjusted and net_profit.normalized_value and adjusted.normalized_value:
        gap = abs(net_profit.normalized_value - adjusted.normalized_value) / max(abs(net_profit.normalized_value), 1)
        if gap >= 0.2:
            flags.append("净利润与扣非净利润差异超过20%，需要核验非经常性损益来源。")
    if net_profit and ocf and net_profit.normalized_value and ocf.normalized_value is not None:
        if net_profit.normalized_value > 0 and ocf.normalized_value < 0:
            flags.append("净利润为正但经营现金流为负，利润质量需要重点复核。")
        elif ocf.normalized_value < net_profit.normalized_value * 0.5:
            flags.append("经营现金流低于净利润的一半，需关注回款和营运资本压力。")
    if gross_margin and gross_margin.value is not None and gross_margin.value < 15:
        flags.append("毛利率低于15%，需要和同业及历史水平比较。")
    if not flags:
        flags.append("未从已抽取指标中发现明显利润质量红旗，但仍需结合历史期和同业复核。")
    return flags[:6]


def _risk_findings(citations: list[ProfessionalCitation]) -> list[str]:
    findings: list[str] = []
    for citation in citations:
        if citation.kind != "chunk":
            continue
        sentence = _risk_sentence(citation.text)
        if sentence and any(term in sentence for term in RISK_TERMS):
            findings.append(f"{sentence} [{citation.citation_id}]")
    return _dedupe(findings)[:6] or ["原文风险片段命中较少，建议补充年报风险章节、审计意见和电话会纪要。"]


def _follow_up_questions(metrics: list[ProfessionalMetricRecord], risks: list[str]) -> list[str]:
    keys = {metric.metric_key for metric in metrics}
    questions = []
    if "revenue_yoy" not in keys:
        questions.append("收入增速没有稳定抽取出来，原文中同比口径是什么？")
    if "adjusted_net_profit" not in keys:
        questions.append("扣非净利润缺失或识别失败，非经常性损益是否影响利润质量？")
    if "operating_cash_flow" not in keys:
        questions.append("经营现金流缺失，需要从现金流量表补齐。")
    if risks and "命中较少" in risks[0]:
        questions.append("风险章节、审计意见和或有事项是否已完整入库？")
    questions.extend(
        [
            "本期核心指标与过去三年趋势相比是否改善？",
            "管理层指引和资本开支计划是否支持当前增长假设？",
        ]
    )
    return _dedupe(questions)[:6]


def _analysis_summary(
    report: ProfessionalReportRecord,
    metrics: list[ProfessionalMetricRecord],
    quality_flags: list[str],
    risks: list[str],
) -> str:
    parts = [f"{report.title} 已进入专业财报库，解析出 {len(metrics)} 个核心指标。"]
    lead_metrics = []
    for metric in metrics[:4]:
        lead_metrics.append(f"{metric.metric_label} {metric.raw_value}")
    if lead_metrics:
        parts.append("核心指标：" + "；".join(lead_metrics) + "。")
    if quality_flags:
        parts.append("质量复核：" + quality_flags[0])
    if risks:
        parts.append("风险复核：" + risks[0])
    return "".join(parts)


def _metric_citations(metrics: list[ProfessionalMetricRecord]) -> list[ProfessionalCitation]:
    citations = [_metric_to_citation(metric, metric.confidence, index) for index, metric in enumerate(metrics, start=1)]
    for index, citation in enumerate(citations, start=1):
        citation.citation_id = f"M{index}"
    return citations


def _metric_to_citation(metric: ProfessionalMetricRecord, score: float, index: int) -> ProfessionalCitation:
    report = get_professional_report(metric.report_id)
    return ProfessionalCitation(
        citation_id=f"M{index}",
        kind="metric",
        source_id=metric.id,
        report_id=metric.report_id,
        report_title=report.title if report else "",
        title=metric.metric_label,
        page=metric.source_page,
        text=metric.source_excerpt,
        score=round(score, 4),
        metadata={
            "metric_key": metric.metric_key,
            "metric_label": metric.metric_label,
            "value": metric.value,
            "normalized_value": metric.normalized_value,
            "unit": metric.unit,
            "raw_value": metric.raw_value,
            "period": metric.period,
            "confidence": metric.confidence,
        },
    )


def _chunk_to_citation(chunk: ProfessionalReportChunkRecord, score: float, index: int) -> ProfessionalCitation:
    report = get_professional_report(chunk.report_id)
    return ProfessionalCitation(
        citation_id=f"C{index}",
        kind="chunk",
        source_id=chunk.id,
        report_id=chunk.report_id,
        report_title=report.title if report else "",
        title=chunk.title,
        page=chunk.page,
        text=chunk.text[:1400],
        score=round(score, 4),
        metadata=chunk.metadata,
    )


def _citation_to_metric(citation: ProfessionalCitation) -> Optional[ProfessionalMetricRecord]:
    if citation.kind != "metric":
        return None
    metric = citation.metadata
    return ProfessionalMetricRecord(
        id=citation.source_id,
        report_id=citation.report_id,
        symbol=None,
        period=metric.get("period"),
        metric_key=str(metric.get("metric_key") or ""),
        metric_label=str(metric.get("metric_label") or citation.title),
        value=metric.get("value"),
        normalized_value=metric.get("normalized_value"),
        unit=metric.get("unit"),
        raw_value=str(metric.get("raw_value") or ""),
        source_page=citation.page,
        source_excerpt=citation.text,
        confidence=float(metric.get("confidence") or 0),
        metadata={},
        created_at="",
    )


def _default_eval_cases(report_id: Optional[str], symbol: Optional[str]) -> list[ProfessionalEvalCase]:
    metrics = _best_metrics(list_professional_metrics(report_id=report_id, symbol=symbol, limit=200))
    cases: list[ProfessionalEvalCase] = []
    for metric in metrics[:4]:
        cases.append(
            ProfessionalEvalCase(
                question=f"这份报告披露的{metric.metric_label}是多少？",
                expected_text=metric.raw_value,
                expected_metric_key=metric.metric_key,
                must_cite=True,
            )
        )
    cases.append(
        ProfessionalEvalCase(
            question="这份报告披露的董事会秘书联系电话是多少？",
            expected_refusal=True,
            must_cite=False,
        )
    )
    return cases[:6]


def _grade_eval_case(case: ProfessionalEvalCase, response: ProfessionalRagQueryResponse) -> ProfessionalEvalCaseResult:
    answer = response.answer or ""
    citation_ids = [citation.citation_id for citation in response.citations]
    citations_present = bool(citation_ids) and all(f"[{cid}]" in answer for cid in citation_ids[:1])
    answer_match = True
    notes: list[str] = []
    if case.expected_text:
        expected_norm = _normalize_for_match(case.expected_text)
        answer_match = expected_norm in _normalize_for_match(answer)
        if not answer_match:
            notes.append("答案未包含期望文本")
    if case.expected_metric_key:
        metric_hit = any(citation.metadata.get("metric_key") == case.expected_metric_key for citation in response.citations)
        answer_match = answer_match and metric_hit
        if not metric_hit:
            notes.append("未命中期望指标")
    refusal_ok = True
    if case.expected_refusal:
        refusal_ok = not response.citations or any(token in answer for token in ("不知道", "没有检索到", "证据不足", "不能编造"))
        if not refusal_ok:
            notes.append("应拒答的问题没有触发缺口保护")
    cite_ok = citations_present if case.must_cite else True
    if case.must_cite and not citations_present:
        notes.append("答案缺少引用标记")
    passed = answer_match and refusal_ok and cite_ok
    return ProfessionalEvalCaseResult(
        question=case.question,
        expected_text=case.expected_text,
        expected_metric_key=case.expected_metric_key,
        expected_refusal=case.expected_refusal,
        answer=answer,
        citations=[citation.model_dump() for citation in response.citations],
        citations_present=citations_present,
        answer_match=answer_match,
        refusal_ok=refusal_ok,
        passed=passed,
        score=1.0 if passed else 0.0,
        notes=notes,
    )


def _split_pages(text: str) -> list[tuple[Optional[int], str]]:
    matches = list(re.finditer(r"^\[Page\s+(\d+)\]\s*$", text, flags=re.M | re.I))
    if not matches:
        return [(None, text)]
    pages: list[tuple[Optional[int], str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((int(match.group(1)), text[start:end].strip()))
    return pages


def _window_text(text: str, size: int, overlap: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= size:
            current = paragraph
        else:
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + size])
                start += max(1, size - overlap)
            current = ""
    if current:
        chunks.append(current)
    return chunks or [text[:size]]


def _metric_lines(text: str) -> list[str]:
    lines = []
    for line in re.split(r"[\n\r]+", text):
        clean = re.sub(r"\s+", " ", line).strip()
        if 4 <= len(clean) <= 700 and any(alias.lower() in clean.lower() for definition in METRIC_DEFINITIONS for alias in definition.aliases):
            lines.append(clean)
    return lines


def _infer_period(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    patterns = (
        r"(20\d{2})\s*年\s*(年度|年报|半年度|半年报|第一季度|一季报|第三季度|三季报|前三季度)",
        r"(20\d{2})\s*(Q[1-4]|H1|H2|FY)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return "".join(match.groups())
    return None


def _metric_keys_for_question(question: str) -> set[str]:
    lowered = question.lower()
    keys = set()
    asks_growth = bool(re.search(r"同比|增速|增长|下降|yoy|increase|decrease", lowered, re.I))
    for definition in METRIC_DEFINITIONS:
        if definition.kind == "growth" and not asks_growth:
            continue
        if definition.key in lowered or definition.label.lower() in lowered:
            keys.add(definition.key)
        if any(alias.lower() in lowered for alias in definition.aliases):
            keys.add(definition.key)
    if "同比" in question:
        keys = {key for key in keys if key.endswith("_yoy")} or keys
    if "扣非" in question:
        keys.add("adjusted_net_profit")
    return keys


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]*|\d+(?:\.\d+)?%?", lowered)
    for block in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
        tokens.extend(block[index : index + 2] for index in range(max(0, len(block) - 1)))
        tokens.extend(block[index : index + 3] for index in range(max(0, len(block) - 2)))
    return [token for token in tokens if token.strip()]


def _meaningful_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in STOP_TOKENS and len(token) >= 2]


def _confidence_from_citations(citations: list[ProfessionalCitation]) -> float:
    if not citations:
        return 0.0
    score = sum(min(1.0, citation.score) for citation in citations[:5]) / min(len(citations), 5)
    metric_bonus = 0.08 if any(citation.kind == "metric" for citation in citations) else 0.0
    return round(max(0.0, min(0.92, score + metric_bonus)), 4)


def _confidence_from_metrics(metrics: list[ProfessionalMetricRecord], citations: list[ProfessionalCitation]) -> float:
    if not metrics:
        return 0.35 if citations else 0.1
    avg = sum(metric.confidence for metric in metrics[:8]) / min(len(metrics), 8)
    coverage = min(0.2, len(metrics) * 0.02)
    return round(max(0.0, min(0.92, avg + coverage)), 4)


def _first_sentence(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s+", clean)
    return parts[0][:220]


def _risk_sentence(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    candidates = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+|[\n\r]+", clean) if part.strip()]
    if not candidates:
        return ""

    def score(sentence: str) -> int:
        base = sum(1 for term in RISK_TERMS if term in sentence)
        base += 2 if any(term in sentence for term in ("主要风险", "承压", "不确定", "下滑", "恶化")) else 0
        return base

    return max(candidates, key=score)[:220]


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower().replace("％", "%")


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = _normalize_for_match(line)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(line)
    return output


def _row_to_report(row: dict[str, Any]) -> ProfessionalReportRecord:
    return ProfessionalReportRecord(
        id=row["id"],
        source_item_id=row.get("source_item_id"),
        title=row["title"],
        symbol=row.get("symbol"),
        report_type=row["report_type"],
        period=row.get("period"),
        parser=row["parser"],
        char_count=int(row["char_count"]),
        metadata=json.loads(row.get("metadata_json") or "{}"),
        metrics_count=int(row.get("metrics_count") or 0),
        chunks_count=int(row.get("chunks_count") or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_chunk(row: dict[str, Any]) -> ProfessionalReportChunkRecord:
    return ProfessionalReportChunkRecord(
        id=row["id"],
        report_id=row["report_id"],
        chunk_index=int(row["chunk_index"]),
        page=row.get("page"),
        title=row["title"],
        text=row["text"],
        token_count=int(row["token_count"]),
        metadata=json.loads(row.get("metadata_json") or "{}"),
        created_at=row["created_at"],
    )


def _row_to_metric(row: dict[str, Any]) -> ProfessionalMetricRecord:
    return ProfessionalMetricRecord(
        id=row["id"],
        report_id=row["report_id"],
        symbol=row.get("symbol"),
        period=row.get("period"),
        metric_key=row["metric_key"],
        metric_label=row["metric_label"],
        value=row.get("value"),
        normalized_value=row.get("normalized_value"),
        unit=row.get("unit"),
        raw_value=row["raw_value"],
        source_page=row.get("source_page"),
        source_excerpt=row["source_excerpt"],
        confidence=float(row["confidence"]),
        metadata=json.loads(row.get("metadata_json") or "{}"),
        created_at=row["created_at"],
    )
