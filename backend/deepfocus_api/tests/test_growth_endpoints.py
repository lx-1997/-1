"""增长端点冒烟测试（2026-07 批次）：快讯落地页 / 龙虎榜每日全榜 / 统一搜索 / 术语表索引。

外部取数全部 mock（东财 datacenter / 行情源不打真网），只验证路由契约与渲染纯函数。
"""
import pytest
from fastapi.testclient import TestClient

from deepfocus_api import data_store, seo_pages, realtime_messages as rm
from deepfocus_api.schemas import RealtimeMessageCreateRequest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_store.DB_PATH = tmp_path / "data.sqlite3"
    data_store.init_data_store()
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "rt.sqlite3")
    rm.init_realtime_message_db()
    from deepfocus_api import main as main_mod
    return TestClient(main_mod.app)


# ── 任务1：快讯公开落地页（承接「复制快讯」引流链接） ─────────────────
def test_flash_news_article_page_now_serves(client):
    flash = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="某芯片大厂获大额订单快讯", content="快讯正文第一句。" + "后续细节。" * 40,
        topic="快讯", severity="info", source_name="DAO财经", tags=["快讯"],
    ))
    r = client.get(f"/article/{flash.id}")
    assert r.status_code == 200
    assert "某芯片大厂获大额订单快讯" in r.text          # 标题公开
    assert "打开 DeepFocus 查看" in r.text             # 快讯软墙 CTA（站内免费可读，不承诺会员解锁）
    assert "会员读全文" not in r.text                   # ⭐ 快讯不是会员专享内容，不出现会员墙口径
    assert "DAO财经" not in r.text                        # 内部源名不外露（品牌红线）


def test_non_article_topic_still_404(client):
    other = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="一条复盘", content="x", topic="复盘", severity="info", tags=["复盘"]))
    assert client.get(f"/article/{other.id}").status_code == 404


# ── 任务2：龙虎榜每日全榜 ──────────────────────────────────
_BILLBOARD = {
    "date": "2026-07-02",
    "count": 2,
    "items": [
        {"code": "000725", "name": "京东方A", "change_rate": 3.7628,
         "reason": "日振幅值达到15%的前5只证券", "net": 1782691282.05},
        {"code": "002842", "name": "翔鹭钨业", "change_rate": 9.9978,
         "reason": "日涨幅偏离值达到7%的前5只证券", "net": -272226695.5},
    ],
    "provider": "eastmoney",
}


@pytest.fixture()
def mock_billboard(monkeypatch):
    from deepfocus_api import dragon_tiger

    async def _fake(date="", limit=100):
        if date and date != "2026-07-02":
            return None
        return _BILLBOARD

    monkeypatch.setattr(dragon_tiger, "fetch_daily_billboard", _fake)
    return _fake


def test_api_dragon_tiger_daily(client, mock_billboard):
    r = client.get("/api/dragon-tiger/daily")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["date"] == "2026-07-02"
    assert body["data"]["count"] == 2
    assert body["data"]["items"][0]["code"] == "000725"
    assert "不构成投资建议" in body["note"]
    # 非法日期 → 422
    assert client.get("/api/dragon-tiger/daily?date=20260702").status_code == 422


def test_lhb_public_pages(client, mock_billboard):
    r = client.get("/lhb")
    assert r.status_code == 200
    assert "2026-07-02 A股龙虎榜全榜单" in r.text
    assert "京东方A" in r.text and "翔鹭钨业" in r.text
    assert "上榜原因" in r.text and "净买额" in r.text
    assert "不构成投资建议" in r.text
    assert "游资看好" not in r.text  # 铁律：零解读
    # 按日期页
    assert client.get("/lhb/2026-07-02").status_code == 200
    # 非法日期 / 无数据日期 → 404（防无限薄页）
    assert client.get("/lhb/bad-date").status_code == 404
    assert client.get("/lhb/2000-01-01").status_code == 404


def test_render_lhb_page_html_pure():
    page = seo_pages.render_lhb_page_html(_BILLBOARD, page_url="https://daocaijing.com/lhb")
    assert "<table>" in page and "代码" in page and "涨跌幅" in page
    assert "+17.83 亿" in page          # 净买额（元）→ 亿可读格式
    assert "-2.72 亿" in page
    assert 'name="ai-generated"' not in page  # 确定性榜单：非 AI 生成
    empty = seo_pages.render_lhb_page_html(None)
    assert "暂无龙虎榜数据" in empty


def test_sitemap_includes_lhb():
    sm = seo_pages.render_sitemap_xml([], [])
    assert "/lhb</loc>" in sm


