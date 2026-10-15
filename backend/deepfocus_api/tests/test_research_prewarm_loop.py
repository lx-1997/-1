"""Run the incremental scheduler/runtime with a persisted-queue API double."""
import asyncio
from types import SimpleNamespace

import pytest

from deepfocus_api import (
    metrics_store,
    pdf_brand,
    research_archive,
    research_digest,
    research_multi_agent,
    research_prewarm,
    research_prewarm_policy,
    research_prewarm_signal,
    stock_name_index,
)
from deepfocus_api.analysis_limiter import AnalysisLimiter
from deepfocus_api.research_cache import deep_draft_cache_key, is_valid_deep_draft_cache


REAL_QUEUE_API = {name: getattr(research_archive, name) for name in (
    "observe_prewarm_feed", "list_prewarm_pending", "mark_prewarm_done",
)}


def report(fid):
    return {"file_id": fid, "title": fid + " 行业研究", "filename": fid + ".pdf"}


def deep_payload(fid="report"):
    return {
        "mode": "deep_draft", "title": fid + " 行业研究",
        "subject": "行业", "one_liner": "原文核心结论", "executive_summary": "原文摘要",
        "confidence": 0.7, "pages_analyzed": 18, "provider": "test-model",
        "instruments": ["600000.SH"],
        "sections": [{"id": "s1", "title": "需求", "summary": "原文需求摘要", "evidence": [{"page": 3, "excerpt": "原文证据"}]}],
        "source_coverage": {"pages_read": 18, "chars_read": 1234, "source_count": 1},
    }


def canonical(fid):
    return deep_draft_cache_key(SimpleNamespace(file_id=fid))


