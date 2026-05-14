from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .data_sources import collect_task_evidence
from .llm import CloudResearchLLM
from .schemas import InvestmentTaskCreateRequest, InvestmentTaskRecord


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_AGENT_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".agent_tasks.sqlite3"),
    )
)

WORKER_POLL_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_WORKER_POLL_SECONDS", "2.5"))
AGENT_LLM_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_AGENT_LLM_TIMEOUT_SECONDS", "120"))

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
                status TEXT NOT NULL,
                priority INTEGER NOT NULL,
                assigned_agent TEXT,
                progress INTEGER NOT NULL DEFAULT 0,
                input_json TEXT NOT NULL,
                result_json TEXT,
                logs_json TEXT NOT NULL,
                error TEXT,
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
        conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'pending',
                progress = 0,
                assigned_agent = 'OrchestratorAgent',
                updated_at = ?
            WHERE status = 'running'
            """,
            (now_iso(),),
        )
        conn.commit()


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
                id, title, symbol, asset_name, task_type, status, priority, assigned_agent,
                progress, input_json, result_json, logs_json, error, created_at, updated_at,
                started_at, completed_at
            ) VALUES (
                :id, :title, :symbol, :asset_name, :task_type, :status, :priority,
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
    logs = [entry.model_dump() for entry in task.logs]
    logs.append({"timestamp": now_iso(), "agent": "TaskCenter", "message": "用户取消任务。"})
    _update_task(
        task_id,
        status="cancelled",
        progress=task.progress,
        logs_json=json.dumps(logs, ensure_ascii=False),
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
        _append_log(task.id, "DataSourceAgent", "同步服务器/API/网页数据源，并检索本地上传资料。", progress=14)
        evidence = await collect_task_evidence(payload)
        evidence_message = (
            f"已命中 {len(evidence)} 条可追溯资料，进入投研上下文。"
            if evidence
            else "暂未命中外部资料，报告将明确提示资料不足。"
        )
        _append_log(task.id, "EvidenceAgent", evidence_message, progress=18)
        payload_with_evidence = {**payload, "evidence": evidence}
        stages = [
            ("ResearchAgent", "梳理业务事实、核心问题和投资假设。", 30),
            ("SentimentAgent", "提炼新闻、社区和文本情绪信号。", 35),
            ("ScenarioAgent", "生成牛市/基准/熊市情景和触发条件。", 55),
            ("RiskAgent", "识别亏损路径、失效条件和仓位纪律。", 72),
            ("ReportAgent", "合并为投资者可读的决策报告。", 90),
        ]
        for agent, message, progress in stages:
            _append_log(task.id, agent, message, progress=progress)
            await asyncio.sleep(0.15)

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            result = _mock_investment_result(task, evidence=evidence)
        else:
            result = await asyncio.wait_for(
                _cloud_investment_result(llm, task, payload_with_evidence),
                timeout=AGENT_LLM_TIMEOUT_SECONDS,
            )

        _append_log(task.id, "ReportAgent", "最终报告已生成，等待投资者复核。", progress=98)
        _update_task(
            task.id,
            status="completed",
            progress=100,
            result_json=json.dumps(result, ensure_ascii=False),
            completed_at=now_iso(),
        )
    except Exception as exc:
        _append_log(task.id, "OrchestratorAgent", f"任务失败：{exc}", progress=100)
        _update_task(task.id, status="failed", error=str(exc), completed_at=now_iso())


async def _cloud_investment_result(
    llm: CloudResearchLLM,
    task: InvestmentTaskRecord,
    payload: dict[str, Any],
) -> dict[str, Any]:
    prompt = (
        "你是一个多 Agent 投资研究系统的总调度器。请模拟以下 Agent 协作："
        "ResearchAgent、SentimentAgent、ScenarioAgent、RiskAgent、ReportAgent。\n"
        "目标是生成投资者能看懂的专业投研报告，帮助提高决策质量，但不能承诺收益。"
        "请严格输出 JSON，字段：investor_summary, decision, confidence, agent_findings, "
        "scenarios, risk_controls, action_plan, watchlist, disconfirming_evidence, evidence, plain_language_takeaway, disclaimer。\n"
        "decision 只能是 avoid / watch / research_more / candidate；confidence 取 0 到 1。"
        "scenarios 每项包含 case, probability, thesis, triggers。"
        "evidence 每项包含 title, source, source_type, credibility_score, url, takeaway。"
        "agent_findings 是对象，包含 research, sentiment, scenario, risk, report 五个键。\n"
        "如果资料不足，必须直接说明缺口，不能编造实时行情、财报或新闻。\n"
        f"任务输入：{json.dumps(payload, ensure_ascii=False)[:16000]}"
    )
    data = await llm.complete_json(prompt)
    return _normalize_investment_result(data, task)


def _mock_investment_result(task: InvestmentTaskRecord, evidence: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    payload = task.input
    evidence = evidence or []
    symbol = payload.get("symbol") or "目标资产"
    name = payload.get("asset_name") or symbol
    horizon = payload.get("horizon") or "1-4周"
    objective = payload.get("objective") or "判断是否值得进一步研究。"
    context = payload.get("context") or ""
    context_signal = (
        f"已从数据源中心命中 {len(evidence)} 条资料"
        if evidence
        else ("资料较充分" if len(context) > 200 else "资料偏少，需补充公告、财报和行情数据")
    )
    evidence_titles = [f"{item.get('source')}：{item.get('title')}" for item in evidence[:3]]

    return {
        "investor_summary": (
            f"{name}（{symbol}）当前进入多 Agent 初筛。结论不是买卖建议，"
            f"而是给投资者一份可执行的研究路线：先确认资料来源，再看基本面、价格和情绪是否互相验证。"
        ),
        "decision": "research_more",
        "confidence": 0.64,
        "agent_findings": {
            "research": [
                f"研究目标：{objective}",
                f"投资周期：{horizon}",
                context_signal,
                *(evidence_titles or ["暂未找到可追溯外部证据"]),
            ],
            "sentiment": [
                "情绪信号需要和成交量/公告交叉验证",
                "单条新闻不应直接转化为交易动作",
            ],
            "scenario": [
                "基准情景：等待催化兑现和数据确认",
                "上行情景：业绩/指引超预期且资金确认",
                "下行情景：预期落空或风险事件放大",
            ],
            "risk": [
                "先定义失效条件，再讨论收益空间",
                "避免因为短期上涨追高扩大仓位",
            ],
            "report": [
                "适合进入观察名单",
                "下一步应补齐真实行情、财报和同业估值",
            ],
        },
        "scenarios": [
            {
                "case": "bull",
                "probability": 25,
                "thesis": "核心催化被市场确认，估值或情绪继续修复。",
                "triggers": ["成交量放大", "上调指引", "高质量订单/业务进展"],
            },
            {
                "case": "base",
                "probability": 50,
                "thesis": "信息尚未形成强共识，价格以震荡和等待验证为主。",
                "triggers": ["公告兑现", "财报确认", "同业表现稳定"],
            },
            {
                "case": "bear",
                "probability": 25,
                "thesis": "催化落空或风险暴露，市场重新下修预期。",
                "triggers": ["毛利率下滑", "监管/供应链风险", "资金流出"],
            },
        ],
        "risk_controls": [
            "任何结论必须有失效条件",
            "单一资产仓位不应由模型自动决定",
            "若价格先涨而基本面未验证，降低追入冲动",
            "遇到财报、监管、流动性事件时重新评估",
        ],
        "action_plan": [
            "补充最近两期财报和电话会纪要",
            "列出三条最重要的买入前验证问题",
            "设置观察触发器：成交量、公告、同业估值",
            "把结论分为事实、推断、待验证三栏",
        ],
        "watchlist": [
            "收入增长是否可持续",
            "毛利率和现金流是否同步改善",
            "市场情绪是否过热",
            "风险事件是否已有价格反应",
        ],
        "disconfirming_evidence": [
            "若核心指标连续恶化，推翻乐观假设",
            "若上涨只来自情绪而非业绩，降低置信度",
            "若同业更便宜且质量更高，重新排序机会",
        ],
        "evidence": [
            {
                "title": str(item.get("title") or "未命名资料"),
                "source": str(item.get("source") or "未知来源"),
                "source_type": str(item.get("source_type") or "unknown"),
                "tags": item.get("tags") or [],
                "credibility_score": item.get("credibility_score", 0.5),
                "url": item.get("url"),
                "takeaway": str(item.get("text") or "")[:180],
            }
            for item in evidence[:8]
        ],
        "plain_language_takeaway": (
            "这不是让你马上买，而是告诉你：这只标的值得继续研究，"
            "但必须用财报、公告和价格行为确认，先控风险再谈收益。"
        ),
        "disclaimer": "仅供投研参考，不构成投资建议、收益承诺或自动交易指令。",
    }


def _normalize_investment_result(data: dict[str, Any], task: InvestmentTaskRecord) -> dict[str, Any]:
    fallback = _mock_investment_result(task)
    result = {**fallback, **(data or {})}
    try:
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
    except (TypeError, ValueError):
        result["confidence"] = 0.5
    if result.get("decision") not in {"avoid", "watch", "research_more", "candidate"}:
        result["decision"] = "research_more"
    if not isinstance(result.get("evidence"), list):
        result["evidence"] = []
    result["disclaimer"] = "仅供投研参考，不构成投资建议、收益承诺或自动交易指令。"
    return result


def _append_log(task_id: str, agent: str, message: str, progress: Optional[int] = None) -> None:
    task = get_investment_task(task_id)
    if not task:
        return
    logs = [entry.model_dump() for entry in task.logs]
    logs.append({"timestamp": now_iso(), "agent": agent, "message": message})
    updates: dict[str, Any] = {"logs_json": json.dumps(logs, ensure_ascii=False)}
    if progress is not None:
        updates["progress"] = progress
        updates["assigned_agent"] = agent
    _update_task(task_id, **updates)


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
