"""深度研判（多智能体）回归守卫。

重点：①iFinD 灰度泄漏隔离（DEEP_NO_PERSIST 抑制一切落库）②门控（起任务+轮询都要白名单、
owner 隔离）③合规化（中性化/方向枚举/强制免责）④降级矩阵（取证失败不崩）⑤TTL/限流。
"""
from __future__ import annotations

import asyncio

import pytest

from deepfocus_api import auth, data_store, deep_research as dr, storage


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _run(coro_factory):
    """在单一事件循环里跑场景，并先清空内存任务表（无锁设计，跨用例不污染）。"""
    async def wrapper():
        dr._DEEP_TASKS.clear()
        return await coro_factory()
    return asyncio.run(wrapper())


class FakeLLM:
    """假 LLM：run_tool_agent（取证）+ complete_json（角色推理）。可配置失败/触发落库。"""

    def __init__(self, *, route_a=True, route_b=True, leak_write=False, judge_extra=None):
        self.route_a = route_a          # 基本面取证是否成功
        self.route_b = route_b          # 观点取证是否成功
        self.leak_write = leak_write    # 取证时是否尝试写 verdict_tool（模拟泄漏面）
        self.judge_extra = judge_extra or {}
        self.tool_calls = 0
        self.json_calls = 0

    async def run_tool_agent(self, *, question, context_hint="", max_rounds=4, timeout_seconds=30, emit=None, ifind_user=False):
        self.tool_calls += 1
        if self.leak_write:
            # 模拟取证 agent 误调 get_stock_verdict → 公开 verdict_tool 写。应被 DEEP_NO_PERSIST 拦。
            data_store.record("verdict_tool", "TEST", {"verdict": "leak"})
        is_fundamental = "基本面取证分析师" in question
        if is_fundamental and not self.route_a:
            return None
        if (not is_fundamental) and not self.route_b:
            return None
        ans = "现价10.0元 PE15 市值200亿 主力净流入" if is_fundamental else "券商一致预期上调；我们快讯提前覆盖"
        return {"answer": ans, "tool_trace": [{"tool": "get_market_quote", "ok": True, "summary": "ok"}], "rounds": 1}

    async def complete_json(self, prompt, max_tokens=2200, timeout_seconds=35, **kw):
        self.json_calls += 1
        # ⚠️ 裁判/辩论/风控的 prompt 都内嵌了多空案的 JSON，必须先判这些独有指令短语，
        # 再判多空案，否则会被内嵌的 "多头"/"空头" 抢先匹配。
        if "投委会主席" in prompt:
            pass  # 落到下方裁判分支
        elif "进入辩论环节" in prompt:
            return {"rebuttals": [{"targets_bull_point": "PE偏低", "verdict": "削弱", "reasoning": "行业整体低"}], "bear_concessions": [], "strongest_bull_point": "PE偏低", "strongest_bear_point": "资金流出", "net_lean": "胶着"}
        elif "独立风控官" in prompt:
            return {"key_risks": [{"risk": "板块轮动", "severity": "中", "evidence_ref": "资金"}], "data_caveats": [], "invalidation_conditions": [], "overall_risk_level": "中"}
        elif "扮演看多分析师" in prompt:
            return {"stance": "多头", "thesis": "基本面稳", "key_args": [{"point": "PE偏低", "evidence_ref": "PE15"}], "catalysts": [], "confidence": 0.6}
        elif "扮演看空分析师" in prompt:
            return {"stance": "空头", "thesis": "增长存疑", "key_args": [{"point": "资金流出", "evidence_ref": "主力"}], "catalysts": [], "confidence": 0.4}
        else:
            return {}
        if "投委会主席" in prompt:
            base = {"direction": "中性偏多", "confidence": 0.55, "thesis": "估值合理，关注资金面",
                    "core_evidence": [{"point": "PE15偏低", "evidence_ref": "PE15"}],
                    "key_risks": [{"risk": "板块轮动", "severity": "中"}],
                    "watch_levels": {"support": "9.5", "resistance": "11.0", "note": "若跌破9.5需警惕"},
                    "debate_synthesis": "多空胶着，略偏多"}
            base.update(self.judge_extra)
            return base
        return {}


# --------------------------------------------------------------------------- #
# ① 泄漏隔离（最高优先级）
# --------------------------------------------------------------------------- #
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    data_store.init_data_store()
    # 每个用例确保 ContextVar 复位（避免串味）
    tok = data_store.DEEP_NO_PERSIST.set(False)
    yield data_store
    data_store.DEEP_NO_PERSIST.reset(tok)


