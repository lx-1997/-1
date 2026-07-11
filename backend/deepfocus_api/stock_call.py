from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Tuple

from .shared_utils import utc_now_iso

"""
战绩闭环 · 核心台账(stock_call)。见 docs/战绩闭环-数据飞轮设计.md §1/§3 与 [[moat-data-flywheel-track-record]]。

一次 call = 用户对某标的的方向判断,系统快照入场、到期用真实行情自动兑现打分——
「记录并验证一个判断」,非荐股。本模块只管「台账存取 + 纯函数打分/归一/渲染」,保持纯净可单测;
行情取数、API 门控、结算扫描器都在调用方(main.py),digest 发送走 push_to_user 直调
(★不挂 weixin_schedule personal——MAX_PERSONAL_PER_USER=1 会挤掉用户已有的自选订阅)。

铁律(评审 must_fix,别改回去):
- 独立 sqlite3,不用 data_store(KEEP_PER_SERIES=500 修剪会吞台账历史,不可篡改历史是护城河的根);
- 唯一 open 索引是 (user_id, symbol) 两键**无 direction**——堵死同标的同时开多空双单的结构性骑墙;
- grade() 两端必须同出结算时刻同一条前复权序列(除权除息自动抹平),
  fetch_market_quotes 快照 entry_price 仅作防篡改证据锚,**绝不作打分输入**;
- 入场统一按收盘归一:盘中表态→当日收盘,盘后/周末/节假日→下一交易日收盘(堵盘后消息套利吃次日跳空);
- 日历(CN_MARKET_HOLIDAYS)未覆盖目标年份→告警拒算,绝不静默按「无节假日」错算;
- 打分口径带 grade_ver 落库,口径迁移靠留存原始输入全量重算,禁止新旧口径混存。
"""

logger = logging.getLogger("deepfocus.stock_call")

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_CALLS_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".stock_call.sqlite3"),
    )
)

BJ_TZ = timezone(timedelta(hours=8))

# ---- 打分口径(§3):改任何一个都要递增 GRADE_VER 并全量重算,禁止混存 ----
GRADE_VER = 1                                  # v1 = 绝对 move%(bench_move 留空,基准 alpha P1 后补)
DEAD_ZONE_PCT = 1.0                            # dead-zone ±1%:骑墙算 flat 不得分
CAP_PCT = 15.0                                 # 单条封顶 ±15%:防一击暴富主导声誉
CONVICTION_MULT = {1: 0.6, 2: 1.0, 3: 1.4}     # 信念乘数 轻/标/重
_DIRECTIONS = ("bull", "bear")
_STATUSES = ("open", "settled", "void", "canceled", "error")
HORIZON_CHOICES = (3, 5, 20)                   # 兑现视野(交易日)
DEFAULT_HORIZON = 5
DEFAULT_CONVICTION = 2
NOTE_MAX_LEN = 140                             # 表态即笔记:一句话理由上限

# ---- 业务规则常量(API/扫描器层执行,集中定义防口径漂移) ----
DAILY_CREATE_LIMIT = 20        # 每日新建上限,防海量撒网
CANCEL_WINDOW_MIN = 60         # 撤销窗口:仅创建后 ≤60min
CANCEL_MAX_DRIFT_PCT = 1.5     # 撤销时 |现价-入场快照| 须 <1.5%;行情取不到=拒绝撤销(fail-closed)
SUSPEND_GRACE_DAYS = 10        # 停牌顺延宽限:horizon+10 交易日仍无价 → void
SETTLE_FAIL_MAX = 5            # 单条结算失败超阈值 → error 状态可见,禁无限静默空转
MARKET_CLOSE_HM = (15, 0)      # A股收盘(北京)
PUSH_QUIET_END_H = 8           # 推送静默时段:08:00 前
PUSH_QUIET_START_H = 21        # 推送静默时段:21:00 起(顺延次日并入)


def calls_enabled() -> bool:
    """总闸,默认关。结算扫描器与 API 都要看它。"""
    return os.getenv("DEEPFOCUS_CALLS_ENABLED", "0") == "1"


def allowed_call_users() -> set:
    """白名单(逗号分隔),默认 lx199710。后端硬闸——前端白名单只是遮罩。"""
    raw = os.getenv("DEEPFOCUS_CALLS_ALLOWED_USERS", "lx199710")
    return {u.strip() for u in raw.split(",") if u.strip()}


# ============================ 交易日历(纯函数) ============================

