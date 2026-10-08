"""证券主数据：代码↔简称 的站内唯一事实源，供标的打标（alias 匹配）与下钻归一使用。

构建：scripts/build_securities_master.py 从新浪 A 股列表全量拉取 + 内置热门港美股 upsert。
symbol 口径与终端自选一致：A股裸 6 位（600519）、港股裸 4-5 位（00700）、美股裸 ticker（AAPL）。
alias 匹配只收 ≥2 字符的简称，降低单词/单字误命中。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_REALTIME_MESSAGE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".realtime_messages.sqlite3"),
    )
)

# 内置热门港美股（A股由构建脚本全量覆盖；港美股列表源杂且时效差，先保交易热点，随自选积累再扩）。
# 键=symbol（终端自选口径），值=中文简称（alias 匹配用）。
BUILTIN_HK_US: dict[str, str] = {
    "00700": "腾讯控股", "09988": "阿里巴巴-W", "03690": "美团-W", "09618": "京东集团-SW",
    "09999": "网易-S", "09888": "百度集团-SW", "01024": "快手-W", "01810": "小米集团-W",
    "02318": "中国平安", "00388": "香港交易所", "00941": "中国移动", "00939": "建设银行",
    "AAPL": "苹果", "NVDA": "英伟达", "TSLA": "特斯拉", "MSFT": "微软", "GOOGL": "谷歌",
    "AMZN": "亚马逊", "META": "Meta", "NFLX": "奈飞", "AMD": "超威半导体", "INTC": "英特尔",
    "COIN": "Coinbase", "MSTR": "微策投资", "PLTR": "Palantir", "UBER": "优步",
}

_ALIASES: dict[str, str] | None = None  # 进程级缓存：alias(简称/全称) -> symbol


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS securities (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_securities_name ON securities(name)")
    conn.commit()
    return conn


def upsert_securities(rows: list[tuple[str, str, str]]) -> int:
    """rows: [(symbol, name, market)]，同名后写覆盖（简uniq冲突时以最新数据源为准）。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO securities(symbol, name, market, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=excluded.updated_at",
            [(s, n, m, now) for s, n, m in rows if s and n],
        )
    globals()["_ALIASES"] = None  # 主数据变更后失效缓存
    return len(rows)


def alias_map() -> dict[str, str]:
    """alias(简称/全称, ≥2字符) -> symbol。进程级缓存；主数据 upsert 后自动失效。"""
    global _ALIASES
    if _ALIASES is not None:
        return _ALIASES
    aliases: dict[str, str] = {}
    with _connect() as conn:
        for symbol, name in conn.execute("SELECT symbol, name FROM securities"):
            clean = str(name or "").strip()
            if len(clean) >= 2 and clean not in aliases:
                aliases[clean] = symbol
    for symbol, name in BUILTIN_HK_US.items():
        if len(name) >= 2:
            aliases.setdefault(name, symbol)
    _ALIASES = aliases
    return aliases


def resolve_symbol(text: str) -> str | None:
    """按简称/全称把自由文本归一到 symbol（打标灌入研报 instruments 等场景）。"""
    clean = str(text or "").strip()
    if len(clean) < 2:
        return None
    return alias_map().get(clean)
