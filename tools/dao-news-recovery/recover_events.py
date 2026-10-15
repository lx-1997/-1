#!/usr/bin/env python3
"""Recover missing current-day DAO events without sending notifications.

Python 3.8+, standard library only. The default is a read-only dry run. All
HTTP pages are validated before --apply imports the standalone event store.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BJ_TZ = timezone(timedelta(hours=8))
API_URL = "https://backend.futoucaixin.cn/api/index/realinfoList"
ROWS = 100
MAX_PAGES = 5


class RecoveryError(Exception):
    """An error whose message contains no credentials or article content."""


def source_datetime(value):
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BJ_TZ)
    return parsed.astimezone(BJ_TZ)


def read_cutoff(db_path, now):
    """Read the largest existing realinfo source time from the same BJ day."""
    if not db_path.is_file():
        raise RecoveryError("DAO event database does not exist")
    day = now.date().isoformat()
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
            rows = conn.execute(
                "SELECT source_time FROM events WHERE type = ? AND substr(source_time, 1, 10) = ?",
                ("realinfo", day),
            ).fetchall()
    except sqlite3.Error:
        raise RecoveryError("Cannot read DAO event cutoff") from None
    times = [source_datetime(row[0]) for row in rows]
    same_day = [stamp for stamp in times if stamp is not None and stamp.date() == now.date()]
    return max(same_day) if same_day else now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_page(token, page, rows=ROWS):
    request = Request(
        API_URL + "?" + urlencode({"page": page, "rows": rows, "keyword": ""}),
        headers={
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.futoucaixin.cn",
            "token": token,
            "user-agent": "Mozilla/5.0 (DAO event recovery)",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RecoveryError("Upstream HTTP request failed (status %s)" % exc.code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RecoveryError("Upstream request failed or returned invalid JSON") from None


def existing_source_ids(db_path, source_ids):
    if not source_ids:
        return set()
    placeholders = ",".join("?" for _ in source_ids)
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
            rows = conn.execute(
                "SELECT source_id FROM events WHERE type = ? AND source_id IN (" + placeholders + ")",
                ["realinfo"] + list(source_ids),
            ).fetchall()
    except sqlite3.Error:
        raise RecoveryError("Cannot read existing DAO event IDs") from None
    return {str(row[0]) for row in rows}


def page_items(payload):
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise RecoveryError("Upstream returned an unsuccessful response")
    data = payload.get("data")
    grouped = data.get("grouped_data") if isinstance(data, dict) else None
    if not isinstance(grouped, dict):
        raise RecoveryError("Upstream response has no grouped_data object")
    items = []
    for rows in grouped.values():
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise RecoveryError("Upstream page contains an invalid item list")
        items.extend(rows)
    return items


def load_store(dao_dir, db_path):
    """Import only dao_event_store; never import the collector or a notifier."""
    path = dao_dir / "dao_event_store.py"
    if not path.is_file():
        raise RecoveryError("Standalone DAO event store module does not exist")
    spec = importlib.util.spec_from_file_location("_dao_news_recovery_store", str(path))
    if spec is None or spec.loader is None:
        raise RecoveryError("Cannot load standalone DAO event store")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Override any inherited DAO_EVENTS_DB_PATH: --dao-dir is the destination.
    module.DB_PATH = str(db_path)
    return module


def recover(dao_dir, apply=False, max_pages=MAX_PAGES, now=None, fetch_page=None, store_loader=None, since=None):
    if not 1 <= max_pages <= MAX_PAGES:
        raise RecoveryError("max_pages must be between 1 and 5")
    dao_dir = Path(dao_dir)
    now = now or datetime.now(BJ_TZ)
    now = now.replace(tzinfo=BJ_TZ) if now.tzinfo is None else now.astimezone(BJ_TZ)
    db_path = dao_dir / ".dao_events.sqlite3"
    latest_existing = read_cutoff(db_path, now)
    cutoff = latest_existing
    if since is not None:
        cutoff = source_datetime(since)
        if cutoff is None or cutoff.date() != now.date() or cutoff > now:
            raise RecoveryError("since must be a valid source time from today and no later than now")
    if apply and cutoff < latest_existing:
        raise RecoveryError(
            "Historical apply is blocked: the bridge sorts by ingestion time, so old news "
            "would appear as latest. Use a dedicated source-time-preserving recovery workflow; "
            "dry-run auditing remains available."
        )
    try:
        token = (dao_dir / ".api_token").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise RecoveryError("Cannot read DAO token file") from None
    if not token:
        raise RecoveryError("DAO token file is empty")
    fetch_page = fetch_page or get_page
    candidates = {}
    scanned = 0
    pages = 0
    rejected = {"invalid": 0, "other_day": 0, "future": 0, "at_or_before_cutoff": 0}
    reached_cutoff = False
    page_limit_reached = False
    for page in range(1, max_pages + 1):
        items = page_items(fetch_page(token, page, ROWS))
        pages += 1
        scanned += len(items)
        for item in items:
            stamp = source_datetime(item.get("createtime_text"))
            source_id = str(item.get("id") or "").strip()
            title = str(item.get("name") or "").strip()
            if stamp is None or not source_id or not title:
                rejected["invalid"] += 1
                continue
            if stamp <= cutoff:
                reached_cutoff = True
            if stamp.date() != now.date():
                rejected["other_day"] += 1
                continue
            if stamp > now + timedelta(seconds=300):
                rejected["future"] += 1
                continue
            if stamp <= cutoff:
                rejected["at_or_before_cutoff"] += 1
                continue
            if source_id not in candidates:
                candidates[source_id] = (stamp, item)
        if reached_cutoff or len(items) < ROWS:
            break
        if page == max_pages:
            page_limit_reached = True
    ordered = sorted(candidates.items(), key=lambda entry: (entry[1][0], entry[0]))
    existing = existing_source_ids(db_path, list(candidates))
    result = {
        "mode": "apply" if apply else "dry-run",
        "day": now.date().isoformat(),
        "cutoff": cutoff.isoformat(),
        "cutoff_source": "since" if since is not None else "latest-existing",
        "latest_existing_source_time": latest_existing.isoformat(),
        "pages": pages,
        "scanned": scanned,
        "candidates": len(ordered),
        "already_present": len(existing),
        "missing": len(ordered) - len(existing),
        "write_attempts": 0,
        "page_limit_reached": page_limit_reached,
        "rejected": rejected,
        "items": [
            {"source_id": source_id, "source_time": str(item.get("createtime_text")), "already_present": source_id in existing}
            for source_id, (_stamp, item) in ordered
        ],
    }
    # Every HTTP page must succeed before any import or database write occurs.
    if apply and len(ordered) > len(existing):
        store = (store_loader or load_store)(dao_dir, db_path)
        for output, (source_id, (_stamp, item)) in zip(result["items"], ordered):
            if source_id in existing:
                continue
            event_id = store.record_event(
                "realinfo",
                source_id=source_id,
                title=str(item["name"]).strip(),
                content=str(item["name"]).strip(),
                url=API_URL,
                source_time=str(item["createtime_text"]),
                raw=item,
            )
            if event_id is None:
                raise RecoveryError("DAO event store did not record a validated item")
            output["event_id"] = int(event_id)
            result["write_attempts"] += 1
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dao-dir", type=Path, default=Path("/DAO财经"))
    parser.add_argument("--max-pages", type=int, choices=range(1, MAX_PAGES + 1), default=MAX_PAGES)
    parser.add_argument("--since", help="Override the cutoff with a current-day source time, e.g. '2026-10-15 14:05:34'")
    parser.add_argument("--apply", action="store_true", help="Record validated events; default is read-only")
    args = parser.parse_args(argv)
    try:
        result = recover(args.dao_dir, apply=args.apply, max_pages=args.max_pages, since=args.since)
    except RecoveryError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:
        # Exception text from HTTP libraries/store modules may contain source data.
        print(json.dumps({"error": "Recovery failed (%s)" % type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
