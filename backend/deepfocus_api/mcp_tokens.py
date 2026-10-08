"""个人 MCP 接入令牌（dfm_ 前缀）的签发 / 校验 / 限流 / 计量。

让用户把 daocaijing 内容与 AI 问答接入自己的 MCP 客户端（Claude Desktop、Cursor、
Claude Code 等）。安全设计沿用合作方 API（partner_api）的成熟范式：
- 明文令牌只在签发时返回一次，库内只存 SHA-256 摘要（泄露 DB 也无法复用令牌）。
- 仅保留前缀（前 12 位）供用户在控制台识别，不可反推完整令牌。
- 校验按摘要查库，须 active 且未过期；令牌绑定的用户被停用后立即失效。
- 每令牌内存滑动窗口限流（单进程；多 worker 需 Redis）+ 非有损日成功计数表做日配额。
- 防暴破：无效令牌按来源 IP 限速。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Optional

from . import db
from .shared_utils import utc_now_iso

TOKEN_PREFIX = "dfm_"  # DeepFocus MCP token
_PREFIX_SHOW = 12

RATE_PER_MIN = int(os.getenv("DEEPFOCUS_MCP_RATE_PER_MIN", "60") or 60)
# 体验期用户每日调用上限；尊享/永久会员 0=不限。
TRIAL_DAILY_QUOTA = int(os.getenv("DEEPFOCUS_MCP_TRIAL_DAILY_QUOTA", "300") or 300)
TOKEN_TTL_DAYS = int(os.getenv("DEEPFOCUS_MCP_TOKEN_DAYS", "365") or 365)
_MAX_TOKENS_PER_USER = int(os.getenv("DEEPFOCUS_MCP_TOKENS_MAX", "5") or 5)

# 防暴破：无效令牌按来源 IP 限速（与 partner_api 同款阈值）
_AUTH_FAILS: dict = {}
_AUTH_FAIL_WINDOW = 60.0
_AUTH_FAIL_MAX = int(os.getenv("DEEPFOCUS_MCP_AUTHFAIL_MAX", "20") or 20)

_WINDOWS: dict = {}  # {token_hash: deque[float]} 最近 60s


def _db_path() -> Path:
    return Path(
        os.getenv(
            "DEEPFOCUS_MCP_TOKEN_DB_PATH",
            str(Path(__file__).resolve().parents[1] / ".mcp_tokens.sqlite3"),
        )
    )


def _connect() -> sqlite3.Connection:
    conn = db.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_mcp_tokens_db() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_tokens (
                token_hash TEXT PRIMARY KEY,
                token_prefix TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '默认令牌',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                call_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user ON mcp_tokens(user_id)
            """
        )
        # 非有损「每令牌·每日成功数」：日配额判定的唯一真相源，永不裁剪。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_usage_daily (
                token_hash TEXT NOT NULL,
                day TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (token_hash, day)
            )
            """
        )
        conn.commit()


def issue_token(user_id: str, username: str, name: str = "") -> dict:
    """为登录用户签发一把新令牌。明文只此一次返回。超出每人上限抛 ValueError。"""
    init_mcp_tokens_db()
    if count_active(user_id) >= _MAX_TOKENS_PER_USER:
        raise ValueError(f"每个账号最多 {_MAX_TOKENS_PER_USER} 把接入令牌，请先撤销不用的")
    token = TOKEN_PREFIX + secrets.token_urlsafe(24)
    prefix = token[:_PREFIX_SHOW]
    expires_at = None
    if TOKEN_TTL_DAYS > 0:
        from datetime import datetime, timedelta, timezone

        expires_at = (datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mcp_tokens (token_hash, token_prefix, user_id, username, name, active, created_at, expires_at)"
            " VALUES (?,?,?,?,?,1,?,?)",
            (_hash_token(token), prefix, user_id, (username or "")[:120],
             (name or "").strip()[:80] or "默认令牌", utc_now_iso(), expires_at),
        )
        conn.commit()
    return {"token": token, "token_prefix": prefix, "name": (name or "").strip()[:80] or "默认令牌",
            "expires_at": expires_at}


def verify_token(token: str) -> Optional[dict]:
    """按摘要校验令牌：存在 + active + 未过期。返回记录（含 user_id，不含完整令牌）或 None。"""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    init_mcp_tokens_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM mcp_tokens WHERE token_hash = ? AND active = 1", (_hash_token(token),)
        ).fetchone()
    if not row:
        return None
    exp = row["expires_at"]
    if exp:
        try:
            from datetime import datetime, timezone

            if datetime.fromisoformat(exp) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            pass
    rec = dict(row)
    rec["_token_hash"] = rec.pop("token_hash")
    return rec


def list_tokens(user_id: str) -> list:
    """某用户的令牌列表（只含前缀，无法反推完整令牌），附今日成功数。"""
    init_mcp_tokens_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT token_prefix, name, active, created_at, expires_at, last_used_at, call_count"
            " FROM mcp_tokens WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["today"] = today_count_by_prefix(conn=None, token_prefix=d["token_prefix"], user_id=user_id)
        out.append(d)
    return out


def revoke_token(user_id: str, token_prefix: str) -> bool:
    """撤销令牌：必须命中 user_id 防越权撤销他人令牌；仅 active=1 的行算撤销成功
    （SQLite changes() 对「匹配但值未变」的行也计数，须在 WHERE 里排除已撤销行）。"""
    init_mcp_tokens_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE mcp_tokens SET active = 0 WHERE user_id = ? AND token_prefix = ? AND active = 1",
            (user_id, (token_prefix or "").strip()),
        )
        conn.commit()
    return cur.rowcount > 0


def count_active(user_id: str) -> int:
    with _connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM mcp_tokens WHERE user_id = ? AND active = 1", (user_id,)
        ).fetchone()[0])


def register_auth_fail(ip: str) -> bool:
    now = time.time()
    dq = _AUTH_FAILS.setdefault(ip or "?", deque())
    cutoff = now - _AUTH_FAIL_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    dq.append(now)
    return len(dq) > _AUTH_FAIL_MAX


def auth_fail_blocked(ip: str) -> bool:
    dq = _AUTH_FAILS.get(ip or "?")
    if not dq:
        return False
    cutoff = time.time() - _AUTH_FAIL_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq) > _AUTH_FAIL_MAX


def check_rate(token_hash: str, rate_per_min: int = RATE_PER_MIN) -> bool:
    now = time.time()
    win = _WINDOWS.setdefault(token_hash, deque())
    cutoff = now - 60.0
    while win and win[0] < cutoff:
        win.popleft()
    if len(win) >= max(1, int(rate_per_min)):
        return False
    win.append(now)
    return True


def today_count(token_hash: str) -> int:
    today = utc_now_iso()[:10]
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT success FROM mcp_usage_daily WHERE token_hash = ? AND day = ?", (token_hash, today)
            ).fetchone()
            return int(row["success"]) if row else 0
    except sqlite3.Error:
        return 0


def today_count_by_prefix(conn: Optional[sqlite3.Connection], token_prefix: str, user_id: str) -> int:
    """控制台展示用：按前缀+用户查今日成功数（前缀不唯一，限定 user_id）。"""
    today = utc_now_iso()[:10]
    try:
        own = conn
        if own is None:
            own = _connect()
        row = own.execute(
            "SELECT COALESCE(SUM(d.success),0) FROM mcp_usage_daily d"
            " JOIN mcp_tokens t ON t.token_hash = d.token_hash"
            " WHERE t.token_prefix = ? AND t.user_id = ? AND d.day = ?",
            (token_prefix, user_id, today),
        ).fetchone()
        return int(row[0])
    except sqlite3.Error:
        return 0


def record_success(token_hash: str, token_prefix: str) -> None:
    """记一次成功调用：日计数 +1 + call_count +1 + last_used_at。失败静默不拖垮主链路。"""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO mcp_usage_daily (token_hash, day, success) VALUES (?,?,1)"
                " ON CONFLICT(token_hash, day) DO UPDATE SET success = success + 1",
                (token_hash, utc_now_iso()[:10]),
            )
            conn.execute(
                "UPDATE mcp_tokens SET call_count = call_count + 1, last_used_at = ? WHERE token_hash = ?",
                (utc_now_iso(), token_hash),
            )
            conn.commit()
    except sqlite3.Error:
        pass