class CalendarCoverageError(ValueError):
    """CN_MARKET_HOLIDAYS 未覆盖目标年份——告警拒算(horizon=20 跨年时必现),绝不静默错算。"""


def _market_holidays() -> set:
    # 惰性取,单一可信源:日历在 ai_fund.CN_MARKET_HOLIDAYS 按年维护(每年12月更新次年),此处不复制一份。
    from .ai_fund import CN_MARKET_HOLIDAYS
    return set(CN_MARKET_HOLIDAYS)


def _covered_years(holidays: Collection[str]) -> set:
    return {d[:4] for d in holidays}


def _ensure_calendar_covers(start: date, end: date, holidays: Collection[str]) -> None:
    """[start, end] 触及的每个年份都必须在日历里,缺一年即告警拒算。"""
    covered = _covered_years(holidays)
    missing = sorted({str(y) for y in range(start.year, end.year + 1)} - covered)
    if missing:
        logger.warning("stock_call 日历缺失年份 %s(区间 %s→%s),拒绝计算——请更新 ai_fund.CN_MARKET_HOLIDAYS",
                       ",".join(missing), start.isoformat(), end.isoformat())
        raise CalendarCoverageError(f"交易日历未覆盖年份: {','.join(missing)}")


def _is_trading_date(d: date, holidays: Collection[str]) -> bool:
    # 与 ai_fund._is_trading_day 同口径:周一~五 且 非法定休市日(A股不随调休在周末开市)
    return d.weekday() < 5 and d.isoformat() not in holidays


def trading_days_between(entry_date: str, today: str) -> int:
    """(entry_date, today] 区间内的交易日数——「入场收盘后过了几个交易日」,≥horizon 才结算。

    现有 ai_fund._is_trading_day 只是单日布尔,这里补区间计数;today ≤ entry_date 返回 0。
    区间触及未维护年份 → CalendarCoverageError(告警拒算)。"""
    start = date.fromisoformat(entry_date)
    end = date.fromisoformat(today)
    if end <= start:
        return 0
    holidays = _market_holidays()
    _ensure_calendar_covers(start, end, holidays)
    n = 0
    d = start + timedelta(days=1)
    while d <= end:
        if _is_trading_date(d, holidays):
            n += 1
        d += timedelta(days=1)
    return n


def normalize_entry_date(created_at: datetime, *, bar_dates: Optional[Collection[str]] = None) -> Optional[str]:
    """表态时刻 → 入场交易日(统一按收盘起算,杀掉日内口径变体):

    - 交易日盘中(<15:00)表态 → 当日收盘起算;
    - 盘后(≥15:00)/周末/节假日 → 下一交易日收盘(堵死盘后消息套利吃次日跳空);
    - 传入 bar_dates(该标的实际有K线的日期集)且归一日无 bar(停牌) → 返回 None,调用方置 void;
    - 归一过程触及日历未覆盖年份 → CalendarCoverageError(告警拒算)。

    created_at:北京时间;带 tz 会先转 BJ_TZ,naive 视为已是北京时间。"""
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(BJ_TZ)
    holidays = _market_holidays()
    d = created_at.date()
    _ensure_calendar_covers(d, d, holidays)
    before_close = (created_at.hour, created_at.minute) < MARKET_CLOSE_HM
    if not (before_close and _is_trading_date(d, holidays)):
        # 顺延到下一交易日(春节最长连休不过十来天,30天护栏纯属防御)
        for _ in range(30):
            d = d + timedelta(days=1)
            _ensure_calendar_covers(d, d, holidays)
            if _is_trading_date(d, holidays):
                break
        else:  # pragma: no cover - 有效日历下不可达
            raise CalendarCoverageError(f"自 {created_at.date().isoformat()} 起 30 天内找不到交易日,日历疑似损坏")
    entry = d.isoformat()
    if bar_dates is not None and entry not in bar_dates:
        return None  # 表态日停牌无bar → void(不顺延入场,防挑停牌股钻空)
    return entry


# ============================ 打分(纯函数,§3) ============================

