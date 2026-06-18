from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .shared_utils import utc_now_iso

"""
微信 iLink 绑定存储：DeepFocus 账号 ↔ iLink bot（多租户、各自 1:1）。

一个 DeepFocus 登录用户扫码绑定一个独立 bot；服务器持有其 token 并独立轮询。
缓存每个绑定「最近一次有效 context_token」——既给问答回包用，也是「准推送」实验的燃料
（iLink 出站强制带 context_token，见 [[wechat-push-channel]]）。
"""

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_WEIXIN_BIND_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".weixin_bind.sqlite3"),
    )
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_weixin_bind_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weixin_bind (
                deepfocus_user_id TEXT PRIMARY KEY,
                ilink_bot_id TEXT NOT NULL,
                token TEXT NOT NULL,
                base_url TEXT NOT NULL,
                wechat_user_id TEXT,
                context_token TEXT,
                context_token_at TEXT,
                bound_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weixin_bind_bot ON weixin_bind(ilink_bot_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weixin_bind_active ON weixin_bind(active)")
        # 推送订阅配置（老库迁移）：push_scope = off/watchlist/all；push_symbols = 关注标的(名称或代码)JSON 列表。
        for col, ddl in (("push_scope", "TEXT DEFAULT 'off'"), ("push_symbols", "TEXT DEFAULT '[]'"), ("username", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE weixin_bind ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        # 投递可观测日志：自动推/保活的每一次结果落库，告别"发了就不管、断联无据可查"。
        # kind=push|keepalive；status=delivered|failed|skipped|dead|recovered；detail=原因/计数。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weixin_push_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                bot_id TEXT,
                username TEXT,
                status TEXT NOT NULL,
                detail TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weixin_push_log_ts ON weixin_push_log(ts)")
        conn.commit()


_PUSH_LOG_KEEP = int(os.getenv("DEEPFOCUS_WEIXIN_PUSHLOG_KEEP", "2000"))  # 日志保留条数上限，防无界增长


def log_push_event(kind: str, status: str, bot_id: str = "", username: str = "", detail: str = "") -> None:
    """记一条投递/保活事件（best-effort，绝不抛出影响推送主链路）。"""
    try:
        init_weixin_bind_db()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO weixin_push_log (ts, kind, bot_id, username, status, detail) VALUES (?,?,?,?,?,?)",
                (utc_now_iso(), kind, (bot_id or "")[:32], (username or "")[:64], status, (detail or "")[:300]),
            )
            # 防无界增长：超量则裁掉最老的（保留最近 _PUSH_LOG_KEEP 条）
            conn.execute(
                "DELETE FROM weixin_push_log WHERE id <= (SELECT MAX(id) FROM weixin_push_log) - ?",
                (_PUSH_LOG_KEEP,),
            )
            conn.commit()
    except Exception:  # noqa: BLE001  可观测性不能反噬主链路
        pass


def recent_push_events(limit: int = 100, kind: str = "") -> List[Dict[str, Any]]:
    """最近的投递/保活事件（管理员排障用），按时间倒序。"""
    init_weixin_bind_db()
    limit = max(1, min(limit, 1000))
    with _connect() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM weixin_push_log WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM weixin_push_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def upsert_binding(
    deepfocus_user_id: str,
    ilink_bot_id: str,
    token: str,
    base_url: str,
    wechat_user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """登录用户扫码确认后落库；同一用户重绑则覆盖（换 bot/token），并重置为 active。
    username 用于问答白名单门控（仅白名单用户走 run_tool_agent，其余只收推送不耗 token）。"""
    init_weixin_bind_db()
    rec = {
        "deepfocus_user_id": deepfocus_user_id,
        "ilink_bot_id": ilink_bot_id,
        "token": token,
        "base_url": base_url,
        "wechat_user_id": wechat_user_id,
        "username": username,
        "bound_at": utc_now_iso(),
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO weixin_bind (
                deepfocus_user_id, ilink_bot_id, token, base_url, wechat_user_id, username,
                context_token, context_token_at, bound_at, active
            ) VALUES (
                :deepfocus_user_id, :ilink_bot_id, :token, :base_url, :wechat_user_id, :username,
                NULL, NULL, :bound_at, 1
            )
            ON CONFLICT(deepfocus_user_id) DO UPDATE SET
                ilink_bot_id=excluded.ilink_bot_id,
                token=excluded.token,
                base_url=excluded.base_url,
                wechat_user_id=excluded.wechat_user_id,
                username=excluded.username,
                bound_at=excluded.bound_at,
                active=1
            """,
            rec,
        )
        conn.commit()
    return get_by_user(deepfocus_user_id) or rec


def update_context_token(ilink_bot_id: str, context_token: str) -> None:
    """每次收到该 bot 的用户入站消息时，刷新缓存的 context_token。"""
    if not context_token:
        return
    init_weixin_bind_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE weixin_bind SET context_token=?, context_token_at=? WHERE ilink_bot_id=?",
            (context_token, utc_now_iso(), ilink_bot_id),
        )
        conn.commit()


def clear_context_token(ilink_bot_id: str) -> None:
    """清掉缓存的 context_token（推送返回失效错误时调用）：标记该绑定为"冷"，
    停止徒劳空发；待用户下次给 bot 发消息时 update_context_token 自动重建。"""
    init_weixin_bind_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE weixin_bind SET context_token=NULL, context_token_at=NULL WHERE ilink_bot_id=?",
            (ilink_bot_id,),
        )
        conn.commit()


def set_push_config(deepfocus_user_id: str, scope: str, symbols: List[str]) -> Optional[Dict[str, Any]]:
    """设置某绑定的自动推送订阅：scope ∈ off/watchlist/all；symbols = 关注标的(名称或代码)。"""
    import json as _json
    scope = scope if scope in ("off", "watchlist", "all") else "off"
    syms = [str(s).strip() for s in (symbols or []) if str(s).strip()][:50]
    init_weixin_bind_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE weixin_bind SET push_scope=?, push_symbols=? WHERE deepfocus_user_id=?",
            (scope, _json.dumps(syms, ensure_ascii=False), deepfocus_user_id),
        )
        conn.commit()
    return get_by_user(deepfocus_user_id)


def deactivate(ilink_bot_id: str) -> None:
    """会话过期（errcode -14）等：标记失效，等用户重扫。"""
    init_weixin_bind_db()
    with _connect() as conn:
        conn.execute("UPDATE weixin_bind SET active=0 WHERE ilink_bot_id=?", (ilink_bot_id,))
        conn.commit()


def get_by_user(deepfocus_user_id: str) -> Optional[Dict[str, Any]]:
    init_weixin_bind_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM weixin_bind WHERE deepfocus_user_id=?", (deepfocus_user_id,)
        ).fetchone()
    return _row(row)


def get_by_bot(ilink_bot_id: str) -> Optional[Dict[str, Any]]:
    init_weixin_bind_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM weixin_bind WHERE ilink_bot_id=?", (ilink_bot_id,)
        ).fetchone()
    return _row(row)


def list_active() -> List[Dict[str, Any]]:
    """所有有效绑定——轮询器据此为每个 bot 起一条 getupdates 循环。"""
    init_weixin_bind_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM weixin_bind WHERE active=1 ORDER BY bound_at").fetchall()
    return [r for r in (_row(x) for x in rows) if r]


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    import json as _json
    d = dict(row)
    d["active"] = bool(d.get("active", 1))
    d["push_scope"] = d.get("push_scope") or "off"
    try:
        d["push_symbols"] = _json.loads(d.get("push_symbols") or "[]")
    except (ValueError, TypeError):
        d["push_symbols"] = []
    return d
