"""Shared CoreAgent boundary for DeepFocus.

The module ports the useful Harness ideas (named profiles, a guarded tool
seam, and an append-only event ledger) without embedding a second runtime in
the FastAPI process. Existing LLM and specialist analyzers remain adapters.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import threading
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping


CORE_AGENT_PROTOCOL_VERSION = "core-agent-v1"
CORE_AGENT_HARNESS_VERSION = "research-harness-v1"
CORE_AGENT_LEDGER_ENV = "DEEPFOCUS_CORE_AGENT_LEDGER_PATH"
CORE_AGENT_MAX_EVENTS_ENV = "DEEPFOCUS_CORE_AGENT_MAX_EVENTS"
CORE_AGENT_ENABLED_ENV = "DEEPFOCUS_CORE_AGENT_ENABLED"


# Finance profile: only registered read-only data tools. Dynamic MCP is
# deny-by-default and may only be intersected with this set by the caller.
TOOL_ALLOWLIST = frozenset({
    "get_market_quote", "get_financials", "get_fund_flow", "get_valuation",
    "get_analyst_consensus", "get_stock_news", "get_dividend_history",
    "get_dragon_tiger", "get_my_watchlist", "get_stock_announcements",
    "get_next_disclosure", "get_cn_calendar", "resolve_symbol",
    "compare_stocks", "get_stock_snapshot", "get_financial_statements",
    "assess_long_term_bull", "search_our_content", "get_site_fast_news",
    "get_site_articles", "get_industry_context", "get_peer_comparison",
    "get_supply_chain_context", "get_theme_stocks", "get_limit_up_ladder",
    "get_stock_research", "get_daily_review", "get_recent_research",
    "get_institution_notes", "get_ai_fund_snapshot", "get_ai_fund_arena",
    "get_recent_content_digest",
    "get_hot_stocks", "get_market_data", "get_celebrity_views",
    "get_trading_discipline",
    # Deterministic read-only tools registered by ``main.py`` (they live
    # beside the API routes rather than in ``agent_tools.py``).  Keep these
    # in the shared profile too; otherwise the CoreAgent filter would hide
    # price history, the ground-truth verdict card, and the A-share screen
    # even though the legacy tool loop advertises them.
    "get_stock_verdict", "get_verdict_history", "get_price_history",
    "get_macro_environment", "get_options_signal", "get_stock_comparison",
    "screen_a_share_candidates",
})

_BLOCKED_TOOL_RE = re.compile(
    r"(?:^|[_./-])(write|delete|remove|update|create|mutate|execute|run|"
    r"shell|bash|filesystem|file|subprocess|code|python|browser|credential|"
    r"secret|order|trade)(?:$|[_./-])",
    re.I,
)


def is_read_only_tool(name: str, allowlist: Iterable[str] = TOOL_ALLOWLIST) -> bool:
    candidate = str(name or "").strip()
    return bool(candidate) and candidate in set(allowlist) and not _BLOCKED_TOOL_RE.search(candidate)


def _safe_profile_tools(profile: "CoreAgentProfile") -> frozenset[str]:
    """Apply the global finance policy even to caller-supplied profiles."""
    return frozenset(
        name for name in profile.allow_tools
        if is_read_only_tool(name, TOOL_ALLOWLIST)
    )


@dataclass(frozen=True)
class CoreAgentProfile:
    name: str
    description: str
    allow_tools: frozenset[str] = field(default_factory=frozenset)
    max_rounds: int = 4
    timeout_seconds: float = 60.0
    require_tool_first: bool = False
    require_site_evidence: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allow_tools": sorted(_safe_profile_tools(self)),
            "max_rounds": self.max_rounds,
            "timeout_seconds": self.timeout_seconds,
            "require_tool_first": self.require_tool_first,
            "require_site_evidence": self.require_site_evidence,
        }


CORE_AGENT_PROFILES: dict[str, CoreAgentProfile] = {
    "chat": CoreAgentProfile("chat", "普通对话；不自动暴露外部工具。", frozenset(), 1, 30.0),
    # Keep an explicit profile for callers that still name the historical
    # orchestrator route.  It is intentionally tool-free: the route may use
    # the legacy orchestrator adapter, but it must still cross the same
    # CoreAgent lifecycle and policy boundary.
    "orchestrator": CoreAgentProfile(
        "orchestrator", "兼容编排回答；由宿主适配器决定是否取证。", frozenset(), 2, 60.0,
    ),
    "research": CoreAgentProfile(
        "research", "只读投研工具闭环，带证据和发布前审校。",
        TOOL_ALLOWLIST, 6, 60.0, True, False,
    ),
    "snapshot": CoreAgentProfile(
        "snapshot", "快照/事实问答，只读工具闭环。",
        TOOL_ALLOWLIST, 3, 45.0, True, False,
    ),
    "document": CoreAgentProfile("document", "研报、新闻和附件解读适配器。", frozenset(), 1, 150.0),
    "roundtable": CoreAgentProfile(
        "roundtable", "统一取证包之上的圆桌适配器。",
        TOOL_ALLOWLIST, 8, 180.0, False, False,
    ),
}


@dataclass
class CoreAgentRequest:
    objective: str
    mode: str = "auto"
    context: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    stock: Any = None
    attachments: list[str] = field(default_factory=list)
    channel: str = "web"
    tenant_id: str = ""
    user_id: str = ""
    ifind_user: bool = False
    timeout_seconds: float | None = None
    max_rounds: int | None = None
    require_tool_first: bool | None = None
    require_site_evidence: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "CoreAgentRequest":
        return CoreAgentRequest(
            objective=str(self.objective or "").strip(),
            mode=str(self.mode or "auto").strip().lower() or "auto",
            context=str(self.context or ""),
            history=[
                {
                    "role": str(item.get("role") or "")[:16],
                    "content": str(item.get("content") or "")[:2000],
                }
                for item in (self.history or [])
                if isinstance(item, Mapping) and str(item.get("content") or "").strip()
            ][-8:],
            stock=self.stock,
            attachments=[str(item)[:160] for item in (self.attachments or []) if str(item).strip()][:8],
            channel=str(self.channel or "web")[:32],
            tenant_id=str(self.tenant_id or "")[:128],
            user_id=str(self.user_id or "")[:128],
            ifind_user=bool(self.ifind_user),
            timeout_seconds=self.timeout_seconds,
            max_rounds=self.max_rounds,
            require_tool_first=self.require_tool_first,
            require_site_evidence=bool(self.require_site_evidence),
            metadata=dict(self.metadata or {}),
        )


@dataclass
class CoreAgentEvent:
    id: str
    run_id: str
    type: str
    phase: str
    title: str
    message: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoreAgentResult:
    run_id: str
    mode: str
    route: str
    status: str = "completed"
    answer: str = ""
    raw: Any = None
    structured: Any = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
    provider: str = ""
    model: str = ""
    error: str | None = None
    protocol_version: str = CORE_AGENT_PROTOCOL_VERSION
    harness_version: str = CORE_AGENT_HARNESS_VERSION
    started_at: str = ""
    completed_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed" and not self.error

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data = {
            "run_id": self.run_id,
            "mode": self.mode,
            "route": self.route,
            "status": self.status,
            "answer": self.answer,
            "trace": _safe_json(self.trace),
            "sources": _safe_json(self.sources),
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
            "confidence": self.confidence,
            "provider": self.provider,
            "model": self.model,
            "error": self.error,
            "protocol_version": self.protocol_version,
            "harness_version": self.harness_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        if include_raw:
            data["raw"] = _safe_json(self.raw)
            data["structured"] = _safe_json(self.structured)
        return data


Runner = Callable[..., Any]
EmitCallback = Callable[[str, dict[str, Any]], Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _safe_json(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:600]
    if hasattr(value, "model_dump"):
        try:
            return _safe_json(value.model_dump(mode="json"), depth + 1)
        except Exception:
            return str(value)[:600]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:80]:
            key = str(raw_key)[:100]
            if re.search(r"(?:api[_-]?key|token|secret|password|authorization|cookie|credential)", key, re.I):
                out[key] = "[REDACTED]"
            else:
                out[key] = _safe_json(raw_value, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_json(item, depth + 1) for item in list(value)[:40]]
    return str(value)[:600]


def _structured(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _answer(value: Any) -> str:
    data = _structured(value)
    if isinstance(data, Mapping):
        for key in ("answer", "content", "synthesis", "executive_summary", "one_liner", "summary", "text"):
            candidate = data.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return data.strip() if isinstance(data, str) else ""


def _provider_model(llm: Any) -> tuple[str, str]:
    try:
        provider = str(getattr(llm, "provider_name", "") or getattr(llm, "provider", "") or "")
    except Exception:
        provider = ""
    try:
        model = str(getattr(llm, "model", "") or "")
    except Exception:
        model = ""
    return provider, model


async def _call_emit(callback: EmitCallback | None, event_type: str, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        result = callback(event_type, payload)
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _invoke(runner: Runner, request: CoreAgentRequest | None = None) -> Any:
    """Invoke an adapter with an optional request without masking its errors.

    ``inspect.signature`` can fail for some C-implemented/callable objects, so
    that part has a compatibility fallback.  Crucially, a ``TypeError`` raised
    *by the runner itself* is not caught and retried with a different arity:
    doing so can execute a side-effecting adapter twice and hide the real bug.
    """
    try:
        signature = inspect.signature(runner)
    except (TypeError, ValueError):
        value = runner()
    else:
        positional = [
            p for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        required = [p for p in positional if p.default is p.empty]
        value = runner(request) if request is not None and required else runner()
    return await value if inspect.isawaitable(value) else value


class CoreAgent:
    """One policy/lifecycle boundary for DeepFocus AI turns."""

    def __init__(
        self,
        llm: Any,
        *,
        profiles: Mapping[str, CoreAgentProfile] | None = None,
        ledger_path: str | os.PathLike[str] | None = None,
        max_events: int | None = None,
    ) -> None:
        self.llm = llm
        self.profiles = dict(profiles or CORE_AGENT_PROFILES)
        self.enabled = _env_bool(CORE_AGENT_ENABLED_ENV, True)
        if max_events is None:
            try:
                max_events = int(os.getenv(CORE_AGENT_MAX_EVENTS_ENV, "512") or 512)
            except (TypeError, ValueError):
                max_events = 512
        self.max_events = max(32, min(int(max_events), 10_000))
        configured_path = ledger_path if ledger_path is not None else os.getenv(CORE_AGENT_LEDGER_ENV, "").strip()
        self.ledger_path = Path(configured_path).expanduser() if configured_path else None
        self._events: deque[CoreAgentEvent] = deque(maxlen=self.max_events)
        self._runs: OrderedDict[str, list[CoreAgentEvent]] = OrderedDict()
        self._lock = threading.RLock()

    def profile(self, mode: str) -> CoreAgentProfile:
        key = str(mode or "chat").strip().lower()
        if key == "auto":
            key = "chat"
        return self.profiles.get(key, self.profiles.get("chat", CORE_AGENT_PROFILES["chat"]))

    def _resolve_mode(self, request: CoreAgentRequest) -> str:
        mode = str(request.mode or "auto").strip().lower()
        if mode != "auto":
            return mode if mode in self.profiles else "chat"
        # Keep intent classification deliberately conservative. The caller can
        # always choose an explicit profile when an adapter knows its mode.
        if re.search(r"行情|股价|估值|财报|研报|个股|大盘|板块|风险|持仓|比较|对比|新闻|公告|分析|研究", request.objective, re.I):
            return "research"
        return "chat"

    def _new_run(self) -> tuple[str, str]:
        return f"ca-{uuid.uuid4().hex[:16]}", _utc_now()

    async def _event(
        self,
        run_id: str,
        event_type: str,
        *,
        phase: str,
        title: str,
        message: str = "",
        payload: Mapping[str, Any] | None = None,
        emit: EmitCallback | None = None,
        callback_payload: Mapping[str, Any] | None = None,
    ) -> CoreAgentEvent:
        event = CoreAgentEvent(
            id=f"{run_id}:{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            type=str(event_type),
            phase=str(phase),
            title=str(title)[:160],
            message=str(message)[:600],
            created_at=_utc_now(),
            payload=_safe_json(dict(payload or {})),
        )
        with self._lock:
            self._events.append(event)
            self._runs.setdefault(run_id, []).append(event)
            while len(self._runs) > max(16, self.max_events // 8):
                self._runs.popitem(last=False)
        self._append_ledger(event)
        await _call_emit(emit, event_type, dict(callback_payload or payload or {}))
        return event

    def _append_ledger(self, event: CoreAgentEvent) -> None:
        if self.ledger_path is None:
            return
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            # Observability is best-effort and must never break user traffic.
            return

    def events(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if run_id:
                return [event.to_dict() for event in self._runs.get(run_id, [])]
            return [event.to_dict() for event in self._events]

    def status(self) -> dict[str, Any]:
        provider, model = _provider_model(self.llm)
        return {
            "enabled": self.enabled,
            "protocol_version": CORE_AGENT_PROTOCOL_VERSION,
            "harness_version": CORE_AGENT_HARNESS_VERSION,
            "provider": provider,
            "model": model,
            "profiles": {name: profile.public_dict() for name, profile in self.profiles.items()},
            "tool_policy": {
                "mode": "read_only_allowlist",
                "allowlist_count": len(TOOL_ALLOWLIST),
                "dynamic_mcp": "deny_by_default",
                "blocked_pattern": "write/delete/shell/filesystem/subprocess/order",
            },
            "ledger": {
                "mode": "jsonl" if self.ledger_path else "memory",
                "path_configured": bool(self.ledger_path),
                "max_events": self.max_events,
                "events_in_memory": len(self._events),
            },
        }

    async def _relay_tool_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        emit: EmitCallback | None,
    ) -> None:
        tool = str(payload.get("tool") or "")
        await self._event(
            run_id,
            event_type,
            phase="evidence" if event_type.startswith("tool_") else "research",
            title=f"工具：{tool}" if tool else "研究步骤",
            message=str(payload.get("summary") or payload.get("message") or ""),
            payload={
                "tool": tool,
                "ok": payload.get("ok"),
                "summary": str(payload.get("summary") or "")[:220],
            },
            emit=emit,
            # Existing SSE consumers still receive the original payload; only
            # the ledger representation is scrubbed/bounded.
            callback_payload=dict(payload),
        )

    async def _announce_plan(
        self,
        run_id: str,
        request: CoreAgentRequest,
        profile: CoreAgentProfile,
        *,
        emit: EmitCallback | None = None,
        route: str = "",
    ) -> None:
        """Record the small, user-safe plan that precedes model work.

        This is the Harness-shaped intent/policy seam: it makes a run
        inspectable without persisting the full prompt or exposing hidden
        chain-of-thought.  Consumers that only understand tool events simply
        ignore these additional lifecycle notifications.
        """
        await self._event(
            run_id,
            "intent_classified",
            phase="intent",
            title="意图已识别",
            message=f"已选择 {profile.name} 能力档。",
            payload={"mode": profile.name, "route": route or None},
            emit=emit,
        )
        await self._event(
            run_id,
            "policy_resolved",
            phase="policy",
            title="能力边界已确定",
            message="本轮只使用声明过的能力和只读数据工具。",
            payload={
                "allow_tools": len(_safe_profile_tools(profile)),
                "require_tool_first": (
                    profile.require_tool_first
                    if request.require_tool_first is None
                    else bool(request.require_tool_first)
                ),
                "require_site_evidence": bool(
                    request.require_site_evidence or profile.require_site_evidence
                ),
            },
            emit=emit,
        )

    async def _with_timeout(self, awaitable: Awaitable[Any], timeout: float | None) -> Any:
        if timeout is None or timeout <= 0:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=float(timeout))

    async def run_chat(
        self,
        request: CoreAgentRequest,
        *,
        legacy_request: Any = None,
        emit: EmitCallback | None = None,
        prefer_tool_agent: bool = True,
        fallback: bool = True,
        tool_kwargs: Mapping[str, Any] | None = None,
    ) -> CoreAgentResult:
        """Run one turn through the shared CoreAgent policy boundary."""
        req = request.normalized()
        mode = self._resolve_mode(req)
        profile = self.profile(mode)
        run_id, started_at = self._new_run()
        provider, model = _provider_model(self.llm)
        result = CoreAgentResult(
            run_id=run_id,
            mode=mode,
            route="pending",
            status="running",
            provider=provider,
            model=model,
            started_at=started_at,
        )
        await self._event(
            run_id,
            "run_start",
            phase="orchestrator",
            title="CoreAgent 开始",
            message="已创建统一投研会话。",
            payload={"mode": mode, "channel": req.channel, "objective_length": len(req.objective)},
            emit=emit,
        )
        await self._announce_plan(run_id, req, profile, emit=emit)

        async def relay(event_type: str, payload: dict[str, Any]) -> None:
            await self._relay_tool_event(run_id, event_type, payload, emit)

        raw: Any = None
        used_tool = False
        if (
            self.enabled
            and prefer_tool_agent
            and mode in {"research", "snapshot"}
            and hasattr(self.llm, "run_tool_agent")
        ):
            used_tool = True
            kwargs: dict[str, Any] = dict(tool_kwargs or {})
            requested_allow = kwargs.get("allowed_tool_names")
            if requested_allow is None:
                allowed = _safe_profile_tools(profile)
            else:
                allowed = frozenset(
                    str(name).strip() for name in requested_allow if str(name).strip()
                ) & _safe_profile_tools(profile)
            kwargs.update({
                "question": req.objective,
                "context_hint": req.context,
                "emit": relay,
                "ifind_user": req.ifind_user,
                "timeout_seconds": float(req.timeout_seconds or profile.timeout_seconds),
                "max_rounds": max(1, min(int(req.max_rounds or profile.max_rounds), 8)),
                "require_tool_first": (
                    profile.require_tool_first
                    if req.require_tool_first is None
                    else bool(req.require_tool_first)
                ),
                "require_site_evidence": bool(
                    req.require_site_evidence or profile.require_site_evidence
                ),
                "allowed_tool_names": allowed,
            })
            try:
                # Do not retry without ``allowed_tool_names``.  An adapter
                # that has not adopted the CoreAgent policy contract must
                # fall back to the non-tool path; silently dropping the
                # allow-list would turn a compatibility shim into an
                # unrestricted tool executor.  The production
                # ``CloudResearchLLM`` implements this keyword explicitly.
                raw = await self.llm.run_tool_agent(**kwargs)
            except asyncio.CancelledError:
                result.status = "cancelled"
                result.route = "tool-agent"
                await self._event(
                    run_id,
                    "error",
                    phase="orchestrator",
                    title="CoreAgent 已取消",
                    message="客户端取消了本次运行。",
                    emit=emit,
                )
                raise
            except Exception as exc:
                await self._event(
                    run_id,
                    "error",
                    phase="research",
                    title="只读工具闭环失败",
                    message="工具闭环暂不可用，准备按兼容路径降级。",
                    payload={"error_type": type(exc).__name__},
                    emit=emit,
                )
                raw = None

            if raw:
                result.route = "tool-agent"
                result.raw = raw
                result.structured = _structured(raw)
                result.answer = _answer(raw)
                if isinstance(raw, Mapping):
                    result.trace = list(raw.get("tool_trace") or [])
                    result.sources = list(
                        raw.get("sources") or raw.get("citable_sources") or []
                    )
                    result.gaps = [
                        str(item) for item in (raw.get("gaps") or []) if str(item).strip()
                    ]
                    result.warnings = [
                        str(item) for item in (raw.get("warnings") or []) if str(item).strip()
                    ]
                    try:
                        result.confidence = (
                            float(raw.get("confidence"))
                            if raw.get("confidence") is not None
                            else None
                        )
                    except (TypeError, ValueError):
                        result.confidence = None
                result.status = "completed"
                await self._event(
                    run_id,
                    "run_complete",
                    phase="report",
                    title="CoreAgent 完成",
                    message="只读取证与回答已完成。",
                    payload={
                        "route": result.route,
                        "answer_length": len(result.answer),
                        "tool_count": len(result.trace),
                    },
                    emit=emit,
                )
                result.completed_at = _utc_now()
                return result

        if used_tool and fallback:
            await self._event(
                run_id,
                "fallback",
                phase="orchestrator",
                title="兼容路径接管",
                message="只读工具闭环没有返回结果。",
                payload={"reason": "tool_agent_empty"},
                emit=emit,
            )
        if not fallback:
            result.route = "tool-agent" if used_tool else "disabled"
            result.status = "fallback"
            result.completed_at = _utc_now()
            return result

        try:
            # Keep the existing Pydantic request/response shape at the edge.
            if (
                legacy_request is not None
                and mode in {"research", "orchestrator"}
                and hasattr(self.llm, "orchestrator_chat")
            ):
                raw = await self._with_timeout(
                    self.llm.orchestrator_chat(legacy_request),
                    req.timeout_seconds or profile.timeout_seconds,
                )
                result.route = "orchestrator"
            elif legacy_request is not None and hasattr(self.llm, "general_chat"):
                raw = await self._with_timeout(
                    self.llm.general_chat(legacy_request),
                    req.timeout_seconds or profile.timeout_seconds,
                )
                result.route = "general-chat"
            elif hasattr(self.llm, "general_chat"):
                from .schemas import GeneralChatRequest

                raw = await self._with_timeout(
                    self.llm.general_chat(
                        GeneralChatRequest(
                            message=req.objective,
                            history=req.history,
                            context={},
                        )
                    ),
                    req.timeout_seconds or profile.timeout_seconds,
                )
                result.route = "general-chat"
            else:
                raise RuntimeError("LLM adapter does not provide a compatible chat method")
            result.raw = raw
            result.structured = _structured(raw)
            result.answer = _answer(raw)
            result.status = "completed"
        except asyncio.CancelledError:
            result.status = "cancelled"
            result.route = result.route if result.route != "pending" else "fallback"
            await self._event(
                run_id,
                "error",
                phase="orchestrator",
                title="CoreAgent 已取消",
                message="客户端取消了本次运行。",
                emit=emit,
            )
            raise
        except Exception as exc:
            result.route = result.route if result.route != "pending" else "fallback"
            result.status = "failed"
            result.error = str(exc)[:240]
            await self._event(
                run_id,
                "error",
                phase="report",
                title="CoreAgent 未返回答案",
                message="兼容路径也未返回可用答案。",
                payload={"error_type": type(exc).__name__},
                emit=emit,
            )

        await self._event(
            run_id,
            "run_complete" if result.status == "completed" else "error",
            phase="report" if result.status == "completed" else "orchestrator",
            title="CoreAgent 完成" if result.status == "completed" else "CoreAgent 失败",
            message="统一回答已完成。" if result.status == "completed" else "统一回答失败。",
            payload={"route": result.route, "answer_length": len(result.answer)},
            emit=emit,
        )
        result.completed_at = _utc_now()
        return result

    async def run_tool_only(
        self,
        request: CoreAgentRequest,
        *,
        emit: EmitCallback | None = None,
        tool_kwargs: Mapping[str, Any] | None = None,
    ) -> CoreAgentResult:
        """Run the research tool loop without stealing the caller's fallback."""
        result = await self.run_chat(
            request,
            emit=emit,
            prefer_tool_agent=True,
            fallback=False,
            tool_kwargs=tool_kwargs,
        )
        if result.status == "fallback":
            # Keep the audit ledger compatible for non-stream callers. The
            # stream router intentionally handles this handoff silently.
            await self._event(
                result.run_id,
                "fallback",
                phase="orchestrator",
                title="兼容路径接管",
                message="只读工具闭环没有返回结果。",
                payload={"reason": "tool_agent_empty"},
                emit=None,
            )
        return result

    async def run_adapter(
        self,
        request: CoreAgentRequest,
        runner: Runner,
        *,
        route: str = "adapter",
        emit: EmitCallback | None = None,
        propagate: bool = True,
    ) -> CoreAgentResult:
        """Wrap a specialist analyzer in the common session/event contract."""
        req = request.normalized()
        mode = self._resolve_mode(req)
        run_id, started_at = self._new_run()
        provider, model = _provider_model(self.llm)
        result = CoreAgentResult(
            run_id=run_id,
            mode=mode,
            route=route,
            status="running",
            provider=provider,
            model=model,
            started_at=started_at,
        )
        await self._event(
            run_id,
            "run_start",
            phase="orchestrator",
            title="CoreAgent 开始",
            message="已进入统一适配器会话。",
            payload={"mode": mode, "route": route, "objective_length": len(req.objective)},
            emit=emit,
        )
        await self._announce_plan(run_id, req, self.profile(mode), emit=emit, route=route)
        try:
            raw = await self._with_timeout(
                _invoke(runner, req),
                req.timeout_seconds or self.profile(mode).timeout_seconds,
            )
            result.raw = raw
            result.structured = _structured(raw)
            result.answer = _answer(raw)
            if isinstance(result.structured, Mapping):
                result.sources = list(
                    result.structured.get("sources")
                    or result.structured.get("citable_sources")
                    or []
                )
                result.gaps = [
                    str(item)
                    for item in (
                        result.structured.get("gaps")
                        or result.structured.get("data_issues")
                        or []
                    )
                ]
                result.warnings = [
                    str(item) for item in (result.structured.get("warnings") or [])
                ]
                try:
                    confidence = result.structured.get("confidence")
                    result.confidence = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    result.confidence = None
            result.status = "completed"
            await self._event(
                run_id,
                "artifact_update",
                phase="report",
                title="适配器产出",
                message="专业解读结果已返回。",
                payload={"route": route, "answer_length": len(result.answer)},
                emit=emit,
            )
        except asyncio.CancelledError:
            result.status = "cancelled"
            await self._event(
                run_id,
                "error",
                phase="report",
                title="CoreAgent 已取消",
                message="客户端取消了本次运行。",
                emit=emit,
            )
            raise
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)[:240]
            await self._event(
                run_id,
                "error",
                phase="report",
                title="适配器失败",
                message="专业解读适配器未返回结果。",
                payload={"route": route, "error_type": type(exc).__name__},
                emit=emit,
            )
            if propagate:
                result.completed_at = _utc_now()
                raise
        await self._event(
            run_id,
            "run_complete" if result.status == "completed" else "error",
            phase="report",
            title="CoreAgent 完成" if result.status == "completed" else "CoreAgent 失败",
            message="统一适配器会话结束。",
            payload={"route": route, "status": result.status},
            emit=emit,
        )
        result.completed_at = _utc_now()
        return result

    async def stream_chat(
        self,
        request: CoreAgentRequest,
        *,
        legacy_request: Any = None,
        emit: EmitCallback | None = None,
    ) -> AsyncIterator[str]:
        """Stream ordinary chat text while recording the shared lifecycle."""
        req = request.normalized()
        mode = self._resolve_mode(req)
        run_id, _started = self._new_run()
        await self._event(
            run_id,
            "run_start",
            phase="orchestrator",
            title="CoreAgent 开始",
            message="已创建流式会话。",
            payload={"mode": mode, "objective_length": len(req.objective)},
            emit=emit,
        )
        await self._announce_plan(run_id, req, self.profile(mode), emit=emit, route="stream")
        if hasattr(self.llm, "general_chat_stream"):
            if legacy_request is None:
                from .schemas import GeneralChatRequest

                legacy_request = GeneralChatRequest(
                    message=req.objective,
                    history=req.history,
                    context={},
                )
            try:
                async for delta in self.llm.general_chat_stream(legacy_request):
                    text = str(delta or "")
                    if text:
                        await self._event(
                            run_id,
                            "delta",
                            phase="report",
                            title="回答输出",
                            message=text,
                            payload={"chars": len(text)},
                            emit=emit,
                        )
                        yield text
                await self._event(
                    run_id,
                    "run_complete",
                    phase="report",
                    title="CoreAgent 完成",
                    message="流式回答已完成。",
                    emit=emit,
                )
                return
            except asyncio.CancelledError:
                await self._event(
                    run_id,
                    "error",
                    phase="report",
                    title="CoreAgent 已取消",
                    message="流式会话已取消。",
                    emit=emit,
                )
                raise
            except Exception:
                await self._event(
                    run_id,
                    "error",
                    phase="report",
                    title="流式回答失败",
                    message="流式模型暂不可用。",
                    emit=emit,
                )
                raise
        result = await self.run_chat(
            req,
            legacy_request=legacy_request,
            prefer_tool_agent=False,
            emit=emit,
        )
        if result.answer:
            yield result.answer


def core_agent_status(llm: Any) -> dict[str, Any]:
    return CoreAgent(llm).status()


__all__ = [
    "CORE_AGENT_HARNESS_VERSION",
    "CORE_AGENT_PROTOCOL_VERSION",
    "CORE_AGENT_PROFILES",
    "CoreAgent",
    "CoreAgentEvent",
    "CoreAgentProfile",
    "CoreAgentRequest",
    "CoreAgentResult",
    "TOOL_ALLOWLIST",
    "core_agent_status",
    "is_read_only_tool",
]