def grade(
    entry_close: float,
    settle_close: float,
    direction: str,
    conviction: int = DEFAULT_CONVICTION,
    *,
    dead_zone: float = DEAD_ZONE_PCT,
    cap: float = CAP_PCT,
    conviction_mult: Optional[Dict[int, float]] = None,
) -> Dict[str, Any]:
    """兑现打分。★契约:entry_close/settle_close 必须同出**结算时刻同一条前复权序列**的两端
    (adj[entry_date]→adj[settle_date],除权除息自动抹平);fetch_market_quotes 快照仅作防篡改
    证据锚,绝不传进来。

    返回 {move_pct, ret_pct(方向化,v1 无基准=alpha), outcome(hit/miss/flat), call_score, grade_ver}:
    - dead-zone:|方向化收益| < dead_zone → flat(骑墙不奖励,不计命中率分母);
    - call_score = clamp(方向化收益, ±cap) × 信念乘数(封顶防一击暴富主导)。"""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction 必须是 {_DIRECTIONS} 之一: {direction!r}")
    mult_map = conviction_mult or CONVICTION_MULT
    if conviction not in mult_map:
        raise ValueError(f"conviction 必须是 {sorted(mult_map)} 之一: {conviction!r}")
    if not entry_close or entry_close <= 0 or not settle_close or settle_close <= 0:
        raise ValueError(f"两端收盘价必须为正: entry={entry_close!r} settle={settle_close!r}")
    move_pct = (settle_close - entry_close) / entry_close * 100.0
    ret_pct = move_pct if direction == "bull" else -move_pct  # v1 绝对口径;P1 接基准后=(move−bench)×方向
    if ret_pct >= dead_zone:
        outcome = "hit"
    elif ret_pct <= -dead_zone:
        outcome = "miss"
    else:
        outcome = "flat"
    call_score = max(-cap, min(cap, ret_pct)) * mult_map[conviction]
    return {
        "move_pct": round(move_pct, 4),
        "ret_pct": round(ret_pct, 4),
        "outcome": outcome,
        "call_score": round(call_score, 4),
        "grade_ver": GRADE_VER,
    }


# ============================ 触点层(纯函数,零LLM固定模板) ============================

_VERDICT_RENDER = {"hit": ("✅", "命中"), "miss": ("❌", "未中"), "flat": ("⚪", "持平")}
_DIRECTION_CN = {"bull": "看多", "bear": "看空"}
_CONVICTION_CN = {1: "·轻仓", 2: "", 3: "·重仓"}  # 标准仓不标注,不增噪

DIGEST_DISCLAIMER = "以上为历史表态的自动兑现记录，仅作事实回顾，不构成投资建议。"


def _fmt_pct(v: float) -> str:
    return f"{v:+.2f}%"


def render_settlement_digest(
    items: List[Dict[str, Any]],
    *,
    month_summary: Optional[Dict[str, Any]] = None,
    first_settlement: bool = False,
    day: Optional[str] = None,
) -> str:
    """当日兑现聚合 digest(一个用户一条,严禁逐笔推)。零 LLM、逐字确定(金十式排版先例)。

    ★输入 schema 冻结(与后续迭代的契约,改字段=改这里+设计文档批注):
      每条 item: {symbol, direction('bull'/'bear'), entry_date('YYYY-MM-DD'), entry_close, settle_close,
                  move_pct, verdict('hit'/'miss'/'flat'), conviction(1/2/3), horizon(交易日数), is_test}
      month_summary(可选,来自 summary_for_user): {settled, hit, miss, flat, avg_move_pct}
    展示铁律:月度战绩永远带样本量、赢亏全量如实;文末免责;品牌只用 DeepFocus。"""
    lines: List[str] = []
    if first_settlement:
        lines.append("你在DeepFocus的第一笔判断兑现了。")
    header = "【DeepFocus·判断兑现】"
    if day:
        header += f"{int(day[5:7])}月{int(day[8:10])}日"
    lines.append(header)
    for it in items:
        icon, word = _VERDICT_RENDER.get(str(it.get("verdict")), ("⚪", "持平"))
        prefix = "[自测]" if it.get("is_test") else ""
        entry_date = str(it.get("entry_date") or "")
        entry_mmdd = entry_date[5:] if len(entry_date) == 10 else entry_date
        lines.append(
            f"{icon} {prefix}{_DIRECTION_CN.get(str(it.get('direction')), '?')} {it.get('symbol')}"
            f"{_CONVICTION_CN.get(int(it.get('conviction') or DEFAULT_CONVICTION), '')}："
            f"{entry_mmdd}收盘起算，{int(it.get('horizon') or 0)}个交易日 "
            f"{_fmt_pct(float(it.get('move_pct') or 0.0))}，{word}"
        )
    ms = month_summary or {}
    settled = int(ms.get("settled") or 0)
    if settled > 0:  # 样本量是铁律的一部分:没有已兑现样本就不渲染战绩行,绝不编数字
        lines.append(
            f"本月战绩：已兑现{settled}笔 · 命中{int(ms.get('hit') or 0)} / 未中{int(ms.get('miss') or 0)}"
            f" / 持平{int(ms.get('flat') or 0)} · 平均{_fmt_pct(float(ms.get('avg_move_pct') or 0.0))}"
        )
    lines.append(DIGEST_DISCLAIMER)
    return "\n".join(lines)


