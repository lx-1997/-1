from __future__ import annotations

import pytest

from deepfocus_api import dulus_runtime as dr
from deepfocus_api.dulus_runtime import (
    build_dulus_runtime_status,
    create_dulus_memory,
    inspect_authorized_webbridge,
    list_dulus_memories,
    run_dulus_roundtable,
)
from deepfocus_api.schemas import (
    DulusMemoryCreateRequest,
    DulusRoundtableRequest,
    DulusWebBridgeInspectRequest,
    StockSnapshot,
)


class MockLLM:
    provider = "mock"
    provider_name = "mock"
    model = "deepfocus-mock"


def test_dulus_status_runs_in_compliant_mode() -> None:
    status = build_dulus_runtime_status(MockLLM())

    assert status.compliant_mode is True
    assert any(provider.mode == "webbridge_disabled" for provider in status.providers)
    assert any(tool.id == "webbridge_browser_capture" and not tool.enabled for tool in status.tools)
    assert "白名单" in status.webbridge_policy
    assert any(tool.id == "authorized_webbridge_inspect" and tool.enabled for tool in status.tools)


def test_dulus_memory_persists_to_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "DB_PATH", tmp_path / "dulus_memory.sqlite3")

    record = create_dulus_memory(
        DulusMemoryCreateRequest(
            scope="project",
            hall="facts",
            title="授权 WebBridge 规则",
            content="只允许本机或白名单域名。",
            tags=["webbridge", "policy"],
        )
    )
    memories = list_dulus_memories(limit=10).memories

    assert record.title == "授权 WebBridge 规则"
    assert any(memory.id == record.id for memory in memories)
    assert any(memory.source == "palace_init" for memory in memories)


def test_authorized_webbridge_blocks_unlisted_hosts() -> None:
    result = inspect_authorized_webbridge(DulusWebBridgeInspectRequest(url="https://gemini.google.com/"))

    assert result.allowed is False
    assert "不在白名单" in result.policy


@pytest.mark.asyncio
async def test_dulus_roundtable_blocks_webbridge_and_returns_synthesis() -> None:
    response = await run_dulus_roundtable(
        MockLLM(),
        DulusRoundtableRequest(
            objective="研究 TSLA 的一到四周风险收益",
            context="需要结合财报、行情和社区分歧，但当前只有简短上下文。",
            stock=StockSnapshot(
                symbol="TSLA",
                name="Tesla",
                market="US",
                sector="EV",
                currentPrice=420.0,
                changePercent=-1.2,
                description="Tesla Inc.",
                focusLevel="high",
                communityScore=82,
            ),
            participants=["evidence", "research", "risk"],
            enabled_tools=["market_snapshot", "webbridge_browser_capture", "risk_review"],
        ),
    )

    assert response.turns
    assert response.synthesis
    assert response.decision == "blocked"
    assert any(trace.status == "blocked" for trace in response.tool_traces)
    assert any("WebBridge" in warning for warning in response.warnings)
