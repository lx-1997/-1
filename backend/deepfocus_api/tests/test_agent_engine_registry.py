"""Agent 引擎注册表的回归守卫。

把「硬编码 if/elif 引擎路由」重构成「ABC 契约 + 注册表」后，这些测试锁定：
  1. 注册表与 schemas.AgentEngine（API 入参枚举）严格同步——防止加了引擎忘记登记、
     或登记了忘记放进 Literal 校验；
  2. 未知/空 engine 回退默认引擎，复刻旧 else 分支语义；
  3. 每个引擎都具备完整契约（label / 非空 stages / 可调用 runner）；
  4. 旧 helper _engine_label / _engine_stages 仍走注册表，行为不回退。
"""
from __future__ import annotations

import inspect
from typing import get_args

# 导入 agent_runtime 触发引擎注册（注册是其 import 时的副作用）。
from deepfocus_api import agent_runtime as ar
from deepfocus_api.agent_engines import (
    DEFAULT_ENGINE_KEY,
    ENGINE_REGISTRY,
    AgentEngineSpec,
    get_engine,
    list_engines,
    resolve_engine,
)
from deepfocus_api.schemas import AgentEngine


def test_registry_is_in_sync_with_api_literal() -> None:
    # 唯一事实源（注册表）必须与 API 校验枚举严格一致。
    assert set(ENGINE_REGISTRY) == set(get_args(AgentEngine))


def test_default_engine_is_registered() -> None:
    assert DEFAULT_ENGINE_KEY in ENGINE_REGISTRY
    assert resolve_engine(DEFAULT_ENGINE_KEY).key == DEFAULT_ENGINE_KEY


def test_unknown_or_empty_engine_falls_back_to_default() -> None:
    # 复刻旧 if/elif 的 else 分支：未知/None/空串都回退默认引擎。
    assert resolve_engine("does-not-exist").key == DEFAULT_ENGINE_KEY
    assert resolve_engine(None).key == DEFAULT_ENGINE_KEY
    assert resolve_engine("").key == DEFAULT_ENGINE_KEY
    assert get_engine("does-not-exist") is None
    assert get_engine(None) is None


def test_every_engine_has_complete_contract() -> None:
    for spec in list_engines():
        assert isinstance(spec, AgentEngineSpec)
        assert spec.label, spec.key
        assert spec.stages, f"{spec.key} 缺少进度阶段"
        for stage in spec.stages:
            agent, message, progress = stage  # 形状必须是 (agent, message, progress)
            assert isinstance(agent, str) and agent
            assert isinstance(message, str) and message
            assert isinstance(progress, int)
        assert inspect.iscoroutinefunction(spec.runner), f"{spec.key} 的 runner 必须是 async"


def test_legacy_helpers_route_through_registry() -> None:
    # 旧 helper 必须返回与注册表一致的结果，且未知 key 回退默认引擎的 label/stages。
    for spec in list_engines():
        assert ar._engine_label(spec.key) == spec.label
        assert ar._engine_stages(spec.key) == spec.stages
    default = resolve_engine(DEFAULT_ENGINE_KEY)
    assert ar._engine_label("zzz-unknown") == default.label
    assert ar._engine_stages("zzz-unknown") == default.stages


def test_register_engine_is_idempotent_overwrite() -> None:
    # 重复 key 覆盖，不新增条目（便于测试/热替换），且事后可恢复。
    original = ENGINE_REGISTRY[DEFAULT_ENGINE_KEY]
    before = len(ENGINE_REGISTRY)

    async def _noop(_ex):  # pragma: no cover - 仅作占位 runner
        return {}

    try:
        ar.register_engine(
            AgentEngineSpec(key=DEFAULT_ENGINE_KEY, label="X", runner=_noop, stages=original.stages)
        )
        assert len(ENGINE_REGISTRY) == before
        assert resolve_engine(DEFAULT_ENGINE_KEY).label == "X"
    finally:
        ar.register_engine(original)
    assert resolve_engine(DEFAULT_ENGINE_KEY).label == original.label


def test_known_engine_labels_unchanged_after_refactor() -> None:
    # 锁定对外展示名，重构不得改变既有引擎的 label。
    assert resolve_engine("deepfocus").label == "DeepFocus Native"
    assert resolve_engine("tradingagents").label == "TradingAgents"
    assert resolve_engine("financial_services").label == "Financial Services Playbook"


def test_runner_accepts_single_execution_argument() -> None:
    # 契约：runner 签名是 (execution) -> awaitable，便于未来新增引擎照抄。
    for spec in list_engines():
        params = list(inspect.signature(spec.runner).parameters)
        assert len(params) == 1, f"{spec.key} runner 应只接收一个 execution 参数"
