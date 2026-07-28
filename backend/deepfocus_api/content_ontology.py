"""统一内容本体：把快讯、文章、研报和机构纪要变成可计算的内容对象。

与只有 ``["快讯"]`` 这类自由文本标签不同，本模块提供：

1. typed facets：内容类型、实体、事件、主题、方向、周期、来源；
2. canonical entity：同一公司/证券使用稳定 ID；
3. normalized links：内容—标签、内容—实体使用多对多表，而不是 JSON LIKE；
4. provenance：每条自动标注保留置信度、生成器和更新时间；
5. graph projection：为前端语义地图输出可交互的节点与关系。

第一版使用确定性规则，保证低延迟、可解释、可回归。后续可以把 LLM/embedding
作为新的 annotation source 写入同一协议，不需要改变前端和数据库关系。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .shared_utils import utc_now_iso


FACET_LABELS: dict[str, str] = {
    "content_type": "内容类型",
    "entity": "关联对象",
    "event": "事件类型",
    "theme": "研究主题",
    "signal": "影响方向",
    "horizon": "影响周期",
    "market": "市场",
    "source": "信息来源",
    "legacy": "原始标签",
}

FACET_COLORS: dict[str, str] = {
    "content_type": "#8b5cf6",
    "entity": "#22d3ee",
    "event": "#f59e0b",
    "theme": "#60a5fa",
    "signal": "#10b981",
    "horizon": "#a78bfa",
    "market": "#2dd4bf",
    "source": "#94a3b8",
    "legacy": "#64748b",
}

CONTENT_TYPE_LABELS: dict[str, str] = {
    "flash": "快讯",
    "article": "文章",
    "research": "研报",
    "institution_note": "机构纪要",
    "evidence": "证据资料",
}

EVENT_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("earnings", "业绩变化", ("财报", "业绩", "营收", "净利润", "毛利率", "eps", "盈利")),
    ("price_change", "价格变化", ("提价", "降价", "价格上调", "价格下调", "批价", "涨价")),
    ("policy", "政策监管", ("政策", "监管", "处罚", "关税", "制裁", "法案", "指导意见")),
    ("order", "订单中标", ("中标", "订单", "合同", "采购", "定点")),
    ("capital_action", "资本动作", ("回购", "增持", "减持", "定增", "并购", "收购", "分红")),
    ("supply_chain", "供应链变化", ("供应链", "库存", "产能", "交付", "原料", "渠道", "供给")),
    ("product", "产品与技术", ("发布", "新品", "技术", "专利", "研发", "模型", "量产")),
    ("management", "管理层变化", ("董事长", "总经理", "ceo", "高管", "辞任", "任命")),
    ("market_move", "市场异动", ("涨停", "跌停", "大涨", "大跌", "异动", "创新高", "成交额")),
    ("geopolitics", "地缘事件", ("战争", "冲突", "海峡", "停火", "外交", "地缘")),
]

THEME_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("ai", "人工智能", ("人工智能", "ai", "大模型", "算力", "gpu")),
    ("consumption", "消费", ("消费", "白酒", "食品饮料", "零售")),
    ("energy", "能源", ("原油", "天然气", "能源", "光伏", "储能")),
    ("metals", "有色与贵金属", ("黄金", "铜", "锂", "稀土", "有色")),
    ("healthcare", "医药医疗", ("医药", "医疗", "创新药", "医院")),
    ("finance", "金融", ("银行", "保险", "券商", "利率", "美债")),
    ("autos", "汽车与新能源车", ("汽车", "新能源车", "电池", "智驾")),
    ("semiconductor", "半导体", ("半导体", "芯片", "晶圆", "光刻")),
    ("globalization", "出海与全球化", ("出海", "海外", "出口", "全球份额", "本地化")),
    ("macro", "宏观经济", ("通胀", "降息", "加息", "汇率", "gdp", "就业")),
]

POSITIVE_TERMS = (
    "增长", "回购", "增持", "上调", "提价", "中标", "改善", "超预期",
    "创新高", "政策支持", "成本回落", "份额提升", "盈利企稳", "突破",
)
RISK_TERMS = (
    "风险", "承压", "减持", "下调", "处罚", "诉讼", "亏损", "低于预期",
    "下滑", "疲弱", "违约", "监管", "不确定", "库存上升", "成本上升",
)

HORIZON_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("intraday", "盘中", ("盘中", "今日", "涨停", "跌停", "异动")),
    ("short", "短期", ("短期", "本周", "近期", "下季度", "未来一个月")),
    ("medium", "中期", ("中期", "全年", "未来一年", "产能释放", "订单兑现")),
    ("long", "长期", ("长期", "未来三年", "战略", "格局", "壁垒")),
]

SOURCE_TIERS: list[tuple[str, str, tuple[str, ...]]] = [
    ("official", "官方/监管", ("交易所", "证监会", "国务院", "央行", "公司公告")),
    ("institution", "机构研究", ("证券", "投行", "研究所", "机构", "纪要")),
    ("media", "财经媒体", ("彭博", "路透", "财联社", "新华社", "央视", "dao财经")),
    ("community", "社区来源", ("雪球", "公众号", "知识星球", "社区")),
]


def _db_path() -> Path:
    return Path(
        os.getenv(
            "DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH",
            str(Path(__file__).resolve().parents[1] / ".content_ontology.sqlite3"),
        )
    )


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_content_ontology_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content_objects (
                id TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ontology_tags (
                id TEXT PRIMARY KEY,
                facet TEXT NOT NULL,
                code TEXT NOT NULL,
                label TEXT NOT NULL,
                color TEXT NOT NULL,
                parent_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(facet, code)
            );
            CREATE TABLE IF NOT EXISTS content_tag_links (
                content_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                confidence REAL NOT NULL,
                annotation_source TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(content_id, tag_id),
                FOREIGN KEY(content_id) REFERENCES content_objects(id) ON DELETE CASCADE,
                FOREIGN KEY(tag_id) REFERENCES ontology_tags(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ontology_entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                label TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_entity_links (
                content_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                role TEXT NOT NULL,
                confidence REAL NOT NULL,
                annotation_source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(content_id, entity_id, role),
                FOREIGN KEY(content_id) REFERENCES content_objects(id) ON DELETE CASCADE,
                FOREIGN KEY(entity_id) REFERENCES ontology_entities(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_content_type ON content_objects(content_type);
            CREATE INDEX IF NOT EXISTS idx_content_symbol ON content_objects(symbol);
            CREATE INDEX IF NOT EXISTS idx_tag_facet ON ontology_tags(facet);
            CREATE INDEX IF NOT EXISTS idx_tag_link_tag ON content_tag_links(tag_id);
            CREATE INDEX IF NOT EXISTS idx_entity_link_entity ON content_entity_links(entity_id);
            """
        )
        conn.commit()


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._:-]+", "-", value.strip().lower()).strip("-")
    if clean:
        return clean[:80]
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _tag(
    facet: str,
    code: str,
    label: str,
    confidence: float,
    *,
    source: str = "rules-v1",
) -> dict[str, Any]:
    normalized_code = _slug(code)
    return {
        "id": f"tag:{facet}:{normalized_code}",
        "facet": facet,
        "facet_label": FACET_LABELS.get(facet, facet),
        "code": normalized_code,
        "label": label[:60],
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 3),
        "annotation_source": source,
        "color": FACET_COLORS.get(facet, "#64748b"),
    }


