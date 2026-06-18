"""会员兑换码（卡密）系统：后台批量生成 → 用户自助兑换 → 自动开通会员。

设计：
- 一张表 `membership_codes`，每行一个码；tier=premium 配 days，tier=lifetime 永久。
- 兑换用原子 UPDATE（WHERE used_by IS NULL）认领，防并发重复使用。
- 码存规范形式（大写、无连字符 12 位），展示加连字符；兑换时对输入做归一（去非字母数字 + 大写），容错。
- 实际开通会员由调用方（main.py 的 redeem 端点）拿到 tier/days 后调 auth.set_membership 完成，避免循环依赖。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, func, inspect as sa_inspect, select, text, update
from sqlalchemy.orm import Mapped, mapped_column

from . import storage
from .storage import Base, session_scope

logger = logging.getLogger("deepfocus.membership_codes")

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 去掉易混 0/O/1/I/L
_CODE_LEN = 12


class MembershipCode(Base):
    __tablename__ = "membership_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)       # 规范形式：大写无连字符
    tier: Mapped[str] = mapped_column(String(16))                         # premium | lifetime
    days: Mapped[int] = mapped_column(Integer, default=0)                 # premium 的天数；lifetime 为 0
    kind: Mapped[str] = mapped_column(String(16), default="normal")       # normal | trial（体验卡：每人每天限兑1张、不计付费）
    note: Mapped[str] = mapped_column(String(120), default="")           # 批次/备注（如「月卡批次1」）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)        # 兑换者 user_id
    used_by_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # 兑换者用户名（展示）
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 体验卡每张默认天数（7 天体验周卡）
TRIAL_DAYS = 7


def _ensure_code_columns() -> None:
    """幂等迁移：给 membership_codes 补 kind 列（体验卡标记）。"""
    engine = storage.get_engine()
    insp = sa_inspect(engine)
    if "membership_codes" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("membership_codes")}
    if "kind" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE membership_codes ADD COLUMN kind VARCHAR(16) DEFAULT 'normal'"))


def _cn_today_start_utc() -> datetime:
    """今天 0 点（北京时间 UTC+8）对应的 UTC 时刻——用于「每人每天限领一张」的当天判定。"""
    cn_now = _now() + timedelta(hours=8)
    cn_midnight = cn_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return cn_midnight - timedelta(hours=8)


def normalize(code: str) -> str:
    """归一兑换码：只保留字母数字 + 大写（用户带不带连字符、大小写都能兑）。"""
    return "".join(c for c in (code or "") if c.isalnum()).upper()


def pretty(code: str) -> str:
    """展示形式：每 4 位一组加连字符。"""
    c = normalize(code)
    return "-".join(c[i:i + 4] for i in range(0, len(c), 4))


def _gen_one() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def generate_codes(count: int, tier: str, days: int = 0, note: str = "") -> list[str]:
    """批量生成兑换码，返回展示形式（带连字符）列表。

    tier 取值：premium（按 days）/ lifetime（永久）/ trial（体验周卡——实为 premium，
    默认 7 天，且 kind=trial 受「每人每天限兑 1 张」约束、不计为付费转化）。
    """
    count = max(1, min(int(count or 1), 500))
    tier = (tier or "premium").strip().lower()
    days = max(0, int(days or 0))
    kind = "normal"
    if tier == "trial":
        kind = "trial"
        tier = "premium"
        if days <= 0:
            days = TRIAL_DAYS
    if tier not in ("premium", "lifetime"):
        tier = "premium"
    if tier == "premium" and days <= 0:
        days = 30  # 默认月卡
    out: list[str] = []
    with session_scope() as session:
        existing = set(session.scalars(select(MembershipCode.code)).all())
        for _ in range(count):
            code = _gen_one()
            while code in existing:
                code = _gen_one()
            existing.add(code)
            session.add(MembershipCode(
                code=code, tier=tier, days=(0 if tier == "lifetime" else days), kind=kind,
                note=(note or "")[:120], created_at=_now(),
            ))
            out.append(pretty(code))
    return out


def redeem(code: str, user_id: str, username: str) -> dict[str, Any]:
    """原子兑换：成功返回 {ok:True, tier, days, kind}；失败返回 {ok:False, reason}。

    体验卡（kind=trial）额外约束：同一账号每天只能兑换 1 张（reason=trial_daily）。
    """
    norm = normalize(code)
    uid = (user_id or "").strip()
    if not norm or not uid:
        return {"ok": False, "reason": "empty"}
    with session_scope() as session:
        # 先取码，判断是否体验卡 + 是否已被用——以便在「认领」前做每日限领校验
        row = session.get(MembershipCode, norm)
        if row is None:
            return {"ok": False, "reason": "not_found"}
        kind = (getattr(row, "kind", None) or "normal")
        if row.used_by:
            return {"ok": False, "reason": "self_used" if row.used_by == uid else "used"}
        # 体验卡：同一账号今天（北京时间）是否已兑过任意体验卡
        if kind == "trial":
            today = _cn_today_start_utc()
            used_today = session.scalar(
                select(func.count()).select_from(MembershipCode).where(
                    MembershipCode.kind == "trial",
                    MembershipCode.used_by == uid,
                    MembershipCode.used_at.isnot(None),
                    MembershipCode.used_at >= today,
                )
            ) or 0
            if used_today >= 1:
                return {"ok": False, "reason": "trial_daily"}
        # 原子认领：仅当未被使用时才标记成功（并发兜底）
        res = session.execute(
            update(MembershipCode)
            .where(MembershipCode.code == norm, MembershipCode.used_by.is_(None))
            .values(used_by=uid, used_by_name=(username or uid)[:120], used_at=_now())
        )
        if res.rowcount == 1:
            return {"ok": True, "tier": row.tier, "days": int(row.days or 0), "kind": kind}
        # 认领失败（并发被抢）：复查归属
        row2 = session.get(MembershipCode, norm)
        if row2 is not None and (row2.used_by or "") == uid:
            return {"ok": False, "reason": "self_used"}
        return {"ok": False, "reason": "used"}


def list_codes(limit: int = 200, only_unused: bool = False) -> list[dict]:
    with session_scope() as session:
        q = select(MembershipCode).order_by(MembershipCode.created_at.desc())
        if only_unused:
            q = q.where(MembershipCode.used_by.is_(None))
        rows = session.scalars(q.limit(limit)).all()
        out = []
        for r in rows:
            ua = r.used_at
            if ua is not None and ua.tzinfo is None:
                ua = ua.replace(tzinfo=timezone.utc)
            out.append({
                "code": pretty(r.code), "tier": r.tier, "days": int(r.days or 0),
                "kind": getattr(r, "kind", None) or "normal", "note": r.note or "",
                "used": bool(r.used_by), "used_by_name": r.used_by_name or "",
                "used_at": ua.isoformat() if ua else "",
                "created_at": (r.created_at.replace(tzinfo=timezone.utc).isoformat() if r.created_at and r.created_at.tzinfo is None else (r.created_at.isoformat() if r.created_at else "")),
            })
        return out


def code_stats() -> dict[str, Any]:
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(MembershipCode)) or 0
        used = session.scalar(select(func.count()).select_from(MembershipCode).where(MembershipCode.used_by.isnot(None))) or 0
        return {"total": total, "used": used, "unused": total - used}
