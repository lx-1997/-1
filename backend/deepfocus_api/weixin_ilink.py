from __future__ import annotations

import base64
import os
import secrets
from typing import Any, Dict, Optional

import httpx

"""
微信 iLink AI 机器人（OpenClaw 协议）最小客户端 —— 文本版。

用途：DeepFocus「微信扫码即问」多租户问答。每个 DeepFocus 用户扫码绑定一个
独立 bot（绑定关系另由 weixin_bind 存储维护：deepfocus_user ↔ ilink_bot/token）。
本模块只封装 iLink 的 4 个核心 HTTP 调用，文本收发，MVP 不含媒体/CDN/AES。

⚠️ 边界（已实测确认，见 [[wechat-push-channel]] 记忆）：
- 纯 PULL：send_text 必带 context_token，且 token 来自用户入站消息——给不了主动推送。
- 身份是冒充官方 openclaw-weixin 客户端（iLink-App-Id: bot），规模化有封禁风险。
- 直连 ilinkai：本环境沙箱出网代理封外域，故全程 trust_env=False 走直连（已验证可达）。
"""

ILINK_BASE = os.getenv("DEEPFOCUS_ILINK_BASE", "https://ilinkai.weixin.qq.com").rstrip("/")
BOT_TYPE = os.getenv("DEEPFOCUS_ILINK_BOT_TYPE", "3")
# semver 2.1.1 编码：((2&0xff)<<16)|((1&0xff)<<8)|(1&0xff) = 131329，与 openclaw-weixin 2.1.1 对齐
_CLIENT_VERSION = os.getenv("DEEPFOCUS_ILINK_CLIENT_VERSION", "131329")
_APP_ID = os.getenv("DEEPFOCUS_ILINK_APP_ID", "bot")

LONGPOLL_TIMEOUT_S = 35.0
DEFAULT_TIMEOUT_S = 15.0


def _common_headers() -> Dict[str, str]:
    """扫码/状态轮询共用头（与官方插件 GET 一致）。"""
    return {
        "iLink-App-Id": _APP_ID,
        "iLink-App-ClientVersion": _CLIENT_VERSION,
    }


def _random_uin() -> str:
    return base64.b64encode(str(secrets.randbelow(0xFFFFFFFF)).encode()).decode()


def _auth_headers(token: str, uin: Optional[str] = None) -> Dict[str, str]:
    """已绑定 bot 的认证头（getupdates / sendmessage / getconfig）。"""
    return {
        **_common_headers(),
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": uin or _random_uin(),
    }


def _client(timeout: float) -> httpx.AsyncClient:
    # trust_env=False：绕开沙箱出网代理直连 ilinkai（外域经代理会 403）。
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def push_client(timeout: float = DEFAULT_TIMEOUT_S) -> httpx.AsyncClient:
    """批量推送复用的客户端：单连接池（keep-alive），整批 send_text 共用一条，免每条新建连接。"""
    return httpx.AsyncClient(
        timeout=timeout, trust_env=False,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
    )


async def get_bot_qrcode() -> Dict[str, Any]:
    """请求一张绑定二维码。返回 {qrcode, qrcode_img_content, ret}。"""
    async with _client(DEFAULT_TIMEOUT_S) as c:
        r = await c.get(
            f"{ILINK_BASE}/ilink/bot/get_bot_qrcode",
            params={"bot_type": BOT_TYPE},
            headers=_common_headers(),
        )
        r.raise_for_status()
        return r.json()


