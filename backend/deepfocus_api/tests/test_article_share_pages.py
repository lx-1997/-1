"""文章分享落地页 + 深链 get-by-id 回归。

核心断言（软墙）：公开落地页只放标题+来源+短导语+「会员读全文」CTA，**第三方全文绝不泄漏**；
路由按 topic='文章' 守门；/api/realtime/messages/{id} 公开可取单条供深链定位，
但文章全文为会员专享（2026-08-07）：匿名/非会员只回 ≤120 字导语 + 会员锁定标记。
"""
import asyncio
from datetime import datetime, timedelta, timezone

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
    assert "打开 DeepFocus · 会员读全文" in page        # 软墙 CTA（全文会员专享，2026-08-07）
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


def _make_futou_article():
    return rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="受限来源文章", content="受限文章正文", topic="文章",
        severity="info", source_name="DAO财经", source_id="88001",
        source_type="dao-article", url="https://backend.futoucaixin.cn/a/88001",
        tags=["文章"],
    ))


def _make_futou_flash():
    return rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="受限来源快讯", content="受限快讯正文", topic="快讯",
        severity="info", source_name="DAO财经", source_id="lxaa88002",
        source_type="dao-news", tags=["快讯"],
    ))


def _make_normal_flash():
    return rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="普通来源快讯", content="普通快讯正文", topic="快讯",
        severity="info", source_name="其他财经", source_id="wire-88003",
        source_type="wire-news", tags=["快讯"],
    ))


def test_article_route_serves_soft_wall(client):
    art = _make_article()
    r = client.get(f"/article/{art.id}")
    assert r.status_code == 200
    assert "重大资产重组获批" in r.text
    assert "打开 DeepFocus · 会员读全文" in r.text
    assert _TAIL_MARKER not in r.text  # 全文尾部不泄漏


def test_article_route_404_for_nonarticle_and_missing(client):
    # 快讯自 2026-07 起放行（复制快讯的引流链接就是 /article/{id}），其它 topic 仍 404。
    other = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="一条晨报", content="x", topic="晨报", severity="info", tags=["晨报"]))
    assert client.get(f"/article/{other.id}").status_code == 404   # 非 文章/快讯 topic 不放行
    assert client.get("/article/does-not-exist").status_code == 404


def test_articles_hub_route(client):
    _make_article()
    r = client.get("/articles")
    assert r.status_code == 200 and "重大资产重组获批" in r.text


def test_get_message_by_id_endpoint(client):
    art = _make_article()
    r = client.get(f"/api/realtime/messages/{art.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == art.id and body["topic"] == "文章"
    # ⭐ 文章会员墙（2026-08-07）：匿名取单条只回导语+锁定标记，全文尾部不泄漏
    assert _TAIL_MARKER not in body["content"]
    assert "全文为会员专享内容" in body["content"]
    assert "文章导语第一句" in body["content"]  # 导语仍给（先展示后要账）
    assert client.get("/api/realtime/messages/nope").status_code == 404
    # 不能截胡字面量 SSE 流路由：/stream 须先于 /{id} 匹配（按声明顺序）。
    # 直接 GET /stream 会永久挂起（SSE），故只校验路由表里 stream 路由排在 {id} 之前。
    from deepfocus_api import main as main_mod
    paths = [getattr(rt, "path", "") for rt in main_mod.app.router.routes]
    assert paths.index("/api/realtime/messages/stream") < paths.index("/api/realtime/messages/{message_id}")


def test_list_endpoint_member_wall_anonymous(client):
    """列表端点同口径：匿名拉流，文章正文被裁成导语+锁标；快讯不受会员墙影响（全文照给）。"""
    _make_article()
    flash_content = "央行宣布降准 0.5 个百分点，释放长期资金约一万亿元。"
    rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="央行降准", content=flash_content, topic="快讯",
        severity="info", source_name="DAO财经", tags=["快讯"],
    ))
    r = client.get("/api/realtime/messages")
    assert r.status_code == 200
    msgs = {m["topic"]: m for m in r.json()["messages"]}
    assert _TAIL_MARKER not in msgs["文章"]["content"]
    assert "全文为会员专享内容" in msgs["文章"]["content"]
    assert msgs["快讯"]["content"] == flash_content  # 快讯全文不受影响


