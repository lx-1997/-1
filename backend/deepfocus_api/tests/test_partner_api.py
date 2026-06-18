"""合作方 API：哈希存储、过期、限流滑窗、IP 防暴破、计量。"""
import pytest

from deepfocus_api import partner_api


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(partner_api, "_db_path", lambda: tmp_path / "partner.sqlite3")
    partner_api._WINDOWS.clear()
    partner_api._AUTH_FAILS.clear()
    return tmp_path


def test_key_stored_hashed_not_plaintext(iso):
    out = partner_api.generate_key("某券商", tier="pro")
    key = out["key"]
    assert key.startswith("dfk_") and out["rate_per_min"] == 300
    # 库里绝不能出现明文密钥，只有摘要 + 前缀
    import sqlite3
    conn = sqlite3.connect(partner_api._db_path()); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM partner_keys").fetchone()
    assert key not in dict(row).values()                  # 明文不落库
    assert row["key_hash"] == partner_api._hash_key(key)  # 存的是摘要
    assert row["key_prefix"] == key[:12]
    # 校验要靠完整密钥
    rec = partner_api.verify_key(key)
    assert rec and rec["name"] == "某券商"
    assert partner_api.verify_key("dfk_bogus") is None
    assert partner_api.verify_key("no-prefix") is None


def test_expiry(iso, monkeypatch):
    from datetime import datetime, timedelta, timezone
    out = partner_api.generate_key("短期方", expires_in_days=7)
    assert out["expires_at"]
    assert partner_api.verify_key(out["key"]) is not None
    # 模拟已过期：把 expires_at 改到过去
    import sqlite3
    conn = sqlite3.connect(partner_api._db_path())
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn.execute("UPDATE partner_keys SET expires_at = ?", (past,)); conn.commit()
    assert partner_api.verify_key(out["key"]) is None  # 过期 → 拒绝


def test_revoke_by_full_key_and_prefix(iso):
    k1 = partner_api.generate_key("按密钥吊销")["key"]
    assert partner_api.revoke_key(k1) is True and partner_api.verify_key(k1) is None
    out2 = partner_api.generate_key("按前缀吊销")
    assert partner_api.revoke_key(out2["key_prefix"]) is True
    assert partner_api.verify_key(out2["key"]) is None
    assert partner_api.revoke_key("dfk_none") is False


def test_rate_limit_sliding_window(iso):
    out = partner_api.generate_key("限流方", rate_per_min=3)
    kh = partner_api.verify_key(out["key"])["_key_hash"]
    assert [partner_api.check_rate(kh, 3) for _ in range(3)] == [True, True, True]
    assert partner_api.check_rate(kh, 3) is False


def test_auth_fail_throttle(iso):
    ip = "9.9.9.9"
    assert partner_api.auth_fail_blocked(ip) is False
    blocked = False
    for _ in range(partner_api._AUTH_FAIL_MAX + 2):
        blocked = partner_api.register_auth_fail(ip)
    assert blocked is True and partner_api.auth_fail_blocked(ip) is True
    # 其他 IP 不受影响
    assert partner_api.auth_fail_blocked("1.1.1.1") is False


def test_usage_logs_prefix_not_key(iso):
    out = partner_api.generate_key("计量方")
    rec = partner_api.verify_key(out["key"])
    partner_api.record_success(rec["_key_hash"], rec["key_prefix"], "/api/v1/news", "1.2.3.4")
    import sqlite3
    conn = sqlite3.connect(partner_api._db_path()); conn.row_factory = sqlite3.Row
    urow = conn.execute("SELECT * FROM partner_usage").fetchone()
    assert out["key"] not in dict(urow).values()           # 流水不落完整密钥
    assert urow["key_prefix"] == out["key_prefix"]
    stats = partner_api.usage_stats()
    assert stats["total"] == 1 and stats["today"] == 1


def test_invalid_tier_falls_back(iso):
    out = partner_api.generate_key("乱填套餐", tier="enterprise")
    assert out["tier"] == "basic" and out["rate_per_min"] == 60


def test_quota_fields_stored(iso):
    out = partner_api.generate_key("配额方", max_calls=1000, daily_quota=100)
    assert out["max_calls"] == 1000 and out["daily_quota"] == 100
    rec = partner_api.verify_key(out["key"])
    assert rec["max_calls"] == 1000 and rec["daily_quota"] == 100
    # 0/空/非法 → 不限
    out2 = partner_api.generate_key("不限方")
    assert out2["max_calls"] == 0 and out2["daily_quota"] == 0
    assert partner_api.generate_key("乱填配额", max_calls="abc", daily_quota=-5)["max_calls"] == 0


