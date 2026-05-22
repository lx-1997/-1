from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Optional


@dataclass
class AgentEngineRunContext:
    task_id: str
    title: str
    symbol: str
    asset_name: str
    task_type: str
    payload: dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    heartbeat: Optional[Callable[[str, Optional[int]], Optional[bool]]] = None
    register_runtime_process: Optional[Callable[[int, str], None]] = None


@dataclass
class AgentEngineRunResult:
    result: dict[str, Any]
    logs: list[tuple[str, str, Optional[int]]] = field(default_factory=list)
