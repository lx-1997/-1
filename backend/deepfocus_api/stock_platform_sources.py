"""Request-scoped platform evidence, without PDF downloads or AI generation.

Capabilities come from verified HTTP claims, never the stock query. SQLite
connections are read-only and never run provider migrations. Shared caches are
source material, not authorization: entitlement is checked before opening them.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from . import db
from .schemas import RealtimeMessageRecord

SCAN_LIMIT = 1200
ITEM_LIMIT = 32
TEXT_LIMIT = 128_000
TOTAL_TEXT_LIMIT = 256_000
CHANNEL_TEXT_LIMIT = 64_000
REMOTE_TIMEOUT = 6.0
REMOTE_BUDGET = 12.0

_STORES = {
    "messages": (".realtime_messages.sqlite3", "DEEPFOCUS_REALTIME_MESSAGE_DB_PATH"),
    "wire": (".research_archive.sqlite3", "DEEPFOCUS_RESEARCH_ARCHIVE_DB_PATH"),
    "ai": (".metrics.sqlite3", "DEEPFOCUS_METRICS_DB_PATH"),
    "notes": (".zsxq_share.sqlite3", "DEEPFOCUS_ZSXQ_SHARE_DB_PATH"),
    "professional": (".professional_research.sqlite3", "DEEPFOCUS_PRO_RESEARCH_DB_PATH"),
    "tasks": (".agent_tasks.sqlite3", "DEEPFOCUS_AGENT_DB_PATH"),
    "memory": (".dulus_memory.sqlite3", "DULUS_MEMORY_DB_PATH"),
    "data": (".data_sources.sqlite3", "DEEPFOCUS_DATA_SOURCE_DB_PATH"),
}
_LABELS = {
    "site_fast_news": "平台快讯", "site_articles": "平台文章",
    "site_research": "平台本地研报", "recent_research": "投行研报已有解读",
    "institution_notes": "机构纪要", "celebrity_views": "名人观点",
    "professional_reports": "专业财报共享库", "owned_research": "我的已完成研究",
    "shared_sources": "已明确公开的资料库",
}


@dataclass(frozen=True)
class PlatformAccess:
    owner_id: str
    username: str
    is_member: bool = False
    wire_ai_allowed: bool = False
    celebrity_allowed: bool = False
    professional_allowed: bool = False


def _json(value: Any, fallback: Any = None) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback


def _mapping(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return value.model_dump() if hasattr(value, "model_dump") else {}


def _url(value: Any) -> str | None:
    value = str(value or "").strip()
    if not value or len(value) > 1500 or re.search(r"[\x00-\x20]", value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password:
            # Signed URLs must never become evidence links or shared identifiers.
            if re.search(r"(?:^|&)(?:token|access_token|cookie|signature|auth)=", parsed.query, re.I):
                return None
            return value
        if value.startswith("/") and not value.startswith("//") and not parsed.scheme:
            return value
    except ValueError:
        pass
    return None


def _symbol(value: Any, market: str) -> str:
    value = str(value or "").strip().upper()
    value = re.sub(r"\.(?:SH|SS|SZ|BJ|HK)$", "", value)
    if value.isdigit() and market == "HK":
        return value.zfill(5)
    return value


def _contains(text: str, term: str) -> bool:
    if not term:
        return False
    # Numeric and Latin identifiers require boundaries: 700 is not 1700,
    # CAT is not CATERPILLAR, and 600519 is not a phone-number substring.
    if re.fullmatch(r"[A-Za-z0-9.-]+", term):
        return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I))
    return term.casefold() in text.casefold()


def _rows(path: Path, sql: str, args: tuple = ()):
    if not path.is_file():
        return
    # Do not use db.connect: it creates directories and changes journal mode.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        for row in connection.execute(sql, args):
            yield dict(row)
    finally:
        connection.close()


class _Collector:
    def __init__(self, access: PlatformAccess, message_view: Callable | None, query: dict, paths: dict | None):
        self.access, self.message_view = access, message_view
        self.query = query
        self.market = str(query.get("market") or "CN").upper()
        self.symbol = _symbol(query.get("symbol"), self.market)
        self.name = str(query.get("name") or "").strip()[:80]
        self.aliases = [self.name] + [str(v).strip()[:80] for v in (query.get("aliases") or [])[:8] if isinstance(v, str)]
        self.industry = str(query.get("industry") or "").strip()[:80]
        try:
            self.window = max(1, min(int(query.get("window_days") or 365), 730))
        except (TypeError, ValueError):
            self.window = 365
        self.since = (datetime.now(timezone.utc) - timedelta(days=self.window)).isoformat()
        self.paths = {key: Path((paths or {}).get(key) or db.data_path(*value)) for key, value in _STORES.items()}
        self.channels: dict[str, dict] = {}
        self.read_chars = 0
        self.seen: set[str] = set()

    def channel(self, key: str, scope: str = "authenticated", *, denied: bool = False) -> dict:
        if key not in self.channels:
            self.channels[key] = {
                "channel": key, "label": _LABELS[key], "scope": scope,
                "status": "access_required" if denied else "empty", "items": [],
                "coverage": {"scanned": 0, "matched": 0, "returned": 0, "complete": not denied,
                             "window_days": self.window, "limit_hit": False, "read_chars": 0},
            }
        return self.channels[key]

    def partial(self, key: str, reason: str) -> None:
        channel = self.channels[key]
        channel["coverage"]["complete"] = False
        channel["coverage"]["reason"] = reason
        if channel["status"] != "access_required":
            channel["status"] = "partial"

    def match(self, title: str, text: str, symbol: Any = "") -> tuple[str, list[dict]] | None:
        hay = title + "\n" + text
        exact = _symbol(symbol, self.market) == self.symbol if symbol else False
        matched_code = _contains(hay, self.symbol)
        title_code = _contains(title, self.symbol)
        # HK news frequently uses 700; accept its unpadded representation with
        # strict digit boundaries, not arbitrary substring matching.
        if self.market == "HK" and self.symbol.isdigit():
            matched_code = matched_code or _contains(hay, str(int(self.symbol)))
            title_code = title_code or _contains(title, str(int(self.symbol)))
        matched_names = [alias for alias in self.aliases if len(alias) >= 2 and _contains(hay, alias)]
        # A report mentioning a ticker in its body may cover an entire sector
        # or several peers. Only an independently identified title subject or
        # the provider's primary symbol establishes company attribution.
        title_subject = any(
            re.search(rf"(?:^|[（(【\[：:\s]){re.escape(alias)}(?:[）)】\]：:\s]|$)", title, re.I)
            for alias in matched_names
        )
        if exact or matched_code or matched_names:
            basis = "symbol_exact" if exact else "title_symbol" if title_code else "title_company_subject" if title_subject else "visible_symbol" if matched_code else "visible_company_name"
            relations = [{"kind": "company", "symbol": self.symbol, "label": self.name or self.symbol,
                          "basis": basis, "verification": "verified" if exact or title_code or title_subject else "context"}]
            for kind, terms in (("upstream", ("上游", "供应商", "原材料")), ("downstream", ("下游", "客户", "经销商"))):
                term = next((term for term in terms if term in hay), None)
                if term:
                    pos = hay.find(term)
                    relations.append({"kind": kind, "label": term, "basis": hay[max(0, pos - 30):pos + 100],
                                      "verification": "context"})
            return basis, relations
        if len(self.industry) >= 2 and _contains(hay, self.industry):
            return "industry_context", [{"kind": "industry", "label": self.industry,
                                          "basis": "visible_industry_mention", "verification": "context"}]
        for peer in (self.query.get("peers") or [])[:8]:
            if not isinstance(peer, dict):
                continue
            code, name = str(peer.get("symbol") or ""), str(peer.get("name") or "")
            if _contains(hay, code) or len(name) >= 2 and _contains(hay, name):
                return "peer_context", [{"kind": "peer", "label": name or code, "symbol": code,
                                          "basis": "visible_peer_mention", "verification": "candidate"}]
        return None

    def add(self, key: str, row: dict, *, body_state: str = "full", role: str = "context", trusted_symbol: str = "") -> bool:
        channel = self.channels[key]
        title = str(row.get("title") or "").strip()[:500]
        text = str(row.get("visible_text") or "")
        match = self.match(title, text, trusted_symbol)
        if not match:
            return False
        channel["coverage"]["matched"] += 1
        raw_id = str(row.get("id") or "").strip()[:200]
        identifier = key + ":" + raw_id
        if not raw_id:
            return True
        if identifier in self.seen:
            existing = next(item for item in channel["items"] if item["id"] == identifier)
            rank = {"title_only": 0, "teaser": 1, "summary_only": 2, "excerpt": 3, "full": 4}
            old_rank, new_rank = rank[existing["body_state"]], rank.get(body_state, 0)
            if new_rank < old_rank or new_rank == old_rank and len(text) <= len(existing["visible_text"]):
                return True
            # A cold online fetch can upgrade a persisted 100-char note teaser.
            # It must not be discarded merely because the id was already seen.
            old_chars = len(existing["visible_text"])
            self.read_chars -= old_chars
            channel["coverage"]["read_chars"] -= old_chars
            channel["items"].remove(existing)
            self.seen.remove(identifier)
        if len(channel["items"]) >= ITEM_LIMIT:
            channel["coverage"]["limit_hit"] = True
            self.partial(key, "匹配资料超过单源输出上限，未宣称完整覆盖")
            return True
        remaining = min(TOTAL_TEXT_LIMIT - self.read_chars, CHANNEL_TEXT_LIMIT - channel["coverage"]["read_chars"], TEXT_LIMIT)
        if text and remaining <= 0:
            channel["coverage"]["limit_hit"] = True
            self.partial(key, "可见正文阅读预算已用完")
            return True
        truncated = len(text) > remaining or bool(row.get("text_truncated"))
        text = text[:max(remaining, 0)]
        if truncated:
            # Reading less of a teaser or cached AI interpretation cannot turn
            # it into a primary-source excerpt or grant directional weight.
            if body_state in {"full", "excerpt"}:
                body_state = "excerpt"
            channel["coverage"]["limit_hit"] = True
            self.partial(key, "可见正文受有界阅读预算限制，保留原文出处")
        # Preserve the collector's source classification separately from the
        # document-wide attribution. A clearly named company clause may later
        # be attributed without assigning the entire sector document to it.
        metadata = {"match_basis": match[0], "scope": channel["scope"], "text_truncated": truncated,
                    "source_role": role if role in {"fact", "opinion", "analysis", "context"} else "context"}
        direct = match[0] in {"symbol_exact", "title_symbol", "title_company_subject"}
        if not direct and role == "fact":
            role = "context"
        # Only collector-created numeric/citation metadata can cross the gate.
        for field in ("metrics", "report_id", "chunk_id", "page", "period", "cache_as_of", "original_body_state"):
            if field in row:
                metadata[field] = row[field]
        self.seen.add(identifier)
        channel["items"].append({
            "id": identifier, "title": title, "visible_text": text,
            "body_state": body_state if text else "title_only", "source_name": str(row.get("source_name") or channel["label"])[:120],
            "published_at": str(row.get("published_at") or "") or None, "url": _url(row.get("url")),
            "symbol": self.symbol if direct else "", "role": role,
            "entity_refs": [{"symbol": self.symbol, "name": self.name, "market": self.market}] if direct else [],
            "relation_refs": match[1], "metadata_safe": metadata,
        })
        self.read_chars += len(text)
        channel["coverage"]["read_chars"] += len(text)
        channel["coverage"]["returned"] = len(channel["items"])
        if channel["status"] != "partial":
            channel["status"] = "available"
        return True

    def messages(self, key: str, topic: str) -> None:
        channel = self.channel(key, "member_fulltext" if topic == "文章" and self.access.is_member else "authenticated_teaser" if topic == "文章" else "authenticated")
        if self.message_view is None:
            channel.update(status="access_required", scope="visibility_gate_required")
            channel["coverage"]["complete"] = False
            return
        if not self.paths["messages"].is_file():
            channel["status"] = "not_enabled"
            channel["coverage"].update(complete=False, reason="平台消息库尚未初始化")
            return
        rows = _rows(self.paths["messages"],
            "SELECT id,title,substr(content,1,?) AS content,length(content) AS content_length,source_id,source_name,source_type,symbol,topic,severity,url,tags_json,metadata_json,created_at "
            "FROM realtime_messages WHERE topic=? AND created_at>=? ORDER BY created_at DESC,id DESC LIMIT ?",
            (TEXT_LIMIT + 1, topic, self.since, SCAN_LIMIT + 1))
        processed = 0
        for raw in rows:
            processed += 1
            if processed > SCAN_LIMIT:
                channel["coverage"]["limit_hit"] = True
                self.partial(key, "独立时间窗扫描达到上限，未宣称全库覆盖")
                break
            if raw.get("source_type") == "upload":
                continue
            record = RealtimeMessageRecord(
                **{field: raw[field] for field in ("id", "title", "content", "source_id", "source_name", "source_type", "symbol", "topic", "severity", "url", "created_at")},
                tags=_json(raw.get("tags_json"), []) or [], metadata=_json(raw.get("metadata_json"), {}) or {},
            )
            # Authorization precedes matching, extraction, entity tagging and
            # counting. Never reuse record.metadata or article AI-cache text.
            viewed = self.message_view(record)
            if viewed is None:
                continue
            safe = _mapping(viewed)
            if not safe:
                continue
            channel["coverage"]["scanned"] += 1
            teaser = topic == "文章" and (not self.access.is_member or str(safe.get("content") or "") != record.content)
            self.add(key, {"id": safe.get("id"), "title": safe.get("title"), "visible_text": safe.get("content"),
                "source_name": safe.get("source_name"), "published_at": safe.get("created_at"), "url": safe.get("url"),
                "text_truncated": not teaser and raw.get("content_length", 0) > TEXT_LIMIT},
                body_state="teaser" if teaser else "full", role="analysis" if topic in {"文章", "研报"} else "fact",
                trusted_symbol="" if teaser else str(safe.get("symbol") or ""))
        if topic == "文章" and not self.access.is_member:
            self.partial(key, "当前账号仅可见会员文章导语；不读取正文、元数据正文或AI解读缓存")

    def cache(self, refs: list[str]) -> dict | None:
        if not self.access.wire_ai_allowed:
            return None
        from .metrics_store import _ai_cache_fresh
        for ref in dict.fromkeys(refs):
            for row in _rows(self.paths["ai"], "SELECT payload,created_at FROM ai_analysis_cache WHERE ref=?", (ref,)):
                if not _ai_cache_fresh(row["created_at"]):
                    continue
                payload = _json(row["payload"], {})
                if isinstance(payload, dict) and payload and not payload.get("vision_unavailable") and payload.get("provider") not in {"local-fallback", "text-only-fallback"}:
                    return {**payload, "_cache_as_of": row["created_at"]}
        return None

    def wire_rows(self, rows: list[dict]) -> None:
        key = "recent_research"
        channel = self.channels[key]
        from .research_multi_agent import analysis_cache_key
        for raw in rows[:SCAN_LIMIT]:
            fid = str(raw.get("file_id") or "")
            if not fid:
                continue
            cached = self.cache([analysis_cache_key(fid), fid])
            # Select only the handler's interpretation fields; provider/raw
            # source blobs, signed URLs and prompts remain private.
            lines = []
            if cached:
                for field in ("subject", "one_liner", "summary", "core_logic", "bullish", "bearish", "key_points", "risks", "logic_lines"):
                    value = cached.get(field)
                    if isinstance(value, str):
                        lines.append(value)
                    elif isinstance(value, list):
                        for item in value[:40]:
                            if isinstance(item, str):
                                lines.append(item)
                            elif isinstance(item, dict):
                                lines.extend(str(item[k]) for k in ("title", "logic", "evidence", "chain", "impact", "watch", "invalidation", "text") if isinstance(item.get(k), str))
            channel["coverage"]["scanned"] += 1
            self.add(key, {"id": fid, "title": raw.get("title"), "visible_text": "\n".join(lines),
                "source_name": raw.get("org") or "投行研报已有AI解读", "published_at": raw.get("date") or raw.get("created_at"),
                # A wire file id is not proof that a public research-blog page
                # exists; original PDF links have a separate access gate.
                "url": None, "cache_as_of": (cached or {}).get("_cache_as_of")},
                body_state="summary_only", role="analysis")

    def wire(self) -> None:
        self.channel("recent_research", "member_cached_interpretation", denied=not self.access.wire_ai_allowed)
        if not self.access.wire_ai_allowed:
            return
        rows = [row for row in _rows(self.paths["wire"], "SELECT payload FROM research_archive WHERE date>=? ORDER BY date DESC,id DESC LIMIT ?", (self.since[:10], SCAN_LIMIT))]
        self.wire_rows([value for row in rows if isinstance(value := _json(row.get("payload"), {}), dict)])
        self.partial("recent_research", "只读取已有解读及标题；不下载研报原文、不生成新解读，缓存并不等于原文")

    def notes_rows(self, rows: list[dict], state: str) -> None:
        channel = self.channels["institution_notes"]
        for row in rows[:SCAN_LIMIT]:
            dated = str(row.get("create_time") or row.get("date") or "")
            if dated and dated[:10] < self.since[:10]:
                continue
            channel["coverage"]["scanned"] += 1
            self.add("institution_notes", {"id": row.get("id"), "title": row.get("title"),
                "visible_text": row.get("text") if state == "full" else row.get("lead"), "source_name": "机构纪要",
                "published_at": dated, "url": "/note/" + quote(str(row.get("id") or ""), safe="")}, body_state=state, role="opinion")

    def notes(self) -> None:
        self.channel("institution_notes", "authenticated_public_stream")
        module = sys.modules.get(__package__ + ".zsxq_stream")
        cache = dict(getattr(module, "_CACHE", {}))
        allowed_groups = {group["id"] for group in module.stream_groups()} if module else set()
        for cache_key, value in list(cache.items())[:40]:
            if not isinstance(cache_key, tuple) or cache_key[0] not in allowed_groups:
                continue
            if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
                self.notes_rows(value[1].get("items") or [], "full")
        rows = list(_rows(self.paths["notes"], "SELECT id,title,lead,date FROM zsxq_share WHERE lead<>'' ORDER BY created_at DESC LIMIT ?", (SCAN_LIMIT,)))
        self.notes_rows(rows, "teaser")
        self.partial("institution_notes", "已缓存正文与公开短导语覆盖；短导语不充当全文，图片和未缓存页面未读取")

    def celebrities(self, payload: Any = None) -> None:
        key = "celebrity_views"
        channel = self.channel(key, "whitelisted", denied=not self.access.celebrity_allowed)
        if not self.access.celebrity_allowed:
            return
        if payload is None:
            module = sys.modules.get(__package__ + ".celebrity_views")
            cache = getattr(module, "_CACHE", {}).get("resp") or {}
            if module is None:
                self.partial(key, "当前进程尚无观点缓存，将尝试有界在线读取")
                return
            cfg = module.get_config()
            if not cfg.get("enabled", False):
                channel["status"] = "not_enabled"
                return
            payload = cache.get("resp") if cache.get("sig") == module._config_signature(cfg) else None
        for figure in (_mapping(payload).get("figures") or [])[:30]:
            figure = _mapping(figure)
            for item in (figure.get("items") or [])[:50]:
                row = _mapping(item)
                dated = str(row.get("published_at") or row.get("reported_date") or "")
                if dated and dated[:10] < self.since[:10]:
                    continue
                if "示例" in str(row.get("title") or "") + str(row.get("body") or ""):
                    continue
                channel["coverage"]["scanned"] += 1
                self.add(key, {"id": row.get("id"), "title": row.get("title"), "visible_text": row.get("body"),
                    "source_name": figure.get("name") or "名人观点", "published_at": row.get("published_at") or row.get("reported_date"),
                    "url": row.get("source_url")}, role="opinion")
        self.partial(key, "仅检索可见观点正文；图片、音频及未读取评论不冒充全文")

    def professional(self) -> None:
        key = "professional_reports"
        channel = self.channel(key, "shared_authenticated", denied=not self.access.professional_allowed)
        if not self.access.professional_allowed:
            return
        if not self.paths["professional"].is_file():
            channel["status"] = "not_enabled"
            channel["coverage"].update(complete=False, reason="专业财报共享库尚未初始化")
            return
        # Existing /api/pro-research routes expose this library to logged-in
        # users. It has no owner column: never describe it as personal uploads.
        symbols = [self.symbol]
        if self.market == "HK":
            symbols.append(self.symbol + ".HK")
        elif self.market == "CN":
            symbols.extend(self.symbol + suffix for suffix in (".SH", ".SZ", ".BJ", ".SS"))
        placeholders = ",".join("?" for _ in symbols)
        reports = list(_rows(self.paths["professional"], f"SELECT id,title,symbol,period,metadata_json,created_at,updated_at FROM professional_reports WHERE UPPER(symbol) IN ({placeholders}) ORDER BY created_at DESC LIMIT 9", tuple(symbols)))
        if len(reports) > 8:
            reports = reports[:8]
            self.partial(key, "精确标的财报超过最近8份扫描上限")
        for report in reports:
            channel["coverage"]["scanned"] += 1
            rid = report["id"]
            report_meta = _json(report.get("metadata_json"), {}) or {}
            published = next((report_meta[field] for field in ("published_at", "release_date") if isinstance(report_meta.get(field), str)), None)
            metrics = list(_rows(self.paths["professional"], "SELECT id,report_id,symbol,period,metric_key,metric_label,value,normalized_value,unit,raw_value,source_page,source_excerpt,confidence FROM professional_financial_metrics WHERE report_id=? ORDER BY confidence DESC LIMIT 41", (rid,)))
            metrics = [metric for metric in metrics if _symbol(metric.get("symbol"), self.market) in {"", self.symbol}]
            if len(metrics) > 40:
                metrics = metrics[:40]
                self.partial(key, "单份财报指标超过40项扫描上限")
            metric_text = "\n".join(f"{metric['metric_label']}：{metric['raw_value']} {metric.get('unit') or ''}；期间{metric.get('period') or '未标注'}；页码{metric.get('source_page')}；原文：{metric.get('source_excerpt') or ''}" for metric in metrics)
            self.add(key, {"id": rid, "title": report["title"], "visible_text": metric_text, "metrics": metrics,
                "report_id": rid, "period": report.get("period"), "source_name": "专业财报共享库·已提取指标",
                "published_at": published, "url": None}, role="fact", trusted_symbol=report["symbol"])
            chunks = list(_rows(self.paths["professional"], "SELECT id,chunk_index,page,title,substr(text,1,?) AS text,length(text) AS text_length FROM professional_report_chunks WHERE report_id=? ORDER BY chunk_index LIMIT 61", (TEXT_LIMIT + 1, rid)))
            if len(chunks) > 60:
                chunks = chunks[:60]
                self.partial(key, "单份财报超过60段扫描上限")
            for chunk in chunks:
                channel["coverage"]["scanned"] += 1
                self.add(key, {"id": rid + ":" + chunk["id"], "title": report["title"] + " · " + chunk["title"],
                    "visible_text": chunk["text"], "text_truncated": chunk["text_length"] > TEXT_LIMIT,
                    "report_id": rid, "chunk_id": chunk["id"], "page": chunk.get("page"), "period": report.get("period"),
                    "source_name": "专业财报共享库·原文分段", "published_at": published, "url": None}, role="fact", trusted_symbol=report["symbol"])

    def owned(self) -> None:
        key = "owned_research"
        channel = self.channel(key, "owner_only")
        # Even admins use this exact owner predicate; legacy NULL rows and
        # other accounts' titles/results are never read into this collector.
        rows = _rows(self.paths["tasks"], "SELECT id,title,symbol,result_json,completed_at FROM agent_tasks WHERE owner_user_id=? AND status='completed' AND task_type='investment_research' AND completed_at>=? ORDER BY completed_at DESC LIMIT ?", (self.access.owner_id, self.since, SCAN_LIMIT))
        for row in rows:
            channel["coverage"]["scanned"] += 1
            result = _json(row.get("result_json"), {})
            if not isinstance(result, dict):
                continue
            fields = ("answer", "summary", "investor_summary", "agent_findings", "conclusion", "analysis", "report", "thesis", "risks", "catalysts", "sections", "scenarios")
            safe = {field: result[field] for field in fields if field in result}
            self.add(key, {"id": row["id"], "title": row["title"], "visible_text": json.dumps(safe, ensure_ascii=False) if safe else "",
                "source_name": "本人已完成投研·历史AI分析", "published_at": row.get("completed_at"), "url": None},
                body_state="summary_only", role="analysis", trusted_symbol=row.get("symbol") or "")
        # Roundtable memories already contain only the owner's saved summary.
        rows = _rows(self.paths["memory"], "SELECT id,title,content,created_at FROM dulus_memory WHERE owner_user_id=? AND scope='user' AND source='roundtable' AND created_at>=? ORDER BY created_at DESC LIMIT ?", (self.access.owner_id, self.since, SCAN_LIMIT))
        for row in rows:
            channel["coverage"]["scanned"] += 1
            self.add(key, {"id": "memory:" + row["id"], "title": row["title"], "visible_text": row["content"],
                "source_name": "本人圆桌研究·历史摘要", "published_at": row["created_at"], "url": None}, body_state="summary_only", role="analysis")
        runtime = sys.modules.get(__package__ + ".research_jobs")
        manager = getattr(runtime, "manager", None)
        # Temporary calculation jobs have a separate explicit owner and expire
        # after 30 minutes. Inspect ownership before even accessing .result.
        for job in list(getattr(manager, "jobs", {}).values())[:20]:
            if job.owner != self.access.owner_id or job.state != "completed" or time.monotonic() - job.touched > 1800:
                continue
            result = job.result
            if not isinstance(result, dict):
                continue
            channel["coverage"]["scanned"] += 1
            safe = {field: result[field] for field in ("symbol", "symbols", "summary", "metrics", "tables", "warnings", "assumptions", "interpretation", "results", "statements") if field in result}
            self.add(key, {"id": "calculation:" + job.id, "title": "本人研究计算·" + job.tool,
                "visible_text": json.dumps(safe, ensure_ascii=False), "published_at": job.updated_at,
                "source_name": "本人已完成研究计算", "url": None}, body_state="summary_only", role="analysis",
                trusted_symbol=str(result.get("symbol") or ""))
        self.partial(key, "仅本人已完成任务与圆桌历史摘要；历史AI结论不充当新的事实来源")

    def shared(self) -> None:
        key = "shared_sources"
        channel = self.channel(key, "explicit_public_only")
        if not self.paths["data"].is_file():
            channel["status"] = "not_enabled"
            channel["coverage"].update(complete=False, reason="显式公开资料库尚未初始化")
            return
        # No owner exists on data_items. A public flag must be explicit and
        # source type must be a website/server ingestion; arbitrary uploads,
        # manual notes and agent crawls are never presumed globally readable.
        rows = _rows(self.paths["data"], "SELECT id,title,symbol,url,text,source_name,source_type,metadata_json,collected_at FROM data_items WHERE source_type IN ('web_page','server_api','market_api') AND json_valid(metadata_json) AND json_extract(metadata_json,'$.access_level')='public' AND collected_at>=? ORDER BY collected_at DESC LIMIT ?", (self.since, SCAN_LIMIT))
        for row in rows:
            record = RealtimeMessageRecord(id=row["id"], title=row["title"], content=row["text"], source_name=row["source_name"],
                source_type=row["source_type"], symbol=row.get("symbol"), topic="研报", severity="info", url=row.get("url"),
                metadata=_json(row.get("metadata_json"), {}) or {}, created_at=row.get("collected_at") or "")
            viewed = self.message_view(record) if self.message_view else None
            if viewed is None:
                continue
            safe = _mapping(viewed)
            source_meta = _json(row.get("metadata_json"), {}) or {}
            published = next((source_meta[field] for field in ("published_at", "reported_date", "publish_time") if isinstance(source_meta.get(field), str)), None)
            channel["coverage"]["scanned"] += 1
            self.add(key, {"id": safe.get("id"), "title": safe.get("title"), "visible_text": safe.get("content"),
                "source_name": safe.get("source_name"), "published_at": published, "url": safe.get("url")},
                trusted_symbol=str(safe.get("symbol") or ""))
        self.partial(key, "无owner的上传/手工/自主抓取资料未混入；只检索显式public的已同步资料")

    def local(self) -> None:
        if not self.access.owner_id or not self.access.username:
            for key in _LABELS:
                self.channel(key, "authenticated_required", denied=True)
            return
        operations = [("site_fast_news", lambda: self.messages("site_fast_news", "快讯")),
                      ("site_articles", lambda: self.messages("site_articles", "文章")),
                      ("site_research", lambda: self.messages("site_research", "研报")),
                      ("recent_research", self.wire), ("institution_notes", self.notes),
                      ("celebrity_views", self.celebrities), ("professional_reports", self.professional),
                      ("owned_research", self.owned), ("shared_sources", self.shared)]
        for key, operation in operations:
            try:
                operation()
            except (sqlite3.Error, ValueError, TypeError, KeyError, OSError):
                channel = self.channel(key)
                if channel["status"] != "access_required":
                    channel["status"] = "partial" if channel["items"] else "error"
                    channel["coverage"].update(complete=False, reason="资料库暂不可读或格式不完整，未扩大权限重试")

    async def remote(self) -> None:
        if not self.access.owner_id or not self.access.username:
            return
        # Separate ordinary stream (currently public) from whitelisted views.
        # Calls use existing metadata/text readers only; do not call main's
        # wire list handler, which can schedule PDF prewarming as a side effect.
        terms = [term for term in (self.name, self.symbol) if term][:2]
        async def notes() -> None:
            if any(item["body_state"] == "full" for item in self.channels["institution_notes"]["items"]) and not self.query.get("refresh"):
                return
            from .zsxq_stream import fetch_stream
            for term in terms:
                result = await asyncio.wait_for(fetch_stream(keyword=term, limit=40, use_cache=True), REMOTE_TIMEOUT)
                self.notes_rows(result.get("items") or [], "full")
        async def wire() -> None:
            if not self.access.wire_ai_allowed or self.channels["recent_research"]["items"] and not self.query.get("refresh"):
                return
            from .research_wire import fetch_research_wire_online
            for term in terms:
                result = await asyncio.wait_for(fetch_research_wire_online(limit=40, query=term), REMOTE_TIMEOUT)
                await asyncio.to_thread(self.wire_rows, result.get("items") or [])
        async def celebrity() -> None:
            if not self.access.celebrity_allowed or self.channels["celebrity_views"]["status"] == "not_enabled" or self.channels["celebrity_views"]["items"] and not self.query.get("refresh"):
                return
            from .celebrity_views import fetch_celebrity_views
            result = await asyncio.wait_for(fetch_celebrity_views(refresh=False), REMOTE_TIMEOUT)
            self.celebrities(result)
        # Sequential updates keep the shared per-request character budget exact.
        # All remote work has one bounded deadline, preserving usable local data.
        for key, operation in (("institution_notes", notes), ("recent_research", wire), ("celebrity_views", celebrity)):
            try:
                await asyncio.wait_for(operation(), REMOTE_TIMEOUT + 1)
            except (Exception, asyncio.TimeoutError):
                self.partial(key, "有界在线检索暂不可用，保留当前账号可见的本地资料；未读取PDF或调用AI")


def make_platform_loader(*, owner_id: str, username: str, is_member: bool = False,
                         wire_ai_allowed: bool = False, celebrity_allowed: bool = False,
                         professional_allowed: bool = False, message_view: Callable | None = None,
                         paths: dict | None = None, allow_remote: bool = True):
    """Bind verified capabilities once; query fields cannot grant permissions.

    ``paths`` and ``allow_remote=False`` support isolated synthetic-fixture
    tests, without opening production stores or fetching third-party content.
    """
    access = PlatformAccess(str(owner_id or "").strip(), str(username or "").strip(),
                            bool(is_member), bool(wire_ai_allowed), bool(celebrity_allowed), bool(professional_allowed))
    async def load(query: dict) -> list[dict]:
        collector = _Collector(access, message_view, dict(query), paths)
        await asyncio.to_thread(collector.local)
        if allow_remote:
            try:
                await asyncio.wait_for(collector.remote(), REMOTE_BUDGET)
            except asyncio.TimeoutError:
                for key, allowed in (("institution_notes", True), ("recent_research", access.wire_ai_allowed),
                                     ("celebrity_views", access.celebrity_allowed)):
                    if allowed and collector.channels[key]["status"] != "not_enabled":
                        collector.partial(key, "在线检索达到总时限，已返回当前账号可读的本地与已完成资料")
        # A cancelled to_thread cache read may finish after the HTTP deadline;
        # isolate its collector state from the immutable response snapshot.
        return copy.deepcopy(list(collector.channels.values()))
    return load