def test_deep_no_persist_blocks_record(store):
    """DEEP_NO_PERSIST=True 时 record 静默跳过；复位后正常写。"""
    tok = store.DEEP_NO_PERSIST.set(True)
    store.record("verdict", "AAPL", {"x": 1})
    assert store.latest("verdict", "AAPL") is None  # 被抑制
    store.DEEP_NO_PERSIST.reset(tok)
    store.record("verdict", "AAPL", {"x": 1})
    assert store.latest("verdict", "AAPL") == {"x": 1}  # 复位后正常


def test_run_deep_research_suppresses_all_persistence(store, monkeypatch):
    """⭐端到端：跑完整管线，取证阶段即便触发 verdict_tool 写也被抑制；产出有效研判。"""
    monkeypatch.setattr(dr, "_make_llm", lambda: FakeLLM(leak_write=True))

    def scenario():
        async def go():
            task = await dr.create_task("lx199710", True, "600519", "贵州茅台", "CN")
            await dr.run_deep_research(task.task_id, "600519", "贵州茅台", "CN", ifind_user=True)
            return await dr.get_task(task.task_id)
        return go()

    task = _run(lambda: scenario())
    assert task.status == "done"
    assert task.result and task.result["direction"] == "中性偏多"
    # ⭐ 取证阶段尝试写的 verdict_tool 被 DEEP_NO_PERSIST 拦下 → 公开历史里查不到
    assert store.latest("verdict_tool", "TEST") is None
    # 运行结束后 ContextVar 已复位
    assert store.DEEP_NO_PERSIST.get() is False
    # 复位后再写能成功（证明拦截是上下文级、非永久关闭）
    store.record("verdict_tool", "TEST", {"ok": 1})
    assert store.latest("verdict_tool", "TEST") == {"ok": 1}


# --------------------------------------------------------------------------- #
# ② 合规化后处理
# --------------------------------------------------------------------------- #
def test_finalize_neutralizes_buy_sell_words():
    raw = {"direction": "看多", "thesis": "强烈建议买入，目标价翻倍", "confidence": 0.9}
    out = dr._finalize_verdict(raw, ifind_used=False, gaps=[], degraded=[])
    # 荐股/承诺词仍被中性化（"建议买入"→"偏多关注"）
    assert "建议买入" not in out["thesis"] and "买入" not in out["thesis"]
    # 但客观财经描述词（目标价/翻倍）保留——无条件替换它们会篡改券商/财报事实+造残句（审计 rank12）
    assert "目标价" in out["thesis"] and "翻倍" in out["thesis"]


def test_finalize_direction_enum_validation():
    out = dr._finalize_verdict({"direction": "强烈推荐", "confidence": 0.5}, ifind_used=False, gaps=[], degraded=[])
    assert out["direction"] == "中性"


def test_finalize_forces_disclaimer_and_ifind_flag():
    out = dr._finalize_verdict({"direction": "看空"}, ifind_used=True, gaps=["x"], degraded=[])
    assert out["disclaimer"] and "不构成投资建议" in out["disclaimer"]
    assert out["data_quality"]["ifind_used"] is True
    assert out["data_quality"]["gaps"] == ["x"]


def test_finalize_clamps_confidence():
    out = dr._finalize_verdict({"direction": "中性", "confidence": 5}, ifind_used=False, gaps=[], degraded=[])
    assert out["confidence"] == 1.0
    out2 = dr._finalize_verdict({"direction": "中性", "confidence": "bad"}, ifind_used=False, gaps=[], degraded=[])
    assert out2["confidence"] is None


# --------------------------------------------------------------------------- #
# ③ to_public 剔 owner
# --------------------------------------------------------------------------- #
def test_to_public_omits_owner_keeps_ifind_used():
    def go():
        return dr.create_task("lx199710", True, "600519", "贵州茅台", "CN")
    task = _run(lambda: go())
    pub = dr.to_public(task)
    assert "owner" not in pub
    assert pub["ifind_used"] is True
    assert [s["key"] for s in pub["stages"]] == ["evidence", "bull", "bear", "risk", "judge"]


# --------------------------------------------------------------------------- #
# ④ 降级矩阵
# --------------------------------------------------------------------------- #
def test_both_evidence_none_errors_cleanly(monkeypatch):
    monkeypatch.setattr(dr, "_make_llm", lambda: FakeLLM(route_a=False, route_b=False))

    def go():
        async def inner():
            task = await dr.create_task("lx199710", False, "600519", "贵州茅台", "CN")
            await dr.run_deep_research(task.task_id, "600519", "贵州茅台", "CN", ifind_user=False)
            return await dr.get_task(task.task_id)
        return inner()

    task = _run(lambda: go())
    assert task.status == "error"
    assert task.result is None
    assert "取证" in (task.error or "")


