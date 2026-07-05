import asyncio

from deepfocus_api import auth
from deepfocus_api import data_store
from deepfocus_api import metrics_store
from deepfocus_api import weixin_bind as bind
from deepfocus_api import weixin_ilink as ilink
from deepfocus_api import weixin_channel as ch


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bind, "DB_PATH", tmp_path / "bind.sqlite3")
    bind.init_weixin_bind_db()
    # 问答会走答案缓存(data_store)+每日配额(metrics_store)，一并隔离到临时库，避免污染真实库
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    monkeypatch.setattr(metrics_store, "DB_PATH", tmp_path / "ms.sqlite3")
    data_store.init_data_store()
    metrics_store.init_db()


def _member(monkeypatch, tier="premium"):
    """把发问用户置为在效会员（AI 问答会员专享，_member_can_push 仅放行 premium/lifetime）。"""
    monkeypatch.setattr(auth, "membership_of_username", lambda u: ({"tier": tier} if u else None))


def test_handle_batch_replies_with_agent_answer(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch)  # 在效会员可问（已去白名单）
    bind.upsert_binding("u1", "botX@im.bot", "tok", "https://ilinkai.weixin.qq.com", "wxU@im.wechat", username="qauser")

    sent = {}

    async def fake_send(token, base_url, to_user_id, context_token, text, uin=None):
        sent.update(token=token, to=to_user_id, ctx=context_token, text=text)
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)

    agent_calls = []

    async def agent(q, h):
        agent_calls.append((q, h))
        return f"答:{q}"

    mgr = ch.WeixinChannelManager(agent_fn=agent, context_hint_fn=lambda b: f"用户{b['deepfocus_user_id']}")
    msgs = [{
        "message_type": 1, "from_user_id": "wxU@im.wechat", "context_token": "CTX1",
        "item_list": [{"type": 1, "text_item": {"text": "茅台怎么样"}}],
    }]
    asyncio.run(mgr._handle_batch("botX@im.bot", msgs))

    assert agent_calls == [("茅台怎么样", "用户u1")]
    # 答案后会追加确定性追问引导 + AI 生成显式标识（《标识办法》硬要求）→ 断言前缀与标识
    assert sent["to"] == "wxU@im.wechat" and sent["ctx"] == "CTX1" and sent["text"].startswith("答:茅台怎么样")
    assert "AI 生成" in sent["text"]
    assert bind.get_by_bot("botX@im.bot")["context_token"] == "CTX1"  # 缓存已刷新


def test_handle_batch_skips_bot_and_uses_fallback(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch)
    bind.upsert_binding("u1", "botX@im.bot", "tok", "base", "wxU", username="qauser")
    sent = {}

    async def fake_send(token, base_url, to_user_id, context_token, text, uin=None):
        sent.update(called=True, text=text)
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)

    async def agent(q, h):
        return None  # 无答案 → 回退话术

    mgr = ch.WeixinChannelManager(agent_fn=agent)
    msgs = [
        {"message_type": 2, "from_user_id": "self", "context_token": "C", "item_list": [{"type": 1, "text_item": {"text": "我是bot"}}]},
        {"message_type": 1, "from_user_id": "wxU", "context_token": "C2", "item_list": [{"type": 1, "text_item": {"text": "茅台财报细节"}}]},
    ]
    asyncio.run(mgr._handle_batch("botX@im.bot", msgs))
    assert sent.get("called") and "投研助手" in sent["text"]  # agent 无答案 → 新版引导式兜底


def test_handle_batch_greeting_skips_agent(tmp_path, monkeypatch):
    """纯打招呼：不调 agent(不耗 token)，回友好引导。"""
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch)
    bind.upsert_binding("u1", "botX@im.bot", "tok", "base", "wxU", username="qauser")
    sent = {}

    async def fake_send(token, base_url, to_user_id, context_token, text, uin=None):
        sent.update(text=text)
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)
    calls = []

    async def agent(q, h):
        calls.append(q)
        return "不该被调用"

    mgr = ch.WeixinChannelManager(agent_fn=agent)
    msgs = [{"message_type": 1, "from_user_id": "wxU", "context_token": "C2", "item_list": [{"type": 1, "text_item": {"text": "你好"}}]}]
    asyncio.run(mgr._handle_batch("botX@im.bot", msgs))
    assert calls == []  # 打招呼不调 agent
    assert "投研助手" in sent["text"]