def quiet_hours(now_bj: Optional[datetime] = None) -> bool:
    """推送静默时段:北京时间 08:00-21:00 之外为 True(顺延次日并入,不半夜打扰)。"""
    now = now_bj or datetime.now(BJ_TZ)
    return now.hour < PUSH_QUIET_END_H or now.hour >= PUSH_QUIET_START_H


def should_send_push(now_bj: datetime, already_sent_today: bool) -> bool:
    """日预算判定:一日一条(以发送 day 为准,already_sent_today 来自 settle_push_log)+ 非静默时段。"""
    return (not already_sent_today) and (not quiet_hours(now_bj))


def bj_day_utc_range(day: str) -> Tuple[str, str]:
    """北京日 D → UTC ISO 区间 [D-1T16:00, DT16:00)。created_at 存 UTC ISO,拿它做「每日新建上限」的 SQL 比较。"""
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone.utc) - timedelta(days=1)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


# ============================ 台账(独立 sqlite3,风格照 weixin_schedule) ============================

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 结算扫描任务(to_thread 工作线程)与 API 请求(事件循环)并发写;busy_timeout 让偶发写锁竞争
    # 等待而非立刻抛 "database is locked"(默认 busy_timeout=0)。
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_call_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_call (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           TEXT NOT NULL,
                symbol            TEXT NOT NULL,              -- 归一码 600519(P0 仅A股,HK/US 在API层422)
                market            TEXT NOT NULL DEFAULT 'CN',
                direction         TEXT NOT NULL,              -- 'bull' / 'bear'
                conviction        INTEGER NOT NULL DEFAULT 2, -- 1轻/2标/3重
                horizon_days      INTEGER NOT NULL DEFAULT 5, -- 兑现视野(交易日 3/5/20)
                target_price      REAL,                       -- 可选目标价(仅展示,不参与打分)
                source_ref        TEXT,                       -- 触发来源 panel / news:<id>(归因)
                note              TEXT,                       -- ≤140字可选理由(表态即笔记)
                created_at        TEXT NOT NULL,              -- UTC ISO
                entry_date        TEXT NOT NULL,              -- 入场交易日(北京),normalize_entry_date 归一
                entry_price       REAL NOT NULL,              -- 入场快照价(防篡改证据锚;打分用前复权序列)
                entry_ccy         TEXT NOT NULL DEFAULT 'CNY',
                status            TEXT NOT NULL DEFAULT 'open', -- open/settled/void/canceled/error
                settle_date       TEXT,                       -- 兑现交易日
                exit_price        REAL,                       -- 前复权兑现价
                ret_pct           REAL,                       -- 方向化收益%(grade_ver=1 无基准)
                bench_move        REAL,                       -- 同窗口基准涨跌%(P1 基准alpha预留,v1 留空)
                outcome           TEXT,                       -- hit / miss / flat
                call_score        REAL,                       -- 封顶×信念乘数后的本条得分
                grade_ver         INTEGER,                    -- 打分口径版本(迁移留痕,全量重算依据)
                is_test           INTEGER NOT NULL DEFAULT 0, -- settle-now 自测标记,summary/指标默认排除
                seen_at           TEXT,                       -- 站内未读:settled 且空=未读红点
                settle_fail_count INTEGER NOT NULL DEFAULT 0, -- 结算失败计数,超 SETTLE_FAIL_MAX 转 error
                settled_at        TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_call_open ON stock_call(status, entry_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_call_user ON stock_call(user_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_call_symbol ON stock_call(symbol, status, direction)")
        # ★两键无 direction:同用户同标的仅一条 open——多空双开必中一单的结构性骑墙在台账层就不生产
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_call_open ON stock_call(user_id, symbol) WHERE status='open'"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settle_push_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                day        TEXT NOT NULL,               -- 实际发送日(北京);顺延消息记发送 day,一日一条以此为准
                kind       TEXT NOT NULL DEFAULT 'settle', -- 首日带齐:结算='settle',周报接入免 ALTER
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_settle_push ON settle_push_log(user_id, day, kind)"
        )
        conn.commit()


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    d["is_test"] = bool(d.get("is_test", 0))
    # 站内未读:已兑现且没看过。settled→红点/toast,打开弹层回写 seen_at
    d["unseen"] = d.get("status") == "settled" and not d.get("seen_at")
    return d


def create_call(
    user_id: str,
    symbol: str,
    direction: str,
    *,
    entry_date: str,
    entry_price: float,
    market: str = "CN",
    conviction: int = DEFAULT_CONVICTION,
    horizon_days: int = DEFAULT_HORIZON,
    target_price: Optional[float] = None,
    source_ref: Optional[str] = None,
    note: Optional[str] = None,
    entry_ccy: str = "CNY",
    is_test: bool = False,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """插一条 open call。撞唯一 open 索引(该用户该标的已有 open,无论方向)→ 幂等返回已有那条。

    返回 (记录, 是否新建)。快照校验(A股代码/白名单/每日上限/行情取数)在 API 层,这里只守台账不变量。"""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction 必须是 {_DIRECTIONS} 之一: {direction!r}")
    if conviction not in CONVICTION_MULT:
        conviction = DEFAULT_CONVICTION
    if horizon_days not in HORIZON_CHOICES:
        horizon_days = DEFAULT_HORIZON
    note = (note or "").strip()[:NOTE_MAX_LEN] or None
    init_call_db()
    now = utc_now_iso()
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO stock_call
                    (user_id, symbol, market, direction, conviction, horizon_days, target_price,
                     source_ref, note, created_at, entry_date, entry_price, entry_ccy, status, is_test)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)
                """,
                (user_id, symbol, market, direction, conviction, horizon_days, target_price,
                 source_ref, note, now, entry_date, float(entry_price), entry_ccy, 1 if is_test else 0),
            )
            conn.commit()
            return get_call(int(cur.lastrowid)), True
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT * FROM stock_call WHERE user_id=? AND symbol=? AND status='open'",
                (user_id, symbol),
            ).fetchone()
            return _row(row), False  # 已在跟踪(含反向单——两键索引刻意如此)


def get_call(call_id: int) -> Optional[Dict[str, Any]]:
    init_call_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM stock_call WHERE id=?", (call_id,)).fetchone()
    return _row(row)


def list_open_calls() -> List[Dict[str, Any]]:
    """结算扫描器取单:全部 open,按入场日从旧到新。"""
    init_call_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM stock_call WHERE status='open' ORDER BY entry_date, id"
        ).fetchall()
    return [r for r in (_row(x) for x in rows) if r]


def list_user_calls(user_id: str, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """我的表态(新→旧),每条带 unseen 标记(settled 且 seen_at 空)。"""
    init_call_db()
    q = "SELECT * FROM stock_call WHERE user_id=?"
    args: list[Any] = [user_id]
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    with _connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return [r for r in (_row(x) for x in rows) if r]


def count_created_on_bj_day(user_id: str, day: str) -> int:
    """该用户北京日 day 内新建的 call 数(含已撤销——撤销不返还额度,防刷)。每日上限判定用。"""
    init_call_db()
    start, end = bj_day_utc_range(day)
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM stock_call WHERE user_id=? AND created_at>=? AND created_at<?",
            (user_id, start, end),
        ).fetchone()
    return int(row["n"] if row else 0)


def cancel_call(call_id: int) -> bool:
    """置 canceled(仅 open 可撤)。60min 窗口/±1.5% 异动/行情失败拒撤(fail-closed)由 API 层执行。"""
    init_call_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE stock_call SET status='canceled', settled_at=? WHERE id=? AND status='open'",
            (utc_now_iso(), call_id),
        )
        conn.commit()
    return cur.rowcount > 0


def settle_call(
    call_id: int,
    *,
    settle_date: str,
    exit_price: float,
    ret_pct: float,
    outcome: str,
    call_score: float,
    grade_ver: int = GRADE_VER,
    bench_move: Optional[float] = None,
) -> bool:
    """兑现落库(仅 open→settled)。字段由 grade() 产出,原始两端价留存,口径迁移可全量重算。"""
    init_call_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE stock_call
               SET status='settled', settle_date=?, exit_price=?, ret_pct=?, outcome=?,
                   call_score=?, grade_ver=?, bench_move=?, settled_at=?
             WHERE id=? AND status='open'
            """,
            (settle_date, float(exit_price), float(ret_pct), outcome,
             float(call_score), int(grade_ver), bench_move, utc_now_iso(), call_id),
        )
        conn.commit()
    return cur.rowcount > 0


def void_call(call_id: int) -> bool:
    """置 void(不计):表态日停牌 / 超宽限仍无价 / 退市 / 数据缺失。仅 open 可置。"""
    init_call_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE stock_call SET status='void', settled_at=? WHERE id=? AND status='open'",
            (utc_now_iso(), call_id),
        )
        conn.commit()
    return cur.rowcount > 0


