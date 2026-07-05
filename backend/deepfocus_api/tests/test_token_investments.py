"""「多花 token 换效果」四项 token 投资优化的回归。

覆盖（全程 mock 掉 MiniMax 与缓存，无网络）：
- D1 英文快讯数字核验轮：数字密集才触发、确定性锚点仲裁拒绝丢数字的"修正"、低数字不触发、开关。
- D2 晨报标题自验证轮：Gen1 草稿 → Gen2 改写采纳、失败回退草稿。
- B  速判卡冲突维度权衡：维度打架才多跑一轮、非冲突不触发、第二轮失败回退第一轮。
- A  争议个股多空深度档：synthesize_multiview 结构与护栏、争议股识别闸、make_multiview_fn。
- C  赛马分歧票多空对话：指纹稳定性、只读缓存挂载、生成结构与缺方安全。
"""
import asyncio
import contextlib

import pytest

from deepfocus_api import news_translate as nt
from deepfocus_api import ai_fund
from deepfocus_api.llm import CloudResearchLLM
import deepfocus_api.weixin_channel as wx


# ════════════════════════════════════════════════════════════════════════════
# 公共：让 CloudResearchLLM 实例「非 mock」+ 注入假 complete_json
# ════════════════════════════════════════════════════════════════════════════
def _live_llm(monkeypatch, fake_complete_json):
    llm = CloudResearchLLM()
    monkeypatch.setattr(type(llm), "provider", property(lambda self: "minimax"))
    monkeypatch.setattr(llm, "complete_json", fake_complete_json)
    return llm


# ════════════════════════════════════════════════════════════════════════════
# D1 · 英文快讯数字核验轮
# ════════════════════════════════════════════════════════════════════════════
def test_anchor_numbers_and_coverage():
    src = "Apple Q4 EPS up 5% and revenue grew 6% in 2024"
    assert set(nt._anchor_numbers(src)) == {"5%", "6%", "2024"}
    # 译文保住全部锚点 → 覆盖 3
    assert nt._anchor_coverage(src, "苹果四季度EPS增长5%，营收增6%，2024年") == 3
    # 译文漏掉 6% → 覆盖只 2
    assert nt._anchor_coverage(src, "苹果四季度EPS增长5%，2024年") == 2
    # 百分点写法也算保住
    assert nt._anchor_coverage("up 5%", "上行 5 个百分点") == 1


