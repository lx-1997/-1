"""stock_call 台账+纯函数群单测(蓝图 P0-1 verification 六类断言全覆盖):

①窗口内含除权除息:快照价≠前复权入场价时仍打对分  ②dead-zone ±1% 边界
③±15% 封顶×信念乘数  ④盘后/周末/节假日 entry 归一  ⑤表态日停牌 void
⑥CN_MARKET_HOLIDAYS 不含目标年份(horizon=20 跨年)→告警拒算不静默错算
外加台账不变量:两键唯一 open 索引(无 direction,堵骑墙)/settle 流转/summary 排除 is_test/
push_log 一日一条/digest 固定模板(样本量+免责)。
"""
from datetime import datetime

import pytest

from deepfocus_api import stock_call as sc


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "calls.sqlite3")
    sc.init_call_db()


# ---------------- ① 除权除息:两端同出前复权序列,快照价只是证据锚 ----------------

def test_grade_correct_across_ex_dividend_window():
    # 场景:入场后 10 送 10。入场时 fetch_market_quotes 快照=100.0(除权前原始价);
    # 结算时拉到的前复权序列两端: adj[entry]=50.0, adj[settle]=53.0(除权已抹平)。
    snapshot_entry_price = 100.0
    qfq = {"2026-07-03": 50.0, "2026-07-10": 53.0}
    entry_close, settle_close = qfq["2026-07-03"], qfq["2026-07-10"]
    assert snapshot_entry_price != entry_close  # 快照≠打分入场价——契约:快照绝不进 grade()

    r = sc.grade(entry_close, settle_close, "bull")
    assert r["move_pct"] == 6.0 and r["outcome"] == "hit" and r["call_score"] == 6.0
    assert r["grade_ver"] == sc.GRADE_VER

    # 反例自证:若错用原始快照当入场价,+6% 的判断会被算成 -47% 的"看错"
    naive_wrong = (settle_close - snapshot_entry_price) / snapshot_entry_price * 100
    assert naive_wrong < -40


def test_grade_rejects_bad_inputs():
    with pytest.raises(ValueError):
        sc.grade(100.0, 106.0, "long")  # 非法方向
    with pytest.raises(ValueError):
        sc.grade(100.0, 106.0, "bull", conviction=9)
    with pytest.raises(ValueError):
        sc.grade(0.0, 106.0, "bull")  # 非正入场价


# ---------------- ② dead-zone ±1% 边界 ----------------

def test_dead_zone_boundaries():
    # 恰好 +1.00% → hit(≥DZ 含边界)
    assert sc.grade(100.0, 101.0, "bull")["outcome"] == "hit"
    # +0.90% → flat(骑墙不奖励)
    assert sc.grade(100.0, 100.9, "bull")["outcome"] == "flat"
    # 恰好 -1.00% → miss
    assert sc.grade(100.0, 99.0, "bull")["outcome"] == "miss"
    # 方向化:看空跌 1% = 方向化 +1% → hit
    bear = sc.grade(100.0, 99.0, "bear")
    assert bear["outcome"] == "hit" and bear["ret_pct"] == 1.0 and bear["move_pct"] == -1.0
    # 0 变动 → flat
    assert sc.grade(100.0, 100.0, "bull")["outcome"] == "flat"


# ---------------- ③ ±15% 封顶 × 信念乘数 ----------------

def test_cap_and_conviction_multiplier():
    # +30% 超封顶 → clamp 到 15,重仓 ×1.4
    r3 = sc.grade(100.0, 130.0, "bull", conviction=3)
    assert r3["outcome"] == "hit" and r3["call_score"] == pytest.approx(15.0 * 1.4)
    # 轻仓 ×0.6
    r1 = sc.grade(100.0, 130.0, "bull", conviction=1)
    assert r1["call_score"] == pytest.approx(15.0 * 0.6)
    # 看多大跌 -40% → clamp 到 -15,标准仓 ×1.0
    rm = sc.grade(100.0, 60.0, "bull", conviction=2)
    assert rm["outcome"] == "miss" and rm["call_score"] == -15.0
    # 看空但大涨 → 方向化 -30% → 同样负向封顶
    rb = sc.grade(100.0, 130.0, "bear")
    assert rb["ret_pct"] == -30.0 and rb["call_score"] == -15.0
    # 未触顶不封:+6% ×1.4
    assert sc.grade(100.0, 106.0, "bull", conviction=3)["call_score"] == pytest.approx(8.4)


# ---------------- ④ entry 归一:盘中当日/盘后/周末/节假日 ----------------

