from datetime import datetime, timezone
import asyncio
import copy

import httpx
import pytest

from deepfocus_api import agent_tools, research_harness as rh
from deepfocus_api.research_evidence import check_tool_evidence

NOW = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)
ARGS = {"symbol": "GOOGL", "market": "US"}


def finance(**changes):
    return {"symbol": "GOOGL", "currency": "USD", "provider": "eastmoney",
            "report_date": "2026-09-30", "published_date": "2026-10-29",
            "period_start": "2026-01-01", "period_basis": "累计季报",
            "net_income": -5, "eps": 0, "revenue": 100, **changes}


def check(data, tool="get_financials", args=None):
    return check_tool_evidence(tool, args or ARGS, {"ok": True, "data": data}, now=NOW)


def test_actual_period_zero_loss_and_input_unchanged():
    original = finance()
    result = check(original)
    assert result["evidence_check"]["status"] == "usable"
    assert result["data"]["net_income"] == -5
    assert result["data"]["eps"] == 0
    assert result["data"]["period_basis"] == "累计季报"
    assert result["evidence_check"]["independently_verified"] is False
    assert original == finance()


@pytest.mark.parametrize("changes,issue", [
    ({"symbol": "GOOG"}, "issuer_mismatch"),
    ({"report_date": "2027-01-01"}, "future_report_date"),
    ({"published_date": "2026-09-01"}, "publication_before_period_end"),
    ({"period_start": "2026-10-01"}, "period_reversed"),
    ({"report_date": "not-a-date"}, "invalid_report_date"),
])
def test_bad_identity_and_dates_are_quarantined(changes, issue):
    result = check(finance(**changes))
    assert result["data"] is None
    assert result["evidence_check"]["status"] == "rejected"
    assert issue in result["evidence_check"]["issues"]
    assert check_tool_evidence("get_financials", ARGS, result, now=NOW) == result


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), {"value": 5}, "100亿元"])
def test_invalid_numeric_field_is_removed_without_erasing_valid_financials(value):
    result = check(finance(revenue=value))
    assert result["data"]["revenue"] is None
    assert result["data"]["net_income"] == -5
    assert result["evidence_check"]["fields"]["revenue"]["status"] == "rejected"


def test_cap_is_not_pe_and_ratings_are_not_earnings_forecast():
    result = check({"market_cap": 100, "currency": "USD", "provider": "nasdaq"}, "get_valuation")
    audit = result["evidence_check"]
    assert audit["fields"]["pe_ratio"]["status"] == "missing"
    assert audit["fields"]["market_cap"]["value"] == 100
    assert "as_of_missing" in audit["issues"]
    result = check({"rating": "Buy", "analyst_count": 5}, "get_analyst_consensus")
    assert result["evidence_check"]["fields"]["forecast_eps_this_year"]["status"] == "missing"
    assert result["evidence_check"]["fields"]["target_price"]["status"] == "missing"


def test_same_hk_issuer_can_report_in_cny_and_missing_metadata_stays_missing():
    data = finance(symbol="700.HK", currency="CNY", published_date=None, period_basis=None)
    result = check(data, args={"symbol": "00700", "market": "HK"})
    assert result["data"]["currency"] == "CNY"
    assert result["evidence_check"]["status"] == "limited"
    assert "publication_date_missing_not_point_in_time" in result["evidence_check"]["issues"]
    assert result["data"]["published_date"] is None


def test_wrong_quote_cannot_be_used_and_duplicates_are_rejected():
    row = {"symbol": "GOOG", "price": 12, "currency": "USD"}
    assert check({"quotes": [row]}, "get_market_quote")["data"] is None
    row["symbol"] = "GOOGL"
    assert check({"quotes": [row, row]}, "get_market_quote")["data"] is None


def test_cn_naive_market_time_is_local_and_delayed_status_preserved():
    result = check({"quotes": [{"symbol": "600519", "price": 100,
                   "currency": "CNY", "market_time": "2026-11-02 15:00:00",
                   "fetched_at": "2026-11-02T08:00:00Z", "provider": "test",
                   "is_realtime": False}]}, "get_market_quote", {"symbol": "600519", "market": "CN"})
    assert result["data"]["quotes"][0]["price"] == 100
    assert result["evidence_check"]["is_realtime"] is False


def test_stale_report_is_historical_and_never_promoted_by_fresh_fetch():
    result = check(finance(report_date="2023-09-30", period_start="2023-01-01",
                           published_date="2023-10-29", fetched_at="2026-11-02T08:00:00Z"))
    assert "historical_report_only" in result["evidence_check"]["issues"]
    assert result["evidence_check"]["status"] == "limited"


@pytest.mark.asyncio
async def test_explicit_timeout_gets_one_retry_and_both_attempts_are_retained(monkeypatch):
    calls = []
    async def execute(tool, args, **kw):
        calls.append(kw["ifind_user"])
        if len(calls) == 1:
            return {"ok": False, "error_type": "timeout"}
        return {"ok": True, "data": finance()}
    monkeypatch.setattr(rh, "execute_tool", execute)
    _, result, trace = await rh._call_with_flags("get_financials", ARGS, ifind_user=True)
    assert calls == [True, True]
    assert len(trace["attempts"]) == 2
    assert trace["attempts"][0]["error_type"] == "timeout"
    assert result["data"]["eps"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    {"ok": True, "data": None}, {"ok": False, "error_type": "not_authorized"},
    {"ok": True, "data": finance(symbol="GOOG")},
])
async def test_no_retry_for_no_data_denied_or_invalid_facts(monkeypatch, result):
    calls = []
    async def execute(*args, **kw):
        calls.append(1)
        return copy.deepcopy(result)
    monkeypatch.setattr(rh, "execute_tool", execute)
    await rh._call_with_flags("get_financials", ARGS)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancel_is_propagated_without_retry(monkeypatch):
    async def execute(*args, **kw):
        raise asyncio.CancelledError()
    monkeypatch.setattr(rh, "execute_tool", execute)
    with pytest.raises(asyncio.CancelledError):
        await rh._call_with_flags("get_financials", ARGS)


@pytest.mark.asyncio
async def test_retry_itself_is_bounded(monkeypatch):
    budgets = []
    async def execute(*args, **kw):
        return {"ok": False, "error_type": "timeout"}
    async def wait_for(awaitable, timeout):
        budgets.append(timeout)
        return await awaitable
    monkeypatch.setattr(rh, "execute_tool", execute)
    monkeypatch.setattr(rh.asyncio, "wait_for", wait_for)
    _, result, trace = await rh._call_with_flags("get_financials", ARGS)
    assert budgets == [14.0, 4.0]
    assert result["evidence_check"]["status"] == "timeout"
    assert len(trace["attempts"]) == 2


@pytest.mark.asyncio
async def test_direct_tool_calls_are_checked_and_transport_failure_is_typed(monkeypatch):
    async def handler(**kwargs):
        return finance(symbol="GOOG")
    monkeypatch.setitem(agent_tools.TOOL_REGISTRY, "get_financials",
                        type("Tool", (), {"handler": staticmethod(handler)})())
    result = await agent_tools.execute_tool("get_financials", ARGS)
    assert result["data"] is None and result["evidence_check"]["status"] == "rejected"
    async def fail(**kwargs):
        raise httpx.ConnectError("private connection detail")
    monkeypatch.setattr(agent_tools.TOOL_REGISTRY["get_financials"], "handler", fail)
    result = await agent_tools.execute_tool("get_financials", ARGS)
    assert result["error_type"] == "transient"
    assert "private" not in result["error"]
