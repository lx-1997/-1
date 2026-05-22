from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, AsyncIterator

from fastapi import Request

from .agent_runtime import get_investment_task
from .schemas import AgentLogEntry, AgentRunEvent, InvestmentTaskRecord


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def task_agent_events(task: InvestmentTaskRecord) -> list[AgentRunEvent]:
    events: list[AgentRunEvent] = [
        AgentRunEvent(
            id=f"{task.id}:state:{task.status}:{task.progress}:{task.updated_at}",
            task_id=task.id,
            type="run_state",
            surface="timeline",
            phase=_phase_from_agent(task.assigned_agent),
            agent=_core_agent_name(task.assigned_agent),
            title=_state_title(task),
            message=f"{task.title} · {task.progress}%",
            progress=task.progress,
            created_at=task.updated_at,
            payload={
                "status": task.status,
                "engine": task.engine,
                "symbol": task.symbol,
                "asset_name": task.asset_name,
            },
        )
    ]

    for index, log in enumerate(task.logs):
        event_type = _event_type_from_log(log, index, len(task.logs))
        events.append(
            AgentRunEvent(
                id=f"{task.id}:log:{index}:{event_type}:{_stable_digest(log.message)}",
                task_id=task.id,
                type=event_type,
                surface=_surface_for_event(event_type),
                phase=_phase_from_agent(log.agent),
                agent=_core_agent_name(log.agent),
                title=_title_for_log(log),
                message=log.message,
                progress=(
                    log.progress
                    if log.progress is not None
                    else (task.progress if index == len(task.logs) - 1 else None)
                ),
                created_at=log.timestamp,
                payload={
                    "raw_agent": log.agent,
                    "log_index": index,
                    "log_progress": log.progress,
                    "status": task.status,
                },
            )
        )

    if task.result:
        decision = str(task.result.get("decision") or "research_more")
        events.append(
            AgentRunEvent(
                id=f"{task.id}:artifact:investment-report:{task.updated_at}",
                task_id=task.id,
                type="artifact_update",
                surface="block",
                phase="report",
                agent="ReportAgent",
                title="投资决策报告",
                message=str(task.result.get("plain_language_takeaway") or task.result.get("investor_summary") or ""),
                progress=100 if task.status == "completed" else task.progress,
                created_at=task.completed_at or task.updated_at,
                payload={
                    "artifact_type": "investment_report",
                    "decision": decision,
                    "confidence": task.result.get("confidence"),
                    "risk_controls": task.result.get("risk_controls") or [],
                    "action_plan": task.result.get("action_plan") or [],
                },
            )
        )

    if task.status == "completed":
        events.append(
            AgentRunEvent(
                id=f"{task.id}:complete:{task.completed_at or task.updated_at}",
                task_id=task.id,
                type="run_complete",
                surface="control",
                phase="report",
                agent="ReportAgent",
                title="Agent Run 完成",
                message="投研报告已生成，等待投资者复核。",
                progress=100,
                created_at=task.completed_at or task.updated_at,
                payload={"status": task.status, "result_available": bool(task.result)},
            )
        )
    elif task.status in {"failed", "cancelled"}:
        events.append(
            AgentRunEvent(
                id=f"{task.id}:error:{task.completed_at or task.updated_at}",
                task_id=task.id,
                type="error",
                surface="block",
                phase=_phase_from_agent(task.assigned_agent),
                agent=_core_agent_name(task.assigned_agent),
                title="Agent Run 异常" if task.status == "failed" else "Agent Run 已取消",
                message=task.error or _latest_log_message(task) or "任务没有返回可用结果。",
                progress=task.progress,
                created_at=task.completed_at or task.updated_at,
                payload={"status": task.status},
            )
        )

    return events


