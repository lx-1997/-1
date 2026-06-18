"""轻量站点指标：页面访问次数 + 研报下载/打开次数（SQLite 持久化）。

仅做计数与汇总，无 PII。被 main.py 的 /api/metrics/* 与研报原文端点调用。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_METRICS_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".metrics.sqlite3"),
    )
)


_PRAGMA_DONE = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    # WAL：并发读不阻塞写、写不阻塞读，几十并发下避免「database is locked」与互锁停顿
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn


def get_ai_cache_many(refs: list[str]) -> dict[str, dict[str, Any]]:
    """批量取多篇 AI 解读缓存（一次 SQL，避免逐条查询在并发下压垮 SQLite）。"""
    clean = [str(r).strip() for r in refs if str(r or "").strip()]
    if not clean:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        import json as _json
        with _connect() as conn:
            # 分批 IN 查询，避免参数过多
            for i in range(0, len(clean), 400):
                chunk = clean[i:i + 400]
                ph = ",".join("?" * len(chunk))
                rows = conn.execute(f"SELECT ref, payload FROM ai_analysis_cache WHERE ref IN ({ph})", chunk).fetchall()
                for r in rows:
                    try:
                        out[r["ref"]] = _json.loads(r["payload"])
                    except Exception:  # noqa: BLE001
                        pass
    except sqlite3.Error:
        pass
    return out


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_counters (
                key TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_daily (
                day TEXT NOT NULL,
                key TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_downloads (
                ref TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_analysis_cache (
                ref TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_analyze_refs (
                ref TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE TABLE IF NOT EXISTS metric_hourly (hour INTEGER PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_heat (
                ref TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # 操作流水：记录每个账号/匿名访客做了什么（看研报/AI解读/复制/下载/搜索/切板块/登录/访问）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT '',
                ip TEXT NOT NULL DEFAULT '',
                device TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity_log(actor_id)")
        conn.commit()


ACTIVITY_MAX = int(os.getenv("DEEPFOCUS_ACTIVITY_MAX", "100000"))  # 保留最近多少条操作流水


def log_activity(*, actor_kind: str, actor_id: str, actor_name: str, action: str,
                 target: str = "", ip: str = "", device: str = "") -> None:
    """记录一条操作流水。失败绝不影响主流程。"""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO activity_log (ts, actor_kind, actor_id, actor_name, action, target, ip, device)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (_now_iso(), actor_kind[:12], actor_id[:80], actor_name[:80],
                 action[:40], target[:200], ip[:64], device[:12]),
            )
            # 超量裁剪（按 id 保留最近 ACTIVITY_MAX 条）
            conn.execute(
                "DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT ?)",
                (ACTIVITY_MAX,),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def recent_activity(limit: int = 200, action: str = "", actor_id: str = "") -> list[dict[str, Any]]:
    clauses, vals = [], []
    if action:
        clauses.append("action = ?"); vals.append(action)
    if actor_id:
        clauses.append("actor_id = ?"); vals.append(actor_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    vals.append(max(1, min(limit, 1000)))
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT ts, actor_kind, actor_id, actor_name, action, target, ip, device"
                f" FROM activity_log {where} ORDER BY id DESC LIMIT ?",
                vals,
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def activity_actor_summary(limit: int = 60) -> list[dict[str, Any]]:
    """按账号/访客聚合：操作次数、最近活跃、身份。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT actor_id,
                       MAX(actor_name) AS actor_name,
                       MAX(actor_kind) AS actor_kind,
                       COUNT(*)        AS actions,
                       MAX(ts)         AS last_seen,
                       MIN(ts)         AS first_seen
                FROM activity_log
                GROUP BY actor_id
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def activity_stats() -> dict[str, Any]:
    """操作流水概览：总条数、今日条数、独立账号/访客数、各动作计数。"""
    out: dict[str, Any] = {"total": 0, "today": 0, "actors": 0, "users": 0, "by_action": {}}
    try:
        with _connect() as conn:
            out["total"] = int(conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
            out["today"] = int(conn.execute("SELECT COUNT(*) FROM activity_log WHERE substr(ts,1,10)=?", (_today(),)).fetchone()[0])
            out["actors"] = int(conn.execute("SELECT COUNT(DISTINCT actor_id) FROM activity_log").fetchone()[0])
            out["users"] = int(conn.execute("SELECT COUNT(DISTINCT actor_id) FROM activity_log WHERE actor_kind='user'").fetchone()[0])
            out["by_action"] = {r["action"]: r["c"] for r in conn.execute("SELECT action, COUNT(*) c FROM activity_log GROUP BY action ORDER BY c DESC").fetchall()}
    except sqlite3.Error:
        pass
    return out


def member_activity_windows() -> dict[str, dict[str, Any]]:
    """按登录账号聚合操作量：今日 / 近 7 日 / 累计 + 最近活跃，键为 actor_id（形如 ``u:<id>``）。

    今日按北京时区自然日切（与 account_stats 口径一致）。一条分组 SQL 完成，
    供运营看板「会员模块」按账号匹配会员的操作数据。失败返回空 dict、绝不抛。"""
    out: dict[str, dict[str, Any]] = {}
    try:
        bj = timezone(timedelta(hours=8))
        now = datetime.now(timezone.utc)
        today_cut = (
            now.astimezone(bj).replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(timezone.utc).isoformat()
        )
        week_cut = (now - timedelta(days=7)).isoformat()
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT actor_id,
                       SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS today,
                       SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS week,
                       COUNT(*) AS total,
                       MAX(ts)  AS last_seen
                FROM activity_log
                WHERE actor_kind = 'user'
                GROUP BY actor_id
                """,
                (today_cut, week_cut),
            ).fetchall()
            for r in rows:
                out[r["actor_id"]] = {
                    "today": int(r["today"] or 0),
                    "week": int(r["week"] or 0),
                    "total": int(r["total"] or 0),
                    "last_seen": r["last_seen"] or "",
                }
    except sqlite3.Error:
        pass
    return out


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def incr(key: str, by: int = 1) -> int:
    """累加总计数器并同步当日计数，返回累计总数。"""
    now, day = _now_iso(), _today()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO metric_counters (key, count, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET count = count + ?, updated_at = ?
                """,
                (key, by, now, by, now),
            )
            conn.execute(
                """
                INSERT INTO metric_daily (day, key, count) VALUES (?, ?, ?)
                ON CONFLICT(day, key) DO UPDATE SET count = count + ?
                """,
                (day, key, by, by),
            )
            row = conn.execute("SELECT count FROM metric_counters WHERE key = ?", (key,)).fetchone()
            conn.commit()
            return int(row["count"]) if row else by
    except sqlite3.Error:
        return 0


