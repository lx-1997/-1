from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import uuid
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

from .shared_utils import utc_now_iso
from .schemas import (
    RealtimeMessageRecord,
    RecallDeliveryRecord,
    RecallDeliveryResult,
    RecallDispatchResponse,
    RecallMetricsResponse,
    RecallSubscriptionCreateRequest,
    RecallSubscriptionRecord,
)

"""
信号召回 · 离线通道订阅 + 投递闭环度量。

负责存储用户的召回订阅（邮件 / Web Push / 微信），在新信号到达时按订阅匹配并交付。
交付走「可插拔适配器」：未配置（无 SMTP / 无 VAPID）时优雅降级为 skipped，
绝不尝试出网、绝不抛出——保证消息创建/广播主链路不受影响。
匹配语义与前端 signalRecall.shouldRecall 对齐。

闭环验真：每条投递都落库并带一个可追踪点击链接（{public}/api/realtime/recall/click/{id}），
用户从邮件/推送点回时记录回流时间，`recall_metrics()` 据此算送达/点击/回流率——
让「召回是否真把人拉回来」从不可见变成可度量。
"""


def _public_base_url() -> str:
    """构建可追踪点击链接用的后端公开基址。

    ⚠️ 默认指向生产同源域名（对齐 seo_pages 既有约定），避免 env 未设时把 localhost
    死链写进召回邮件/推送——召回是把人拉回来的资产，链接必须能落地。本地开发请显式设
    DEEPFOCUS_PUBLIC_BASE_URL=http://localhost:8300 覆盖。"""
    return os.getenv("DEEPFOCUS_PUBLIC_BASE_URL", "https://daocaijing.com").strip().rstrip("/")


def _app_base_url() -> str:
    """点击后回流的前端 App 基址（把人拉回应用而非外部源站）。默认生产同源，理由同上。"""
    return os.getenv("DEEPFOCUS_APP_BASE_URL", "https://daocaijing.com").strip().rstrip("/")


def _deep_link(message: RealtimeMessageRecord) -> str:
    """返回 App 的信号深链：带上 message id，落地即回到应用内。"""
    return f"{_app_base_url()}/?signal={message.id}"


def _tracked_click_url(delivery_id: str) -> str:
    return f"{_public_base_url()}/api/realtime/recall/click/{delivery_id}"

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_RECALL_SUBSCRIPTION_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".recall_subscriptions.sqlite3"),
    )
)

MAX_DELIVERY_LOG = 50
_recent_deliveries: list[RecallDeliveryResult] = []


