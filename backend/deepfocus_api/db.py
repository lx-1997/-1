"""统一 SQLite 连接入口：全站一致的 WAL + busy_timeout，消除多模块裸连接并发写锁库。

- WAL：持久属性，首次对某库文件设置后长期生效；每次执行为幂等兜底。
- busy_timeout：每连接属性，写锁冲突时等待而非立刻抛 "database is locked"。
- synchronous=NORMAL：WAL 下安全等级足够的持久化默认值。
只读 URI 连接（file:...?mode=ro）不要走这里——它们不能切换 journal_mode。
"""
from __future__ import annotations

import sqlite3
from typing import Any


def connect(path: Any, *, timeout: float = 10.0) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass  # 库文件只读或文件系统不支持共享内存时保持原 journal 模式
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