def bump_settle_fail(call_id: int, *, max_fail: int = SETTLE_FAIL_MAX) -> int:
    """结算失败计数 +1;达到阈值自动转 error 状态(用户可见)——禁无限静默空转。返回新计数。"""
    init_call_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE stock_call SET settle_fail_count=settle_fail_count+1 WHERE id=?", (call_id,)
        )
        row = conn.execute("SELECT settle_fail_count FROM stock_call WHERE id=?", (call_id,)).fetchone()
        n = int(row["settle_fail_count"]) if row else 0
        if n >= max_fail:
            conn.execute(
                "UPDATE stock_call SET status='error' WHERE id=? AND status='open'", (call_id,)
            )
        conn.commit()
    return n


def mark_call_test(call_id: int) -> bool:
    """settle-now 自测强制标记(B1 评审 must_fix):经管理端立即结算的 call 一律补打 is_test=1,
    summary/北极星指标默认排除——保住台账从 D0 的真实性。"""
    init_call_db()
    with _connect() as conn:
        cur = conn.execute("UPDATE stock_call SET is_test=1 WHERE id=?", (call_id,))
        conn.commit()
    return cur.rowcount > 0


def mark_calls_seen(user_id: str) -> int:
    """打开战绩弹层即回写 seen_at,清未读红点。返回清掉的条数。"""
    init_call_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE stock_call SET seen_at=? WHERE user_id=? AND status='settled' AND seen_at IS NULL",
            (utc_now_iso(), user_id),
        )
        conn.commit()
    return cur.rowcount


