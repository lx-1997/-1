"""「昨日回看」单测：上一期复盘点名的板块主线 × 今日板块真实涨跌对照（问责闭环后半环）。

覆盖：有昨日数据（延续/未延续/回落 三态 + 对不上跳过）、无昨日数据空态（整节省略）、
同日场次不算上一期、措辞合规红线、build_review 挂载（结构化字段 + 叙述追加，零新增东财请求）。
"""
import asyncio

import pytest

from deepfocus_api import ashare_review
from deepfocus_api.ashare_review import (
    _build_yesterday_lookback,
    _lookback_status,
    _prev_review_payload,
)

TODAY = "2026-07-02"


def _prev_payload(date="2026-07-01", session="close", top=None):
    """一份「上一期取全复盘」payload（is_complete 依赖 sectors.top / breadth）。"""
    return {
        "date": date,
        "session": session,
        "session_label": ashare_review.session_label(session),
        "sectors": {"top": top if top is not None else [
            {"name": "半导体", "pct": 3.2},
            {"name": "白酒", "pct": 2.8},
            {"name": "银行", "pct": 1.0},
            {"name": "已改名板块", "pct": 0.9},
        ]},
        "breadth": {"total": 5000, "limit_up": 30},
    }


def _hist_rows(*payloads):
    return [{"recorded_at": "2026-07-01T07:40:00", "payload": p} for p in payloads]


# ---------------------------------------------------------------- 有昨日数据 #

def test_lookback_with_yesterday_data(monkeypatch):
    monkeypatch.setattr(ashare_review.data_store, "history",
                        lambda *a, **k: _hist_rows(_prev_payload()))
    board_pcts = {"半导体": 2.1, "白酒": -1.3, "银行": 0.1, "券商": 4.0}
    lb = _build_yesterday_lookback(TODAY, board_pcts)
    assert lb is not None
    assert lb["date"] == "2026-07-01"
    assert lb["session"] == "close"
    by_name = {it["name"]: it for it in lb["items"]}
    # 三态：≥+0.3 延续 / ≤-0.3 回落 / 其间 未延续
    assert by_name["半导体"]["status"] == "延续"
    assert by_name["白酒"]["status"] == "回落"
    assert by_name["银行"]["status"] == "未延续"
    # 昨日提过但今日板块里对不上的（改名/缺数）→ 跳过，不硬凑
    assert "已改名板块" not in by_name
    # 昨日/今日涨跌都如实带上
    assert by_name["半导体"]["prev_pct"] == 3.2
    assert by_name["半导体"]["today_pct"] == 2.1
    # 文本：中性事实描述
    assert "昨日回看" in lb["text"]
    assert "半导体今日+2.10%延续" in lb["text"]
    assert "白酒今日-1.30%回落" in lb["text"]
    assert "2026-07-01" in lb["text"]


def test_lookback_wording_compliance(monkeypatch):
    """措辞铁律：说对说错都平铺直叙，禁自夸、禁未来引导。"""
    monkeypatch.setattr(ashare_review.data_store, "history",
                        lambda *a, **k: _hist_rows(_prev_payload()))
    lb = _build_yesterday_lookback(TODAY, {"半导体": 5.0, "白酒": 3.0, "银行": 2.0})
    assert lb is not None
    for banned in ("精准", "帮你", "赚", "预判", "验证了", "证明", "建议", "布局", "把握", "机会", "跟上"):
        assert banned not in lb["text"], f"违禁词「{banned}」出现在昨日回看文本：{lb['text']}"


# ------------------------------------------------------------ 无昨日数据空态 #

def test_lookback_no_history_omitted(monkeypatch):
    """首日/无历史 → 整节省略（None），不硬找辩解。"""
    monkeypatch.setattr(ashare_review.data_store, "history", lambda *a, **k: [])
    assert _build_yesterday_lookback(TODAY, {"半导体": 2.1}) is None


def test_lookback_no_board_data_omitted(monkeypatch):
    """今日板块数据缺失（东财限流）→ 整节省略，且不读历史。"""
    def _boom(*a, **k):
        raise AssertionError("board_pcts 为空时不应触发历史读取")
    monkeypatch.setattr(ashare_review.data_store, "history", _boom)
    assert _build_yesterday_lookback(TODAY, {}) is None


def test_lookback_same_day_session_not_used(monkeypatch):
    """收盘复盘不能把今日午盘当「上一期」：date 必须早于今日；全是今日 → 空态。"""
    monkeypatch.setattr(ashare_review.data_store, "history",
                        lambda *a, **k: _hist_rows(_prev_payload(date=TODAY, session="midday")))
    assert _prev_review_payload(TODAY) is None
    assert _build_yesterday_lookback(TODAY, {"半导体": 2.1}) is None
    # 今日午盘 + 昨日收盘混排 → 跳过今日、取到昨日
    monkeypatch.setattr(ashare_review.data_store, "history",
                        lambda *a, **k: _hist_rows(_prev_payload(date=TODAY, session="midday"),
                                                   _prev_payload(date="2026-07-01")))
    prev = _prev_review_payload(TODAY)
    assert prev is not None and prev["date"] == "2026-07-01"