def test_one_evidence_none_degrades_still_done(monkeypatch):
    monkeypatch.setattr(dr, "_make_llm", lambda: FakeLLM(route_a=True, route_b=False))

    def go():
        async def inner():
            task = await dr.create_task("lx199710", False, "600519", "贵州茅台", "CN")
            await dr.run_deep_research(task.task_id, "600519", "贵州茅台", "CN", ifind_user=False)
            return await dr.get_task(task.task_id)
        return inner()

    task = _run(lambda: go())
    assert task.status == "done"
    assert task.result is not None
    assert any("取证不可用" in g for g in (task.result["data_quality"].get("gaps") or []))


# --------------------------------------------------------------------------- #
# ⑤ TTL / 限流
# --------------------------------------------------------------------------- #
def test_sweep_evicts_expired_and_caps():
    def go():
        async def inner():
            t1 = await dr.create_task("u1", False, "AAA", "a")
            t1.expires_ts = dr._now() - 1  # 强制过期
            t2 = await dr.create_task("u2", False, "BBB", "b")  # 触发 _sweep
            return t1, t2
        return inner()

    t1, t2 = _run(lambda: go())
    assert t1.task_id not in dr._DEEP_TASKS  # 过期被清
    assert t2.task_id in dr._DEEP_TASKS


def test_owner_running_and_in_flight():
    def go():
        async def inner():
            t = await dr.create_task("lx199710", False, "AAA", "a")
            running = await dr.owner_running("lx199710")
            count = await dr.in_flight_count()
            none_other = await dr.owner_running("someone")
            return t.task_id, running, count, none_other
        return inner()

    tid, running, count, none_other = _run(lambda: go())
    assert running == tid
    assert count == 1
    assert none_other is None


# --------------------------------------------------------------------------- #
# ⑥ 端点门控（TestClient + 真 JWT）
# --------------------------------------------------------------------------- #
@pytest.fixture
def gated_client(tmp_path, monkeypatch):
    # 前面用 asyncio.run 的单元测试会把线程事件循环置 None（污染 TestClient 的 get_event_loop）→ 复原一个新 loop。
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path/'auth.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("DEEPFOCUS_IFIND_ALLOWED_USERS", "lx199710,lx_other")
    # 背景任务不打真 LLM
    monkeypatch.setattr(dr, "_make_llm", lambda: FakeLLM())
    dr._DEEP_TASKS.clear()
    storage.reset_engine_for_tests()
    auth.init_auth()
    from fastapi.testclient import TestClient
    from deepfocus_api.main import app
    client = TestClient(app)
    u1 = auth.create_user("u1@t.local", "lx199710", "pw12345678")
    u2 = auth.create_user("u2@t.local", "lx_other", "pw12345678")
    u3 = auth.create_user("u3@t.local", "outsider", "pw12345678")
    tokens = {
        "lx199710": auth.create_access_token(u1),
        "lx_other": auth.create_access_token(u2),
        "outsider": auth.create_access_token(u3),
    }
    yield client, tokens
    storage.reset_engine_for_tests()
    dr._DEEP_TASKS.clear()


def test_endpoints_block_anonymous(gated_client):
    client, _ = gated_client
    assert client.post("/api/agents/deep-research?symbol=600519").status_code in (401, 403)
    assert client.get("/api/agents/deep-research/anytask").status_code in (401, 403)


def test_endpoint_blocks_non_whitelist(gated_client):
    """深研已从白名单放开到会员：非会员非白名单 → 402（引导开通，前端转升级弹窗），不再是 403。"""
    client, tokens = gated_client
    h = {"Authorization": f"Bearer {tokens['outsider']}"}
    assert client.post("/api/agents/deep-research?symbol=600519", headers=h).status_code == 402
    assert client.get("/api/agents/deep-research/x", headers=h).status_code == 402


def test_whitelist_can_start_and_poll(gated_client):
    client, tokens = gated_client
    h = {"Authorization": f"Bearer {tokens['lx199710']}"}
    r = client.post("/api/agents/deep-research?symbol=600519&name=贵州茅台", headers=h)
    assert r.status_code == 200
    tid = r.json()["task_id"]
    poll = client.get(f"/api/agents/deep-research/{tid}", headers=h)
    assert poll.status_code == 200
    body = poll.json()
    assert body["task_id"] == tid and "owner" not in body


