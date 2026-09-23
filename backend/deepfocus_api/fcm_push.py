"""Firebase Cloud Messaging HTTP v1 adapter.

FCM is deliberately optional: without a Firebase service account the rest of the
recall pipeline continues to work and reports a clear ``skipped`` delivery.
Credentials are read only from environment variables (never committed):

* ``DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_FILE``
* ``DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_JSON``
* optional ``DEEPFOCUS_FIREBASE_PROJECT_ID``
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional


_SCOPES = ("https://www.googleapis.com/auth/firebase.messaging",)
_LOCK = threading.Lock()
_CREDENTIALS: Any = None
_PROJECT_ID = ""


def _service_account_info() -> Optional[dict[str, Any]]:
    raw = os.getenv("DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    path = os.getenv("DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def configured() -> bool:
    return bool(_service_account_info())


def _credentials() -> tuple[Any, str, str]:
    global _CREDENTIALS, _PROJECT_ID
    info = _service_account_info()
    if not info:
        return None, "", "FCM 未配置（需 Firebase service account）"
    project_id = (os.getenv("DEEPFOCUS_FIREBASE_PROJECT_ID", "").strip() or str(info.get("project_id") or "").strip())
    if not project_id:
        return None, "", "FCM 配置缺少 project_id"
    try:
        from google.oauth2 import service_account  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore
    except Exception:
        return None, "", "FCM 依赖未安装（需 google-auth）"
    with _LOCK:
        if _CREDENTIALS is None or _PROJECT_ID != project_id:
            _CREDENTIALS = service_account.Credentials.from_service_account_info(info, scopes=list(_SCOPES))
            _PROJECT_ID = project_id
        credentials = _CREDENTIALS
        if not credentials.valid or credentials.expired or not credentials.token:
            credentials.refresh(Request())
        return credentials, project_id, ""


def send_data_message(
    token: str,
    *,
    title: str,
    body: str,
    message_id: str,
    topic: str,
    severity: str,
    symbol: str,
    url: str,
    tracked_url: str,
    dedupe_id: str = "",
) -> tuple[str, str, bool]:
    """Send one high-priority data message.

    Returns ``(status, detail, permanent_failure)``. The caller uses the last
    flag to deactivate expired registration tokens (UNREGISTERED/404).
    """
    token = (token or "").strip()
    if not token:
        return "skipped", "FCM token 为空", True
    credentials, project_id, reason = _credentials()
    if credentials is None:
        return "skipped", reason, False
    try:
        import httpx  # type: ignore
    except Exception:
        return "skipped", "FCM 依赖未安装（需 httpx）", False

    payload = {
        "message": {
            "token": token,
            "data": {
                "message_id": str(message_id or ""),
                # Android 轮询兜底使用 report:/zsxq: 前缀，和同一条 FCM 消息共用
                # 去重键，避免「FCM 到达 + 90 秒轮询」重复弹两次。
                "dedupe_id": str(dedupe_id or message_id or "")[:200],
                "title": str(title or "资讯更新")[:240],
                "body": str(body or "")[:1600],
                "topic": str(topic or "快讯")[:80],
                "severity": str(severity or "info")[:20],
                "symbol": str(symbol or "")[:32],
                "url": str(url or ""),
                "track": str(tracked_url or ""),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
            "android": {
                "priority": "HIGH",
                "ttl": "120s",
                "direct_boot_ok": True,
            },
        }
    }
    endpoint = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    last_response = None
    last_exc: Optional[Exception] = None
    # FCM 偶发 429/5xx 或短暂网络抖动时做两次有界重试；永久 token 错误立即返回，
    # 不把单个坏 token 拖慢整轮召回。
    for attempt in range(3):
        try:
            response = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json"},
                json=payload,
                timeout=12,
            )
            last_response = response
            if 200 <= response.status_code < 300:
                return "sent", "FCM 已推送", False
            detail = response.text[:240] or f"HTTP {response.status_code}"
            try:
                error_code = str((response.json().get("error") or {}).get("status") or "")
            except Exception:
                error_code = ""
            permanent = response.status_code in (400, 404) and any(
                marker in f"{error_code} {detail}".upper()
                for marker in ("UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT")
            )
            retryable = response.status_code == 408 or response.status_code == 429 or response.status_code >= 500
            if permanent or not retryable:
                return "error", f"FCM 返回 {response.status_code}: {detail}", permanent
        except Exception as exc:  # noqa: BLE001 - 网络抖动走有界重试
            last_exc = exc
        if attempt < 2:
            time.sleep(0.6 * (2**attempt))
    if last_response is not None:
        detail = last_response.text[:240] or f"HTTP {last_response.status_code}"
        return "error", f"FCM 返回 {last_response.status_code}: {detail}", False
    return "error", f"FCM 请求失败：{last_exc}"[:240], False
