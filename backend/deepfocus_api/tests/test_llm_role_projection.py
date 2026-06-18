from __future__ import annotations

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
