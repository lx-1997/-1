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
