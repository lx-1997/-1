"""T+1 未回访召回：候选筛选口径 + 去重落库 + SMTP 未配置优雅降级。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from deepfocus_api import t1_recall


def _user(uid: str, email: str, hours_ago: float):
    return SimpleNamespace(
        id=uid, username=f"u_{uid}", email=email,
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(t1_recall, "RECALL_DB_PATH", tmp_path / "recall.sqlite3")
    monkeypatch.setattr(t1_recall, "METRICS_DB_PATH", tmp_path / "metrics.sqlite3")  # 不存在 → last_seen 空
    monkeypatch.setattr(t1_recall, "_email_smtp_config", lambda: None)
    return tmp_path


def test_candidate_window(isolated, monkeypatch):
    users = [
        _user("a", "a@x.com", 30),    # 命中：24~72h、有邮箱
        _user("b", "", 30),           # 无邮箱 → 排除
        _user("c", "c@x.com", 10),    # 注册不足 24h → 排除
        _user("d", "d@x.com", 100),   # 超 72h（首发保护）→ 排除
    ]
    monkeypatch.setattr(t1_recall, "list_users", lambda: users)
    got = [u.id for u in t1_recall.find_t1_candidates()]
    assert got == ["a"]


def test_recent_activity_excludes(isolated, monkeypatch):
    users = [_user("a", "a@x.com", 30)]
    monkeypatch.setattr(t1_recall, "list_users", lambda: users)
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(t1_recall, "_last_seen_map", lambda: {"u:a": recent})
    assert t1_recall.find_t1_candidates() == []
    # 最近活跃已是 30h 前 → 仍算流失，重新命中
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    monkeypatch.setattr(t1_recall, "_last_seen_map", lambda: {"u:a": stale})
    assert [u.id for u in t1_recall.find_t1_candidates()] == ["a"]


def test_run_once_dedupes_and_degrades(isolated, monkeypatch):
    users = [_user("a", "a@x.com", 30)]
    monkeypatch.setattr(t1_recall, "list_users", lambda: users)
    out = t1_recall.run_t1_recall_once()
    assert out["candidates"] == 1 and out["skipped"] == 1 and out["sent"] == 0  # SMTP 未配置 → skipped
    # 已落库去重：第二轮不再进候选
    out2 = t1_recall.run_t1_recall_once()
    assert out2["candidates"] == 0
    stats = t1_recall.t1_recall_stats()
    assert stats["skipped"] == 1 and stats["recent"][0]["email"] == "a@x.com"


def test_daily_limit_defers_rest(isolated, monkeypatch):
    users = [_user(f"u{i}", f"u{i}@x.com", 25 + i) for i in range(5)]
    monkeypatch.setattr(t1_recall, "list_users", lambda: users)
    out = t1_recall.run_t1_recall_once(limit=2)
    assert out["candidates"] == 5 and out["deferred"] == 3
    assert out["sent"] + out["skipped"] == 2  # 只处理上限内的 2 人
    # 注册更早(hours_ago 更大)的优先：u4(29h)/u3(28h) 先发
    stats = t1_recall.t1_recall_stats()
    assert {r["email"] for r in stats["recent"]} == {"u4@x.com", "u3@x.com"}
    # 未发的 3 人没落库，下一轮仍是候选
    out2 = t1_recall.run_t1_recall_once(limit=10)
    assert out2["candidates"] == 3


def test_email_content_uses_review(isolated, monkeypatch):
    monkeypatch.setattr(t1_recall.ashare_review, "latest_review", lambda: {
        "date": "2026-06-11",
        "narrative": {"one_liner": "高位震荡，资金回流半导体"},
        "our_edge": [1, 2, 3],
    })
    subject, body = t1_recall._build_email("张三")
    assert "2026-06-11" in subject and "高位震荡" in subject
    assert "3 条" in body and "张三" in body


def test_email_fallback_without_review(isolated, monkeypatch):
    monkeypatch.setattr(t1_recall.ashare_review, "latest_review", lambda: None)
    subject, body = t1_recall._build_email("李四")
    assert "体验会员" in subject and "李四" in body