def test_handle_batch_expired_member_tasting_then_quota(tmp_path, monkeypatch):
    """非会员试吃：每天 _WX_FREE_QA 次现算问答（微信是最好的转化面，一刀切拒绝=锁死在墙内）；
    超额后不再触发 agent，回复带自助购买链接的升级提示。"""
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch, tier="trial")  # 已降级/过期 → 非 premium/lifetime
    bind.upsert_binding("u2", "botY@im.bot", "tok", "base", "wxV", username="somemember")
    sent = {}

    async def fake_send(token, base_url, to_user_id, context_token, text, uin=None):
        sent.update(called=True, text=text)
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)

    agent_calls = []

    async def agent(q, h):
        agent_calls.append(q)
        return "答"

    mgr = ch.WeixinChannelManager(agent_fn=agent)
    msgs = [{"message_type": 1, "from_user_id": "wxV", "context_token": "C3", "item_list": [{"type": 1, "text_item": {"text": "茅台怎么样"}}]}]
    asyncio.run(mgr._handle_batch("botY@im.bot", msgs))
    assert agent_calls == ["茅台怎么样"]  # 试吃第一问触发 agent 正常回答
    assert sent.get("called") and sent["text"].startswith("答")
    # 把当日试吃额度打满 → 不再触发 agent，回自助购买引导
    qkey = "q:wxfree:somemember"
    while metrics_store.get_daily(qkey) < ch._WX_FREE_QA:
        metrics_store.incr(qkey)
    agent_calls.clear()
    msgs2 = [{"message_type": 1, "from_user_id": "wxV", "context_token": "C3", "item_list": [{"type": 1, "text_item": {"text": "宁德时代怎么样"}}]}]
    asyncio.run(mgr._handle_batch("botY@im.bot", msgs2))
    assert agent_calls == []  # 超额不触发 agent → 不耗 token
    assert sent["text"] == ch._NONMEMBER_QUOTA_REPLY
    assert "daocaijing" in ch._NONMEMBER_QUOTA_REPLY  # 超额提示带自助购买链接


def test_member_can_push_gates_on_membership(tmp_path, monkeypatch):
    """过期会员停推：_member_can_push 仅对 premium/lifetime 放行，trial/None/无用户名拒绝。"""
    from deepfocus_api import auth

    monkeypatch.setattr(auth, "membership_of_username", lambda u: {"premium": {"tier": "premium"}, "life": {"tier": "lifetime"}, "trial": {"tier": "trial"}}.get(u))

    assert ch._member_can_push({"username": "premium"}) is True
    assert ch._member_can_push({"username": "life"}) is True
    assert ch._member_can_push({"username": "trial"}) is False    # 过期/降级 → 停推
    assert ch._member_can_push({"username": "unknown"}) is False   # 查无会员
    assert ch._member_can_push({"username": ""}) is False          # 无用户名
    assert ch._member_can_push({}) is False


def test_dispatch_push_reuses_client_and_sends_all(monkeypatch):
    """批量错峰发送：整批复用同一个 client，每条 job 都发出。"""
    monkeypatch.setattr(ch, "_PUSH_GAP_MAX", 0)  # 测试里不真等
    monkeypatch.setattr(ch, "_PUSH_GAP_MIN", 0)

    class DummyClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    shared = DummyClient()
    monkeypatch.setattr(ilink, "push_client", lambda *a, **k: shared)

    calls = []

    async def fake_send(token, base_url, to, ctx, text, uin=None, client=None):
        calls.append((to, text, client is shared))  # 复用同一连接
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)

    mgr = ch.WeixinChannelManager(agent_fn=lambda q, h: None)
    mgr._running = True
    jobs = [
        ({"token": "t", "base_url": "b", "ilink_bot_id": "x1"}, "wxA", "CA", "同一条全量文本"),
        ({"token": "t", "base_url": "b", "ilink_bot_id": "x2"}, "wxB", "CB", "同一条全量文本"),
    ]
    asyncio.run(mgr._dispatch_push(jobs))
    assert len(calls) == 2
    assert all(reused for (_, _, reused) in calls)        # 整批共用一个 client
    assert {c[0] for c in calls} == {"wxA", "wxB"}


def test_quasi_push_delivers_to_active_with_ctx(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    bind.upsert_binding("u1", "botA", "tA", "base", "wxA")
    bind.upsert_binding("u2", "botB", "tB", "base", "wxB")
    bind.update_context_token("botA", "CTXA")  # 只有 A 有 context_token

    calls = []

    async def fake_send(token, base_url, to, ctx, text, uin=None, client=None):
        calls.append((to, ctx, text))
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)
    mgr = ch.WeixinChannelManager(agent_fn=lambda q, h: None)
    res = asyncio.run(mgr.quasi_push("今日快讯：xxx"))
    assert res == {"delivered": 1, "skipped": 1, "failed": 0}  # B 无 ctx 被跳过
    assert calls == [("wxA", "CTXA", "今日快讯：xxx")]


def test_quasi_push_counts_failures(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    bind.upsert_binding("u1", "botA", "tA", "base", "wxA")
    bind.update_context_token("botA", "CTXA")

    async def boom(token, base_url, to, ctx, text, uin=None, client=None):
        raise RuntimeError("token expired")

    monkeypatch.setattr(ilink, "send_text", boom)
    monkeypatch.setattr(ch, "_SEND_RETRY_BACKOFF", 0)  # 重试不等待，测试快
    mgr = ch.WeixinChannelManager(agent_fn=lambda q, h: None)
    res = asyncio.run(mgr.quasi_push("x"))
    assert res["failed"] == 1 and res["delivered"] == 0
