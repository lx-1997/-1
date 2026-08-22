"""AI 工具不得绕过账号级资讯来源隔离。"""

import asyncio

from deepfocus_api import agent_tools


def test_dao2_site_content_tool_enables_source_filter(monkeypatch):
    captured = []

    def fake_list_realtime_messages(**kwargs):
        captured.append(kwargs)
        return []

    async def empty_data(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(agent_tools, "list_realtime_messages", fake_list_realtime_messages)
    monkeypatch.setattr(agent_tools, "fetch_eastmoney_earnings", empty_data)
    monkeypatch.setattr(agent_tools, "_tool_get_valuation", empty_data)
    monkeypatch.setattr(agent_tools.financial_statements, "fetch_statements", empty_data)
    token = agent_tools._BINDING_USER.set(" DAO2 ")
    try:
        asyncio.run(agent_tools._tool_search_our_content(query="茅台"))
        asyncio.run(agent_tools._tool_assess_long_term_bull("600519"))
    finally:
        agent_tools._BINDING_USER.reset(token)

    assert len(captured) == 2
    assert all(call["exclude_futoucaixin"] is True for call in captured)


def test_regular_user_site_content_tool_keeps_normal_view(monkeypatch):
    captured = []

    def fake_list_realtime_messages(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr(agent_tools, "list_realtime_messages", fake_list_realtime_messages)
    token = agent_tools._BINDING_USER.set("reader")
    try:
        asyncio.run(agent_tools._tool_search_our_content(query="茅台"))
    finally:
        agent_tools._BINDING_USER.reset(token)

    assert captured[0]["exclude_futoucaixin"] is False


def test_anonymous_ai_tool_uses_same_restricted_view():
    token = agent_tools._BINDING_USER.set("")
    try:
        assert agent_tools._exclude_restricted_news_source() is True
    finally:
        agent_tools._BINDING_USER.reset(token)
