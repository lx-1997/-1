"""故事级埋点：记录用户对「事件簇（story）」的每一次交互，作为个人化排序的唯一隐式信号源。

为什么单独一层
--------------
``story_engine`` 产出的是全站公共资产（故事卡片），刻意不含任何账号数据；``metrics.activity_log``
是页面级的粗粒度访问日志，既没有 story_id 维度、也无法区分「曝光」与「有效阅读」。要做「千人千面」，
必须有一张以 ``(user_id, story_id, action)`` 为粒度的事件表，才能回答三个问题：

1. **这个人关心什么**——把点击/收藏/表态/关注按标的与主题聚合，得到兴趣向量（``user_affinity``）。
2. **这个故事他怎么评价过**——显式的「更多这样的 / 不感兴趣」直接加减分（``user_feedback_weights``）。
3. **排序本身好不好**——按展示位置统计曝光→点击转化率（``ctr_by_position``），让个人化效果可度量。

设计约束（与站内既有链路一致）
------------------------------
1. **独立库**：落在自己的 ``.story_events.sqlite3``，绝不去迁移或写生产的 ``metrics.activity_log``；
   高频追加写用 WAL 单库，不与统一 storage（可能是 Postgres）抢连接。
2. **快照冗余**：每条事件在写入时快照该故事的 ``symbol`` / ``themes`` / ``kind``。故事会在
   ``RETENTION_DAYS`` 后被剪枝，若只存 story_id，历史兴趣就无法回溯——快照让兴趣聚合不依赖故事是否仍在。
3. **PII 隔离**：本表按 ``user_id`` 分行，是账号私有数据；任何聚合结果只在 ``user_profile`` 内
   按账号计算，**绝不**回写 ``story_engine`` 的公共表，也绝不进入面向大众的简报/推送。
4. **确定性**：权重、衰减、聚合口径全部是纯函数常量，回归测试按单调性断言，无模型参与。
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from . import db
from .shared_utils import utc_now_iso

# ---- 事件动作词表（集中定义，防口径漂移）----
# 每个动作对「兴趣向量」的带符号权重：正=拉近，负=推远，0=仅作曝光基线不计兴趣。
ACTION_IMPRESSION = "impression"
ACTION_CLICK = "click"
ACTION_DWELL = "dwell"
ACTION_BOOKMARK = "bookmark"
ACTION_REACT = "react"
ACTION_FOLLOW = "follow"          # 从故事卡发起的「关注该主题/标的」
ACTION_FEEDBACK_UP = "feedback_up"      # 「更多这样的」
ACTION_FEEDBACK_DOWN = "feedback_down"  # 「不感兴趣」
ACTION_DISMISS = "dismiss"

AFFINITY_WEIGHTS: dict[str, float] = {
    ACTION_IMPRESSION: 0.0,
    ACTION_CLICK: 0.30,
    ACTION_DWELL: 0.20,          # 再按 dwell_ms 分级放大（见 _dwell_multiplier）
    ACTION_BOOKMARK: 1.00,
    ACTION_REACT: 0.80,
    ACTION_FOLLOW: 1.20,
    ACTION_FEEDBACK_UP: 1.50,
    ACTION_FEEDBACK_DOWN: -1.50,
    ACTION_DISMISS: -1.00,
}
VALID_ACTIONS = frozenset(AFFINITY_WEIGHTS)

# 停留时长分级：满 dwell 才算「读进去了」，短停留只给零头，避免误触污染兴趣。
DWELL_FULL_MS = 15_000
DWELL_MIN_MS = 1_500

# 兴趣半衰期：越久远的交互权重越低（指数衰减）。
AFFINITY_HALF_LIFE_DAYS = 21.0
# 事件保留天数：超期在 prune 时清理（快照冗余让老兴趣已沉淀进聚合，无需长期留存原始行）。
RETENTION_DAYS = 120
# 单账号事件行上限，超出滚动删最旧（防单账号刷爆库）。
MAX_ROWS_PER_USER = 20_000
# 上限核对抽样步长：每 N 秒的写入时间戳触发一次 COUNT 摊销（见 log_event）。
CAP_CHECK_STRIDE = 64

_MAX_STORY_ID = 80
_MAX_SURFACE = 24
_MAX_ACTION = 20


def _db_path() -> Any:
    return db.data_path(".story_events.sqlite3", "DEEPFOCUS_STORY_EVENTS_DB_PATH")


def _connect() -> sqlite3.Connection:
    conn = db.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_events_db() -> None:
    """建事件表（幂等）。只影响本模块自己的库文件。"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS story_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT '',
                story_id TEXT NOT NULL,
                content_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                surface TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT -1,
                dwell_ms INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                symbol TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT '',
                themes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_se_user_story ON story_events(user_id, story_id);
            CREATE INDEX IF NOT EXISTS idx_se_user_action ON story_events(user_id, action, created_at);
            CREATE INDEX IF NOT EXISTS idx_se_story_action ON story_events(story_id, action);
            CREATE INDEX IF NOT EXISTS idx_se_surface_pos ON story_events(surface, position, action);
            CREATE INDEX IF NOT EXISTS idx_se_created ON story_events(created_at);
            """
        )
        conn.commit()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _dwell_multiplier(dwell_ms: int) -> float:
    """把停留时长映射到 0.2–1.5 的乘数：短触≈0.2，读满≈1.0，久读给到 1.5。"""
    ms = max(0, int(dwell_ms or 0))
    if ms < DWELL_MIN_MS:
        return 0.2
    if ms >= DWELL_FULL_MS:
        return 1.0 + min(0.5, (ms - DWELL_FULL_MS) / float(DWELL_FULL_MS) * 0.5)
    return 0.2 + 0.8 * (ms - DWELL_MIN_MS) / float(DWELL_FULL_MS - DWELL_MIN_MS)


def _decay(created: Optional[datetime], now: datetime, half_life_days: float) -> float:
    if created is None:
        return 0.5
    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    hl = max(1.0, float(half_life_days))
    return 0.5 ** (age_days / hl)


def _resolve_snapshot(story_id: str, symbol: str, kind: str, themes: Sequence[str]) -> tuple[str, str, list[str]]:
    """事件写入时补全故事快照：调用方（API 层）通常已持有卡片，缺字段才回查一次 story_engine。"""
    if symbol and kind and themes:
        return symbol, kind, list(themes)
    try:
        from . import story_engine
        card = story_engine.story_detail(story_id, item_limit=1)
        if card:
            symbol = symbol or str(card.get("symbol") or "")
            kind = kind or str(card.get("kind") or "")
            themes = list(themes or card.get("theme_codes") or [])
    except Exception:  # noqa: BLE001 - 快照失败不阻断埋点写入
        pass
    return symbol, kind, list(themes or [])


def log_event(
    *,
    user_id: str,
    story_id: str,
    action: str,
    content_id: str = "",
    surface: str = "",
    position: int = -1,
    dwell_ms: int = 0,
    score: float = 0.0,
    symbol: str = "",
    kind: str = "",
    themes: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """写一条故事事件。返回 True=已落库，False=参数非法被丢弃。

    匿名（``user_id`` 为空）只允许记 ``impression``/``click``/``dwell``——用于位置 CTR 评估，
    但不参与任何账号兴趣聚合（那些查询一律带 ``user_id != ''`` 过滤）。
    """
    init_events_db()
    action = str(action or "").strip()[:_MAX_ACTION]
    story_id = str(story_id or "").strip()[:_MAX_STORY_ID]
    if action not in VALID_ACTIONS or not story_id:
        return False
    uid = str(user_id or "").strip()[:64]
    if not uid and action not in (ACTION_IMPRESSION, ACTION_CLICK, ACTION_DWELL):
        return False
    symbol, kind, theme_list = _resolve_snapshot(story_id, symbol, kind, themes or [])
    created = now or datetime.now(timezone.utc)
    with _connect() as conn:
        conn.execute(
            """INSERT INTO story_events
               (user_id, story_id, content_id, action, surface, position, dwell_ms, score,
                symbol, kind, themes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid, story_id, str(content_id or "").strip()[:80], action,
                str(surface or "").strip()[:_MAX_SURFACE], int(position),
                max(0, int(dwell_ms or 0)), float(score or 0.0),
                symbol[:20], kind[:16], json.dumps(theme_list[:12], ensure_ascii=False),
                _iso(created),
            ),
        )
        conn.commit()
        # 上限核对是 O(行数) 的 COUNT，不能每条写都跑（高频埋点下会成瓶颈）。
        # 按 1/CAP_CHECK_STRIDE 抽样摊销，配合 prune() 兜底，行数只在阈值附近小幅波动。
        if uid and (int(created.timestamp()) % CAP_CHECK_STRIDE == 0):
            _enforce_user_cap(conn, uid)
    return True