def summary_for_user(user_id: str, *, month: Optional[str] = None) -> Dict[str, Any]:
    """即时 SQL 聚合战绩(不建 user_reputation 表,声誉公式 P1 有真实样本后再上)。

    ★排除 is_test(settle-now 自测不进战绩/北极星);month='YYYY-MM' 按兑现月过滤(digest 月度战绩用)。
    返回 {settled, hit, miss, flat, avg_move_pct}——settled 就是样本量,展示永远带它。"""
    init_call_db()
    q = ("SELECT COUNT(*) AS settled,"
         " SUM(CASE WHEN outcome='hit' THEN 1 ELSE 0 END) AS hit,"
         " SUM(CASE WHEN outcome='miss' THEN 1 ELSE 0 END) AS miss,"
         " SUM(CASE WHEN outcome='flat' THEN 1 ELSE 0 END) AS flat,"
         " AVG(ret_pct) AS avg_move_pct"
         " FROM stock_call WHERE user_id=? AND status='settled' AND is_test=0")
    args: list[Any] = [user_id]
    if month:
        q += " AND settle_date LIKE ?"
        args.append(f"{month}-%")
    with _connect() as conn:
        row = conn.execute(q, args).fetchone()
    settled = int(row["settled"] or 0) if row else 0
    return {
        "settled": settled,
        "hit": int(row["hit"] or 0) if row else 0,
        "miss": int(row["miss"] or 0) if row else 0,
        "flat": int(row["flat"] or 0) if row else 0,
        "avg_move_pct": round(float(row["avg_move_pct"]), 2) if row and row["avg_move_pct"] is not None else 0.0,
    }


def has_pushed(user_id: str, day: str, kind: str = "settle") -> bool:
    """该用户该(发送)日该类推送是否已发过——一日一条铁律的判定输入。"""
    init_call_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM settle_push_log WHERE user_id=? AND day=? AND kind=?",
            (user_id, day, kind),
        ).fetchone()
    return row is not None


def log_push(user_id: str, day: str, kind: str = "settle") -> bool:
    """记一次已发送(唯一索引兜底并发双发)。返回 False = 当日已有记录,调用方不应重发。"""
    init_call_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO settle_push_log (user_id, day, kind, created_at) VALUES (?,?,?,?)",
            (user_id, day, kind, utc_now_iso()),
        )
        conn.commit()
    return cur.rowcount > 0