# ── 任务4：统一搜索 ─────────────────────────────────────
def test_universal_search_aggregates(client, monkeypatch):
    from deepfocus_api import main as main_mod, theme_navigation

    class _Cand:
        symbol, code, name, market = "600519", "600519", "贵州茅台", "CN"

    class _SearchRes:
        candidates = [_Cand()]

    async def _fake_symbols(q, market=None):
        return _SearchRes()

    class _Item:
        id, title, org, date, symbol = "AP1", "白酒行业深度", "某券商", "2026-07-01", "600519"

    class _ReportRes:
        items = [_Item()]

    async def _fake_research(keyword, market=None, page_size=20):
        return _ReportRes()

    async def _fake_boards():
        return [{"code": "BK0477", "name": "白酒"}, {"code": "BK9999", "name": "非白酒概念"}]

    monkeypatch.setattr(main_mod, "search_market_symbols", _fake_symbols)
    monkeypatch.setattr(main_mod, "api_research_search", _fake_research)
    monkeypatch.setattr(theme_navigation, "_board_name_pairs", _fake_boards)
    rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="白酒板块午后走强", content="x", topic="快讯", severity="info"))

    r = client.get("/api/search/universal?q=白酒")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"q", "stocks", "news", "reports", "terms", "boards"}
    assert body["stocks"][0]["name"] == "贵州茅台"
    assert any("白酒" in n["title"] for n in body["news"])
    assert body["reports"][0]["id"] == "AP1"
    assert body["boards"][0]["name"] == "白酒"  # 短名优先


def test_universal_search_degrades_per_lane(client, monkeypatch):
    """任一路失败只影响该路：股票路抛异常 → stocks=[]，其余照常返回。"""
    from deepfocus_api import main as main_mod, theme_navigation

    async def _boom(q, market=None):
        raise RuntimeError("provider down")

    async def _boom_pairs():
        raise RuntimeError("network down")

    monkeypatch.setattr(main_mod, "search_market_symbols", _boom)
    monkeypatch.setattr(main_mod, "api_research_search", _boom)
    monkeypatch.setattr(theme_navigation, "_board_name_pairs", _boom_pairs)
    r = client.get("/api/search/universal?q=市盈率")
    assert r.status_code == 200
    body = r.json()
    assert body["stocks"] == [] and body["reports"] == [] and body["boards"] == []
    assert body["terms"] and body["terms"][0]["slug"] == "pe-ratio"  # 术语路（纯内存）仍命中


def test_universal_search_empty_query(client):
    body = client.get("/api/search/universal").json()
    assert body == {"q": "", "stocks": [], "news": [], "reports": [], "terms": [], "boards": []}


# ── 任务6：术语表索引 ────────────────────────────────────
def test_glossary_index(client):
    from deepfocus_api.glossary import GLOSSARY

    r = client.get("/api/glossary/index")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(GLOSSARY) and len(body["items"]) == len(GLOSSARY)
    first = body["items"][0]
    assert set(first) == {"slug", "term", "aliases", "brief"}
    assert first["slug"] == "pe-ratio" and "市盈率" in first["term"]
    assert all(len(it["brief"]) <= 60 and it["brief"] for it in body["items"])
    assert all(isinstance(it["aliases"], list) for it in body["items"])


# ── 任务5（顺带）：自选巡检消息合成（确定性模板 + 合规尾巴） ─────────
def test_build_watchlist_scan_message(client, monkeypatch, mock_billboard):
    import asyncio

    from deepfocus_api import main as main_mod

    monkeypatch.setattr(main_mod, "_watchlist_union_symbols",
                        lambda cap=200: (["600519", "000725"], {"600519": "贵州茅台"}))

    class _Q:
        def __init__(self, pct, name):
            self.change_percent, self.name = pct, name

    class _QRes:
        def __init__(self, q):
            self.quotes = [q]

    async def _fake_quotes(symbols, ifind_user=False):
        sym = list(symbols)[0]
        return _QRes(_Q(6.2 if sym == "600519" else 1.0, "贵州茅台" if sym == "600519" else "京东方A"))

    monkeypatch.setattr(main_mod, "fetch_market_quotes", _fake_quotes)

    req = asyncio.get_event_loop().run_until_complete(
        main_mod._build_watchlist_scan_message("2026-07-02"))  # 2 只 × 0.3s 限流间隔，可接受
    assert req is not None and req.topic == "异动" and req.symbol is None
    assert "自选股今日异动 · 2 只" in req.title            # 涨幅命中 + 龙虎榜命中
    assert "贵州茅台(600519) 收盘 +6.2%" in req.content
    assert "京东方A(000725) 登上龙虎榜" in req.content and "万元" in req.content
    assert req.content.rstrip().endswith("（不构成投资建议）")
    for banned in ("建议关注", "买入", "看好"):
        assert banned not in req.content  # 纯事实模板，零方向性措辞
