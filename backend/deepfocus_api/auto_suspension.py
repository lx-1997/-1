"""Automatic one-day account suspension for warned high-frequency page refreshes."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from . import metrics_store

logger = logging.getLogger("deepfocus.auto_suspension")

_DB_PATH = metrics_store.DB_PATH.with_name(".auto_suspension.sqlite3")
_METRICS_DB_PATH = metrics_store.DB_PATH
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_BJ = timezone(timedelta(hours=8))


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=8)
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS auto_suspension_watches (
                    user_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    warning_message_id INTEGER NOT NULL,
                    starts_at TEXT NOT NULL,
                    threshold_count INTEGER NOT NULL,
                    window_seconds INTEGER NOT NULL,
                    ban_seconds INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auto_suspensions (
                    user_id TEXT PRIMARY KEY,
                    suspended_until TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    trigger_count INTEGER NOT NULL,
                    warning_message_id INTEGER NOT NULL
                );
                """
            )
            conn.commit()
            os.chmod(_DB_PATH, 0o600)
            _SCHEMA_READY = True
        finally:
            conn.close()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def arm_watch(
    *,
    user_id: str,
    actor_id: str,
    warning_message_id: int,
    starts_at: str,
    threshold_count: int = 10,
    window_seconds: int = 300,
    ban_seconds: int = 86400,
) -> dict[str, Any]:
    """Start a persisted per-user watch after the stated warning grace period."""
    uid = str(user_id or "").strip()
    actor = str(actor_id or "").strip()
    if not uid or actor != f"u:{uid}":
        raise ValueError("user_id and actor_id do not match")
    if threshold_count < 1 or window_seconds < 1 or ban_seconds < 1:
        raise ValueError("watch limits must be positive")
    start_iso = _iso_utc(_parse_utc(starts_at))
    _ensure_schema()
    now_iso = _iso_utc(datetime.now(timezone.utc))
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO auto_suspension_watches
               (user_id,actor_id,warning_message_id,starts_at,threshold_count,window_seconds,ban_seconds,enabled,created_at)
               VALUES (?,?,?,?,?,?,?,1,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 actor_id=excluded.actor_id,
                 warning_message_id=excluded.warning_message_id,
                 starts_at=excluded.starts_at,
                 threshold_count=excluded.threshold_count,
                 window_seconds=excluded.window_seconds,
                 ban_seconds=excluded.ban_seconds,
                 enabled=1,
                 created_at=excluded.created_at""",
            (uid, actor, int(warning_message_id), start_iso, int(threshold_count), int(window_seconds), int(ban_seconds), now_iso),
        )
        conn.commit()
        row = conn.execute(
            "SELECT starts_at,threshold_count,window_seconds,ban_seconds,enabled FROM auto_suspension_watches WHERE user_id=?",
            (uid,),
        ).fetchone()
        return {
            "user_id": uid,
            "starts_at": row[0],
            "threshold_count": int(row[1]),
            "window_seconds": int(row[2]),
            "ban_seconds": int(row[3]),
            "enabled": bool(row[4]),
        }
    finally:
        conn.close()


def watch_status(user_id: str) -> Optional[dict[str, Any]]:
    _ensure_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT actor_id,warning_message_id,starts_at,threshold_count,window_seconds,ban_seconds,enabled FROM auto_suspension_watches WHERE user_id=?",
            (str(user_id or "").strip(),),
        ).fetchone()
        if not row:
            return None
        return {
            "actor_id": row[0],
            "warning_message_id": int(row[1]),
            "starts_at": row[2],
            "threshold_count": int(row[3]),
            "window_seconds": int(row[4]),
            "ban_seconds": int(row[5]),
            "enabled": bool(row[6]),
        }
    finally:
        conn.close()


def current_suspension(user_id: str) -> Optional[dict[str, Any]]:
    _ensure_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT suspended_until,triggered_at,trigger_count FROM auto_suspensions WHERE user_id=?",
            (str(user_id or "").strip(),),
        ).fetchone()
        if not row or _parse_utc(row[0]) <= datetime.now(timezone.utc):
            return None
        return {"suspended_until": row[0], "triggered_at": row[1], "trigger_count": int(row[2])}
    finally:
        conn.close()


def is_user_suspended(user_id: str) -> bool:
    try:
        return current_suspension(user_id) is not None
    except (OSError, sqlite3.Error, ValueError):
        logger.exception("Could not read automatic suspension state")
        return False


def evaluate_pageview(user_id: str, actor_id: str) -> Optional[dict[str, Any]]:
    """Suspend a watched account when its warned post-grace refresh rate is exceeded."""
    uid = str(user_id or "").strip()
    if not uid or actor_id != f"u:{uid}":
        return None
    _ensure_schema()
    now = datetime.now(timezone.utc)
    conn = _connect()
    try:
        watch = conn.execute(
            "SELECT actor_id,warning_message_id,starts_at,threshold_count,window_seconds,ban_seconds,enabled FROM auto_suspension_watches WHERE user_id=?",
            (uid,),
        ).fetchone()
    finally:
        conn.close()
    if not watch or watch[0] != actor_id or not bool(watch[6]):
        return None

    starts_at = _parse_utc(watch[2])
    if now < starts_at:
        return None
    window_start = max(starts_at, now - timedelta(seconds=int(watch[4])))
    try:
        metrics_uri = _METRICS_DB_PATH.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(metrics_uri, uri=True, timeout=8) as metrics:
            count = int(metrics.execute(
                "SELECT COUNT(*) FROM activity_log WHERE actor_id=? AND action='pageview' AND ts>=? AND ts<=?",
                (actor_id, _iso_utc(window_start), _iso_utc(now)),
            ).fetchone()[0])
    except (OSError, sqlite3.Error):
        logger.exception("Could not read pageview activity for automatic suspension")
        return None
    if count < int(watch[3]):
        return None

    until = now + timedelta(seconds=int(watch[5]))
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute(
            "SELECT actor_id,warning_message_id,starts_at,threshold_count,window_seconds,ban_seconds,enabled FROM auto_suspension_watches WHERE user_id=?",
            (uid,),
        ).fetchone()
        existing = conn.execute(
            "SELECT suspended_until FROM auto_suspensions WHERE user_id=?", (uid,)
        ).fetchone()
        if not latest or not bool(latest[6]) or (existing and _parse_utc(existing[0]) > now):
            conn.rollback()
            return None
        conn.execute(
            """INSERT OR REPLACE INTO auto_suspensions
               (user_id,suspended_until,triggered_at,trigger_count,warning_message_id)
               VALUES (?,?,?,?,?)""",
            (uid, _iso_utc(until), _iso_utc(now), count, int(latest[1])),
        )
        conn.execute("UPDATE auto_suspension_watches SET enabled=0 WHERE user_id=?", (uid,))
        conn.commit()
        return {
            "suspended_until": _iso_utc(until),
            "triggered_at": _iso_utc(now),
            "trigger_count": count,
            "threshold_count": int(latest[3]),
            "window_seconds": int(latest[4]),
            "ban_seconds": int(latest[5]),
            "warning_message_id": int(latest[1]),
        }
    finally:
        conn.close()


def _verified_subject(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        # Verify the signature but ignore token expiry so a one-day suspension
        # cannot be bypassed when the browser's 12-hour JWT expires mid-ban.
        from .auth import JWT_ALG, jwt_secret

        claims = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[JWT_ALG],
            options={"verify_exp": False},
        )
        subject = str(claims.get("sub") or "").strip()
        return subject or None
    except (JWTError, ValueError, TypeError):
        return None


class AutoSuspensionMiddleware(BaseHTTPMiddleware):
    """Reject API requests from a suspended account while preserving support access."""

    _SUPPORT_PATHS = {
        "/api/support/thread",
        "/api/support/unread",
        "/api/support/send",
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/") or path in self._SUPPORT_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization") or ""
        parts = header.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        user_id = _verified_subject(token)
        if not user_id:
            return await call_next(request)
        try:
            suspension = current_suspension(user_id)
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("Could not check automatic suspension state")
            suspension = None
        if not suspension:
            return await call_next(request)
        until = _parse_utc(suspension["suspended_until"])
        seconds = max(1, int((until - datetime.now(timezone.utc)).total_seconds() + 0.999))
        shown = until.astimezone(_BJ).strftime("%Y-%m-%d %H:%M")
        return JSONResponse(
            {
                "detail": f"账号因高频刷新暂时封禁至北京时间 {shown}，期间仍可联系管理员。",
                "suspended_until": suspension["suspended_until"],
            },
            status_code=403,
            headers={"Retry-After": str(seconds)},
        )