def test_record_success_vs_log_usage(iso):
    out = partner_api.generate_key("日配额方")
    rec = partner_api.verify_key(out["key"])
    kh, kp = rec["_key_hash"], rec["key_prefix"]
    partner_api.record_success(kh, kp, "/api/v1/news", "1.1.1.1")  # 真实成功 → 计数
    partner_api.record_success(kh, kp, "/api/v1/news", "1.1.1.1")
    partner_api.log_usage(kp, "/api/v1/news", 429, "1.1.1.1")  # 限流：只记明细，不计数
    partner_api.log_usage(kp, "/api/v1/news", 403, "1.1.1.1")  # 拒绝：不计数
    assert partner_api.today_count(kh) == 2                    # 非有损日计数=2
    assert partner_api.verify_key(out["key"])["call_count"] == 2
    assert partner_api.month_count(kh) == 2


def test_daily_counter_not_lossy(iso, monkeypatch):
    """日计数表不受 partner_usage 全局裁剪影响（对账真相源）。"""
    monkeypatch.setattr(partner_api, "USAGE_MAX", 3)  # 故意把明细表上限设极小
    out = partner_api.generate_key("大客户")
    rec = partner_api.verify_key(out["key"])
    kh, kp = rec["_key_hash"], rec["key_prefix"]
    for _ in range(10):
        partner_api.record_success(kh, kp, "/api/v1/news", "1.1.1.1")
    # partner_usage 明细被裁到 3 行，但日计数表仍是 10（配额/对账靠它）
    assert partner_api.today_count(kh) == 10
    assert partner_api.verify_key(out["key"])["call_count"] == 10


def test_billing_fields_and_mark_paid(iso):
    out = partner_api.generate_key("付费方", price_cents=200000, billing_period="monthly")
    assert out["price_cents"] == 200000 and out["billing_period"] == "monthly"
    assert out["billing_status"] == "unpaid"  # 有价默认待收款
    pfx = out["key_prefix"]
    assert partner_api.mark_paid(pfx, "对公转账 #123") is True
    rec = partner_api.verify_key(out["key"])
    assert rec["billing_status"] == "paid" and rec["paid_at"] and "123" in rec["billing_note"]
    # 免费 key 默认 comp
    free = partner_api.generate_key("联调方")
    assert free["price_cents"] == 0 and free["billing_status"] == "comp"


def test_billing_summary(iso):
    a = partner_api.generate_key("A", price_cents=200000, billing_period="monthly")
    partner_api.mark_paid(a["key_prefix"])
    partner_api.generate_key("B", price_cents=800000, billing_period="yearly")  # 未收款
    s = partner_api.billing_summary()
    assert s["paid_month_cents"] == 200000 and s["unpaid_cents"] == 800000
    assert s["paid_keys"] == 1 and s["unpaid_keys"] == 1


def test_extend_expiry_keeps_same_key(iso):
    out = partner_api.generate_key("续期方", expires_in_days=10, price_cents=100, billing_period="monthly")
    new_exp = partner_api.extend_expiry(out["key_prefix"], days=31)
    assert new_exp is not None
    rec = partner_api.verify_key(out["key"])  # 同一密钥仍有效
    assert rec is not None and rec["expires_at"] == new_exp and rec["billing_status"] == "paid"


def test_compute_alerts(iso, monkeypatch):
    from datetime import datetime, timedelta, timezone
    # 近到期(auto_renew) + 待收款超期
    near = partner_api.generate_key("近到期方", expires_in_days=3, price_cents=200000, billing_period="monthly")
    import sqlite3
    conn = sqlite3.connect(partner_api._db_path())
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute("UPDATE partner_keys SET created_at = ? WHERE key_prefix = ?", (old, near["key_prefix"]))
    conn.commit()
    al = partner_api.compute_alerts()
    assert al["counts"]["near_expiry"] >= 1
    assert al["counts"]["unpaid_overdue"] >= 1  # unpaid 且签发 10 天前
    # comp(免费)不进待收款告警
    partner_api.generate_key("联调", expires_in_days=3)
    al2 = partner_api.compute_alerts()
    assert all(x["name"] != "联调" for x in al2["unpaid_overdue"])