def test_normalize_entry_date_intraday_and_after_close():
    # 2026-07-10 周五交易日,盘中 10:00 → 当日收盘起算
    assert sc.normalize_entry_date(datetime(2026, 7, 10, 10, 0)) == "2026-07-10"
    # 14:59 仍算盘中
    assert sc.normalize_entry_date(datetime(2026, 7, 10, 14, 59)) == "2026-07-10"
    # 15:00 收盘后 → 下一交易日(周一 07-13)——堵盘后消息套利吃次日跳空
    assert sc.normalize_entry_date(datetime(2026, 7, 10, 15, 0)) == "2026-07-13"


def test_normalize_entry_date_weekend_and_holiday():
    # 周六 → 下周一
    assert sc.normalize_entry_date(datetime(2026, 7, 11, 10, 0)) == "2026-07-13"
    # 国庆 10-01(周四休市)盘中时刻表态 → 顺延过 10-01/02 假日+周末+10-05~07 假日 → 10-08
    assert sc.normalize_entry_date(datetime(2026, 10, 1, 10, 0)) == "2026-10-08"


# ---------------- ⑤ 表态日停牌(归一日无 bar)→ void ----------------

def test_normalize_entry_date_suspended_returns_none():
    bars = {"2026-07-09", "2026-07-13"}  # 07-10 停牌无bar
    assert sc.normalize_entry_date(datetime(2026, 7, 10, 10, 0), bar_dates=bars) is None
    # 有 bar 则正常
    assert sc.normalize_entry_date(datetime(2026, 7, 10, 10, 0), bar_dates=bars | {"2026-07-10"}) == "2026-07-10"


# ---------------- ⑥ 跨年日历缺失 → 告警拒算,不静默错算 ----------------

def test_cross_year_calendar_missing_raises_with_warning(caplog):
    # horizon=20 从 2026-12 跨进 2027(CN_MARKET_HOLIDAYS 目前只维护到 2026)
    with caplog.at_level("WARNING", logger="deepfocus.stock_call"):
        with pytest.raises(sc.CalendarCoverageError):
            sc.trading_days_between("2026-12-20", "2027-01-10")
    assert "2027" in caplog.text and "日历缺失" in caplog.text

    # 年末收盘后表态,顺延会踏进 2027 → 同样拒算
    with pytest.raises(sc.CalendarCoverageError):
        sc.normalize_entry_date(datetime(2026, 12, 31, 16, 0))


def test_trading_days_between_counts_trading_days_only():
    # (entry, today] 计数:周五→下周一 = 1 个交易日
    assert sc.trading_days_between("2026-07-03", "2026-07-06") == 1
    # 周五→下周五 = 5 个交易日
    assert sc.trading_days_between("2026-07-03", "2026-07-10") == 5
    # 跨国庆长假:09-30→10-08 中间只有 10-08 一个交易日
    assert sc.trading_days_between("2026-09-30", "2026-10-08") == 1
    # today ≤ entry → 0
    assert sc.trading_days_between("2026-07-06", "2026-07-06") == 0
    assert sc.trading_days_between("2026-07-06", "2026-07-03") == 0


# ---------------- 台账:两键唯一 open 索引(无 direction,堵骑墙) ----------------