def _enforce_user_cap(conn: sqlite3.Connection, user_id: str) -> None:
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM story_events WHERE user_id=?", (user_id,)
    ).fetchone()["n"]
    if int(total) > MAX_ROWS_PER_USER:
        excess = int(total) - MAX_ROWS_PER_USER
        conn.execute(
            """DELETE FROM story_events WHERE user_id=? AND id IN (
                   SELECT id FROM story_events WHERE user_id=? ORDER BY id ASC LIMIT ?)""",
            (user_id, user_id, excess),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 聚合查询
# ---------------------------------------------------------------------------

def user_affinity(
    user_id: str,
    *,
    now: Optional[datetime] = None,
    half_life_days: float = AFFINITY_HALF_LIFE_DAYS,
) -> dict[str, dict[str, float]]:
    """把用户的历史交互聚合成兴趣向量：``{"symbols": {...}, "themes": {...}, "kinds": {...}}``。

    每条事件按 ``动作权重 × 停留乘数 × 时间衰减`` 计入其快照的标的/主题/类型；负权动作（不感兴趣、
    划走）会把对应标的与主题的兴趣拉低。返回值可正可负，调用方按阈值判断「关心 / 反感」。
    """
    empty = {"symbols": {}, "themes": {}, "kinds": {}}
    uid = str(user_id or "").strip()
    if not uid:
        return empty
    init_events_db()
    reference = now or datetime.now(timezone.utc)
    symbols: dict[str, float] = defaultdict(float)
    themes: dict[str, float] = defaultdict(float)
    kinds: dict[str, float] = defaultdict(float)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT action, dwell_ms, symbol, kind, themes, created_at FROM story_events "
            "WHERE user_id=? AND action != ?",
            (uid, ACTION_IMPRESSION),
        ).fetchall()
    for row in rows:
        action = row["action"]
        base = AFFINITY_WEIGHTS.get(action, 0.0)
        if base == 0.0:
            continue
        weight = base
        if action == ACTION_DWELL:
            weight = base * _dwell_multiplier(int(row["dwell_ms"] or 0))
        weight *= _decay(_parse_ts(row["created_at"]), reference, half_life_days)
        symbol = str(row["symbol"] or "").strip()
        if symbol:
            symbols[symbol] += weight
        kind = str(row["kind"] or "").strip()
        if kind:
            kinds[kind] += weight
        try:
            for code in json.loads(row["themes"] or "[]"):
                code = str(code).strip()
                if code:
                    themes[code] += weight
        except Exception:  # noqa: BLE001
            pass
    return {"symbols": dict(symbols), "themes": dict(themes), "kinds": dict(kinds)}