@pytest.fixture
def loop_harness(monkeypatch):
    real_sleep = asyncio.sleep
    state = SimpleNamespace(
        wire=[], archive=[], local=set(), store={}, writes=[], pdf_calls=[],
        resolve_calls=[], model_calls=[], wire_calls=0, used=0, increments=[],
        wire_error=None, pdf_errors={}, model_overrides={}, rounds=1, waits=[],
        block_model=False, model_started=None, model_closed=None, limiter=None,
        initialized=True, seen=set(), queue={}, marks=[], mark_snapshots=[],
        wire_snapshots=None, stop_at=1,
    )
    for key, value in {
        "DEEPFOCUS_RESEARCH_PREWARM": "1",
        "DEEPFOCUS_RESEARCH_PREWARM_PER_CYCLE": "6",
        "DEEPFOCUS_RESEARCH_PREWARM_GAP_SECONDS": "0",
        "DEEPFOCUS_RESEARCH_PREWARM_CYCLE_SECONDS": "60",
        "DEEPFOCUS_RESEARCH_PREWARM_JITTER_RATIO": "0",
        "DEEPFOCUS_RESEARCH_PREWARM_DAILY_MAX": "40",
    }.items():
        monkeypatch.setenv(key, value)

    async def sleep(_seconds):
        await real_sleep(0)

    async def fetch_wire(**kwargs):
        assert kwargs == {"limit": 200, "use_cache": False}
        state.wire_calls += 1
        if state.wire_error is not None:
            raise state.wire_error
        if state.wire_snapshots is not None:
            return {"items": list(state.wire_snapshots[min(state.wire_calls - 1, len(state.wire_snapshots) - 1)])}
        return {"items": list(state.wire)}

    def observe_feed(items):
        # The real SQLite behavior is covered separately by archive tests.
        # This double persists across separate run() calls to model a restart.
        if not state.initialized:
            state.seen.update(item["file_id"] for item in state.archive)
            state.seen.update(item["file_id"] for item in items)
            state.initialized = True
            return {"initialized": True, "enqueued": 0}
        state.seen.update(state.queue)
        added = 0
        for item in items:
            fid = item["file_id"]
            if fid not in state.seen:
                state.seen.add(fid)
                state.queue[fid] = dict(item)
                added += 1
        return {"initialized": False, "enqueued": added}

    def mark_done(fid, status="done"):
        state.marks.append((fid, status))
        state.mark_snapshots.append((fid, status, set(state.store)))
        state.queue.pop(fid, None)

    async def fetch_pdf(fid, filename):
        state.pdf_calls.append((fid, filename))
        if fid in state.pdf_errors:
            raise state.pdf_errors[fid]
        state.local.add(fid)
        return b"pdf", filename

    async def resolve(request):
        assert request.max_pages == 18
        state.resolve_calls.append(request.file_id)
        return [request.file_id]

    async def generate(request, *, documents, background):
        assert documents == [request.file_id] and background is True
        state.model_calls.append(request.file_id)
        if state.model_started is not None:
            state.model_started.set()
        if state.block_model:
            try:
                await asyncio.Event().wait()
            finally:
                await real_sleep(0)
                state.model_closed.set()
        return state.model_overrides.get(request.file_id, deep_payload(request.file_id))

    def set_cache(key, payload):
        state.writes.append(key)
        state.store[key] = payload

    def incr(key, *args, **kwargs):
        assert key == "research_pdf_dl"
        state.used += 1
        state.increments.append(key)

    async def wait_arrival(delay):
        state.waits.append(delay)
        if len(state.waits) >= state.stop_at:
            raise asyncio.CancelledError()
        return False

    monkeypatch.setattr(research_prewarm.asyncio, "sleep", sleep)
    def should_not_scan_archive():
        raise AssertionError("incremental scheduler must never scan/requeue the old archive")

    monkeypatch.setattr(research_archive, "list_all", should_not_scan_archive, raising=False)
    monkeypatch.setattr(research_archive, "observe_prewarm_feed", observe_feed, raising=False)
    monkeypatch.setattr(research_archive, "list_prewarm_pending", lambda: list(state.queue.values()), raising=False)
    monkeypatch.setattr(research_archive, "mark_prewarm_done", mark_done, raising=False)
    monkeypatch.setattr(stock_name_index, "exact_a_share_names", lambda: frozenset({"聚灿光电"}))
    monkeypatch.setattr(pdf_brand, "has_cached_file_id", lambda fid: fid in state.local)
    monkeypatch.setattr(metrics_store, "get_ai_cache", state.store.get)
    monkeypatch.setattr(metrics_store, "get_ai_cache_many", lambda keys: {key: state.store[key] for key in keys if key in state.store})
    monkeypatch.setattr(metrics_store, "set_ai_cache", set_cache)
    monkeypatch.setattr(metrics_store, "get_daily", lambda _key: state.used)
    monkeypatch.setattr(metrics_store, "incr", incr)
    monkeypatch.setattr(research_digest, "resolve_source_documents", resolve)
    monkeypatch.setattr(research_digest, "generate_deep_draft", generate)
    monkeypatch.setattr(research_prewarm_policy, "research_prewarm_download_cap", lambda daily_max, _hour: daily_max)
    monkeypatch.setattr(research_prewarm_signal, "wait_research_arrival", wait_arrival)

    async def run():
        state.stop_at = len(state.waits) + state.rounds
        state.limiter = AnalysisLimiter(2)
        await research_prewarm.run_research_prewarm_loop(
            fetch_wire=fetch_wire, fetch_pdf=fetch_pdf, limiter=state.limiter,
        )

    async def run_cycles():
        # The same cancellation used by application shutdown bounds this test.
        before = len(state.waits)
        with pytest.raises(asyncio.CancelledError):
            await run()
        assert len(state.waits) - before == state.rounds
        assert state.limiter._background == state.limiter._foreground == state.limiter._waiting == 0

    state.run = run
    state.run_cycles = run_cycles
    return state


