import asyncio

import pytest
from starlette.exceptions import HTTPException
from starlette.responses import StreamingResponse

from deepfocus_api import cost_quotas as quotas


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))


@pytest.mark.asyncio
async def test_unstarted_sse_iterator_close_releases_preflight_reservation():
    @quotas.quota_scope
    async def endpoint():
        quotas.reserve("unstarted", 1)
        async def body():
            yield "final"
        return StreamingResponse(body(), status_code=206, headers={"X-Test": "preserved"})

    response = await endpoint()
    assert response.status_code == 206
    assert response.headers["x-test"] == "preserved"
    assert quotas.used("unstarted") == 1
    await response.body_iterator.aclose()
    await response.body_iterator.aclose()
    assert quotas.used("unstarted") == 0
    assert response._quota_scope.heartbeat.done()


@pytest.mark.asyncio
async def test_response_header_disconnect_releases_reservation():
    @quotas.quota_scope
    async def endpoint():
        quotas.reserve("headers", 1)
        async def body():
            yield "final"
        return StreamingResponse(body())

    response = await endpoint()
    async def send(message):
        raise ConnectionResetError("disconnected before response headers")
    async def receive():
        await asyncio.Event().wait()

    with pytest.raises(BaseException):
        await response({"type": "http"}, receive, send)
    assert quotas.used("headers") == 0
    assert response._quota_scope.heartbeat.done()


@pytest.mark.asyncio
async def test_long_handler_renews_lease_and_keeps_budget_reserved(monkeypatch):
    now = [1000.0]
    renewed = asyncio.Queue()
    original_renew = quotas.renew
    monkeypatch.setattr(quotas.time, "time", lambda: now[0])
    monkeypatch.setattr(quotas, "_HEARTBEAT_SECONDS", 0.001)

    def observed_renew(lease):
        original_renew(lease)
        renewed.put_nowait(None)
    monkeypatch.setattr(quotas, "renew", observed_renew)

    @quotas.quota_scope
    async def endpoint():
        lease = quotas.reserve("waiting", 1, ttl_seconds=1)
        for _ in range(3):
            now[0] += 0.75
            await asyncio.wait_for(renewed.get(), timeout=1)
            with pytest.raises(quotas.QuotaExceeded):
                quotas.reserve("waiting", 1)
        assert quotas.complete(lease)
        return "done"

    assert await endpoint() == "done"
    assert quotas.used("waiting") == 1


@pytest.mark.asyncio
async def test_sse_keeps_lease_alive_between_body_iterations(monkeypatch):
    now = [1000.0]
    renewed = asyncio.Queue()
    original_renew = quotas.renew
    monkeypatch.setattr(quotas.time, "time", lambda: now[0])
    monkeypatch.setattr(quotas, "_HEARTBEAT_SECONDS", 0.001)

    def observed_renew(lease):
        original_renew(lease)
        renewed.put_nowait(None)
    monkeypatch.setattr(quotas, "renew", observed_renew)

    @quotas.quota_scope
    async def endpoint():
        lease = quotas.reserve("stream-waiting", 1, ttl_seconds=1)
        async def body():
            yield "status"
            assert quotas.complete(lease)
            yield "final"
        return StreamingResponse(body())

    response = await endpoint()
    assert await response.body_iterator.__anext__() == "status"
    for _ in range(3):
        now[0] += 0.75
        await asyncio.wait_for(renewed.get(), timeout=1)
        with pytest.raises(quotas.QuotaExceeded):
            quotas.reserve("stream-waiting", 1)
    assert [chunk async for chunk in response.body_iterator] == ["final"]
    assert response._quota_scope.heartbeat.done()
    assert quotas.used("stream-waiting") == 1


@pytest.mark.asyncio
async def test_caller_cancel_releases_scope_and_stops_heartbeat():
    started = asyncio.Event()
    @quotas.quota_scope
    async def endpoint():
        quotas.reserve("caller-cancel", 1)
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(endpoint())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert quotas.used("caller-cancel") == 0


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_handler_and_releases_lease(monkeypatch):
    monkeypatch.setattr(quotas, "_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(quotas, "renew", lambda lease: (_ for _ in ()).throw(OSError("store unavailable")))
    execution_closed = asyncio.Event()

    @quotas.quota_scope
    async def endpoint():
        quotas.reserve("failed-heartbeat", 1)
        try:
            await asyncio.Event().wait()
        finally:
            execution_closed.set()

    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(endpoint(), timeout=1)
    assert caught.value.status_code == 503
    assert execution_closed.is_set()
    assert quotas.used("failed-heartbeat") == 0


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_sse_generation(monkeypatch):
    monkeypatch.setattr(quotas, "_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(quotas, "renew", lambda lease: (_ for _ in ()).throw(OSError("store unavailable")))
    execution_closed = asyncio.Event()

    @quotas.quota_scope
    async def endpoint():
        async def body():
            quotas.reserve("failed-stream", 1)
            try:
                await asyncio.Event().wait()
                yield "never delivered"
            finally:
                execution_closed.set()
        return StreamingResponse(body())

    response = await endpoint()
    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(response.body_iterator.__anext__(), timeout=1)
    assert caught.value.status_code == 503
    assert execution_closed.is_set()
    assert quotas.used("failed-stream") == 0
    assert response._quota_scope.heartbeat.done()


def test_crashed_expired_lease_cannot_resume_or_charge(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(quotas.time, "time", lambda: now[0])
    crashed = quotas.reserve("crashed", 1, ttl_seconds=1)
    now[0] += 2
    replacement = quotas.reserve("crashed", 1)
    with pytest.raises(HTTPException):
        quotas.renew(crashed)
    with pytest.raises(HTTPException):
        quotas.complete(crashed)
    assert quotas.complete(replacement)
    assert quotas.used("crashed") == 1
