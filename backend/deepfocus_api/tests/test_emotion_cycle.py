"""情绪周期层：四阶段判定 + 流派闸门 + 数据降级。纯函数单测(合成指标,不打网络)。"""
from datetime import datetime, timedelta, timezone

from deepfocus_api import emotion_cycle as ec


# ---------------- ① 四阶段判定 ----------------

def _m(**kw):
    base = {"collected": True, "zt_count": 50, "dt_count": 3, "max_boards": 5,
            "high_boards": 4, "first_boards": 25, "broke_avg": 0.5, "broke_top": 1,
            "premium": 1.2}
    base.update(kw)
    return base


def test_attack_full_breadth():
    r = ec.classify(_m(zt_count=85, max_boards=6, high_boards=6, first_boards=40,
                       broke_avg=0.3, broke_top=0, premium=2.3))
    assert r["stage"] == "attack"


def test_benign_divergence():
    r = ec.classify(_m(zt_count=52, premium=1.2, broke_avg=0.7))
    assert r["stage"] == "benign"


def test_evil_divergence_two_paths():
    # 路径一：溢价转弱 + 封板质量恶化
    r1 = ec.classify(_m(zt_count=38, dt_count=7, premium=-0.4, broke_avg=1.2))
    assert r1["stage"] == "evil"
    # 路径二：龙头炸板 + 跌停升温
    r2 = ec.classify(_m(max_boards=5, broke_top=3, dt_count=6, premium=1.0, broke_avg=0.4))
    assert r2["stage"] == "evil"


def test_ebb_priority_over_attack():
    # 溢价显著为负+跌停批量：即使家数/高度仍高，也优先判退潮(接力资金在亏钱)
    r = ec.classify(_m(zt_count=75, max_boards=6, high_boards=5, premium=-2.0, dt_count=6))
    assert r["stage"] == "ebb"


def test_ebb_by_breadth_collapse():
    r = ec.classify(_m(zt_count=18, dt_count=14, max_boards=2, premium=-2.8))
    assert r["stage"] == "ebb"


def test_freeze_trial():
    r = ec.classify(_m(zt_count=24, dt_count=4, max_boards=3, high_boards=0,
                       first_boards=20, premium=0.5))
    assert r["stage"] == "freeze"


def test_neutral_mild_rotation():
    r = ec.classify(_m(zt_count=48, dt_count=2, max_boards=4, first_boards=10,
                       broke_avg=0.4, premium=0.8))
    assert r["stage"] == "neutral"


def test_missing_data_never_kills_trading():
    r = ec.classify({"collected": False})
    assert r["stage"] == "neutral" and "数据暂缺" in r["reasons"][0]
    assert ec.classify({})["stage"] == "neutral"


# ---------------- ② 流派闸门 ----------------

def test_cycle_style_obey_discipline():
    assert ec.gate("cycle", "ebb")["blocked"] is True
    assert ec.gate("cycle", "evil")["blocked"] is True
    assert ec.gate("cycle", "benign")["size_mult"] == 0.5
    assert ec.gate("cycle", "freeze")["size_mult"] == 0.5
    g = ec.gate("cycle", "attack")
    assert g["size_mult"] == 1.25 and g["thr_delta"] == -0.02
    assert ec.gate("cycle", "neutral")["blocked"] is False


def test_contrarian_small_bounce_in_ebb():
    # 逆向派退潮期不禁买，但只给极小仓位(超跌反弹猎场)
    g = ec.gate("contrarian", "ebb")
    assert g["blocked"] is False and 0 < g["size_mult"] <= 0.4


def test_unknown_style_falls_back_and_never_blocks_by_default():
    g = ec.gate("mystery", "ebb")
    assert g["blocked"] is False and g["size_mult"] == 0.7
    assert ec.gate("cycle", "weird_stage")["key"] == "neutral"


# ---------------- ③ 缓存与降级 ----------------

def test_current_cycle_cache_hit(monkeypatch):
    sentinel = {"stage": "attack", "label": "发酵主升"}
    monkeypatch.setattr(ec, "_CACHE", {"cycle": (ec.time.time(), sentinel)})
    assert ec.current_cycle() is sentinel


def test_current_cycle_failure_degrades_to_neutral(monkeypatch):
    monkeypatch.setattr(ec, "_CACHE", {})
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ec, "_gather_metrics", _boom)
    assert ec.current_cycle()["stage"] == "neutral"


def test_current_cycle_uses_metrics(monkeypatch):
    monkeypatch.setattr(ec, "_CACHE", {})
    monkeypatch.setattr(ec, "_gather_metrics", lambda h, q: _m(zt_count=85, premium=2.3))
    assert ec.current_cycle()["stage"] == "attack"


# ---------------- ④ 上一交易日 ----------------

class _FakeDT:
    """把 datetime.now 钉在 2026-10-25(周日)，验证周末/节假日回退。"""
    _fixed = datetime(2026, 10, 25, 12, 0, 0, tzinfo=ec._BJ)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def test_prev_trading_day_skips_weekend_and_holidays(monkeypatch):
    real_dt = ec.datetime
    monkeypatch.setattr(ec, "datetime", _FakeDT)
    try:
        assert ec._prev_trading_day() == "20261023"                     # 周日→周五
        assert ec._prev_trading_day({"2026-10-23"}) == "20261022"       # 周五也休市→周四
    finally:
        ec.datetime = real_dt


# ---------------- ⑤ 连亏停手铁律(_performance_brake) ----------------

def test_two_big_losses_stop_three_days():
    from deepfocus_api import ai_fund
    now = datetime.now(timezone.utc)
    stats = {"closed": 6, "last2": -4.2, "last_sell_ts": now.isoformat(), "win_rate": 55.0, "avg": 0.5}
    b = ai_fund._performance_brake(stats)
    assert b["key"] == "stop" and b.get("blocked") is True and b["size_mult"] == 0.0
    # 4 天前平仓 → 停手期满自动解禁
    old = (now - timedelta(days=4)).isoformat()
    assert ai_fund._performance_brake({**stats, "last_sell_ts": old})["key"] != "stop"
    # 停手档坏时间串 → 保守按 0 天(继续停手)，不抛错
    assert ai_fund._performance_brake({**stats, "last_sell_ts": "garbage"})["key"] == "stop"
    # 单笔大亏不构成「连续两笔」
    assert ai_fund._performance_brake({"closed": 1, "last2": -8.0, "last_sell_ts": now.isoformat()})["key"] != "stop"
    # 停手档仍保留其余键(下游 _do_buy/_buy_allowed 消费)
    assert b["thr_delta"] == 0.20 and b["rotate_mult"] == 2.0
