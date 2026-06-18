"""后台私信：登录用户 ↔ 管理员 的一对一消息。

设计：
- 复用统一 storage 层，表 `support_messages` 一行一条消息（user_id 分组成"会话"）。
- sender ∈ {user, admin}；read_by_admin / read_by_user 标记未读，用于看板红点与用户端徽标。
- 用户端：发消息 + 拉自己的会话；管理端（看板，令牌鉴权）：列所有会话 + 看某会话 + 回复。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select, func, update
from sqlalchemy.orm import Mapped, mapped_column

from .storage import Base, session_scope

logger = logging.getLogger("deepfocus.support")

_MAX_LEN = 2000  # 单条消息上限，防滥用


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(120))
    sender: Mapped[str] = mapped_column(String(8))  # 'user' | 'admin'
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    read_by_user: Mapped[bool] = mapped_column(Boolean, default=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(m: SupportMessage) -> dict[str, Any]:
    ts = m.created_at
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return {
        "id": m.id,
        "user_id": m.user_id,
        "username": m.username,
        "sender": m.sender,
        "content": m.content,
        "created_at": ts.isoformat() if ts else "",
    }


def add_message(user_id: str, username: str, sender: str, content: str) -> Optional[dict]:
    """追加一条消息。sender='user' → 管理员侧未读；sender='admin' → 用户侧未读。"""
    uid = (user_id or "").strip()
    text = (content or "").strip()[:_MAX_LEN]
    if not uid or not text or sender not in ("user", "admin"):
        return None
    with session_scope() as session:
        m = SupportMessage(
            user_id=uid, username=(username or uid)[:120], sender=sender, content=text,
            created_at=_now(),
            read_by_admin=(sender == "admin"),   # 管理员发的对管理员算已读
            read_by_user=(sender == "user"),     # 用户发的对用户算已读
        )
        session.add(m)
        session.flush()
        return _row(m)


def get_thread(user_id: str, limit: int = 300) -> list[dict]:
    """某用户的完整会话（时间升序）。"""
    uid = (user_id or "").strip()
    if not uid:
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(SupportMessage).where(SupportMessage.user_id == uid)
            .order_by(SupportMessage.created_at.asc(), SupportMessage.id.asc()).limit(limit)
        ).all()
        return [_row(m) for m in rows]


def mark_read(user_id: str, by: str) -> None:
    """把某会话标记为某方已读：by='admin' 清管理员未读；by='user' 清用户未读。"""
    uid = (user_id or "").strip()
    if not uid or by not in ("admin", "user"):
        return
    col = SupportMessage.read_by_admin if by == "admin" else SupportMessage.read_by_user
    with session_scope() as session:
        session.execute(
            update(SupportMessage).where(SupportMessage.user_id == uid, col.is_(False)).values(
                **{("read_by_admin" if by == "admin" else "read_by_user"): True}
            )
        )


def user_unread(user_id: str) -> int:
    """用户侧未读数（管理员回复但用户还没看的）。"""
    uid = (user_id or "").strip()
    if not uid:
        return 0
    with session_scope() as session:
        return session.scalar(
            select(func.count()).select_from(SupportMessage)
            .where(SupportMessage.user_id == uid, SupportMessage.read_by_user.is_(False))
        ) or 0


def list_threads(limit: int = 200) -> list[dict]:
    """管理端：所有会话概览，按最近活动倒序。每项含最后一条、未读数、消息总数。"""
    with session_scope() as session:
        rows = session.scalars(
            select(SupportMessage).order_by(SupportMessage.created_at.asc(), SupportMessage.id.asc())
        ).all()
        threads: dict[str, dict] = {}
        for m in rows:
            t = threads.setdefault(m.user_id, {
                "user_id": m.user_id, "username": m.username,
                "last": "", "last_sender": "", "last_at": "", "unread": 0, "count": 0,
            })
            t["username"] = m.username or t["username"]
            t["last"] = m.content
            t["last_sender"] = m.sender
            ts = m.created_at
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            t["last_at"] = ts.isoformat() if ts else ""
            t["count"] += 1
            if m.sender == "user" and not m.read_by_admin:
                t["unread"] += 1
        out = list(threads.values())
        out.sort(key=lambda x: x["last_at"], reverse=True)
        return out[:limit]


def admin_unread_total() -> int:
    """管理端总未读（看板红点）。"""
    with session_scope() as session:
        return session.scalar(
            select(func.count()).select_from(SupportMessage)
            .where(SupportMessage.sender == "user", SupportMessage.read_by_admin.is_(False))
        ) or 0