def incr_research(ref: str, title: str = "", by: int = 1) -> None:
    """累加单篇研报的打开/下载次数，并累加研报总计数。"""
    ref = (ref or "").strip()
    if not ref:
        return
    now = _now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO research_downloads (ref, title, count, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET count = count + ?, updated_at = ?,
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE research_downloads.title END
                """,
                (ref, title[:200], by, now, by, now),
            )
            conn.commit()
    except sqlite3.Error:
        pass
    incr("research_download", by)


def get_ai_cache(ref: str) -> dict[str, Any] | None:
    """取研报 AI 解读缓存（研报 PDF 静态，按 file_id/文件名长期缓存，命中即秒回）。"""
    import json as _json
    ref = (ref or "").strip()
    if not ref:
        return None
    try:
        with _connect() as conn:
            row = conn.execute("SELECT payload FROM ai_analysis_cache WHERE ref = ?", (ref,)).fetchone()
            if row:
                return _json.loads(row["payload"])
    except (sqlite3.Error, ValueError):
        pass
    return None


def set_ai_cache(ref: str, payload: dict[str, Any]) -> None:
    import json as _json
    ref = (ref or "").strip()
    if not ref:
        return
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_analysis_cache (ref, payload, created_at) VALUES (?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET payload = excluded.payload, created_at = excluded.created_at
                """,
                (ref, _json.dumps(payload, ensure_ascii=False), _now_iso()),
            )
            conn.commit()
    except (sqlite3.Error, TypeError, ValueError):
        pass


def prune_ai_cache(max_age_days: int = 90) -> int:
    """清理超过 max_age_days 天的 AI 解读缓存（按 created_at）。老研报/文章几乎不会再被读，
    删后即使再点也只是重新解析一次，无损正确性。返回删除条数。"""
    if max_age_days <= 0:
        return 0
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM ai_analysis_cache WHERE created_at < datetime('now', ?)",
                (f"-{int(max_age_days)} days",),
            )
            conn.commit()
            return cur.rowcount or 0
    except sqlite3.Error:
        return 0


