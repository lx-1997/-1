"""一次性迁移:清掉历史 realtime_messages 里残留的「[链接已隐藏]」占位符。

早期竞品链接过滤把 futoucaixin 链接替换成「[链接已隐藏]」并入库烘焙;新策略改为直接删除不留提示
(见 news_filter.scrub)。这些老条目已无竞品域名,retract_news 抓不到,故单独清理:
去掉占位符 + 复用 news_filter._tidy 收尾(空括号/串尾悬挂分隔符/多余空白)。

默认 dry-run 只预览;--apply 才改库,且改前自动备份整库文件。

    python -m deepfocus_api.strip_link_placeholder            # 预览
    python -m deepfocus_api.strip_link_placeholder --apply    # 执行(先备份)
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timezone

from .news_filter import _tidy
from .realtime_messages import DB_PATH

PLACEHOLDER = "[链接已隐藏]"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=8000")
    except sqlite3.Error:
        pass
    return conn


def find_placeholdered() -> list[dict]:
    """命中占位符且清理后确有变化的条目(带新旧值预览)。LIKE 里 '[' 在 SQLite 非通配,字面匹配。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, content FROM realtime_messages "
            "WHERE title LIKE ? OR content LIKE ? ORDER BY created_at DESC",
            (f"%{PLACEHOLDER}%", f"%{PLACEHOLDER}%"),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        ot, oc = r["title"] or "", r["content"] or ""
        nt = _tidy(ot.replace(PLACEHOLDER, ""))
        nc = _tidy(oc.replace(PLACEHOLDER, ""))
        if nt != ot or nc != oc:
            out.append({"id": r["id"], "ot": ot, "oc": oc, "nt": nt, "nc": nc})
    return out


def apply_changes(items: list[dict]) -> int:
    if not items:
        return 0
    n = 0
    with _connect() as conn:
        for it in items:
            cur = conn.execute(
                "UPDATE realtime_messages SET title=?, content=? WHERE id=?",
                (it["nt"][:240] or "实时消息", it["nc"][:8000], it["id"]),
            )
            n += cur.rowcount
        conn.commit()
    return n


def _backup_db() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst = f"{DB_PATH}.bak-{stamp}"
    shutil.copy2(DB_PATH, dst)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正改库(默认只预览)")
    args = ap.parse_args()
    print(f"[strip-placeholder] DB = {DB_PATH}")
    items = find_placeholdered()
    print(f"[strip-placeholder] 含「{PLACEHOLDER}」待清理: {len(items)} 条")
    for it in items[:8]:
        print(f"  - {it['id'][:8]} | {(it['ot'] or it['oc'])[:46]!r} → {(it['nt'] or it['nc'])[:46]!r}")
    if not args.apply:
        print("[strip-placeholder] DRY-RUN(加 --apply 执行,会先自动备份整库)")
        return
    bak = _backup_db()
    print(f"[strip-placeholder] 已备份 → {bak}")
    n = apply_changes(items)
    print(f"[strip-placeholder] 已清理 {n} 条")


if __name__ == "__main__":
    main()
