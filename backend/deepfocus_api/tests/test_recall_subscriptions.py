from deepfocus_api import recall_subscriptions as recall
from deepfocus_api.schemas import (
    RealtimeMessageRecord,
    RecallSubscriptionCreateRequest,
    RecallSubscriptionRecord,
)


def _use_temp_db(tmp_path):
    recall.DB_PATH = tmp_path / "recall.sqlite3"
    recall._recent_deliveries.clear()


def _msg(symbol="TSLA", severity="warning"):
    return RealtimeMessageRecord(
        id=f"m-{symbol}-{severity}",
        title="股价异动",
        content="盘中快速拉升",
        topic="external-push",
        severity=severity,
        symbol=symbol,
        created_at="2026-06-04T00:00:00Z",
    )


def _sub(**overrides):
    base = dict(
        id="s1",
        channel="email",
        address="me@example.com",
        symbols=["TSLA"],
        severities=["warning", "critical"],
        scope="watchlist",
        label=None,
        active=True,
        created_at="2026-06-04T00:00:00Z",
    )
    base.update(overrides)
    return RecallSubscriptionRecord(**base)


def test_subscription_crud_normalizes_and_roundtrips(tmp_path):
    _use_temp_db(tmp_path)
    created = recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel="email",
            address="  me@example.com ",
            symbols=["tsla", "TSLA", "nvda"],
            severities=["critical"],
            scope="watchlist",
        )
    )
    assert created.address == "me@example.com"
    assert created.symbols == ["TSLA", "NVDA"]  # 去重 + 大写
    assert created.severities == ["critical"]

    listed = recall.list_recall_subscriptions()
    assert len(listed) == 1 and listed[0].id == created.id

    assert recall.delete_recall_subscription(created.id) is True
    assert recall.list_recall_subscriptions() == []
    assert recall.delete_recall_subscription("nope") is False


def test_subscription_matches_scope_and_severity():
    sub = _sub()
    assert recall.subscription_matches(sub, _msg("TSLA", "warning")) is True
    assert recall.subscription_matches(sub, _msg("NVDA", "warning")) is False  # 非关注标的
    assert recall.subscription_matches(sub, _msg("TSLA", "info")) is False     # 级别未命中

    sub_all = _sub(scope="all", symbols=[])
    assert recall.subscription_matches(sub_all, _msg("ANYTHING", "warning")) is True
    assert recall.subscription_matches(sub_all, _msg("ANYTHING", "info")) is False


def test_dispatch_email_degrades_to_skipped_without_smtp(tmp_path, monkeypatch):
    _use_temp_db(tmp_path)
    monkeypatch.delenv("DEEPFOCUS_SMTP_HOST", raising=False)
    recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel="email", address="me@example.com", symbols=["TSLA"], severities=["warning"], scope="watchlist"
        )
    )

    matched = recall.dispatch_recall(_msg("TSLA", "warning"))
    assert matched.matched == 1
    assert matched.deliveries[0].status == "skipped"
    assert "未配置" in matched.deliveries[0].detail
    assert recall.recent_deliveries()[0].status == "skipped"

    # 不匹配的信号不交付
    miss = recall.dispatch_recall(_msg("NVDA", "warning"))
    assert miss.matched == 0
    assert miss.deliveries == []


def test_dispatch_webpush_degrades_without_vapid(tmp_path, monkeypatch):
    _use_temp_db(tmp_path)
    monkeypatch.delenv("DEEPFOCUS_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("DEEPFOCUS_VAPID_SUBJECT", raising=False)
    recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel="webpush", address="{}", symbols=[], severities=["critical"], scope="all"
        )
    )
    result = recall.dispatch_recall(_msg("TSLA", "critical"))
    assert result.matched == 1
    assert result.deliveries[0].status == "skipped"
    assert "Web Push" in result.deliveries[0].detail


# === 投递闭环：落库 / 点击回流 / 度量 ===

def _subscribe_all(channel="email", address="me@example.com"):
    recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel=channel, address=address, symbols=[], severities=["warning", "critical"], scope="all"
        )
    )


def test_dispatch_persists_delivery_and_metrics(tmp_path, monkeypatch):
    _use_temp_db(tmp_path)
    monkeypatch.delenv("DEEPFOCUS_SMTP_HOST", raising=False)  # → skipped
    _subscribe_all()

    recall.dispatch_recall(_msg("TSLA", "warning"))

    log = recall.list_deliveries()
    assert len(log) == 1
    assert log[0].status == "skipped"
    assert log[0].clicked_at is None
    assert log[0].target_url and "signal=m-TSLA-warning" in log[0].target_url

    metrics = recall.recall_metrics()
    assert metrics.skipped == 1
    assert metrics.delivered == 0
    assert metrics.click_through_rate == 0.0