def test_anonymous_cannot_see_futou_messages(client, monkeypatch):
    """匿名的列表、单条深链、公开落地页和头条全部隐藏；普通来源仍可见。"""
    article = _make_futou_article()
    flash = _make_futou_flash()
    normal = _make_normal_flash()

    listed = {m["id"] for m in client.get("/api/realtime/messages", params={"limit": 20}).json()["messages"]}
    assert article.id not in listed and flash.id not in listed
    assert normal.id in listed
    assert client.get(f"/api/realtime/messages/{article.id}").status_code == 404
    assert client.get(f"/api/realtime/messages/{flash.id}").status_code == 404
    assert client.get(f"/article/{article.id}").status_code == 404
    assert client.get(f"/article/{flash.id}").status_code == 404

    from deepfocus_api import main as main_mod
    monkeypatch.setattr(main_mod, "_HEADLINES", {
        "kx": [main_mod._hl_pack_msg(flash, "restricted"), main_mod._hl_pack_msg(normal, "normal")],
        "wz": [main_mod._hl_pack_msg(article, "restricted")],
        "yb": [], "generated_at": "now",
    })
    headlines = client.get("/api/headlines").json()
    assert [m["id"] for m in headlines["kx"]] == [normal.id]
    assert headlines["wz"] == []
    assert article.id not in client.get("/sitemap.xml").text
    assert article.id not in client.get("/feed.xml").text
    assert article.id not in client.get("/articles").text

    async def _empty(*_args, **_kwargs):
        return []

    for name in ("_usearch_stocks", "_usearch_reports", "_usearch_terms", "_usearch_boards"):
        monkeypatch.setattr(main_mod, name, _empty)
    search = client.get("/api/search/universal", params={"q": "受限来源"}).json()
    assert search["news"] == []


def test_exclude_futou_query_keeps_full_limit(client):
    """SQL 层过滤要先于 LIMIT，不能因最新受限消息占满窗口而返回空页。"""
    normal = _make_normal_flash()
    for i in range(5):
        rm.create_realtime_message(RealtimeMessageCreateRequest(
            title=f"受限快讯 {i}", content="x", topic="快讯", severity="info",
            source_id=f"lxaa99{i}", source_type="dao-news",
        ))
    rows = rm.list_realtime_messages(exclude_futoucaixin=True, limit=1)
    assert [m.id for m in rows] == [normal.id]


def test_filtered_latest_hides_stale_flash_but_keeps_history_search(client):
    """匿名最新流不把超过 72h 的普通源快讯伪装成「最新」；
    只修复最新流的语义，明确的历史搜索仍可取回。
    """
    stale = _make_normal_flash()
    stale_created = (datetime.now(timezone.utc) - timedelta(hours=96)).isoformat()
    with rm._connect() as conn:
        conn.execute("UPDATE realtime_messages SET created_at=? WHERE id=?", (stale_created, stale.id))
        conn.commit()

    latest = client.get("/api/realtime/messages", params={"topic": "快讯", "limit": 20})
    assert latest.status_code == 200
    assert stale.id not in {m["id"] for m in latest.json()["messages"]}

    history = client.get("/api/realtime/messages", params={"topic": "快讯", "q": "普通来源", "limit": 20})
    assert history.status_code == 200
    assert stale.id in {m["id"] for m in history.json()["messages"]}


def test_sse_transform_can_drop_restricted_message(client):
    restricted = _make_futou_flash()
    normal = _make_normal_flash()

    class _Request:
        async def is_disconnected(self):
            return False

    async def _run():
        stream = rm.realtime_message_event_stream(
            _Request(),
            transform=lambda message: None if rm.is_futoucaixin_message(message) else message,
        )
        try:
            assert "event: connected" in await stream.__anext__()
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.sleep(0)
            rm._broadcast_message(restricted)
            await asyncio.sleep(0.02)
            assert not pending.done()
            rm._broadcast_message(normal)
            event = await asyncio.wait_for(pending, timeout=1)
            assert normal.id in event and restricted.id not in event
        finally:
            await stream.aclose()

    asyncio.run(_run())


