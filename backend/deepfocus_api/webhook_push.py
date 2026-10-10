"""Webhook 事件推送：把平台事件（快讯入库等）实时推到用户注册的回调 URL。

与 MCP（AI 主动来拉）互补：webhook 是平台主动推进用户的自动化系统（n8n/Dify/量化脚本）。

安全设计：
- 每订阅一把随机签名密钥（whsec_，创建时一次返回），投递时按 Stripe 风格 HMAC-SHA256 签名：
  头 `X-DaoCaijing-Signature: t=<时间戳>,v1=<hex(hmac_sha256(secret, f"{ts}.{body}"))>`，接收方防重放/防伪造。
- SSRF 防护：仅 http(s)、禁止 user:pass@、解析后落私网/环回/链路本地地址一律拒绝
  （DEEPFOCUS_WEBHOOK_ALLOW_LOCAL=1 仅为本地联调放开）。
- 连续失败 20 次自动停用（打挂你的回调不该拖累我们）；成功即清零。
- 投递在线程池 fire-and-forget，绝不阻塞快讯入库主链路。
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import db
from .auth import require_current_user
from .shared_utils import utc_now_iso

DB_PATH = db.data_path('.webhooks.sqlite3', 'DEEPFOCUS_WEBHOOK_DB_PATH')
ALLOW_LOCAL = os.getenv("DEEPFOCUS_WEBHOOK_ALLOW_LOCAL", "").strip() == "1"
TIMEOUT_S = 8.0
MAX_SUBS_PER_USER = 10
AUTO_DISABLE_FAILS = 20
EVENTS = ("news",)  # v1 事件集；后续在此扩（review/research/ai_analysis…）

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webhook")

router = APIRouter(prefix="/api/account/webhooks", tags=["webhooks"])


def _connect() -> sqlite3.Connection:
    conn = db.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_webhook_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_subs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT 'news',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                delivered_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                last_status INTEGER,
                last_delivery_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_webhook_subs_user ON webhook_subs(user_id)")
        conn.commit()


# --------------------------------------------------------------------------- #
# SSRF 防护
# --------------------------------------------------------------------------- #
def _url_guard_error(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return "URL 无法解析"
    if parsed.scheme not in ("http", "https"):
        return "仅支持 http/https"
    if not parsed.hostname:
        return "缺少主机名"
    if parsed.username or parsed.password:
        return "不允许携带用户凭证的 URL"
    if ALLOW_LOCAL:
        return None
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror:
        return "主机名无法解析"
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return "不允许指向内网/环回地址的回调 URL"
    return None


# --------------------------------------------------------------------------- #
# 订阅管理
# --------------------------------------------------------------------------- #
def create_subscription(user_id: str, url: str, events: list[str], description: str = "") -> dict:
    init_webhook_db()
    err = _url_guard_error(url)
    if err:
        raise ValueError(err)
    evs = [e for e in (events or ["news"]) if e in EVENTS] or ["news"]
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM webhook_subs WHERE user_id = ? AND active = 1", (user_id,)).fetchone()[0]
        if n >= MAX_SUBS_PER_USER:
            raise ValueError(f"每个账号最多 {MAX_SUBS_PER_USER} 个 Webhook 订阅，请先删除不用的")
        sub_id = "wh_" + uuid.uuid4().hex[:12]
        secret = "whsec_" + secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO webhook_subs (id, user_id, url, secret, events, active, created_at, description)"
            " VALUES (?,?,?,?,?,1,?,?)",
            (sub_id, user_id, url.strip()[:500], secret, ",".join(evs), utc_now_iso(), (description or "").strip()[:80]),
        )
        conn.commit()
    return {"id": sub_id, "secret": secret, "url": url.strip()[:500], "events": evs,
            "warning": "签名密钥仅此一次完整显示，请立即保存"}


def list_subscriptions(user_id: str) -> list[dict]:
    init_webhook_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, url, events, active, created_at, description, delivered_count, fail_count,"
            " last_status, last_delivery_at FROM webhook_subs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active"] = bool(d["active"])
        d["events"] = (d["events"] or "").split(",")
        out.append(d)
    return out


def delete_subscription(user_id: str, sub_id: str) -> bool:
    init_webhook_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM webhook_subs WHERE user_id = ? AND id = ?", (user_id, sub_id))
        conn.commit()
    return cur.rowcount > 0


def _active_subs(event: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        # 逗号包围匹配：'news' / 'news,review' / 'review,news' 都能命中
        return conn.execute(
            "SELECT * FROM webhook_subs WHERE active = 1 AND (',' || events || ',') LIKE ?",
            (f"%,{event},%",),
        ).fetchall()


# --------------------------------------------------------------------------- #
# 投递
# --------------------------------------------------------------------------- #
def _sign(secret: str, timestamp: str, body: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.{body}".encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def _deliver(sub: sqlite3.Row, event: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False)
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Daocaijing-Webhook/1.0",
        "X-DaoCaijing-Event": event,
        "X-DaoCaijing-Delivery": str(payload.get("id") or ""),
        "X-DaoCaijing-Signature": f"t={ts},v1={_sign(sub['secret'], ts, body)}",
    }
    status: Optional[int] = None
    try:
        resp = httpx.post(sub["url"], content=body.encode("utf-8"), headers=headers, timeout=TIMEOUT_S,
                          follow_redirects=False)
        status = resp.status_code
    except Exception as exc:  # noqa: BLE001 - 投递失败只记数，绝不外抛
        status = 0
        _record(sub["id"], 0, ok=False, error=str(exc)[:120])
        return
    _record(sub["id"], status, ok=200 <= status < 300)


def _record(sub_id: str, status: int, ok: bool, error: str = "") -> None:
    try:
        with _connect() as conn:
            if ok:
                conn.execute(
                    "UPDATE webhook_subs SET delivered_count = delivered_count + 1, fail_count = 0,"
                    " last_status = ?, last_delivery_at = ? WHERE id = ?",
                    (status, utc_now_iso(), sub_id),
                )
            else:
                conn.execute(
                    "UPDATE webhook_subs SET fail_count = fail_count + 1, last_status = ?, last_delivery_at = ?,"
                    " active = CASE WHEN fail_count + 1 >= ? THEN 0 ELSE active END WHERE id = ?",
                    (status, utc_now_iso(), AUTO_DISABLE_FAILS, sub_id),
                )
            conn.commit()
    except sqlite3.Error:
        pass


def dispatch_event(event: str, data: dict) -> int:
    """对外分发入口：向订阅了该事件的活跃订阅投递（线程池异步）。返回投递任务数。"""
    if event not in EVENTS:
        return 0
    subs = _active_subs(event)
    if not subs:
        return 0
    payload = {"id": "evt_" + uuid.uuid4().hex[:16], "type": event,
               "created_at": utc_now_iso(), "data": data}
    queued = 0
    for sub in subs:
        err = _url_guard_error(sub["url"])
        if err:  # 订阅时合法、后来 DNS 指向内网等：跳过不投
            continue
        _pool.submit(_deliver, sub, event, payload)
        queued += 1
    return queued


def dispatch_message(record: Any) -> None:
    """post_message_hook 挂点：快讯入库 → news 事件。"""
    try:
        data = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
        dispatch_event("news", data)
    except Exception:  # noqa: BLE001 - 推送绝不拖垮消息主链路
        pass


def send_test(user_id: str, sub_id: str) -> dict:
    init_webhook_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM webhook_subs WHERE id = ? AND user_id = ?", (sub_id, user_id)).fetchone()
    if row is None:
        raise ValueError("订阅不存在或不属于当前账号")
    if not row["active"]:
        raise ValueError("订阅已停用（连续失败过多），删除后重建即可")
    payload = {"id": "evt_test_" + uuid.uuid4().hex[:8], "type": "test", "created_at": utc_now_iso(),
               "data": {"message": "这是稻草财经 Webhook 测试事件，收到即说明对接成功。"}}
    _pool.submit(_deliver, row, "test", payload)
    return {"ok": True, "detail": "测试事件已投递（异步），请到你的接收端确认；订阅的 last_status 稍后更新"}


# --------------------------------------------------------------------------- #
# HTTP 端点（JWT 鉴权）
# --------------------------------------------------------------------------- #
class WebhookCreateRequest(BaseModel):
    url: str
    events: list[str] = ["news"]
    description: str = ""


@router.get("")
async def list_webhooks(_user: dict = Depends(require_current_user)) -> dict:
    return {"webhooks": list_subscriptions(str(_user.get("sub") or "")), "supported_events": list(EVENTS)}


@router.post("")
async def create_webhook(body: WebhookCreateRequest, _user: dict = Depends(require_current_user)) -> dict:
    try:
        return create_subscription(str(_user.get("sub") or ""), body.url, body.events, body.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{sub_id}")
async def delete_webhook(sub_id: str, _user: dict = Depends(require_current_user)) -> dict:
    if not delete_subscription(str(_user.get("sub") or ""), sub_id):
        raise HTTPException(status_code=404, detail="订阅不存在或不属于当前账号")
    return {"ok": True}


@router.post("/{sub_id}/test")
async def test_webhook(sub_id: str, _user: dict = Depends(require_current_user)) -> dict:
    try:
        return send_test(str(_user.get("sub") or ""), sub_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