def incr_ai_ref(ref: str, title: str = "", by: int = 1) -> None:
    """累加单篇研报的 AI 解读次数（用于 AI 解读榜）。"""
    ref = (ref or "").strip()
    if not ref:
        return
    now = _now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_analyze_refs (ref, title, count, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET count = count + ?, updated_at = ?,
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE ai_analyze_refs.title END
                """,
                (ref, title[:200], by, now, by, now),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def incr_hourly(hour: int) -> None:
    """按小时累加访问（时段活跃分布，0-23）。"""
    try:
        h = int(hour) % 24
        with _connect() as conn:
            conn.execute(
                "INSERT INTO metric_hourly (hour, count) VALUES (?, 1) ON CONFLICT(hour) DO UPDATE SET count = count + 1",
                (h,),
            )
            conn.commit()
    except (sqlite3.Error, ValueError, TypeError):
        pass


def incr_news_heat(ref: str, title: str = "", by: int = 1) -> None:
    """单条快讯/文章热度（复制 + AI 解读累加）。"""
    ref = (ref or "").strip()
    if not ref:
        return
    now = _now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO news_heat (ref, title, count, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET count = count + ?, updated_at = ?,
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE news_heat.title END
                """,
                (ref, title[:200], by, now, by, now),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def get_counter(key: str) -> int:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT count FROM metric_counters WHERE key = ?", (key,)).fetchone()
            return int(row["count"]) if row else 0
    except sqlite3.Error:
        return 0


def get_daily(key: str, day: str | None = None) -> int:
    """读取某 key 在指定自然日（默认今天）的计数；用于「每日下载预算」等限额。"""
    target = day or _today()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT count FROM metric_daily WHERE day = ? AND key = ?", (target, key)
            ).fetchone()
            return int(row["count"]) if row else 0
    except sqlite3.Error:
        return 0


def summary(top: int = 10, days: int = 14) -> dict[str, Any]:
    """汇总：总访问/研报下载、今日访问、近 N 日趋势、下载榜。"""
    out: dict[str, Any] = {
        "pageviews": 0, "pageviews_today": 0,
        "research_downloads": 0,
        "daily": [], "top_reports": [],
        "generated_at": _now_iso(),
    }
    try:
        with _connect() as conn:
            counters = {r["key"]: int(r["count"]) for r in conn.execute("SELECT key, count FROM metric_counters")}
            out["pageviews"] = counters.get("pageview", 0)
            out["research_downloads"] = counters.get("research_download", 0)
            out["ai_research"] = counters.get("ai_research", 0)
            out["ai_news"] = counters.get("ai_news", 0)
            out["ai_total"] = out["ai_research"] + out["ai_news"]
            out["copy_text"] = counters.get("copy_text", 0)
            out["copy_image"] = counters.get("copy_image", 0)
            out["copy_news"] = counters.get("copy_news", 0)
            out["copy_total"] = out["copy_text"] + out["copy_image"] + out["copy_news"]
            out["brief"] = counters.get("brief", 0)
            out["interactions"] = out["ai_total"] + out["copy_total"] + out["research_downloads"]
            out["counters"] = counters
            today = _today()
            yday = (datetime.now(timezone.utc).astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")
            # 近 N 日多指标趋势（访问/AI/复制/下载）
            byday: dict[str, dict[str, int]] = {}
            for r in conn.execute("SELECT day, key, count FROM metric_daily ORDER BY day DESC LIMIT ?", (max(1, days) * 8,)):
                d = byday.setdefault(r["day"], {})
                d[r["key"]] = int(r["count"])
            def day_metric(d: dict[str, int], *keys: str) -> int:
                return sum(d.get(k, 0) for k in keys)
            trend = []
            for day in sorted(byday.keys(), reverse=True)[:days]:
                d = byday[day]
                trend.append({
                    "day": day,
                    "pageview": d.get("pageview", 0),
                    "ai": day_metric(d, "ai_research", "ai_news"),
                    "copy": day_metric(d, "copy_text", "copy_image", "copy_news"),
                    "download": d.get("research_download", 0),
                })
            trend.reverse()  # 旧→新
            out["trend"] = trend
            out["pageviews_today"] = byday.get(today, {}).get("pageview", 0)
            out["pageviews_yesterday"] = byday.get(yday, {}).get("pageview", 0)
            out["top_reports"] = [
                {"ref": r["ref"], "title": r["title"], "count": int(r["count"])}
                for r in conn.execute(
                    "SELECT ref, title, count FROM research_downloads ORDER BY count DESC LIMIT ?", (max(1, top),),
                )
            ]
            out["top_ai"] = [
                {"ref": r["ref"], "title": r["title"], "count": int(r["count"])}
                for r in conn.execute(
                    "SELECT ref, title, count FROM ai_analyze_refs ORDER BY count DESC LIMIT ?", (max(1, top),),
                )
            ]
            out["top_news"] = [
                {"ref": r["ref"], "title": r["title"], "count": int(r["count"])}
                for r in conn.execute(
                    "SELECT ref, title, count FROM news_heat ORDER BY count DESC LIMIT ?", (max(1, top),),
                )
            ]
            hours = {int(r["hour"]): int(r["count"]) for r in conn.execute("SELECT hour, count FROM metric_hourly")}
            out["hourly"] = [{"h": h, "count": hours.get(h, 0)} for h in range(24)]
            mob = counters.get("pv_mobile", 0); pc = counters.get("pv_desktop", 0)
            out["device"] = {"mobile": mob, "desktop": pc, "total": mob + pc}
    except sqlite3.Error:
        pass
    return out