@pytest.fixture()
def member_client(tmp_path, monkeypatch):
    """client + 独立 auth 库：用于「会员带 token 解锁全文」正向路径。"""
    data_store.DB_PATH = tmp_path / "data.sqlite3"
    data_store.init_data_store()
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "rt.sqlite3")
    rm.init_realtime_message_db()
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'auth.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    from deepfocus_api import storage, auth as auth_mod
    storage.reset_engine_for_tests()
    auth_mod.init_auth()
    from deepfocus_api import main as main_mod
    yield TestClient(main_mod.app)
    storage.reset_engine_for_tests()


def test_member_unlocks_full_article(member_client):
    """正向路径：付费会员带 token 取单条 → 全文放行、无锁定标记。"""
    from deepfocus_api import auth as auth_mod
    c = member_client
    art = _make_article()
    r = c.post("/api/auth/register", json={"username": "vipreader", "password": "password1", "email": "vip@firm.com"})
    assert r.status_code == 200, r.text
    auth_mod.grant_membership("vipreader", days=30, source="paid")
    tok = c.post("/api/auth/login", json={"username": "vipreader", "password": "password1"}).json()["access_token"]
    r = c.get(f"/api/realtime/messages/{art.id}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert _TAIL_MARKER in body["content"]              # ⭐ 会员拿到完整全文
    assert "全文为会员专享内容" not in body["content"]   # 无锁定标记


def test_dao2_is_restricted_but_regular_user_can_read(member_client, monkeypatch):
    c = member_client
    article = _make_futou_article()
    flash = _make_futou_flash()
    normal = _make_normal_flash()

    dao2 = c.post("/api/auth/register", json={
        "username": "dao2", "password": "password1", "email": "dao2@example.com",
    })
    assert dao2.status_code == 200, dao2.text
    dao2_token = dao2.json()["access_token"]
    dao2_headers = {"Authorization": f"Bearer {dao2_token}"}
    dao2_ids = {
        m["id"] for m in c.get("/api/realtime/messages", headers=dao2_headers).json()["messages"]
    }
    assert article.id not in dao2_ids and flash.id not in dao2_ids
    assert normal.id in dao2_ids
    assert c.get(f"/api/realtime/messages/{article.id}", headers=dao2_headers).status_code == 404

    from deepfocus_api import main as main_mod
    monkeypatch.setattr(main_mod, "_HEADLINES", {
        "kx": [main_mod._hl_pack_msg(flash, "restricted"), main_mod._hl_pack_msg(normal, "normal")],
        "wz": [main_mod._hl_pack_msg(article, "restricted")],
        "yb": [], "generated_at": "now",
    })
    dao2_heads = c.get("/api/headlines", headers=dao2_headers).json()
    assert [m["id"] for m in dao2_heads["kx"]] == [normal.id]
    assert dao2_heads["wz"] == []

    reader = c.post("/api/auth/register", json={
        "username": "reader", "password": "password1", "email": "reader@example.com",
    })
    assert reader.status_code == 200, reader.text
    reader_headers = {"Authorization": f"Bearer {reader.json()['access_token']}"}
    reader_ids = {
        m["id"] for m in c.get("/api/realtime/messages", headers=reader_headers).json()["messages"]
    }
    assert article.id in reader_ids and flash.id in reader_ids and normal.id in reader_ids
    assert c.get(f"/api/realtime/messages/{article.id}", headers=reader_headers).status_code == 200
    reader_heads = c.get("/api/headlines", headers=reader_headers).json()
    assert [m["id"] for m in reader_heads["kx"]] == [flash.id, normal.id]
    assert [m["id"] for m in reader_heads["wz"]] == [article.id]


def test_sitemap_route_includes_articles(client):
    art = _make_article()
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and f"/article/{art.id}</loc>" in r.text
