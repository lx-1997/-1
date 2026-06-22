"""微信「扫码即问」会员问答开放后的回归守卫。

覆盖本次改动（去 lx199710 白名单 → 所有在效会员可用 + 省 token）：
- 在效会员发问 → 调 agent、计 1 次、答案写缓存；
- 答案缓存【跨用户复用】→ 命中即回、不再调 agent、不计次（省 token 核心）；
- 每日"现算"配额闸 → 超额不调 agent、回引导话术；
- 寒暄 / 帮助 / 会员过期 → 友好引导，均不调 agent、不计次；
- 出口经 compliance 中性化。

每个测试用独立临时库（monkeypatch data_store + metrics_store 的 DB_PATH），不触真实库、互不污染。
"""
from __future__ import annotations

import asyncio

import pytest

from deepfocus_api import auth, compliance, data_store, metrics_store
from deepfocus_api import weixin_channel as wc


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    monkeypatch.setattr(metrics_store, "DB_PATH", tmp_path / "ms.sqlite3")
    data_store.init_data_store()
    metrics_store.init_db()

    binding = {
        "active": True, "username": "alice", "ilink_bot_id": "bot-alice",
        "token": "tk", "base_url": "https://ilink.test",
    }
    monkeypatch.setattr(wc.bind, "get_by_bot", lambda bot_id: binding)
    monkeypatch.setattr(wc.bind, "update_context_token", lambda *a, **k: None)
    monkeypatch.setattr(wc.ilink, "extract_inbound_text", lambda m: m.get("text", ""))

    sent: list[str] = []

    async def _fake_send(token, base_url, to, ctx, text, client=None):
        sent.append(text)
        return {"ret": 0}

    monkeypatch.setattr(wc.ilink, "send_text", _fake_send)
    # 默认：在效尊享会员
    monkeypatch.setattr(auth, "membership_of_username", lambda u: {"tier": "premium"})

    return {"binding": binding, "sent": sent, "mp": monkeypatch}


def _msg(text: str, ctx: str = "ctx1", user: str = "u1") -> dict:
    return {"message_type": 1, "from_user_id": user, "context_token": ctx, "text": text}


def _mgr(answer: str = "对该标的的研究观点……", calls: list | None = None) -> wc.WeixinChannelManager:
    async def _agent(question: str, hint: str):
        if calls is not None:
            calls.append(question)
        return answer

    return wc.WeixinChannelManager(agent_fn=_agent)


# ---------- 指纹归一化 ----------

def test_fingerprint_normalizes_whitespace_and_punct():
    # 仅"首尾空白 + 尾部标点 + 大小写"的差异应归一到同一指纹（内部空格按单空格保留，不强删）
    a = wc._qa_fingerprint("今天大盘怎么样？")
    b = wc._qa_fingerprint("  今天大盘怎么样 ")
    assert a and a == b
    assert wc._qa_fingerprint("AAPL") == wc._qa_fingerprint(" aapl. ")
    assert wc._qa_fingerprint("   ") == ""  # 空问题不缓存


# ---------- 会员发问：调 agent + 计次 + 写缓存 ----------

def test_member_question_calls_agent_counts_and_caches(env):
    calls: list = []
    mgr = _mgr(answer="贵州茅台的研究观点", calls=calls)
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("贵州茅台怎么样")]))

    assert calls == ["贵州茅台怎么样"]                       # agent 被调一次
    assert env["sent"][-1] == "贵州茅台的研究观点"            # 回了答案
    assert metrics_store.get_daily("q:wxqa:alice") == 1      # 现算计 1 次
    fp = wc._qa_fingerprint("贵州茅台怎么样")
    assert data_store.latest("wx_qa", fp)["answer"] == "贵州茅台的研究观点"  # 已写缓存


# ---------- 缓存跨用户复用：命中即回、不调 agent、不计次 ----------

def test_cache_hit_is_cross_user_and_free(env):
    calls: list = []
    mgr = _mgr(answer="大盘综述", calls=calls)
    # alice 先问 → 现算 + 缓存
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("今天大盘怎么样")]))
    assert len(calls) == 1

    # bob（另一用户）问同一问题 → 命中 alice 的缓存
    env["binding"]["username"] = "bob"
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("今天大盘怎么样")]))

    assert len(calls) == 1                                   # agent 没有被再次调用（0 token）
    assert env["sent"][-1] == "大盘综述"                      # 仍回了答案
    assert metrics_store.get_daily("q:wxqa:bob") == 0        # 命中缓存不计次


# ---------- 每日配额闸：超额不调 agent ----------

def test_daily_quota_blocks_agent(env):
    env["mp"].setattr(wc, "_QA_DAILY_LIMIT", 2)
    calls: list = []
    mgr = _mgr(answer="观点", calls=calls)
    # 两个不同问题（不命中缓存）→ 各计 1 次
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("问题甲")]))
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("问题乙")]))
    assert len(calls) == 2
    assert metrics_store.get_daily("q:wxqa:alice") == 2

    # 第三个新问题 → 超额，不调 agent，回引导话术
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("问题丙")]))
    assert len(calls) == 2                                   # 仍是 2，未现算
    assert env["sent"][-1] == wc._QUOTA_REPLY


# ---------- 寒暄 / 帮助 / 过期：均不调 agent、不计次 ----------

def test_greeting_is_free(env):
    calls: list = []
    mgr = _mgr(calls=calls)
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("你好")]))
    assert calls == []
    assert env["sent"][-1] == wc._GREETING_REPLY
    assert metrics_store.get_daily("q:wxqa:alice") == 0


