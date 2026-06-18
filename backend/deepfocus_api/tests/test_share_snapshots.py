from deepfocus_api import share_snapshots as share
from deepfocus_api.schemas import ShareSnapshotCreateRequest


def _use_temp_db(tmp_path):
    share.DB_PATH = tmp_path / "share.sqlite3"


def test_create_and_get_roundtrip(tmp_path):
    _use_temp_db(tmp_path)
    record = share.create_share_snapshot(
        ShareSnapshotCreateRequest(
            title="NVDA 投研体检", summary="数据中心高增。\n估值偏上。", byline="由 DeepFocus 生成"
        )
    )
    assert record.id and record.views == 0
    got = share.get_share_snapshot(record.id)
    assert got is not None and got.title == "NVDA 投研体检"
    assert share.get_share_snapshot("nope") is None


def test_html_page_has_meta_and_content(tmp_path):
    _use_temp_db(tmp_path)
    record = share.create_share_snapshot(
        ShareSnapshotCreateRequest(
            title="特斯拉结论", summary="第一段。\n第二段。", byline="由 DeepFocus 生成"
        )
    )
    page = share.render_share_page_html(record)
    assert "<title>特斯拉结论" in page
    assert 'property="og:title" content="特斯拉结论"' in page
    assert 'name="description"' in page
    assert 'name="robots" content="index,follow"' in page
    assert "<p>第一段。</p>" in page and "<p>第二段。</p>" in page
    assert "由 DeepFocus 生成" in page
    # schema.org 结构化数据（富搜索结果）
    assert 'application/ld+json' in page
    assert '"@type": "Article"' in page


def test_html_page_url_adds_canonical_and_jsonld_url(tmp_path):
    _use_temp_db(tmp_path)
    record = share.create_share_snapshot(ShareSnapshotCreateRequest(title="标的结论", summary="要点。"))
    url = "https://deepfocus.example/s/" + record.id
    page = share.render_share_page_html(record, page_url=url)
    assert f'<link rel="canonical" href="{url}">' in page
    assert f'<meta property="og:url" content="{url}">' in page
    assert url in page  # JSON-LD 内也带 url
    # 无 page_url 时不输出 canonical
    assert '<link rel="canonical"' not in share.render_share_page_html(record)


def test_html_escapes_user_content(tmp_path):
    _use_temp_db(tmp_path)
    record = share.create_share_snapshot(
        ShareSnapshotCreateRequest(title="<script>alert(1)</script>", summary="x & y <b>z</b>")
    )
    page = share.render_share_page_html(record)
    assert "<script>alert(1)</script>" not in page  # 原样脚本不得注入
    assert "&lt;script&gt;" in page
    assert "x &amp; y" in page
