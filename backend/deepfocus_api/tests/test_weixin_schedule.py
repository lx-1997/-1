from deepfocus_api import auth
from deepfocus_api import weixin_schedule as wsched
from deepfocus_api import weixin_channel as ch


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wsched, "DB_PATH", tmp_path / "sched.sqlite3")
    wsched.init_schedule_db()


# ---------------- store: CRUD + due + dedup ----------------

def test_create_list_update_delete(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    rec = wsched.create_schedule("broadcast", "news", 8, 30, title="早报", day_mode="trading")
    assert rec["id"] > 0 and rec["kind"] == "broadcast" and rec["content_type"] == "news"
    assert rec["hour"] == 8 and rec["minute"] == 30 and rec["day_mode"] == "trading" and rec["enabled"] is True

    got = wsched.list_schedules(kind="broadcast")
    assert len(got) == 1

    up = wsched.update_schedule(rec["id"], hour=9, minute=5, enabled=False)
    assert up["hour"] == 9 and up["minute"] == 5 and up["enabled"] is False

    assert wsched.delete_schedule(rec["id"]) is True
    assert wsched.list_schedules() == []


def test_invalid_values_fall_back_to_safe_defaults(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    # 非法 kind→broadcast；非法 content_type→该 kind 首个合法值；非法 day_mode→daily；越界时间归一
    rec = wsched.create_schedule("garbage", "watchlist_quote", 99, 77, day_mode="weekly")
    assert rec["kind"] == "broadcast" and rec["content_type"] == "text"
    assert rec["day_mode"] == "daily" and rec["hour"] == 23 and rec["minute"] == 59


def test_due_window_and_dedup(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    s = wsched.create_schedule("broadcast", "text", 8, 30, content="hi")

    # 早于目标：不 due
    assert wsched.due_schedules(8, 29, "2026-07-06") == []
    # 命中：due
    assert [x["id"] for x in wsched.due_schedules(8, 30, "2026-07-06")] == [s["id"]]
    # 补发窗口内(默认15min)：仍 due（服务重启补发）
    assert [x["id"] for x in wsched.due_schedules(8, 44, "2026-07-06")] == [s["id"]]
    # 超窗口：不 due
    assert wsched.due_schedules(8, 46, "2026-07-06") == []

    # 标记今日已发 → 同日窗口内不再 due（去重）
    wsched.mark_fired(s["id"], "2026-07-06")
    assert wsched.due_schedules(8, 31, "2026-07-06") == []
    # 次日重置 → 又 due
    assert [x["id"] for x in wsched.due_schedules(8, 30, "2026-07-07")] == [s["id"]]


def test_disabled_never_due(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    s = wsched.create_schedule("broadcast", "text", 8, 30, content="hi", enabled=False)
    assert wsched.due_schedules(8, 30, "2026-07-06") == []


def test_set_personal_upserts_single_row(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    a = wsched.set_personal("u1", "watchlist_quote", 8, 40)
    assert a["kind"] == "personal" and a["day_mode"] == "trading"
    # 再订阅：更新同一行（换内容+时间），不新增
    b = wsched.set_personal("u1", "watchlist_news", 9, 0)
    assert b["id"] == a["id"] and b["content_type"] == "watchlist_news" and b["hour"] == 9
    assert len(wsched.list_personal("u1")) == 1

    # disable：enabled=0 但保留记录
    assert wsched.disable_personal("u1") == 1
    assert wsched.list_personal("u1")[0]["enabled"] is False
    # 关闭后不再 due
    assert wsched.due_schedules(9, 0, "2026-07-06") == []


def test_set_personal_collapses_pre_existing_duplicates(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    # 直接建两条历史个性化（模拟旧数据）→ set_personal 应收敛到 1 条
    wsched.create_schedule("personal", "watchlist_quote", 8, 0, owner_user_id="u2")
    wsched.create_schedule("personal", "watchlist_news", 9, 0, owner_user_id="u2")
    assert len(wsched.list_personal("u2")) == 2
    wsched.set_personal("u2", "watchlist_quote", 8, 40)
    assert len(wsched.list_personal("u2")) == 1


# ---------------- NL 口令解析 ----------------

def test_parse_bj_time():
    assert ch._parse_bj_time("每天8点推自选") == (8, 0)
    assert ch._parse_bj_time("每天8:30推自选") == (8, 30)
    assert ch._parse_bj_time("每天8点半推自选") == (8, 30)
    assert ch._parse_bj_time("下午3点推自选") == (15, 0)
    assert ch._parse_bj_time("晚上8点推自选") == (20, 0)
    assert ch._parse_bj_time("订阅自选行情") is None  # 无时间
    # 点后裸数字不当分钟（否则「8点8只自选」→08:08）
    assert ch._parse_bj_time("每天8点推我的8只自选") == (8, 0)
    assert ch._parse_bj_time("每天8点30分推自选") == (8, 30)
    assert ch._parse_bj_time("早上7点3刻推自选") == (7, 45)   # N刻=N×15
    assert ch._parse_bj_time("早上7点三刻推自选") == (7, 45)
    assert ch._parse_bj_time("凌晨12点推自选") == (0, 0)     # 夜间12点=0点


def _member(monkeypatch, tier="premium"):
    monkeypatch.setattr(auth, "membership_of_username", lambda u: ({"tier": tier} if u else None))


def test_schedule_command_enable_disable_status(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch)  # 会员
    b = {"deepfocus_user_id": "u9", "username": "vip"}

    # 非定时口令 → None（交回主流程）
    assert ch._schedule_command_reply("茅台怎么样", b) is None

    # 开启（默认 08:40、行情、交易日）
    r = ch._schedule_command_reply("每天推我的自选", b)
    assert r and "每个交易日 08:40" in r
    rows = wsched.list_personal("u9")
    assert len(rows) == 1 and rows[0]["content_type"] == "watchlist_quote" and rows[0]["hour"] == 8

    # 换时间 + 换成快讯
    r2 = ch._schedule_command_reply("每天9点推自选快讯", b)
    assert "09:00" in r2
    rows = wsched.list_personal("u9")
    assert len(rows) == 1 and rows[0]["content_type"] == "watchlist_news" and rows[0]["hour"] == 9

    # 查看状态
    st = ch._schedule_command_reply("我的定时", b)
    assert "每个交易日 09:00" in st

    # 关闭
    off = ch._schedule_command_reply("关闭定时", b)
    assert "已关闭" in off
    assert wsched.list_personal("u9")[0]["enabled"] is False


def test_schedule_command_members_only(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch, tier=None)  # 非会员
    b = {"deepfocus_user_id": "u10", "username": "free"}
    r = ch._schedule_command_reply("每天推我的自选", b)
    assert r and "会员专属" in r
    assert wsched.list_personal("u10") == []  # 未创建


def test_schedule_command_ignores_questions_and_honors_send_verb(tmp_path, monkeypatch):
    """疑问句(问功能)不建计划；「发」动词的自然订阅也能触发(不只限「推」)。"""
    _use_temp_db(tmp_path, monkeypatch)
    _member(monkeypatch)
    b = {"deepfocus_user_id": "u11", "username": "vip"}

    # 疑问句 → None（交回 agent），不建计划
    assert ch._schedule_command_reply("怎么订阅推送", b) is None
    assert ch._schedule_command_reply("你们每天资讯几点推送", b) is None
    assert ch._schedule_command_reply("定时推送是什么", b) is None
    assert wsched.list_personal("u11") == []

    # 「发」动词的自然订阅 → 建计划（旧逻辑只认「推」会漏）
    r = ch._schedule_command_reply("每天早上把我的自选行情发给我", b)
    assert r and "已设定" in r
    assert len(wsched.list_personal("u11")) == 1


def test_broadcast_fire_marks_only_on_delivery(tmp_path, monkeypatch):
    """群发：送达≥1 或无内容 → done=True（了结）；全员冷 delivered=0 → done=False（窗口内重试）。"""
    import asyncio
    from deepfocus_api import main as m

    class Mgr:
        def __init__(self, delivered):
            self.delivered = delivered
            self.calls = 0

        async def quasi_push(self, text, gap_seconds=0.0):
            self.calls += 1
            return {"delivered": self.delivered, "skipped": 2, "failed": 0}

    s = {"id": 1, "kind": "broadcast", "content_type": "text", "content": "今日公告：例行提示"}

    mgr0 = Mgr(0)
    assert asyncio.run(m._fire_wechat_schedule(mgr0, s)) is False and mgr0.calls == 1  # 全冷 → 不了结
    assert asyncio.run(m._fire_wechat_schedule(Mgr(3), s)) is True                     # 送达 → 了结
    s_empty = {"id": 2, "kind": "broadcast", "content_type": "text", "content": ""}
    assert asyncio.run(m._fire_wechat_schedule(Mgr(0), s_empty)) is True               # 无内容 → 了结(不重试)


# ---------------- 个性化定投递：push_to_user ----------------

def test_push_to_user_sends_to_own_binding(tmp_path, monkeypatch):
    import asyncio
    from deepfocus_api import weixin_bind as bind
    from deepfocus_api import weixin_ilink as ilink

    monkeypatch.setattr(bind, "DB_PATH", tmp_path / "bind.sqlite3")
    bind.init_weixin_bind_db()
    bind.upsert_binding("u1", "botX@im.bot", "tok", "https://ilinkai.weixin.qq.com", "wxU@im.wechat", username="vip")
    bind.update_context_token("botX@im.bot", "CTX1")

    sent = {}

    async def fake_send(token, base_url, to_user_id, context_token, text, uin=None, client=None):
        sent.update(to=to_user_id, ctx=context_token, text=text)
        return {"ret": 0}

    monkeypatch.setattr(ilink, "send_text", fake_send)

    async def _noop(_q, _h):
        return None

    mgr = ch.WeixinChannelManager(agent_fn=_noop)
    ok = asyncio.run(mgr.push_to_user("u1", "📊 你的自选行情：贵州茅台 1680 +1.20%"))
    assert ok is True and sent["to"] == "wxU@im.wechat" and sent["ctx"] == "CTX1"

    # token 冷（无 ctx）→ 跳过、返回 False、不发送
    bind.clear_context_token("botX@im.bot")
    sent.clear()
    ok2 = asyncio.run(mgr.push_to_user("u1", "x"))
    assert ok2 is False and sent == {}

    # 未绑定用户 → False
    assert asyncio.run(mgr.push_to_user("nobody", "x")) is False


def test_personal_fire_gated_by_membership(tmp_path, monkeypatch):
    """个性化定时在【触发时】校验会员：非会员不发；会员按渲染内容发。防会员过期后仍持续推。"""
    import asyncio
    from deepfocus_api import main as m
    from deepfocus_api import weixin_bind as bind
    from deepfocus_api import auth as _auth
    from deepfocus_api import user_prefs

    monkeypatch.setattr(bind, "DB_PATH", tmp_path / "bind.sqlite3")
    bind.init_weixin_bind_db()
    bind.upsert_binding("u1", "botX@im.bot", "tok", "https://x", "wxU@im.wechat", username="vip")
    bind.update_context_token("botX@im.bot", "CTX1")

    calls = []

    class FakeMgr:
        async def push_to_user(self, uid, text):
            calls.append((uid, text))
            return True

        async def quasi_push(self, text, gap_seconds=0.0):
            calls.append(("bcast", text))
            return {"delivered": 1}

    mgr = FakeMgr()
    s = {"id": 1, "kind": "personal", "owner_user_id": "u1", "content_type": "watchlist_quote"}

    # 非会员（过期）→ 不发（触发时断流）
    monkeypatch.setattr(_auth, "membership_of_username", lambda u: None)
    asyncio.run(m._fire_wechat_schedule(mgr, s))
    assert calls == []

    # 会员 + 有自选 + 假行情 → 发，且内容含自选名
    monkeypatch.setattr(_auth, "membership_of_username", lambda u: {"tier": "premium"})
    monkeypatch.setattr(user_prefs, "get_watchlist",
                        lambda uid: {"symbols": ["600519"], "names": {"600519": "贵州茅台"}})

    class _Q:
        name = "贵州茅台"
        price = 1680.0
        change_percent = 1.23

    class _Resp:
        quotes = [_Q()]

    async def fake_quotes(syms, **kw):
        return _Resp()

    monkeypatch.setattr(m, "fetch_market_quotes", fake_quotes)
    asyncio.run(m._fire_wechat_schedule(mgr, s))
    assert len(calls) == 1 and calls[0][0] == "u1" and "贵州茅台" in calls[0][1]
