"""微信/orchestrator 路由层修复回归：

覆盖三件事——
1. 全市场扫描类技能的「误触发」否决闸（个股代码锚定 / 收紧后的裸"市场"不再命中）；
2. 闲聊·客服·计费类问题的判定（这类不该被强制走投研取数）；
3. 技能仲裁（多个候选命中时按特异性取最高分，而非固定顺序首个命中即短路）。
"""

from datetime import datetime, timezone

import pytest

from deepfocus_api.cn_earnings_skill import detect_cn_earnings_request
from deepfocus_api.major_event_skill import detect_major_event_request
from deepfocus_api.shareholder_change_skill import detect_shareholder_change_request
from deepfocus_api.schemas import OrchestratorChatRequest


# ── 1. 误触发否决闸 ───────────────────────────────────────────────

def test_specific_code_blocks_market_scan_skills():
    """带 6 位代码 = 问"这只股"，三个全市场扫描技能都应让位(返回 None)。"""
    assert detect_shareholder_change_request("帮我看看 000001 最近有没有减持") is None
    assert detect_cn_earnings_request("600519 的财报怎么样") is None
    assert detect_major_event_request("300750 A股最近有重组吗") is None


def test_known_stock_name_blocks_market_scan_skills():
    """带具体股票**名称**(非代码)的提问 = 问"这只股"，全市场扫描技能应让位(返回 None)。
    依赖内置种子名(宁德时代/贵州茅台/思源电气)，无网络也成立。"""
    assert detect_major_event_request("A股的宁德时代有没有回购") is None
    assert detect_cn_earnings_request("贵州茅台 A股财报怎么样") is None
    assert detect_shareholder_change_request("思源电气在A股有没有减持") is None


def test_mentions_specific_stock_basic():
    from deepfocus_api.stock_name_index import mentions_specific_stock

    assert mentions_specific_stock("600519 怎么样") is True          # 代码
    assert mentions_specific_stock("宁德时代值得买吗") is True         # 名称(种子)
    assert mentions_specific_stock("今天天气不错随便聊聊") is False     # 无个股
    assert mentions_specific_stock("A股最近哪些公司回购") is False      # 全市场扫描，不含具体个股


def test_bare_market_word_no_longer_triggers():
    """收紧范围词后，裸"市场"+事件词不再误命中(需明确的 A股/全A/沪深等)。"""
    assert detect_major_event_request("市场回购情况怎么样") is None
    assert detect_cn_earnings_request("市场财报季到了吗") is None
    # 而明确的全市场表述仍然命中
    assert detect_major_event_request("查看 A 股近一周重大事项风险预警") is not None
    assert detect_cn_earnings_request("查看 A 股近一个月所有财报公告") is not None
    assert detect_shareholder_change_request("列出全A最近10天减持公告前20") is not None


# ── 2. 闲聊 / 客服 / 计费判定 ─────────────────────────────────────

def test_smalltalk_and_service_detected():
    from deepfocus_api.main import _is_smalltalk_or_service

    for q in ["你好", "在吗", "谢谢", "怎么充值", "会员多少钱", "怎么续费", "找人工客服", "我要退款"]:
        assert _is_smalltalk_or_service(q) is True, q


def test_research_questions_not_smalltalk():
    from deepfocus_api.main import _is_smalltalk_or_service

    for q in ["贵州茅台怎么样", "今天大盘怎么样", "A股变压器龙头股", "宁德时代值得长期持有吗", "半导体板块前景如何"]:
        assert _is_smalltalk_or_service(q) is False, q


# ── 3. 技能仲裁：多个候选命中时取特异性最高，而非固定顺序 ──────────

@pytest.mark.asyncio
async def test_scan_skill_arbitration_prefers_more_specific(monkeypatch):
    """一句话同时命中「股东增减持(泛, score=1)」与「财报·两类报告(具体, score=3)」时，
    应路由到更具体的财报扫描，而不是因声明顺序在前就选股东扫描。"""
    from deepfocus_api import main
    from deepfocus_api.schemas import CnEarningsScanResponse, ShareholderChangeScanResponse

    message = "列出全A最近30天股东增减持以及一季报、年报情况"

    # 双双命中、确认特异性分差成立
    assert detect_shareholder_change_request(message) is not None
    ce = detect_cn_earnings_request(message)
    assert ce is not None and set(ce.report_types) >= {"q1", "annual"}

    async def fake_cn_earnings(skill_request):
        return CnEarningsScanResponse(
            generated_at=datetime.now(timezone.utc),
            start_date="2026-05-27",
            end_date="2026-06-26",
            total_found=0,
            returned_count=0,
            summary="命中 0 条 A 股财报公告。",
            records=[],
        )

    async def fail_shareholder(skill_request):  # 不应被调用
        raise AssertionError("仲裁应选更具体的财报扫描，而非股东增减持扫描")

    monkeypatch.setattr(main, "scan_cn_earnings", fake_cn_earnings)
    monkeypatch.setattr(main, "scan_shareholder_changes", fail_shareholder)

    resp = await main._route_orchestrator_chat(
        OrchestratorChatRequest(message=message),
        _ifind=False,
        force_research=False,
        skip_professional=True,
    )
    assert resp is not None
    assert "cn.earnings.scan" in resp.chips


