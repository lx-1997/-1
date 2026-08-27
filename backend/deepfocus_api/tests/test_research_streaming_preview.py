from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

from deepfocus_api import auth, ifind_api, main


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": [], "query_string": b""})


def test_temporary_preview_streams_original_pdf_without_buffering(monkeypatch):
    source = b"%PDF-1.7\noriginal-watermark\n%%EOF"
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/preview":
            return httpx.Response(200, json={"previewUrl": "/api/previews/temporary-id"})
        if request.url.path == "/api/previews/temporary-id":
            return httpx.Response(
                200,
                stream=_ChunkStream([source[:9], source[9:21], source[21:]]),
                headers={"content-type": "application/pdf", "content-length": str(len(source))},
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://workbench")
    monkeypatch.setattr(main, "_research_preview_http_client", lambda: client)
    monkeypatch.setattr(main, "zsxq_auth_payload", lambda: {"cookie": "test-cookie"})

    async def run() -> tuple[StreamingResponse, bytes]:
        response = await main._stream_research_online_pdf("fid-1", "original.pdf")
        body = b"".join([chunk async for chunk in response.body_iterator])
        return response, body

    response, body = asyncio.run(run())

    assert body == source
    assert paths == ["/api/preview", "/api/previews/temporary-id"]
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-research-preview"] == "transient-stream"
    assert client.is_closed is True


def test_temporary_preview_rejects_non_workbench_url(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"previewUrl": "https://example.com/report.pdf"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(main, "_research_preview_http_client", lambda: client)
    monkeypatch.setattr(main, "zsxq_auth_payload", lambda: {})

    with pytest.raises(Exception) as exc:
        asyncio.run(main._stream_research_online_pdf("fid-2", "report.pdf"))

    assert getattr(exc.value, "status_code", None) == 502
    assert client.is_closed is True


def test_wire_file_uses_stream_path_and_keeps_membership_gate(monkeypatch):
    monkeypatch.setattr(main, "_SERVE_RESEARCH_ORIGINAL", True)
    monkeypatch.setattr(main, "require_current_user", lambda request: {"sub": "u-test", "username": "paid-user"})
    monkeypatch.setattr(main, "membership_of_username", lambda value: {"tier": "premium"})
    monkeypatch.setattr(ifind_api, "allowed_usernames", lambda: set())
    monkeypatch.setattr(main, "metrics_incr_research", lambda *args: None)

    async def must_not_buffer(*args, **kwargs):
        raise AssertionError("buffering/cache path must not be used for online preview")

    expected = StreamingResponse(iter([b"%PDF-test"]), media_type="application/pdf")

    async def stream(*args, **kwargs):
        return expected

    monkeypatch.setattr(main, "_fetch_research_online_pdf", must_not_buffer)
    monkeypatch.setattr(main, "_stream_research_online_pdf", stream)

    main._PDF_TOKENS.clear()
    landing = asyncio.run(main.api_research_wire_file(_request(), file_id="fid-3", name="report.pdf"))
    assert landing.media_type == "text/html"
    assert b"location.replace" in landing.body
    token = next(iter(main._PDF_TOKENS))
    response = asyncio.run(
        main.api_research_wire_file(_request(), file_id="fid-3", name="report.pdf", token=token)
    )
    assert response is expected


def test_pdf_token_is_bound_and_single_use(monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 1000.0)
    main._PDF_TOKENS.clear()
    token = main._issue_pdf_token("fid-4", "bound.pdf", "paid-user")

    main._consume_pdf_token(token, "fid-4", "bound.pdf")
    with pytest.raises(Exception) as exc:
        main._consume_pdf_token(token, "fid-4", "bound.pdf")

    assert getattr(exc.value, "status_code", None) == 403


def test_auth_middleware_only_bypasses_wire_file_when_one_time_token_is_present(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    middleware = auth.AuthMiddleware(lambda scope, receive, send: None)
    calls: list[str] = []

    async def next_handler(request: Request) -> Response:
        calls.append(request.url.path)
        return Response(status_code=204)

    with_token = Request({
        "type": "http", "method": "GET", "path": "/api/research/wire-file",
        "headers": [], "query_string": b"file_id=fid&token=one-time",
    })
    without_token = Request({
        "type": "http", "method": "GET", "path": "/api/research/wire-file",
        "headers": [], "query_string": b"file_id=fid",
    })

    allowed = asyncio.run(middleware.dispatch(with_token, next_handler))
    blocked = asyncio.run(middleware.dispatch(without_token, next_handler))

    assert allowed.status_code == 204
    assert blocked.status_code == 401
    assert calls == ["/api/research/wire-file"]