def test_unique_open_index_blocks_hedged_double_calls(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    first, created = sc.create_call("u1", "600519", "bull", entry_date="2026-07-10", entry_price=1800.0)
    assert created and first["status"] == "open"

    # ★同标的开反向单也被幂等挡回(两键索引刻意无 direction)——多空双开必中一单的骑墙在台账层就不存在
    dup, created2 = sc.create_call("u1", "600519", "bear", entry_date="2026-07-10", entry_price=1800.0)
    assert not created2 and dup["id"] == first["id"] and dup["direction"] == "bull"

    # 不同标的/不同用户不受影响
    _, ok_other_symbol = sc.create_call("u1", "000858", "bear", entry_date="2026-07-10", entry_price=130.0)
    _, ok_other_user = sc.create_call("u2", "600519", "bear", entry_date="2026-07-10", entry_price=1800.0)
    assert ok_other_symbol and ok_other_user

    # 结算后同标的可再开新单(唯一约束只锁 open)
    assert sc.settle_call(first["id"], settle_date="2026-07-17", exit_price=1908.0,
                          ret_pct=6.0, outcome="hit", call_score=6.0)
    _, reopened = sc.create_call("u1", "600519", "bear", entry_date="2026-07-17", entry_price=1908.0)
    assert reopened


def test_settle_flow_unseen_and_mark_seen(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    rec, _ = sc.create_call("u1", "600519", "bull", entry_date="2026-07-10", entry_price=1800.0)
    assert sc.settle_call(rec["id"], settle_date="2026-07-17", exit_price=1908.0,
                          ret_pct=6.0, outcome="hit", call_score=6.0)
    got = sc.get_call(rec["id"])
    assert got["status"] == "settled" and got["unseen"] is True and got["grade_ver"] == sc.GRADE_VER
    assert got["bench_move"] is None  # v1 基准留空,P1 alpha 接入零返工

    # 已 settled 不能再 settle/void/cancel
    assert not sc.settle_call(rec["id"], settle_date="2026-07-18", exit_price=1.0,
                              ret_pct=0.0, outcome="flat", call_score=0.0)
    assert not sc.void_call(rec["id"]) and not sc.cancel_call(rec["id"])

    assert sc.mark_calls_seen("u1") == 1
    assert sc.get_call(rec["id"])["unseen"] is False
    assert sc.mark_calls_seen("u1") == 0  # 幂等


def test_void_and_cancel_only_from_open(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    a, _ = sc.create_call("u1", "600519", "bull", entry_date="2026-07-10", entry_price=1800.0)
    assert sc.void_call(a["id"]) and sc.get_call(a["id"])["status"] == "void"
    b, _ = sc.create_call("u1", "000858", "bear", entry_date="2026-07-10", entry_price=130.0)
    assert sc.cancel_call(b["id"]) and sc.get_call(b["id"])["status"] == "canceled"


def test_settle_fail_counter_escalates_to_error(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    rec, _ = sc.create_call("u1", "600519", "bull", entry_date="2026-07-10", entry_price=1800.0)
    for i in range(1, sc.SETTLE_FAIL_MAX):
        assert sc.bump_settle_fail(rec["id"]) == i
        assert sc.get_call(rec["id"])["status"] == "open"
    # 第 5 次达到阈值 → error 可见,禁无限静默空转
    assert sc.bump_settle_fail(rec["id"]) == sc.SETTLE_FAIL_MAX
    assert sc.get_call(rec["id"])["status"] == "error"


def test_summary_excludes_is_test_and_month_filter(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    a, _ = sc.create_call("u1", "600519", "bull", entry_date="2026-07-03", entry_price=1800.0)
    sc.settle_call(a["id"], settle_date="2026-07-10", exit_price=1908.0, ret_pct=6.0, outcome="hit", call_score=6.0)
    b, _ = sc.create_call("u1", "000858", "bear", entry_date="2026-07-03", entry_price=130.0)
    sc.settle_call(b["id"], settle_date="2026-07-10", exit_price=133.9, ret_pct=-3.0, outcome="miss", call_score=-3.0)
    # settle-now 自测单:必须被 summary/北极星排除
    t, _ = sc.create_call("u1", "601318", "bull", entry_date="2026-07-03", entry_price=50.0, is_test=True)
    sc.settle_call(t["id"], settle_date="2026-07-10", exit_price=60.0, ret_pct=20.0, outcome="hit", call_score=15.0)

    s = sc.summary_for_user("u1")
    assert s == {"settled": 2, "hit": 1, "miss": 1, "flat": 0, "avg_move_pct": 1.5}
    # 月过滤:6月无兑现 → 全零(样本量如实为0,渲染层据此不出战绩行)
    assert sc.summary_for_user("u1", month="2026-06")["settled"] == 0


def test_daily_create_count_by_bj_day(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    # 固定 created_at:UTC 03:00 = 北京 11:00,属北京日 2026-07-10
    monkeypatch.setattr(sc, "utc_now_iso", lambda: "2026-07-10T03:00:00+00:00")
    sc.create_call("u1", "600519", "bull", entry_date="2026-07-10", entry_price=1800.0)
    rec, _ = sc.create_call("u1", "000858", "bear", entry_date="2026-07-10", entry_price=130.0)
    sc.cancel_call(rec["id"])  # 撤销不返还当日额度(防刷)
    assert sc.count_created_on_bj_day("u1", "2026-07-10") == 2
    assert sc.count_created_on_bj_day("u1", "2026-07-09") == 0
    # UTC 区间换算:北京日 D = [D-1T16:00Z, DT16:00Z)
    assert sc.bj_day_utc_range("2026-07-10") == ("2026-07-09T16:00:00+00:00", "2026-07-10T16:00:00+00:00")


# ---------------- 触点层:quiet hours / 日预算 / push_log 去重 ----------------

def test_quiet_hours_and_push_gate():
    assert sc.quiet_hours(datetime(2026, 7, 10, 7, 59)) is True
    assert sc.quiet_hours(datetime(2026, 7, 10, 8, 0)) is False
    assert sc.quiet_hours(datetime(2026, 7, 10, 20, 59)) is False
    assert sc.quiet_hours(datetime(2026, 7, 10, 21, 0)) is True
    # 日预算判定:未发过+非静默才发
    assert sc.should_send_push(datetime(2026, 7, 10, 16, 0), already_sent_today=False) is True
    assert sc.should_send_push(datetime(2026, 7, 10, 16, 0), already_sent_today=True) is False
    assert sc.should_send_push(datetime(2026, 7, 10, 22, 0), already_sent_today=False) is False


def test_push_log_one_per_day_per_kind(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    assert sc.has_pushed("u1", "2026-07-10") is False
    assert sc.log_push("u1", "2026-07-10") is True
    assert sc.has_pushed("u1", "2026-07-10") is True
    assert sc.log_push("u1", "2026-07-10") is False  # 同日同类第二次 → 拒
    # kind 维度独立(周报接入免 ALTER)、次日重置
    assert sc.log_push("u1", "2026-07-10", kind="weekly") is True
    assert sc.log_push("u1", "2026-07-11") is True


# ---------------- digest 固定模板:样本量铁律 + 免责 + 自测标记 ----------------

def test_render_settlement_digest_fixed_template():
    items = [
        {"symbol": "600519", "direction": "bull", "entry_date": "2026-07-03", "entry_close": 50.0,
         "settle_close": 53.0, "move_pct": 6.0, "verdict": "hit", "conviction": 2, "horizon": 5, "is_test": False},
        {"symbol": "000858", "direction": "bear", "entry_date": "2026-07-03", "entry_close": 130.0,
         "settle_close": 133.9, "move_pct": 3.0, "verdict": "miss", "conviction": 3, "horizon": 5, "is_test": False},
        {"symbol": "601318", "direction": "bull", "entry_date": "2026-07-07", "entry_close": 50.0,
         "settle_close": 50.2, "move_pct": 0.4, "verdict": "flat", "conviction": 1, "horizon": 3, "is_test": True},
    ]
    month = {"settled": 7, "hit": 4, "miss": 2, "flat": 1, "avg_move_pct": 2.3}
    text = sc.render_settlement_digest(items, month_summary=month, first_settlement=True, day="2026-07-10")

    assert text.startswith("你在DeepFocus的第一笔判断兑现了。")
    assert "【DeepFocus·判断兑现】7月10日" in text
    assert "✅ 看多 600519：07-03收盘起算，5个交易日 +6.00%，命中" in text
    assert "❌ 看空 000858·重仓：07-03收盘起算，5个交易日 +3.00%，未中" in text
    assert "⚪ [自测]看多 601318·轻仓" in text  # settle-now 自测在文案里如实标注
    # 展示铁律:月度战绩永远带样本量,赢亏全量如实
    assert "本月战绩：已兑现7笔 · 命中4 / 未中2 / 持平1 · 平均+2.30%" in text
    assert text.rstrip().endswith(sc.DIGEST_DISCLAIMER)
    # 品牌合规:只用 DeepFocus,不出现旧名
    assert "道财经" not in text and "DAO" not in text


def test_render_settlement_digest_no_month_line_without_samples():
    items = [{"symbol": "600519", "direction": "bull", "entry_date": "2026-07-03", "entry_close": 50.0,
              "settle_close": 53.0, "move_pct": 6.0, "verdict": "hit", "conviction": 2, "horizon": 5,
              "is_test": False}]
    text = sc.render_settlement_digest(items, month_summary={"settled": 0})
    assert "本月战绩" not in text  # 零样本不编数字
    assert sc.DIGEST_DISCLAIMER in text


# ---------------- 建库/note 截断/env 开关缺省 ----------------

def test_note_truncated_and_defaults(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    rec, _ = sc.create_call("u1", "600519", "bull", entry_date="2026-07-10", entry_price=1800.0,
                            note="长" * 200, conviction=9, horizon_days=7)
    assert len(rec["note"]) == sc.NOTE_MAX_LEN  # 表态即笔记,≤140字
    assert rec["conviction"] == sc.DEFAULT_CONVICTION and rec["horizon_days"] == sc.DEFAULT_HORIZON
    assert rec["market"] == "CN" and rec["entry_ccy"] == "CNY" and rec["is_test"] is False


def test_env_gates_default_closed(monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_CALLS_ENABLED", raising=False)
    monkeypatch.delenv("DEEPFOCUS_CALLS_ALLOWED_USERS", raising=False)
    assert sc.calls_enabled() is False  # 总闸默认关
    assert sc.allowed_call_users() == {"lx199710"}  # 白名单默认仅 lx199710
    monkeypatch.setenv("DEEPFOCUS_CALLS_ENABLED", "1")
    monkeypatch.setenv("DEEPFOCUS_CALLS_ALLOWED_USERS", "lx199710, openclaw-svc")
    assert sc.calls_enabled() is True
    assert sc.allowed_call_users() == {"lx199710", "openclaw-svc"}