def _matches(text: str, terms: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _match_rules(
    text: str,
    facet: str,
    rules: Iterable[tuple[str, str, tuple[str, ...]]],
    confidence: float,
    *,
    max_items: int = 3,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for code, label, terms in rules:
        hits = sum(1 for term in terms if term.casefold() in text.casefold())
        if hits:
            matches.append(_tag(facet, code, label, min(0.97, confidence + hits * 0.04)))
    return matches[:max_items]


def _entity_from_security(
    security_id: str,
    label: str,
    canonical_key: str,
    market: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "id": security_id,
        "type": "Security",
        "canonical_key": canonical_key,
        "label": label,
        "market": market,
        "role": "about",
        "confidence": round(confidence, 3),
        "annotation_source": "canonical-context",
    }


def annotate_content(
    *,
    content_id: str,
    content_type: str,
    title: str,
    text: str = "",
    source_name: str = "",
    symbol: str = "",
    url: str = "",
    published_at: str = "",
    legacy_tags: Optional[list[str]] = None,
    security_context: Optional[dict[str, str]] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """生成一条类型化多标签内容对象，并可选持久化规范关系。"""
    kind = content_type if content_type in CONTENT_TYPE_LABELS else "evidence"
    clean_title = re.sub(r"\s+", " ", str(title or "")).strip()[:240] or "未命名内容"
    clean_text = re.sub(r"\s+", " ", str(text or "")).strip()
    combined = f"{clean_title} {clean_text}"
    source = str(source_name or "").strip()
    sym = str(symbol or "").strip().upper()
    tags: list[dict[str, Any]] = [
        _tag("content_type", kind, CONTENT_TYPE_LABELS[kind], 1.0, source="system"),
    ]
    entities: list[dict[str, Any]] = []

    if security_context:
        security_id = str(security_context.get("security_id") or "").strip()
        label = str(security_context.get("label") or "").strip()
        canonical = str(security_context.get("canonical_key") or sym).strip()
        market = str(security_context.get("market") or "").strip().upper()
        aliases = [
            alias for alias in (
                label,
                canonical,
                canonical.split(".")[0] if canonical else "",
                sym,
            ) if alias
        ]
        if security_id and (_matches(combined, aliases) or sym == canonical or sym in aliases):
            entities.append(_entity_from_security(security_id, label or canonical, canonical, market, 0.98))
            tags.append(_tag("entity", security_id, label or canonical, 0.98, source="canonical-context"))
            if market:
                market_labels = {"CN": "A股", "HK": "港股", "US": "美股", "MULTI": "多市场"}
                tags.append(_tag("market", market, market_labels.get(market, market), 1.0, source="canonical-context"))

    if not entities and sym:
        market = "CN" if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ))?", sym) else ""
        security_id = f"security:{market.lower() or 'unknown'}:{sym}"
        entities.append(_entity_from_security(security_id, sym, sym, market, 0.82))
        tags.append(_tag("entity", security_id, sym, 0.82, source="symbol"))
        if market:
            tags.append(_tag("market", market, "A股", 0.9, source="symbol"))

    tags.extend(_match_rules(combined, "event", EVENT_RULES, 0.72))
    tags.extend(_match_rules(combined, "theme", THEME_RULES, 0.68))

    positive_hits = sum(1 for term in POSITIVE_TERMS if term.casefold() in combined.casefold())
    risk_hits = sum(1 for term in RISK_TERMS if term.casefold() in combined.casefold())
    if positive_hits and risk_hits:
        tags.append(_tag("signal", "mixed", "多空并存", min(0.92, 0.62 + 0.04 * (positive_hits + risk_hits))))
    elif positive_hits:
        tags.append(_tag("signal", "supportive", "支持论点", min(0.94, 0.66 + 0.05 * positive_hits)))
    elif risk_hits:
        tags.append(_tag("signal", "risk", "削弱论点", min(0.94, 0.66 + 0.05 * risk_hits)))
    else:
        tags.append(_tag("signal", "unconfirmed", "待确认", 0.58))

    horizon_tags = _match_rules(combined, "horizon", HORIZON_RULES, 0.66, max_items=2)
    tags.extend(horizon_tags or [_tag("horizon", "unassigned", "周期待定", 0.5)])

    source_text = f"{source} {combined[:120]}"
    source_tags = _match_rules(source_text, "source", SOURCE_TIERS, 0.74, max_items=1)
    tags.extend(source_tags or [_tag("source", "other", source or "其他来源", 0.55)])

    for raw in (legacy_tags or [])[:12]:
        value = str(raw).strip()
        if value and value not in CONTENT_TYPE_LABELS.values():
            tags.append(_tag("legacy", value, value, 0.6, source="upstream"))

    unique_tags: dict[str, dict[str, Any]] = {}
    for item in tags:
        previous = unique_tags.get(item["id"])
        if previous is None or item["confidence"] > previous["confidence"]:
            unique_tags[item["id"]] = item
    final_tags = list(unique_tags.values())

    summary = clean_text[:260] + ("…" if len(clean_text) > 260 else "")
    annotation = {
        "id": str(content_id),
        "content_type": kind,
        "content_type_label": CONTENT_TYPE_LABELS[kind],
        "title": clean_title,
        "summary": summary,
        "source_name": source,
        "symbol": sym,
        "url": str(url or ""),
        "published_at": str(published_at or ""),
        "tags": final_tags,
        "entities": entities,
        "tag_count": len(final_tags),
        "facet_count": len({item["facet"] for item in final_tags}),
        "annotation_quality": round(
            sum(float(item["confidence"]) for item in final_tags) / max(1, len(final_tags)),
            3,
        ),
    }
    if persist:
        persist_annotation(annotation)
    return annotation


def persist_annotation(annotation: dict[str, Any]) -> None:
    init_content_ontology_db()
    now = utc_now_iso()
    content_hash = hashlib.sha1(
        f"{annotation.get('title', '')}\n{annotation.get('summary', '')}".encode("utf-8")
    ).hexdigest()
    content_record = (
        annotation["id"],
        annotation["content_type"],
        annotation["title"],
        annotation.get("summary") or "",
        annotation.get("source_name") or "",
        annotation.get("symbol") or "",
        annotation.get("url") or "",
        annotation.get("published_at") or "",
        content_hash,
        json.dumps(
            {
                "tag_count": annotation.get("tag_count", 0),
                "facet_count": annotation.get("facet_count", 0),
                "annotation_quality": annotation.get("annotation_quality", 0),
            },
            ensure_ascii=False,
        ),
        now,
        now,
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO content_objects (
                id, content_type, title, summary, source_name, symbol, url,
                published_at, content_hash, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content_type=excluded.content_type,
                title=excluded.title,
                summary=excluded.summary,
                source_name=excluded.source_name,
                symbol=excluded.symbol,
                url=excluded.url,
                published_at=excluded.published_at,
                content_hash=excluded.content_hash,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            content_record,
        )
        for tag in annotation.get("tags") or []:
            conn.execute(
                """
                INSERT INTO ontology_tags (
                    id, facet, code, label, color, parent_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label, color=excluded.color, updated_at=excluded.updated_at
                """,
                (
                    tag["id"], tag["facet"], tag["code"], tag["label"],
                    tag["color"], now, now,
                ),
            )
            conn.execute(
                """
                INSERT INTO content_tag_links (
                    content_id, tag_id, confidence, annotation_source,
                    review_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'auto', ?, ?)
                ON CONFLICT(content_id, tag_id) DO UPDATE SET
                    confidence=excluded.confidence,
                    annotation_source=excluded.annotation_source,
                    updated_at=excluded.updated_at
                """,
                (
                    annotation["id"], tag["id"], tag["confidence"],
                    tag["annotation_source"], now, now,
                ),
            )
        for entity in annotation.get("entities") or []:
            conn.execute(
                """
                INSERT INTO ontology_entities (
                    id, entity_type, canonical_key, label, market, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    canonical_key=excluded.canonical_key,
                    label=excluded.label,
                    market=excluded.market,
                    updated_at=excluded.updated_at
                """,
                (
                    entity["id"], entity["type"], entity["canonical_key"],
                    entity["label"], entity.get("market") or "", now, now,
                ),
            )
            conn.execute(
                """
                INSERT INTO content_entity_links (
                    content_id, entity_id, role, confidence, annotation_source,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_id, entity_id, role) DO UPDATE SET
                    confidence=excluded.confidence,
                    annotation_source=excluded.annotation_source,
                    updated_at=excluded.updated_at
                """,
                (
                    annotation["id"], entity["id"], entity["role"],
                    entity["confidence"], entity["annotation_source"], now, now,
                ),
            )
        conn.commit()


def _content_type_from_topic(topic: str) -> str:
    normalized = str(topic or "").strip()
    return {
        "快讯": "flash",
        "文章": "article",
        "研报": "research",
        "机构纪要": "institution_note",
    }.get(normalized, "evidence")


def annotation_from_message(
    message: Any,
    *,
    security_context: Optional[dict[str, str]] = None,
    persist: bool = True,
) -> dict[str, Any]:
    def read(key: str, default: Any = "") -> Any:
        if isinstance(message, dict):
            return message.get(key, default)
        return getattr(message, key, default)

    return annotate_content(
        content_id=f"message:{read('id')}",
        content_type=_content_type_from_topic(read("topic")),
        title=read("title"),
        text=read("content"),
        source_name=read("source_name"),
        symbol=read("symbol"),
        url=read("url"),
        published_at=read("created_at"),
        legacy_tags=list(read("tags", []) or []),
        security_context=security_context,
        persist=persist,
    )


def build_content_map(
    *,
    security_context: dict[str, str],
    messages: list[Any],
    notes: Optional[list[dict[str, Any]]] = None,
    max_items: int = 48,
) -> dict[str, Any]:
    """把四类内容投影成前端可直接消费的语义图和标签矩阵。"""
    annotations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in messages[:max_items]:
        annotation = annotation_from_message(
            message,
            security_context=security_context,
            persist=True,
        )
        if annotation["id"] not in seen:
            annotations.append(annotation)
            seen.add(annotation["id"])
    for note in (notes or [])[:12]:
        note_id = f"note:{note.get('id')}"
        if note_id in seen:
            continue
        annotations.append(
            annotate_content(
                content_id=note_id,
                content_type="institution_note",
                title=str(note.get("title") or "机构纪要"),
                text=str(note.get("lead") or note.get("text") or ""),
                source_name="机构纪要",
                published_at=str(note.get("date") or ""),
                security_context=security_context,
                persist=True,
            )
        )
        seen.add(note_id)

    facet_counts: dict[str, Counter[str]] = defaultdict(Counter)
    tag_lookup: dict[str, dict[str, Any]] = {}
    content_type_counts: Counter[str] = Counter()
    for annotation in annotations:
        content_type_counts[annotation["content_type"]] += 1
        for tag in annotation["tags"]:
            facet_counts[tag["facet"]][tag["id"]] += 1
            tag_lookup[tag["id"]] = tag

    facets: list[dict[str, Any]] = []
    top_tag_ids: set[str] = set()
    facet_order = ("content_type", "entity", "event", "theme", "signal", "horizon", "source")
    for facet in facet_order:
        items = []
        for tag_id, count in facet_counts.get(facet, Counter()).most_common(8):
            tag = tag_lookup[tag_id]
            items.append({**tag, "count": count})
            top_tag_ids.add(tag_id)
        facets.append(
            {
                "facet": facet,
                "label": FACET_LABELS[facet],
                "color": FACET_COLORS[facet],
                "items": items,
                "coverage": sum(facet_counts.get(facet, Counter()).values()),
            }
        )

    asset_id = security_context["security_id"]
    graph_nodes: list[dict[str, Any]] = [
        {
            "id": asset_id,
            "kind": "asset",
            "label": security_context.get("label") or security_context.get("canonical_key"),
            "subtitle": security_context.get("canonical_key") or "",
            "facet": "entity",
            "color": FACET_COLORS["entity"],
            "weight": len(annotations),
        }
    ]
    graph_edges: list[dict[str, Any]] = []
    graph_content = annotations[:12]
    for annotation in graph_content:
        graph_nodes.append(
            {
                "id": annotation["id"],
                "kind": "content",
                "label": annotation["title"],
                "subtitle": annotation["content_type_label"],
                "facet": "content_type",
                "content_type": annotation["content_type"],
                "color": FACET_COLORS["content_type"],
                "weight": annotation["tag_count"],
            }
        )
        if annotation["entities"]:
            graph_edges.append(
                {
                    "id": f"edge:{annotation['id']}:asset",
                    "source": annotation["id"],
                    "target": asset_id,
                    "type": "ABOUT",
                    "label": "关于",
                    "confidence": max(item["confidence"] for item in annotation["entities"]),
                }
            )
        for tag in [
            item for item in annotation["tags"]
            if item["id"] in top_tag_ids and item["facet"] not in {"content_type", "entity"}
        ][:3]:
            graph_edges.append(
                {
                    "id": f"edge:{annotation['id']}:{tag['id']}",
                    "source": annotation["id"],
                    "target": tag["id"],
                    "type": "HAS_TAG",
                    "label": FACET_LABELS[tag["facet"]],
                    "confidence": tag["confidence"],
                }
            )

    used_tag_ids = {edge["target"] for edge in graph_edges if edge["type"] == "HAS_TAG"}
    for tag_id in used_tag_ids:
        tag = tag_lookup[tag_id]
        graph_nodes.append(
            {
                "id": tag_id,
                "kind": "tag",
                "label": tag["label"],
                "subtitle": FACET_LABELS[tag["facet"]],
                "facet": tag["facet"],
                "color": tag["color"],
                "weight": facet_counts[tag["facet"]][tag_id],
            }
        )

    tag_total = sum(len(annotation["tags"]) for annotation in annotations)
    matrix_coverage = sum(annotation["facet_count"] for annotation in annotations)
    return {
        "generated_at": utc_now_iso(),
        "security": security_context,
        "items": annotations,
        "facets": facets,
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "stats": {
            "content_count": len(annotations),
            "tag_count": tag_total,
            "unique_tag_count": len(tag_lookup),
            "relation_count": len(graph_edges),
            "avg_tags_per_content": round(tag_total / max(1, len(annotations)), 1),
            "avg_facets_per_content": round(matrix_coverage / max(1, len(annotations)), 1),
            "content_type_counts": dict(content_type_counts),
            "ontology_coverage": round(
                100
                * sum(1 for annotation in annotations if annotation["facet_count"] >= 5)
                / max(1, len(annotations))
            ),
        },
    }