def test_help_is_free(env):
    calls: list = []
    mgr = _mgr(calls=calls)
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("帮助")]))
    assert calls == []
    assert env["sent"][-1] == wc._HELP_REPLY
    assert metrics_store.get_daily("q:wxqa:alice") == 0


def test_expired_member_blocked(env):
    env["mp"].setattr(auth, "membership_of_username", lambda u: {"tier": "trial"})
    calls: list = []
    mgr = _mgr(calls=calls)
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("茅台怎么样")]))
    assert calls == []                                       # 过期会员不触发 agent
    assert env["sent"][-1] == wc._EXPIRED_REPLY
    assert metrics_store.get_daily("q:wxqa:alice") == 0


# ---------- 出口合规中性化 ----------

def test_answer_goes_through_compliance(env):
    env["mp"].setattr(compliance, "neutralize_text", lambda s: "NZ:" + s)
    mgr = _mgr(answer="原始答案")
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("某新问题")]))
    assert env["sent"][-1] == "NZ:原始答案"


# ---------- make_agent_fn 传放宽后的超时（个股多轮取数不被 30s 默认超时掐死） ----------

def test_make_agent_fn_passes_long_timeout():
    captured: dict = {}

    class FakeLLM:
        async def run_tool_agent(self, *, question, context_hint, timeout_seconds, **kw):
            captured["timeout"] = timeout_seconds
            return {"answer": "ok"}

    out = asyncio.run(wc.make_agent_fn(FakeLLM())("某股怎么样", ""))
    assert out == "ok"
    assert captured["timeout"] == wc._QA_AGENT_TIMEOUT and wc._QA_AGENT_TIMEOUT >= 60


# ---------- 微信 orchestrator 适配器：强制研究路径 + 放宽超时 + 取 content ----------

def test_weixin_orchestrator_agent_fn(monkeypatch):
    from deepfocus_api import main

    class _Resp:  # 仿 OrchestratorChatResponse，只需 content
        content = "  路由后的答案  "

    captured: dict = {}

    async def fake_route(request, _ifind, tool_timeout=30.0, force_research=False):
        captured.update(msg=request.message, ifind=_ifind, timeout=tool_timeout, force=force_research)
        return _Resp()

    monkeypatch.setattr(main, "_route_orchestrator_chat", fake_route)
    out = asyncio.run(main.make_weixin_orchestrator_agent_fn()("茅台怎么样", ""))
    assert out == "路由后的答案"                       # 取 content 并 strip
    assert captured["force"] is True                   # 微信强制研究路径(避免漏判意图)
    assert captured["timeout"] >= 60                   # tool-agent 超时已放宽
    assert captured["ifind"] is False
    assert captured["msg"] == "茅台怎么样"


# ---------- 大盘/复盘高频问：今日复盘直出(0 token)，隔夜则落实时 agent ----------

def test_market_overview_direct_render(env, monkeypatch):
    from deepfocus_api import ashare_review
    monkeypatch.setattr(ashare_review, "cn_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(ashare_review, "latest_review", lambda: {
        "date": "2026-06-22", "session_label": "收盘复盘",
        "narrative": {"one_liner": "放量长阳", "market": "沪指+1.8%", "sectors": "AI领涨", "funds": "主力净流入"},
        "our_edge": [{"title": "我们提前覆盖AI电力"}],
    })
    calls: list = []
    mgr = _mgr(answer="不该被调用", calls=calls)
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("今天大盘怎么样")]))
    assert calls == []                                       # 没调 agent → 0 token
    assert metrics_store.get_daily("q:wxqa:alice") == 0      # 不计现算配额
    sent = env["sent"][-1]
    assert "收盘复盘" in sent and "放量长阳" in sent and "我们提前覆盖AI电力" in sent


def test_market_overview_falls_through_when_stale(env, monkeypatch):
    from deepfocus_api import ashare_review
    monkeypatch.setattr(ashare_review, "cn_today_str", lambda: "2026-06-22")
    monkeypatch.setattr(ashare_review, "latest_review", lambda: {"date": "2026-06-20", "narrative": {}})
    calls: list = []
    mgr = _mgr(answer="实时agent答案", calls=calls)
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("今天大盘怎么样")]))
    assert calls == ["今天大盘怎么样"]                        # 隔夜复盘 → 落到实时 agent
    assert env["sent"][-1] == "实时agent答案"


def test_is_market_overview_q_heuristic():
    assert wc._is_market_overview_q("今天大盘怎么样")
    assert wc._is_market_overview_q("复盘")
    assert not wc._is_market_overview_q("贵州茅台今天怎么样")        # 个股,不直出
    assert not wc._is_market_overview_q("600519 大盘里表现如何")    # 含代码
    assert not wc._is_market_overview_q("分析今天大盘对消费和半导体板块的影响到底多大")  # 太长


# ---------- 出口护栏：答案里的数据源/工具名被剥掉再发出 ----------

def test_answer_output_scrubs_internal_leaks(env):
    mgr = _mgr(answer="据 iFinD 实时数据，我调用 get_market_quote 得出 PE 18.8。")
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("某出口护栏测试问题")]))
    sent = env["sent"][-1]
    assert "iFinD" not in sent and "get_market_quote" not in sent
    assert "18.8" in sent  # 数据本身保留


# ---------- agent 空答：回兜底、不写缓存（但已计次，保护成本） ----------

def test_empty_answer_falls_back_and_not_cached(env):
    mgr = _mgr(answer="")
    asyncio.run(mgr._handle_batch("bot-alice", [_msg("冷门问题")]))
    assert env["sent"][-1] == wc._FALLBACK_REPLY
    assert data_store.latest("wx_qa", wc._qa_fingerprint("冷门问题")) is None  # 空答不缓存
    assert metrics_store.get_daily("q:wxqa:alice") == 1                       # 现算已计次