# ── 4. 会话历史不污染技能路由(「粘滞技能」bug 回归) ───────────────────

# 上一轮 shareholder.change.scan 的回答头部(增持/减持/A股 等关键词密集、无具体个股)——
# _ctx_hint 在生产中会把历史答案截断到 160 字，恰好留下这段会"粘住"路由的关键词头。
_PREV_SCAN_ANSWER = (
    "已调用 skill：shareholder.change.scan\n"
    "范围：A股 2026-05-29 至 2026-06-27，方向：增减持。共命中 407 条 A股增减持相关披露；"
    "其中增持 168 条、减持 239 条、同时涉及增减持 0 条，高风险 194 条。"
)
# 用生产同款 _ctx_hint 拼上下文前缀(含 160 字截断 + "用户当前消息：" 结尾哨兵)，确保测试与线上一致。
from deepfocus_api.weixin_channel import _ctx_hint  # noqa: E402

_PREV_CTX_PREFIX = _ctx_hint([{"q": "列出全A最近30天股东增减持", "a": _PREV_SCAN_ANSWER}])


def test_root_cause_history_baked_into_message_would_re_trigger():
    """根因留档：若把会话历史(生产 _ctx_hint 的 160 字截断头)拼进 message(旧行为)，detector 会被历史里的
    增持/减持/A股 关键词带偏而**误命中**；而仅看当前这句干净问题则不命中——这正是修复「路由只看当前问题」要消除的。"""
    current = "总结下近期研报"
    assert detect_shareholder_change_request(current) is None                     # 干净问题:不该命中
    assert detect_shareholder_change_request(_PREV_CTX_PREFIX + current) is not None  # 旧行为:被历史污染→误命中


@pytest.mark.asyncio
async def test_history_via_context_prefix_does_not_re_trigger_scan_skill(monkeypatch):
    """粘滞技能 bug 回归：上一轮股东扫描的长答案作为会话历史经 context_prefix 传入时，
    本轮「总结下近期研报」**不得**再命中任何全市场扫描技能；历史只应进入 LLM 上下文(tool-agent 的 context_hint)。"""
    from deepfocus_api import main
    from deepfocus_api.schemas import OrchestratorChatResponse

    current_question = "总结下近期研报"  # 与股东增减持无关

    async def fail_scan(*a, **k):  # 任一扫描技能被调用 = 路由被历史污染 = bug 复现
        raise AssertionError("会话历史污染了技能路由——粘滞技能 bug 复现")

    monkeypatch.setattr(main, "scan_shareholder_changes", fail_scan)
    monkeypatch.setattr(main, "scan_cn_earnings", fail_scan)
    monkeypatch.setattr(main, "scan_major_events", fail_scan)

    captured: dict = {}

    async def fake_tool_agent(*, question, context_hint="", ifind_user=False, timeout_seconds=30.0, **k):
        captured["question"] = question
        captured["context_hint"] = context_hint
        return {"answer": "近期研报要点：……", "tool_trace": [], "rounds": 1}

    def fake_map(agent_result, request, provider, model):
        return OrchestratorChatResponse(
            provider=provider, model=model, generated_at=datetime.now(timezone.utc),
            title="研报要点", content=agent_result.get("answer", ""), chips=["tool-agent"],
        )

    monkeypatch.setattr(main.llm, "run_tool_agent", fake_tool_agent)
    monkeypatch.setattr(main, "tool_agent_to_orchestrator_response", fake_map)

    resp = await main._route_orchestrator_chat(
        OrchestratorChatRequest(message=current_question),
        _ifind=False,
        force_research=True,        # 微信场景:非闲聊即强制研究
        skip_professional=True,
        context_prefix=_PREV_CTX_PREFIX,
    )

    assert isinstance(resp, OrchestratorChatResponse)
    # ① 技能路由只看了当前这句干净问题(tool-agent 收到的 question 即它,绝不含历史)
    assert captured["question"] == current_question
    # ② 但历史确实作为 LLM 上下文一并下传(多轮追问仍能接上)——只是不进确定性路由
    assert "shareholder.change.scan" in captured["context_hint"]
    assert captured["context_hint"] == _PREV_CTX_PREFIX
