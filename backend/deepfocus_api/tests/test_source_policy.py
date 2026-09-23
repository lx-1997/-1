from deepfocus_api import source_policy
from deepfocus_api import realtime_messages as rm
from deepfocus_api.schemas import RealtimeMessageCreateRequest


def test_lxaa_identifier_is_tradealpha_not_futou(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")

    assert source_policy.is_tradealpha_source(source_id="lxaa123")
    assert not source_policy.is_futoucaixin_source(source_id="lxaa123")


def test_tradealpha_explicit_name_or_domain_is_blocked(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")

    assert source_policy.is_tradealpha_source(source_name="TradeAlpha")
    assert source_policy.is_tradealpha_source(url="https://alpha.lxaa.top/news/1")
    assert source_policy.is_tradealpha_source(
        source_id="lxaa123",
        metadata={"upstream_source_name": "Trade Alpha"},
    )
    assert source_policy.is_tradealpha_source(metadata={"source": {"name": "TradeAlpha"}})


def test_futou_url_is_retained_and_not_tradealpha():
    assert source_policy.is_futoucaixin_source(url="https://backend.futoucaixin.cn/a.pdf")
    assert not source_policy.is_tradealpha_source(url="https://backend.futoucaixin.cn/a.pdf")


def test_create_rejects_lxaa_but_keeps_futou(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "messages.sqlite3")

    blocked = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="TradeAlpha 快讯",
        content="市场消息",
        source_name="TradeAlpha",
        topic="快讯",
    ))
    retained = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="富途快讯",
        content="市场消息",
        source_id="7128",
        source_name="DAO财经",
        url="https://backend.futoucaixin.cn/uploads/article.pdf",
        topic="快讯",
    ))

    assert blocked is None
    assert retained is not None
    assert len(rm.list_realtime_messages(limit=10)) == 1


def test_futou_visibility_filter_does_not_confuse_lxaa(monkeypatch, tmp_path):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "messages.sqlite3")
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "0")
    lxaa = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="lxaa 存量快讯",
        content="历史内容",
        source_id="lxaa704036",
        source_name="DAO财经",
        topic="快讯",
    ))
    futou = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="富途文章",
        content="正文",
        source_id="7128",
        source_name="DAO财经",
        url="https://backend.futoucaixin.cn/uploads/a.pdf",
        topic="文章",
    ))
    assert lxaa is not None and futou is not None

    # 已登录视图：去掉 lxaa，保留真富途。
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")
    visible = rm.list_realtime_messages(limit=10, exclude_futoucaixin=False)
    assert [item.id for item in visible] == [futou.id]

    # 匿名/受限视图：两者都不可见。
    assert rm.list_realtime_messages(limit=10, exclude_futoucaixin=True) == []


def test_existing_tradealpha_rows_are_hidden_from_default_queries(monkeypatch, tmp_path):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "messages.sqlite3")
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "0")
    legacy = rm.create_realtime_message(RealtimeMessageCreateRequest(
        title="旧 TradeAlpha 快讯",
        content="历史内容",
        source_name="TradeAlpha",
        url="https://alpha.lxaa.top/news/old",
        topic="快讯",
    ))
    assert legacy is not None

    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")
    assert rm.list_realtime_messages(limit=10) == []
    assert rm.list_realtime_messages(limit=10, exclude_tradealpha=False)[0].id == legacy.id