async def agent_task_event_stream(task_id: str, request: Request) -> AsyncIterator[str]:
    emitted: set[str] = set()
    last_event_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    resume_applied = False
    yield _sse_event("connected", {"task_id": task_id, "connected": True}, retry_ms=2000)

    while not await request.is_disconnected():
        task = get_investment_task(task_id)
        if not task:
            yield _sse_event("error", {"task_id": task_id, "message": "Task not found"})
            yield _sse_event("done", {"task_id": task_id})
            return

        events = task_agent_events(task)
        if last_event_id and not resume_applied:
            emitted.update(_emitted_ids_through(events, last_event_id))
            resume_applied = True
        for event in events:
            if event.id in emitted:
                continue
            emitted.add(event.id)
            yield _sse_event(event.type, event.model_dump(mode="json"), event_id=event.id)

        if task.status in TERMINAL_STATUSES:
            yield _sse_event("done", {"task_id": task_id, "status": task.status})
            return

        yield _sse_event("heartbeat", {"task_id": task_id, "updated_at": task.updated_at})
        await asyncio.sleep(1.2)


def _event_type_from_log(log: AgentLogEntry, index: int, total: int) -> str:
    agent = log.agent.lower()
    message = log.message.lower()
    if "失败" in message or "error" in message or "异常" in message:
        return "error"
    if index == 0 or "taskcenter" in agent or "orchestrator" in agent:
        return "run_state"
    if "evidence" in agent or "datasource" in agent or "资料" in message or "证据" in message:
        return "tool_result" if ("命中" in message or index == total - 1) else "tool_progress"
    if "report" in agent or "portfolio" in agent:
        return "artifact_update"
    return "reasoning_delta"


def _emitted_ids_through(events: list[AgentRunEvent], last_event_id: str) -> set[str]:
    event_ids = [event.id for event in events]
    if last_event_id not in event_ids:
        return set()
    return set(event_ids[: event_ids.index(last_event_id) + 1])


def _surface_for_event(event_type: str) -> str:
    if event_type in {"run_complete"}:
        return "control"
    if event_type in {"reasoning_delta", "artifact_update", "approval_required", "error"}:
        return "block"
    return "timeline"


def _phase_from_agent(agent: str | None) -> str:
    text = (agent or "").lower()
    if "evidence" in text or "datasource" in text:
        return "evidence"
    if any(
        token in text
        for token in (
            "research",
            "analyst",
            "sentiment",
            "scenario",
            "debate",
            "trader",
            "fsiworkflow",
            "modelbuilder",
            "earnings",
            "valuation",
            "pitch",
        )
    ):
        return "research"
    if any(token in text for token in ("risk", "control", "kyc", "reconciler", "reconciliation")):
        return "risk"
    if any(token in text for token in ("report", "portfolio", "modelrouter", "resultmapper")):
        return "report"
    return "orchestrator"


def _core_agent_name(agent: str | None) -> str:
    phase = _phase_from_agent(agent)
    return {
        "orchestrator": "OrchestratorAgent",
        "evidence": "EvidenceAgent",
        "research": "ResearchAgent",
        "risk": "RiskAgent",
        "report": "ReportAgent",
    }[phase]


def _title_for_log(log: AgentLogEntry) -> str:
    phase = _phase_from_agent(log.agent)
    return {
        "orchestrator": "任务编排",
        "evidence": "证据检索",
        "research": "投资研究",
        "risk": "风险复核",
        "report": "报告生成",
    }[phase]


def _state_title(task: InvestmentTaskRecord) -> str:
    return {
        "pending": "任务排队中",
        "running": "Agent Run 执行中",
        "waiting_approval": "等待投资者确认",
        "completed": "Agent Run 已完成",
        "failed": "Agent Run 失败",
        "cancelled": "Agent Run 已取消",
    }.get(task.status, "Agent Run 状态")


def _latest_log_message(task: InvestmentTaskRecord) -> str:
    return task.logs[-1].message if task.logs else ""


def _stable_digest(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:10]


def _sse_event(
    event_type: str,
    payload: dict[str, Any],
    event_id: str | None = None,
    retry_ms: int | None = None,
) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"