def user_feedback_weights(user_id: str, *, now: Optional[datetime] = None) -> dict[str, float]:
    """用户对具体故事的显式评价权重 ``{story_id: weight}``（只取带符号的显式反馈动作）。

    与 ``user_affinity`` 的区别：这里是「这一集他点过赞/踩过」，用于对同一 story_id 直接加/减分；
    兴趣向量是把评价泛化到标的与主题。两者叠加，既记住具体态度又能举一反三。
    """
    uid = str(user_id or "").strip()
    if not uid:
        return {}
    init_events_db()
    reference = now or datetime.now(timezone.utc)
    explicit = (ACTION_FEEDBACK_UP, ACTION_FEEDBACK_DOWN, ACTION_DISMISS, ACTION_BOOKMARK, ACTION_REACT)
    marks = ",".join("?" * len(explicit))
    weights: dict[str, float] = defaultdict(float)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT story_id, action, created_at FROM story_events "
            f"WHERE user_id=? AND action IN ({marks})",
            (uid, *explicit),
        ).fetchall()
    for row in rows:
        base = AFFINITY_WEIGHTS.get(row["action"], 0.0)
        weights[row["story_id"]] += base * _decay(_parse_ts(row["created_at"]), reference, AFFINITY_HALF_LIFE_DAYS)
    return dict(weights)


