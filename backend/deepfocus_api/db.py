"""统一 SQLite 连接入口：全站一致的 WAL + busy_timeout，消除多模块裸连接并发写锁库。

- WAL：持久属性，首次对某库文件设置后长期生效；每次执行为幂等兜底。
- busy_timeout：每连接属性，写锁冲突时等待而非立刻抛 "database is locked"。
- synchronous=NORMAL：WAL 下安全等级足够的持久化默认值。
只读 URI 连接（file:...?mode=ro）不要走这里——它们不能切换 journal_mode。
"""
from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from typing import Any


def data_path(filename: str, env_var: str | None = None) -> Path:
    """Resolve persisted data: per-store override > DATA_DIR > legacy backend path.

    Keeping the legacy default avoids moving existing deployments implicitly.
    Containers set DEEPFOCUS_DATA_DIR to their mounted persistent volume.
    """
    explicit = os.getenv(env_var, "").strip() if env_var else ""
    if explicit:
        return Path(explicit).expanduser()
    directory = os.getenv("DEEPFOCUS_DATA_DIR", "").strip()
    return (Path(directory).expanduser() if directory else Path(__file__).resolve().parents[1]) / filename


def connect(path: Any, *, timeout: float = 10.0) -> sqlite3.Connection:
    if str(path) != ":memory:" and not str(path).startswith("file:"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass  # 库文件只读或文件系统不支持共享内存时保持原 journal 模式
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
