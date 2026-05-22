from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sqlite3
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from .agent_engines import AgentEngineRunContext, TradingAgentsAdapter
from .customs_hs_detail import build_customs_hs_detail_analysis_text, fetch_customs_hs_detail_snapshot
from .customs_trade import build_customs_trade_analysis_text, fetch_customs_trade_snapshot
from .data_sources import collect_task_evidence, keyword_crawl_data_source
from .llm import CloudResearchLLM
from .market_data import fetch_market_quotes
from .options_signal import fetch_options_signals
from .professional_research import analyze_professional_report, list_professional_reports, query_professional_rag
from .schemas import (
    DataSourceKeywordCrawlRequest,
    InvestmentTaskCreateRequest,
    InvestmentTaskRecord,
    ProfessionalRagQueryRequest,
    ProfessionalReportAnalysisRequest,
)


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_AGENT_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".agent_tasks.sqlite3"),
    )
)

WORKER_POLL_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_WORKER_POLL_SECONDS", "2.5"))
AGENT_LLM_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_LLM_TIMEOUT_SECONDS", "120"))
AGENT_REPORT_CLOUD_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_REPORT_CLOUD_TIMEOUT_SECONDS", "90"))
RUNNING_TASK_STALE_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_RUNNING_STALE_SECONDS", "300"))
AGENT_HEARTBEAT_LOG_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_HEARTBEAT_LOG_SECONDS", "60"))
MARKET_QUOTE_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_QUOTE_TIMEOUT_SECONDS", "8"))
SEC_RESEARCH_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_SEC_TIMEOUT_SECONDS", "10"))
SEC_RESEARCH_USER_AGENT = os.getenv(
    "DEEPFOCUS_SEC_USER_AGENT",
    "DeepFocus Agent Research Tool contact@deepfocus.local",
)
AGENT_CONTEXT_WINDOW_TOKENS = int(os.getenv("DEEPFOCUS_AGENT_CONTEXT_WINDOW_TOKENS", "24000"))
AGENT_CONTEXT_RESERVED_TOKENS = int(os.getenv("DEEPFOCUS_AGENT_CONTEXT_RESERVED_TOKENS", "1800"))
AGENT_CLOUD_OUTPUT_TOKENS = int(os.getenv("DEEPFOCUS_AGENT_CLOUD_OUTPUT_TOKENS", "2400"))
AGENT_COMPACT_EVIDENCE_LIMIT = int(os.getenv("DEEPFOCUS_AGENT_COMPACT_EVIDENCE_LIMIT", "8"))

IMPORTANT_TOOL_LINE_RE = re.compile(
    r"("
    r"traceback|exception|error|failed|failure|fatal|timeout|timed out|invalid|denied|"
    r"unauthorized|quota|warning|warn|not found|stack trace|"
    r"错误|异常|失败|超时|告警|警告|拒绝|未找到|不可用"
    r")",
    re.IGNORECASE,
)
PATH_OR_URL_RE = re.compile(
    r"https?://[^\s，。；;）)]+|"
    r"(?:/[\w.\-+/]+)|"
    r"(?:[\w.\-]+/[\w.\-+/]+\.[A-Za-z0-9]{1,8})"
)

_worker_task: Optional[asyncio.Task] = None
_worker_stop_event: Optional[asyncio.Event] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_task_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                symbol TEXT,
                asset_name TEXT,
                task_type TEXT NOT NULL,
                engine TEXT NOT NULL DEFAULT 'deepfocus',
                status TEXT NOT NULL,
                priority INTEGER NOT NULL,
                assigned_agent TEXT,
                progress INTEGER NOT NULL DEFAULT 0,
                input_json TEXT NOT NULL,
                result_json TEXT,
                logs_json TEXT NOT NULL,
                error TEXT,
                runtime_pid INTEGER,
                runtime_kind TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_priority ON agent_tasks(status, priority, created_at)"
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_tasks)").fetchall()}
        if "engine" not in columns:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN engine TEXT NOT NULL DEFAULT 'deepfocus'")
        if "runtime_pid" not in columns:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN runtime_pid INTEGER")
        if "runtime_kind" not in columns:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN runtime_kind TEXT")
        conn.commit()
    recover_stale_running_tasks()


