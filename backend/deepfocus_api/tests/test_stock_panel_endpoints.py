"""个股面板薄 REST 端点 + 战绩公开页的冒烟守卫。

覆盖本次改动：
- /api/stock/{dragon-tiger,consensus,dividends,news,risk-check}：登录门生效（匿名 401）、
  统一 {"data": …} 契约、任何数据源异常 → {"data": null} 绝不 500、一致预期恒带免责 note；
- /api/stock/risk-check：确定性风险体检字段齐全 + 免责措辞 + 数据不齐 → data=null；
- verdict 落库 payload 新增 dims 证据快照（仅 key+signal 精简）；
- /track-record 公开 HTML 页：措辞铁律（只事实表述，无命中率/准确率/收益归因）+ sitemap 收录。

全部 mock 外部取数（monkeypatch 源模块函数），不触外网、不触真实库。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from deepfocus_api import auth, data_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    data_store.init_data_store()
    from deepfocus_api.main import app

    # 无 sid 的 token：session_is_current 宽限放行（不查库），无需真实用户落库。
    user = auth.AuthUserOut(
        id="u-test", email="t@t.local", username="tester", role="member",
        is_active=True, created_at=datetime.now(timezone.utc),
    )
    token = auth.create_access_token(user)
    return TestClient(app), {"Authorization": f"Bearer {token}"}


_PANEL_PATHS = (
    "/api/stock/dragon-tiger?symbol=600519",
    "/api/stock/consensus?symbol=600519&market=CN",
    "/api/stock/dividends?symbol=600519&market=CN",
    "/api/stock/news?symbol=600519&market=CN",
    "/api/stock/risk-check?symbol=600519",
)


def test_panel_endpoints_block_anonymous(client):
    cli, _ = client
    for path in _PANEL_PATHS:
        assert cli.get(path).status_code in (401, 403), path


def test_panel_endpoints_swallow_upstream_errors(client, monkeypatch):
    """任何数据源抛异常 → {"data": null}，绝不 500。"""
    cli, headers = client
    from deepfocus_api import cn_consensus, dividend_history, dragon_tiger, eastmoney_data, stock_news

    async def _boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(dragon_tiger, "fetch_dragon_tiger", _boom)
    monkeypatch.setattr(cn_consensus, "fetch_cn_consensus", _boom)
    monkeypatch.setattr(dividend_history, "fetch_dividend_history", _boom)
    monkeypatch.setattr(stock_news, "fetch_stock_news", _boom)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_kline", _boom)
    for path in _PANEL_PATHS:
        r = cli.get(path, headers=headers)
        assert r.status_code == 200, path
        assert r.json()["data"] is None, path


def test_dragon_tiger_passthrough(client, monkeypatch):
    cli, headers = client
    from deepfocus_api import dragon_tiger

    sample = {"symbol": "600519", "items": [{"date": "2026-07-01", "reason": "日涨幅偏离值达7%"}]}

    async def _fake(symbol, *a, **k):
        assert symbol == "600519"
        return sample

    monkeypatch.setattr(dragon_tiger, "fetch_dragon_tiger", _fake)
    r = cli.get("/api/stock/dragon-tiger?symbol=600519", headers=headers)
    assert r.status_code == 200 and r.json() == {"data": sample}


def test_consensus_carries_disclaimer_note(client, monkeypatch):
    cli, headers = client
    from deepfocus_api import cn_consensus

    sample = {"consensus_rating": "买入", "avg_target_price": 2100.0, "institution_count": 28}

    async def _fake(symbol, market=None, **k):
        return sample

    monkeypatch.setattr(cn_consensus, "fetch_cn_consensus", _fake)
    r = cli.get("/api/stock/consensus?symbol=600519&market=CN", headers=headers)
    body = r.json()
    assert r.status_code == 200 and body["data"] == sample
    assert body["note"] == "券商一致预期，非本站观点，不构成投资建议"
    # 无数据（None）时 note 仍在（前端恒可展示免责）
    async def _none(symbol, market=None, **k):
        return None

    monkeypatch.setattr(cn_consensus, "fetch_cn_consensus", _none)
    body = cli.get("/api/stock/consensus?symbol=AAPL", headers=headers).json()
    assert body["data"] is None and "note" in body


def test_dividends_and_news_passthrough(client, monkeypatch):
    cli, headers = client
    from deepfocus_api import dividend_history, stock_news

    seen = {}

    async def _fake_div(symbol, market=None, limit=6, **k):
        seen["div_limit"] = limit
        return [{"plan": "10派30(含税)", "ex_date": "2026-06-20"}]

    async def _fake_news(symbol, market=None, limit=8, **k):
        seen["news_limit"] = limit
        return [{"title": "公司发布年报", "date": "2026-07-01", "url": "https://e.cn/1"}]

    monkeypatch.setattr(dividend_history, "fetch_dividend_history", _fake_div)
    monkeypatch.setattr(stock_news, "fetch_stock_news", _fake_news)
    r = cli.get("/api/stock/dividends?symbol=600519&market=CN", headers=headers)
    assert r.status_code == 200 and r.json()["data"][0]["plan"] == "10派30(含税)"
    r = cli.get("/api/stock/news?symbol=600519&market=CN", headers=headers)
    assert r.status_code == 200 and r.json()["data"][0]["title"] == "公司发布年报"
    assert seen == {"div_limit": 10, "news_limit": 10}  # 面板契约：各取近 10 条


# --------------------------------------------------------------------------- #
# 持仓风险体检
# --------------------------------------------------------------------------- #
def test_risk_check_returns_positional_facts_only(client, monkeypatch):
    cli, headers = client
    from deepfocus_api import eastmoney_data

    async def _fake_kline(secid, points=160, **k):
        return [(f"d{i}", 100.0 + i * 0.5) for i in range(280)]  # 稳步上行的日线

    async def _fake_index(secid, points=140, **k):
        return [(f"d{i}", 3000.0 + i * 2.0) for i in range(200)]  # 指数上行 → bull

    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_kline", _fake_kline)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_index", _fake_index)
    r = cli.get("/api/stock/risk-check?symbol=600519", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["symbol"] == "600519"
    assert data["price"] == pytest.approx(100.0 + 279 * 0.5)
    assert data["support"] is not None and data["dist_support_pct"] is not None
    assert data["drawdown_from_52w_high_pct"] == pytest.approx(0.0)  # 现价即 52 周高点
    assert data["market_regime"]["status"] == "bull"
    assert "沪深300" in data["market_regime"]["note"]
    assert data["note"] == "以上为历史统计事实与纪律科普，不构成操作建议"
    # ⚠️合规红线：绝不能出现操作性结论
    import json as _json
    flat = _json.dumps(data, ensure_ascii=False)
    for banned in ("建议止损", "建议减仓", "建议卖出", "建议买入"):
        assert banned not in flat


def test_risk_check_insufficient_data_returns_null(client, monkeypatch):
    cli, headers = client
    from deepfocus_api import eastmoney_data

    async def _empty(secid, points=160, **k):
        return []

    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_kline", _empty)
    r = cli.get("/api/stock/risk-check?symbol=600519", headers=headers)
    assert r.status_code == 200 and r.json() == {"data": None}
    # 非 A/港股代码（无东财 secid）同样 data=null
    r = cli.get("/api/stock/risk-check?symbol=AAPL", headers=headers)
    assert r.status_code == 200 and r.json() == {"data": None}


# --------------------------------------------------------------------------- #
# verdict 落库证据快照
# --------------------------------------------------------------------------- #
def test_verdict_datapoint_includes_dims_snapshot(monkeypatch, tmp_path):
    from deepfocus_api import (
        consensus_source, eastmoney_data, github_data, google_finance,
        nasdaq_data, valuation_source, yahoo_finance,
    )
    from deepfocus_api import main as main_mod

    async def _none(*a, **k):
        return None

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(google_finance, "fetch_google_finance_quote", _none)
    monkeypatch.setattr(yahoo_finance, "fetch_yahoo_history", _empty)
    monkeypatch.setattr(yahoo_finance, "fetch_yahoo_quote", _none)
    monkeypatch.setattr(nasdaq_data, "fetch_nasdaq_earnings", _none)
    monkeypatch.setattr(nasdaq_data, "fetch_nasdaq_options", _none)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_earnings", _none)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_index", _empty)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_kline", _empty)
    monkeypatch.setattr(eastmoney_data, "fetch_fund_flow", _none)
    monkeypatch.setattr(github_data, "fetch_sp500_index_history", _empty)
    monkeypatch.setattr(github_data, "fetch_us10y_history", _empty)
    monkeypatch.setattr(github_data, "fetch_sp500_constituent", _none)
    monkeypatch.setattr(valuation_source, "fetch_valuation", _none)
    monkeypatch.setattr(consensus_source, "fetch_analyst_consensus", _none)

    captured: dict = {}

    def _rec(kind, sym, payload, *, market=""):
        if kind == "verdict":
            captured["payload"] = payload

    monkeypatch.setattr(main_mod, "record_datapoint", _rec)
    asyncio.run(main_mod._build_stock_tear_sheet_core("TSLA", market="US"))
    dims = captured["payload"]["dims"]
    assert isinstance(dims, list) and len(dims) >= 5  # 7–9 维的精简快照
    assert all(set(d.keys()) == {"key", "signal"} for d in dims)  # 只存 key+signal，不带重字段


# --------------------------------------------------------------------------- #
# 战绩公开页 /track-record
# --------------------------------------------------------------------------- #
def _seed_review_with_edge():
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    data_store.record("ashare_review", "DAILY", {
        "date": today,
        "our_edge": [{
            "kind": "stock", "name": "中芯国际", "pct": 5.6, "lead_hours": 14.0,
            "reason": "盘前快讯提示产能涨价", "evidence": 2,
            "signals": [{"id": "n1", "url": "https://daocaijing.com/article/n1",
                         "title": "产能涨价快讯", "topic": "快讯", "lead": 14}],
        }],
    })


def test_track_record_page_facts_only_and_public(client):
    cli, _ = client
    _seed_review_with_edge()
    r = cli.get("/track-record")  # 匿名可看（非 /api 路径，中间件放行）
    assert r.status_code == 200
    page = r.text
    assert "提前覆盖" in page and "中芯国际" in page
    assert "提前 14 小时" in page and "产能涨价快讯" in page  # 明细：领先小时 + 佐证链接
    assert "不构成" in page  # 页脚/口径免责
    # ⚠️措辞铁律：无命中率/准确率/收益归因（变相宣传预测能力 = 投顾红线）
    for banned in ("命中率", "准确率", "帮你抓住", "%涨幅", "5.6"):
        assert banned not in page, banned


def test_track_record_in_sitemap(client):
    cli, _ = client
    r = cli.get("/sitemap.xml")
    assert r.status_code == 200 and "/track-record</loc>" in r.text
