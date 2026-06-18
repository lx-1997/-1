"""按账号存储的用户偏好（当前：自选股 watchlist）。

设计：
- 复用统一 storage 层（SQLite 默认，可切 Postgres），表 `user_watchlist` 按 user_id 主键。
- symbols / names 以 JSON 文本存（无需为每个代码建行，读写一次到位）。
- 仅登录用户落库；未登录走前端 localStorage，互不影响。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .storage import Base, session_scope

logger = logging.getLogger("deepfocus.user_prefs")

_MAX_SYMBOLS = 100      # 单账号自选上限，防滥用
_MAX_SYMBOL_LEN = 20
_MAX_NAME_LEN = 40


class UserWatchlist(Base):
    __tablename__ = "user_watchlist"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbols: Mapped[str] = mapped_column(Text, default="[]")   # JSON 数组
    names: Mapped[str] = mapped_column(Text, default="{}")     # JSON 对象 code->名称
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _sanitize(symbols: list, names: dict) -> tuple[list[str], dict[str, str]]:
    syms: list[str] = []
    seen: set[str] = set()
    for raw in (symbols or []):
        s = str(raw).strip()[:_MAX_SYMBOL_LEN]
        if s and s not in seen:
            seen.add(s)
            syms.append(s)
        if len(syms) >= _MAX_SYMBOLS:
            break
    nm: dict[str, str] = {}
    for k, v in (names or {}).items():
        key = str(k).strip()[:_MAX_SYMBOL_LEN]
        val = str(v).strip()[:_MAX_NAME_LEN]
        if key and val:
            nm[key] = val
    return syms, nm


def get_watchlist(user_id: str) -> Optional[dict]:
    """返回 {"symbols": [...], "names": {...}}；无记录返回 None（前端据此判断首次、用默认种子）。"""
    with session_scope() as session:
        row = session.get(UserWatchlist, user_id)
        if row is None:
            return None
        try:
            return {"symbols": json.loads(row.symbols or "[]"), "names": json.loads(row.names or "{}")}
        except Exception:  # 损坏数据当空处理，不崩
            return {"symbols": [], "names": {}}


def set_watchlist(user_id: str, symbols: list, names: dict) -> dict:
    """整表覆盖式保存（前端持有全量，简单可靠）。返回入库后的归一结果。"""
    syms, nm = _sanitize(symbols, names)
    with session_scope() as session:
        row = session.get(UserWatchlist, user_id)
        if row is None:
            row = UserWatchlist(user_id=user_id)
            session.add(row)
        row.symbols = json.dumps(syms, ensure_ascii=False)
        row.names = json.dumps(nm, ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
    return {"symbols": syms, "names": nm}
