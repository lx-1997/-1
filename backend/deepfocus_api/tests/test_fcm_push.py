from deepfocus_api import recall_subscriptions as recall
from deepfocus_api.schemas import RealtimeMessageRecord, RecallSubscriptionCreateRequest


def _msg(topic="研报", severity="warning", symbol="TSLA"):
    return RealtimeMessageRecord(
        id="fcm-msg-1", title="TSLA 研报更新", content="目标价与风险提示", topic=topic,
        severity=severity, symbol=symbol, tags=["AI"], created_at="2026-06-04T00:00:00Z",
    )


def test_fcm_filter_matches_topics_keywords_and_important_mode(tmp_path):
    recall.DB_PATH = tmp_path / "recall.sqlite3"
    created = recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel="fcm", address="token-1", symbols=["TSLA"], severities=["success", "warning", "critical"],
            scope="watchlist", topics=["研报"], keywords=["目标价"], mode="important",
        )
    )
    assert created.topics == ["研报"]
    assert created.keywords == ["目标价"]
    assert recall.subscription_matches(created, _msg()) is True
    assert recall.subscription_matches(created, _msg(topic="文章")) is False
    assert recall.subscription_matches(created, _msg(severity="info")) is False


def test_fcm_delivery_degrades_without_firebase_credentials(tmp_path, monkeypatch):
    recall.DB_PATH = tmp_path / "recall.sqlite3"
    monkeypatch.delenv("DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_FILE", raising=False)
    recall.create_recall_subscription(
        RecallSubscriptionCreateRequest(
            channel="fcm", address="token-1", symbols=["TSLA"], severities=["warning"], scope="all", mode="all"
        )
    )
    result = recall.dispatch_recall(_msg())
    assert result.matched == 1
    assert result.deliveries[0].status == "skipped"
    assert "FCM" in result.deliveries[0].detail
