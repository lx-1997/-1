"""Request-level security controls for the public FastAPI surface."""

from __future__ import annotations

import ipaddress
import os
import time
from collections import defaultdict, deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    raw = os.getenv("DEEPFOCUS_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128")
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        try:
            if item.strip():
                networks.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _normalise_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return ""


def client_ip(request: Request) -> str:
    """Use forwarded addresses only when the immediate peer is trusted."""
    peer = (request.client.host if request.client else "") or ""
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        peer_addr = None
    trusted = bool(peer_addr and any(peer_addr in n for n in _trusted_proxy_networks()))
    if not trusted:
        return _normalise_ip(peer) or peer[:64] or "unknown"
    real = _normalise_ip(request.headers.get("x-real-ip", ""))
    if real:
        return real
    for item in reversed((request.headers.get("x-forwarded-for", "") or "").split(",")):
        value = _normalise_ip(item)
        if value:
            return value
    return _normalise_ip(peer) or peer[:64] or "unknown"


class _SlidingWindow:
    def __init__(self, max_keys: int = 8192) -> None:
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        self._max_keys = max_keys

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        if len(self._hits) > self._max_keys:
            for old_key in list(self._hits)[: len(self._hits) - self._max_keys]:
                self._hits.pop(old_key, None)
        return True


_LIMITER = _SlidingWindow()


def _is_public_path(path: str) -> bool:
    try:
        from .auth import is_public_path

        return is_public_path(path)
    except Exception:
        return False


def _is_seo_page(path: str) -> bool:
    exact = {"/stocks", "/stocks/all", "/articles", "/reports", "/notes", "/review", "/research"}
    return path in exact or path.startswith(("/stock/", "/article/", "/report/", "/note/", "/review/", "/research/"))


class SecurityMiddleware(BaseHTTPMiddleware):
    """Add browser protections, cap request bodies, and budget guest traffic."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > _env_int("DEEPFOCUS_MAX_REQUEST_BYTES", 50 * 1024 * 1024):
                    return self._secure(JSONResponse({"detail": "请求体过大"}, status_code=413), path)
            except ValueError:
                return self._secure(JSONResponse({"detail": "无效的 Content-Length"}, status_code=400), path)

        ip = client_ip(request)
        method = request.method.upper()
        if method == "POST" and path in {"/api/auth/login", "/api/auth/register"}:
            if not _LIMITER.allow(f"auth:{path}:{ip}", _env_int("DEEPFOCUS_AUTH_RPM", 60)):
                return self._secure(JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429, headers={"Retry-After": "60"}), path)

        if (_is_public_path(path) or _is_seo_page(path)) and not path.endswith("/stream"):
            env_name = "DEEPFOCUS_ANON_API_RPM" if path.startswith("/api/") else "DEEPFOCUS_SEO_RPM"
            default = 600 if path.startswith("/api/") else 240
            if not _LIMITER.allow(f"guest:{ip}", _env_int(env_name, default)):
                return self._secure(JSONResponse({"detail": "访问频率过高，请稍后再试"}, status_code=429, headers={"Retry-After": "60"}), path)

        return self._secure(await call_next(request), path)

    @staticmethod
    def _secure(response: Response, path: str) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith("/api/"):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        return response


__all__ = ["SecurityMiddleware", "client_ip"]
