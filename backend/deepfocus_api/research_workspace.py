"""Private research projects and immutable evidence revisions in the Agent DB.

Queued refreshes are ordinary Agent tasks, committed with the run mapping in
the same transaction. No additional scheduler, process or credentials.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any

from . import db


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("authenticated owner required")
    return value.strip()


def connect():
    from .agent_runtime import DB_PATH
    connection = db.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def transaction():
    connection = connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_store(connection=None):
    own = connection is None
    connection = connection or connect()
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS rw_project(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
            symbol TEXT NOT NULL, market TEXT NOT NULL, objective TEXT NOT NULL,
            notes TEXT NOT NULL, hypotheses TEXT NOT NULL, version INTEGER NOT NULL,
            interval_seconds INTEGER NOT NULL DEFAULT 0, next_due REAL NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS rw_owner ON rw_project(owner,updated)")
        connection.execute("""CREATE TABLE IF NOT EXISTS rw_run(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner TEXT NOT NULL,
            request_key TEXT NOT NULL, config_version INTEGER NOT NULL,
            created REAL NOT NULL, UNIQUE(project_id,request_key))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS rw_revision(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE, config_version INTEGER NOT NULL,
            evidence_hash TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS rw_observation(
            task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner TEXT NOT NULL,
            evidence_hash TEXT NOT NULL, changed INTEGER NOT NULL, created REAL NOT NULL)""")
        if own:
            connection.commit()
    finally:
        if own:
            connection.close()


def _project(row) -> dict:
    result = dict(row)
    result["hypotheses"] = json.loads(result["hypotheses"])
    return result


def get_project(owner: str, project_id: str) -> dict:
    owner = _owner(owner)
    with transaction() as conn:
        row = conn.execute("SELECT * FROM rw_project WHERE id=? AND owner=?", (project_id, owner)).fetchone()
        if row is None:
            raise LookupError("project not found")
        result = _project(row)
        result["revisions"] = [dict(r, payload=json.loads(r["payload"])) for r in conn.execute(
            "SELECT * FROM rw_revision WHERE project_id=? AND owner=? ORDER BY created DESC,id DESC LIMIT 30", (project_id, owner))]
        result["runs"] = [dict(r) for r in conn.execute("""SELECT r.id,r.config_version,r.created,
            t.status,t.progress,t.error FROM rw_run r LEFT JOIN agent_tasks t ON t.id=r.id AND t.owner_user_id=r.owner
            WHERE r.project_id=? AND r.owner=? ORDER BY r.created DESC LIMIT 30""", (project_id, owner))]
        last = conn.execute("SELECT * FROM rw_observation WHERE project_id=? AND owner=? ORDER BY created DESC LIMIT 1", (project_id, owner)).fetchone()
        result["last_observation"] = dict(last) if last else None
        return result


def list_projects(owner: str) -> list[dict]:
    with transaction() as conn:
        return [_project(row) for row in conn.execute("SELECT * FROM rw_project WHERE owner=? ORDER BY updated DESC LIMIT 100", (_owner(owner),))]


def create_project(owner: str, *, title: str, symbol: str, market: str, objective: str,
                   notes: str = "", hypotheses: list[str] | None = None, interval_seconds: int = 0) -> dict:
    owner = _owner(owner)
    import re
    symbol, market = symbol.strip().upper(), market.strip().upper()
    valid = {"CN": r"\d{6}", "HK": r"\d{5}", "US": r"[A-Z][A-Z0-9.\-]{0,9}"}
    if market not in valid or not re.fullmatch(valid[market], symbol):
        raise ValueError("请提供准确的市场和股票代码（A股6位、港股5位、美股代码）")
    if not title.strip() or len(title) > 120 or not objective.strip() or len(objective) > 2000 or len(notes) > 8000:
        raise ValueError("项目标题、问题或笔记长度不合法")
    hypotheses = hypotheses or []
    if len(hypotheses) > 12 or any(not isinstance(h, str) or not h.strip() or len(h) > 500 for h in hypotheses):
        raise ValueError("最多12条待验证假设，每条最多500字")
    if interval_seconds not in {0, 86400, 604800}:
        raise ValueError("跟踪频率仅支持手动、每天或每周")
    key, now = uuid.uuid4().hex, time.time()
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM rw_project WHERE owner=?", (owner,)).fetchone()[0] >= 50:
            raise ValueError("最多保留50个研究项目")
        conn.execute("INSERT INTO rw_project VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (key, owner, title.strip(), symbol, market, objective.strip(), notes, _json(hypotheses), 1,
                      interval_seconds, now + interval_seconds if interval_seconds else 0, now, now))
    return get_project(owner, key)


def update_project(owner: str, project_id: str, *, expected_version: int, notes: str,
                   hypotheses: list[str], interval_seconds: int) -> dict:
    owner = _owner(owner)
    if len(notes) > 8000 or len(hypotheses) > 12 or any(not isinstance(h, str) or not h.strip() or len(h) > 500 for h in hypotheses):
        raise ValueError("笔记或假设长度不合法")
    if interval_seconds not in {0, 86400, 604800}:
        raise ValueError("跟踪频率不合法")
    now = time.time()
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM rw_project WHERE id=? AND owner=?", (project_id, owner)).fetchone()
        if current is None:
            raise LookupError("project not found")
        if current["version"] != expected_version:
            raise ValueError("项目已更新，请刷新后重试")
        conn.execute("UPDATE rw_project SET notes=?,hypotheses=?,interval_seconds=?,next_due=?,version=version+1,updated=? WHERE id=? AND owner=?",
                     (notes, _json(hypotheses), interval_seconds,
                      now + interval_seconds if interval_seconds else 0, now, project_id, owner))
    return get_project(owner, project_id)


def enqueue_refresh(owner: str, project_id: str, request_key: str, *, followup_question: str = "", scheduled=False, connection=None) -> str:
    """Atomically queue one existing-runtime task per explicit idempotency key."""
    owner = _owner(owner)
    if not request_key or len(request_key) > 100:
        raise ValueError("刷新请求标识不合法")
    if not isinstance(followup_question, str) or len(followup_question) > 2000:
        raise ValueError("追问最多2000字")
    followup_question = followup_question.strip()
    own = connection is None
    conn = connection or connect()
    try:
        if own:
            conn.execute("BEGIN IMMEDIATE")
        project = conn.execute("SELECT * FROM rw_project WHERE id=? AND owner=?", (project_id, owner)).fetchone()
        if not project:
            raise LookupError("project not found")
        existing = conn.execute("SELECT id FROM rw_run WHERE project_id=? AND owner=? AND request_key=?", (project_id, owner, request_key)).fetchone()
        if existing:
            original = conn.execute("SELECT input_json FROM agent_tasks WHERE id=? AND owner_user_id=?", (existing["id"], owner)).fetchone()
            if not original or (json.loads(original["input_json"]).get("engine_config") or {}).get("followup_question", "") != followup_question:
                raise ValueError("请求标识已被另一个问题使用，请刷新后重试")
            return existing["id"]
        active = conn.execute("""SELECT r.id FROM rw_run r JOIN agent_tasks t ON r.id=t.id
            WHERE r.project_id=? AND r.owner=? AND t.status IN ('pending','running','waiting_approval') LIMIT 1""", (project_id, owner)).fetchone()
        if active:
            if followup_question:
                raise ValueError("当前研究正在执行，请完成或取消后再追问")
            return active["id"]
        now = time.time()
        if conn.execute("SELECT COUNT(*) FROM rw_run WHERE owner=? AND created>?", (owner, now - 86400)).fetchone()[0] >= 100:
            raise ValueError("今日研究刷新预算已用完")
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        task_id = uuid.uuid4().hex
        payload = {"title": project["title"], "symbol": project["symbol"], "asset_name": project["symbol"],
                   "task_type": "watchlist_monitor", "engine": "deepfocus", "horizon": "研究项目设定期限",
                   "investor_profile": "专业", "objective": project["objective"], "context": "",
                   "engine_config": {"research_project_id": project_id, "config_version": project["version"], "scheduled": scheduled, "followup_question": followup_question}, "priority": 4}
        conn.execute("""INSERT INTO agent_tasks(id,owner_user_id,title,symbol,asset_name,task_type,engine,status,
            priority,assigned_agent,progress,input_json,logs_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,'pending',4,'ResearchWorkspace',0,?,'[]',?,?)""",
            (task_id, owner, project["title"], project["symbol"], project["symbol"], "watchlist_monitor", "deepfocus", _json(payload), stamp, stamp))
        conn.execute("INSERT INTO rw_run VALUES(?,?,?,?,?,?)", (task_id, project_id, owner, request_key, project["version"], now))
        if scheduled:
            conn.execute("UPDATE rw_project SET next_due=? WHERE id=? AND owner=?", (now + project["interval_seconds"], project_id, owner))
        if own:
            conn.commit()
        return task_id
    except BaseException:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def enqueue_due() -> int:
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        due = conn.execute("SELECT id,owner,next_due FROM rw_project WHERE interval_seconds>0 AND next_due<=? ORDER BY next_due LIMIT 1", (time.time(),)).fetchone()
        if not due:
            return 0
        try:
            enqueue_refresh(due["owner"], due["id"], f"schedule:{int(due['next_due'])}", scheduled=True, connection=conn)
        except ValueError:
            # A user's exhausted budget cannot starve every other user's due job.
            conn.execute("UPDATE rw_project SET next_due=? WHERE id=? AND owner=?", (time.time() + 3600, due["id"], due["owner"]))
            return 0
        conn.execute("UPDATE rw_project SET next_due=?+interval_seconds WHERE id=? AND owner=?", (time.time(), due["id"], due["owner"]))
        return 1


def evidence_state(packet: dict) -> dict:
    """Stable facts and limitations: exclude fetch clock, ordering and prose."""
    state = {}
    for check in (packet.get("evidence_audit") or {}).get("checks", []):
        state[check["tool"]] = {key: check.get(key) for key in (
            "status", "symbol", "currency", "source", "pe_basis", "report_date", "period_basis", "published_date", "fields", "issues")}
    # Content is a change signal, not an automatically verified company fact.
    # Preserve source links and dates; ignore retrieval clocks and ordering.
    signals = []
    for trace in packet.get("traces") or []:
        for ref in trace.get("references") or []:
            if not isinstance(ref, dict):
                continue
            signals.append({key: ref.get(key) for key in ("title", "url", "source", "published_at", "category")})
    if signals:
        state["content_signals"] = {"context_only": True, "items": sorted(
            {store_key: row for row in signals for store_key in [_json(row)]}.values(), key=_json)[:60]}
    return state


def finalize_refresh(owner: str, project_id: str, task_id: str, lease: str, config_version: int,
                     state: dict, report: dict) -> bool:
    """Publish revision + task completion atomically, guarded by the worker lease."""
    owner = _owner(owner)
    digest = hashlib.sha256(_json(state).encode()).hexdigest()
    now = time.time()
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        task = conn.execute("""SELECT t.id FROM agent_tasks t JOIN rw_run r ON r.id=t.id
            JOIN rw_project p ON p.id=r.project_id AND p.owner=r.owner
            WHERE t.id=? AND t.owner_user_id=? AND t.status='running' AND t.lease_token=?
            AND r.owner=t.owner_user_id AND t.lease_expires_at>?
            AND r.project_id=? AND r.config_version=? AND p.version=?""",
            (task_id, owner, lease, stamp, project_id, config_version, config_version)).fetchone()
        if not task:
            return False
        latest = conn.execute("SELECT evidence_hash,payload,config_version FROM rw_revision WHERE project_id=? AND owner=? ORDER BY created DESC,id DESC LIMIT 1", (project_id, owner)).fetchone()
        old_payload = json.loads(latest["payload"]) if latest else {}
        changed = latest is None or latest["evidence_hash"] != digest or latest["config_version"] != config_version or bool(report.get("followup_question")) or (
            report.get("analysis_status") == "generated" and old_payload.get("report", {}).get("analysis_status") != "generated")
        payload = {"evidence": state, "report": report}
        if changed:
            project = conn.execute("SELECT objective,notes,hypotheses FROM rw_project WHERE id=? AND owner=?", (project_id, owner)).fetchone()
            payload["project_context"] = {"objective": project["objective"], "notes": project["notes"], "hypotheses": json.loads(project["hypotheses"])}
            old = json.loads(latest["payload"])["evidence"] if latest else {}
            payload["changed_modules"] = [key for key in sorted(set(state) | set(old)) if state.get(key) != old.get(key)]
            changes = []
            for module in payload["changed_modules"]:
                previous_fields = (old.get(module) or {}).get("fields", {})
                current_fields = (state.get(module) or {}).get("fields", {})
                for field in sorted(set(previous_fields) | set(current_fields)):
                    before, after = previous_fields.get(field), current_fields.get(field)
                    if before != after:
                        changes.append({"tool": module, "field": field, "before": before, "after": after})
            payload["changes"] = changes
            conn.execute("INSERT INTO rw_revision VALUES(?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, project_id, owner, task_id, config_version, digest, _json(payload), now))
        conn.execute("INSERT INTO rw_observation VALUES(?,?,?,?,?,?)", (task_id, project_id, owner, digest, int(changed), now))
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        conn.execute("UPDATE agent_tasks SET status='completed',progress=100,result_json=?,completed_at=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND owner_user_id=? AND lease_token=?",
                     (_json({"project_id": project_id, "changed": changed, "evidence_hash": digest, **report}), stamp, stamp, task_id, owner, lease))
    return True
