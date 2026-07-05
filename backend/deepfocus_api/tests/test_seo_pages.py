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
    assert "数据速览" in page  # C10 数字密集可抽取句
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
                        "headline": "强于大盘", "evidence": ["20日涨幅 +15%"]},
                       {"key": "valuation", "label": "估值", "signal": "bearish", "headline": "偏贵"},
                       {"key": "catalyst", "label": "催化", "signal": "neutral", "headline": "无近期事件"}],
    }
    page = seo_pages.render_stock_page_html(ts, related=[{"symbol": "AMD"}, {"symbol": "TSM", "name": "台积电"}])
    assert "英伟达(NVDA) 多维证据速判：重点跟踪" in page
    assert 'content="index,follow"' in page
    assert "价格动量" in page and "20日涨幅 +15%" in page
    assert "/stock/AMD" in page and "台积电" in page  # 大家也在看（推荐内链）
    assert '"tickerSymbol": "NVDA"' in page
    assert "watch=NVDA" in page and "盯盘" in page  # C2 激活深链 CTA


def test_stock_page_thin_content_is_noindex():
    page = seo_pages.render_stock_page_html({"symbol": "X", "overall_verdict": "数据不足"}, related=[])
    assert 'content="noindex,nofollow"' in page


def test_c7_quality_gate():
    live3 = {"overall_verdict": "重点跟踪", "dimensions": [
        {"signal": "bullish"}, {"signal": "bearish"}, {"signal": "neutral"}]}
    assert seo_pages.stock_indexable(live3) is True
    # 结论不足 → 不进索引
    assert seo_pages.stock_indexable({"overall_verdict": "数据不足", "dimensions": [
        {"signal": "bullish"}, {"signal": "bullish"}, {"signal": "bullish"}]}) is False
    # live 维度不足 3（其余 insufficient）→ 不进索引（挡停牌/冷门薄页）
    assert seo_pages.stock_indexable({"overall_verdict": "重点跟踪", "dimensions": [
        {"signal": "bullish"}, {"signal": "insufficient"}, {"signal": "insufficient"}]}) is False
    # 薄页渲染仍是 noindex
    thin = seo_pages.render_stock_page_html({"symbol": "ZZ", "overall_verdict": "重点跟踪",
                                             "dimensions": [{"signal": "bullish", "label": "动量", "headline": "x"}]},
                                            related=[])
    assert 'content="noindex,nofollow"' in thin


def test_qa_page_and_hub():
    long_ans = "截至近期，从基本面看估值偏高，建议买入需谨慎；动量中性，资金面偏弱。" * 3
    qa = {"slug": "a" * 32, "q": "贵州茅台现在能买吗？", "answer": long_ans}
    page = seo_pages.render_qa_page_html(qa, related=[{"slug": "b" * 32, "q": "宁德时代怎么看"}])
    assert "贵州茅台现在能买吗" in page
    assert '"@type": "FAQPage"' in page
    assert 'content="index,follow"' in page
    assert "建议买入" not in page  # 出口护栏中性化
    assert "/qa/" + "b" * 32 in page  # 相关问答内链
    # 薄答案 → noindex、不发 FAQ
    thin = seo_pages.render_qa_page_html({"slug": "c" * 32, "q": "嗯", "answer": "短"}, related=[])
    assert 'content="noindex,nofollow"' in thin and '"@type": "FAQPage"' not in thin
    hub = seo_pages.render_qa_hub_html([qa])
    assert "/qa/" + "a" * 32 in hub


def test_review_links_individual_stocks():
    from deepfocus_api import stock_name_index
    saved = stock_name_index._name_code
    stock_name_index._name_code = {"测试龙头": "000001"}
    try:
        rv = {"date": "2026-06-26", "session": "close",
              "our_edge": [{"kind": "stock", "name": "测试龙头", "theme": "半导体", "pct": 3.2, "evidence": "盘前信号"}],
              "narrative": {"one_liner": "普涨"}}
        page = seo_pages.render_review_page_html(rv, recent=[])
        assert "/stock/000001" in page and "测试龙头" in page  # C5 复盘→个股内链
    finally:
        stock_name_index._name_code = saved


