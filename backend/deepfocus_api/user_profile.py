"""个人化画像与故事排序：把「千人千面」落到每个登录账号，且不污染任何公共资产。

两层信号
--------
- **显式画像**（用户主动填）：关注市场、投资视野、风险偏好、主题订阅。存 ``user_profile`` 表
  （SQLAlchemy，与 ``user_prefs`` 同构），是账号私有 PII。
- **隐式兴趣**（行为推断）：由四个既有账号数据源融合而成，带时间衰减——
  自选股（``user_prefs``）、收藏与表态（``engagement``）、战绩表态（``stock_call``）、
  故事级埋点（``story_events``）。不落新表，实时聚合，避免与源数据口径漂移。

排序（``rank_stories``）
------------------------
在 ``story_engine`` 给出的大众重要度 ``mass`` 之上叠加个人相关性 ``relevance``：
显式订阅/自选命中给强 boost，隐式兴趣按权重给软 boost，显式负反馈（不感兴趣/划走）直接压制，
已读故事降权。每张卡片产出 ``personal_score`` 与可解释的 ``why_personal`` chips（「你关注的半导体」
「自选股·贵州茅台」），让用户知道**为什么**推给他，而不是黑箱。

护栏（防信息茧房与单一化）
--------------------------
- **多样性**：同一标的 / 同一主题在结果头部有配额上限，避免一只票刷屏。
- **探索位**：保留固定比例的名额给「大众重要度高但个人没命中」的故事，持续给新用户/冷启动兜底，
  也让兴趣能被发现和纠偏。

隐私铁律
--------
本模块读到的所有账号数据只在**按账号**计算排序时使用，结果只回给该账号；
**绝不**把 user_id、持仓、兴趣写回 ``story_engine`` 的公共表，**绝不**进入大众简报/推送。
匿名请求（无 user_id）直接退回纯 mass 排序，不做任何个人化。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .storage import Base, session_scope

logger = logging.getLogger("deepfocus.user_profile")

# ---- 显式画像取值词表（前端下拉与后端校验共用同一套 code）----
MARKETS = ("CN", "HK", "US")
HORIZONS = ("short", "mid", "long")            # 短线(<5日) / 波段(数周) / 中长线(季度+)
RISKS = ("conservative", "balanced", "aggressive")
_MAX_THEME_FOLLOWS = 12
_MAX_MARKETS = 3

# ---- 排序权重（集中定义，回归测试按单调性断言而非硬编码数值）----
MASS_WEIGHT = 1.0                 # 大众重要度基线
THEME_FOLLOW_BOOST = 0.55         # 显式订阅主题命中
WATCHLIST_BOOST = 0.85            # 自选股命中（最强显式意图）
MARKET_MATCH_BOOST = 0.12         # 关注市场命中
CALL_SYMBOL_BOOST = 0.35          # 战绩表态过的标的
BOOKMARK_SYMBOL_BOOST = 0.30      # 收藏过的标的
AFFINITY_SYMBOL_SCALE = 0.30      # 隐式标的兴趣（user_affinity）缩放
AFFINITY_THEME_SCALE = 0.25       # 隐式主题兴趣缩放
FEEDBACK_SCALE = 0.60             # 显式故事级反馈（含负）
SEEN_PENALTY = 0.35               # 已读降权

# ---- 多样性 / 探索护栏 ----
MAX_PER_SYMBOL_HEAD = 2           # 头部（前 head_window 名）同标的上限
MAX_PER_THEME_HEAD = 3            # 头部同主题上限
HEAD_WINDOW = 12
EXPLORE_RATIO = 0.25              # 每页保留给「高大众重要度但个人未命中」的比例
RELEVANCE_HIT_THRESHOLD = 0.05    # relevance 超过此值算「个人命中」，否则视为可被探索位替换


class UserProfile(Base):
    __tablename__ = "user_profile"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    markets: Mapped[str] = mapped_column(Text, default="[]")        # JSON 数组
    horizon: Mapped[str] = mapped_column(String(16), default="")    # short|mid|long
    risk: Mapped[str] = mapped_column(String(16), default="")       # conservative|balanced|aggressive
    theme_follows: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组（本体 theme code）
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 显式画像 CRUD
# ---------------------------------------------------------------------------

def _valid_theme_codes() -> set[str]:
    try:
        from .content_ontology import THEME_RULES
        return {code for code, _label, _pat in THEME_RULES}
    except Exception:  # noqa: BLE001
        return set()


def _sanitize_profile(markets: Any, horizon: Any, risk: Any, theme_follows: Any) -> dict[str, Any]:
    mk: list[str] = []
    for raw in (markets or []):
        code = str(raw).strip().upper()
        if code in MARKETS and code not in mk:
            mk.append(code)
        if len(mk) >= _MAX_MARKETS:
            break
    hz = str(horizon or "").strip().lower()
    if hz not in HORIZONS:
        hz = ""
    rk = str(risk or "").strip().lower()
    if rk not in RISKS:
        rk = ""
    allowed = _valid_theme_codes()
    tf: list[str] = []
    for raw in (theme_follows or []):
        code = str(raw).strip().lower()
        if code and (not allowed or code in allowed) and code not in tf:
            tf.append(code)
        if len(tf) >= _MAX_THEME_FOLLOWS:
            break
    return {"markets": mk, "horizon": hz, "risk": rk, "theme_follows": tf}


def get_profile(user_id: str) -> dict[str, Any]:
    """返回账号显式画像；无记录返回空默认（前端据此判断是否首次引导填写）。"""
    uid = str(user_id or "").strip()
    if not uid:
        return {"markets": [], "horizon": "", "risk": "", "theme_follows": []}
    with session_scope() as session:
        row = session.get(UserProfile, uid)
        if row is None:
            return {"markets": [], "horizon": "", "risk": "", "theme_follows": []}
        try:
            markets = json.loads(row.markets or "[]")
            follows = json.loads(row.theme_follows or "[]")
        except Exception:  # noqa: BLE001 - 损坏数据当空处理，不崩
            markets, follows = [], []
        return {
            "markets": [m for m in markets if m in MARKETS],
            "horizon": row.horizon if row.horizon in HORIZONS else "",
            "risk": row.risk if row.risk in RISKS else "",
            "theme_follows": [t for t in follows],
        }


def set_profile(user_id: str, *, markets: Any = None, horizon: Any = None,
                risk: Any = None, theme_follows: Any = None) -> dict[str, Any]:
    """整表覆盖式保存显式画像，返回归一后的结果。"""
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("缺少登录账号")
    clean = _sanitize_profile(markets, horizon, risk, theme_follows)
    with session_scope() as session:
        row = session.get(UserProfile, uid)
        if row is None:
            row = UserProfile(user_id=uid)
            session.add(row)
        row.markets = json.dumps(clean["markets"], ensure_ascii=False)
        row.horizon = clean["horizon"]
        row.risk = clean["risk"]
        row.theme_follows = json.dumps(clean["theme_follows"], ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
    return clean


# ---------------------------------------------------------------------------
# 隐式兴趣融合
# ---------------------------------------------------------------------------

def _watchlist_symbols(user_id: str) -> set[str]:
    try:
        from . import user_prefs
        data = user_prefs.get_watchlist(user_id) or {}
        return {str(s).strip().upper() for s in (data.get("symbols") or []) if str(s).strip()}
    except Exception:  # noqa: BLE001
        return set()


def _bookmark_symbols(user_id: str) -> set[str]:
    try:
        from . import engagement
        rows = engagement.list_bookmarks(user_id, limit=200)
        return {str(r.get("symbol") or "").strip().upper() for r in rows if str(r.get("symbol") or "").strip()}
    except Exception:  # noqa: BLE001
        return set()


def _call_symbols(user_id: str) -> set[str]:
    try:
        from . import stock_call
        rows = stock_call.list_user_calls(user_id, limit=200)
        return {str(r.get("symbol") or "").strip().upper() for r in rows if str(r.get("symbol") or "").strip()}
    except Exception:  # noqa: BLE001
        return set()


def interest_vector(user_id: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """融合显式画像 + 四路隐式信号，产出一个用于排序的兴趣包（账号私有，绝不外泄）。

    返回::

        {
          "markets": [...], "horizon": "", "risk": "",
          "theme_follows": {...},          # 显式订阅主题（强意图，权重 1.0）
          "symbols": {...},                # 标的 -> 权重（自选/收藏/战绩/埋点融合，已衰减）
          "themes": {...},                 # 主题 -> 权重（隐式埋点 + 显式订阅）
          "feedback": {...},               # story_id -> 显式故事反馈权重（含负）
          "seen": set(),                   # 由 rank_stories 现场填充
        }
    """
    profile = get_profile(user_id)
    symbols: dict[str, float] = {}
    themes: dict[str, float] = {code: 1.0 for code in profile["theme_follows"]}

    for sym in _watchlist_symbols(user_id):
        symbols[sym] = max(symbols.get(sym, 0.0), 1.0)      # 自选=最强显式意图
    for sym in _call_symbols(user_id):
        symbols[sym] = symbols.get(sym, 0.0) + 0.6
    for sym in _bookmark_symbols(user_id):
        symbols[sym] = symbols.get(sym, 0.0) + 0.5

    try:
        from . import story_events
        affinity = story_events.user_affinity(user_id, now=now)
        for sym, weight in (affinity.get("symbols") or {}).items():
            sym = str(sym).strip().upper()
            if sym:
                symbols[sym] = symbols.get(sym, 0.0) + float(weight)
        for code, weight in (affinity.get("themes") or {}).items():
            code = str(code).strip()
            if code:
                themes[code] = themes.get(code, 0.0) + float(weight)
        feedback = story_events.user_feedback_weights(user_id, now=now)
    except Exception:  # noqa: BLE001 - 埋点库缺失时退回显式画像，不阻断排序
        logger.exception("story_events affinity unavailable")
        feedback = {}

    return {
        "markets": profile["markets"],
        "horizon": profile["horizon"],
        "risk": profile["risk"],
        "theme_follows": {code: 1.0 for code in profile["theme_follows"]},
        "symbols": symbols,
        "themes": themes,
        "feedback": feedback,
        "seen": set(),
    }


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------

def _card_themes(card: dict[str, Any]) -> list[str]:
    codes = card.get("theme_codes")
    if codes:
        return [str(c) for c in codes]
    return [str(t.get("code")) for t in (card.get("themes") or []) if t.get("code")]


def _relevance(card: dict[str, Any], interest: dict[str, Any]) -> tuple[float, list[str]]:
    """算一张卡片对某账号的个人相关性，并给出可解释 chips（为什么推给你）。"""
    score = 0.0
    chips: list[str] = []
    symbol = str(card.get("symbol") or "").strip().upper()
    card_symbols = {symbol} if symbol else set()
    for extra in card.get("symbols") or []:
        code = str(extra or "").strip().upper()
        if code:
            card_symbols.add(code)

    symbols: dict[str, float] = interest.get("symbols") or {}
    theme_follows: dict[str, float] = interest.get("theme_follows") or {}
    themes: dict[str, float] = interest.get("themes") or {}

    # 标的命中：自选/收藏/战绩/埋点融合权重
    best_symbol = ""
    best_symbol_w = 0.0
    for sym in card_symbols:
        w = float(symbols.get(sym, 0.0))
        if w > best_symbol_w:
            best_symbol, best_symbol_w = sym, w
    if best_symbol_w > 0:
        if best_symbol_w >= 1.0:
            score += WATCHLIST_BOOST + (best_symbol_w - 1.0) * AFFINITY_SYMBOL_SCALE
            name = str(card.get("entity_name") or best_symbol)
            chips.append(f"自选股·{name}")
        else:
            score += best_symbol_w * AFFINITY_SYMBOL_SCALE * 2.0
            if best_symbol_w >= 0.5:
                chips.append("你常看的标的")

    # 主题命中：显式订阅优先，其次隐式兴趣
    card_theme_codes = _card_themes(card)
    hit_follow = [c for c in card_theme_codes if theme_follows.get(c, 0.0) > 0]
    if hit_follow:
        score += THEME_FOLLOW_BOOST
        labels = card.get("theme_labels") or []
        label = labels[0] if labels else hit_follow[0]
        chips.append(f"你关注的{label}")
    else:
        best_theme_w = max((float(themes.get(c, 0.0)) for c in card_theme_codes), default=0.0)
        if best_theme_w > 0:
            score += best_theme_w * AFFINITY_THEME_SCALE

    # 市场命中
    markets = interest.get("markets") or []
    card_market = str(card.get("market") or "").strip().upper()
    if markets and card_market and card_market in markets:
        score += MARKET_MATCH_BOOST

    # 显式故事反馈（含负：不感兴趣/划走直接压制）
    fb = float((interest.get("feedback") or {}).get(card.get("story_id"), 0.0))
    if fb:
        score += fb * FEEDBACK_SCALE
        if fb < 0:
            chips.append("已按你的反馈减少此类")

    return score, chips


def rank_stories(
    user_id: str,
    cards: Sequence[dict[str, Any]],
    *,
    limit: int = 20,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """按「大众重要度 + 个人相关性」重排故事卡片，附可解释 chips、多样性与探索护栏。

    - 匿名（``user_id`` 空）或空兴趣：直接按 mass 排序返回，不做个人化。
    - 登录：``personal_score = mass × MASS_WEIGHT + relevance``，命中越多越靠前。
    - 头部施加同标的/同主题配额，避免刷屏；并保留 ``EXPLORE_RATIO`` 的名额给高 mass 但未命中的故事。

    返回的每张卡片新增 ``personal_score`` / ``relevance`` / ``why_personal`` 字段，原 mass 等字段不变。
    """
    cards = list(cards or [])
    if not cards:
        return []
    limit = max(1, min(int(limit or 20), 100))

    uid = str(user_id or "").strip()
    if not uid:
        ranked = sorted(cards, key=lambda c: -float(c.get("mass") or 0.0))[:limit]
        return [_decorate(c, 0.0, float(c.get("mass") or 0.0), []) for c in ranked]

    interest = interest_vector(uid, now=now)
    try:
        from . import story_events
        interest["seen"] = story_events.seen_story_ids(uid, [c.get("story_id") for c in cards])
    except Exception:  # noqa: BLE001
        interest["seen"] = set()

    scored: list[dict[str, Any]] = []
    for card in cards:
        relevance, chips = _relevance(card, interest)
        mass = float(card.get("mass") or 0.0)
        personal = mass * MASS_WEIGHT + relevance
        if card.get("story_id") in interest["seen"]:
            personal -= SEEN_PENALTY
            chips = chips + ["已读过"]
        scored.append({
            "card": card, "personal": personal, "relevance": relevance,
            "mass": mass, "chips": chips,
            "hit": relevance >= RELEVANCE_HIT_THRESHOLD,
        })

    scored.sort(key=lambda s: (-s["personal"], -s["mass"]))
    picked = _apply_guardrails(scored, limit)
    return [_decorate(p["card"], p["relevance"], p["personal"], p["chips"]) for p in picked]


def _apply_guardrails(scored: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """头部多样性配额 + 探索位。输入已按 personal 降序。"""
    head = max(1, min(HEAD_WINDOW, limit))
    explore_slots = int(round(limit * EXPLORE_RATIO))
    picked: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    symbol_count: dict[str, int] = {}
    theme_count: dict[str, int] = {}

    for entry in scored:
        if len(picked) >= limit:
            break
        card = entry["card"]
        idx = len(picked)
        in_head = idx < head
        symbol = str(card.get("symbol") or "").strip().upper()
        themes = _card_themes(card)

        blocked = False
        if in_head:
            if symbol and symbol_count.get(symbol, 0) >= MAX_PER_SYMBOL_HEAD:
                blocked = True
            elif themes:
                if any(theme_count.get(t, 0) >= MAX_PER_THEME_HEAD for t in themes):
                    blocked = True
        if blocked:
            deferred.append(entry)
            continue
        picked.append(entry)
        if symbol:
            symbol_count[symbol] = symbol_count.get(symbol, 0) + 1
        for t in themes:
            theme_count[t] = theme_count.get(t, 0) + 1

    # 探索位：把「个人未命中但大众重要度高」的故事插进尾部名额，防茧房与冷启动。
    if explore_slots > 0:
        explore_pool = [e for e in scored if not e["hit"] and e not in picked]
        explore_pool.sort(key=lambda e: -e["mass"])
        take = min(explore_slots, len(explore_pool), max(0, limit - len(picked)))
        for entry in explore_pool[:take]:
            if entry not in picked:
                picked.append(entry)

    # 若因配额仍有空位，用被推迟的高分项补齐。
    if len(picked) < limit:
        for entry in deferred:
            if len(picked) >= limit:
                break
            if entry not in picked:
                picked.append(entry)

    return picked[:limit]


def _decorate(card: dict[str, Any], relevance: float, personal: float, chips: Sequence[str]) -> dict[str, Any]:
    """产出排序后的卡片副本（不改原对象），附上个人化字段。"""
    out = dict(card)
    out["relevance"] = round(float(relevance), 4)
    out["personal_score"] = round(float(personal), 4)
    out["why_personal"] = list(chips)[:4]
    return out
