from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Any, Optional

from . import ashare_review
from .auth import list_users
from .metrics_store import DB_PATH as METRICS_DB_PATH
from .recall_subscriptions import DB_PATH as RECALL_DB_PATH, _app_base_url, _email_smtp_config, _smtp_sendmail
from .shared_utils import utc_now_iso

"""
T+1 未回访召回：注册后 24h 内没再回来的新用户，发一封「带具体内容」的召回邮件。

口径（对齐增长报告 D1 留存的痛点）：
- 候选 = 注册时间落在 [now-72h, now-24h] 且留了邮箱的用户；
  最近活跃（activity_log 按 actor_id=u:<id> 取 MAX(ts)）距今超过 24h——
  即「注册那天来过、之后一整天没回来」。72h 上限避免首次上线时给老用户群发。
- 内容 = 最近一期 A 股收盘复盘的买方一句话 + 「我们提前发现」条数，给一个今天就能用的理由回来。
- 每个用户终生只发一封（t1_recall_sent 落库去重），SMTP 未配置时优雅记 skipped、绝不抛出。
"""


def _connect_recall() -> sqlite3.Connection:
    conn = sqlite3.connect(RECALL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_sent_table() -> None:
    RECALL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect_recall() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS t1_recall_sent (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                sent_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _already_sent_ids() -> set:
    _init_sent_table()
    with _connect_recall() as conn:
        rows = conn.execute("SELECT user_id FROM t1_recall_sent").fetchall()
    return {r["user_id"] for r in rows}


def _record_sent(user_id: str, email: str, status: str, detail: str) -> None:
    try:
        with _connect_recall() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO t1_recall_sent (user_id, email, status, detail, sent_at) VALUES (?,?,?,?,?)",
                (user_id, email, status, detail[:200], utc_now_iso()),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def _last_seen_map() -> dict:
    """activity_log：登录账号 → 最近活跃 ts（ISO）。失败返回空 dict。"""
    try:
        with sqlite3.connect(METRICS_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT actor_id, MAX(ts) AS last_seen FROM activity_log WHERE actor_kind='user' GROUP BY actor_id"
            ).fetchall()
            return {r["actor_id"]: r["last_seen"] for r in rows}
    except sqlite3.Error:
        return {}


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def find_t1_candidates() -> list:
    """注册 24~72h、留了邮箱、最近 24h 没回来、还没发过的新用户。"""
    now = datetime.now(timezone.utc)
    sent = _already_sent_ids()
    last_seen = _last_seen_map()
    out = []
    for u in list_users():
        if not (u.email or "").strip() or u.id in sent:
            continue
        created = _as_utc(u.created_at)
        if not (now - timedelta(hours=72) <= created <= now - timedelta(hours=24)):
            continue
        seen_iso = last_seen.get(f"u:{u.id}") or ""
        if seen_iso:
            try:
                seen = datetime.fromisoformat(seen_iso)
                if _as_utc(seen) > now - timedelta(hours=24):
                    continue  # 最近一天来过，不算流失
            except ValueError:
                pass
        out.append(u)
    # 注册早的在前：受每日上限截断时，优先发给快滑出 72h 窗口的人，晚到的明天还有机会
    out.sort(key=lambda u: _as_utc(u.created_at))
    return out


_EMAIL_FOOTER = (
    "—————————————————————\n"
    "DeepFocus · 金融终端 daocaijing.com\n"
    "本邮件由系统自动发送。内容仅供研究参考，不构成投资建议；据此操作风险自负。\n"
    "如不希望再收到此类邮件，回复「退订」即可。"
)


def _indices_line(review: dict) -> str:
    """指数收盘一行摘要，如「上证指数 3,425.18 +1.20% · 深证成指 …」。取不到返回空。"""
    parts = []
    for idx in (review.get("indices") or [])[:3]:
        name, close, pct = idx.get("name"), idx.get("close"), idx.get("pct")
        if not name or close is None or pct is None:
            continue
        parts.append(f"{name} {close} {'+' if pct >= 0 else ''}{pct}%")
    return " ｜ ".join(parts)


def _build_email(username: str) -> tuple[str, str]:
    """召回邮件 (subject, body)：复用当日复盘的正式叙述（大盘/板块/明日关注），按研报体例排版。"""
    app = _app_base_url()
    review: Optional[dict] = None
    try:
        review = ashare_review.latest_review()
    except Exception:  # noqa: BLE001 —— 复盘读取失败退回通用文案
        review = None
    if review:
        nar = review.get("narrative") or {}
        one_liner = (nar.get("one_liner") or "").strip()
        edges = review.get("our_edge") or []
        date = review.get("date") or ""
        session_label = (review.get("session_label") or "收盘复盘").strip()
        subject = f"【DeepFocus】{date} A股{session_label}｜{one_liner[:42]}" if one_liner else f"【DeepFocus】{date} A股{session_label}"
        lines = [f"{username}，您好：", "", f"以下是 DeepFocus {date} A股{session_label}的核心内容。"]
        idx_line = _indices_line(review)
        if idx_line:
            lines += ["", idx_line]
        for head, key in (("▌大盘", "market"), ("▌板块与个股主线", "sectors"), ("▌明日关注", "tomorrow")):
            text = (nar.get(key) or "").strip()
            if text:
                lines += ["", head, text]
        lines.append("")
        if edges:
            lines.append(f"本期复盘中有 {len(edges)} 条本站提前于盘面发布的资讯与当日走势形成对照，完整对照与数据溯源请见站内完整版。")
        lines += [
            f"阅读完整复盘（每个交易日 15:35 自动更新）：{app}",
            "",
            _EMAIL_FOOTER,
        ]
        return subject, "\n".join(lines)
    subject = "【DeepFocus】您的 3 天体验会员尚未领取"
    body = (
        f"{username}，您好：\n\n"
        f"每个交易日 15:35，DeepFocus 会自动生成当日 A股收盘复盘（大盘 / 板块 / 个股，并与本站提前发布的资讯逐条对照）。"
        f"登录即可免费领取 3 天体验会员、解锁全部功能：{app}\n\n{_EMAIL_FOOTER}"
    )
    return subject, body


def _daily_limit() -> int:
    """每轮最多发几封（保护 163 个人号发信额度；拉新脉冲日发不完的明天自动续）。0/负数 = 不限。"""
    try:
        return int(os.getenv("DEEPFOCUS_T1_DAILY_LIMIT", "40"))
    except ValueError:
        return 40


def run_t1_recall_once(limit: Optional[int] = None) -> dict:
    """执行一轮 T+1 召回。返回 {candidates, sent, skipped, errors, deferred, detail[]}，任何失败不抛出。

    limit 不传则用 DEEPFOCUS_T1_DAILY_LIMIT（默认 40/轮）；超出的候选不落库，明天还在窗口内会自动续发。"""
    summary: dict = {"candidates": 0, "sent": 0, "skipped": 0, "errors": 0, "deferred": 0, "detail": []}
    try:
        candidates = find_t1_candidates()
    except Exception as exc:  # noqa: BLE001
        summary["detail"].append(f"候选筛选失败：{exc}"[:200])
        return summary
    summary["candidates"] = len(candidates)
    cap = _daily_limit() if limit is None else limit
    if cap > 0 and len(candidates) > cap:
        summary["deferred"] = len(candidates) - cap
        summary["detail"].append(f"超出本轮上限 {cap}，{summary['deferred']} 人留待下轮（窗口内自动续发）")
        candidates = candidates[:cap]
    if not candidates:
        return summary
    config = _email_smtp_config()
    if config is None:
        # ⭐ SMTP 未配置：本轮整体跳过，但【绝不逐人落库】。
        # t1_recall_sent 以 user_id 为主键、且 find_t1_candidates 排除已落库者，
        # 一旦写入 skipped 即终生去重；而候选窗口只有注册后 24~72h——毒化一次就错过
        # 这批人唯一的 T+1 召回。改为不落库直接返回：配好 SMTP 后他们仍在窗口内会自动续发。
        summary["skipped"] = len(candidates)
        summary["detail"].append(
            f"SMTP 未配置，{len(candidates)} 名候选本轮跳过（不落库，配置 SMTP 后窗口内自动续发）"
        )
        return summary
    for u in candidates:
        email = (u.email or "").strip()
        subject, body = _build_email(u.username)
        try:
            mime = MIMEText(body, "plain", "utf-8")
            mime["Subject"] = subject
            mime["From"] = config["sender"] or config["user"]
            mime["To"] = email
            _smtp_sendmail(config, [email], mime)
            _record_sent(u.id, email, "sent", subject)
            summary["sent"] += 1
            summary["detail"].append(f"sent → {u.username}")
        except Exception as exc:  # noqa: BLE001 —— 单封失败不影响其余
            _record_sent(u.id, email, "error", str(exc))
            summary["errors"] += 1
            summary["detail"].append(f"error → {u.username}: {exc}"[:200])
    return summary


def t1_recall_stats() -> dict:
    """运营可见：累计发送/跳过/失败 + 最近 20 条记录。"""
    _init_sent_table()
    out: dict = {"sent": 0, "skipped": 0, "error": 0, "recent": []}
    try:
        with _connect_recall() as conn:
            for r in conn.execute("SELECT status, COUNT(*) c FROM t1_recall_sent GROUP BY status").fetchall():
                if r["status"] in out:
                    out[r["status"]] = int(r["c"])
            out["recent"] = [
                dict(r) for r in conn.execute(
                    "SELECT email, status, detail, sent_at FROM t1_recall_sent ORDER BY sent_at DESC LIMIT 20"
                ).fetchall()
            ]
    except sqlite3.Error:
        pass
    return out
