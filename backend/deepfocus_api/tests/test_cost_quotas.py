from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.responses import StreamingResponse

from deepfocus_api import cost_quotas as quotas


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))


def test_parallel_reservations_cannot_exceed_budget():
    def attempt(_):
        try:
            return quotas.reserve("account-a", 3)
        except quotas.QuotaExceeded:
            return None
    with ThreadPoolExecutor(max_workers=20) as pool:
        leases = [lease for lease in pool.map(attempt, range(30)) if lease]
    assert len(leases) == quotas.used("account-a") == 3
    assert quotas.used("account-b") == 0
    for lease in leases:
        assert quotas.complete(lease)
        assert not quotas.complete(lease)
        quotas.release(lease)
    assert quotas.used("account-a") == 3


def test_legacy_consumption_is_imported_once_and_release_restores_slot():
    lease = quotas.reserve("legacy", 3, initial_consumed=2)
    with pytest.raises(quotas.QuotaExceeded):
        quotas.reserve("legacy", 3, initial_consumed=0)
    quotas.release(lease)
    replacement = quotas.reserve("legacy", 3, initial_consumed=999)
    assert quotas.complete(replacement)
    assert quotas.used("legacy") == 3


@pytest.mark.asyncio
async def test_error_and_clarification_release_reserved_slot():
    @quotas.quota_scope
    async def failing():
        quotas.reserve("fail", 1)
        raise RuntimeError("model failed")
    with pytest.raises(RuntimeError):
        await failing()
    assert quotas.used("fail") == 0

    @quotas.quota_scope
    async def clarify():
        quotas.reserve("clarify", 1)
        return {"needs_clarification": True}
    await clarify()
    assert quotas.used("clarify") == 0


@pytest.mark.asyncio
async def test_sse_disconnect_releases_lease_created_inside_generator():
    @quotas.quota_scope
    async def endpoint():
        async def body():
            quotas.reserve("stream", 1)
            yield "status"
            yield "final"
        return StreamingResponse(body())
    response = await endpoint()
    assert await response.body_iterator.__anext__() == "status"
    assert quotas.used("stream") == 1
    await response.body_iterator.aclose()
    assert quotas.used("stream") == 0


@pytest.mark.asyncio
async def test_successful_stream_charged_once():
    @quotas.quota_scope
    async def endpoint():
        lease = quotas.reserve("success", 1)
        async def body():
            quotas.complete(lease)
            yield "final"
        return StreamingResponse(body())
    response = await endpoint()
    assert [chunk async for chunk in response.body_iterator] == ["final"]
    assert quotas.used("success") == 1


def test_quota_storage_error_does_not_start_costly_work(tmp_path, monkeypatch):
    from deepfocus_api import main
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path))  # a directory cannot be opened as a DB
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        main._reserve_cost_quota("blocked", 1)
    assert caught.value.status_code == 503
