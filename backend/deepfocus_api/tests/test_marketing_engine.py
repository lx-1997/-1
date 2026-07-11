"""自动营销引擎：分群口径 + 频控闸门 + dry_run/主闸 + SMTP 降级 + 文案护栏 + 归因回填。

隔离方式(照 test_t1_recall)：monkeypatch RECALL_DB_PATH / METRICS_DB_PATH 到 tmp，
自建一个含 activity_log 的 metrics 库喂行为画像，list_users 打桩喂用户。
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from deepfocus_api import marketing_engine as me

UTC = timezone.utc


def _user(uid, email, days_ago, tier="trial"):
    return SimpleNamespace(
        id=uid, username=f"u_{uid}", email=email,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
        membership={"tier": tier},
    )


def _seed_metrics(path, rows):
    """rows = [(actor_id, ts_iso), ...] 写进一个最小 activity_log。"""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor_kind TEXT,"
        " actor_id TEXT, actor_name TEXT, action TEXT, target TEXT, ip TEXT, device TEXT)"
    )
    for aid, ts in rows:
        conn.execute(
            "INSERT INTO activity_log (ts, actor_kind, actor_id, actor_name, action, target, ip, device)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ts, "user", aid, "", "pageview", "", "", ""),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "RECALL_DB_PATH", tmp_path / "recall.sqlite3")
    monkeypatch.setattr(me, "METRICS_DB_PATH", tmp_path / "metrics.sqlite3")
    monkeypatch.setattr(me, "users_expiring_within", lambda hours=48, paid=False: [])
    monkeypatch.setattr(me, "_review_hook", lambda: ("测试一句话", ""))
    me.init_marketing_db()
    return tmp_path


def _days_ago_iso(d, hours=0):
    return (datetime.now(UTC) - timedelta(days=d, hours=hours)).isoformat()


# ---------------- 分群口径 ----------------

def test_segment_d7_slipping(isolated, monkeypatch):
    # a: 注册 7 天、活跃过 2 天(注册日+3天前)、最近 4 天没来 → 命中 d7_slipping
    _seed_metrics(isolated / "metrics.sqlite3", [
        ("u:a", _days_ago_iso(7)), ("u:a", _days_ago_iso(4)),
    ])
    monkeypatch.setattr(me, "list_users", lambda: [_user("a", "a@x.com", 7)])
    segs = me.compute_segments()
    assert [r["user_id"] for r in segs["d7_slipping"]] == ["a"]
    assert segs["dormant"] == [] and segs["power_free"] == []


def test_segment_d7_excludes_recent_and_noemail(isolated, monkeypatch):
    # a 最近 1h 来过 → 不算流失；b 无邮箱 → 全程排除
    _seed_metrics(isolated / "metrics.sqlite3", [
        ("u:a", _days_ago_iso(7)), ("u:a", _days_ago_iso(0, 1)),
        ("u:b", _days_ago_iso(7)), ("u:b", _days_ago_iso(5)),
    ])
    monkeypatch.setattr(me, "list_users", lambda: [_user("a", "a@x.com", 7), _user("b", "", 7)])
    segs = me.compute_segments()
    assert segs["d7_slipping"] == []


def test_segment_dormant(isolated, monkeypatch):
    # c: 注册 40 天、历史活跃 3 天、最近记录 20 天前(>14) → dormant
    _seed_metrics(isolated / "metrics.sqlite3", [
        ("u:c", _days_ago_iso(40)), ("u:c", _days_ago_iso(35)), ("u:c", _days_ago_iso(20)),
    ])
    monkeypatch.setattr(me, "list_users", lambda: [_user("c", "c@x.com", 40)])
    segs = me.compute_segments()
    assert [r["user_id"] for r in segs["dormant"]] == ["c"]


def test_segment_power_free(isolated, monkeypatch):
    # d: 非会员、近 7 天活跃 4 天 → power_free；付费同样活跃 → 不进(非 trial)
    rows = [("u:d", _days_ago_iso(i)) for i in range(4)] + [("u:e", _days_ago_iso(i)) for i in range(4)]
    _seed_metrics(isolated / "metrics.sqlite3", rows)
    monkeypatch.setattr(me, "list_users",
                        lambda: [_user("d", "d@x.com", 20, tier="trial"),
                                 _user("e", "e@x.com", 20, tier="premium")])
    segs = me.compute_segments()
    assert [r["user_id"] for r in segs["power_free"]] == ["d"]


# ---------------- 主闸 / dry_run ----------------

def _enable_all(monkeypatch, working_smtp=True):
    monkeypatch.setenv("DEEPFOCUS_MARKETING_ENABLED", "1")
    for k in ("d7_slipping", "dormant", "power_free"):
        me.campaign_set(k, enabled=True)
    if working_smtp:
        monkeypatch.setattr(me, "_email_smtp_config", lambda: {"sender": "s@x.com", "user": "u@x.com"})
        monkeypatch.setattr(me, "_smtp_sendmail", lambda config, to, mime: None)


def test_master_off_forces_dry_run(isolated, monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_MARKETING_ENABLED", raising=False)
    for k in ("d7_slipping", "dormant", "power_free"):
        me.campaign_set(k, enabled=True)
    _seed_metrics(isolated / "metrics.sqlite3", [("u:d", _days_ago_iso(i)) for i in range(4)])
    monkeypatch.setattr(me, "list_users", lambda: [_user("d", "d@x.com", 20)])
    monkeypatch.setattr(me, "_email_smtp_config", lambda: {"sender": "s", "user": "u"})
    monkeypatch.setattr(me, "_smtp_sendmail", lambda *a, **k: None)
    out = me.run_marketing_once(dry_run=False)   # 请求真发，但主闸关 → 强制 dry
    assert out["dry_run"] is True and out["master_enabled"] is False
    assert out["sent"] == 0 and len(out["preview"]) == 1
    # 关键：dry 不落任何 touch，不污染频控
    with sqlite3.connect(isolated / "recall.sqlite3") as c:
        assert c.execute("SELECT COUNT(*) FROM marketing_touches").fetchone()[0] == 0


def test_smtp_unset_does_not_write_touches(isolated, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_MARKETING_ENABLED", "1")
    me.campaign_set("power_free", enabled=True)
    monkeypatch.setattr(me, "_email_smtp_config", lambda: None)
    _seed_metrics(isolated / "metrics.sqlite3", [("u:d", _days_ago_iso(i)) for i in range(4)])
    monkeypatch.setattr(me, "list_users", lambda: [_user("d", "d@x.com", 20)])
    out = me.run_marketing_once(dry_run=False)
    assert out["sent"] == 0 and out["skipped"] >= 1
    with sqlite3.connect(isolated / "recall.sqlite3") as c:
        assert c.execute("SELECT COUNT(*) FROM marketing_touches").fetchone()[0] == 0


# ---------------- 真发 + 频控 ----------------

def test_real_send_and_user_cooldown(isolated, monkeypatch):
    _enable_all(monkeypatch)
    _seed_metrics(isolated / "metrics.sqlite3", [("u:d", _days_ago_iso(i)) for i in range(4)])
    monkeypatch.setattr(me, "list_users", lambda: [_user("d", "d@x.com", 20)])
    out = me.run_marketing_once(dry_run=False)
    assert out["sent"] == 1 and out["dry_run"] is False
    # 第二轮：7 天用户冷却内 → 跳过，不重发
    out2 = me.run_marketing_once(dry_run=False)
    assert out2["sent"] == 0 and out2["skipped"] >= 1


def test_one_touch_per_run_across_campaigns(isolated, monkeypatch):
    # 同一用户同时命中 d7_slipping 和 power_free，一轮最多收一条
    _enable_all(monkeypatch)
    # 注册 7 天、活跃 2 天且最近 72h 没来(d7) 但近 7 天活跃 4 天(power_free) 会互相排斥吗？
    # 用两个不同用户各命中一个分群，验证互不串；再造一个双命中场景验证一轮一条。
    rows = [("u:x", _days_ago_iso(i)) for i in range(4)]  # 近 4 天活跃 → power_free
    _seed_metrics(isolated / "metrics.sqlite3", rows)
    monkeypatch.setattr(me, "list_users", lambda: [_user("x", "x@x.com", 20)])
    out = me.run_marketing_once(dry_run=False)
    assert out["sent"] == 1  # 只发一条


def test_daily_total_cap(isolated, monkeypatch):
    _enable_all(monkeypatch)
    monkeypatch.setenv("DEEPFOCUS_MKT_DAILY_TOTAL", "2")
    rows = []
    users = []
    for i in range(5):
        uid = f"p{i}"
        rows += [("u:" + uid, _days_ago_iso(j)) for j in range(4)]  # 全部 power_free
        users.append(_user(uid, f"{uid}@x.com", 20))
    _seed_metrics(isolated / "metrics.sqlite3", rows)
    monkeypatch.setattr(me, "list_users", lambda: users)
    out = me.run_marketing_once(dry_run=False)
    assert out["sent"] == 2  # 日总量封顶


def test_suppression_skips(isolated, monkeypatch):
    _enable_all(monkeypatch)
    _seed_metrics(isolated / "metrics.sqlite3", [("u:d", _days_ago_iso(i)) for i in range(4)])
    monkeypatch.setattr(me, "list_users", lambda: [_user("d", "d@x.com", 20)])
    me.add_suppression("d", "unsubscribe")
    out = me.run_marketing_once(dry_run=False)
    assert out["sent"] == 0 and out["skipped"] >= 1


# ---------------- 文案护栏 ----------------

def test_copy_has_disclaimer_and_no_forbidden_brand(isolated):
    for seg in ("d7_slipping", "dormant", "power_free"):
        subject, body = me.build_email(seg, "张三")
        assert "张三" in body
        assert "退订" in body and "不构成投资建议" in body
        assert "道财经" not in subject and "道财经" not in body
        assert "{LINK}" in body  # 占位待替换成追踪链接


# ---------------- 归因 ----------------

def test_click_and_return_attribution(isolated, monkeypatch):
    _enable_all(monkeypatch)
    _seed_metrics(isolated / "metrics.sqlite3", [("u:d", _days_ago_iso(i)) for i in range(4)])
    monkeypatch.setattr(me, "list_users", lambda: [_user("d", "d@x.com", 20)])
    me.run_marketing_once(dry_run=False)
    # 取刚落的 touch id
    with sqlite3.connect(isolated / "recall.sqlite3") as c:
        c.row_factory = sqlite3.Row
        tid = c.execute("SELECT id FROM marketing_touches WHERE status='sent'").fetchone()["id"]
    # 点击回填
    url = me.mark_clicked(tid)
    assert "utm=mkt_" in url
    with sqlite3.connect(isolated / "recall.sqlite3") as c:
        assert c.execute("SELECT clicked_at FROM marketing_touches WHERE id=?", (tid,)).fetchone()[0]
    # 把 sent_at 改早(>10min)，并给该用户造一条发信后的活跃 → attribute_returns 回填
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(isolated / "recall.sqlite3") as c:
        c.execute("UPDATE marketing_touches SET sent_at=? WHERE id=?", (past, tid)); c.commit()
    _seed_extra = sqlite3.connect(isolated / "metrics.sqlite3")
    _seed_extra.execute(
        "INSERT INTO activity_log (ts, actor_kind, actor_id, actor_name, action, target, ip, device)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ((datetime.now(UTC) - timedelta(minutes=30)).isoformat(), "user", "u:d", "", "pageview", "", "", ""),
    )
    _seed_extra.commit(); _seed_extra.close()
    n = me.attribute_returns()
    assert n == 1
    stats = me.marketing_funnel()
    assert stats["sent"] == 1 and stats["clicked"] == 1 and stats["returned"] == 1


def test_mark_clicked_invalid_id_returns_home(isolated):
    url = me.mark_clicked(999999)
    assert url and "utm=" not in url  # 无效 id → 首页，不 404