@pytest.mark.asyncio
async def test_valid_canonical_is_ready_without_pdf_or_model(loop_harness, capsys):
    state = loop_harness
    state.wire = [report("ready")]
    state.archive = [report("ready")]
    state.store[canonical("ready")] = deep_payload("ready")
    await state.run_cycles()
    assert state.pdf_calls == state.resolve_calls == state.model_calls == []
    assert state.increments == []
    assert state.marks == [("ready", "done")]
    assert state.queue == {}
    assert "eligible=1 excluded=0 ready=1 pending=0 batch=0" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_pending_writes_deep_and_compact_before_ack_then_next_cycle_is_empty(loop_harness, capsys):
    state = loop_harness
    state.rounds = 2
    state.wire = [report("new")]
    await state.run_cycles()
    assert state.pdf_calls == [("new", "new.pdf")]
    assert state.resolve_calls == state.model_calls == ["new"]
    assert state.used == 1 and state.increments == ["research_pdf_dl"]
    assert is_valid_deep_draft_cache(state.store[canonical("new")])
    compact = state.store[research_multi_agent.analysis_cache_key("new")]
    assert state.store["new"] == compact
    assert compact["summary"] == "原文摘要"
    assert len(compact["logic_lines"]) == 1
    assert state.marks == [("new", "done")] and state.queue == {}
    assert {canonical("new"), research_multi_agent.analysis_cache_key("new"), "new"} <= state.mark_snapshots[0][2]
    assert state.waits == [30.0, 60.0]
    output = capsys.readouterr().out
    assert output.count("深度缓存完成 fid=new") == 1
    assert "eligible=0 excluded=0 ready=0 pending=0 batch=0" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", ["local-fallback", "text-only-fallback"])
async def test_fallback_is_not_done_or_cached_and_next_cycle_defers(loop_harness, capsys, fallback):
    state = loop_harness
    state.rounds = 2
    state.wire = [report("fallback")]
    state.model_overrides["fallback"] = {**deep_payload("fallback"), "provider": fallback}
    await state.run_cycles()
    assert state.model_calls == ["fallback"]
    assert state.store == {} and state.writes == []
    assert state.marks == [] and set(state.queue) == {"fallback"}
    assert state.used == 1
    assert state.waits == [60.0, 60.0]
    output = capsys.readouterr().out
    assert "深度缓存完成" not in output
    assert "ready=0 pending=1 batch=0" in output


@pytest.mark.asyncio
async def test_wire_failure_still_processes_persisted_queued_local_pdf(loop_harness, capsys):
    state = loop_harness
    state.wire_error = RuntimeError("feed unavailable")
    state.archive = [report("old-archive-local")]
    state.queue["queued-local"] = report("queued-local")
    state.local.update({"queued-local", "old-archive-local"})
    await state.run_cycles()
    assert state.model_calls == ["queued-local"]
    assert is_valid_deep_draft_cache(state.store[canonical("queued-local")])
    assert state.marks == [("queued-local", "done")]
    assert canonical("old-archive-local") not in state.store
    assert state.increments == []
    assert "最新列表暂不可用，继续待办增量" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_exhausted_daily_budget_keeps_warming_local_without_counting_download(loop_harness):
    state = loop_harness
    state.used = 40
    state.wire = [report("remote"), report("local")]
    state.local.add("local")
    await state.run_cycles()
    assert state.model_calls == ["local"]
    assert state.pdf_calls == [("local", "local.pdf")]
    assert state.used == 40 and state.increments == []
    assert canonical("remote") not in state.store
    assert state.marks == [("local", "done")] and set(state.queue) == {"remote"}
    assert is_valid_deep_draft_cache(state.store[canonical("local")])


