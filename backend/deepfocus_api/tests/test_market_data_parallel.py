from __future__ import annotations

import asyncio

import pytest

from deepfocus_api import agent_tools, market_dashboard


def dashboard(key, value, timestamp):
    return {"generated_at": timestamp, "categories": [{"indicators": [
        {"key": key, "value": value, "change_pct": 0.3},
    ]}]}


GLOBAL = dashboard("gold", 4694.44, "2026-10-15T06:38:00Z")
ASHARE = dashboard("sse_composite", 3500, "2026-10-15T06:39:00Z")
EXTRA = {"int_nasdaq": [19000, 1.0], "int_dji": [43000, -0.2], "hf_SI": [60, 2.0],
         "rt_hkHSI": ["", "", "", "", "", "", "26000", "", "0.5"]}


@pytest.fixture
def install_sources(monkeypatch):
    def install(global_source, ashare_source, extra_source):
        monkeypatch.setattr(market_dashboard, "fetch_market_dashboard", global_source)
        monkeypatch.setattr(market_dashboard, "fetch_ashare_dashboard", ashare_source)
        monkeypatch.setattr(market_dashboard, "_fetch_sina_quotes", extra_source)
        monkeypatch.setattr(market_dashboard, "_parse_sina_fields", lambda kind, fields: {
            "price": fields[0], "change_pct": fields[1],
        })
    return install


def test_three_sources_overlap_without_changing_quote_order_or_timestamp(install_sources):
    async def run():
        entered = set()
        all_entered = asyncio.Event()
        release = asyncio.Event()

        def source(name, result):
            async def fetch(*args):
                entered.add(name)
                if len(entered) == 3:
                    all_entered.set()
                await release.wait()
                return result
            return fetch

        install_sources(source("global", GLOBAL), source("ashare", ASHARE), source("extra", EXTRA))
        task = asyncio.create_task(agent_tools._tool_get_market_data("黄金"))
        try:
            await asyncio.wait_for(all_entered.wait(), 0.5)
            assert not task.done() and entered == {"global", "ashare", "extra"}
            release.set()
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    result = asyncio.run(run())
    assert result["snapshot_generated_at_beijing"] == "2026-10-15T14:38:00+08:00"
    assert [row["name"] for row in result["quotes"]] == [
        "黄金(美元/盎司)", "上证指数", "纳斯达克综合指数", "道琼斯工业指数", "白银(美元/盎司)", "恒生指数",
    ]
    assert result["quotes"][-1] == {"name": "恒生指数", "price": 26000, "change_pct": 0.5}
    assert "抓取时刻，不是行情成交时刻" in result["note"]


@pytest.mark.parametrize("failed", ["global", "ashare", "extra"])
def test_one_source_failure_keeps_healthy_sibling_quotes(install_sources, failed):
    def source(name, result):
        async def fetch(*args):
            await asyncio.sleep(0)
            if name == failed:
                raise RuntimeError("source unavailable")
            return result
        return fetch

    install_sources(source("global", GLOBAL), source("ashare", ASHARE), source("extra", EXTRA))
    result = asyncio.run(agent_tools._tool_get_market_data("黄金"))
    names = {row["name"] for row in result["quotes"]}
    assert ("黄金(美元/盎司)" in names) == (failed != "global")
    assert ("上证指数" in names) == (failed != "ashare")
    assert ("恒生指数" in names) == (failed != "extra")
    expected_time = "2026-10-15T14:39:00+08:00" if failed == "global" else "2026-10-15T14:38:00+08:00"
    assert result["snapshot_generated_at_beijing"] == expected_time


def test_all_source_failures_keep_empty_result_semantics(install_sources):
    async def fail(*args):
        raise RuntimeError("unavailable")

    install_sources(fail, fail, fail)
    assert asyncio.run(agent_tools._tool_get_market_data()) is None


def test_cancellation_propagates_after_all_source_tasks_close(install_sources):
    async def run():
        entered = set()
        closed = set()
        all_entered = asyncio.Event()

        def source(name):
            async def fetch(*args):
                try:
                    entered.add(name)
                    if len(entered) == 3:
                        all_entered.set()
                    await asyncio.Event().wait()
                finally:
                    closed.add(name)
            return fetch

        install_sources(source("global"), source("ashare"), source("extra"))
        task = asyncio.create_task(agent_tools._tool_get_market_data())
        await asyncio.wait_for(all_entered.wait(), 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == entered == {"global", "ashare", "extra"}

    asyncio.run(run())


def test_supplementary_parse_failure_preserves_prior_rows(install_sources, monkeypatch):
    async def glob():
        return GLOBAL

    async def ashare():
        return ASHARE

    async def extra(*args):
        return EXTRA

    install_sources(glob, ashare, extra)

    def parse(kind, fields):
        if fields[0] == 43000:
            raise ValueError("bad second item")
        return {"price": fields[0], "change_pct": fields[1]}

    monkeypatch.setattr(market_dashboard, "_parse_sina_fields", parse)
    result = asyncio.run(agent_tools._tool_get_market_data())
    assert [row["name"] for row in result["quotes"]] == ["黄金(美元/盎司)", "上证指数", "纳斯达克综合指数"]
