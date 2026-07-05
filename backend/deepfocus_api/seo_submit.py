"""搜索 / AI 引擎 URL 主动提交：百度主动推送 + IndexNow(Bing/Yandex)。

为什么要主动推送：新域名被动等爬极慢；主动推送把新 URL 直接塞进引擎抓取队列，是冷启动收录最快的路径。
设计原则：
- 全部 env 门控——无 token 即 no-op，绝不影响主流程。
- httpx trust_env=False 直连，绕开沙箱出网代理（与行情/翻译同款绕代理手法）。
- 吞掉所有异常，只返回结构化结果给调用方打日志。
- 去重 / 按天节流由调用方(main 的后台任务)用 data_store 持久化负责，本模块只管「把这批 URL 推出去」。
⚠️ 沙箱出网被封 → 本模块只能在生产环境真正验证。
"""
from __future__ import annotations

import os
from typing import Iterable

try:  # httpx 一定在依赖里，保险起见软导入
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore


def _base_url() -> str:
    return (os.getenv("DEEPFOCUS_PUBLIC_BASE_URL", "https://daocaijing.com").strip().rstrip("/")
            or "https://daocaijing.com")


def _host(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/")


def enabled() -> bool:
    """是否配置了任一推送渠道。"""
    return bool(
        os.getenv("DEEPFOCUS_BAIDU_PUSH_TOKEN", "").strip()
        or os.getenv("DEEPFOCUS_INDEXNOW_KEY", "").strip()
    )


def _baidu_push(urls: list[str]) -> dict:
    token = os.getenv("DEEPFOCUS_BAIDU_PUSH_TOKEN", "").strip()
    if not token or httpx is None or not urls:
        return {"skipped": True}
    site = os.getenv("DEEPFOCUS_BAIDU_PUSH_SITE", "").strip() or _base_url()
    api = f"http://data.zz.baidu.com/urls?site={_host(site)}&token={token}"
    try:
        r = httpx.post(
            api,
            content="\n".join(urls).encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            timeout=10.0,
            trust_env=False,
        )
        return {"ok": r.status_code == 200, "status": r.status_code, "resp": r.text[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def _indexnow(urls: list[str]) -> dict:
    key = os.getenv("DEEPFOCUS_INDEXNOW_KEY", "").strip()
    if not key or httpx is None or not urls:
        return {"skipped": True}
    base = _base_url()
    payload = {
        "host": _host(base),
        "key": key,
        "keyLocation": f"{base}/indexnow-key.txt",
        "urlList": urls[:10000],
    }
    try:
        r = httpx.post("https://api.indexnow.org/indexnow", json=payload, timeout=10.0, trust_env=False)
        return {"ok": r.status_code in (200, 202), "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def submit_urls(urls: Iterable[str]) -> dict:
    """把 URL 列表推给百度主动推送 + IndexNow（同步阻塞，调用方请用 to_thread 包一层）。

    各渠道独立 env 门控、独立 try/except，一个挂了不影响另一个；返回每渠道结果便于打日志。
    """
    clean = [u for u in dict.fromkeys(urls) if u]  # 去重保序
    if not clean:
        return {"count": 0, "baidu": {"skipped": True}, "indexnow": {"skipped": True}}
    return {"count": len(clean), "baidu": _baidu_push(clean), "indexnow": _indexnow(clean)}