def init_recall_subscription_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recall_subscriptions (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                address TEXT NOT NULL,
                symbols_json TEXT NOT NULL,
                severities_json TEXT NOT NULL,
                scope TEXT NOT NULL,
                label TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recall_sub_channel ON recall_subscriptions(channel)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recall_deliveries (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                address TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                target_url TEXT,
                created_at TEXT NOT NULL,
                clicked_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recall_deliveries_created ON recall_deliveries(created_at)")
        conn.commit()


def create_recall_subscription(request: RecallSubscriptionCreateRequest) -> RecallSubscriptionRecord:
    init_recall_subscription_db()
    symbols = _normalize_symbols(request.symbols)
    severities = list(request.severities) or ["warning", "critical"]
    record = {
        "id": str(uuid.uuid4()),
        "channel": request.channel,
        "address": request.address.strip(),
        "symbols_json": json.dumps(symbols, ensure_ascii=False),
        "severities_json": json.dumps(severities, ensure_ascii=False),
        "scope": request.scope,
        "label": (request.label or "").strip() or None,
        "active": 1,
        "created_at": utc_now_iso(),
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO recall_subscriptions (
                id, channel, address, symbols_json, severities_json, scope, label, active, created_at
            ) VALUES (
                :id, :channel, :address, :symbols_json, :severities_json, :scope, :label, :active, :created_at
            )
            """,
            record,
        )
        conn.commit()
    return _row_to_subscription(record)


def list_recall_subscriptions(active_only: bool = True) -> list[RecallSubscriptionRecord]:
    init_recall_subscription_db()
    where = "WHERE active = 1" if active_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM recall_subscriptions {where} ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_subscription(dict(row)) for row in rows]


def delete_recall_subscription(subscription_id: str) -> bool:
    init_recall_subscription_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM recall_subscriptions WHERE id = ?", (subscription_id,))
        conn.commit()
        return cursor.rowcount > 0


def subscription_matches(subscription: RecallSubscriptionRecord, message: RealtimeMessageRecord) -> bool:
    """与前端 signalRecall.shouldRecall 同义：级别命中 + 范围/标的匹配。
    例外：每日复盘是一日一次的策划摘要，对所有开了盯盘的人都是高价值「每日回访钩子」
    → 全员送达（不受 scope=watchlist / severities 收窄）。这是把流失用户拉回来的核心信号。"""
    if (getattr(message, "topic", "") or "") == "复盘":
        return True
    if message.severity not in subscription.severities:
        return False
    if subscription.scope == "all":
        return True
    # watchlist 范围：必须命中订阅显式关注的标的
    if not message.symbol:
        return False
    return message.symbol.upper() in [symbol.upper() for symbol in subscription.symbols]


def dispatch_recall(message: RealtimeMessageRecord) -> RecallDispatchResponse:
    """新信号扇出到匹配的订阅并按通道交付。任何交付失败/未配置都不抛出。

    每条投递都分配一个 delivery_id 并落库，邮件/推送内嵌可追踪点击链接——
    点击回流时回填 clicked_at，让召回效果可度量（见 recall_metrics）。
    """
    matched = [sub for sub in list_recall_subscriptions(active_only=True) if subscription_matches(sub, message)]
    deliveries: list[RecallDeliveryResult] = []
    for subscription in matched:
        delivery_id = str(uuid.uuid4())
        tracked_url = _tracked_click_url(delivery_id)
        target_url = _deep_link(message)
        result = _deliver(subscription, message, tracked_url)
        deliveries.append(result)
        _record_delivery(result)
        _persist_delivery(delivery_id, subscription, message, result, target_url)
    return RecallDispatchResponse(message_id=message.id, matched=len(matched), deliveries=deliveries)


def recent_deliveries() -> list[RecallDeliveryResult]:
    return list(_recent_deliveries)


# === 通道适配器（可插拔；未配置时优雅降级，不尝试出网） ===

def _deliver(
    subscription: RecallSubscriptionRecord,
    message: RealtimeMessageRecord,
    tracked_url: str,
) -> RecallDeliveryResult:
    if subscription.channel == "email":
        return _deliver_email(subscription, message, tracked_url)
    if subscription.channel == "webpush":
        return _deliver_webpush(subscription, message, tracked_url)
    if subscription.channel == "wechat":
        return _deliver_wechat(subscription, message, tracked_url)
    return _result(subscription, "skipped", "未知通道")


def _email_smtp_config() -> Optional[dict[str, Any]]:
    host = os.getenv("DEEPFOCUS_SMTP_HOST", "").strip()
    if not host:
        return None
    user = os.getenv("DEEPFOCUS_SMTP_USER", "").strip()
    port = int(os.getenv("DEEPFOCUS_SMTP_PORT", "587"))
    # SSL：163/QQ 等常用 465 直连 SSL；DEEPFOCUS_SMTP_SSL=1 或端口 465 自动启用
    use_ssl = os.getenv("DEEPFOCUS_SMTP_SSL", "").strip() == "1" or port in (465, 994)
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": os.getenv("DEEPFOCUS_SMTP_PASSWORD", "").strip(),
        "sender": os.getenv("DEEPFOCUS_SMTP_FROM", user).strip() or user,
        "use_tls": os.getenv("DEEPFOCUS_SMTP_TLS", "1") != "0",
        "use_ssl": use_ssl,
    }


def _smtp_sendmail(config: dict[str, Any], recipients: list[str], mime) -> None:
    """按配置选择 SSL(465) 或 STARTTLS(587/25) 发信。"""
    if config.get("use_ssl"):
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=15) as smtp:
            if config["user"]:
                smtp.login(config["user"], config["password"])
            smtp.sendmail(mime["From"], recipients, mime.as_string())
    else:
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as smtp:
            if config["use_tls"]:
                smtp.starttls()
            if config["user"]:
                smtp.login(config["user"], config["password"])
            smtp.sendmail(mime["From"], recipients, mime.as_string())


def send_alert_email(subject: str, body: str, to: str = "") -> tuple[bool, str]:
    """通用告警邮件（运维用，如研报 cookie 失效）。需 DEEPFOCUS_SMTP_* + 收件人 DEEPFOCUS_ALERT_EMAIL。"""
    config = _email_smtp_config()
    if config is None:
        return False, "SMTP 未配置（需 DEEPFOCUS_SMTP_HOST 等）"
    recipient = (to or os.getenv("DEEPFOCUS_ALERT_EMAIL", "") or config.get("user") or "").strip()
    if not recipient:
        return False, "未配置收件人（DEEPFOCUS_ALERT_EMAIL）"
    try:
        mime = MIMEText(body, "plain", "utf-8")
        mime["Subject"] = subject
        mime["From"] = config["sender"] or config["user"]
        mime["To"] = recipient
        _smtp_sendmail(config, [recipient], mime)
        return True, "已发送"
    except Exception as exc:  # noqa: BLE001
        return False, f"发送失败：{exc}"[:200]


def _deliver_email(
    subscription: RecallSubscriptionRecord,
    message: RealtimeMessageRecord,
    tracked_url: str,
) -> RecallDeliveryResult:
    config = _email_smtp_config()
    if config is None:
        return _result(subscription, "skipped", "邮件通道未配置（需 DEEPFOCUS_SMTP_HOST 等环境变量）")
    try:
        prefix = f"{message.symbol} · " if message.symbol else ""
        body = f"{message.content}\n\n级别：{message.severity}\n时间：{message.created_at}"
        body += f"\n\n查看详情（返回 App）：{tracked_url}"  # 可追踪点击链接 → 回流度量
        if message.url:
            body += f"\n原文：{message.url}"
        body += (
            "\n\n—————————————————————\n"
            "DeepFocus · 金融终端 daocaijing.com\n"
            "本邮件由系统自动发送。内容仅供研究参考，不构成投资建议；据此操作风险自负。\n"
            "如不希望再收到此类提醒，可在 App 内关闭信号召回订阅。"
        )
        mime = MIMEText(body, "plain", "utf-8")
        mime["Subject"] = f"[DeepFocus 召回] {prefix}{message.title}"
        mime["From"] = config["sender"] or config["user"]
        mime["To"] = subscription.address
        _smtp_sendmail(config, [subscription.address], mime)
        return _result(subscription, "sent", "已发送")
    except Exception as exc:  # 出网/SMTP 失败优雅降级，不影响主链路
        return _result(subscription, "error", f"发送失败：{exc}"[:200])


def _deliver_webpush(
    subscription: RecallSubscriptionRecord,
    message: RealtimeMessageRecord,
    tracked_url: str,
) -> RecallDeliveryResult:
    vapid_private = os.getenv("DEEPFOCUS_VAPID_PRIVATE_KEY", "").strip()
    vapid_subject = os.getenv("DEEPFOCUS_VAPID_SUBJECT", "").strip()
    if not vapid_private or not vapid_subject:
        return _result(subscription, "skipped", "Web Push 未配置（需 VAPID 密钥）")
    try:
        from pywebpush import webpush  # type: ignore
    except Exception:
        return _result(subscription, "skipped", "Web Push 依赖未安装（pywebpush）")
    try:
        prefix = f"{message.symbol} · " if message.symbol else ""
        # 通知点击 url 走可追踪链接，点回时记录回流。
        payload = json.dumps(
            {"title": f"{prefix}{message.title}", "body": message.content, "url": tracked_url},
            ensure_ascii=False,
        )
        webpush(
            subscription_info=json.loads(subscription.address),
            data=payload,
            vapid_private_key=vapid_private,
            vapid_claims={"sub": vapid_subject},
        )
        return _result(subscription, "sent", "已推送")
    except Exception as exc:
        return _result(subscription, "error", f"推送失败：{exc}"[:200])


def _wechat_bridge_config() -> Optional[dict[str, Any]]:
    """个人微信稳态桥接（gewechat）配置。未配置（无 BRIDGE_URL）返回 None → 优雅 skip。

    BRIDGE_URL 填 gewechat 的 API 基址（含 /gewe/v2/api），如 http://127.0.0.1:2531/gewe/v2/api。
    """
    base = os.getenv("DEEPFOCUS_WECHAT_BRIDGE_URL", "").strip().rstrip("/")
    if not base:
        return None
    return {
        "base": base,
        "token": os.getenv("DEEPFOCUS_WECHAT_BRIDGE_TOKEN", "").strip(),
        "app_id": os.getenv("DEEPFOCUS_WECHAT_BRIDGE_APPID", "").strip(),
        "default_target": os.getenv("DEEPFOCUS_WECHAT_DEFAULT_TARGET", "").strip(),
    }


def probe_wechat_online() -> tuple[bool, str]:
    """探活个人微信桥接登录态（gewechat /login/checkOnline）。

    返回 (在线?, 说明)。未配置 → (False, "未配置")，调用方据此跳过告警。
    个人号登录态会被微信踢线 / 风控 / 多端互踢掉线，掉线后 token 失效需重新扫码，
    无法无人值守自动重连——所以靠本探活 + 邮件告警让人第一时间补扫。
    httpx trust_env=False 直连同机 localhost，绕沙箱出网代理。
    """
    config = _wechat_bridge_config()
    if config is None:
        return False, "未配置（无 DEEPFOCUS_WECHAT_BRIDGE_URL）"
    if not config["app_id"]:
        return False, "缺 appId（DEEPFOCUS_WECHAT_BRIDGE_APPID）"
    try:
        import httpx
    except Exception:
        return False, "httpx 依赖不可用"
    try:
        resp = httpx.post(
            f"{config['base']}/login/checkOnline",
            headers={"X-GEWE-TOKEN": config["token"]},
            json={"appId": config["app_id"]},
            timeout=10,
            trust_env=False,
        )
        data = resp.json() if resp.content else {}
        # gewechat：ret==200 且 data 为 true 表示在线
        if resp.status_code == 200 and data.get("ret", 0) == 200:
            online = bool(data.get("data"))
            return online, "在线" if online else "已掉线（需重新扫码登录）"
        return False, f"探活返回异常：{resp.status_code} {str(data)[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"探活失败：{exc}"[:160]


def _deliver_wechat(
    subscription: RecallSubscriptionRecord,
    message: RealtimeMessageRecord,
    tracked_url: str,
) -> RecallDeliveryResult:
    """个人微信桥接：让绑定的专用号把信号 postText 到目标群/好友（gewechat）。

    与邮件/Web Push 同约定：未配置 → skipped；出网/桥接失败 → error 但绝不抛出，
    不影响消息创建/广播主链路。target 取订阅 address（群id @chatroom / wxid），
    为空回退默认群（DEEPFOCUS_WECHAT_DEFAULT_TARGET）。httpx 走 trust_env=False
    直连——桥接在同机 localhost，避开沙箱出网代理（10.9.0.31:8838）拦截。
    """
    config = _wechat_bridge_config()
    if config is None:
        return _result(subscription, "skipped", "微信桥接未配置（需 DEEPFOCUS_WECHAT_BRIDGE_URL 等）")
    target = subscription.address.strip() or config["default_target"]
    if not target:
        return _result(subscription, "skipped", "未指定推送目标（订阅 address 或 DEEPFOCUS_WECHAT_DEFAULT_TARGET）")
    if not config["app_id"]:
        return _result(subscription, "skipped", "微信桥接缺 appId（DEEPFOCUS_WECHAT_BRIDGE_APPID）")
    try:
        import httpx  # 延迟导入，与 webpush 同风格；缺依赖则优雅 skip
    except Exception:
        return _result(subscription, "skipped", "httpx 依赖不可用")
    try:
        prefix = f"{message.symbol} · " if message.symbol else ""
        body = (
            f"【DeepFocus 信号】{prefix}{message.title}\n"
            f"{message.content}\n"
            f"级别：{message.severity} · {message.created_at}\n"
            f"详情：{tracked_url}\n"  # 可追踪链接 → 复用召回点击回流度量
            "内容仅供研究参考，不构成投资建议；据此操作风险自负。"
        )
        resp = httpx.post(
            f"{config['base']}/message/postText",
            headers={"X-GEWE-TOKEN": config["token"]},
            json={"appId": config["app_id"], "toWxid": target, "content": body},
            # 连接 5s / 整体 15s：桥被封号或进程挂掉时快速失败，不空等 15s（即便已挪到 worker 线程也更干净）
            timeout=httpx.Timeout(15.0, connect=5.0),
            trust_env=False,  # 同机 localhost，绕开沙箱出网代理
        )
        data = resp.json() if resp.content else {}
        # gewechat 约定 ret==200 为成功（部分分支不回 ret，宽容放行）
        if resp.status_code == 200 and data.get("ret", 200) == 200:
            return _result(subscription, "sent", "已推送（微信桥接）")
        return _result(subscription, "error", f"桥接返回异常：{resp.status_code} {str(data)[:120]}")
    except Exception as exc:  # 出网/桥接失败优雅降级，不影响主链路
        return _result(subscription, "error", f"推送失败：{exc}"[:200])


def _result(subscription: RecallSubscriptionRecord, status: str, detail: str) -> RecallDeliveryResult:
    return RecallDeliveryResult(
        subscription_id=subscription.id,
        channel=subscription.channel,
        address=subscription.address,
        status=status,  # type: ignore[arg-type]
        detail=detail,
    )


def _record_delivery(result: RecallDeliveryResult) -> None:
    _recent_deliveries.insert(0, result)
    del _recent_deliveries[MAX_DELIVERY_LOG:]


# === 投递闭环：落库 / 点击回流 / 度量 ===

def _persist_delivery(
    delivery_id: str,
    subscription: RecallSubscriptionRecord,
    message: RealtimeMessageRecord,
    result: RecallDeliveryResult,
    target_url: str,
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO recall_deliveries (
                    id, subscription_id, message_id, channel, address,
                    status, detail, target_url, created_at, clicked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    delivery_id,
                    subscription.id,
                    message.id,
                    subscription.channel,
                    subscription.address,
                    result.status,
                    result.detail,
                    target_url,
                    utc_now_iso(),
                ),
            )
            conn.commit()
    except Exception:
        # 度量落库失败绝不影响投递主链路。
        pass


def mark_recall_click(delivery_id: str) -> Optional[str]:
    """记录一次点击回流（首次点击回填 clicked_at），返回回流目标 URL；未知 id 返回 None。"""
    init_recall_subscription_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT target_url, clicked_at FROM recall_deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        if row is None:
            return None
        if not row["clicked_at"]:  # 只记首次回流，重复点击不重复计数
            conn.execute(
                "UPDATE recall_deliveries SET clicked_at = ? WHERE id = ?",
                (utc_now_iso(), delivery_id),
            )
            conn.commit()
        return row["target_url"]


def resolve_recall_click(delivery_id: str) -> str:
    """记录点击回流并返回最终跳转 URL；未知 id 兜底跳回 App 首页（回流体验优先）。"""
    return mark_recall_click(delivery_id) or _app_base_url() or "https://daocaijing.com"


def list_deliveries(limit: int = 50) -> list[RecallDeliveryRecord]:
    init_recall_subscription_db()
    capped = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM recall_deliveries ORDER BY created_at DESC LIMIT ?",
            (capped,),
        ).fetchall()
    return [_row_to_delivery(dict(row)) for row in rows]


def recall_metrics() -> RecallMetricsResponse:
    """召回闭环验真：送达/点击/回流率，以及各通道送达分布。"""
    init_recall_subscription_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT channel, status, clicked_at FROM recall_deliveries"
        ).fetchall()
    delivered = clicked = skipped = error = 0
    by_channel: dict[str, int] = {}
    for row in rows:
        status = row["status"]
        if status == "sent":
            delivered += 1
            by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + 1
            if row["clicked_at"]:
                clicked += 1
        elif status == "skipped":
            skipped += 1
        elif status == "error":
            error += 1
    ctr = round(clicked / delivered, 4) if delivered else 0.0
    return RecallMetricsResponse(
        delivered=delivered,
        clicked=clicked,
        click_through_rate=ctr,
        skipped=skipped,
        error=error,
        by_channel=by_channel,
    )


def _row_to_delivery(row: dict[str, Any]) -> RecallDeliveryRecord:
    return RecallDeliveryRecord(
        id=row["id"],
        subscription_id=row["subscription_id"],
        message_id=row["message_id"],
        channel=row["channel"],
        address=row["address"],
        status=row["status"],
        detail=row.get("detail") or "",
        target_url=row.get("target_url"),
        created_at=row["created_at"],
        clicked_at=row.get("clicked_at"),
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_subscription(row: dict[str, Any]) -> RecallSubscriptionRecord:
    return RecallSubscriptionRecord(
        id=row["id"],
        channel=row["channel"],
        address=row["address"],
        symbols=json.loads(row.get("symbols_json") or "[]"),
        severities=json.loads(row.get("severities_json") or "[]"),
        scope=row.get("scope") or "watchlist",
        label=row.get("label"),
        active=bool(row.get("active", 1)),
        created_at=row["created_at"],
    )


def _normalize_symbols(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    for symbol in symbols:
        clean = str(symbol).strip().upper()
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized[:50]
