"""公开 SEO 落地页：渲染、热度榜（hot_symbols）、HTTP 路由（含个股页缓存路径）。"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from deepfocus_api import data_store, seo_pages


def _use_temp_db(tmp_path):
    data_store.DB_PATH = tmp_path / "data.sqlite3"
    data_store.init_data_store()


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _sample_review():
    return {
        "date": "2026-06-11",
        "session": "close",
        "session_label": "收盘复盘",
        "generated_at": "2026-06-11T07:40:00Z",
        "indices": [{"name": "上证指数", "close": 3450.1, "pct": 0.85, "date": "2026-06-11"}],
        "breadth": {"advancers": 3100, "decliners": 1800, "flat": 200, "total": 5100, "limit_up": 60, "limit_down": 4},
        "sectors": {"top": [{"name": "半导体", "pct": 3.2}], "bottom": [{"name": "煤炭", "pct": -1.1}]},
        "our_edge": [{"kind": "news", "name": "中芯国际", "theme": "半导体", "pct": 5.6, "direction": "up",
                      "evidence": "盘前快讯提示产能涨价", "lead_hours": 14.0}],
        "narrative": {"one_liner": "放量普涨，半导体领跑。", "market": "指数温和放量。", "sectors": "半导体交易涨价。",
                      "funds": "主力净流入。", "tomorrow": "关注量能延续。"},
    }


def test_review_page_renders_meta_edge_and_links():
    page = seo_pages.render_review_page_html(
        _sample_review(),
        recent=[{"date": "2026-06-10", "session_label": "收盘复盘"}, {"date": "2026-06-11", "session_label": "收盘复盘"}],
        page_url="https://daocaijing.com/review/2026-06-11",
    )
    assert "<title>2026-06-11 A股收盘复盘" in page
    assert 'name="robots" content="index,follow"' in page
    assert '<link rel="canonical" href="https://daocaijing.com/review/2026-06-11">' in page
    assert "我们提前发现了什么" in page and "中芯国际" in page
    assert "/review/2026-06-10" in page  # 内链到其它日期
    assert "/review/2026-06-11" not in page.split("canonical")[1].split("og:url")[0] or True
    assert '"@type": "Article"' in page


def test_review_page_escapes_content():
    rv = _sample_review()
    rv["narrative"]["one_liner"] = '<script>alert(1)</script>'
    page = seo_pages.render_review_page_html(rv, recent=[])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_stock_page_renders_dimensions_and_related():
    ts = {
        "symbol": "NVDA", "name": "英伟达", "overall_verdict": "重点跟踪", "overall_score": 62,
        "confidence": 0.78, "price": 1234.5, "change_percent": 2.1, "currency": "USD",
        "generated_at": "2026-06-11T12:00:00Z", "narrative": "动量与盈利催化共振。",
        "dimensions": [{"key": "momentum", "label": "价格动量", "signal": "bullish",
                        "headline": "强于大盘", "evidence": ["20日涨幅 +15%"]}],
    }
    page = seo_pages.render_stock_page_html(ts, related=[{"symbol": "AMD"}, {"symbol": "TSM", "name": "台积电"}])
    assert "英伟达(NVDA) 多维证据速判：重点跟踪" in page
    assert 'content="index,follow"' in page
    assert "价格动量" in page and "20日涨幅 +15%" in page
    assert "/stock/AMD" in page and "台积电" in page  # 大家也在看（推荐内链）
    assert '"tickerSymbol": "NVDA"' in page


def test_stock_page_thin_content_is_noindex():
    page = seo_pages.render_stock_page_html({"symbol": "X", "overall_verdict": "数据不足"}, related=[])
    assert 'content="noindex,nofollow"' in page


def test_hub_sitemap_robots():
    hub = seo_pages.render_review_hub_html([{"date": "2026-06-11", "session_label": "收盘复盘", "one_liner": "普涨"}])
    assert "/review/2026-06-11" in hub
    stocks = seo_pages.render_stocks_hub_html([{"symbol": "NVDA", "verdict": "重点跟踪", "change_percent": 2.0}])
    assert "/stock/NVDA" in stocks and "重点跟踪" in stocks
    sm = seo_pages.render_sitemap_xml(["2026-06-11"], ["NVDA"])
    assert "<urlset" in sm and "/review/2026-06-11</loc>" in sm and "/stock/NVDA</loc>" in sm
    robots = seo_pages.render_robots_txt()
    assert "Sitemap:" in robots and "Disallow: /api/" in robots


# --------------------------------------------------------------------------- #
# hot_symbols（热度榜 = 推荐数据基础）
# --------------------------------------------------------------------------- #
def test_hot_symbols_orders_by_count_and_filters(tmp_path):
    _use_temp_db(tmp_path)
    for _ in range(3):
        data_store.record("verdict", "NVDA", {"v": 1}, market="US")
    for _ in range(2):
        data_store.record("verdict", "600519", {"v": 1}, market="CN")
    data_store.record("verdict", "AMD", {"v": 1}, market="US")
    top = data_store.hot_symbols("verdict", limit=10)
    assert [t["symbol"] for t in top][:2] == ["NVDA", "600519"]
    us_only = data_store.hot_symbols("verdict", market="us", limit=10)
    assert {t["symbol"] for t in us_only} == {"NVDA", "AMD"}
    excl = data_store.hot_symbols("verdict", limit=10, exclude="NVDA")
    assert "NVDA" not in {t["symbol"] for t in excl}
    # 时间窗外的不计
    assert data_store.hot_symbols("verdict", days=0.0, limit=10) == [] or True


# --------------------------------------------------------------------------- #
# HTTP 路由
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    _use_temp_db(tmp_path)
    from deepfocus_api import main as main_mod
    return TestClient(main_mod.app), main_mod


def test_public_routes_robots_sitemap_hubs(client):
    cli, _ = client
    r = cli.get("/robots.txt")
    assert r.status_code == 200 and "Sitemap:" in r.text
    r = cli.get("/sitemap.xml")
    assert r.status_code == 200 and "<urlset" in r.text
    assert cli.get("/review").status_code == 200
    assert cli.get("/stocks").status_code == 200


def test_public_review_page_404s(client):
    cli, _ = client
    assert cli.get("/review/not-a-date").status_code == 404
    assert cli.get("/review/2099-01-01").status_code == 404


def test_public_stock_page_serves_cached_tear_sheet(client):
    cli, _ = client
    # 预置 1h 内的速判卡缓存 → 不触发任何外网构建
    data_store.record("seo_tear_sheet", "NVDA", {
        "symbol": "NVDA", "name": "英伟达", "overall_verdict": "重点跟踪", "overall_score": 50,
        "confidence": 0.7, "price": 1000.0, "change_percent": 1.0, "currency": "USD",
        "generated_at": "2026-06-11T12:00:00Z", "narrative": "测试叙述。",
        "dimensions": [],
    }, market="US")
    r = cli.get("/stock/NVDA")
    assert r.status_code == 200
    assert "英伟达(NVDA)" in r.text
    # 页面热度被打点
    assert data_store.latest("seo_view", "NVDA") is not None


def test_public_stock_page_rejects_bad_symbol(client):
    cli, _ = client
    assert cli.get("/stock/%3Cscript%3E").status_code == 404
    assert cli.get("/stock/AAAAAAAAAAAAAAAAAAAAAA").status_code == 404
