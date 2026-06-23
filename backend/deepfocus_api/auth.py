"""
真实认证 / RBAC 子系统（替换前端演示态登录）。

- 口令：passlib `pbkdf2_sha256`（纯标准库后端，无需 bcrypt C 扩展），永不明文存储。
- 令牌：python-jose 签发 HS256 JWT，claims 携带 sub/role/email/username/exp。
- 角色：viewer < analyst < admin（数值化 rank 做层级比较）。
- 存储：走统一 storage 层（SQLite 默认，可一键切 Postgres）。
- 强制：`AuthMiddleware` 在 `DEEPFOCUS_AUTH_REQUIRED=true` 时对 /api 路由集中鉴权 + 路径级 RBAC；
  关闭时（默认/演示/测试）完全旁路，不影响既有 133 路由与 190 测试。
- 敏感端点（/auth/me、/auth/users）即使在旁路态也有 handler 级守卫，避免裸奔。
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, String, func, inspect as sa_inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from starlette.middleware.base import BaseHTTPMiddleware

from . import storage
from .storage import Base, session_scope

logger = logging.getLogger("deepfocus.auth")

# --------------------------------------------------------------------------- #
# 角色层级
# --------------------------------------------------------------------------- #
ROLE_ADMIN = "admin"
ROLE_ANALYST = "analyst"
ROLE_VIEWER = "viewer"
ROLE_RANK = {ROLE_VIEWER: 1, ROLE_ANALYST: 2, ROLE_ADMIN: 3}
VALID_ROLES = set(ROLE_RANK)

JWT_ALG = "HS256"
# 仅用于无显式密钥的本地开发；生产必须设置 DEEPFOCUS_JWT_SECRET。
_DEV_SECRET = "deepfocus-dev-insecure-secret-change-me"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class UserExistsError(Exception):
    """邮箱或用户名已存在。"""


# --------------------------------------------------------------------------- #
# 运行时配置（全部 live 读取 env，便于测试/热切换）
# --------------------------------------------------------------------------- #
def auth_required() -> bool:
    return os.getenv("DEEPFOCUS_AUTH_REQUIRED", "").strip().lower() == "true"


def self_register_enabled() -> bool:
    return os.getenv("DEEPFOCUS_ALLOW_SELF_REGISTER", "true").strip().lower() != "false"


def jwt_secret() -> str:
    secret = os.getenv("DEEPFOCUS_JWT_SECRET")
    if secret:
        return secret
    if auth_required():
        # 生产强制鉴权却用开发默认密钥 = 重大风险，明确告警。
        logger.warning(
            "DEEPFOCUS_AUTH_REQUIRED=true 但未设置 DEEPFOCUS_JWT_SECRET，正在使用不安全的开发默认密钥。"
        )
    return _DEV_SECRET


def jwt_ttl_minutes() -> int:
    try:
        return int(os.getenv("DEEPFOCUS_JWT_TTL_MINUTES", "720"))
    except ValueError:
        return 720


# --------------------------------------------------------------------------- #
# ORM 模型
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 邮箱/手机号均选填：未填存 NULL（SQLite/Postgres 的唯一索引允许多个 NULL 并存）。
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True, index=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True, nullable=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=ROLE_ANALYST)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 拉新机制：每个用户专属邀请码；invited_by = 邀请人 user id（注册时填了邀请码则记录）
    invite_code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, index=True, nullable=True)
    invited_by: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    # 单设备登录：每次登录/注册轮换一个会话标识，写进 JWT(sid)。新登录改写它 → 旧端 token 的 sid 失配 → 被挤下线。
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 会员：尊享会员到期时间。空/已过期 = 体验期；未来时间 = 尊享会员（剩余天数 = 到期-现在，自然每天递减）。
    membership_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 会员来源：trial(体验) / paid(付费购买) / reward(邀请奖励兑换) / admin(管理员赠送)。
    # 仅 paid 计入「邀请的年费付费用户」奖励判定（区分真金白银 vs 赠送）。
    membership_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # 注册时客户端 IP（邀请防刷：同一邀请人名下被邀注册 IP 重复则不计有效）。
    registered_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 最近登录时间（激活信号：被邀请人至少登录/活跃过一次才算有效邀请）。
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 「登录送 3 天体验会员」：领取时间。非空 = 已领过（每账号仅一次，自助领取）。
    trial_claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Pydantic 契约
# --------------------------------------------------------------------------- #
class AuthUserOut(BaseModel):
    id: str
    email: Optional[str] = None
    phone: Optional[str] = None
    username: str
    role: str
    is_active: bool
    created_at: datetime
    invite_code: Optional[str] = None  # 我的专属邀请码
    invited_by: Optional[str] = None   # 邀请我的人（user id）
    membership: Optional[dict] = None  # 会员状态 {tier: trial|premium, expires_at, days_left}
    trial_claimable: bool = False      # 是否可领「登录送 3 天体验会员」（未领过 + 当前是体验期）


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut


class RegisterRequest(BaseModel):
    email: Optional[str] = None  # 收集但不强制：填了才校验格式，可作找回/召回通道
    phone: Optional[str] = None  # 收集但不强制：填了才校验格式，未来可作短信验证登录
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6, max_length=200)
    invite_code: Optional[str] = None  # 选填：填了则记录邀请关系（拉新）
    website: Optional[str] = None  # 蜜罐：真人前端置空且隐藏；机器人常会填 → 判为刷号
    turnstile_token: Optional[str] = None  # Cloudflare Turnstile 人机校验票据（启用时校验）


class InviteOverview(BaseModel):
    code: str
    invited_count: int
    invited: list[dict] = []   # [{username, created_at}]


class LoginRequest(BaseModel):
    username: str  # 接受邮箱或用户名
    password: str


class UserListResponse(BaseModel):
    users: list[AuthUserOut]


# --------------------------------------------------------------------------- #
# 口令 & 令牌
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:  # 损坏/未知哈希格式：当作校验失败而非崩溃
        return False


def create_access_token(user: AuthUserOut, sid: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "role": user.role,
        "email": user.email,
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=jwt_ttl_minutes()),
    }
    if sid:
        payload["sid"] = sid  # 单设备登录：会话标识，校验时须等于库内当前 session_id
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALG)


_MAX_DEVICES = max(1, int(os.getenv("DEEPFOCUS_MAX_DEVICES", "2")))  # 一个账号最多同时在线的设备数


def rotate_session(user_id: str) -> str:
    """登录/注册时新增一个会话标识并落库（最多保留最近 N 台）；返回新 sid（写进本次签发的 JWT）。
    超过 N 台时，最早登录的那台 sid 被挤出列表 → 其 token 下次鉴权失配被挤下线。"""
    sid = secrets.token_hex(8)
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is not None:
                cur = [s for s in (user.session_id or "").split(",") if s]
                cur.append(sid)
                user.session_id = ",".join(cur[-_MAX_DEVICES:])  # 只保留最近 N 台
    except Exception as exc:  # noqa: BLE001 - 失败不阻断登录（退化为多端可用）
        logger.warning("更新会话标识失败：%s", exc)
    return sid


def session_is_current(claims: Optional[dict]) -> bool:
    """多设备(≤N)登录校验：token 的 sid 必须在该用户库内当前会话列表中。
    - 旧 token 无 sid（本特性上线前签发）→ 宽限放行（12h 内自然过期换新）。
    - 库内 session_id 为空（从没登录过新版）→ 放行。
    - 校验异常 → 放行，绝不误伤正常用户。"""
    if not claims:
        return False
    sid = claims.get("sid")
    if not sid:
        return True
    uid = claims.get("sub")
    if not uid:
        return True
    try:
        with session_scope() as session:
            user = session.get(User, uid)
            cur = getattr(user, "session_id", None) if user else None
        if not cur:
            return True
        return sid in [s for s in cur.split(",") if s]
    except Exception:  # noqa: BLE001
        return True


def decode_token(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        return jwt.decode(token, jwt_secret(), algorithms=[JWT_ALG])
    except JWTError:
        return None


# --------------------------------------------------------------------------- #
# 用户存储
# --------------------------------------------------------------------------- #
_LIFETIME_DAYS = 36500  # 到期距今 >100 年 → 视为永久会员（哨兵到期时间为年 9999）
INVITE_SIGNUP_BONUS_DAYS = int(os.getenv("DEEPFOCUS_INVITE_SIGNUP_BONUS_DAYS", "3") or 3)  # 被邀请好友凭有效邀请码注册即送的尊享会员天数


def membership_of(user: User) -> dict:
    """会员状态：永久(>100年)=永久会员；未来=尊享会员(算剩余天数,ceil)；否则=体验期。"""
    exp = getattr(user, "membership_expires_at", None)
    if exp is not None:
        if exp.tzinfo is None:  # SQLite 可能取回 naive datetime，按 UTC 归一
            exp = exp.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if exp > now:
            delta = exp - now
            if delta.days > _LIFETIME_DAYS:
                return {"tier": "lifetime", "expires_at": exp.isoformat(), "days_left": None}
            days = delta.days + (1 if (delta.seconds or delta.microseconds) else 0)
            return {"tier": "premium", "expires_at": exp.isoformat(), "days_left": max(1, days)}
    return {"tier": "trial", "expires_at": None, "days_left": None}


def membership_of_username(username: str) -> Optional[dict]:
    """按用户名取会员状态（大小写不敏感）；用户不存在返回 None。"""
    uname = (username or "").strip()
    if not uname:
        return None
    with session_scope() as session:
        user = session.scalar(select(User).where(func.lower(User.username) == uname.lower()))
        return membership_of(user) if user is not None else None


def membership_source_of(username: str) -> Optional[str]:
    """按用户名取会员来源（paid/reward/trial/admin…）；用户不存在返回 None。"""
    uname = (username or "").strip()
    if not uname:
        return None
    with session_scope() as session:
        user = session.scalar(select(User).where(func.lower(User.username) == uname.lower()))
        return (getattr(user, "membership_source", None) or "") if user is not None else None


def _trial_claimable(user: User) -> bool:
    """可领 3 天体验会员 = 没领过 且 当前还是体验期（已是尊享/永久会员不再显示）。"""
    if getattr(user, "trial_claimed_at", None) is not None:
        return False
    return membership_of(user).get("tier") == "trial"


def _to_out(user: User) -> AuthUserOut:
    return AuthUserOut(
        id=user.id,
        email=user.email,
        phone=user.phone,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        invite_code=getattr(user, "invite_code", None),
        invited_by=getattr(user, "invited_by", None),
        membership=membership_of(user),
        trial_claimable=_trial_claimable(user),
    )


def users_expiring_within(hours: int = 48) -> list[dict]:
    """会员将在 [now, now+hours] 内到期、留了邮箱、且非付费来源的用户——到期转化召回用。
    返回 [{id, username, email, expires_at(iso), source, days_left}]，按到期先后。付费(paid)用户不打扰。"""
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=max(1, int(hours)))
    out: list[dict] = []
    with session_scope() as session:
        rows = session.scalars(
            select(User).where(
                User.membership_expires_at.is_not(None),
                User.membership_expires_at > now,
                User.membership_expires_at <= until,
            ).order_by(User.membership_expires_at)
        ).all()
        for u in rows:
            if (u.membership_source or "") == "paid":
                continue  # 付费用户的续费另走（不混入免费转化召回）
            if not (u.email or "").strip():
                continue
            exp = u.membership_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            out.append({
                "id": u.id, "username": u.username, "email": u.email.strip(),
                "expires_at": exp.isoformat(), "source": u.membership_source or "",
                "days_left": max(0, (exp - now).days),
            })
    return out


def set_membership_expiry(username: str, expires_at: datetime, source: str = "admin") -> Optional[dict]:
    """直接把某用户会员到期时间设为指定时刻（精确到期日；SET 语义，覆盖原有）。
    返回新的会员状态 dict；用户不存在返回 None。"""
    uname = (username or "").strip()
    if not uname:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    with session_scope() as session:
        user = session.scalar(select(User).where(func.lower(User.username) == uname.lower()))
        if user is None:
            return None
        user.membership_expires_at = expires_at
        user.membership_source = source
        session.flush()
        return membership_of(user)


def claim_trial(user_id: str, days: int = 3) -> dict:
    """自助领取「登录送 N 天体验会员」：每账号仅一次，原子防重复。
    返回 {ok, reason?, days?, membership?}。"""
    uid = (user_id or "").strip()
    if not uid:
        return {"ok": False, "reason": "empty"}
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        # 原子认领：仅当 trial_claimed_at 为空时占位，杜绝并发重复领取
        res = session.execute(
            update(User)
            .where(User.id == uid, User.trial_claimed_at.is_(None))
            .values(trial_claimed_at=now)
        )
        if res.rowcount != 1:
            user = session.get(User, uid)
            if user is None:
                return {"ok": False, "reason": "not_found"}
            return {"ok": False, "reason": "claimed", "membership": membership_of(user)}
        user = session.get(User, uid)
        exp = user.membership_expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        # 已是永久会员：占位标记为已领（隐藏入口）即可，不动到期时间
        if exp is not None and (exp - now).days > _LIFETIME_DAYS:
            return {"ok": False, "reason": "already_member", "membership": membership_of(user)}
        start = exp if (exp is not None and exp > now) else now  # 未过期则顺延，否则从现在起
        user.membership_expires_at = start + timedelta(days=max(1, int(days)))
        # 体验赠送：来源标 trial，绝不冒充 paid（不计入邀请付费奖励）；不覆盖已有的 paid/reward
        if not user.membership_source or user.membership_source in ("trial", ""):
            user.membership_source = "trial"
        session.flush()
        return {"ok": True, "days": max(1, int(days)), "membership": membership_of(user)}


def set_membership(username: str, tier: str, days: int = 0, source: str = "admin") -> Optional[dict]:
    """直接设定某用户的会员级别（按用户名，大小写不敏感）——SET 语义，不叠加：
    - tier=lifetime → 永久会员
    - tier=premium  → 尊享会员，从现在起 days 天到期（覆盖原有）
    - 其他(trial)   → 体验期（取消会员）
    source 标会员来源（paid/reward/admin），仅 paid 计入「邀请付费用户」奖励。
    返回新的会员状态 dict；用户不存在返回 None。"""
    uname = (username or "").strip()
    if not uname:
        return None
    t = (tier or "").strip().lower()
    with session_scope() as session:
        user = session.scalar(select(User).where(func.lower(User.username) == uname.lower()))
        if user is None:
            return None
        if t == "lifetime":
            user.membership_expires_at = datetime(9999, 12, 31, tzinfo=timezone.utc)
            user.membership_source = source
        elif t == "premium":
            n = max(1, int(days or 0))
            user.membership_expires_at = datetime.now(timezone.utc) + timedelta(days=n)
            user.membership_source = source
        else:  # trial / 取消会员
            user.membership_expires_at = None
            user.membership_source = "trial"
        session.flush()
        return membership_of(user)


def grant_membership(username: str, days: int, permanent: bool = False, source: str = "admin") -> Optional[dict]:
    """给某用户开/续会员（按用户名，大小写不敏感）。
    permanent=True → 永久会员；days>0 从现有到期或现在起顺延尊享；days<=0 撤销转体验期。
    source 标会员来源（paid/reward/admin），仅 paid 计入「邀请付费用户」奖励。
    返回新的会员状态 dict；用户不存在返回 None。"""
    uname = (username or "").strip()
    if not uname:
        return None
    with session_scope() as session:
        user = session.scalar(select(User).where(func.lower(User.username) == uname.lower()))
        if user is None:
            return None
        now = datetime.now(timezone.utc)
        if permanent:
            user.membership_expires_at = datetime(9999, 12, 31, tzinfo=timezone.utc)  # 哨兵：永久
            user.membership_source = source
        elif days <= 0:
            user.membership_expires_at = None
            user.membership_source = "trial"
        else:
            base = user.membership_expires_at
            if base is not None and base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            # 已是永久(哨兵远期)时改限时：从现在起算，避免在 9999 上叠加溢出
            if base is not None and (base - now).days > _LIFETIME_DAYS:
                base = None
            start = base if (base and base > now) else now  # 未过期则续期，否则从现在起
            user.membership_expires_at = start + timedelta(days=days)
            user.membership_source = source
        session.flush()
        return membership_of(user)


_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 去掉易混 0/O/1/I/L


def _gen_invite_code(length: int = 6) -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(length))


def _unique_invite_code(session, length: int = 6) -> str:
    for _ in range(12):
        code = _gen_invite_code(length)
        if session.scalar(select(User).where(User.invite_code == code)) is None:
            return code
    return _gen_invite_code(length + 2)  # 极低概率连撞，加长再试


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def is_valid_phone(phone: str) -> bool:
    # 宽松校验：仅在用户填写时拦明显垃圾——允许 + 前缀与分隔符，6~20 位数字。
    digits = re.sub(r"[\s\-()]", "", phone.strip())
    return bool(re.fullmatch(r"\+?\d{6,20}", digits))


def count_users() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(User)) or 0


# 反刷号：每 IP 每日注册上限（默认 5，可 env 调）
def reg_ip_daily_max() -> int:
    try:
        return max(1, int(os.getenv("DEEPFOCUS_REG_IP_DAILY_MAX", "5")))
    except ValueError:
        return 5


def count_recent_ip_registrations(ip: str, hours: int = 24) -> int:
    """近 N 小时内同一 IP 的注册数（防同 IP 批量刷号）。本机/未知 IP 不计。"""
    ip = (ip or "").strip()
    if not ip or ip in ("127.0.0.1", "::1", "unknown"):
        return 0
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with session_scope() as session:
        return session.scalar(
            select(func.count()).select_from(User).where(User.registered_ip == ip, User.created_at >= since)
        ) or 0


# 一次性/临时邮箱域名黑名单（刷号常用）
_DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "10minutemail.com", "temp-mail.org",
    "tempmail.com", "tempmail.net", "yopmail.com", "sharklasers.com", "trashmail.com", "getnada.com",
    "maildrop.cc", "throwawaymail.com", "fakeinbox.com", "mohmal.com", "dispostable.com", "mintemail.com",
    "emailondeck.com", "spam4.me", "grr.la", "guerrillamailblock.com", "tmpmail.org", "tmpmail.net",
    "linshiyouxiang.net", "linshi-email.com", "24mail.chacuo.net", "0sg.net", "1secmail.com", "moakt.com",
}


def is_disposable_email(email: Optional[str]) -> bool:
    e = (email or "").strip().lower()
    if "@" not in e:
        return False
    return e.rsplit("@", 1)[-1] in _DISPOSABLE_EMAIL_DOMAINS


# Cloudflare Turnstile 人机校验（启用 = 同时配了 sitekey + secret）
def turnstile_sitekey() -> str:
    return (os.getenv("DEEPFOCUS_TURNSTILE_SITEKEY") or "").strip()


def turnstile_secret() -> str:
    return (os.getenv("DEEPFOCUS_TURNSTILE_SECRET") or "").strip()


def turnstile_enabled() -> bool:
    return bool(turnstile_sitekey() and turnstile_secret())


def turnstile_soft() -> bool:
    """软模式(默认开)：拿不到票据(CF 被墙/未加载)时放行，靠其余反刷防线兜底，避免误伤大陆真人。
    设 DEEPFOCUS_TURNSTILE_SOFT=false 改为强制(无票据即拒)。"""
    return os.getenv("DEEPFOCUS_TURNSTILE_SOFT", "true").strip().lower() != "false"


def get_user_out_by_id(user_id: str) -> Optional[AuthUserOut]:
    with session_scope() as session:
        user = session.get(User, user_id)
        return _to_out(user) if user else None


def get_user_out_by_email(email: str) -> Optional[AuthUserOut]:
    with session_scope() as session:
        # 邮箱入库即小写，直接比对带索引的列（避免 func.lower 让索引失效）。
        user = session.scalar(select(User).where(User.email == email.strip().lower()))
        return _to_out(user) if user else None


def list_users() -> list[AuthUserOut]:
    with session_scope() as session:
        rows = session.scalars(select(User).order_by(User.created_at)).all()
        return [_to_out(user) for user in rows]


def account_stats(recent_limit: int = 8) -> dict:
    """账号体系统计（数据看板用）：总数 / 今日 / 7 日新增、角色分布、手机&邮箱填写率、
    最近注册（只露用户名 + 是否填了手机/邮箱，不暴露明文 PII）。时间按北京时区归整。"""
    bj = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj)
    today_start = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now_bj - timedelta(days=7)

    def _to_bj(dt: datetime) -> datetime:
        # SQLite 取回的 created_at 可能是 naive（按 UTC 存）；统一按 UTC 解释再转北京。
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(bj)

    out: dict = {
        "total": 0, "new_today": 0, "new_7d": 0,
        "with_phone": 0, "with_email": 0,
        "by_role": {ROLE_ADMIN: 0, ROLE_ANALYST: 0, ROLE_VIEWER: 0},
        "recent": [],
    }
    try:
        with session_scope() as session:
            users = session.scalars(select(User).order_by(User.created_at.desc())).all()
            out["total"] = len(users)
            for u in users:
                if u.phone:
                    out["with_phone"] += 1
                if u.email:
                    out["with_email"] += 1
                out["by_role"][u.role] = out["by_role"].get(u.role, 0) + 1
                created = _to_bj(u.created_at)
                if created >= today_start:
                    out["new_today"] += 1
                if created >= week_start:
                    out["new_7d"] += 1
            for u in users[:recent_limit]:
                out["recent"].append({
                    "username": u.username,
                    "role": u.role,
                    "created_at": _to_bj(u.created_at).strftime("%Y-%m-%d %H:%M"),
                    "has_phone": bool(u.phone),
                    "has_email": bool(u.email),
                })
    except Exception as exc:  # 看板容错：统计失败不致整页崩
        logger.warning("account_stats 失败：%s", exc)
    return out


def invite_stats(top: int = 10) -> dict:
    """拉新看板数据：经邀请注册的人数/占比/今日·7日、有效邀请人数、邀请榜（谁拉的人最多）。"""
    from collections import Counter

    bj = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj)
    today_start = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now_bj - timedelta(days=7)

    def _to_bj(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(bj)

    out: dict = {
        "invited_total": 0, "invited_today": 0, "invited_7d": 0,
        "inviters": 0, "invite_rate": 0.0, "top": [],
    }
    try:
        with session_scope() as session:
            total = session.scalar(select(func.count()).select_from(User)) or 0
            invited = session.scalars(select(User).where(User.invited_by.isnot(None))).all()
            out["invited_total"] = len(invited)
            for u in invited:
                created = _to_bj(u.created_at)
                if created >= today_start:
                    out["invited_today"] += 1
                if created >= week_start:
                    out["invited_7d"] += 1
            counts = Counter(u.invited_by for u in invited)
            out["inviters"] = len(counts)
            out["invite_rate"] = round(out["invited_total"] / total * 100, 1) if total else 0.0
            if counts:
                id_to_name = {
                    uid: name
                    for uid, name in session.execute(
                        select(User.id, User.username).where(User.id.in_(list(counts.keys())))
                    )
                }
                out["top"] = [
                    {"username": id_to_name.get(uid, (uid or "")[:8]), "count": n}
                    for uid, n in counts.most_common(top)
                ]
    except Exception as exc:  # 看板容错
        logger.warning("invite_stats 失败：%s", exc)
    return out


def create_user(
    email: Optional[str],
    username: str,
    password: str,
    role: Optional[str] = None,
    phone: Optional[str] = None,
    invite_code_used: Optional[str] = None,
    registered_ip: Optional[str] = None,
) -> AuthUserOut:
    # 邮箱/手机号选填：空串归一成 None，避免空字符串撞唯一索引。
    email_n = (email or "").strip().lower() or None
    phone_n = re.sub(r"[\s\-()]", "", (phone or "").strip()) or None
    username_n = username.strip()
    with session_scope() as session:
        # 用户名必查重（大小写不敏感，堵死「Admin/admin」仿冒）；邮箱/手机号仅在填写时查重（多个 NULL 不冲突）。
        clash_filter = func.lower(User.username) == username_n.lower()
        if email_n is not None:
            clash_filter = clash_filter | (User.email == email_n)
        if phone_n is not None:
            clash_filter = clash_filter | (User.phone == phone_n)
        clash = session.scalar(select(User).where(clash_filter))
        if clash is not None:
            raise UserExistsError("用户名、邮箱或手机号已被占用")

        is_first_user = (session.scalar(select(func.count()).select_from(User)) or 0) == 0
        resolved_role = role or (ROLE_ADMIN if is_first_user else ROLE_ANALYST)
        if resolved_role not in VALID_ROLES:
            resolved_role = ROLE_VIEWER

        # 解析邀请码 → 邀请人（拉新关系）；自荐/无效码忽略
        invited_by = None
        code_used = (invite_code_used or "").strip().upper()
        if code_used:
            inviter = session.scalar(select(User).where(User.invite_code == code_used))
            if inviter is not None:
                invited_by = inviter.id

        # 被邀请的好友：凭有效邀请码注册即送 N 天尊享会员（来源标 invite，不计入邀请人的「付费」奖励）
        bonus_days = INVITE_SIGNUP_BONUS_DAYS if (invited_by is not None and INVITE_SIGNUP_BONUS_DAYS > 0) else 0
        member_expires_at = (datetime.now(timezone.utc) + timedelta(days=bonus_days)) if bonus_days > 0 else None
        member_source = "invite" if bonus_days > 0 else "trial"

        user = User(
            id=uuid.uuid4().hex,
            email=email_n,
            phone=phone_n,
            username=username_n,
            password_hash=hash_password(password),
            role=resolved_role,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            invite_code=_unique_invite_code(session),  # 每个新用户自带专属邀请码
            invited_by=invited_by,
            membership_source=member_source,
            membership_expires_at=member_expires_at,
            registered_ip=(registered_ip or None),
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            # 并发竞态：SELECT 查重通过但 INSERT 撞唯一约束（用户名/邮箱/手机号/邀请码）
            # → 当作"已占用"返回干净的 409，而非裸 500；DB 唯一约束保证绝不产生重复行。
            raise UserExistsError("用户名、邮箱或手机号已被占用") from exc
        return _to_out(user)


def authenticate(identifier: str, password: str) -> Optional[AuthUserOut]:
    ident = identifier.strip()
    with session_scope() as session:
        # 用户名大小写不敏感（防仿冒、登录更宽容）；邮箱本就小写入库。
        user = session.scalar(
            select(User).where(
                (User.email == ident.lower()) | (func.lower(User.username) == ident.lower())
            )
        )
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return _to_out(user)


def ensure_seed_admin() -> None:
    """生产首启可通过 env 预置管理员，避免锁死在外。"""
    email = os.getenv("DEEPFOCUS_ADMIN_EMAIL")
    password = os.getenv("DEEPFOCUS_ADMIN_PASSWORD")
    if not email or not password:
        return
    try:
        if get_user_out_by_email(email):
            return
        username = os.getenv("DEEPFOCUS_ADMIN_USERNAME", "admin")
        create_user(email, username, password, role=ROLE_ADMIN)
        logger.info("已预置管理员账号：%s", email)
    except UserExistsError:
        pass
    except Exception as exc:  # 预置失败不应阻断启动
        logger.warning("预置管理员失败：%s", exc)


def _reconcile_auth_schema() -> None:
    """幂等迁移既有 auth_users 老表：补 phone 列 + 放开 email 可空（兼容已落库的旧账号）。

    新库由 create_all 直接建成新 schema，此函数无操作；老库（email NOT NULL、无 phone）才迁移。
    SQLite 无法 DROP NOT NULL / 受限加唯一列 → 用 Alembic 式重建（先建 _new、灌数据、最后才换名），
    原表全程保留到最后一刻，中途失败不致数据无主；Postgres 直接 ALTER。
    """
    engine = storage.get_engine()
    insp = sa_inspect(engine)
    names = set(insp.get_table_names())
    if "auth_users" not in names:
        return

    # 清理历史迁移可能遗留的临时表——仅在其为空时删，绝不误删真实数据。
    for stale in ("auth_users_legacy", "auth_users_new"):
        if stale in names:
            with engine.begin() as conn:
                n = conn.execute(text(f"SELECT count(*) FROM {stale}")).scalar() or 0
                if n == 0:
                    conn.execute(text(f"DROP TABLE {stale}"))

    cols = {c["name"]: c for c in insp.get_columns("auth_users")}
    needs_phone = "phone" not in cols
    email_not_null = not cols.get("email", {}).get("nullable", True)
    if not needs_phone and not email_not_null:
        return  # 已是新 schema

    if storage.is_sqlite():
        phone_src = "NULL" if needs_phone else "phone"
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS auth_users_new"))
            # 新表先不带唯一索引——避免与旧表同名索引在重建期撞名。
            conn.execute(text(
                "CREATE TABLE auth_users_new ("
                " id VARCHAR(64) NOT NULL PRIMARY KEY,"
                " email VARCHAR(320), phone VARCHAR(32),"
                " username VARCHAR(120) NOT NULL,"
                " password_hash VARCHAR(255) NOT NULL,"
                " role VARCHAR(32), is_active BOOLEAN, created_at DATETIME)"
            ))
            conn.execute(text(
                f"INSERT INTO auth_users_new "
                f"(id, email, phone, username, password_hash, role, is_active, created_at) "
                f"SELECT id, email, {phone_src}, username, password_hash, role, is_active, created_at "
                f"FROM auth_users"
            ))
            conn.execute(text("DROP TABLE auth_users"))  # 旧表索引随表一并消失，腾出索引名
            conn.execute(text("ALTER TABLE auth_users_new RENAME TO auth_users"))
            conn.execute(text("CREATE UNIQUE INDEX ix_auth_users_email ON auth_users (email)"))
            conn.execute(text("CREATE UNIQUE INDEX ix_auth_users_phone ON auth_users (phone)"))
            conn.execute(text("CREATE UNIQUE INDEX ix_auth_users_username ON auth_users (username)"))
    else:
        with engine.begin() as conn:
            if needs_phone:
                conn.execute(text("ALTER TABLE auth_users ADD COLUMN phone VARCHAR(32)"))
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_auth_users_phone "
                    "ON auth_users (phone)"
                ))
            if email_not_null:
                conn.execute(text("ALTER TABLE auth_users ALTER COLUMN email DROP NOT NULL"))
    logger.info("auth_users 表已迁移到新 schema（phone 列 + email 可空）。")


def _ensure_invite_columns() -> None:
    """幂等迁移：给既有 auth_users 补 invite_code / invited_by 列，并为存量用户补发邀请码。"""
    engine = storage.get_engine()
    insp = sa_inspect(engine)
    if "auth_users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("auth_users")}
    with engine.begin() as conn:
        if "invite_code" not in cols:
            conn.execute(text("ALTER TABLE auth_users ADD COLUMN invite_code VARCHAR(16)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_auth_users_invite_code ON auth_users (invite_code)"))
        if "invited_by" not in cols:
            conn.execute(text("ALTER TABLE auth_users ADD COLUMN invited_by VARCHAR(64)"))
    # 存量用户补发邀请码
    try:
        with session_scope() as session:
            existing = set(session.scalars(select(User.invite_code).where(User.invite_code.isnot(None))).all())
            for u in session.scalars(select(User).where(User.invite_code.is_(None))).all():
                code = _gen_invite_code()
                while code in existing:
                    code = _gen_invite_code()
                existing.add(code)
                u.invite_code = code
    except Exception as exc:  # noqa: BLE001 - 补码失败不阻断启动
        logger.warning("补发邀请码失败：%s", exc)


def get_invite_overview(user_id: str) -> Optional[InviteOverview]:
    """某用户的拉新概览：自己的邀请码 + 已邀请人数 + 最近被邀请者。"""
    with session_scope() as session:
        me = session.get(User, user_id)
        if me is None:
            return None
        if not me.invite_code:  # 老账号兜底补码
            me.invite_code = _unique_invite_code(session)
        session.flush()
        invited = session.scalars(
            select(User).where(User.invited_by == user_id).order_by(User.created_at.desc())
        ).all()
        return InviteOverview(
            code=me.invite_code,
            invited_count=len(invited),
            invited=[{"username": u.username, "created_at": u.created_at.isoformat()} for u in invited[:50]],
        )


def _ensure_session_column() -> None:
    """幂等迁移：给既有 auth_users 补 session_id 列（单设备登录用）。"""
    engine = storage.get_engine()
    insp = sa_inspect(engine)
    if "auth_users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("auth_users")}
    if "session_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE auth_users ADD COLUMN session_id VARCHAR(64)"))


def _ensure_membership_column() -> None:
    """幂等迁移：给既有 auth_users 补 membership_expires_at 列（会员到期时间）。"""
    engine = storage.get_engine()
    insp = sa_inspect(engine)
    if "auth_users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("auth_users")}
    if "membership_expires_at" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE auth_users ADD COLUMN membership_expires_at DATETIME"))


def _ensure_referral_columns() -> None:
    """幂等迁移：补 membership_source / registered_ip / last_login_at（会员来源 + 邀请防刷去重 + 激活信号）。"""
    engine = storage.get_engine()
    insp = sa_inspect(engine)
    if "auth_users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("auth_users")}
    stmts = []
    if "membership_source" not in cols:
        stmts.append("ALTER TABLE auth_users ADD COLUMN membership_source VARCHAR(16)")
    if "registered_ip" not in cols:
        stmts.append("ALTER TABLE auth_users ADD COLUMN registered_ip VARCHAR(64)")
    if "last_login_at" not in cols:
        stmts.append("ALTER TABLE auth_users ADD COLUMN last_login_at DATETIME")
    if "trial_claimed_at" not in cols:
        stmts.append("ALTER TABLE auth_users ADD COLUMN trial_claimed_at DATETIME")
    if stmts:
        with engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))


def init_auth() -> None:
    # 安全硬校验：开了强制鉴权却没设独立密钥 = 用源码里写死的开发默认密钥签发 JWT（任何拿到源码的人都能伪造），
    # 鉴权形同虚设。宁可拒绝启动逼出显式配置，也不带病上线。
    if auth_required() and not os.getenv("DEEPFOCUS_JWT_SECRET"):
        raise RuntimeError(
            "DEEPFOCUS_AUTH_REQUIRED=true 但未设置 DEEPFOCUS_JWT_SECRET——"
            "将回落到源码内置的开发默认密钥，JWT 可被伪造、鉴权失效。"
            "请先设置高熵 DEEPFOCUS_JWT_SECRET（例如 `openssl rand -hex 32`）再启动。"
        )
    storage.init_storage()
    _reconcile_auth_schema()
    _ensure_invite_columns()
    _ensure_session_column()
    _ensure_membership_column()
    _ensure_referral_columns()
    ensure_seed_admin()


# --------------------------------------------------------------------------- #
# RBAC 规则（仅在 auth_required 时由中间件强制）
# --------------------------------------------------------------------------- #
# 这些前缀即使在强制态也无需鉴权（健康检查、登录注册、公开分享页、文档、研报工作台代理）。
PUBLIC_PREFIXES = (
    "/health",
    "/api/system/readiness",
    "/api/auth/login",
    "/api/auth/register",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/share",
    "/api/share",
    "/api/realtime/recall/click",  # 邮件/推送点击回流：用户无 JWT 也要能点回
    "/api/realtime/messages",      # 实时快讯列表 + /stream SSE：免登录免费看（终端公开页核心体验）
    "/api/review/",                # A股收盘复盘：免登录可看（信息价值引流位）；/api/admin/review/* 仍受保护
    "/research-workbench",
    # 管理端点（会员管理 / 看板私信）：无用户 JWT，由各 handler 自校验 DEEPFOCUS_MEMBERSHIP_TOKEN /
    # METRICS_TOKEN（与 /api/metrics 看板同款）；中间件放行才能在 AUTH_REQUIRED=true 时正常用 curl/看板。
    "/api/admin/",
    "/api/payment-qr/",   # 收款码图片（公开，购买页展示）
    "/api/v1",            # 合作方/开发者 API：handler 自校验 X-API-Key（中间件放行，不走用户 JWT）
)

# 免费公开的 /api 端点（终端公开页的免费层：行情 / 快讯 / 研报列表 / 打点）。
# 必须用「精确匹配」而非前缀——否则 "/api/research/wire"（研报列表，免费）会顺带放行
# "/api/research/wire-file"（研报原文，须登录）与 "/api/research/vision-analyze"（AI 解读，须登录）。
PUBLIC_EXACT = frozenset(
    {
        "/api/market/quotes",       # 行情报价
        "/api/market/search",       # 标的搜索（命令面板）
        "/api/market-dashboard",    # 大盘指标盘
        "/api/headlines",           # AI 今日头条（免费引流位）
        "/api/news/reactions",      # 资讯聚合情绪（看多/看空计数）：匿名也可看，handler 内 mine 仅登录可见
        "/api/track-record",        # 「我们提前发现的」平台战绩：公开引流（登录附个人战绩）
        "/api/ai-fund/snapshot",    # AI 模拟盘快照：公开展示位（净值/持仓/决策日志），/api/ai-fund/tick 仍受管理员保护
        "/api/ai-fund/arena",       # AI 策略竞技场排行榜：公开展示位（各派收益/排名/观点 + 沪深300）
        "/api/research/wire",        # 海外投行研报「列表」（标题/来源/日期），原文(wire-file)仍须登录
        # AI 解读(研报/资讯)：放行到端点，由 _check_ai_quota 精细把关——**非会员/匿名绝不触发新生成（省 token）**，
        # 仅当结果已缓存才放行、每天免费 1 次（匿名按 IP），未缓存/超额 → 403(匿名引导登录)/402(非会员引导开通)。
        # 会员无限。注意：放行的是 analyze（须带 body 走配额），研报原文 wire-file 仍须登录、不在此列。
        "/api/research/vision-analyze",
        "/api/news/ai-analyze",
        "/api/metrics/pageview",    # 匿名打点
        "/api/metrics/event",       # 匿名打点
        # 看板（HTML 外壳 + 数据接口）：均无 JWT（独立 HTML 页用 ?token= 取数），
        # 由各 handler 自校验 DEEPFOCUS_METRICS_TOKEN；中间件放行才能在开启全局鉴权时正常加载。
        "/api/metrics/dashboard",
        "/api/metrics/members",     # 会员看板数据（handler 自校验 metrics token）
        "/api/metrics/referrals",   # 邀请活动看板数据（handler 自校验 metrics token）
        "/api/metrics/summary",
        "/api/metrics/activity",
        "/api/metrics/review-quality",
        "/api/metrics/growth",      # 增长分析看板数据（handler 自校验 metrics token）
        "/api/research/auth-status",
        "/api/research/auth",
        "/api/activity",            # 匿名操作流水
        "/api/qr",                  # 二维码渲染（分享/邀请）
        "/api/referral/leaderboard",  # 邀请排行榜（脱敏，引流位）
        "/api/referral/campaign",     # 限时冲榜赛（榜单+奖励+倒计时，引流位）
        "/api/auth/captcha",          # 人机校验配置（公开，前端据此渲染 Turnstile）
        "/api/payment-config",      # 购买页配置（套餐价格 + 收款码是否就绪），公开展示
        "/api/app/version",         # 移动 App 版本检查（壳更新提示），公开
        "/api/recall/t1/run",       # T+1 召回手动触发（handler 自校验 metrics token）
        "/api/recall/t1/stats",     # T+1 召回效果（handler 自校验 metrics token）
        "/api/recall/expiry/run",   # 到期转化召回手动触发（handler 自校验 metrics token）
        "/api/recall/expiry/stats", # 到期转化召回效果（handler 自校验 metrics token）
        "/api/realtime/recall/webpush-key",  # Web Push VAPID 公钥（公开、非敏感，前端订阅离线召回前读取）
        # 微信推送台：独立 HTML 页(?token=)+ 推送接口，handler 自校验 metrics token；与看板同款，故放行 JWT 网关。
        "/api/weixin/console",
        "/api/weixin/push-news",
        "/api/weixin/push-config",
    }
)

# (method, path 前缀) → 需要管理员。method 为 "*" 表示任意方法。
ADMIN_RULES = (
    ("*", "/api/auth/users"),
    ("POST", "/api/fingpt/model-config"),
    ("POST", "/api/mcp/servers"),
    ("PUT", "/api/mcp/servers"),
    ("DELETE", "/api/mcp/servers"),
    ("POST", "/api/data-sources/agent-crawl"),
    ("POST", "/api/data-sources/keyword-crawl"),  # 与 agent-crawl 同属管理端爬取，曾漏在规则外→trial 用户可触发出网/耗资源
)


def role_satisfies(user_role: str, required_role: str) -> bool:
    return ROLE_RANK.get(user_role, 0) >= ROLE_RANK.get(required_role, 99)


def required_role_for(method: str, path: str) -> Optional[str]:
    for rule_method, prefix in ADMIN_RULES:
        if (rule_method == "*" or rule_method == method) and path.startswith(prefix):
            return ROLE_ADMIN
    return None  # 其余 /api 路由：任意已认证用户即可


def is_public_path(path: str) -> bool:
    if path in PUBLIC_EXACT:  # 免费 /api 端点：精确匹配，绝不前缀放行（防 wire→wire-file 泄露）
        return True
    return any(path == prefix or path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


# --------------------------------------------------------------------------- #
# 中间件 + 依赖
# --------------------------------------------------------------------------- #
class AuthMiddleware(BaseHTTPMiddleware):
    """集中式鉴权 + 路径级 RBAC。

    始终把解析出的 claims 挂到 `request.state.auth_claims`（即便旁路态，供 handler 守卫读取）；
    仅当 `DEEPFOCUS_AUTH_REQUIRED=true` 时对非公开 /api 路由强制 401/403。
    """

    async def dispatch(self, request: Request, call_next):
        token = bearer_token(request)
        claims = decode_token(token) if token else None
        request.state.auth_claims = claims

        if not auth_required():
            return await call_next(request)
        if request.method == "OPTIONS":  # 放行 CORS 预检
            return await call_next(request)

        path = request.url.path
        if is_public_path(path) or not path.startswith("/api/"):
            return await call_next(request)

        if claims is None:
            return JSONResponse(
                {"detail": "未认证或登录已过期，请重新登录"}, status_code=401
            )

        required = required_role_for(request.method, path)
        if required and not role_satisfies(str(claims.get("role", "")), required):
            return JSONResponse({"detail": "权限不足，需要更高角色"}, status_code=403)

        return await call_next(request)


def current_claims(request: Request) -> Optional[dict]:
    """读取中间件挂载的 claims；缺失时就地解析 Bearer（兼容未经中间件的调用路径）。"""
    claims = getattr(request.state, "auth_claims", None)
    if claims is not None:
        return claims
    token = bearer_token(request)
    return decode_token(token) if token else None


def require_current_user(request: Request) -> dict:
    claims = current_claims(request)
    if claims is None:
        raise HTTPException(status_code=401, detail="未认证或登录已过期，请重新登录")
    if not session_is_current(claims):
        # 多设备(≤2)登录：账号登录设备超过上限，最早的这台被挤下线。前端据此清登录态并提示。
        raise HTTPException(status_code=401, detail="账号登录设备已达上限（最多 2 台），此设备已退出，请重新登录")
    return claims


def optional_current_user(request: Request) -> Optional[dict]:
    """有有效登录 → 返回 claims；匿名或令牌失效 → 返回 None（不抛 401）。

    用于「匿名可免费体验一次、之后引导登录」的端点（AI 解读）：让 handler 自行按 IP 限额，
    而不是在中间件/依赖层直接 401 把匿名挡死。"""
    claims = current_claims(request)
    if claims is None or not session_is_current(claims):
        return None
    return claims


def require_admin(request: Request) -> dict:
    claims = require_current_user(request)
    if not role_satisfies(str(claims.get("role", "")), ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return claims
