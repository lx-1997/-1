from __future__ import annotations

from types import SimpleNamespace

import pytest

from deepfocus_api import llm as llm_mod
from deepfocus_api.llm import _display_role_text, _fallback_reasoning_trace
from deepfocus_api.schemas import OrchestratorChatRequest


def test_display_role_text_collapses_legacy_agent_names() -> None:
    text = _display_role_text("OrchestratorAgent 调度 ResearchAgent，随后进入多 Agent Run。")

    assert text == "Orchestrator 调度 Analyst，随后进入投研任务。"


def test_fallback_reasoning_trace_uses_core_roles() -> None:
    request = OrchestratorChatRequest(
        message="分析 NVDA",
        engine="deepfocus",
        mode="research",
        reasoning_mode="thinking",
    )

    trace = _fallback_reasoning_trace(request, should_create_task=True)
    titles = [step["title"] for step in trace]

    assert titles == ["Orchestrator", "Evidence", "Analyst", "Risk", "Report Builder"]


@pytest.mark.asyncio
async def test_complete_vision_disables_hidden_thinking_for_latency(monkeypatch) -> None:
    config = {
        "provider": "minimax",
        "model": "MiniMax-M3",
        "api_key": "test-key",
        "temperature": 0.2,
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    payloads = []

    class _Completions:
        async def create(self, **payload):
            payloads.append(payload)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
            )

    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(
        llm,
        "_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
    )

    await llm.complete_vision("read this", [b"png"], max_tokens=20, timeout_seconds=1)

    assert payloads
    assert payloads[0]["extra_body"] == {
        "reasoning_split": True,
        "thinking": {"type": "disabled"},
    }
    assert payloads[0]["temperature"] == 0.2
