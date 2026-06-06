from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
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


# --- 引擎注册表 ---------------------------------------------------------------
# 每个投研引擎 = 一份声明式描述（key/label/阶段进度/runner）。新增引擎 = 写一个
# runner 函数 + register_engine(...) 一行，无需再散改 _process_task / _engine_stages /
# _engine_label 的硬编码 if/elif。与前端的视图注册表（workspaces.ts + VIEW_RENDER_CONFIG）
# 同构：注册表是引擎的唯一事实源（schemas.AgentEngine 由测试守卫保持同步）。

DEFAULT_ENGINE_KEY = "deepfocus"


@dataclass
class AgentEngineExecution:
    """一次引擎运行的全部输入。runner 与 agent_runtime 同模块，可直接复用其私有 helper，
    因此这里只携带「每个任务都不同」的数据，不重复传 _append_log 等模块级函数。"""

    task: Any
    payload: dict[str, Any]  # payload_with_evidence
    evidence: list[dict[str, Any]]
    run_context: AgentEngineRunContext


# runner: 拿到一次执行输入 → 返回已规范化 + 已应用兜底的最终 result dict。
EngineRunner = Callable[[AgentEngineExecution], Awaitable[dict[str, Any]]]


@dataclass
class AgentEngineSpec:
    key: str
    label: str
    runner: EngineRunner
    # 进度阶段（agent, message, progress），任务执行前逐条推送给前端时间线。
    stages: list[tuple[str, str, int]] = field(default_factory=list)
    description: str = ""


ENGINE_REGISTRY: dict[str, AgentEngineSpec] = {}


def register_engine(spec: AgentEngineSpec) -> AgentEngineSpec:
    """登记一个引擎；重复 key 覆盖（便于热替换/测试）。返回 spec 便于链式。"""
    ENGINE_REGISTRY[spec.key] = spec
    return spec


def get_engine(key: Optional[str]) -> Optional[AgentEngineSpec]:
    if not key:
        return None
    return ENGINE_REGISTRY.get(key)


def resolve_engine(key: Optional[str]) -> AgentEngineSpec:
    """按 key 取引擎；未知/为空 → 回退默认引擎（与旧 if/elif 的 else 分支语义一致）。"""
    spec = ENGINE_REGISTRY.get(key or "")
    if spec is not None:
        return spec
    default = ENGINE_REGISTRY.get(DEFAULT_ENGINE_KEY)
    if default is None:
        raise RuntimeError(
            f"默认引擎 {DEFAULT_ENGINE_KEY!r} 未注册——agent_runtime 是否已导入？"
        )
    return default


def list_engines() -> list[AgentEngineSpec]:
    """注册顺序返回全部引擎，供 API 自描述（/api/agent/engines）与 UI 渲染。"""
    return list(ENGINE_REGISTRY.values())
