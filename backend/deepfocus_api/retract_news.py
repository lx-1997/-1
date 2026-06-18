"""撤回已发布的违规快讯·文章：从 realtime_messages 库删除命中屏蔽词(斧头 / futou)的消息。

撤回口径与「going-forward 过滤」完全一致——都用 news_filter.block_reason，避免两套标准。
默认 **dry-run 只预览**；加 --apply 才真正删除，且删除前自动备份整个 DB 文件。

在生产服务器 backend 目录(已 source 进 venv、与线上同环境变量)运行：
    python -m deepfocus_api.retract_news                 # 预览全部命中
    python -m deepfocus_api.retract_news --days 3        # 只看最近 3 天
    python -m deepfocus_api.retract_news --apply         # 执行删除(先自动备份)
    python -m deepfocus_api.retract_news --apply --days 3

说明：删除后，前端刷新即不再显示这些消息(已推送到在线客户端的旧条目会在下次加载时消失)。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

from .news_filter import block_reason, scrub
from .realtime_messages import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=8000")
    except sqlite3.Error:
        pass
    return conn


def find_blocked(days: int | None = None) -> list[dict]:
    """扫描消息，返回命中屏蔽词的条目 [{id, created_at, topic, title, reason}]（新→旧）。"""
    clauses, values = [], []
    if days and days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        clauses.append("created_at > ?")
        values.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, title, content, topic, created_at FROM realtime_messages {where} "
            f"ORDER BY created_at DESC",
            values,
        ).fetchall()
    hits = []
    for r in rows:
        reason = block_reason(r["title"] or "", r["content"] or "")
        if reason:
            hits.append({
                "id": r["id"], "created_at": r["created_at"], "topic": r["topic"],
                "title": (r["title"] or "")[:80], "reason": reason,
            })
    return hits


def find_scrubbable(days: int | None = None) -> list[dict]:
    """命中竞品域名/品牌但非广告的条目（应抹域名保留，而非删除）。返回带 scrub 预览。"""
    clauses, values = [], []
    if days and days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        clauses.append("created_at > ?")
        values.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, title, content, url, topic, created_at FROM realtime_messages {where} "
            f"ORDER BY created_at DESC",
            values,
        ).fetchall()
    out = []
    for r in rows:
        if block_reason(r["title"] or "", r["content"] or ""):
            continue  # 广告归 find_blocked 删除，不在此抹
        nt, nc, nu, changed = scrub(r["title"] or "", r["content"] or "", r["url"] or "")
        if changed:
            out.append({"id": r["id"], "created_at": r["created_at"], "topic": r["topic"],
                        "title": (r["title"] or "")[:80], "nt": nt, "nc": nc, "nu": nu})
    return out


def scrub_existing(items: list[dict]) -> int:
    """对已入库条目就地抹竞品域名（保留文章）。返回更新行数。"""
    if not items:
        return 0
    with _connect() as conn:
        n = 0
        for it in items:
            cur = conn.execute(
                "UPDATE realtime_messages SET title=?, content=?, url=? WHERE id=?",
                (it["nt"][:240], it["nc"][:8000], it["nu"], it["id"]),
            )
            n += cur.rowcount or 0
        conn.commit()
    return n


def _backup_db() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{DB_PATH}.bak-{stamp}"
    shutil.copy2(DB_PATH, dst)
    return dst


def retract(ids: list[str]) -> int:
    """按 id 删除，返回实际删除行数。"""
    if not ids:
        return 0
    with _connect() as conn:
        deleted = 0
        for i in range(0, len(ids), 500):  # 分批，避免 SQL 变量上限
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"DELETE FROM realtime_messages WHERE id IN ({placeholders})", chunk
            )
            deleted += cur.rowcount or 0
        conn.commit()
    return deleted


def main() -> None:
    ap = argparse.ArgumentParser(description="撤回含屏蔽词的快讯/文章(默认仅预览)")
    ap.add_argument("--apply", action="store_true", help="真正删除(默认只预览)")
    ap.add_argument("--days", type=int, default=None, help="只处理最近 N 天(默认全部)")
    args = ap.parse_args()

    print(f"[retract] DB = {DB_PATH}")
    hits = find_blocked(args.days)       # 广告 → 删除
    scrubs = find_scrubbable(args.days)  # 研报夹带竞品域名 → 抹域名保留
    print(f"[retract] 广告待删 {len(hits)} 条 / 抹域名保留 {len(scrubs)} 条" + (f"（最近 {args.days} 天）" if args.days else "（全部历史）"))
    for h in hits[:100]:
        print(f"  删 - {h['created_at']} | {h['topic']} | {h['reason']} | {h['title']}")
    for s in scrubs[:100]:
        print(f"  抹 - {s['created_at']} | {s['topic']} | {s['title']}")

    if not hits and not scrubs:
        print("[retract] 无命中，无需处理。")
        return
    if not args.apply:
        print("[retract] 预览模式：确认无误后加 --apply 执行（删广告 + 抹域名）。")
        return

    backup = _backup_db()
    print(f"[retract] 已备份 DB → {backup}")
    deleted = retract([h["id"] for h in hits])
    scrubbed = scrub_existing(scrubs)
    print(f"[retract] ✅ 已删除广告 {deleted} 条、抹竞品域名保留 {scrubbed} 条。前端刷新后生效。")


if __name__ == "__main__":
    main()