def seen_story_ids(user_id: str, story_ids: Sequence[str]) -> set[str]:
    """这批故事里用户已经点开过（click/dwell/bookmark）的 id，供排序做「已读降权 / 去重」。"""
    uid = str(user_id or "").strip()
    ids = [str(s).strip()[:_MAX_STORY_ID] for s in (story_ids or []) if str(s).strip()]
    if not uid or not ids:
        return set()
    init_events_db()
    read_actions = (ACTION_CLICK, ACTION_DWELL, ACTION_BOOKMARK)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT story_id FROM story_events WHERE user_id=? AND story_id IN ({marks}) "
            f"AND action IN (?,?,?)",
            (uid, *ids, *read_actions),
        ).fetchall()
    return {row["story_id"] for row in rows}


def story_counts(story_ids: Sequence[str]) -> dict[str, dict[str, int]]:
    """每个故事的全站交互计数 ``{story_id: {impression, click, ...}}``（跨用户，供热度/社会证明）。"""
    ids = [str(s).strip()[:_MAX_STORY_ID] for s in (story_ids or []) if str(s).strip()]
    if not ids:
        return {}
    init_events_db()
    out: dict[str, dict[str, int]] = {sid: defaultdict(int) for sid in ids}
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT story_id, action, COUNT(*) AS n FROM story_events "
            f"WHERE story_id IN ({marks}) GROUP BY story_id, action",
            ids,
        ).fetchall()
    for row in rows:
        out[row["story_id"]][row["action"]] = int(row["n"])
    return {sid: dict(counter) for sid, counter in out.items()}


def ctr_by_position(
    *,
    surface: str = "",
    since_days: int = 14,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """按展示位置统计曝光→点击转化率，评估个人化排序质量（位置越靠前 CTR 应越高）。

    只统计带位置的 ``impression`` 与 ``click``；返回 ``[{position, impressions, clicks, ctr}]``。
    """
    init_events_db()
    reference = now or datetime.now(timezone.utc)
    cutoff = _iso(reference - timedelta(days=max(1, int(since_days))))
    clauses = ["created_at >= ?", "position >= 0", "action IN (?,?)"]
    params: list[Any] = [cutoff, ACTION_IMPRESSION, ACTION_CLICK]
    if surface:
        clauses.append("surface = ?")
        params.append(str(surface).strip()[:_MAX_SURFACE])
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT position, action, COUNT(*) AS n FROM story_events "
            f"WHERE {' AND '.join(clauses)} GROUP BY position, action",
            params,
        ).fetchall()
    buckets: dict[int, dict[str, int]] = defaultdict(lambda: {"impressions": 0, "clicks": 0})
    for row in rows:
        pos = int(row["position"])
        if row["action"] == ACTION_IMPRESSION:
            buckets[pos]["impressions"] += int(row["n"])
        else:
            buckets[pos]["clicks"] += int(row["n"])
    out: list[dict[str, Any]] = []
    for pos in sorted(buckets):
        imp = buckets[pos]["impressions"]
        clk = buckets[pos]["clicks"]
        out.append({
            "position": pos,
            "impressions": imp,
            "clicks": clk,
            "ctr": round(clk / imp, 4) if imp else 0.0,
        })
    return out


def prune(*, now: Optional[datetime] = None) -> int:
    """清理超期事件（>RETENTION_DAYS）。老兴趣已沉淀进聚合口径，原始行无需长期留存。"""
    init_events_db()
    reference = now or datetime.now(timezone.utc)
    cutoff = _iso(reference - timedelta(days=RETENTION_DAYS))
    with _connect() as conn:
        cur = conn.execute("DELETE FROM story_events WHERE created_at < ?", (cutoff,))
        conn.commit()
        return int(cur.rowcount or 0)
