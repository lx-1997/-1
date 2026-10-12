"""Account-bound, single-use confirmation receipts for platform operations."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import sqlite3

from .db import connect, data_path


class PlatformActionStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path

    def _open(self):
        path = self.path or str(data_path(".platform_actions.sqlite3", "DEEPFOCUS_PLATFORM_ACTION_DB_PATH"))
        conn = connect(path)
        if str(path) != ":memory:" and not str(path).startswith("file:"):
            Path(path).chmod(0o600)
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS platform_actions (
            token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL,
            status TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS platform_assistant_runs (
            owner TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            started REAL NOT NULL, status TEXT NOT NULL, result TEXT,
            PRIMARY KEY(owner, request_id)
        )""")
        conn.commit()
        return conn

    def begin_run(self, owner: str, request_id: str, fingerprint: str) -> dict[str, Any] | None:
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM platform_assistant_runs WHERE started < ?", (time.time() - 86400,))
            row = conn.execute("SELECT * FROM platform_assistant_runs WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise ValueError("同一请求 ID 不能用于不同的操作")
                if row["status"] == "completed":
                    return json.loads(row["result"])
                raise ValueError("该请求已提交，请查询操作状态；不会重复执行")
            conn.execute("INSERT INTO platform_assistant_runs VALUES (?, ?, ?, ?, 'executing', NULL)", (owner, request_id, fingerprint, time.time()))
        return None

    def finish_run(self, owner: str, request_id: str, result: dict[str, Any]) -> None:
        with closing(self._open()) as conn, conn:
            conn.execute("UPDATE platform_assistant_runs SET status='completed',result=? WHERE owner=? AND request_id=?", (json.dumps(result, ensure_ascii=False), owner, request_id))

    def checkpoint_run(self, owner: str, request_id: str, result: dict[str, Any]) -> None:
        with closing(self._open()) as conn, conn:
            conn.execute("UPDATE platform_assistant_runs SET result=? WHERE owner=? AND request_id=? AND status='executing'",
                         (json.dumps(result, ensure_ascii=False), owner, request_id))

    def interrupt_run(self, owner: str, request_id: str) -> dict[str, Any] | None:
        """Recover known/uncertain receipts without restarting any operation."""
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT result FROM platform_assistant_runs WHERE owner=? AND request_id=? AND status='executing'",
                               (owner, request_id)).fetchone()
            if not row or not row["result"]:
                return None
            result = json.loads(row["result"])
            result.update(interrupted=True, answer="助手回复中断。已取得的操作回执保留如下；结果未确认的操作请到对应页面核对，不会自动重试。")
            result["content"] = result["answer"]
            result["ok"] = any(action.get("status") in {"completed", "needs_confirmation"}
                               for action in result.get("platform_actions", []))
            conn.execute("UPDATE platform_assistant_runs SET status='completed',result=? WHERE owner=? AND request_id=?",
                         (json.dumps(result, ensure_ascii=False), owner, request_id))
            return result

    def abort_unstarted_run(self, owner: str, request_id: str) -> None:
        with closing(self._open()) as conn, conn:
            conn.execute("DELETE FROM platform_assistant_runs WHERE owner=? AND request_id=? AND status='executing'", (owner, request_id))

    def propose(self, owner: str, payload: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        with closing(self._open()) as conn, conn:
            conn.execute("DELETE FROM platform_actions WHERE expires < ?", (time.time() - 86400,))
            conn.execute("INSERT INTO platform_actions VALUES (?, ?, ?, 'pending', ?, NULL)", (
                hashlib.sha256(token.encode()).hexdigest(), owner, time.time() + 600,
                json.dumps(payload, ensure_ascii=False),
            ))
        return token

    def claim(self, owner: str, token: str) -> tuple[str, dict[str, Any]]:
        """Atomically claim once; an interrupted execution never becomes retryable."""
        key = hashlib.sha256(token.encode()).hexdigest()
        with closing(self._open()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM platform_actions WHERE token_hash=? AND owner=?", (key, owner)).fetchone()
            if not row:
                raise ValueError("确认操作不存在或不属于当前账号")
            if row["status"] == "completed":
                return "completed", json.loads(row["receipt"])
            if row["status"] != "pending":
                raise ValueError("该操作已提交，请查看执行回执；不会重复执行")
            if row["expires"] < time.time():
                raise ValueError("操作确认已过期，请重新提出请求")
            conn.execute("UPDATE platform_actions SET status='executing' WHERE token_hash=?", (key,))
            return "executing", json.loads(row["payload"])

    def finish(self, owner: str, token: str, receipt: dict[str, Any]) -> None:
        with closing(self._open()) as conn, conn:
            conn.execute("UPDATE platform_actions SET status='completed',receipt=? WHERE token_hash=? AND owner=? AND status='executing'", (
                json.dumps(receipt, ensure_ascii=False), hashlib.sha256(token.encode()).hexdigest(), owner,
            ))