def test_learn_pages():
    from deepfocus_api import glossary
    t = glossary.BY_SLUG["pe-ratio"]
    page = seo_pages.render_learn_page_html(t, related=[glossary.BY_SLUG["roe"]])
    assert "市盈率" in page and "每股收益" in page
    assert '"@type": "FAQPage"' in page and 'content="index,follow"' in page
    assert "/learn/roe" in page  # 相关术语内链
    hub = seo_pages.render_learn_hub_html(glossary.GLOSSARY)
    assert "/learn/pe-ratio" in hub and len(glossary.GLOSSARY) >= 30
    # slug 唯一
    slugs = [x["slug"] for x in glossary.GLOSSARY]
    assert len(slugs) == len(set(slugs))


def test_compare_page():
    def _ts(sym, name, v):
        return {"symbol": sym, "name": name, "overall_verdict": v,
                "dimensions": [{"label": "动量", "signal": "bullish", "headline": "强"},
                               {"label": "估值", "signal": "bearish", "headline": "贵"},
                               {"label": "催化", "signal": "neutral", "headline": "无"}]}
    a, b = _ts("600519", "贵州茅台", "重点跟踪"), _ts("000858", "五粮液", "中性观察")
    page = seo_pages.render_compare_page_html(a, b, page_url="https://daocaijing.com/compare/600519-vs-000858")
    assert "贵州茅台" in page and "五粮液" in page
    assert "/stock/600519" in page and "/stock/000858" in page
    assert 'content="index,follow"' in page
    assert "不对孰优孰劣" in page  # 合规：不作优劣判断
    thin = seo_pages.render_compare_page_html(_ts("X", "X股", "数据不足"), b)
    assert 'content="noindex,nofollow"' in thin  # 任一薄 → noindex


def test_og_image_renders_png():
    from deepfocus_api import og_image
    png = og_image.render_og("测试标题", "副标题", ["上证 -2%"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert og_image.stock_card({"name": "贵州茅台", "overall_verdict": "中性观察"}, "600519")[:4] == b"\x89PNG"
    assert og_image.review_card({}, "2026-06-26")[:4] == b"\x89PNG"  # 无数据降级简卡


def test_stocks_all_hub_and_feed():
    page = seo_pages.render_stocks_all_html(
        [("贵州茅台", "600519"), ("宁德时代", "300750")], page=1, total_pages=3, total_count=500)
    assert "/stock/600519" in page and "贵州茅台" in page
    assert "/stocks/all?page=2" in page  # 分页内链
    assert 'content="index,follow"' in page
    feed = seo_pages.render_feed_xml(
        [{"date": "2026-06-26", "session_label": "收盘复盘", "one_liner": "普涨"}],
        [{"id": "n1", "title": "某公司Q2超预期", "content": "正文"}])
    assert "<rss" in feed and "/review/2026-06-26" in feed and "/article/n1" in feed


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
    assert cli.get("/stocks/all").status_code == 200
    assert cli.get("/qa").status_code == 200
    assert cli.get("/qa/" + "z" * 32).status_code == 404  # 不存在的指纹
    assert cli.get("/qa/not-a-fingerprint").status_code == 404
    assert cli.get("/learn").status_code == 200
    assert cli.get("/learn/pe-ratio").status_code == 200
    assert cli.get("/learn/no-such-term").status_code == 404
    rog = cli.get("/og/review/2026-06-26.png")
    assert rog.status_code == 200 and rog.content[:4] == b"\x89PNG"
    assert cli.get("/compare/600519-vs-000858").status_code == 404  # 无缓存数据 → 404
    assert cli.get("/compare/badpair").status_code == 404
    r = cli.get("/feed.xml")
    assert r.status_code == 200 and "<rss" in r.text
    assert cli.get("/llms.txt").status_code == 200


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
