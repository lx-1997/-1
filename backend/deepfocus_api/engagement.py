"""轻互动：资讯一键表态（看多/看空）+ 收藏。低风险 UGC——无自由文本，零审核压力。

设计：
- 表态 NewsReaction(user_id, message_id, stance)：每用户每条资讯一个立场，再点同立场=取消、点另一立场=切换。
  聚合 bull/bear 计数对所有人公开（情绪条卖点），「我的立场」仅登录可见。
- 收藏 UserBookmark(user_id, message_id, 标题/类型/链接快照)：登录态，头像菜单「我的收藏」查看。
- 复用统一 storage（SQLite 默认），与 user_prefs 同构；仅登录用户落库。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .storage import Base, session_scope

_STANCES = ("bull", "bear")
_MAX_BOOKMARKS = 500  # 单账号收藏上限，防滥用


class NewsReaction(Base):
    __tablename__ = "news_reaction"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stance: Mapped[str] = mapped_column(String(8))  # bull | bear
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserBookmark(Base):
    __tablename__ = "user_bookmark"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(16), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    symbol: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _agg(session, message_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {mid: {"bull": 0, "bear": 0} for mid in message_ids}
    if not message_ids:
        return out
    rows = session.execute(
        select(NewsReaction.message_id, NewsReaction.stance, func.count())
        .where(NewsReaction.message_id.in_(message_ids))
        .group_by(NewsReaction.message_id, NewsReaction.stance)
    ).all()
    for mid, stance, cnt in rows:
        if mid in out and stance in ("bull", "bear"):
            out[mid][stance] = int(cnt)
    return out


def react(user_id: str, message_id: str, stance: str) -> dict:
    """切换表态：点相同立场=取消、点不同立场=切换、首次=新增。返回该条聚合 {bull,bear,mine}。"""
    message_id = (message_id or "").strip()[:80]
    if not user_id or not message_id or stance not in _STANCES:
        return {"bull": 0, "bear": 0, "mine": None}
    with session_scope() as session:
        row = session.get(NewsReaction, {"user_id": user_id, "message_id": message_id})
        mine: Optional[str]
        if row is None:
            session.add(NewsReaction(user_id=user_id, message_id=message_id, stance=stance,
                                     updated_at=datetime.now(timezone.utc)))
            mine = stance
        elif row.stance == stance:  # 再点同立场 → 取消
            session.delete(row)
            mine = None
        else:  # 切换立场
            row.stance = stance
            row.updated_at = datetime.now(timezone.utc)
            mine = stance
        session.flush()
        agg = _agg(session, [message_id])[message_id]
    return {**agg, "mine": mine}


def reactions_for(message_ids: list[str], user_id: Optional[str] = None) -> dict[str, dict]:
    """批量取多条资讯的聚合 {mid: {bull,bear,mine}}。匿名时 mine 恒为 None。"""
    ids = [str(m).strip()[:80] for m in (message_ids or []) if str(m).strip()][:200]
    if not ids:
        return {}
    with session_scope() as session:
        agg = _agg(session, ids)
        mine_map: dict[str, str] = {}
        if user_id:
            rows = session.execute(
                select(NewsReaction.message_id, NewsReaction.stance)
                .where(NewsReaction.user_id == user_id, NewsReaction.message_id.in_(ids))
            ).all()
            mine_map = {mid: st for mid, st in rows}
    return {mid: {**agg[mid], "mine": mine_map.get(mid)} for mid in ids}


def toggle_bookmark(user_id: str, message_id: str, *, title: str = "", topic: str = "",
                    url: str = "", symbol: str = "") -> dict:
    """收藏开关：已收藏→取消，未收藏→加入。返回 {bookmarked, count}。"""
    message_id = (message_id or "").strip()[:80]
    if not user_id or not message_id:
        return {"bookmarked": False, "count": 0}
    with session_scope() as session:
        row = session.get(UserBookmark, {"user_id": user_id, "message_id": message_id})
        if row is not None:
            session.delete(row)
            bookmarked = False
        else:
            total = session.execute(
                select(func.count()).select_from(UserBookmark).where(UserBookmark.user_id == user_id)
            ).scalar() or 0
            if total >= _MAX_BOOKMARKS:
                # 超上限：删最旧一条再加（滚动保留）
                oldest = session.execute(
                    select(UserBookmark).where(UserBookmark.user_id == user_id)
                    .order_by(UserBookmark.created_at.asc()).limit(1)
                ).scalar_one_or_none()
                if oldest is not None:
                    session.delete(oldest)
            session.add(UserBookmark(
                user_id=user_id, message_id=message_id,
                title=(title or "")[:500], topic=(topic or "")[:16],
                url=(url or "")[:1000], symbol=(symbol or "")[:40],
                created_at=datetime.now(timezone.utc),
            ))
            bookmarked = True
        session.flush()
        count = session.execute(
            select(func.count()).select_from(UserBookmark).where(UserBookmark.user_id == user_id)
        ).scalar() or 0
    return {"bookmarked": bookmarked, "count": int(count)}


def list_bookmarks(user_id: str, limit: int = 200) -> list[dict]:
    """我的收藏（新→旧）。"""
    if not user_id:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(UserBookmark).where(UserBookmark.user_id == user_id)
            .order_by(UserBookmark.created_at.desc()).limit(max(1, min(limit, 500)))
        ).scalars().all()
        return [{
            "message_id": r.message_id, "title": r.title, "topic": r.topic,
            "url": r.url, "symbol": r.symbol,
            "created_at": (r.created_at.isoformat() if r.created_at else ""),
        } for r in rows]


def bookmarked_ids(user_id: str, message_ids: list[str]) -> list[str]:
    """这批资讯里，当前用户已收藏的 id（前端标星用）。"""
    ids = [str(m).strip()[:80] for m in (message_ids or []) if str(m).strip()][:200]
    if not user_id or not ids:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(UserBookmark.message_id)
            .where(UserBookmark.user_id == user_id, UserBookmark.message_id.in_(ids))
        ).all()
    return [mid for (mid,) in rows]
