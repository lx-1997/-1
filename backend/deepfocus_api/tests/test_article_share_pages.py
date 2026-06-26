"""文章分享落地页 + 深链 get-by-id 回归。

核心断言（软墙）：公开落地页只放标题+来源+短导语+「登录看全文」CTA，**第三方全文绝不泄漏**；
路由按 topic='文章' 守门；/api/realtime/messages/{id} 公开可取单条供深链定位。
"""
import pytest
from fastapi.testclient import TestClient

from deepfocus_api import data_store, seo_pages, realtime_messages as rm
from deepfocus_api.schemas import RealtimeMessageCreateRequest


# ── 渲染层（纯函数） ─────────────────────────────────────────
# 导语开头短、机密标记放在 120 字预览窗口之外（全文尾部）——用来证明只放导语、全文被挡。
_TAIL_MARKER = "【机密尾部不应出现在公开页】"


def _article(aid="a-1"):
    return {
        "id": aid,
        "title": "某公司发布重大利好公告",
        "content": "这是文章导语第一句。" + "中间正文段落。" * 60 + _TAIL_MARKER,
        "source_name": "DAO财经",
        "created_at": "2026-06-26T08:00:00Z",
        "topic": "文章",
    }


def test_article_page_soft_wall_no_fulltext_leak():
    a = _article()
    page = seo_pages.render_article_page_html(a, recent=[a], page_url="https://daocaijing.com/article/a-1")
    assert "某公司发布重大利好公告" in page          # 标题公开
    assert "这是文章导语第一句" in page               # 短导语公开（120字预览）
    assert _TAIL_MARKER not in page                   # ⭐ 导语之外的全文尾部不泄漏
    assert "登录 DeepFocus 看全文" in page            # 软墙 CTA
    assert "?article=a-1" in page                      # 登录深链
    assert "DeepFocus" in page                         # 对外署名 DeepFocus
    assert "DAO财经" not in page                        # ⭐ 内部聚合源名不外露(品牌红线)
    assert '"@type": "NewsArticle"' in page            # 结构化数据


def test_public_source_neutralizes_internal_names():
    assert seo_pages._public_source("DAO财经") == "DeepFocus"
    assert seo_pages._public_source("道财经") == "DeepFocus"
    assert seo_pages._public_source("") == "DeepFocus"
    assert seo_pages._public_source("Morgan Stanley") == "Morgan Stanley"  # 正经外部源保留


def test_article_page_escapes_content():
    a = _article()
    a["title"] = "<script>alert(1)</script>"
    page = seo_pages.render_article_page_html(a, recent=[])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_articles_hub_and_sitemap():
    hub = seo_pages.render_articles_hub_html([_article("a-9")])
    assert "/article/a-9" in hub and "财经资讯文章" in hub
    sm = seo_pages.render_sitemap_xml(["2026-06-26"], ["NVDA"], ["a-7"])
    assert "/article/a-7</loc>" in sm and "/articles</loc>" in sm


# ── HTTP 路由 ───────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_store.DB_PATH = tmp_path / "data.sqlite3"
    data_store.init_data_store()
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "rt.sqlite3")
    rm.init_realtime_message_db()
    from deepfocus_api import main as main_mod
    return TestClient(main_mod.app)


def _make_article(content="文章导语第一句。" + "中间正文段落。" * 60 + _TAIL_MARKER):
    return rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="重大资产重组获批", content=content, topic="文章",
        severity="info", source_name="DAO财经", tags=["文章"],
    ))


def test_article_route_serves_soft_wall(client):
    art = _make_article()
    r = client.get(f"/article/{art.id}")
    assert r.status_code == 200
    assert "重大资产重组获批" in r.text
    assert "登录 DeepFocus 看全文" in r.text
    assert _TAIL_MARKER not in r.text  # 全文尾部不泄漏


def test_article_route_404_for_nonarticle_and_missing(client):
    flash = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="一条快讯", content="x", topic="快讯", severity="info", tags=["快讯"]))
    assert client.get(f"/article/{flash.id}").status_code == 404   # 快讯不是文章
    assert client.get("/article/does-not-exist").status_code == 404


def test_articles_hub_route(client):
    _make_article()
    r = client.get("/articles")
    assert r.status_code == 200 and "重大资产重组获批" in r.text


def test_get_message_by_id_endpoint(client):
    art = _make_article()
    r = client.get(f"/api/realtime/messages/{art.id}")
    assert r.status_code == 200
    assert r.json()["id"] == art.id and r.json()["topic"] == "文章"
    assert client.get("/api/realtime/messages/nope").status_code == 404
    # 不能截胡字面量 SSE 流路由：/stream 须先于 /{id} 匹配（按声明顺序）。
    # 直接 GET /stream 会永久挂起（SSE），故只校验路由表里 stream 路由排在 {id} 之前。
    from deepfocus_api import main as main_mod
    paths = [getattr(rt, "path", "") for rt in main_mod.app.router.routes]
    assert paths.index("/api/realtime/messages/stream") < paths.index("/api/realtime/messages/{message_id}")


def test_sitemap_route_includes_articles(client):
    art = _make_article()
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and f"/article/{art.id}</loc>" in r.text
