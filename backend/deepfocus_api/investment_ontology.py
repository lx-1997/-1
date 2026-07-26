"""DeepFocus 投资本体 MVP。

这个模块先把 Palantir Ontology 最关键的四件事跑通：
1. Canonical entity：公司、证券、事件、证据、论点、持仓有稳定身份；
2. Typed relationship：关系带方向、置信度、证据与有效时间；
3. Action：用户可以对对象执行受约束的演示动作；
4. Audit：每次动作落审计台账，绝不连接真实券商或下单系统。

第一版使用独立 SQLite，便于在现有 FastAPI 架构中渐进试点。后续可将相同
schema 迁移到 PostgreSQL / Apache AGE，而不影响前端对象协议。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .shared_utils import utc_now_iso


def _db_path() -> Path:
    return Path(
        os.getenv(
            "DEEPFOCUS_ONTOLOGY_DB_PATH",
            str(Path(__file__).resolve().parents[1] / ".investment_ontology.sqlite3"),
        )
    )


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


class OntologyDemoActionRequest(BaseModel):
    security_id: str
    action_type: str
    reason: str = Field(default="", max_length=500)


class OntologyDemoActionRecord(BaseModel):
    id: str
    security_id: str
    action_type: str
    action_label: str
    status: str
    actor: str
    reason: str
    created_at: str


_ACTION_LABELS = {
    "keep_watch": "维持观察",
    "reduce_paper": "模拟减仓",
    "request_research": "发起补证",
    "invalidate_thesis": "标记论点失效",
}


_ENTITIES: list[dict[str, Any]] = [
    {
        "id": "portfolio:demo:core",
        "type": "Portfolio",
        "key": "demo-core",
        "label": "核心组合 · Demo",
        "market": "MULTI",
        "attrs": {"nav": 1_000_000, "currency": "CNY", "mode": "paper"},
    },
    {
        "id": "issuer:cn:moutai",
        "type": "Issuer",
        "key": "issuer-cn-moutai",
        "label": "贵州茅台酒股份有限公司",
        "market": "CN",
        "attrs": {"sector": "食品饮料", "industry": "白酒"},
    },
    {
        "id": "security:cn:600519.SH",
        "type": "Security",
        "key": "600519.SH",
        "label": "贵州茅台",
        "market": "CN",
        "attrs": {
            "ticker": "600519",
            "exchange": "SSE",
            "currency": "CNY",
            "price": 1297.0,
            "as_of": "2026-07-26",
        },
    },
    {
        "id": "position:demo:600519.SH",
        "type": "Position",
        "key": "demo-core:600519.SH",
        "label": "贵州茅台持仓",
        "market": "CN",
        "attrs": {"weight_pct": 12.5, "cost": 1442.0, "pnl_pct": -10.1, "risk_budget_pct": 10.0},
    },
    {
        "id": "thesis:600519:pricing-power",
        "type": "Thesis",
        "key": "600519-pricing-power",
        "label": "品牌壁垒支撑长期定价权",
        "market": "CN",
        "attrs": {
            "status": "under_review",
            "confidence": 0.68,
            "invalidation": "连续两个报告期营收低个位数增长且批价持续低于建议零售价",
        },
    },
    {
        "id": "event:600519:earnings-growth",
        "type": "Event",
        "key": "600519-earnings-growth",
        "label": "最新财报：营收 +6.3%，净利 +1.5%",
        "market": "CN",
        "attrs": {"event_type": "earnings", "occurred_at": "2026-07-25", "severity": "medium"},
    },
    {
        "id": "event:600519:channel-pressure",
        "type": "Event",
        "key": "600519-channel-pressure",
        "label": "渠道库存与批价承压",
        "market": "CN",
        "attrs": {"event_type": "channel", "occurred_at": "2026-07-26", "severity": "high", "demo": True},
    },
    {
        "id": "evidence:600519:financials",
        "type": "Evidence",
        "key": "600519-financials",
        "label": "东方财富财务指标快照",
        "market": "CN",
        "attrs": {"source": "eastmoney", "credibility": 0.86, "known_at": "2026-07-26T07:34:00Z"},
    },
    {
        "id": "evidence:600519:channel-check",
        "type": "Evidence",
        "key": "600519-channel-check",
        "label": "渠道调研摘要（演示证据）",
        "market": "CN",
        "attrs": {"source": "demo-research", "credibility": 0.58, "known_at": "2026-07-26T08:00:00Z"},
    },
    {
        "id": "issuer:cn:catl",
        "type": "Issuer",
        "key": "issuer-cn-catl",
        "label": "宁德时代新能源科技股份有限公司",
        "market": "CN",
        "attrs": {"sector": "电力设备", "industry": "动力电池"},
    },
    {
        "id": "security:cn:300750.SZ",
        "type": "Security",
        "key": "300750.SZ",
        "label": "宁德时代",
        "market": "CN",
        "attrs": {
            "ticker": "300750",
            "exchange": "SZSE",
            "currency": "CNY",
            "price": 383.01,
            "as_of": "2026-07-26",
        },
    },
    {
        "id": "position:demo:300750.SZ",
        "type": "Position",
        "key": "demo-core:300750.SZ",
        "label": "宁德时代持仓",
        "market": "CN",
        "attrs": {"weight_pct": 8.3, "cost": 348.0, "pnl_pct": 10.1, "risk_budget_pct": 12.0},
    },
    {
        "id": "thesis:300750:global-scale",
        "type": "Thesis",
        "key": "300750-global-scale",
        "label": "规模与技术优势维持全球份额",
        "market": "CN",
        "attrs": {
            "status": "active",
            "confidence": 0.74,
            "invalidation": "海外份额连续两个季度下滑且单位盈利同步恶化",
        },
    },
    {
        "id": "event:300750:lithium-cost",
        "type": "Event",
        "key": "300750-lithium-cost",
        "label": "锂价回落改善电池成本",
        "market": "CN",
        "attrs": {"event_type": "commodity", "occurred_at": "2026-07-24", "severity": "medium"},
    },
    {
        "id": "event:300750:overseas-policy",
        "type": "Event",
        "key": "300750-overseas-policy",
        "label": "海外政策提高本地化门槛",
        "market": "CN",
        "attrs": {"event_type": "policy", "occurred_at": "2026-07-26", "severity": "high"},
    },
    {
        "id": "evidence:300750:supply-chain",
        "type": "Evidence",
        "key": "300750-supply-chain",
        "label": "锂电产业链价格与政策证据包",
        "market": "CN",
        "attrs": {"source": "deepfocus-supply-chain", "credibility": 0.79, "known_at": "2026-07-26T06:00:00Z"},
    },
    {
        "id": "issuer:cn:zijin",
        "type": "Issuer",
        "key": "issuer-cn-zijin",
        "label": "紫金矿业集团股份有限公司",
        "market": "CN",
        "attrs": {"sector": "有色金属", "industry": "铜金"},
    },
    {
        "id": "security:cn:601899.SH",
        "type": "Security",
        "key": "601899.SH",
        "label": "紫金矿业",
        "market": "CN",
        "attrs": {
            "ticker": "601899",
            "exchange": "SSE",
            "currency": "CNY",
            "price": 31.58,
            "as_of": "2026-07-26",
        },
    },
    {
        "id": "position:demo:601899.SH",
        "type": "Position",
        "key": "demo-core:601899.SH",
        "label": "紫金矿业持仓",
        "market": "CN",
        "attrs": {"weight_pct": 11.2, "cost": 28.4, "pnl_pct": 11.2, "risk_budget_pct": 15.0},
    },
    {
        "id": "thesis:601899:metal-cycle",
        "type": "Thesis",
        "key": "601899-metal-cycle",
        "label": "铜金共振驱动盈利上修",
        "market": "CN",
        "attrs": {
            "status": "active",
            "confidence": 0.81,
            "invalidation": "铜价跌破成本曲线关键区间且主要矿山产量指引下修",
        },
    },
    {
        "id": "event:601899:gold",
        "type": "Event",
        "key": "601899-gold",
        "label": "金价维持历史高位",
        "market": "CN",
        "attrs": {"event_type": "commodity", "occurred_at": "2026-07-26", "severity": "medium"},
    },
    {
        "id": "event:601899:copper",
        "type": "Event",
        "key": "601899-copper",
        "label": "铜供给扰动推升价格弹性",
        "market": "CN",
        "attrs": {"event_type": "supply", "occurred_at": "2026-07-25", "severity": "high"},
    },
    {
        "id": "evidence:601899:commodity",
        "type": "Evidence",
        "key": "601899-commodity",
        "label": "黄金与铜价时间序列证据",
        "market": "CN",
        "attrs": {"source": "market-dashboard", "credibility": 0.91, "known_at": "2026-07-26T08:10:00Z"},
    },
]


_ALIASES = [
    ("600519", "security:cn:600519.SH", "ticker", "CN"),
    ("600519.SH", "security:cn:600519.SH", "canonical", "CN"),
    ("SH600519", "security:cn:600519.SH", "vendor", "CN"),
    ("贵州茅台", "security:cn:600519.SH", "name", "CN"),
    ("300750", "security:cn:300750.SZ", "ticker", "CN"),
    ("300750.SZ", "security:cn:300750.SZ", "canonical", "CN"),
    ("宁德时代", "security:cn:300750.SZ", "name", "CN"),
    ("601899", "security:cn:601899.SH", "ticker", "CN"),
    ("601899.SH", "security:cn:601899.SH", "canonical", "CN"),
    ("紫金矿业", "security:cn:601899.SH", "name", "CN"),
]


_RELATIONSHIPS: list[dict[str, Any]] = [
    {"id": "rel:moutai-security", "source": "security:cn:600519.SH", "type": "REPRESENTS", "target": "issuer:cn:moutai", "polarity": 0, "confidence": 1.0},
    {"id": "rel:moutai-position", "source": "position:demo:600519.SH", "type": "POSITION_IN", "target": "portfolio:demo:core", "polarity": 0, "confidence": 1.0},
    {"id": "rel:moutai-position-security", "source": "position:demo:600519.SH", "type": "HOLDS", "target": "security:cn:600519.SH", "polarity": 0, "confidence": 1.0},
    {"id": "rel:moutai-thesis", "source": "thesis:600519:pricing-power", "type": "ABOUT", "target": "security:cn:600519.SH", "polarity": 0, "confidence": 1.0},
    {"id": "rel:moutai-fin-evidence", "source": "evidence:600519:financials", "type": "EVIDENCES", "target": "event:600519:earnings-growth", "polarity": 0, "confidence": 0.86},
    {"id": "rel:moutai-fin-thesis", "source": "event:600519:earnings-growth", "type": "WEAKENS", "target": "thesis:600519:pricing-power", "polarity": -1, "confidence": 0.78},
    {"id": "rel:moutai-channel-evidence", "source": "evidence:600519:channel-check", "type": "EVIDENCES", "target": "event:600519:channel-pressure", "polarity": 0, "confidence": 0.58},
    {"id": "rel:moutai-channel-thesis", "source": "event:600519:channel-pressure", "type": "CONTRADICTS", "target": "thesis:600519:pricing-power", "polarity": -1, "confidence": 0.72},
    {"id": "rel:moutai-thesis-position", "source": "thesis:600519:pricing-power", "type": "GOVERNS", "target": "position:demo:600519.SH", "polarity": 0, "confidence": 0.9},
    {"id": "rel:catl-security", "source": "security:cn:300750.SZ", "type": "REPRESENTS", "target": "issuer:cn:catl", "polarity": 0, "confidence": 1.0},
    {"id": "rel:catl-position", "source": "position:demo:300750.SZ", "type": "POSITION_IN", "target": "portfolio:demo:core", "polarity": 0, "confidence": 1.0},
    {"id": "rel:catl-position-security", "source": "position:demo:300750.SZ", "type": "HOLDS", "target": "security:cn:300750.SZ", "polarity": 0, "confidence": 1.0},
    {"id": "rel:catl-thesis", "source": "thesis:300750:global-scale", "type": "ABOUT", "target": "security:cn:300750.SZ", "polarity": 0, "confidence": 1.0},
    {"id": "rel:catl-evidence-cost", "source": "evidence:300750:supply-chain", "type": "EVIDENCES", "target": "event:300750:lithium-cost", "polarity": 0, "confidence": 0.79},
    {"id": "rel:catl-cost-thesis", "source": "event:300750:lithium-cost", "type": "SUPPORTS", "target": "thesis:300750:global-scale", "polarity": 1, "confidence": 0.76},
    {"id": "rel:catl-policy-thesis", "source": "event:300750:overseas-policy", "type": "WEAKENS", "target": "thesis:300750:global-scale", "polarity": -1, "confidence": 0.84},
    {"id": "rel:catl-thesis-position", "source": "thesis:300750:global-scale", "type": "GOVERNS", "target": "position:demo:300750.SZ", "polarity": 0, "confidence": 0.9},
    {"id": "rel:zijin-security", "source": "security:cn:601899.SH", "type": "REPRESENTS", "target": "issuer:cn:zijin", "polarity": 0, "confidence": 1.0},
    {"id": "rel:zijin-position", "source": "position:demo:601899.SH", "type": "POSITION_IN", "target": "portfolio:demo:core", "polarity": 0, "confidence": 1.0},
    {"id": "rel:zijin-position-security", "source": "position:demo:601899.SH", "type": "HOLDS", "target": "security:cn:601899.SH", "polarity": 0, "confidence": 1.0},
    {"id": "rel:zijin-thesis", "source": "thesis:601899:metal-cycle", "type": "ABOUT", "target": "security:cn:601899.SH", "polarity": 0, "confidence": 1.0},
    {"id": "rel:zijin-evidence-gold", "source": "evidence:601899:commodity", "type": "EVIDENCES", "target": "event:601899:gold", "polarity": 0, "confidence": 0.91},
    {"id": "rel:zijin-evidence-copper", "source": "evidence:601899:commodity", "type": "EVIDENCES", "target": "event:601899:copper", "polarity": 0, "confidence": 0.91},
    {"id": "rel:zijin-gold-thesis", "source": "event:601899:gold", "type": "SUPPORTS", "target": "thesis:601899:metal-cycle", "polarity": 1, "confidence": 0.82},
    {"id": "rel:zijin-copper-thesis", "source": "event:601899:copper", "type": "SUPPORTS", "target": "thesis:601899:metal-cycle", "polarity": 1, "confidence": 0.86},
    {"id": "rel:zijin-thesis-position", "source": "thesis:601899:metal-cycle", "type": "GOVERNS", "target": "position:demo:601899.SH", "polarity": 0, "confidence": 0.9},
]


_ASSET_CONFIG = {
    "security:cn:600519.SH": {
        "security": "security:cn:600519.SH",
        "issuer": "issuer:cn:moutai",
        "position": "position:demo:600519.SH",
        "thesis": "thesis:600519:pricing-power",
        "events": ["event:600519:earnings-growth", "event:600519:channel-pressure"],
        "evidence": ["evidence:600519:financials", "evidence:600519:channel-check"],
        "verdict": "需要复核",
        "verdict_tone": "warning",
        "change_summary": "新增两条反证：利润增速低于营收，渠道批价压力上升。",
        "recommended_action": "维持观察；若仓位高于风险预算，先在模拟盘降至 10%。",
        "action_type": "reduce_paper",
        "action_reason": "论点置信度下降且当前仓位 12.5% 高于 10% 风险预算",
    },
    "security:cn:300750.SZ": {
        "security": "security:cn:300750.SZ",
        "issuer": "issuer:cn:catl",
        "position": "position:demo:300750.SZ",
        "thesis": "thesis:300750:global-scale",
        "events": ["event:300750:lithium-cost", "event:300750:overseas-policy"],
        "evidence": ["evidence:300750:supply-chain"],
        "verdict": "持有观察",
        "verdict_tone": "neutral",
        "change_summary": "成本端改善，但海外本地化政策形成新的执行风险。",
        "recommended_action": "保持当前仓位，发起海外产能与份额补证任务。",
        "action_type": "request_research",
        "action_reason": "正负事件对冲，需要补证海外份额与产能兑现进度",
    },
    "security:cn:601899.SH": {
        "security": "security:cn:601899.SH",
        "issuer": "issuer:cn:zijin",
        "position": "position:demo:601899.SH",
        "thesis": "thesis:601899:metal-cycle",
        "events": ["event:601899:gold", "event:601899:copper"],
        "evidence": ["evidence:601899:commodity"],
        "verdict": "论点增强",
        "verdict_tone": "positive",
        "change_summary": "黄金高位与铜供给扰动同时强化盈利上修路径。",
        "recommended_action": "维持持仓与止盈纪律，不因短期强势追高。",
        "action_type": "keep_watch",
        "action_reason": "核心论点得到两条独立商品价格证据支持，仓位仍在风险预算内",
    },
}


def init_ontology_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                canonical_key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                valid_from TEXT,
                valid_to TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_aliases (
                alias TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                scheme TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (alias, scheme, market),
                FOREIGN KEY (entity_id) REFERENCES ontology_entities(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_relationships (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                polarity INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 1,
                evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                valid_from TEXT,
                valid_to TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES ontology_entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES ontology_entities(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_actions (
                id TEXT PRIMARY KEY,
                security_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (security_id) REFERENCES ontology_entities(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ontology_entity_type ON ontology_entities(entity_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ontology_alias ON ontology_aliases(alias)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ontology_rel_source ON ontology_relationships(source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ontology_rel_target ON ontology_relationships(target_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ontology_action_security ON ontology_actions(security_id, created_at DESC)")
        now = utc_now_iso()
        for entity in _ENTITIES:
            conn.execute(
                """
                INSERT INTO ontology_entities (
                    id, entity_type, canonical_key, label, market, attributes_json,
                    valid_from, valid_to, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    entity_type=excluded.entity_type,
                    canonical_key=excluded.canonical_key,
                    label=excluded.label,
                    market=excluded.market,
                    attributes_json=excluded.attributes_json,
                    updated_at=excluded.updated_at
                """,
                (
                    entity["id"],
                    entity["type"],
                    entity["key"],
                    entity["label"],
                    entity["market"],
                    json.dumps(entity["attrs"], ensure_ascii=False),
                    None,
                    None,
                    now,
                    now,
                ),
            )
        for alias, entity_id, scheme, market in _ALIASES:
            conn.execute(
                """
                INSERT INTO ontology_aliases (alias, entity_id, scheme, market, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(alias, scheme, market) DO UPDATE SET entity_id=excluded.entity_id
                """,
                (alias.upper() if scheme != "name" else alias, entity_id, scheme, market, now),
            )
        for rel in _RELATIONSHIPS:
            conn.execute(
                """
                INSERT INTO ontology_relationships (
                    id, source_id, relation_type, target_id, polarity, confidence,
                    evidence_ids_json, valid_from, valid_to, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_id=excluded.source_id,
                    relation_type=excluded.relation_type,
                    target_id=excluded.target_id,
                    polarity=excluded.polarity,
                    confidence=excluded.confidence
                """,
                (
                    rel["id"],
                    rel["source"],
                    rel["type"],
                    rel["target"],
                    rel["polarity"],
                    rel["confidence"],
                    None,
                    None,
                    now,
                ),
            )
        conn.commit()


def _entity(row: sqlite3.Row) -> dict[str, Any]:
    attrs = json.loads(row["attributes_json"] or "{}")
    return {
        "id": row["id"],
        "type": row["entity_type"],
        "label": row["label"],
        "canonical_key": row["canonical_key"],
        "market": row["market"],
        "attributes": attrs,
    }


def _all_entities(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM ontology_entities WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return {row["id"]: _entity(row) for row in rows}


def resolve_alias(alias: str, market: str = "") -> Optional[dict[str, Any]]:
    init_ontology_db()
    raw = (alias or "").strip()
    if not raw:
        return None
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT e.*, a.alias, a.scheme
            FROM ontology_aliases a
            JOIN ontology_entities e ON e.id=a.entity_id
            WHERE (a.alias=? OR a.alias=?) AND (?='' OR a.market=?)
            ORDER BY CASE a.scheme WHEN 'canonical' THEN 0 WHEN 'ticker' THEN 1 ELSE 2 END
            LIMIT 1
            """,
            (raw, raw.upper(), market.upper(), market.upper()),
        ).fetchone()
    if not rows:
        return None
    result = _entity(rows)
    result["matched_alias"] = rows["alias"]
    result["matched_scheme"] = rows["scheme"]
    return result


def _list_actions(conn: sqlite3.Connection, security_id: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM ontology_actions
        WHERE security_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (security_id, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "security_id": row["security_id"],
            "action_type": row["action_type"],
            "action_label": _ACTION_LABELS.get(row["action_type"], row["action_type"]),
            "status": row["status"],
            "actor": row["actor"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_demo_snapshot(security_id: str = "security:cn:600519.SH") -> dict[str, Any]:
    init_ontology_db()
    config = _ASSET_CONFIG.get(security_id)
    if not config:
        raise ValueError("未知的 Canonical Security")
    selected_id = config["security"]
    ids = [
        config["security"],
        config["issuer"],
        config["position"],
        config["thesis"],
        "portfolio:demo:core",
        *config["events"],
        *config["evidence"],
    ]
    with _connect() as conn:
        entities = _all_entities(conn, ids)
        placeholders = ",".join("?" for _ in ids)
        rel_rows = conn.execute(
            f"""
            SELECT * FROM ontology_relationships
            WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
            ORDER BY id
            """,
            [*ids, *ids],
        ).fetchall()
        aliases = conn.execute(
            """
            SELECT alias, scheme, market
            FROM ontology_aliases
            WHERE entity_id=?
            ORDER BY scheme, alias
            """,
            (selected_id,),
        ).fetchall()
        actions = _list_actions(conn, selected_id)

    ordered_nodes = [
        entities[eid]
        for eid in [
            config["evidence"][0],
            *(config["evidence"][1:]),
            *config["events"],
            config["thesis"],
            config["security"],
            config["position"],
            "portfolio:demo:core",
        ]
        if eid in entities
    ]
    # 按对象类型固定列。这样只有一条 Evidence 的标的也不会导致 Event/Thesis
    # 整体左移，切换标的时读图方向始终一致。
    positions_by_type: dict[str, list[tuple[int, int]]] = {
        "Evidence": [(8, 28), (8, 72)],
        "Event": [(31, 24), (31, 70)],
        "Thesis": [(54, 47)],
        "Security": [(73, 28)],
        "Position": [(73, 70)],
        "Portfolio": [(91, 50)],
    }
    type_offsets: dict[str, int] = {}
    for node in ordered_nodes:
        node_type = node["type"]
        offset = type_offsets.get(node_type, 0)
        candidates = positions_by_type.get(node_type, [(50, 50)])
        position = candidates[min(offset, len(candidates) - 1)]
        node["position"] = {"x": position[0], "y": position[1]}
        type_offsets[node_type] = offset + 1

    thesis = entities[config["thesis"]]
    position = entities[config["position"]]
    security = entities[config["security"]]
    issuer = entities[config["issuer"]]
    positive = sum(1 for row in rel_rows if row["polarity"] > 0)
    negative = sum(1 for row in rel_rows if row["polarity"] < 0)

    return {
        "mode": "demo",
        "generated_at": utc_now_iso(),
        "assets": [
            {
                "security_id": asset_id,
                "label": next(e["label"] for e in _ENTITIES if e["id"] == asset_id),
                "canonical_key": next(e["key"] for e in _ENTITIES if e["id"] == asset_id),
            }
            for asset_id in _ASSET_CONFIG
        ],
        "selected_security_id": selected_id,
        "identity": {
            "issuer": issuer,
            "security": security,
            "aliases": [dict(row) for row in aliases],
        },
        "decision": {
            "verdict": config["verdict"],
            "tone": config["verdict_tone"],
            "change_summary": config["change_summary"],
            "recommended_action": config["recommended_action"],
            "recommended_action_type": config["action_type"],
            "recommended_reason": config["action_reason"],
            "thesis": thesis,
            "position": position,
            "supporting_paths": positive,
            "contradicting_paths": negative,
        },
        "graph": {
            "nodes": ordered_nodes,
            "edges": [
                {
                    "id": row["id"],
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "type": row["relation_type"],
                    "polarity": row["polarity"],
                    "confidence": row["confidence"],
                }
                for row in rel_rows
            ],
        },
        "actions": actions,
        "guardrails": [
            "演示环境 · 不连接券商",
            "动作只写审计台账",
            "所有结论必须可追溯到证据路径",
        ],
    }


def create_demo_action(
    request: OntologyDemoActionRequest,
    *,
    actor: str = "demo-user",
) -> OntologyDemoActionRecord:
    init_ontology_db()
    if request.security_id not in _ASSET_CONFIG:
        raise ValueError("未知演示证券对象")
    if request.action_type not in _ACTION_LABELS:
        raise ValueError("不支持的演示动作")
    action_id = f"oa_{uuid.uuid4().hex[:16]}"
    now = utc_now_iso()
    reason = request.reason.strip() or _ASSET_CONFIG[request.security_id]["action_reason"]
    status = "paper-recorded"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ontology_actions (
                id, security_id, action_type, status, actor, reason, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                request.security_id,
                request.action_type,
                status,
                actor or "demo-user",
                reason,
                json.dumps({"mode": "demo", "real_trade": False}, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    return OntologyDemoActionRecord(
        id=action_id,
        security_id=request.security_id,
        action_type=request.action_type,
        action_label=_ACTION_LABELS[request.action_type],
        status=status,
        actor=actor or "demo-user",
        reason=reason,
        created_at=now,
    )
