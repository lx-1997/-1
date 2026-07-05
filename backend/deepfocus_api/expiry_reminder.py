from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional

from .auth import users_expiring_within
from .recall_subscriptions import DB_PATH as RECALL_DB_PATH, _app_base_url, _email_smtp_config, _smtp_sendmail
from .shared_utils import utc_now_iso

"""
会员到期转化召回：在免费会员（trial/invite/reward/checkin/admin）到期前 ~24-48h，
发一封「即将到期 + 续费立享 + 一键购买」邮件，把最高意向的到期时刻转成付费。

- 候选：会员将在 48h 内到期、留了邮箱、非付费来源（auth.users_expiring_within）。
- 去重：每个「用户 + 到期日」只发一次（续费后再次临期是新周期，会再提醒）。
- 复用召回邮件机器（SMTP 配置 / 发信），SMTP 未配置时优雅 skipped、绝不抛。
- 每轮发送上限 DEEPFOCUS_EXPIRY_DAILY_LIMIT（默认 60，保护发信额度）。
"""

BJ_TZ = timezone(timedelta(hours=8))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(RECALL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    RECALL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expiry_reminded (
                user_id TEXT NOT NULL,
                expiry_day TEXT NOT NULL,
                email TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (user_id, expiry_day)
            )
            """
        )
        conn.commit()


def _already(user_id: str, expiry_day: str) -> bool:
    _init()
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM expiry_reminded WHERE user_id = ? AND expiry_day = ?",
            (user_id, expiry_day),
        ).fetchone() is not None


def _record(user_id: str, expiry_day: str, email: str, status: str, detail: str) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO expiry_reminded (user_id, expiry_day, email, status, detail, sent_at)"
                " VALUES (?,?,?,?,?,?)",
                (user_id, expiry_day, email, status, detail[:200], utc_now_iso()),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def _daily_limit() -> int:
    try:
        return int(os.getenv("DEEPFOCUS_EXPIRY_DAILY_LIMIT", "60"))
    except ValueError:
        return 60


def _build_renewal_email(username: str, days_left: int) -> tuple[str, str]:
    """付费用户续费提醒：语气是「感谢+无缝续期」，不是免费转付费的推销。"""
    app = _app_base_url()
    when = "今天" if days_left <= 0 else ("明天" if days_left == 1 else f"{days_left} 天后")
    subject = f"【DeepFocus】你的会员{when}到期 · 续费无缝衔接"
    body = (
        f"{username}，您好：\n\n"
        f"感谢你一直以来的支持。你的 DeepFocus 会员将于{when}到期，"
        f"续费后到期时间自动顺延、权益不中断：{app}\n\n"
        f"如已续费，请忽略本提醒。\n\n"
        "—————————————————————\n"
        "DeepFocus · 金融终端 daocaijing.com\n"
        "本邮件由系统自动发送。内容仅供研究参考，不构成投资建议。\n"
        "如不希望再收到此类邮件，回复「退订」即可。"
    )
    return subject, body


def _build_email(username: str, days_left: int) -> tuple[str, str]:
    app = _app_base_url()
    when = "今天" if days_left <= 0 else ("明天" if days_left == 1 else f"{days_left} 天后")
    subject = f"【DeepFocus】你的会员{when}到期 · 续费立享全部功能"
    body = (
        f"{username}，您好：\n\n"
        f"你的 DeepFocus 尊享会员将于{when}到期。到期后将无法继续使用：\n"
        f"· AI 研报 / 快讯多模态解读（买方观点、评级、目标价）\n"
        f"· 资讯原文与深度内容\n"
        f"· 每个交易日 15:35 的 A股收盘复盘（含「我们提前发现的资讯」对照）\n\n"
        f"现在续费即可无缝衔接、不中断：{app}\n\n"
        f"（已是付费会员可忽略本提醒。）\n\n"
        "—————————————————————\n"
        "DeepFocus · 金融终端 daocaijing.com\n"
        "本邮件由系统自动发送。内容仅供研究参考，不构成投资建议。\n"
        "如不希望再收到此类邮件，回复「退订」即可。"
    )
    return subject, body


def _renewal_candidates() -> list[tuple[dict, str]]:
    """付费用户续费召回候选：到期前 7 天 / 1 天两档触达（churn 止血）。
    去重键在 expiry_day 上带档位后缀（免表迁移）：同一到期日 7 天档与 1 天档各发一次。
    返回 [(user, dedup_key)]。"""
    out: list[tuple[dict, str]] = []
    for u in users_expiring_within(7 * 24, paid=True):
        day = u["expires_at"][:10]
        days_left = int(u.get("days_left", 7))
        stage = f"{day}#r1" if days_left <= 1 else f"{day}#r7"
        if not _already(u["id"], stage):
            out.append((u, stage))
    return out


def run_expiry_reminder_once(limit: Optional[int] = None) -> dict:
    """执行一轮到期召回（免费转化 + 付费续费两条线）。
    返回 {candidates, sent, skipped, errors, deferred, renewal_candidates, renewal_sent, detail[]}，绝不抛出。"""
    summary: dict = {"candidates": 0, "sent": 0, "skipped": 0, "errors": 0, "deferred": 0,
                     "renewal_candidates": 0, "renewal_sent": 0, "detail": []}
    try:
        cands = [u for u in users_expiring_within(48) if not _already(u["id"], u["expires_at"][:10])]
    except Exception as exc:  # noqa: BLE001
        summary["detail"].append(f"候选筛选失败：{exc}"[:200])
        return summary
    try:
        renewals = _renewal_candidates()
    except Exception as exc:  # noqa: BLE001
        renewals = []
        summary["detail"].append(f"续费候选筛选失败：{exc}"[:200])
    summary["candidates"] = len(cands)
    summary["renewal_candidates"] = len(renewals)
    cap = _daily_limit() if limit is None else limit
    if cap > 0 and len(cands) > cap:
        summary["deferred"] = len(cands) - cap
        summary["detail"].append(f"超出本轮上限 {cap}，{summary['deferred']} 人留待下轮")
        cands = cands[:cap]
    if not cands and not renewals:
        return summary
    config = _email_smtp_config()
    if config is None:
        # SMTP 未配置：本轮整体跳过、不逐人落库（到期提醒按 expiry_day 去重、下周期会自愈，
        # 但仍不写 skipped 行以免污染统计、并与 t1_recall 保持一致）。
        summary["skipped"] = len(cands) + len(renewals)
        summary["detail"].append(f"SMTP 未配置，{len(cands) + len(renewals)} 名候选本轮跳过（不落库）")
        return summary

    def _send(u: dict, dedup_key: str, subject: str, bdy: str, counter: str) -> None:
        try:
            mime = MIMEText(bdy, "plain", "utf-8")
            mime["Subject"] = subject
            mime["From"] = config["sender"] or config["user"]
            mime["To"] = u["email"]
            _smtp_sendmail(config, [u["email"]], mime)
            _record(u["id"], dedup_key, u["email"], "sent", subject)
            summary[counter] += 1
        except Exception as exc:  # noqa: BLE001
            _record(u["id"], dedup_key, u["email"], "error", str(exc))
            summary["errors"] += 1

    for u in cands:
        subject, bdy = _build_email(u["username"], int(u.get("days_left", 1)))
        _send(u, u["expires_at"][:10], subject, bdy, "sent")
    for u, stage in renewals:
        subject, bdy = _build_renewal_email(u["username"], int(u.get("days_left", 7)))
        _send(u, stage, subject, bdy, "renewal_sent")
    return summary


def expiry_reminder_stats() -> dict:
    _init()
    out: dict = {"sent": 0, "skipped": 0, "error": 0, "recent": []}
    try:
        with _connect() as conn:
            for r in conn.execute("SELECT status, COUNT(*) c FROM expiry_reminded GROUP BY status").fetchall():
                if r["status"] in out:
                    out[r["status"]] = int(r["c"])
            out["recent"] = [
                dict(r) for r in conn.execute(
                    "SELECT email, status, detail, sent_at FROM expiry_reminded ORDER BY sent_at DESC LIMIT 20"
                ).fetchall()
            ]
    except sqlite3.Error:
        pass
    return out