def create_investment_task(request: InvestmentTaskCreateRequest) -> InvestmentTaskRecord:
    init_task_db()
    task_id = str(uuid.uuid4())
    timestamp = now_iso()
    input_payload = request.model_dump()
    record = {
        "id": task_id,
        "title": request.title,
        "symbol": request.symbol,
        "asset_name": request.asset_name,
        "task_type": request.task_type,
        "engine": request.engine,
        "status": "pending",
        "priority": request.priority,
        "assigned_agent": "OrchestratorAgent",
        "progress": 0,
        "input_json": json.dumps(input_payload, ensure_ascii=False),
        "result_json": None,
        "logs_json": json.dumps(
            [
                {
                    "timestamp": timestamp,
                    "agent": "TaskCenter",
                    "message": "任务已进入投研队列，等待多 Agent 调度。",
                }
            ],
            ensure_ascii=False,
        ),
        "error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "started_at": None,
        "completed_at": None,
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks (
                id, title, symbol, asset_name, task_type, engine, status, priority, assigned_agent,
                progress, input_json, result_json, logs_json, error, created_at, updated_at,
                started_at, completed_at
            ) VALUES (
                :id, :title, :symbol, :asset_name, :task_type, :engine, :status, :priority,
                :assigned_agent, :progress, :input_json, :result_json, :logs_json,
                :error, :created_at, :updated_at, :started_at, :completed_at
            )
            """,
            record,
        )
        conn.commit()
    return _row_to_record(record)


def list_investment_tasks(limit: int = 50) -> list[InvestmentTaskRecord]:
    init_task_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_record(dict(row)) for row in rows]


def get_investment_task(task_id: str) -> Optional[InvestmentTaskRecord]:
    init_task_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_record(dict(row)) if row else None


def retry_investment_task(task_id: str) -> Optional[InvestmentTaskRecord]:
    task = get_investment_task(task_id)
    if not task or task.status not in {"failed", "cancelled", "completed"}:
        return task
    logs = [entry.model_dump() for entry in task.logs]
    logs.append({"timestamp": now_iso(), "agent": "TaskCenter", "message": "任务已重新排队。"})
    _update_task(
        task_id,
        status="pending",
        progress=0,
        error=None,
        result_json=None,
        logs_json=json.dumps(logs, ensure_ascii=False),
        started_at=None,
        completed_at=None,
    )
    return get_investment_task(task_id)


def cancel_investment_task(task_id: str) -> Optional[InvestmentTaskRecord]:
    task = get_investment_task(task_id)
    if not task or task.status in {"completed", "failed", "cancelled"}:
        return task
    _terminate_task_runtime_process(task_id)
    logs = [entry.model_dump() for entry in task.logs]
    logs.append({"timestamp": now_iso(), "agent": "TaskCenter", "message": "用户取消任务。"})
    _update_task(
        task_id,
        status="cancelled",
        progress=task.progress,
        logs_json=json.dumps(logs, ensure_ascii=False),
        runtime_pid=None,
        runtime_kind=None,
        completed_at=now_iso(),
    )
    return get_investment_task(task_id)


def task_counts() -> dict[str, int]:
    init_task_db()
    with _connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM agent_tasks GROUP BY status").fetchall()
    counts = {row["status"]: int(row["count"]) for row in rows}
    return {
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
    }


def recover_stale_running_tasks(now: Optional[datetime] = None) -> int:
    """Move abandoned running tasks to failed so the UI can stop waiting forever."""
    timestamp_dt = now or datetime.now(timezone.utc)
    stale_cutoff = (timestamp_dt - timedelta(seconds=RUNNING_TASK_STALE_SECONDS)).isoformat()
    timestamp = timestamp_dt.isoformat()
    recovered = 0

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, progress, assigned_agent, logs_json, updated_at, runtime_pid, runtime_kind
            FROM agent_tasks
            WHERE status = 'running' AND updated_at < ?
            """,
            (stale_cutoff,),
        ).fetchall()

        for row in rows:
            try:
                progress = max(0, min(100, int(row["progress"] or 0)))
            except (TypeError, ValueError):
                progress = 0
            try:
                logs = json.loads(row["logs_json"] or "[]")
                if not isinstance(logs, list):
                    logs = []
            except json.JSONDecodeError:
                logs = []

            stale_seconds = int(RUNNING_TASK_STALE_SECONDS)
            terminated = _terminate_registered_runtime(row["runtime_pid"], row["runtime_kind"])
            message = (
                f"任务运行心跳超过 {stale_seconds} 秒没有更新，已标记为失败；"
                "通常是开发热重载或后端重启打断了长任务，可以重新运行。"
                + (" 已终止失联的外部运行进程。" if terminated else "")
            )
            logs.append(
                {
                    "timestamp": timestamp,
                    "agent": "TaskCenter",
                    "message": message,
                    "progress": progress,
                }
            )
            cursor = conn.execute(
                """
                UPDATE agent_tasks
                SET status = 'failed',
                    assigned_agent = 'TaskCenter',
                    error = ?,
                    logs_json = ?,
                    runtime_pid = NULL,
                    runtime_kind = NULL,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    message,
                    json.dumps(logs, ensure_ascii=False),
                    timestamp,
                    timestamp,
                    row["id"],
                ),
            )
            recovered += cursor.rowcount
        conn.commit()

    return recovered


async def start_agent_worker() -> None:
    global _worker_task, _worker_stop_event
    init_task_db()
    if _worker_task and not _worker_task.done():
        return
    _worker_stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(_worker_stop_event))


async def stop_agent_worker() -> None:
    global _worker_task, _worker_stop_event
    if _worker_stop_event:
        _worker_stop_event.set()
    if _worker_task:
        _worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await _worker_task


def is_worker_running() -> bool:
    return bool(_worker_task and not _worker_task.done())


async def _worker_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        with suppress(Exception):
            recover_stale_running_tasks()
        task = _claim_next_task()
        if task:
            await _process_task(task)
        else:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=WORKER_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


def _claim_next_task() -> Optional[InvestmentTaskRecord]:
    timestamp = now_iso()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM agent_tasks
            WHERE status = 'pending'
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'running', progress = 5, started_at = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (timestamp, timestamp, row["id"]),
        )
        conn.commit()
    task = get_investment_task(row["id"])
    if task:
        _append_log(task.id, "OrchestratorAgent", "任务已启动，开始拆解为多 Agent 投研流程。", progress=8)
    return task


async def _process_task(task: InvestmentTaskRecord) -> None:
    try:
        payload = task.input
        if task.task_type == "customs_trade_analysis" or payload.get("analysis_domain") == "customs_trade":
            await _process_customs_trade_task(task)
            return
        engine = task.engine or payload.get("engine") or "deepfocus"
        _append_log(task.id, "EvidenceAgent", "同步服务器/API/网页数据源，并检索本地上传资料。", progress=14)
        evidence = await collect_task_evidence(payload)
        evidence_message = (
            f"已命中 {len(evidence)} 条可追溯资料，进入投研上下文。"
            if evidence
            else "暂未命中外部资料，报告将明确提示资料不足。"
        )
        _append_log(task.id, "EvidenceAgent", evidence_message, progress=18)
        professional_evidence = await _collect_professional_research_evidence(payload)
        if professional_evidence:
            evidence = [*professional_evidence, *evidence]
            _append_log(
                task.id,
                "EvidenceAgent",
                f"研报模块命中 {len(professional_evidence)} 条专业证据，已注入投研上下文。",
                progress=20,
            )
        elif payload.get("symbol"):
            _append_log(
                task.id,
                "EvidenceAgent",
                "研报模块已查询，但未命中该标的的入库研报/财报。",
                progress=20,
            )
            external_research_evidence = await _collect_external_research_evidence(payload)
            if external_research_evidence:
                evidence = [*external_research_evidence, *evidence]
                _append_log(
                    task.id,
                    "EvidenceAgent",
                    f"研报未入库，已外查公开研究线索并命中 {len(external_research_evidence)} 条。",
                    progress=21,
                )
            else:
                _append_log(
                    task.id,
                    "EvidenceAgent",
                    "研报未入库，已尝试外查公开研究线索但暂未命中。",
                    progress=21,
                )
        capacity_evidence = _collect_ai_capacity_utilization_evidence(payload)
        if capacity_evidence:
            evidence = [*capacity_evidence, *evidence]
            _append_log(
                task.id,
                "EvidenceAgent",
                f"行业产能利用率模块命中 {len(capacity_evidence)} 条AI产业链证据，已纳入供需判断。",
                progress=22,
            )
        options_evidence = await _collect_options_signal_evidence(payload)
        if options_evidence:
            evidence = [*options_evidence, *evidence]
            has_signal = any(item.get("source_type") == "options_signal" for item in options_evidence)
            options_message = (
                f"期权模块命中 {len(options_evidence)} 条信号，已纳入买卖点与风险判断。"
                if has_signal
                else "期权模块已查询，但免费期权链暂未返回合约；已把数据源状态和风险提示纳入上下文。"
            )
            _append_log(
                task.id,
                "EvidenceAgent",
                options_message,
                progress=23,
            )
        elif _is_us_optionable_symbol(payload.get("symbol")):
            _append_log(
                task.id,
                "EvidenceAgent",
                "期权模块已查询，但免费期权链暂未返回可用信号。",
                progress=23,
            )
        market_quote = await _collect_market_quote(payload)
        if market_quote:
            _append_log(task.id, "EvidenceAgent", _quote_log_message(market_quote), progress=24)
        payload_with_evidence = {**payload, "evidence": evidence, "market_quote": market_quote}
        stages = _engine_stages(engine)
        for agent, message, progress in stages:
            _append_log(task.id, agent, message, progress=progress)
            await asyncio.sleep(0.15)

        if engine == "tradingagents":
            run_context = AgentEngineRunContext(
                task_id=task.id,
                title=task.title,
                symbol=task.symbol or payload.get("symbol") or "",
                asset_name=task.asset_name or payload.get("asset_name") or "",
                task_type=task.task_type,
                payload=payload_with_evidence,
                evidence=evidence,
                heartbeat=_make_task_heartbeat(task.id),
                register_runtime_process=_register_task_runtime_process(task.id),
            )
            engine_result = await TradingAgentsAdapter().run(run_context)
            for agent, message, progress in engine_result.logs:
                _append_log(task.id, agent, message, progress=progress)
            result = _normalize_investment_result(engine_result.result, task)
            if result.get("engine_status") != "completed":
                issue = _safe_short(
                    str(result.get("investor_summary") or result.get("plain_language_takeaway") or "TradingAgents 未稳定完成。"),
                    260,
                )
                _append_log(
                    task.id,
                    "ModelRouter",
                    f"TradingAgents 未在可用时间内完成，已切换为 DeepFocus Native 兜底报告：{issue}",
                    progress=92,
                )
                result = await _tradingagents_fallback_investment_result(
                    task,
                    evidence,
                    payload_with_evidence,
                    issue,
                )
        elif engine == "financial_services":
            llm = CloudResearchLLM()
            if llm.provider == "mock":
                result = _financial_services_playbook_result(task, evidence=evidence, payload_override=payload_with_evidence)
            else:
                try:
                    result = await asyncio.wait_for(
                        _cloud_investment_result(llm, task, payload_with_evidence),
                        timeout=min(AGENT_LLM_TIMEOUT_SECONDS, AGENT_REPORT_CLOUD_TIMEOUT_SECONDS),
                    )
                    result = _apply_financial_services_overlay(result, task, payload_with_evidence, evidence)
                except Exception as exc:
                    guidance = _cloud_failure_guidance(exc)
                    _append_log(
                        task.id,
                        "ModelRouter",
                        f"云模型不可用，已切换为 Financial Services 本地 playbook：{guidance}",
                        progress=92,
                    )
                    result = _financial_services_playbook_result(
                        task,
                        evidence=evidence,
                        payload_override=payload_with_evidence,
                        engine_status="cloud_fallback",
                    )
                    result["confidence"] = min(float(result.get("confidence", 0.5)), 0.58)
        else:
            llm = CloudResearchLLM()
            if llm.provider == "mock":
                result = _mock_investment_result(task, evidence=evidence, payload_override=payload_with_evidence)
            else:
                try:
                    result = await asyncio.wait_for(
                        _cloud_investment_result(llm, task, payload_with_evidence),
                        timeout=min(AGENT_LLM_TIMEOUT_SECONDS, AGENT_REPORT_CLOUD_TIMEOUT_SECONDS),
                    )
                except Exception as exc:
                    guidance = _cloud_failure_guidance(exc)
                    _append_log(
                        task.id,
                        "ModelRouter",
                        f"云模型不可用，已切换为本地投研兜底：{guidance}",
                        progress=92,
                    )
                    result = _cloud_fallback_investment_result(task, evidence, guidance, llm, payload_with_evidence)

        current = get_investment_task(task.id)
        if not current or current.status != "running":
            return
        _append_log(task.id, "ReportAgent", "最终报告已生成，等待投资者复核。", progress=98)
        _update_task(
            task.id,
            status="completed",
            progress=100,
            result_json=json.dumps(result, ensure_ascii=False),
            runtime_pid=None,
            runtime_kind=None,
            completed_at=now_iso(),
        )
    except Exception as exc:
        current = get_investment_task(task.id)
        if current and current.status == "cancelled":
            return
        _append_log(task.id, "OrchestratorAgent", f"任务失败：{exc}", progress=100)
        _terminate_task_runtime_process(task.id)
        _update_task(
            task.id,
            status="failed",
            error=str(exc),
            runtime_pid=None,
            runtime_kind=None,
            completed_at=now_iso(),
        )


def _engine_stages(engine: str) -> list[tuple[str, str, int]]:
    if engine == "tradingagents":
        return [
            ("ResearchAgent", "底层调用 TradingAgents analyst team 完成 market / news / fundamentals 分析。", 32),
            ("ResearchAgent", "吸收 bull / bear debate 与 trader proposal，形成研究假设。", 55),
            ("RiskAgent", "映射 TradingAgents 风险经理结论，复核流动性和仓位纪律。", 76),
            ("ReportAgent", "把 TradingAgents 组合经理结论映射为 DeepFocus 投资报告。", 84),
        ]
    if engine == "financial_services":
        return [
            ("FSIWorkflowAgent", "按 financial-services cookbook 选择 market research、earnings、model、pitch、valuation、KYC 或 reconciliation 路线。", 30),
            ("ModelBuilderAgent", "抽取模型输入、估值假设、可比公司和三表/DCF/LBO 工作底稿需求。", 52),
            ("ControlAgent", "加入审计、引用、审批、KYC/对账和人工复核闸门。", 72),
            ("ReportAgent", "把金融服务工作流压缩为可交付备忘录、模型清单和后续动作。", 88),
        ]
    return [
        ("ResearchAgent", "梳理业务事实、情绪信号、核心问题和投资假设。", 35),
        ("ResearchAgent", "生成牛市/基准/熊市情景和触发条件。", 55),
        ("RiskAgent", "识别亏损路径、失效条件和仓位纪律。", 72),
        ("ReportAgent", "合并为投资者可读的决策报告。", 90),
    ]


async def _process_customs_trade_task(task: InvestmentTaskRecord) -> None:
    payload = task.input
    _append_log(
        task.id,
        "OrchestratorAgent",
        "识别为海关进出口专题任务，切换到 CustomsTradeAgent 编排。",
        progress=10,
    )
    _append_log(
        task.id,
        "EvidenceAgent",
        "读取海关总署官方快报、正式统计表、月报索引和最近12个月同表数据。",
        progress=18,
    )
    snapshot = await fetch_customs_trade_snapshot()
    month_label = snapshot.get("month_label") or snapshot.get("observed_month") or "未知月份"
    history_count = len(snapshot.get("history_months") or [])
    _append_log(
        task.id,
        "EvidenceAgent",
        f"已装填 {month_label} 海关快照，近12个月曲线 {history_count} 期，HS2 {len(snapshot.get('hs_chapters') or [])} 条，重点商品 {(len(snapshot.get('major_exports') or []) + len(snapshot.get('major_imports') or []))} 条。",
        progress=32,
    )
    focus = str(payload.get("customs_focus") or payload.get("objective") or "全局海关进出口快照")
    context = build_customs_trade_analysis_text(
        snapshot,
        focus=focus,
        selected_tab=payload.get("customs_selected_tab"),
        focus_key=payload.get("customs_focus_key"),
    )
    detail_snapshot: Optional[dict[str, Any]] = None
    if payload.get("customs_focus_type") == "hs_detail" or payload.get("customs_selected_tab") == "fine":
        _append_log(
            task.id,
            "EvidenceAgent",
            "当前焦点为HS明细商品，读取中国报告方HS6近12个月金额、数量和伙伴结构。",
            progress=40,
        )
        detail_snapshot = await fetch_customs_hs_detail_snapshot(
            query=focus,
            code=payload.get("customs_focus_key"),
            months=12,
        )
        product = detail_snapshot.get("product") or {}
        context = f"{context}\n{build_customs_hs_detail_analysis_text(detail_snapshot)}"
        _append_log(
            task.id,
            "EvidenceAgent",
            f"已装填HS6明细：{product.get('code') or detail_snapshot.get('code')} {product.get('name_zh') or product.get('name') or ''}，近12个月 {len(detail_snapshot.get('monthly_points') or [])} 期。",
            progress=45,
        )
    _append_log(
        task.id,
        "ResearchAgent",
        "拆解总量、HS2、重点商品、贸易伙伴、HS明细和代表股票候选池，生成产业链映射。",
        progress=52,
    )
    llm = CloudResearchLLM()
    analysis = await asyncio.wait_for(
        llm.analyze_customs_trade_agent(context),
        timeout=min(AGENT_LLM_TIMEOUT_SECONDS, AGENT_REPORT_CLOUD_TIMEOUT_SECONDS),
    )
    _append_log(
        task.id,
        "RiskAgent",
        "复核口径风险、价格扰动、基数效应、转口和对美敞口反证。",
        progress=76,
    )
    result = _customs_trade_task_result(analysis, snapshot, payload, task)
    if detail_snapshot:
        result.setdefault("artifacts", []).append(
            {
                "type": "customs_hs_detail_snapshot",
                "title": "HS明细商品近12个月数据",
                "payload": detail_snapshot,
            }
        )
    _append_log(
        task.id,
        "ReportAgent",
        "海关进出口投研 Agent 报告已生成，并写入 Agent Workspace 任务结果。",
        progress=98,
    )
    _update_task(
        task.id,
        status="completed",
        progress=100,
        assigned_agent="ReportAgent",
        result_json=json.dumps(result, ensure_ascii=False),
        runtime_pid=None,
        runtime_kind=None,
        completed_at=now_iso(),
    )


def _customs_trade_task_result(
    analysis: Any,
    snapshot: dict[str, Any],
    payload: dict[str, Any],
    task: InvestmentTaskRecord,
) -> dict[str, Any]:
    actions = list(getattr(analysis, "actions", []) or [])
    risks = list(getattr(analysis, "risks", []) or [])
    key_points = list(getattr(analysis, "key_points", []) or [])
    signals = list(getattr(analysis, "signals", []) or [])
    sources = list(getattr(analysis, "sources", []) or [])
    confidence = _normalize_confidence(getattr(analysis, "confidence", 0.62))
    decision = "candidate" if any("建议关注" in item for item in actions) else "research_more"
    month_label = snapshot.get("month_label") or snapshot.get("observed_month") or "未知月份"
    focus = str(payload.get("customs_focus") or "全局海关进出口快照")
    source_items = [
        {
            "title": str(source.get("name") or "GACC Customs Source"),
            "source": "GACC",
            "source_type": str(source.get("type") or "official_customs"),
            "tags": ["海关总署", "官方数据", "进出口"],
            "credibility_score": 0.9,
            "url": source.get("url"),
            "takeaway": str(source.get("note") or "")[:180],
        }
        for source in (snapshot.get("sources") or [])[:4]
        if isinstance(source, dict)
    ]
    return {
        "engine": "deepfocus",
        "engine_label": "DeepFocus Native",
        "engine_status": "completed",
        "investor_summary": getattr(analysis, "summary", "") or "海关进出口投研 Agent 已完成。",
        "decision": decision,
        "confidence": confidence,
        "agent_findings": {
            "orchestrator": [
                f"专题：{task.title}",
                f"分析焦点：{focus}",
            ],
            "evidence": [
                f"官方月份：{month_label}",
                f"近12个月曲线：{len(snapshot.get('history_months') or [])} 期",
                f"数据源：{', '.join(sources[:3]) or 'GACC'}",
            ],
            "research": [*key_points[:4], *signals[:2]][:6],
            "risk": risks[:6],
            "report": actions[:6],
        },
        "scenarios": [
            {
                "case": "bull",
                "probability": 0.28,
                "thesis": "HS85、集成电路、电力设备出海连续验证，代表股票池可提升研究优先级。",
                "triggers": [item for item in actions if "加仓触发" in item][:3] or actions[:2],
            },
            {
                "case": "base",
                "probability": 0.50,
                "thesis": "外贸结构分化延续，按子链条区分建议关注、谨慎观察和暂时回避。",
                "triggers": key_points[:3],
            },
            {
                "case": "bear",
                "probability": 0.22,
                "thesis": "对美敞口、价格扰动、抢出口透支或基数效应导致后续月度回落。",
                "triggers": risks[:3],
            },
        ],
        "risk_controls": risks[:6],
        "action_plan": actions[:6],
        "watchlist": _dedupe_strings([*signals, *actions])[:8],
        "disconfirming_evidence": risks[:6],
        "evidence": source_items,
        "plain_language_takeaway": getattr(analysis, "summary", "") or "",
        "disclaimer": getattr(analysis, "disclaimer", None) or "仅供投研参考，不构成投资建议。",
        "artifacts": [
            {
                "type": "customs_trade_agent_analysis",
                "title": getattr(analysis, "title", "中国海关进出口投研Agent"),
                "content": json.dumps(
                    analysis.model_dump(mode="json") if hasattr(analysis, "model_dump") else {},
                    ensure_ascii=False,
                ),
            }
        ],
    }


async def _collect_market_quote(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        return None
    try:
        response = await asyncio.wait_for(
            fetch_market_quotes([symbol]),
            timeout=MARKET_QUOTE_TIMEOUT_SECONDS,
        )
        if response.quotes:
            return response.quotes[0].model_dump()
        if response.warnings:
            return {"symbol": symbol, "warning": "; ".join(response.warnings[:2])}
    except Exception as exc:  # noqa: BLE001 - quote enrichment must not block research runs
        return {"symbol": symbol, "warning": _safe_short(str(exc), 160)}
    return {"symbol": symbol, "warning": "行情源未返回可用快照"}


def _professional_research_question(payload: dict[str, Any]) -> str:
    return _safe_short(
        " ".join(
            str(part)
            for part in (
                payload.get("asset_name"),
                payload.get("symbol"),
                payload.get("title"),
                payload.get("objective"),
                str(payload.get("context") or "")[:700],
            )
            if part
        ),
        1200,
    )


async def _collect_professional_research_evidence(
    payload: dict[str, Any],
    *,
    report_limit: int = 2,
    evidence_limit: int = 4,
) -> list[dict[str, Any]]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        return []

    try:
        reports = list_professional_reports(symbol=symbol, limit=report_limit)
    except Exception:
        return []
    if not reports:
        return []

    question = _professional_research_question(payload) or f"{symbol} 基本面、估值、风险和买卖点"
    evidence: list[dict[str, Any]] = []
    for report in reports:
        if len(evidence) >= evidence_limit:
            break

        try:
            analysis = await analyze_professional_report(
                report.id,
                ProfessionalReportAnalysisRequest(focus=question, use_cloud_model=False),
            )
            metric_lines = [
                f"{metric.metric_label}: {metric.raw_value}"
                for metric in analysis.key_metrics[:5]
            ]
            citation_lines = [
                f"{citation.citation_id}: {citation.title}"
                for citation in analysis.citations[:4]
            ]
            evidence.append(
                {
                    "source": "专业研报库",
                    "source_type": "professional_report_analysis",
                    "source_category": "research",
                    "title": report.title,
                    "symbol": report.symbol,
                    "url": None,
                    "tags": _dedupe_strings([
                        "专业研报",
                        "财报分析Agent",
                        report.report_type,
                        report.period or "",
                    ]),
                    "credibility_score": max(float(analysis.confidence or 0), 0.72),
                    "collected_at": report.updated_at,
                    "text": "\n".join(
                        item
                        for item in (
                            f"专业研报分析：{analysis.summary}",
                            f"关键指标：{'；'.join(metric_lines)}" if metric_lines else "",
                            f"风险提示：{'；'.join(analysis.risks[:4])}" if analysis.risks else "",
                            f"质量标记：{'；'.join(analysis.quality_flags[:3])}" if analysis.quality_flags else "",
                            f"引用：{'；'.join(citation_lines)}" if citation_lines else "",
                        )
                        if item
                    ),
                }
            )
        except Exception:
            pass

        if len(evidence) >= evidence_limit:
            break

        try:
            rag = await query_professional_rag(
                ProfessionalRagQueryRequest(
                    question=question,
                    symbol=symbol,
                    report_id=report.id,
                    top_k=4,
                    use_cloud_model=False,
                )
            )
            if rag.citations:
                citation_lines = [
                    f"{citation.citation_id}: {citation.title} - {_safe_short(citation.text, 180)}"
                    for citation in rag.citations[:4]
                ]
                evidence.append(
                    {
                        "source": "专业研报库 RAG",
                        "source_type": "professional_rag",
                        "source_category": "research",
                        "title": f"{report.title}：引用型RAG",
                        "symbol": report.symbol,
                        "url": None,
                        "tags": ["专业研报", "引用型RAG"],
                        "credibility_score": max(float(rag.confidence or 0), 0.7),
                        "collected_at": now_iso(),
                        "text": "\n".join(
                            item
                            for item in (
                                f"问题：{question}",
                                f"回答：{rag.answer}",
                                f"引用：{'；'.join(citation_lines)}",
                            )
                            if item
                        ),
                    }
                )
        except Exception:
            pass

    return evidence[:evidence_limit]


def _external_research_keyword(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or "").strip().upper()
    name = str(payload.get("asset_name") or "").strip()
    base = " ".join(part for part in (name, symbol) if part)
    return _safe_short(f"{base} 研报 财报 基本面 估值 买卖点", 80)


async def _collect_external_research_evidence(
    payload: dict[str, Any],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    keyword = _external_research_keyword(payload)
    if not keyword:
        return []

    evidence = await _collect_sec_filing_evidence(payload, limit=min(limit, 3))
    if len(evidence) >= limit:
        return evidence[:limit]

    try:
        _, items, warnings, meta = await keyword_crawl_data_source(
            DataSourceKeywordCrawlRequest(
                provider="wechat_public",
                keyword=keyword,
                symbol=symbol or None,
                limit=min(max(limit, 1), 6),
                sort="time_desc",
                freshness="month",
            )
        )
    except Exception:
        return evidence[:limit]

    effective_provider = str(meta.get("effective_provider") or meta.get("provider") or "wechat_public")
    for item in items[:limit]:
        if not _external_item_matches_target(item.title, item.text, payload):
            continue
        evidence.append(
            {
                "source": f"公开研报外查/{item.source_name}",
                "source_type": "external_research_crawl",
                "source_category": "research",
                "title": item.title,
                "symbol": item.symbol or symbol or None,
                "url": item.url,
                "tags": _dedupe_strings([
                    "研报外查",
                    "自动补证",
                    effective_provider,
                    *list(item.tags or [])[:3],
                ]),
                "credibility_score": min(float(item.credibility_score or 0.5), 0.62),
                "collected_at": item.collected_at,
                "text": "\n".join(
                    part
                    for part in (
                        f"外查关键词：{keyword}",
                        f"来源策略：{effective_provider}",
                        item.text[:4000],
                        f"抓取提醒：{'；'.join(warnings[:2])}" if warnings else "",
                    )
                    if part
                ),
            }
        )
    return evidence[:limit]


def _collect_ai_capacity_utilization_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    haystack = " ".join(
        str(value)
        for value in (
            payload.get("title"),
            payload.get("asset_name"),
            payload.get("symbol"),
            payload.get("objective"),
            payload.get("context"),
        )
        if value
    ).lower()
    ai_tokens = ("ai", "人工智能", "算力", "gpu", "hbm", "cowos", "数据中心", "服务器", "光模块")
    capacity_tokens = ("产能", "利用率", "稼动率", "供应链", "瓶颈", "交付", "供需", "capacity", "utilization")
    if not any(token in haystack for token in ai_tokens) or not any(token in haystack for token in capacity_tokens):
        return []

    collected_at = now_iso()
    return [
        {
            "source": "AI产业链产能利用率模块",
            "source_type": "ai_supply_chain_capacity",
            "source_category": "market",
            "title": "官方行业产能利用率基线",
            "symbol": payload.get("symbol"),
            "url": "https://fred.stlouisfed.org/series/CAPUTLG3344SQ",
            "tags": _dedupe_strings(["AI供应链", "产能利用率", "官方统计", "Fed G.17", "国家统计局"]),
            "credibility_score": 0.86,
            "collected_at": collected_at,
            "text": (
                "官方基线：Fed/FRED G.17提供美国电脑与电子产品NAICS 334、半导体及电子元件NAICS 3344产能利用率；"
                "国家统计局提供中国计算机、通信和其他电子设备制造业季度产能利用率。"
                "最近三个月周频看板采用13周口径：官方Fed G.17月度数据按周承接，但最近三周若没有新观测会标记为待更新区，不再画成真实横盘。"
                "截至最近有效观测，电子制造从2月下旬约74.7%到4月下旬约75.4%；半导体及电子元件从约72.9%承接到约72.2%。"
                "这些是全行业均值，只能校准宏观电子制造周期，不能直接代表CoWoS、HBM或AI服务器局部瓶颈。"
            ),
        },
        {
            "source": "AI产业链产能利用率模块",
            "source_type": "ai_supply_chain_capacity",
            "source_category": "market",
            "title": "AI链局部瓶颈代理利用率",
            "symbol": payload.get("symbol"),
            "url": "https://www.trendforce.com/presscenter/news/20260430-13028.html",
            "tags": _dedupe_strings(["AI供应链", "CoWoS", "HBM", "先进制程", "代理指标"]),
            "credibility_score": 0.78,
            "collected_at": collected_at,
            "text": (
                "AI链专属产能利用率通常没有统一官方口径，应使用TSMC/Micron/SK hynix等公司法说会、TrendForce/SEMI/Omdia等产业调研、"
                "台湾ODM月营收、订单积压、交期、涨价和库存作为代理变量。当前需重点跟踪先进制程晶圆、CoWoS/2.5D先进封装、"
                "HBM、高端光模块、AI服务器ODM和数据中心电力设备。最近13周代理趋势显示CoWoS约44周升至52周、HBM约44周升至52周、"
                "光互联约36周升至52周、数据中心电力设备约58周升至65周；最近三周若没有新增产业观测会作为待更新区处理，"
                "应用时必须标注为代理趋势，不得当作官方产能利用率。"
            ),
        },
    ]


async def _collect_sec_filing_evidence(payload: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not _is_us_optionable_symbol(symbol):
        return []

    headers = {
        "User-Agent": SEC_RESEARCH_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        async with httpx.AsyncClient(
            timeout=SEC_RESEARCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        ) as client:
            cik = await _sec_cik_for_symbol(client, symbol)
            if not cik:
                return []
            response = await client.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
            response.raise_for_status()
            return _sec_filing_evidence_from_submissions(
                symbol=symbol,
                asset_name=str(payload.get("asset_name") or symbol),
                cik=str(cik),
                submissions=response.json(),
                limit=limit,
            )
    except Exception:
        return []


async def _sec_cik_for_symbol(client: httpx.AsyncClient, symbol: str) -> Optional[int]:
    response = await client.get("https://www.sec.gov/files/company_tickers.json")
    response.raise_for_status()
    raw = response.json()
    rows = raw.values() if isinstance(raw, dict) else raw
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker") or "").strip().upper() == symbol:
            with suppress(TypeError, ValueError):
                return int(row.get("cik_str"))
    return None


def _sec_filing_evidence_from_submissions(
    *,
    symbol: str,
    asset_name: str,
    cik: str,
    submissions: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    recent = ((submissions.get("filings") or {}).get("recent") or {}) if isinstance(submissions, dict) else {}
    forms = list(recent.get("form") or [])
    filing_dates = list(recent.get("filingDate") or [])
    report_dates = list(recent.get("reportDate") or [])
    accessions = list(recent.get("accessionNumber") or [])
    primary_docs = list(recent.get("primaryDocument") or [])
    accepted_at = list(recent.get("acceptanceDateTime") or [])
    company_name = str(submissions.get("name") or asset_name or symbol)
    preferred_forms = {"10-K", "10-Q", "8-K", "6-K", "20-F", "DEF 14A"}

    evidence: list[dict[str, Any]] = []
    for index, form in enumerate(forms):
        form_text = str(form or "").strip().upper()
        if form_text not in preferred_forms:
            continue
        accession = str(accessions[index] if index < len(accessions) else "").strip()
        primary_doc = str(primary_docs[index] if index < len(primary_docs) else "").strip()
        filing_date = str(filing_dates[index] if index < len(filing_dates) else "").strip()
        report_date = str(report_dates[index] if index < len(report_dates) else "").strip()
        accepted = str(accepted_at[index] if index < len(accepted_at) else "").strip()
        if not accession or not primary_doc:
            continue

        evidence.append(
            {
                "source": "SEC EDGAR",
                "source_type": "filing",
                "source_category": "filing",
                "title": f"{symbol} {form_text} filing {filing_date or report_date}",
                "symbol": symbol,
                "url": _sec_filing_url(cik, accession, primary_doc),
                "tags": _dedupe_strings(["SEC", "EDGAR", "官方文件", form_text, "财报" if form_text in {"10-K", "10-Q", "20-F"} else "公告"]),
                "credibility_score": 0.94,
                "collected_at": accepted or now_iso(),
                "text": "\n".join(
                    part
                    for part in (
                        f"公司：{company_name}（{symbol}），CIK {int(cik):010d}",
                        f"SEC 表格：{form_text}",
                        f"提交日期：{filing_date}" if filing_date else "",
                        f"报告期：{report_date}" if report_date else "",
                        "来源说明：SEC EDGAR 官方公开文件索引；用于补充本地研报库未入库时的一手财报/公告证据。",
                    )
                    if part
                ),
            }
        )
        if len(evidence) >= limit:
            break
    return evidence


def _sec_filing_url(cik: str, accession: str, primary_doc: str) -> str:
    cik_int = int(cik)
    accession_slug = re.sub(r"[^0-9]", "", accession)
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_slug}/{primary_doc}"


def _external_item_matches_target(title: str, text: str, payload: dict[str, Any]) -> bool:
    symbol = str(payload.get("symbol") or "").strip().upper()
    name = str(payload.get("asset_name") or "").strip()
    title_haystack = title.lower()
    compact_title = re.sub(r"\s+", "", title_haystack)
    cleaned_text = "\n".join(
        line
        for line in str(text or "").splitlines()
        if not line.strip().startswith(("搜索关键词", "外查关键词"))
    ).lower()
    haystack = f"{title_haystack} {cleaned_text}"
    compact_haystack = re.sub(r"\s+", "", haystack)
    candidates = [
        symbol,
        symbol.split(".")[0] if symbol else "",
        name,
    ]
    if symbol == "TSLA" or "特斯拉" in name or "TESLA" in name.upper():
        candidates.extend(["特斯拉", "tesla", "tsla"])
    if symbol.startswith("300750") or "宁德时代" in name:
        candidates.extend(["宁德时代", "catl", "300750"])
    normalized_candidates = [
        re.sub(r"\s+", "", candidate.lower())
        for candidate in candidates
        if candidate
    ]
    title_hit = any(candidate in compact_title for candidate in normalized_candidates)
    if title_hit:
        return True
    # Avoid broad market roundups where the target only appears once in the snippet.
    mention_count = sum(compact_haystack.count(candidate) for candidate in normalized_candidates)
    return mention_count >= 3 and any(term in compact_title for term in ("研报", "财报", "估值", "基本面", "research", "earnings"))


def _is_us_optionable_symbol(value: Any) -> bool:
    symbol = str(value or "").strip().upper()
    if not symbol:
        return False
    if "." in symbol:
        return False
    return bool(re.fullmatch(r"[A-Z]{1,5}", symbol))


async def _collect_options_signal_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not _is_us_optionable_symbol(symbol):
        return []

    try:
        response = await asyncio.wait_for(
            fetch_options_signals([symbol], horizon_days=45, max_expirations=3),
            timeout=18,
        )
    except Exception:
        return []

    evidence: list[dict[str, Any]] = []
    for signal in response.signals[:1]:
        if signal.source_status == "unavailable" or signal.contract_count <= 0:
            source_lines = [
                f"{source.name}: {source.status}；{source.coverage}；{source.notes}"
                for source in response.sources[:4]
            ]
            evidence.append(
                {
                    "source": "期权雷达",
                    "source_type": "options_signal_unavailable",
                    "source_category": "market",
                    "title": f"{signal.symbol} 期权链查询状态（暂不可用）",
                    "symbol": signal.symbol,
                    "url": None,
                    "tags": ["期权", "数据源状态", "已查询", "不可判定"],
                    "credibility_score": 0.36,
                    "collected_at": signal.fetched_at,
                    "text": "\n".join(
                        part
                        for part in (
                            signal.summary,
                            f"源状态：{signal.provider_name} / {signal.source_status}",
                            f"合约数：{signal.contract_count}，到期日数：{signal.expiration_count}",
                            f"风险：{'；'.join(signal.risk_flags[:4])}" if signal.risk_flags else "",
                            f"可用源：{'；'.join(source_lines)}" if source_lines else "",
                            f"源告警：{'；'.join(response.warnings[:4])}" if response.warnings else "",
                            signal.delay_note,
                            response.disclaimer,
                        )
                        if part
                    ),
                }
            )
            continue
        key_strikes = [
            f"{strike.side} {strike.strike:g} {strike.metric}={strike.value:g}"
            for strike in signal.key_strikes[:5]
        ]
        expirations = [
            (
                f"{expiration.expiration} DTE={expiration.dte} "
                f"PCR成交={expiration.pcr_volume:.2f}" if expiration.pcr_volume is not None
                else f"{expiration.expiration} DTE={expiration.dte}"
            )
            for expiration in signal.expirations[:3]
        ]
        unusual_flows = [
            (
                f"{'Call' if flow.side == 'call' else 'Put'} {flow.strike:g} {flow.expiration} "
                f"成交={flow.volume:g} 权利金=${(flow.premium_notional or 0) / 1_000_000:.2f}M "
                f"评分={flow.score}/100"
            )
            for flow in signal.unusual_flows[:4]
        ]
        evidence.append(
            {
                "source": "期权雷达",
                "source_type": "options_signal",
                "source_category": "market",
                "title": f"{signal.symbol} 期权链信号（{signal.provider_name}）",
                "symbol": signal.symbol,
                "url": None,
                "tags": ["期权", "买卖点", "隐含波动", signal.direction, signal.conviction],
                "credibility_score": max(0.45, min(0.78, signal.data_quality / 100)),
                "collected_at": signal.fetched_at,
                "text": "\n".join(
                    part
                    for part in (
                        signal.summary,
                        f"标的价格：{signal.underlying_price}" if signal.underlying_price is not None else "",
                        f"方向分：{signal.score}/100，方向：{signal.direction}，置信度：{signal.conviction}",
                        f"Put/Call 成交比：{signal.put_call_volume_ratio:.2f}" if signal.put_call_volume_ratio is not None else "",
                        f"Put/Call OI 比：{signal.put_call_open_interest_ratio:.2f}" if signal.put_call_open_interest_ratio is not None else "",
                        f"平均 IV：{signal.avg_iv * 100:.1f}%" if signal.avg_iv is not None else "",
                        f"预期波动：+/-{signal.expected_move_pct * 100:.1f}%" if signal.expected_move_pct is not None else "",
                        f"Call Wall：{signal.call_wall:g}" if signal.call_wall is not None else "",
                        f"Put Wall：{signal.put_wall:g}" if signal.put_wall is not None else "",
                        f"Max Pain：{signal.max_pain:g}" if signal.max_pain is not None else "",
                        f"异常大单候选：{'；'.join(unusual_flows)}" if unusual_flows else "",
                        f"关键行权价：{'；'.join(key_strikes)}" if key_strikes else "",
                        f"到期结构：{'；'.join(expirations)}" if expirations else "",
                        f"信号：{'；'.join(signal.signals[:6])}" if signal.signals else "",
                        f"风险：{'；'.join(signal.risk_flags[:4])}" if signal.risk_flags else "",
                        signal.delay_note,
                    )
                    if part
                ),
            }
        )
    return evidence


def _quote_log_message(quote: dict[str, Any]) -> str:
    if quote.get("price") is None:
        return f"行情快照暂不可用：{quote.get('warning') or quote.get('symbol') or '无返回'}。"
    change = quote.get("change_percent")
    change_text = f"，涨跌幅 {float(change):+.2f}%" if isinstance(change, (int, float)) else ""
    provider = quote.get("provider_name") or quote.get("provider") or "行情源"
    return f"已获取行情快照：{quote.get('symbol')} {quote.get('price')} {quote.get('currency') or ''}{change_text}（{provider}）。"


async def _cloud_investment_result(
    llm: CloudResearchLLM,
    task: InvestmentTaskRecord,
    payload: dict[str, Any],
) -> dict[str, Any]:
    timeout_seconds = max(35.0, min(AGENT_REPORT_CLOUD_TIMEOUT_SECONDS, AGENT_LLM_TIMEOUT_SECONDS - 5.0))
    retry_schema_hint = (
        "必须填充 investor_summary, decision, confidence, scenarios, risk_controls, "
        "action_plan, watchlist, disconfirming_evidence；所有数组最多 4 项。"
    )
    output_token_budget = AGENT_CLOUD_OUTPUT_TOKENS
    compact_payload = _compact_cloud_report_payload(payload, output_token_budget=output_token_budget)
    fsi_instruction = ""
    if task.engine == "financial_services":
        fsi_instruction = (
            "当前引擎是 Financial Services Playbook。必须先选择最贴近的工作流：market-researcher、"
            "earnings-reviewer、model-builder、pitch-agent、valuation-reviewer、kyc-screener、"
            "gl-reconciler、month-end-closer 或 statement-auditor；"
            "输出要包含交付件清单、输入缺口、模型/表格审计规则、引用/审批闸门和人工复核点。\n"
        )
    prompt = (
        "你是华尔街投研委员会级别的 ReportAgent。只输出一个 JSON object，不要 Markdown，不要解释。\n"
        "任务：给投资研究报告生成判断层，不要写长文，不要承诺收益，不要编造没有给出的实时数据。\n"
        "JSON 字段：investor_summary, decision, confidence, agent_findings, scenarios, "
        "risk_controls, action_plan, watchlist, disconfirming_evidence, evidence, "
        "plain_language_takeaway, disclaimer。\n"
        "长度限制：investor_summary 不超过 180 字；agent_findings 每阶段最多 2 条；"
        "risk_controls/action_plan/watchlist/disconfirming_evidence 各最多 4 条，每条不超过 45 字；"
        "evidence 最多 4 条；scenarios 只给 bull/base/bear 三个对象。\n"
        "decision 只能是 avoid/watch/research_more/candidate；confidence 用 0-1 小数。"
        "agent_findings 是对象，含 orchestrator/evidence/research/risk/report。"
        "scenarios 对象字段：case, probability, thesis, triggers。"
        "evidence 对象字段：title, source, source_type, credibility_score, url, takeaway。\n"
        "必须优先读取 context_checkpoint，它是上游确定性压缩后的状态快照；"
        "context 和 evidence.takeaway 可能已被 context-gc 截断，不能把省略段当作事实。\n"
        "必须覆盖：商业质量、收入/利润驱动、行业周期、竞争格局、估值验证、催化剂、期权链/隐含波动/关键行权价、失效条件、仓位纪律。\n"
        "先审计 evidence_status：公众号、雪球、社区和自媒体只能当线索，不能当官方事实；"
        "如果缺少 SEC/IR/10-Q/10-K/电话会/公司公告等一手资料，必须明确写成证据缺口。"
        "如果 evidence_status.confidence_cap <= 0.45，confidence 不能超过该值，decision 不能是 candidate。"
        "如果 market_quote.price 为空，禁止写实时股价、单日涨跌或具体价格；如果 price 存在，只能引用输入里的 price/provider。"
        "Optimus、FSD、Robotaxi、机器人等长周期叙事，除非 evidence 里有官方/SEC/IR 证据，否则必须标为待验证叙事。\n"
        f"{fsi_instruction}"
        f"压缩输入：{json.dumps(compact_payload, ensure_ascii=False)}"
    )
    data = await llm.complete_json(
        prompt,
        max_tokens=output_token_budget,
        timeout_seconds=timeout_seconds,
        force_json_first=False,
        retry_schema_hint=retry_schema_hint,
    )
    result = _normalize_investment_result(data, task)
    return _apply_report_guardrails(result, task, payload)


def _compact_cloud_report_payload(
    payload: dict[str, Any],
    *,
    output_token_budget: int = AGENT_CLOUD_OUTPUT_TOKENS,
) -> dict[str, Any]:
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    symbol = str(payload.get("symbol") or "")
    name = str(payload.get("asset_name") or symbol or "目标资产")
    objective = str(payload.get("objective") or "")
    relevant_evidence = _rank_relevant_evidence(evidence, symbol, name, objective) if evidence else []
    evidence_status = _evidence_quality_status(relevant_evidence, evidence, payload.get("market_quote") or {})
    effective_tokens = _effective_context_tokens(output_token_budget)
    evidence_limit = max(1, min(AGENT_COMPACT_EVIDENCE_LIMIT, 10))
    evidence_budget = max(900, min(6200, int(effective_tokens * 0.44)))
    per_evidence_budget = max(160, min(820, evidence_budget // max(1, min(len(relevant_evidence or evidence), evidence_limit))))
    compact_evidence = []
    for item in (relevant_evidence or evidence)[:evidence_limit]:
        raw_takeaway = str(item.get("takeaway") or item.get("text") or "")
        compact_evidence.append({
            "title": _safe_short(str(item.get("title") or ""), 120),
            "source": _safe_short(str(item.get("source") or ""), 60),
            "source_type": _safe_short(str(item.get("source_type") or ""), 40),
            "source_category": _safe_short(str(item.get("source_category") or ""), 40),
            "tags": list(item.get("tags") or [])[:8],
            "url": item.get("url"),
            "credibility_score": item.get("credibility_score"),
            "takeaway": _compress_tool_result_text(raw_takeaway, max_tokens=per_evidence_budget),
        })
    compact_payload = {
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "engine": payload.get("engine"),
        "title": payload.get("title"),
        "symbol": payload.get("symbol"),
        "asset_name": payload.get("asset_name"),
        "task_type": payload.get("task_type"),
        "horizon": payload.get("horizon"),
        "investor_profile": payload.get("investor_profile"),
        "objective": payload.get("objective"),
        "engine_config": payload.get("engine_config") or {},
        "context_checkpoint": _deterministic_context_checkpoint(payload, evidence_status, relevant_evidence or evidence),
        "context": _compress_tool_result_text(
            str(payload.get("context") or ""),
            max_tokens=max(320, min(2200, int(effective_tokens * 0.16))),
        ),
        "market_quote": payload.get("market_quote"),
        "evidence_status": evidence_status,
        "evidence": compact_evidence,
    }
    return _fit_compact_payload_to_budget(compact_payload, effective_tokens)


def _effective_context_tokens(output_token_budget: int = AGENT_CLOUD_OUTPUT_TOKENS) -> int:
    window = max(4096, int(AGENT_CONTEXT_WINDOW_TOKENS or 0))
    reserved = max(800, int(AGENT_CONTEXT_RESERVED_TOKENS or 0))
    output_budget = max(512, int(output_token_budget or 0))
    return max(1600, window - reserved - output_budget)


def _estimate_context_tokens(value: Any) -> int:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = value or ""
    ascii_chars = 0
    cjk_chars = 0
    other_chars = 0
    for char in text:
        code = ord(char)
        if char.isspace():
            other_chars += 0.25
        elif _is_cjk_char(code):
            cjk_chars += 1
        elif code < 128:
            ascii_chars += 1
        else:
            other_chars += 1
    estimate = int((ascii_chars / 4.0) + (cjk_chars * 2.0) + (other_chars * 1.2) + 8)
    return max(1, estimate)


def _estimate_json_tokens(value: Any) -> int:
    return _estimate_context_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _is_cjk_char(code: int) -> bool:
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
    )


def _compress_tool_result_text(text: str, *, max_tokens: int) -> str:
    clean = str(text or "").strip()
    if not clean or _estimate_context_tokens(clean) <= max_tokens:
        return clean

    lines = clean.splitlines()
    important = _important_line_window_text(lines)
    if important:
        marker = f"[context-gc: tool_result trimmed; original_tokens~{_estimate_context_tokens(clean)}]"
        marker_tokens = _estimate_context_tokens(marker) + 8
        remaining = max(80, max_tokens - marker_tokens)
        important_budget = max(70, int(remaining * 0.56))
        edge_budget = max(40, remaining - important_budget)
        head = _limit_text_to_tokens(clean, max(20, edge_budget // 2), mode="prefix")
        tail = _limit_text_to_tokens(clean, max(20, edge_budget - edge_budget // 2), mode="suffix")
        core = _limit_text_to_tokens(important, important_budget, mode="head_tail")
        return "\n".join(part for part in (head, marker, core, tail) if part).strip()

    return _limit_text_to_tokens(clean, max_tokens, mode="head_tail")


def _important_line_window_text(lines: list[str], *, before: int = 3, after: int = 5, limit: int = 72) -> str:
    selected: set[int] = set()
    for index, line in enumerate(lines):
        if IMPORTANT_TOOL_LINE_RE.search(line):
            selected.update(range(max(0, index - before), min(len(lines), index + after + 1)))
        if len(selected) >= limit:
            break
    if not selected:
        return ""

    chunks: list[str] = []
    previous = -2
    for index in sorted(selected)[:limit]:
        if previous >= 0 and index > previous + 1:
            chunks.append("[context-gc: omitted unrelated log lines]")
        chunks.append(lines[index])
        previous = index
    return "\n".join(chunks)


def _limit_text_to_tokens(text: str, max_tokens: int, *, mode: str = "prefix") -> str:
    clean = str(text or "").strip()
    if not clean or _estimate_context_tokens(clean) <= max_tokens:
        return clean
    max_tokens = max(16, int(max_tokens))
    if mode == "suffix":
        return _binary_trim_text(clean, max_tokens, suffix=True)
    if mode == "head_tail":
        return _head_tail_text(clean, max_tokens)
    return _binary_trim_text(clean, max_tokens, suffix=False)


def _binary_trim_text(text: str, max_tokens: int, *, suffix: bool) -> str:
    low = 0
    high = len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = text[-mid:] if suffix and mid else text[:mid]
        if _estimate_context_tokens(candidate) <= max_tokens:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best.strip()


def _head_tail_text(text: str, max_tokens: int) -> str:
    original_tokens = _estimate_context_tokens(text)
    marker = f"\n[context-gc: omitted middle; original_tokens~{original_tokens}; budget={max_tokens}]\n"
    marker_tokens = _estimate_context_tokens(marker)
    remaining = max(32, max_tokens - marker_tokens)
    head_budget = max(16, int(remaining * 0.56))
    tail_budget = max(16, remaining - head_budget)
    head = _binary_trim_text(text, head_budget, suffix=False)
    tail = _binary_trim_text(text, tail_budget, suffix=True)
    return f"{head}{marker}{tail}".strip()


def _deterministic_context_checkpoint(
    payload: dict[str, Any],
    evidence_status: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    context = str(payload.get("context") or "")
    quote = payload.get("market_quote") if isinstance(payload.get("market_quote"), dict) else {}
    source_mix = _source_mix(evidence)
    urls = [
        str(item.get("url"))
        for item in evidence
        if item.get("url")
    ][:6]
    context_refs = PATH_OR_URL_RE.findall(context)[:8]
    warning_lines = _signal_lines_from_text(context, limit=4)
    for item in evidence[:6]:
        warning_lines.extend(_signal_lines_from_text(str(item.get("text") or item.get("takeaway") or ""), limit=2))

    return {
        "current_goal": _safe_short(str(payload.get("objective") or payload.get("title") or ""), 220),
        "target": {
            "symbol": payload.get("symbol"),
            "asset_name": payload.get("asset_name"),
            "task_type": payload.get("task_type"),
            "horizon": payload.get("horizon"),
            "investor_profile": payload.get("investor_profile"),
        },
        "evidence_state": {
            "label": evidence_status.get("label"),
            "summary": evidence_status.get("summary"),
            "relevant_count": evidence_status.get("relevant_count"),
            "core_count": evidence_status.get("core_count"),
            "source_mix": source_mix,
            "gaps": list(evidence_status.get("gaps") or [])[:5],
        },
        "market_state": _checkpoint_market_state(quote),
        "required_next_actions": list(evidence_status.get("required_actions") or [])[:5],
        "risk_controls": list(evidence_status.get("risk_controls") or [])[:4],
        "key_refs": _dedupe_strings([*urls, *context_refs])[:10],
        "error_or_warning_signals": _dedupe_strings(warning_lines)[:8],
        "compression": {
            "effective_context_tokens": _effective_context_tokens(),
            "input_context_tokens_estimate": _estimate_context_tokens(context),
            "cjk_aware": True,
        },
    }


def _checkpoint_market_state(quote: dict[str, Any]) -> dict[str, Any]:
    if not quote:
        return {"available": False, "summary": "行情快照未接入"}
    return {
        "available": quote.get("price") is not None,
        "symbol": quote.get("symbol"),
        "price": quote.get("price"),
        "currency": quote.get("currency"),
        "change_percent": quote.get("change_percent"),
        "provider": quote.get("provider_name") or quote.get("provider"),
        "warning": quote.get("warning"),
    }


def _source_mix(evidence: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        key = str(item.get("source_type") or item.get("source_category") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8])


def _signal_lines_from_text(text: str, *, limit: int) -> list[str]:
    lines = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        if clean and IMPORTANT_TOOL_LINE_RE.search(clean):
            lines.append(_safe_short(clean, 180))
        if len(lines) >= limit:
            break
    return lines


def _fit_compact_payload_to_budget(compact_payload: dict[str, Any], budget_tokens: int) -> dict[str, Any]:
    payload = json.loads(json.dumps(compact_payload, ensure_ascii=False))
    budget_tokens = max(1200, int(budget_tokens))
    payload["context_gc"] = {
        "budget_tokens": budget_tokens,
        "estimated_tokens_before_fit": _estimate_json_tokens(payload),
        "strategy": "checkpoint+semantic_tool_trim+ranked_evidence",
    }
    if _estimate_json_tokens(payload) <= budget_tokens:
        payload["context_gc"]["estimated_tokens_after_fit"] = _estimate_json_tokens(payload)
        return payload

    payload["context"] = _compress_tool_result_text(str(payload.get("context") or ""), max_tokens=max(120, budget_tokens // 14))
    evidence = list(payload.get("evidence") or [])
    while len(evidence) > 4 and _estimate_json_tokens(payload) > budget_tokens:
        evidence.pop()
        payload["evidence"] = evidence

    for per_item_budget in (260, 180, 120, 80):
        for item in evidence:
            item["takeaway"] = _compress_tool_result_text(str(item.get("takeaway") or ""), max_tokens=per_item_budget)
            if isinstance(item.get("tags"), list):
                item["tags"] = item["tags"][:5]
        payload["evidence"] = evidence
        if _estimate_json_tokens(payload) <= budget_tokens:
            payload["context_gc"]["estimated_tokens_after_fit"] = _estimate_json_tokens(payload)
            return payload

    payload["evidence"] = evidence[:3]
    payload["engine_config"] = {}
    payload["context"] = _compress_tool_result_text(str(payload.get("context") or ""), max_tokens=80)
    payload["context_gc"]["estimated_tokens_after_fit"] = _estimate_json_tokens(payload)
    payload["context_gc"]["over_budget_after_fit"] = payload["context_gc"]["estimated_tokens_after_fit"] > budget_tokens
    return payload


CORE_EVIDENCE_HINTS = (
    "sec",
    "10-k",
    "10-q",
    "8-k",
    "form 10",
    "investor relation",
    "investor relations",
    "ir.tesla",
    "earnings",
    "transcript",
    "shareholder deck",
    "annual report",
    "quarterly report",
    "公司公告",
    "财报",
    "电话会",
    "年报",
    "季报",
)


def _evidence_quality_status(
    relevant_evidence: list[dict[str, Any]],
    all_evidence: list[dict[str, Any]],
    quote: dict[str, Any],
) -> dict[str, Any]:
    evidence_pool = relevant_evidence or []
    has_quote = bool(quote and quote.get("price") is not None)
    has_official = any(_is_official_evidence(item) for item in evidence_pool)
    has_research = any(_is_research_or_internal_evidence(item) for item in evidence_pool)
    has_soft_evidence = bool(evidence_pool)
    core_count = sum(1 for item in evidence_pool if _is_core_evidence(item))

    gaps: list[str] = []
    required_actions: list[str] = []
    risk_controls: list[str] = []
    if not has_official:
        gaps.append("缺少 SEC/IR/10-Q/10-K、电话会或公司公告等一手资料")
        required_actions.append("补齐最新 10-Q/10-K/8-K、IR 材料和电话会文字稿")
    if not has_research:
        gaps.append("缺少券商模型、机构研报或内部上传底稿")
        required_actions.append("获取至少两份可追溯财务模型、目标价或同业估值表")
    if not has_quote:
        gaps.append("缺少可用行情快照，不能引用实时价格或单日涨跌")
        required_actions.append("刷新行情源，记录价格、时间戳和提供方")
    if has_soft_evidence and core_count == 0:
        gaps.append("当前资料偏社区/媒体/自媒体，只能作为线索")
        risk_controls.append("社区/自媒体叙事必须经官方文件或财报二次验证")
    if not evidence_pool:
        gaps.append("未命中强相关可追溯证据")
        required_actions.append("按标的代码、公司名、核心事件重新检索证据库")

    if not evidence_pool:
        summary = "未命中强相关可追溯资料；报告只能列研究框架和输入缺口。"
        label = "低"
        confidence_cap = 0.35 if not all_evidence else 0.42
    elif core_count == 0:
        summary = f"已命中 {len(evidence_pool)} 条资料，但主要来自社区/媒体/自媒体，不能支撑交易结论。"
        label = "低"
        confidence_cap = 0.45
    elif has_official and has_research and has_quote:
        summary = f"已命中 {len(evidence_pool)} 条强相关资料，其中 {core_count} 条属于核心证据，并有行情快照辅助。"
        label = "高"
        confidence_cap = 0.78
    else:
        summary = f"已命中 {len(evidence_pool)} 条强相关资料，其中 {core_count} 条属于核心证据，但仍有输入缺口。"
        label = "中"
        confidence_cap = 0.62 if has_quote else 0.55

    return {
        "label": label,
        "summary": summary,
        "gaps": _dedupe_strings(gaps)[:5],
        "required_actions": _dedupe_strings(required_actions)[:5],
        "risk_controls": _dedupe_strings(risk_controls)[:4],
        "relevant_count": len(evidence_pool),
        "core_count": core_count,
        "has_quote": has_quote,
        "confidence_cap": confidence_cap,
    }


def _is_core_evidence(item: dict[str, Any]) -> bool:
    return _is_official_evidence(item) or _is_research_or_internal_evidence(item)


def _is_official_evidence(item: dict[str, Any]) -> bool:
    category = str(item.get("source_category") or "").lower()
    source_type = str(item.get("source_type") or "").lower()
    if category in {"filing", "earnings"} or source_type in {"filing", "earnings"}:
        return True
    try:
        score = float(item.get("credibility_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    haystack = _evidence_haystack(item)
    return score >= 0.82 or any(hint in haystack for hint in CORE_EVIDENCE_HINTS)


def _is_research_or_internal_evidence(item: dict[str, Any]) -> bool:
    category = str(item.get("source_category") or "").lower()
    source_type = str(item.get("source_type") or "").lower()
    if category in {"research", "upload", "internal"} or source_type in {"upload", "manual"}:
        return True
    haystack = _evidence_haystack(item)
    return any(token in haystack for token in ("research", "研报", "模型", "model", "target price", "valuation", "dcf"))


def _evidence_haystack(item: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            item.get("source"),
            item.get("source_type"),
            item.get("source_category"),
            item.get("title"),
            item.get("text"),
            item.get("takeaway"),
            " ".join(str(tag) for tag in (item.get("tags") or [])),
        )
        if value
    ).lower()


def _apply_report_guardrails(
    result: dict[str, Any],
    task: InvestmentTaskRecord,
    payload: dict[str, Any],
) -> dict[str, Any]:
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    quote = payload.get("market_quote") if isinstance(payload.get("market_quote"), dict) else {}
    symbol = str(payload.get("symbol") or task.symbol or "")
    name = str(payload.get("asset_name") or task.asset_name or symbol or "目标资产")
    objective = str(payload.get("objective") or task.input.get("objective") or task.title)
    relevant_evidence = _rank_relevant_evidence(evidence, symbol, name, objective) if evidence else []
    quality = _evidence_quality_status(relevant_evidence, evidence, quote)

    result["confidence"] = min(_normalize_confidence(result.get("confidence", 0.5)), float(quality["confidence_cap"]))
    if result.get("decision") == "candidate" and result["confidence"] <= 0.45:
        result["decision"] = "research_more"

    findings = result.get("agent_findings") if isinstance(result.get("agent_findings"), dict) else {}
    evidence_lines = [
        str(quality["summary"]),
        _quote_summary(quote),
        *list(quality.get("gaps") or [])[:3],
    ]
    findings["evidence"] = _merge_priority_items(evidence_lines, findings.get("evidence", []), limit=6)
    result["agent_findings"] = findings

    if not quote or quote.get("price") is None:
        result["investor_summary"] = _strip_unbacked_market_claims(str(result.get("investor_summary") or ""))
        result["plain_language_takeaway"] = _strip_unbacked_market_claims(str(result.get("plain_language_takeaway") or ""))

    if quality.get("required_actions"):
        result["action_plan"] = _merge_priority_items(quality["required_actions"], result.get("action_plan", []), limit=8)
    if quality.get("risk_controls"):
        result["risk_controls"] = _merge_priority_items(quality["risk_controls"], result.get("risk_controls", []), limit=8)

    result["evidence"] = _evidence_result_items(relevant_evidence)
    return result


def _strip_unbacked_market_claims(text: str) -> str:
    cleaned = re.sub(r"(?:当前)?(?:股价|最新价|收盘价)[^。；;\n]{0,80}[。；;]?", "", text or "")
    cleaned = re.sub(r"(?:单日|今日|当日)(?:上涨|下跌|涨|跌)[^。；;\n]{0,60}[。；;]?", "", cleaned)
    cleaned = re.sub(r"涨跌幅[^。；;\n]{0,60}[。；;]?", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；;")
    if not cleaned:
        return "行情快照未接入，本次不引用实时价格；证据不足时仅保留研究框架和待验证清单。"
    prefix = "行情快照未接入，本次不引用实时价格；"
    return cleaned if cleaned.startswith(prefix) else f"{prefix}{cleaned}"


def _merge_priority_items(prefix_items: list[Any], existing_items: Any, *, limit: int) -> list[str]:
    existing = [
        item
        for item in (existing_items if isinstance(existing_items, list) else [])
        if "资料较充分" not in str(item)
    ]
    return _dedupe_strings([*prefix_items, *existing])[:limit]


def _dedupe_strings(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = _safe_short(str(item), 180)
        if text and text not in result:
            result.append(text)
    return result


def _evidence_result_items(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": str(item.get("title") or "未命名资料"),
            "source": str(item.get("source") or "未知来源"),
            "source_type": str(item.get("source_type") or "unknown"),
            "tags": item.get("tags") or [],
            "credibility_score": item.get("credibility_score", 0.5),
            "url": item.get("url"),
            "takeaway": str(item.get("text") or item.get("takeaway") or "")[:180],
        }
        for item in evidence[:8]
    ]


def _mock_investment_result(
    task: InvestmentTaskRecord,
    evidence: Optional[list[dict[str, Any]]] = None,
    payload_override: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = payload_override or task.input
    evidence = evidence or []
    symbol = payload.get("symbol") or "目标资产"
    name = payload.get("asset_name") or symbol
    horizon = payload.get("horizon") or "1-4周"
    objective = payload.get("objective") or "判断是否值得进一步研究。"
    engine = task.engine or payload.get("engine") or "deepfocus"
    profile = _asset_research_profile(symbol, name)
    quote = payload.get("market_quote") or {}
    relevant_evidence = _rank_relevant_evidence(evidence, symbol, name, objective)
    weak_evidence_count = max(0, len(evidence) - len(relevant_evidence))
    evidence_status = _evidence_quality_status(relevant_evidence, evidence, quote)
    context_signal = str(evidence_status["summary"])
    evidence_titles = [f"{item.get('source')}：{item.get('title')}" for item in relevant_evidence[:3]]
    quote_summary = _quote_summary(quote)
    evidence_quality = str(evidence_status["label"])
    if weak_evidence_count:
        evidence_quality += f"；已降权 {weak_evidence_count} 条弱相关资料"
    confidence = 0.68 if relevant_evidence else 0.40
    if quote.get("price") is not None:
        confidence += 0.03
    confidence = min(confidence, 0.72, float(evidence_status["confidence_cap"]))

    return {
        "engine": engine,
        "engine_label": _engine_label(engine),
        "engine_status": "completed",
        "investor_summary": (
            f"{name}（{symbol}）完整投研报告已按机构框架生成：先看商业质量和行业位置，"
            f"再看估值与价格是否给出安全边际，最后用反证条件约束仓位。"
            f"当前证据强度：{evidence_quality}。{quote_summary}"
        ),
        "decision": "research_more",
        "confidence": confidence,
        "agent_findings": {
            "orchestrator": [
                f"研究目标：{objective}",
                f"投资周期：{horizon}",
                "任务已拆成证据、商业质量、行业周期、风险约束和行动清单",
            ],
            "evidence": [
                context_signal,
                quote_summary,
                *list(evidence_status.get("gaps") or [])[:3],
                *(evidence_titles or ["暂未找到可追溯外部证据"]),
            ],
            "research": [
                profile["business_quality"],
                *profile["drivers"][:4],
                "任何单一新闻或主题热度都必须和财务、订单、价格行为交叉验证",
            ],
            "risk": [
                *profile["risks"][:4],
                "先定义失效条件，再讨论收益空间和仓位",
            ],
            "report": [
                "适合进入研究队列，暂不由模型自动给出买卖指令",
                "下一步应补齐财报、公告、同业估值、价格行为和管理层指引",
            ],
        },
        "scenarios": [
            {
                "case": "bull",
                "probability": 25,
                "thesis": profile["bull_case"],
                "triggers": profile["bull_triggers"],
            },
            {
                "case": "base",
                "probability": 52,
                "thesis": profile["base_case"],
                "triggers": profile["base_triggers"],
            },
            {
                "case": "bear",
                "probability": 23,
                "thesis": profile["bear_case"],
                "triggers": profile["bear_triggers"],
            },
        ],
        "risk_controls": [
            *list(evidence_status.get("risk_controls") or []),
            *profile["risk_controls"],
            "任何结论必须有失效条件",
            "单一资产仓位不应由模型自动决定",
        ][:8],
        "action_plan": [
            *list(evidence_status.get("required_actions") or []),
            *profile["action_plan"],
            "把结论分为事实、推断、待验证三栏",
        ][:8],
        "watchlist": [
            *profile["watchlist"],
            "价格是否已经提前反映乐观假设",
        ][:8],
        "disconfirming_evidence": [
            *profile["disconfirming_evidence"],
            "若上涨只来自情绪而非业绩，降低置信度",
        ][:8],
        "evidence": _evidence_result_items(relevant_evidence),
        "plain_language_takeaway": (
            f"{name}现在更适合先补证据再谈动作。先把官方文件、财报电话会、同业估值和行情快照补齐，"
            "再判断盈利质量、行业周期和估值是否支持仓位。"
        ),
        "disclaimer": "仅供投研参考，不构成投资建议、收益承诺或自动交易指令。",
        "artifacts": [
            {
                "type": "institutional_memo",
                "title": f"{name}（{symbol}）机构级投研备忘录",
                "content": _institutional_memo(symbol, name, objective, horizon, profile, quote_summary, relevant_evidence),
            },
            {
                "type": "evidence_ledger",
                "title": "证据台账",
                "content": "\n".join(evidence_titles or ["暂无强相关证据；需要补充公告、财报、电话会纪要和行情数据。"]),
            },
        ],
    }


FINANCIAL_SERVICES_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "market_researcher": {
        "label": "Market Researcher",
        "triggers": ["market", "sector", "行业", "市场", "主题", "comps", "竞品", "可比"],
        "workers": ["SectorReader", "CompsSpreader", "NoteWriter"],
        "deliverables": ["行业概览", "竞争格局", "可比公司表", "候选机会短名单"],
        "controls": ["同业口径一致", "估值倍数注明日期", "事实和观点分栏"],
    },
    "earnings_reviewer": {
        "label": "Earnings Reviewer",
        "triggers": ["earnings", "财报", "业绩", "电话会", "transcript", "10-q", "10-k"],
        "workers": ["TranscriptReader", "ModelUpdater", "NoteWriter"],
        "deliverables": ["业绩快评", "模型更新清单", "管理层指引差异", "下一季关注项"],
        "controls": ["财报口径对齐", "一次性项目单列", "模型更新需复核公式"],
    },
    "model_builder": {
        "label": "Model Builder",
        "triggers": ["model", "模型", "dcf", "lbo", "三表", "three-statement", "comps", "估值"],
        "workers": ["DataPuller", "Builder", "Auditor"],
        "deliverables": ["DCF/三表/LBO 输入表", "关键假设区", "敏感性表", "模型审计清单"],
        "controls": ["计算单元格使用公式", "硬编码假设标注来源", "输出追溯到输入"],
    },
    "pitch_agent": {
        "label": "Pitch Agent",
        "triggers": ["pitch", "路演", "并购", "收购", "acquirer", "deck", "投行"],
        "workers": ["Researcher", "Modeler", "DeckWriter"],
        "deliverables": ["交易论点", "可比交易/公司", "估值桥", "Pitch Deck 大纲"],
        "controls": ["交易假设显式列出", "估值区间保留敏感性", "材料需人工审批"],
    },
    "valuation_reviewer": {
        "label": "Valuation Reviewer",
        "triggers": ["valuation", "估值复核", "nav", "gp", "lp", "portco", "私募", "pe"],
        "workers": ["PackageReader", "ValuationRunner", "Publisher"],
        "deliverables": ["估值包摘要", "关键假设变动", "LP 报告要点", "复核问题清单"],
        "controls": ["NAV 桥接可追溯", "估值方法和倍数日期透明", "重大变动升级审批"],
    },
    "kyc_screener": {
        "label": "KYC Screener",
        "triggers": ["kyc", "kya", "onboarding", "尽调", "开户", "身份", "制裁", "pep"],
        "workers": ["DocReader", "RulesEngine", "Escalator"],
        "deliverables": ["资料完整性检查", "规则命中", "风险分层", "升级处理建议"],
        "controls": ["缺失材料不得通过", "命中名单需人工复核", "保留审计日志"],
    },
    "gl_reconciler": {
        "label": "GL Reconciler",
        "triggers": ["gl", "reconcile", "reconciliation", "对账", "总账", "子账", "break"],
        "workers": ["Reader", "Critic", "Resolver"],
        "deliverables": ["差异清单", "根因追踪", "调整分录建议", "签核路径"],
        "controls": ["金额和期间一致", "差异原因可解释", "写入前需要审批"],
    },
    "month_end_closer": {
        "label": "Month End Closer",
        "triggers": ["month-end", "close", "关账", "月结", "accrual", "roll-forward"],
        "workers": ["LedgerReader", "Rollforward", "Poster"],
        "deliverables": ["应计表", "滚动表", "差异说明", "关账动作清单"],
        "controls": ["分录需双人复核", "异常波动解释到科目", "关账后保留追溯"],
    },
}


def _financial_services_playbook_result(
    task: InvestmentTaskRecord,
    evidence: Optional[list[dict[str, Any]]] = None,
    payload_override: Optional[dict[str, Any]] = None,
    engine_status: str = "completed",
) -> dict[str, Any]:
    payload = payload_override or task.input
    result = _mock_investment_result(task, evidence=evidence, payload_override=payload)
    return _apply_financial_services_overlay(result, task, payload, evidence or [], engine_status=engine_status)


def _apply_financial_services_overlay(
    result: dict[str, Any],
    task: InvestmentTaskRecord,
    payload: dict[str, Any],
    evidence: list[dict[str, Any]],
    engine_status: Optional[str] = None,
) -> dict[str, Any]:
    playbook = _infer_financial_services_playbook(payload)
    symbol = payload.get("symbol") or "目标资产"
    name = payload.get("asset_name") or symbol
    workflow_label = playbook["label"]
    result["engine"] = "financial_services"
    result["engine_label"] = _engine_label("financial_services")
    result["engine_status"] = engine_status or result.get("engine_status") or "completed"
    result["investor_summary"] = (
        f"{workflow_label} 已挂到 DeepFocus 任务链路：先确认输入包和工作流，再交给专门 worker 生成模型、"
        f"备忘录或控制清单，最后由 ReportAgent 输出可复核结论。当前对象：{name}（{symbol}）。"
    )

    findings = result.get("agent_findings") if isinstance(result.get("agent_findings"), dict) else {}
    findings["orchestrator"] = [
        f"已选择 Financial Services 工作流：{workflow_label}",
        f"目标：{payload.get('objective') or task.title}",
        "采用工作流编排，不替换 DeepFocus 的证据、日志和任务队列",
    ]
    findings["research"] = [
        f"叶子 worker：{', '.join(playbook['workers'])}",
        f"预期交付件：{', '.join(playbook['deliverables'][:3])}",
        "投研、估值、财报和运营控制会用同一套证据结构沉淀",
    ]
    findings["risk"] = [
        *playbook["controls"][:3],
        "所有模型输出、对账结论和 KYC 判断都保留人工复核闸门",
    ]
    findings["report"] = [
        f"输出会同时包含投资结论和 {workflow_label} 交付件清单",
        "资料不足时优先列输入缺口，而不是编造模型或引用",
    ]
    result["agent_findings"] = findings

    result["action_plan"] = [
        f"按 {workflow_label} 收集输入包：标的、期间、文件、假设和审批要求",
        *playbook["deliverables"][:4],
        "把最终材料交给人工复核后再进入下游动作",
    ][:8]
    result["risk_controls"] = [
        *playbook["controls"],
        "每个关键数字必须能追溯到文件、行情源或显式假设",
        "不自动执行交易、分录、KYC 放行或外部分发",
    ][:8]
    result["watchlist"] = [
        f"{workflow_label} 输入包是否完整",
        "关键假设是否有来源和日期",
        "模型、表格或备忘录是否通过审计检查",
        *list(result.get("watchlist") or [])[:3],
    ][:8]
    result["disconfirming_evidence"] = [
        "核心输入缺失或来源不可追溯",
        "模型输出无法由公式或文件回链验证",
        "人工审批前发现口径、期间或主体不一致",
        *list(result.get("disconfirming_evidence") or [])[:3],
    ][:8]
    result["plain_language_takeaway"] = (
        f"这次不是单纯问一只股票，而是按 {workflow_label} 做一套金融服务交付流程："
        "先补齐输入和审计口径，再产出报告、模型或控制清单。"
    )

    artifacts = list(result.get("artifacts") or [])
    result["artifacts"] = [
        {
            "type": "financial_services_playbook",
            "title": f"{workflow_label} 工作流计划",
            "content": _financial_services_playbook_artifact(playbook, payload, evidence),
        },
        {
            "type": "review_gate",
            "title": "人工复核闸门",
            "content": "\n".join(f"- {item}" for item in result["risk_controls"][:6]),
        },
        *artifacts[:2],
    ]
    return result


def _infer_financial_services_playbook(payload: dict[str, Any]) -> dict[str, Any]:
    haystack = " ".join(
        str(value)
        for value in [
            payload.get("title"),
            payload.get("task_type"),
            payload.get("objective"),
            payload.get("context"),
            json.dumps(payload.get("engine_config") or {}, ensure_ascii=False),
        ]
        if value
    ).lower()
    for key in (
        "kyc_screener",
        "gl_reconciler",
        "month_end_closer",
        "pitch_agent",
        "valuation_reviewer",
        "earnings_reviewer",
        "model_builder",
        "market_researcher",
    ):
        playbook = FINANCIAL_SERVICES_PLAYBOOKS[key]
        if any(str(trigger).lower() in haystack for trigger in playbook["triggers"]):
            return playbook
    return FINANCIAL_SERVICES_PLAYBOOKS["market_researcher"]


def _financial_services_playbook_artifact(
    playbook: dict[str, Any],
    payload: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    evidence_lines = [
        f"- {item.get('source') or '未知来源'}：{item.get('title') or '未命名资料'}"
        for item in evidence[:5]
    ] or ["- 暂无强相关资料；先补齐文件、行情、公告或内部台账。"]
    sections = [
        f"# {playbook['label']} 工作流计划",
        f"目标：{payload.get('objective') or '未填写'}",
        f"对象：{payload.get('asset_name') or payload.get('symbol') or '未指定'}",
        "## Worker 编排",
        "\n".join(f"- {item}" for item in playbook["workers"]),
        "## 交付件",
        "\n".join(f"- {item}" for item in playbook["deliverables"]),
        "## 控制点",
        "\n".join(f"- {item}" for item in playbook["controls"]),
        "## 当前证据",
        "\n".join(evidence_lines),
    ]
    return "\n\n".join(sections)


def _asset_research_profile(symbol: str, name: str) -> dict[str, Any]:
    normalized = f"{symbol} {name}".upper()
    if "300750" in normalized or "CATL" in normalized or "宁德时代" in name:
        return {
            "business_quality": "全球动力电池与储能电池龙头，商业质量取决于份额、技术路线、制造效率和客户结构能否抵消电池价格周期。",
            "drivers": [
                "动力电池需求跟随全球新能源车渗透率、车企库存周期和车型结构变化",
                "储能业务提供第二增长曲线，但需要验证订单质量、交付节奏和项目回款",
                "原材料价格下行利好成本端，但电池 ASP 下行会压缩超额利润",
                "海外客户、海外产能和合规能力决定中长期估值上限",
                "快充、钠电、麒麟电池、回收等技术布局需要转化为可计量利润",
            ],
            "risks": [
                "车企价格战向电池环节传导，导致 ASP 和毛利率承压",
                "海外贸易、补贴、合规和地缘政策影响全球扩张节奏",
                "资本开支、库存和应收账款若同步走高，会削弱现金流质量",
                "竞争对手在磷酸铁锂、储能或下一代电池路线上的替代风险",
            ],
            "risk_controls": [
                "没有看到毛利率、现金流和库存三者同时改善前，不提高结论等级",
                "若价格先大涨但财报未确认，避免追高扩大单一仓位",
                "海外政策或客户订单出现不利变化时，立即重估情景概率",
                "把储能和动力电池分开建模，避免用单一叙事覆盖全部业务",
            ],
            "action_plan": [
                "补齐最近两期财报：收入增速、毛利率、经营现金流、库存、应收账款、资本开支",
                "核对动力电池装机份额、储能出货/订单、海外客户和产能进展",
                "和 BYD、亿纬锂能、国轩高科、LGES、三星 SDI 做同业估值与盈利质量对比",
                "设置触发器：锂价、电池 ASP、整车价格战、海外政策、储能中标价格",
                "把估值拆成动力电池、储能、回收/材料和技术期权四块分别验证",
            ],
            "watchlist": [
                "动力电池全球份额是否稳定或提升",
                "储能业务是否贡献真实利润而非只贡献收入规模",
                "毛利率改善是否来自结构/效率，而不是一次性原材料周期",
                "经营现金流是否跟随利润同步改善",
                "海外产能和客户是否有可验证进展",
            ],
            "disconfirming_evidence": [
                "连续两个季度毛利率下滑且库存/应收上升",
                "海外政策或大客户订单导致出货节奏明显低于预期",
                "储能价格竞争恶化，收入增长不能转化为利润",
                "同业以更低估值提供相近增长和更好现金流",
            ],
            "bull_case": "电池价格周期趋稳，动力电池份额保持领先，储能利润率被验证，海外业务提供估值重估空间。",
            "bull_triggers": ["毛利率和经营现金流同步改善", "海外客户/产能进展超预期", "储能订单质量提升", "同业估值上修"],
            "base_case": "行业仍处于价格和库存消化阶段，公司保持龙头位置，但市场等待财报和订单质量确认。",
            "base_triggers": ["财报符合预期", "份额稳定", "锂价和电池 ASP 进入相对均衡", "价格行为未破坏中期趋势"],
            "bear_case": "价格战、海外政策或技术路线变化压缩盈利质量，市场下修龙头溢价。",
            "bear_triggers": ["毛利率持续下行", "库存/应收恶化", "海外政策不利", "储能低价竞争扩大"],
        }
    if "TSLA" in normalized or "TESLA" in normalized or "特斯拉" in name:
        return {
            "business_quality": "特斯拉需要拆成汽车制造、能源储能、FSD/Robotaxi 和 Optimus 等长期期权；短期可验证利润仍主要来自交付量、ASP、汽车毛利率和现金流。",
            "drivers": [
                "汽车交付量、ASP 和汽车业务毛利率决定短期盈利质量",
                "能源储能收入和毛利率能否持续扩张，是汽车周期外的第二验证点",
                "FSD/Robotaxi 需要监管、安全数据、用户付费率和会计收入共同验证",
                "Optimus 属于长周期叙事，量产、成本、客户和收入确认前不能按成熟业务估值",
                "中国、欧洲和美国新能源车竞争会影响价格、份额和库存周期",
            ],
            "risks": [
                "价格战或需求放缓导致 ASP 下滑、销量补偿不足，压缩汽车毛利率",
                "FSD/Robotaxi 安全事故或监管调查推迟商业化节奏",
                "Optimus 等叙事兑现慢于估值预期，引发长久期期权重估",
                "马斯克关键人风险、注意力分散或治理争议影响市场风险溢价",
                "AI/产能资本开支上行但现金流未同步改善",
            ],
            "risk_controls": [
                "没有最新 10-Q/10-K、电话会和 IR 口径前，不把机器人或 FSD 当作已验证收入",
                "若汽车 ASP 下行而交付量未补，先降低情景概率",
                "任何基于社媒或自媒体的催化，都必须回到 SEC/IR/主流媒体交叉验证",
                "估值拆分为汽车、能源、软件和长期期权，避免用单一叙事覆盖全部市值",
            ],
            "action_plan": [
                "调取最新 10-Q/10-K、8-K、股东信和电话会文字稿",
                "拆解交付量、ASP、汽车毛利率、能源毛利率、自由现金流和资本开支",
                "核对 FSD/Robotaxi 与 Optimus 进展是否来自 SEC/IR 或公司正式材料",
                "和 BYD、GM、Ford、Rivian、Lucid 及储能同业做估值和盈利质量对比",
                "把汽车、能源、FSD/Robotaxi、Optimus 分别做情景估值和反证条件",
            ],
            "watchlist": [
                "下一季度全球交付量及中国区价格/份额变化",
                "汽车业务毛利率和自由现金流是否同步改善",
                "能源储能部署量、毛利率和订单质量",
                "FSD/Robotaxi 监管、安全事件和付费率信号",
                "Optimus 是否出现官方量产、成本、客户或收入确认口径",
            ],
            "disconfirming_evidence": [
                "连续两个季度汽车 ASP 下滑且交付量未补",
                "汽车毛利率或自由现金流低于管理层指引和市场预期",
                "FSD/Robotaxi 因安全或监管问题被迫延后",
                "Optimus 量产被官方材料否认、延期或无法形成收入确认",
                "机构模型下修长期软件/机器人渗透率假设",
            ],
            "bull_case": "汽车毛利率企稳，能源业务继续高质量增长，FSD/Robotaxi 或 Optimus 出现官方可验证进展，市场上修长期期权价值。",
            "bull_triggers": ["交付量和汽车毛利率双改善", "能源业务利润率超预期", "FSD/Robotaxi 官方商业化里程碑", "自由现金流改善"],
            "base_case": "汽车业务仍受价格和需求周期约束，能源与软件叙事需要更多官方证据验证，价格以等待财报和交付数据为主。",
            "base_triggers": ["财报符合预期", "交付量未明显失速", "管理层指引稳定", "核心叙事未被官方否定"],
            "bear_case": "汽车价格战、监管事件或长期期权兑现落差压缩估值，市场重新聚焦近端毛利率和现金流。",
            "bear_triggers": ["ASP 持续下行", "毛利率/现金流恶化", "FSD 监管调查", "Optimus 延期或证据不足"],
        }
    return {
        "business_quality": f"{name}（{symbol}）需要从商业模式、盈利质量、行业周期和资本配置四个维度拆解。",
        "drivers": [
            "收入增长是否可持续",
            "毛利率和现金流是否同步改善",
            "竞争格局是否支持长期定价权",
            "估值是否已经透支乐观预期",
        ],
        "risks": [
            "核心指标恶化",
            "行业周期下行",
            "估值高于可验证增长",
            "单一事件导致情绪失真",
        ],
        "risk_controls": [
            "先定义失效条件，再讨论收益空间",
            "若价格先涨而基本面未验证，降低追入冲动",
            "遇到财报、监管、流动性事件时重新评估",
        ],
        "action_plan": [
            "补充最近两期财报和电话会纪要",
            "列出三条最重要的买入前验证问题",
            "设置观察触发器：成交量、公告、同业估值",
        ],
        "watchlist": [
            "收入增长是否可持续",
            "毛利率和现金流是否同步改善",
            "市场情绪是否过热",
        ],
        "disconfirming_evidence": [
            "若核心指标连续恶化，推翻乐观假设",
            "若同业更便宜且质量更高，重新排序机会",
        ],
        "bull_case": "核心催化被市场确认，估值或情绪继续修复。",
        "bull_triggers": ["成交量放大", "上调指引", "高质量订单/业务进展"],
        "base_case": "信息尚未形成强共识，价格以震荡和等待验证为主。",
        "base_triggers": ["公告兑现", "财报确认", "同业表现稳定"],
        "bear_case": "催化落空或风险暴露，市场重新下修预期。",
        "bear_triggers": ["毛利率下滑", "监管/供应链风险", "资金流出"],
    }


def _rank_relevant_evidence(
    evidence: list[dict[str, Any]],
    symbol: str,
    name: str,
    objective: str,
) -> list[dict[str, Any]]:
    aliases = _asset_aliases(symbol, name)
    objective_tokens = [token for token in re.split(r"[\s,，。;；:：/]+", objective) if len(token) >= 2][:8]
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in evidence:
        haystack = _compact_for_match(
            " ".join(
                str(value)
                for value in [
                    item.get("title"),
                    item.get("text"),
                    item.get("takeaway"),
                    item.get("source"),
                    " ".join(str(tag) for tag in (item.get("tags") or [])),
                ]
                if value
            )
        )
        score = _evidence_priority_bonus(item)
        score += sum(4 for alias in aliases if alias and _compact_for_match(alias) in haystack)
        score += sum(1 for token in objective_tokens if _compact_for_match(token) in haystack)
        if "1270018300health" in haystack or "deepfocus项目资料测试" in haystack:
            score -= 3
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:8]]


def _evidence_priority_bonus(item: dict[str, Any]) -> int:
    source_type = str(item.get("source_type") or "").lower()
    category = str(item.get("source_category") or "").lower()
    if source_type == "professional_report_analysis":
        return 100
    if source_type == "professional_rag":
        return 96
    if category in {"filing", "earnings"} or source_type in {"filing", "earnings"}:
        return 94
    if source_type == "options_signal":
        return 88
    if source_type == "options_signal_unavailable":
        return 82
    if source_type == "ai_supply_chain_capacity":
        return 84
    if source_type == "external_research_crawl":
        return 78
    if category in {"research", "upload", "internal"}:
        return 72
    if category == "market":
        return 60
    return 0


def _asset_aliases(symbol: str, name: str) -> list[str]:
    base = str(symbol or "").split(".")[0]
    aliases = [symbol, base, name]
    if base == "300750" or "宁德时代" in str(name):
        aliases.extend(["宁德时代", "宁王", "CATL", "Contemporary Amperex", "动力电池", "储能"])
    if base.upper() == "TSLA" or "特斯拉" in str(name) or "TESLA" in str(name).upper():
        aliases.extend(["TSLA", "Tesla", "特斯拉", "Elon Musk", "马斯克", "FSD", "Robotaxi", "Optimus"])
    return [alias for alias in aliases if alias]


def _compact_for_match(value: str) -> str:
    return re.sub(r"[\s，。！？、；;:：,.!?~～\"'“”‘’（）()【】\\[\\]{}_-]+", "", value).lower()


def _quote_summary(quote: dict[str, Any]) -> str:
    if not quote:
        return "行情快照未接入，本次不使用价格作为核心证据。"
    if quote.get("price") is None:
        return f"行情快照未返回价格：{quote.get('warning') or '需要手动刷新行情源'}。"
    change = quote.get("change_percent")
    change_text = f"，涨跌幅 {float(change):+.2f}%" if isinstance(change, (int, float)) else ""
    provider = quote.get("provider_name") or quote.get("provider") or "行情源"
    return f"行情快照：{quote.get('symbol')} 最新价 {quote.get('price')} {quote.get('currency') or ''}{change_text}，来源 {provider}。"


def _engine_label(engine: str) -> str:
    if engine == "tradingagents":
        return "TradingAgents"
    if engine == "financial_services":
        return "Financial Services Playbook"
    return "DeepFocus Native"


def _institutional_memo(
    symbol: str,
    name: str,
    objective: str,
    horizon: str,
    profile: dict[str, Any],
    quote_summary: str,
    evidence: list[dict[str, Any]],
) -> str:
    evidence_lines = [
        f"- {item.get('source') or '未知来源'}：{item.get('title') or '未命名资料'}"
        for item in evidence[:5]
    ] or ["- 暂无强相关资料；需要补充公告、财报、电话会纪要和行情数据。"]
    sections = [
        f"# {name}（{symbol}）机构级投研备忘录",
        f"研究目标：{objective}",
        f"研究周期：{horizon}",
        f"行情：{quote_summary}",
        "## 核心判断",
        profile["business_quality"],
        "## 主要驱动",
        "\n".join(f"- {item}" for item in profile["drivers"]),
        "## 情景框架",
        f"- Bull：{profile['bull_case']}",
        f"- Base：{profile['base_case']}",
        f"- Bear：{profile['bear_case']}",
        "## 关键风险",
        "\n".join(f"- {item}" for item in profile["risks"]),
        "## 需要验证的问题",
        "\n".join(f"- {item}" for item in profile["action_plan"]),
        "## 证据台账",
        "\n".join(evidence_lines),
    ]
    return "\n\n".join(sections)


def _safe_short(value: str, max_length: int = 120) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:max_length]


def _cloud_fallback_investment_result(
    task: InvestmentTaskRecord,
    evidence: list[dict[str, Any]],
    reason: str,
    llm: CloudResearchLLM,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    result = _mock_investment_result(task, evidence=evidence, payload_override=payload)
    result["engine_status"] = "cloud_fallback"
    result["confidence"] = min(float(result.get("confidence", 0.5)), 0.58)
    result["investor_summary"] = (
        f"云模型 {llm.provider_name}/{llm.model} 本轮未稳定返回，原因：{reason} "
        "系统已切换为本地机构框架，基于可用证据、行情快照和行业模板生成可审计报告；"
        "结论等级会被保守压低，建议模型通道恢复后复跑以获得更完整的引用和估值细节。"
    )
    findings = result.get("agent_findings") or {}
    findings["report"] = [
        *list(findings.get("report") or []),
        f"已命中全局配置：{llm.provider_name}/{llm.model}",
        reason,
        "云模型恢复后可重新运行，补齐更细的估值、引用和财报拆解。",
    ][:6]
    result["agent_findings"] = findings
    result["action_plan"] = [
        *list(result.get("action_plan") or []),
        "模型通道恢复后复跑一次完整云模型报告，核对引用和估值细节",
    ][:8]
    result["risk_controls"] = [
        "本次未使用云模型完成最终写作，置信度已按规则保守下调",
        *list(result.get("risk_controls") or []),
    ][:8]
    return result


async def _tradingagents_fallback_investment_result(
    task: InvestmentTaskRecord,
    evidence: list[dict[str, Any]],
    payload: dict[str, Any],
    issue: str,
) -> dict[str, Any]:
    llm = CloudResearchLLM()
    if llm.provider == "mock":
        result = _mock_investment_result(task, evidence=evidence, payload_override=payload)
    else:
        try:
            result = await asyncio.wait_for(
                _cloud_investment_result(llm, task, payload),
                timeout=min(AGENT_LLM_TIMEOUT_SECONDS, AGENT_REPORT_CLOUD_TIMEOUT_SECONDS),
            )
        except Exception as exc:
            guidance = _cloud_failure_guidance(exc)
            result = _cloud_fallback_investment_result(
                task,
                evidence,
                f"TradingAgents 未完成：{issue}；DeepFocus 云模型也未稳定返回：{guidance}",
                llm,
                payload,
            )

    result = _normalize_investment_result(result, task)
    result["engine"] = "tradingagents"
    result["engine_label"] = "TradingAgents + DeepFocus Native"
    result["engine_status"] = "tradingagents_fallback"
    result["confidence"] = min(float(result.get("confidence", 0.5)), 0.62)
    result["investor_summary"] = (
        f"TradingAgents 本轮未在 Cockpit 可用时限内稳定完成：{issue} "
        "系统已切换 DeepFocus Native，用已抓取证据、行情快照和本地投研框架生成可复核报告；"
        "建议需要完整 TradingAgents 辩论链时再用长任务模式重跑。"
    )

    findings = result.get("agent_findings") if isinstance(result.get("agent_findings"), dict) else {}
    findings["orchestrator"] = [
        f"TradingAgents 运行未完成：{issue}",
        "已自动切换 DeepFocus Native，避免 Cockpit 卡在长任务等待状态。",
        *list(findings.get("orchestrator") or []),
    ][:6]
    findings["risk"] = [
        "本次 TradingAgents 输出不完整，置信度已按规则保守下调。",
        *list(findings.get("risk") or []),
    ][:6]
    result["agent_findings"] = findings

    result["risk_controls"] = [
        "TradingAgents 未完整返回时不应直接作为交易依据",
        *list(result.get("risk_controls") or []),
    ][:8]
    result["action_plan"] = [
        *list(result.get("action_plan") or []),
        "如需完整 TradingAgents analyst/debate/trader 链路，切换长任务后重跑并对照本报告差异。",
    ][:8]
    artifacts = list(result.get("artifacts") or [])
    artifacts.append(
        {
            "type": "runtime_diagnostic",
            "title": "TradingAgents 运行诊断",
            "content": issue,
        }
    )
    result["artifacts"] = artifacts
    return result


def _cloud_failure_guidance(exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    if "invalidsubscription" in lowered or "codingplan" in lowered:
        return (
            "火山 Ark 返回 InvalidSubscription，账号没有有效 CodingPlan 订阅或订阅已过期。"
            "配置已生效，但服务端拒绝调用。请开通/续订 CodingPlan，或切换到可用的 Base URL 和模型。"
        )
    if "insufficient_quota" in lowered or "quota" in lowered or "billing" in lowered:
        return "云模型额度、账单或订阅状态不可用，请检查服务商控制台，或切换到可用模型。"
    if "invalid_api_key" in lowered or "unauthorized" in lowered or "authentication" in lowered:
        return "云模型鉴权失败，请检查设置里的 API Key、Base URL 和模型名。"
    return text[:500] or exc.__class__.__name__


def _normalize_investment_result(data: dict[str, Any], task: InvestmentTaskRecord) -> dict[str, Any]:
    fallback = _mock_investment_result(task)
    result = {**fallback, **(data or {})}
    result["engine"] = result.get("engine") or task.engine or "deepfocus"
    result["engine_label"] = result.get("engine_label") or _engine_label(str(result["engine"]))
    result["engine_status"] = result.get("engine_status") or "completed"
    result["confidence"] = _normalize_confidence(result.get("confidence", 0.5))
    if result.get("decision") not in {"avoid", "watch", "research_more", "candidate"}:
        result["decision"] = "research_more"
    result["agent_findings"] = _normalize_agent_findings(result.get("agent_findings"), fallback.get("agent_findings") or {})
    result["scenarios"] = _normalize_scenarios(result.get("scenarios"), fallback.get("scenarios") or [])
    for key in ("risk_controls", "action_plan", "watchlist", "disconfirming_evidence"):
        result[key] = _normalize_string_list(result.get(key), fallback.get(key) or [])[:8]
    result["evidence"] = _normalize_evidence_items(result.get("evidence"), fallback.get("evidence") or [])
    if not result.get("plain_language_takeaway"):
        result["plain_language_takeaway"] = fallback.get("plain_language_takeaway", "")
    result["disclaimer"] = "仅供投研参考，不构成投资建议、收益承诺或自动交易指令。"
    return result


def _normalize_confidence(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip()
        try:
            if text.endswith("%"):
                return max(0.0, min(1.0, float(text.rstrip("%")) / 100))
            parsed = float(text)
            return max(0.0, min(1.0, parsed / 100 if parsed > 1 else parsed))
        except ValueError:
            return 0.5
    try:
        parsed = float(value)
        return max(0.0, min(1.0, parsed / 100 if parsed > 1 else parsed))
    except (TypeError, ValueError):
        return 0.5


def _normalize_string_list(value: Any, fallback: list[Any]) -> list[str]:
    source = value if isinstance(value, list) else fallback
    items: list[str] = []
    for item in source:
        if isinstance(item, dict):
            text = item.get("summary") or item.get("title") or item.get("thesis") or item.get("takeaway") or json.dumps(item, ensure_ascii=False)
        else:
            text = str(item)
        clean = _safe_short(text, 180)
        if clean and clean not in items:
            items.append(clean)
    return items


def _normalize_agent_findings(value: Any, fallback: dict[str, Any]) -> dict[str, list[str]]:
    phases = ("orchestrator", "evidence", "research", "risk", "report")
    if isinstance(value, dict):
        return {
            phase: _normalize_string_list(value.get(phase), fallback.get(phase, []))
            for phase in phases
        }
    if isinstance(value, list):
        normalized = {phase: _normalize_string_list(fallback.get(phase), []) for phase in phases}
        normalized["research"] = _normalize_string_list(value, fallback.get("research", []))
        return normalized
    return {phase: _normalize_string_list(fallback.get(phase), []) for phase in phases}


def _normalize_scenarios(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) and value else fallback
    labels = ["bull", "base", "bear"]
    probabilities = [25, 50, 25]
    scenarios: list[dict[str, Any]] = []
    for index, item in enumerate(source[:3]):
        if isinstance(item, dict):
            case = str(item.get("case") or labels[index])
            thesis = str(item.get("thesis") or item.get("summary") or item.get("description") or "")
            triggers = _normalize_string_list(item.get("triggers"), [])
            probability = item.get("probability", probabilities[index])
        else:
            case = labels[index]
            thesis = str(item)
            triggers = []
            probability = probabilities[index]
        try:
            probability_value = int(float(str(probability).rstrip("%")))
        except (TypeError, ValueError):
            probability_value = probabilities[index]
        scenarios.append({
            "case": case,
            "probability": max(0, min(100, probability_value)),
            "thesis": _safe_short(thesis, 240),
            "triggers": triggers[:5],
        })
    return scenarios


def _normalize_evidence_items(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else fallback
    items: list[dict[str, Any]] = []
    for item in source[:8]:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("source") or item.get("takeaway") or "未命名证据")
            source_name = str(item.get("source") or "模型输出")
            source_type = str(item.get("source_type") or "model")
            takeaway = str(item.get("takeaway") or item.get("text") or item.get("summary") or title)
            try:
                score = float(item.get("credibility_score", 0.5))
            except (TypeError, ValueError):
                score = 0.5
            url = item.get("url")
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        else:
            title = _safe_short(str(item), 120)
            source_name = "模型输出"
            source_type = "model"
            takeaway = str(item)
            score = 0.5
            url = None
            tags = []
        items.append({
            "title": _safe_short(title, 160),
            "source": source_name,
            "source_type": source_type,
            "tags": tags,
            "credibility_score": max(0.0, min(1.0, score)),
            "url": url,
            "takeaway": _safe_short(takeaway, 240),
        })
    return items


def _append_log(task_id: str, agent: str, message: str, progress: Optional[int] = None) -> None:
    task = get_investment_task(task_id)
    if not task:
        return
    bounded_progress: Optional[int] = None
    if progress is not None:
        try:
            bounded_progress = max(0, min(100, int(progress)))
        except (TypeError, ValueError):
            bounded_progress = None
    logs = [entry.model_dump() for entry in task.logs]
    log_entry: dict[str, Any] = {"timestamp": now_iso(), "agent": agent, "message": message}
    if bounded_progress is not None:
        log_entry["progress"] = bounded_progress
    logs.append(log_entry)
    updates: dict[str, Any] = {"logs_json": json.dumps(logs, ensure_ascii=False)}
    if bounded_progress is not None:
        updates["progress"] = bounded_progress
        updates["assigned_agent"] = agent
    _update_task(task_id, **updates)


def _make_task_heartbeat(task_id: str, agent: str = "TradingAgents.Runner"):
    last_log_at: Optional[float] = None

    def heartbeat(message: str, progress: Optional[int] = None) -> bool:
        nonlocal last_log_at
        task = get_investment_task(task_id)
        if not task or task.status != "running":
            return False

        updates: dict[str, Any] = {"assigned_agent": agent}
        if progress is not None:
            try:
                bounded_progress = max(task.progress, min(98, max(0, int(progress))))
                updates["progress"] = bounded_progress
            except (TypeError, ValueError):
                bounded_progress = None
        else:
            bounded_progress = None
        _update_task(task_id, **updates)

        now = time.monotonic()
        if last_log_at is None or now - last_log_at >= AGENT_HEARTBEAT_LOG_SECONDS:
            _append_log(task_id, agent, message, progress=bounded_progress)
            last_log_at = now
        return True

    return heartbeat


def _register_task_runtime_process(task_id: str):
    def register(pid: int, kind: str) -> None:
        try:
            _update_task(task_id, runtime_pid=int(pid), runtime_kind=kind)
        except Exception:
            return

    return register


def _terminate_task_runtime_process(task_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT runtime_pid, runtime_kind FROM agent_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if not row:
        return False
    return _terminate_registered_runtime(row["runtime_pid"], row["runtime_kind"])


def _terminate_registered_runtime(pid: Any, kind: Any) -> bool:
    if str(kind or "") != "tradingagents_external":
        return False
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False

    try:
        os.killpg(process_id, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        try:
            os.kill(process_id, signal.SIGTERM)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return False


def _update_task(task_id: str, **updates: Any) -> None:
    if not updates:
        return
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [task_id]
    with _connect() as conn:
        conn.execute(f"UPDATE agent_tasks SET {assignments} WHERE id = ?", values)
        conn.commit()


def _row_to_record(row: dict[str, Any]) -> InvestmentTaskRecord:
    return InvestmentTaskRecord(
        id=row["id"],
        title=row["title"],
        symbol=row.get("symbol"),
        asset_name=row.get("asset_name"),
        task_type=row["task_type"],
        engine=row.get("engine") or (json.loads(row["input_json"]).get("engine") if row.get("input_json") else "deepfocus"),
        status=row["status"],
        priority=int(row["priority"]),
        assigned_agent=row.get("assigned_agent"),
        progress=int(row.get("progress") or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        error=row.get("error"),
        input=json.loads(row["input_json"]),
        logs=json.loads(row["logs_json"] or "[]"),
        result=json.loads(row["result_json"]) if row.get("result_json") else None,
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