def test_lookback_no_matching_boards_omitted(monkeypatch):
    """昨日主线全对不上今日板块（改名/缺数）→ 整节省略。"""
    monkeypatch.setattr(ashare_review.data_store, "history",
                        lambda *a, **k: _hist_rows(_prev_payload(top=[{"name": "旧板块A", "pct": 3.0}])))
    assert _build_yesterday_lookback(TODAY, {"半导体": 2.1}) is None


def test_lookback_status_thresholds():
    assert _lookback_status(0.3) == "延续"
    assert _lookback_status(2.1) == "延续"
    assert _lookback_status(0.29) == "未延续"
    assert _lookback_status(0.0) == "未延续"
    assert _lookback_status(-0.29) == "未延续"
    assert _lookback_status(-0.3) == "回落"
    assert _lookback_status(-1.3) == "回落"


# ------------------------------------------------------- build_review 挂载 #

def _patch_build_review_io(monkeypatch, history_rows):
    """把 build_review 的外部取数全部替换为确定性桩（零真实请求），LLM 走模板路径。"""
    async def fake_indices():
        return [{"name": "上证指数", "close": 3400.0, "pct": 0.5}]

    async def fake_sectors_breadth():
        sectors = {"top": [{"name": "半导体", "pct": 2.1, "main_flow": 1e8, "leader": "某股"}],
                   "bottom": [{"name": "白酒", "pct": -1.3, "main_flow": -1e8, "leader": ""}]}
        breadth = {"advancers": 3000, "decliners": 2000, "flat": 300, "total": 5300}
        board_pcts = {"半导体": 2.1, "白酒": -1.3, "银行": 0.1}
        return sectors, breadth, board_pcts

    async def fake_movers():
        return {"gainers": [], "losers": [], "limit_up": 20, "limit_down": 3}

    async def fake_none(*a, **k):
        return None

    async def fake_empty_list(*a, **k):
        return []

    async def fake_empty_dict(*a, **k):
        return {}

    monkeypatch.setattr(ashare_review, "_gather_indices", fake_indices)
    monkeypatch.setattr(ashare_review, "_gather_sectors_breadth", fake_sectors_breadth)
    monkeypatch.setattr(ashare_review, "_gather_movers", fake_movers)
    monkeypatch.setattr(ashare_review, "_gather_our_signals", lambda *a, **k: [])
    monkeypatch.setattr(ashare_review, "_agentic_linkages", fake_none)
    monkeypatch.setattr(ashare_review, "_judge_linkages", fake_empty_list)
    monkeypatch.setattr(ashare_review, "_gather_content_items", lambda *a, **k: [])
    monkeypatch.setattr(ashare_review, "_broker_views", fake_empty_list)
    monkeypatch.setattr(ashare_review, "_ifind_fundamentals", fake_empty_dict)
    monkeypatch.setattr(ashare_review, "_llm_narrative", fake_none)  # → 模板叙述路径
    monkeypatch.setattr(ashare_review.data_store, "history", lambda *a, **k: history_rows)


def test_build_review_mounts_lookback(monkeypatch):
    """有上一期复盘 → snap 挂 yesterday_lookback，且叙述「板块」段尾追加同一句文本。"""
    _patch_build_review_io(monkeypatch, _hist_rows(_prev_payload()))
    snap = asyncio.run(ashare_review.build_review(TODAY))
    lb = snap.get("yesterday_lookback")
    assert lb is not None
    assert lb["date"] == "2026-07-01"
    statuses = {it["name"]: it["status"] for it in lb["items"]}
    assert statuses == {"半导体": "延续", "白酒": "回落", "银行": "未延续"}
    nar = snap.get("narrative") or {}
    assert lb["text"] in (nar.get("sectors") or "")
    # 追加不吞掉原有板块叙述
    assert (nar.get("sectors") or "") != lb["text"] or not nar.get("sectors")


def test_build_review_omits_lookback_without_history(monkeypatch):
    """无历史（首日）→ 不挂字段、叙述不带「昨日回看」，绝不硬找。"""
    _patch_build_review_io(monkeypatch, [])
    snap = asyncio.run(ashare_review.build_review(TODAY))
    assert "yesterday_lookback" not in snap
    assert "昨日回看" not in ((snap.get("narrative") or {}).get("sectors") or "")