@pytest.mark.asyncio
async def test_source_download_ban_allows_local_on_next_cycle_without_redownload(loop_harness, monkeypatch, capsys):
    state = loop_harness
    monkeypatch.setenv("DEEPFOCUS_RESEARCH_PREWARM_PER_CYCLE", "1")
    state.rounds = 2
    state.wire = [report("remote-banned")]
    state.queue["local-after-ban"] = report("local-after-ban")
    state.local.add("local-after-ban")
    state.pdf_errors["remote-banned"] = RuntimeError("源站拒绝下载，请下个自然日重试")
    await state.run_cycles()
    assert state.pdf_calls == [("remote-banned", "remote-banned.pdf"), ("local-after-ban", "local-after-ban.pdf")]
    assert state.model_calls == ["local-after-ban"]
    assert state.increments == [] and state.used == 0
    assert is_valid_deep_draft_cache(state.store[canonical("local-after-ban")])
    assert state.marks == [("local-after-ban", "done")]
    assert set(state.queue) == {"remote-banned"}
    assert "源站今日下载限额，继续处理本地PDF" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_shutdown_cancel_is_not_swallowed_and_awaits_provider_cleanup(loop_harness):
    state = loop_harness
    state.wire = [report("cancel")]
    state.block_model = True
    state.model_started, state.model_closed = asyncio.Event(), asyncio.Event()
    baseline = set(asyncio.all_tasks())
    task = asyncio.create_task(state.run())
    await asyncio.wait_for(state.model_started.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.model_closed.is_set()
    assert state.store == {} and state.waits == []
    assert state.marks == [] and set(state.queue) == {"cancel"}
    assert state.limiter._background == state.limiter._foreground == state.limiter._waiting == 0
    assert not [task for task in asyncio.all_tasks() - baseline if not task.done()]


@pytest.mark.asyncio
async def test_initial_snapshot_and_old_archive_never_generate_or_download(loop_harness):
    state = loop_harness
    state.initialized = False
    state.wire = [report("old-feed")]
    state.archive = [report("old-feed"), report("old-outside-feed")]
    state.local.update({"old-feed", "old-outside-feed"})
    await state.run_cycles()
    assert state.seen == {"old-feed", "old-outside-feed"}
    assert state.queue == {} and state.marks == []
    assert state.pdf_calls == state.model_calls == state.resolve_calls == []


@pytest.mark.asyncio
async def test_second_feed_filters_bare_company_date_and_only_generates_new_eligible(loop_harness):
    state = loop_harness
    state.initialized, state.rounds = False, 2
    old = report("old-feed")
    new = report("new-sector")
    excluded = {**report("new-company-date"), "title": "聚灿光电20261015"}
    state.archive = [old, report("old-outside-feed")]
    state.wire_snapshots = [[old], [old, new, excluded]]
    await state.run_cycles()
    assert state.pdf_calls == [("new-sector", "new-sector.pdf")]
    assert state.model_calls == ["new-sector"]
    assert state.marks == [("new-company-date", "excluded"), ("new-sector", "done")]
    assert state.queue == {}
    assert canonical("old-feed") not in state.store
    assert canonical("old-outside-feed") not in state.store
    assert canonical("new-company-date") not in state.store


@pytest.mark.asyncio
async def test_restart_reuses_seen_state_without_old_archive_backfill(loop_harness):
    state = loop_harness
    state.initialized = False
    state.wire = [report("baseline")]
    state.archive = [report("baseline"), report("old-outside-feed")]
    await state.run_cycles()
    # A new worker lifetime observes an additional report after the baseline.
    state.wire.append(report("after-enable"))
    await state.run_cycles()
    assert state.model_calls == ["after-enable"]
    assert state.marks == [("after-enable", "done")]
    # Another restart must neither regenerate acknowledged work nor backfill.
    state.wire.append(report("old-outside-feed"))
    await state.run_cycles()
    assert state.model_calls == ["after-enable"]
    assert state.queue == {}
    assert canonical("baseline") not in state.store
    assert canonical("old-outside-feed") not in state.store


@pytest.mark.asyncio
async def test_real_sqlite_boundary_filters_arrivals_and_survives_restart(loop_harness, tmp_path, monkeypatch):
    state = loop_harness
    monkeypatch.setattr(research_archive, "DB_PATH", tmp_path / "incremental.sqlite3")
    for name, function in REAL_QUEUE_API.items():
        monkeypatch.setattr(research_archive, name, function)
    research_archive.upsert([{**report("old-archive"), "id": "old-id"}])
    old = report("old-feed")
    bare = {**report("a-bare"), "title": "聚灿光电20261013"}
    hk = {**report("hk-arrival"), "title": "汇量科技20261013"}
    theme = {**report("a-theme"), "title": "聚灿光电业绩点评20261013"}
    state.wire_snapshots = [[old], [old, bare, hk, theme]]
    state.rounds = 2
    await state.run_cycles()
    assert set(state.model_calls) == {"hk-arrival", "a-theme"}
    assert {fid for fid, _ in state.pdf_calls} == {"hk-arrival", "a-theme"}
    assert research_archive.list_prewarm_pending() == []
    with research_archive._connect() as conn:
        statuses = dict(conn.execute("SELECT file_id,status FROM research_prewarm_seen"))
    assert statuses == {"old-archive": "baseline", "old-feed": "baseline", "a-bare": "excluded",
                        "hk-arrival": "done", "a-theme": "done"}
    state.rounds = 1
    await state.run_cycles()
    assert len(state.model_calls) == 2