def test_poll_owner_isolation_returns_404(gated_client):
    """白名单用户 A 起任务，白名单用户 B 拿 task_id 轮询 → 404（不泄漏存在性）。"""
    client, tokens = gated_client
    ha = {"Authorization": f"Bearer {tokens['lx199710']}"}
    hb = {"Authorization": f"Bearer {tokens['lx_other']}"}
    tid = client.post("/api/agents/deep-research?symbol=600519", headers=ha).json()["task_id"]
    assert client.get(f"/api/agents/deep-research/{tid}", headers=hb).status_code == 404


def test_start_missing_symbol_400(gated_client):
    client, tokens = gated_client
    h = {"Authorization": f"Bearer {tokens['lx199710']}"}
    assert client.post("/api/agents/deep-research?symbol=", headers=h).status_code == 400


# --------------------------------------------------------------------------- #
# ⑦ 材料召回:券商研报工具(修「网站有研报却搜不到」)
# --------------------------------------------------------------------------- #
def test_get_stock_research_tool(monkeypatch):
    from deepfocus_api import agent_tools, eastmoney_reports

    async def fake(*, code, market=None, page_size=20):
        assert code == "300450"
        return ([{"info_code": "x", "title": "锂电装备龙头多元破局", "org": "东吴证券",
                  "rating": "买入", "date": "2026-03-31", "stock_name": "先导智能",
                  "stock_code": "300450", "pdf_url": ""}], [])

    monkeypatch.setattr(eastmoney_reports, "query_eastmoney_reports", fake)
    r = asyncio.run(agent_tools._tool_get_stock_research("300450"))
    assert r["ok"] is True and r["data"][0]["org"] == "东吴证券" and r["data"][0]["rating"] == "买入"
    assert "get_stock_research" in agent_tools.TOOL_REGISTRY


def test_get_stock_research_empty_graceful(monkeypatch):
    from deepfocus_api import agent_tools, eastmoney_reports

    async def fake_empty(*, code, market=None, page_size=20):
        return ([], ["东方财富研报 暂无该标的研报"])

    monkeypatch.setattr(eastmoney_reports, "query_eastmoney_reports", fake_empty)
    r = asyncio.run(agent_tools._tool_get_stock_research("000001"))
    assert r["ok"] is False and r["data"] is None and "暂无" in (r["error"] or "")


def test_deep_evidence_context_drives_research_tool():
    """取证路B prompt 必须显式驱动 get_stock_research（修材料召回的关键）。"""
    q = dr._evidence_q_context("先导智能", "300450")
    assert "get_stock_research" in q and "300450" in q


# --------------------------------------------------------------------------- #
# ⑧ 省 token：结果复用
# --------------------------------------------------------------------------- #
def test_recent_done_reuse_scope_and_ttl():
    def go():
        async def inner():
            t = await dr.create_task("lx199710", True, "600519", "茅台", "CN")
            none_pending = await dr.recent_done("lx199710", "600519", "CN")  # 未完成不复用
            t.status = "done"; t.result = {"direction": "中性"}; t.updated_ts = dr._now()
            hit = await dr.recent_done("lx199710", "600519", "CN")
            cross_owner = await dr.recent_done("someone", "600519", "CN")    # 跨人不复用
            cross_sym = await dr.recent_done("lx199710", "300750", "CN")     # 别股不复用
            t.updated_ts = dr._now() - 700                                   # 超 600s TTL
            expired = await dr.recent_done("lx199710", "600519", "CN")
            return none_pending, hit, t.task_id, cross_owner, cross_sym, expired
        return inner()
    none_pending, hit, tid, co, cs, expired = _run(lambda: go())
    assert none_pending is None and hit == tid and co is None and cs is None and expired is None


def test_endpoint_reuses_recent_done_and_force_bypasses(gated_client):
    client, tokens = gated_client
    h = {"Authorization": f"Bearer {tokens['lx199710']}"}
    seed = dr.DeepTask(task_id="seed123", owner="lx199710", ifind_used=True, symbol="600519",
                       name="", market="CN", status="done", result={"direction": "中性"},
                       created_ts=dr._now(), updated_ts=dr._now(), expires_ts=dr._now() + 1800)
    dr._DEEP_TASKS[seed.task_id] = seed
    r = client.post("/api/agents/deep-research?symbol=600519&market=CN", headers=h).json()
    assert r["reused"] is True and r["task_id"] == "seed123"           # 复用，0 LLM
    r2 = client.post("/api/agents/deep-research?symbol=600519&market=CN&force=1", headers=h).json()
    assert r2["task_id"] != "seed123" and r2["status"] == "pending"    # force 绕过 → 新任务