def test_click_tracks_return_and_is_idempotent(tmp_path, monkeypatch):
    _use_temp_db(tmp_path)
    monkeypatch.setenv("DEEPFOCUS_APP_BASE_URL", "http://localhost:3000")
    # 强制一条"已送达"投递，便于验证点击回流与 CTR。
    monkeypatch.setattr(recall, "_deliver", lambda sub, msg, url: recall._result(sub, "sent", "ok"))
    _subscribe_all()
    recall.dispatch_recall(_msg("TSLA", "warning"))

    delivery = recall.list_deliveries()[0]
    assert delivery.status == "sent" and delivery.clicked_at is None

    target = recall.mark_recall_click(delivery.id)
    assert target is not None and "signal=m-TSLA-warning" in target

    after = recall.list_deliveries()[0]
    assert after.clicked_at is not None
    first_click = after.clicked_at

    # 重复点击幂等：不重复计数、clicked_at 不变。
    recall.mark_recall_click(delivery.id)
    assert recall.list_deliveries()[0].clicked_at == first_click

    metrics = recall.recall_metrics()
    assert metrics.delivered == 1
    assert metrics.clicked == 1
    assert metrics.click_through_rate == 1.0
    assert metrics.by_channel.get("email") == 1


def test_mark_click_unknown_id_returns_none(tmp_path):
    _use_temp_db(tmp_path)
    assert recall.mark_recall_click("does-not-exist") is None


def test_recall_click_endpoint_redirects_to_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from deepfocus_api.main import app

    _use_temp_db(tmp_path)
    monkeypatch.setenv("DEEPFOCUS_APP_BASE_URL", "http://localhost:3000")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    monkeypatch.setattr(recall, "_deliver", lambda sub, msg, url: recall._result(sub, "sent", "ok"))
    _subscribe_all()
    recall.dispatch_recall(_msg("TSLA", "warning"))
    delivery_id = recall.list_deliveries()[0].id

    client = TestClient(app)
    resp = client.get(f"/api/realtime/recall/click/{delivery_id}", follow_redirects=False)
    assert resp.status_code == 302
    assert "signal=m-TSLA-warning" in resp.headers["location"]

    # 未知 id 也不报错，兜底跳回 App。
    miss = client.get("/api/realtime/recall/click/nope", follow_redirects=False)
    assert miss.status_code == 302
    assert "localhost:3000" in miss.headers["location"]

    metrics = client.get("/api/realtime/recall/metrics").json()
    assert metrics["delivered"] == 1 and metrics["clicked"] == 1


# === 微信通道（个人微信桥接 gewechat） ===

def _clear_wechat_env(monkeypatch):
    for key in (
        "DEEPFOCUS_WECHAT_BRIDGE_URL",
        "DEEPFOCUS_WECHAT_BRIDGE_TOKEN",
        "DEEPFOCUS_WECHAT_BRIDGE_APPID",
        "DEEPFOCUS_WECHAT_DEFAULT_TARGET",
    ):
        monkeypatch.delenv(key, raising=False)


def test_dispatch_wechat_degrades_without_bridge(tmp_path, monkeypatch):
    _use_temp_db(tmp_path)
    _clear_wechat_env(monkeypatch)
    _subscribe_all(channel="wechat", address="room123@chatroom")
    result = recall.dispatch_recall(_msg("TSLA", "warning"))
    assert result.matched == 1
    assert result.deliveries[0].status == "skipped"
    assert "微信桥接" in result.deliveries[0].detail


def test_dispatch_wechat_sends_via_bridge(tmp_path, monkeypatch):
    import httpx

    _use_temp_db(tmp_path)
    _clear_wechat_env(monkeypatch)
    monkeypatch.setenv("DEEPFOCUS_WECHAT_BRIDGE_URL", "http://127.0.0.1:2531/gewe/v2/api")
    monkeypatch.setenv("DEEPFOCUS_WECHAT_BRIDGE_TOKEN", "tok")
    monkeypatch.setenv("DEEPFOCUS_WECHAT_BRIDGE_APPID", "app")

    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"ret": 200, "msg": "操作成功"}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    _subscribe_all(channel="wechat", address="room123@chatroom")
    result = recall.dispatch_recall(_msg("TSLA", "warning"))

    assert result.matched == 1
    assert result.deliveries[0].status == "sent"
    assert captured["url"].endswith("/gewe/v2/api/message/postText")
    assert captured["headers"]["X-GEWE-TOKEN"] == "tok"
    assert captured["json"]["toWxid"] == "room123@chatroom"  # 订阅 address 即推送目标
    assert captured["json"]["appId"] == "app"
    assert "股价异动" in captured["json"]["content"]
    assert "/api/realtime/recall/click/" in captured["json"]["content"]  # 嵌可追踪链接 → 复用召回回流度量
    assert captured["trust_env"] is False  # 绕沙箱出网代理


def test_dispatch_wechat_falls_back_to_default_target(tmp_path, monkeypatch):
    import httpx

    _use_temp_db(tmp_path)
    _clear_wechat_env(monkeypatch)
    monkeypatch.setenv("DEEPFOCUS_WECHAT_BRIDGE_URL", "http://127.0.0.1:2531/gewe/v2/api")
    monkeypatch.setenv("DEEPFOCUS_WECHAT_BRIDGE_APPID", "app")
    monkeypatch.setenv("DEEPFOCUS_WECHAT_DEFAULT_TARGET", "fallback@chatroom")

    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"ret": 200}

    monkeypatch.setattr(httpx, "post", lambda url, **kw: (captured.update(kw) or _Resp()))
    # 订阅 address 留空 → 回退默认群
    recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel="wechat", address="", symbols=[], severities=["warning"], scope="all"
        )
    )
    result = recall.dispatch_recall(_msg("TSLA", "warning"))
    assert result.deliveries[0].status == "sent"
    assert captured["json"]["toWxid"] == "fallback@chatroom"