def _patch_translate_store(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(nt.data_store, "record",
                        lambda kind, sym, payload, **k: store.__setitem__((kind, sym), payload))
    monkeypatch.setattr(nt.data_store, "latest", lambda kind, sym, **k: store.get((kind, sym)))
    return store


def test_verify_fires_only_on_number_dense(monkeypatch):
    """数字密集英文 → 触发核验轮(2 次调用)；稀疏 → 不触发(1 次)。"""
    _patch_translate_store(monkeypatch)
    calls = {"n": 0}

    class _LLM:
        def __init__(self, *a, **k):
            pass

        async def complete_json(self, prompt, **kw):
            calls["n"] += 1
            if "质检" in prompt:               # 核验轮
                return {"ok": True}
            return {"title": "苹果四季度EPS增5% 营收增6% 2024展望", "content": ""}

    monkeypatch.setattr(nt, "CloudResearchLLM", _LLM)
    # 3 个数字(5,6,2024) ≥ 阈值 3 → 触发核验
    out = asyncio.run(nt.translate_news("Apple Q4 EPS up 5%, revenue up 6%, 2024 outlook", ""))
    assert out is not None and calls["n"] == 2  # 直译 + 核验

    calls["n"] = 0
    # 叙述型(仅 1 个数字 2028) < 阈值 → 不触发核验
    out2 = asyncio.run(nt.translate_news("EU proposes extending protection to 2028 for refugees", ""))
    assert out2 is not None and calls["n"] == 1  # 只直译


def test_verify_arbiter_rejects_fix_that_drops_number(monkeypatch):
    """核验轮给的'修正版'若弄丢了原文百分比 → 确定性仲裁拒绝，保留基础译文。"""
    _patch_translate_store(monkeypatch)

    class _LLM:
        def __init__(self, *a, **k):
            pass

        async def complete_json(self, prompt, **kw):
            if "质检" in prompt:
                # 恶意/手滑修正：把 6% 弄没了
                return {"ok": False, "issues": ["x"], "title": "苹果EPS增5% 2024展望", "content": ""}
            return {"title": "苹果四季度EPS增5% 营收增6% 2024展望", "content": ""}

    monkeypatch.setattr(nt, "CloudResearchLLM", _LLM)
    out = asyncio.run(nt.translate_news("Apple Q4 EPS up 5%, revenue up 6%, 2024 outlook", ""))
    assert "6%" in out["title"]  # 仲裁保住了基础版(没被丢数字的修正覆盖)


def test_verify_accepts_fix_that_keeps_numbers(monkeypatch):
    """核验轮修正版保住全部锚点 → 采纳修正版。"""
    _patch_translate_store(monkeypatch)

    class _LLM:
        def __init__(self, *a, **k):
            pass

        async def complete_json(self, prompt, **kw):
            if "质检" in prompt:
                return {"ok": False, "issues": ["单位"], "title": "苹果EPS增5% 营收增6% 2024(已修单位)", "content": ""}
            return {"title": "苹果四季度EPS增5% 营收增6% 2024展望", "content": ""}

    monkeypatch.setattr(nt, "CloudResearchLLM", _LLM)
    out = asyncio.run(nt.translate_news("Apple Q4 EPS up 5%, revenue up 6%, 2024 outlook", ""))
    assert "已修单位" in out["title"]  # 采纳了修正版


def test_verify_disabled_skips_round(monkeypatch):
    _patch_translate_store(monkeypatch)
    monkeypatch.setattr(nt, "_VERIFY_ENABLED", False)
    calls = {"n": 0}

    class _LLM:
        def __init__(self, *a, **k):
            pass

        async def complete_json(self, prompt, **kw):
            calls["n"] += 1
            return {"title": "苹果四季度EPS增5% 营收增6% 2024", "content": ""}

    monkeypatch.setattr(nt, "CloudResearchLLM", _LLM)
    asyncio.run(nt.translate_news("Apple Q4 EPS up 5%, revenue up 6%, 2024 outlook", ""))
    assert calls["n"] == 1  # 开关关 → 只直译，不核验


# ════════════════════════════════════════════════════════════════════════════
# D2 · 晨报标题自验证轮
# ════════════════════════════════════════════════════════════════════════════
class _Brief:
    def __init__(self, verdict, dims):
        self.overall_verdict = verdict
        self.dimensions = dims


class _BDim:
    def __init__(self, label, headline, signal="bullish"):
        self.label = label
        self.headline = headline
        self.signal = signal


def test_briefing_self_verify_adopts_refined(monkeypatch):
    """Gen1 草稿 → Gen2 改写成更锐主线 → 采纳改写版。"""
    _patch_narr_store(monkeypatch)  # 强制晨报标题缓存未命中 + record 空操作

    async def fake_cj(prompt, **kw):
        if "终审" in prompt or "草稿" in prompt:      # 自验证轮
            return {"headline": "主线明确：利率下行驱动成长占优"}
        return {"headline": "市场涨跌互现，多空交织"}    # Gen1 骑墙草稿

    llm = _live_llm(monkeypatch, fake_cj)
    macro = _Brief("risk-on", [_BDim("利率", "10Y 下行")])
    port = _Brief("均衡", [_BDim("敞口", "成长偏高")])
    out = asyncio.run(llm.synthesize_briefing_headline(macro, port))
    assert out == "主线明确：利率下行驱动成长占优"


def test_briefing_self_verify_falls_back_on_failure(monkeypatch):
    """自验证轮抛错 → 回退 Gen1 草稿，不阻断晨报。"""
    _patch_narr_store(monkeypatch)

    async def fake_cj(prompt, **kw):
        if "终审" in prompt or "草稿" in prompt:
            raise RuntimeError("refine down")
        return {"headline": "成长占优，关注利率拐点"}

    llm = _live_llm(monkeypatch, fake_cj)
    macro = _Brief("risk-on", [_BDim("利率", "10Y 下行")])
    port = _Brief("均衡", [_BDim("敞口", "成长偏高")])
    out = asyncio.run(llm.synthesize_briefing_headline(macro, port))
    assert out == "成长占优，关注利率拐点"


def test_briefing_self_verify_can_disable(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_BRIEFING_SELF_VERIFY", "0")
    _patch_narr_store(monkeypatch)
    calls = {"n": 0}

    async def fake_cj(prompt, **kw):
        calls["n"] += 1
        return {"headline": "成长占优"}

    llm = _live_llm(monkeypatch, fake_cj)
    macro = _Brief("risk-on", [_BDim("利率", "10Y 下行")])
    port = _Brief("均衡", [_BDim("敞口", "成长偏高")])
    asyncio.run(llm.synthesize_briefing_headline(macro, port))
    assert calls["n"] == 1  # 关闭 → 只 Gen1


def test_briefing_cache_hit_skips_both_llm_rounds(monkeypatch):
    """指纹缓存命中 → Gen1 与自验证两轮全跳过(反复进页面不重复双调)。"""
    from deepfocus_api import data_store
    monkeypatch.setattr(data_store, "latest", lambda kind, key, **k: "缓存的晨会主线")

    async def fake_cj(prompt, **kw):
        raise AssertionError("缓存命中不应再调模型")

    llm = _live_llm(monkeypatch, fake_cj)
    macro = _Brief("risk-on", [_BDim("利率", "10Y 下行")])
    port = _Brief("均衡", [_BDim("敞口", "成长偏高")])
    out = asyncio.run(llm.synthesize_briefing_headline(macro, port))
    assert out == "缓存的晨会主线"


# ════════════════════════════════════════════════════════════════════════════
# B · 速判卡冲突维度权衡
# ════════════════════════════════════════════════════════════════════════════
class _DQ:
    level = "live"


class _RDim:
    def __init__(self, label, signal, headline="h", confidence=0.6):
        self.label = label
        self.signal = signal
        self.headline = headline
        self.confidence = confidence
        self.evidence = ["证据"]
        self.data_quality = _DQ()


def _patch_narr_store(monkeypatch):
    from deepfocus_api import data_store
    monkeypatch.setattr(data_store, "latest", lambda *a, **k: None)   # 强制缓存未命中
    monkeypatch.setattr(data_store, "record", lambda *a, **k: None)


def test_conflict_weigh_fires_on_conflict(monkeypatch):
    """既有看多又有看空 → 多跑一轮权衡，narrative 被点破主导项的版本替换。"""
    _patch_narr_store(monkeypatch)
    seen = {"conflict": False}

    async def fake_cj(prompt, **kw):
        if "方向打架" in prompt:                    # 冲突权衡轮
            seen["conflict"] = True
            return {"narrative": "估值虽贵但动量主导，趋势未破不轻言反转"}
        return {"narrative": "综合看多"}

    llm = _live_llm(monkeypatch, fake_cj)
    dims = [_RDim("动量", "bullish"), _RDim("估值", "bearish")]
    out = asyncio.run(llm.synthesize_review_narrative(
        subject="某股", verdict="看多", score=60, confidence=0.7, dimensions=dims))
    assert seen["conflict"] is True
    assert "主导" in out


def test_no_conflict_skips_weigh(monkeypatch):
    """全看多 → 不触发权衡轮(只 1 次 complete_json)。"""
    _patch_narr_store(monkeypatch)
    calls = {"n": 0}

    async def fake_cj(prompt, **kw):
        calls["n"] += 1
        assert "方向打架" not in prompt   # 不该进权衡轮
        return {"narrative": "动量与基本面共振看多"}

    llm = _live_llm(monkeypatch, fake_cj)
    dims = [_RDim("动量", "bullish"), _RDim("催化", "bullish")]
    asyncio.run(llm.synthesize_review_narrative(
        subject="某股", verdict="看多", score=70, confidence=0.8, dimensions=dims))
    assert calls["n"] == 1


def test_conflict_weigh_falls_back_on_failure(monkeypatch):
    """权衡轮抛错 → 回退第一轮叙述。"""
    _patch_narr_store(monkeypatch)

    async def fake_cj(prompt, **kw):
        if "方向打架" in prompt:
            raise RuntimeError("weigh down")
        return {"narrative": "综合看多但分歧大"}

    llm = _live_llm(monkeypatch, fake_cj)
    dims = [_RDim("动量", "bullish"), _RDim("估值", "bearish")]
    out = asyncio.run(llm.synthesize_review_narrative(
        subject="某股", verdict="看多", score=55, confidence=0.6, dimensions=dims))
    assert out == "综合看多但分歧大"


def test_conflict_weigh_can_disable(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_TEARSHEET_CONFLICT_WEIGH", "0")
    _patch_narr_store(monkeypatch)
    calls = {"n": 0}

    async def fake_cj(prompt, **kw):
        calls["n"] += 1
        return {"narrative": "综合看多"}

    llm = _live_llm(monkeypatch, fake_cj)
    dims = [_RDim("动量", "bullish"), _RDim("估值", "bearish")]
    asyncio.run(llm.synthesize_review_narrative(
        subject="某股", verdict="看多", score=55, confidence=0.6, dimensions=dims))
    assert calls["n"] == 1  # 关闭 → 不跑权衡轮


# ════════════════════════════════════════════════════════════════════════════
# A · 争议个股多空深度档
# ════════════════════════════════════════════════════════════════════════════
def test_multiview_structure(monkeypatch):
    async def fake_cj(prompt, **kw):
        return {"bull": "盈利提速、订单饱满", "bear": "估值偏高、减持压力", "verdict": "偏多，盯量能确认"}

    llm = _live_llm(monkeypatch, fake_cj)
    out = asyncio.run(llm.synthesize_multiview("600519 还能买吗", "一段足够长的已有研究分析素材" * 5))
    assert out is not None
    assert "🐂 多头立论" in out and "🐻 空头审视" in out and "⚖️ 投委裁决" in out
    assert "盈利提速" in out and "偏多" in out


def test_multiview_thin_base_returns_none(monkeypatch):
    async def fake_cj(prompt, **kw):
        raise AssertionError("基础答案太薄不应调模型")

    llm = _live_llm(monkeypatch, fake_cj)
    assert asyncio.run(llm.synthesize_multiview("某股怎么看", "太短")) is None


def test_multiview_missing_field_returns_none(monkeypatch):
    async def fake_cj(prompt, **kw):
        return {"bull": "只有多头", "bear": "", "verdict": ""}

    llm = _live_llm(monkeypatch, fake_cj)
    out = asyncio.run(llm.synthesize_multiview("某股怎么看", "足够长的已有分析素材内容" * 5))
    assert out is None  # 三段缺一 → 不出深度档


def test_controversial_stock_gate():
    assert wx._is_controversial_stock_q("600519 现在还能买吗")          # 代码 + 决策词
    assert wx._is_controversial_stock_q("000001 后市怎么看")
    assert not wx._is_controversial_stock_q("今天大盘怎么样")            # 无具体个股
    assert not wx._is_controversial_stock_q("600519 最新股价是多少")     # 有股无决策/争议措辞


def test_make_multiview_fn(monkeypatch):
    class _LLM:
        async def synthesize_multiview(self, q, a):
            return "深度档:" + q

    fn = wx.make_multiview_fn(_LLM())
    assert asyncio.run(fn("600519 能买吗", "分析")) == "深度档:600519 能买吗"


# ════════════════════════════════════════════════════════════════════════════
# C · 赛马分歧票多空对话
# ════════════════════════════════════════════════════════════════════════════
def _div_item():
    return {"symbol": "600000", "name": "测试股",
            "bulls": [{"fund_id": "mammoth", "name": "猛犸", "emoji": "🦣"}],
            "bears": [{"fund_id": "rock", "name": "磐石", "emoji": "🗿"}]}


def test_divergence_fp_stable_and_order_independent():
    a = {"symbol": "600000", "bulls": [{"fund_id": "mammoth"}, {"fund_id": "falcon"}],
         "bears": [{"fund_id": "rock"}]}
    b = {"symbol": "600000", "bulls": [{"fund_id": "falcon"}, {"fund_id": "mammoth"}],  # 顺序不同
         "bears": [{"fund_id": "rock"}]}
    assert ai_fund._divergence_fp(a) == ai_fund._divergence_fp(b)
    c = {"symbol": "600000", "bulls": [{"fund_id": "mammoth"}], "bears": [{"fund_id": "rock"}]}
    assert ai_fund._divergence_fp(a) != ai_fund._divergence_fp(c)  # 阵营变 → 指纹变


def test_attach_divergence_takes_reads_cache(monkeypatch):
    monkeypatch.setattr(ai_fund, "DIV_TAKES", True)
    item = _div_item()
    fp = ai_fund._divergence_fp(item)
    cache = {("aif_div", fp): {"bull": "我看多", "bear": "我看空"}}
    monkeypatch.setattr(ai_fund.data_store, "latest", lambda kind, sym, **k: cache.get((kind, sym)))
    div = [item]
    ai_fund._attach_divergence_takes(div)
    assert div[0]["debate"]["bull"] == "我看多"

    # 无缓存 → 不挂 debate 字段（前端自然降级）
    monkeypatch.setattr(ai_fund.data_store, "latest", lambda *a, **k: None)
    div2 = [_div_item()]
    ai_fund._attach_divergence_takes(div2)
    assert "debate" not in div2[0]


def test_attach_divergence_takes_respects_flag(monkeypatch):
    monkeypatch.setattr(ai_fund, "DIV_TAKES", False)
    called = {"n": 0}
    monkeypatch.setattr(ai_fund.data_store, "latest",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    ai_fund._attach_divergence_takes([_div_item()])
    assert called["n"] == 0  # 关闭 → 连缓存都不读


def test_gen_divergence_take_structure(monkeypatch):
    @contextlib.contextmanager
    def _fake_connect(*a, **k):
        class _C:
            def execute(self, *a, **k):
                class _R:
                    def fetchone(self_inner):
                        return None
                return _R()
        yield _C()

    monkeypatch.setattr(ai_fund, "_connect", _fake_connect)

    class _LLM:
        def __init__(self, *a, **k):
            pass

        async def complete_json(self, prompt, **kw):
            return {"bull": "趋势在我就拿", "bear": "估值贵我躲开"}

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _LLM)
    out = ai_fund._gen_divergence_take(_div_item())
    assert out["bull"] == "趋势在我就拿" and out["bear"] == "估值贵我躲开"
    assert out["bull_agent"]["fund_id"] == "mammoth"
    assert out["bear_agent"]["fund_id"] == "rock"


def test_gen_divergence_take_missing_side_returns_none():
    item = {"symbol": "600000", "name": "x", "bulls": [], "bears": [{"fund_id": "rock"}]}
    assert ai_fund._gen_divergence_take(item) is None