async def get_qrcode_status(qrcode: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """轮询扫码状态。status ∈ wait/scaned/scaned_but_redirect/confirmed/expired。
    confirmed 时带 {bot_token, ilink_bot_id, baseurl, ilink_user_id}。
    base_url 用于 scaned_but_redirect 后切就近节点。"""
    base = (base_url or ILINK_BASE).rstrip("/")
    async with _client(LONGPOLL_TIMEOUT_S + 5) as c:
        r = await c.get(
            f"{base}/ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            headers=_common_headers(),
        )
        r.raise_for_status()
        return r.json()


async def get_updates(token: str, base_url: str, sync_buf: str = "", uin: Optional[str] = None) -> Dict[str, Any]:
    """长轮询收消息。返回 {msgs, get_updates_buf/sync_buf, ret/errcode}。
    errcode == -14 表示会话过期（需重新扫码绑定）。"""
    base = (base_url or ILINK_BASE).rstrip("/")
    async with _client(LONGPOLL_TIMEOUT_S + 5) as c:
        r = await c.post(
            f"{base}/ilink/bot/getupdates",
            headers=_auth_headers(token, uin),
            json={"get_updates_buf": sync_buf or "", "base_info": {"channel_version": "deepfocus"}},
        )
        r.raise_for_status()
        return r.json()


async def send_text(
    token: str,
    base_url: str,
    to_user_id: str,
    context_token: str,
    text: str,
    uin: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """回一条文本。⚠️ context_token 必带，且须来自该用户最近一次入站消息。
    client 不为空则复用该连接（批量推送共用一条 keep-alive 连接，免每条新建）。"""
    base = (base_url or ILINK_BASE).rstrip("/")
    payload = {
        "msg": {
            "to_user_id": to_user_id,
            "client_id": secrets.token_hex(16),
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        },
        "base_info": {"channel_version": "deepfocus"},
    }
    url = f"{base}/ilink/bot/sendmessage"
    headers = _auth_headers(token, uin)
    if client is not None:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()
    async with _client(DEFAULT_TIMEOUT_S) as c:
        r = await c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


async def get_config(
    token: str,
    base_url: str,
    ilink_user_id: str,
    context_token: str,
    uin: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """getconfig：用 context_token 探一次会话配置（完全不可见，不给用户发任何东西）。
    返回 {ret, typing_ticket?, ...}。用作「保活探针」——定期调以试图维持 context_token 活性，
    并据 ret 判断 token 是否仍有效。ret==0 视为有效。"""
    base = (base_url or ILINK_BASE).rstrip("/")
    url = f"{base}/ilink/bot/getconfig"
    headers = _auth_headers(token, uin)
    payload = {"ilink_user_id": ilink_user_id, "context_token": context_token}
    if client is not None:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()
    async with _client(DEFAULT_TIMEOUT_S) as c:
        r = await c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


def extract_inbound_text(msg: Dict[str, Any]) -> str:
    """从一条入站消息里取拼接的文本（仅 type==1 文本项）。"""
    parts = []
    for it in msg.get("item_list") or []:
        if it.get("type") == 1:
            t = (it.get("text_item") or {}).get("text")
            if t:
                parts.append(t)
    return " ".join(parts).strip()


def format_news_push(msgs: list) -> str:
    """快讯推送文本：每条一行 `MM-DD HH:MM  内容`（北京时间，无序号/无大标题）+ 一行免责。"""
    from datetime import datetime as _d, timezone as _z, timedelta as _t
    CN = _z(_t(hours=8))

    def hm(iso: str) -> str:
        try:
            return _d.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(CN).strftime("%m-%d %H:%M")
        except Exception:
            return ""

    TAG = {"warning": "利空", "success": "利好"}  # 与网站一致：中性(info)不标

    def _line(m):
        t = hm(getattr(m, "created_at", ""))
        tag = TAG.get((getattr(m, "severity", "") or ""), "")
        title = (getattr(m, "title", "") or "").strip()[:200]
        prefix = f"{t} 【{tag}】 " if tag else f"{t}  "
        return (prefix + title).strip()

    lines = [_line(m) for m in (msgs or [])[:10]]
    return "\n\n".join(x for x in lines if x)


def qr_png_data_url(content: str) -> Optional[str]:
    """把 qrcode_img_content（通常是 liteapp URL）渲染成 PNG data URL，前端 <img> 直接显示。
    缺 qrcode 依赖则返回 None（前端可退回自渲染 qr_content）。"""
    try:
        import io
        import qrcode  # 延迟导入；未装则优雅降级
        img = qrcode.make(content)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
