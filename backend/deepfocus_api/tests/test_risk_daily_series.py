"""Account risk needs a real daily series, never a set of closed trades."""
import json
import math

import pytest

from deepfocus_api import risk_management as risk
from deepfocus_api.schemas import RiskSummaryResponse, VaRResult


UNKNOWN_FIELDS = ("historical_var", "parametric_var", "var_amount", "var_pct", "mean_return", "std_dev", "sharpe_ratio")


@pytest.fixture
def isolated_risk_store(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "DB_PATH", tmp_path / "risk.sqlite3")
    return risk


def _assert_unavailable(result):
    assert result["method"] == "daily_series_unavailable"
    assert result["data_available"] is False
    assert result["observations"] == 0
    assert "每日" in result["reason"] and "出入金" in result["reason"]
    assert all(result[field] is None for field in UNKNOWN_FIELDS)
    model = VaRResult(**result)
    assert model.data_available is False and model.sharpe_ratio is None
    encoded = model.model_dump() if hasattr(model, "model_dump") else model.dict()
    json.dumps(encoded, allow_nan=False)


def test_many_same_day_closed_trade_returns_are_not_a_daily_account_series(isolated_risk_store):
    # Enough trades for the old >=5 shortcut, with diverse large returns all
    # recorded on one day. They do not describe daily account NAV or its risk.
    for number, exit_price in enumerate([5, 40, 9, 15, 25, 7, 11, 30]):
        position = risk.create_position(symbol="DEMO" + str(number), entry_price=10, quantity=1, owner_user_id="alice")
        risk.close_position(position["id"], exit_price, owner_user_id="alice")
    records = risk.list_pnl_records(owner_user_id="alice")
    assert len(records) == 8 and len({row["date"] for row in records}) == 1
    assert any(row["return_pct"] > 0 for row in records) and any(row["return_pct"] < 0 for row in records)
    result = risk.get_risk_summary(owner_user_id="alice")
    assert result["closed_positions_count"] == 8
    _assert_unavailable(result["var"])
    # With no open positions the complete existing response schema also accepts
    # unknown risk values; null must survive API serialization rather than be 0.
    response = RiskSummaryResponse(**result)
    assert response.var.std_dev is None and response.var.var_pct is None


def test_open_positions_and_empty_history_do_not_claim_zero_daily_risk(isolated_risk_store):
    empty = risk.get_risk_summary(owner_user_id="alice")
    _assert_unavailable(empty["var"])
    position = risk.create_position(symbol="DEMO", entry_price=10, quantity=10, owner_user_id="alice")
    risk.update_position(position["id"], current_price=11, owner_user_id="alice")
    result = risk.get_risk_summary(owner_user_id="alice")
    assert result["portfolio"]["total_value"] == 110
    assert result["portfolio"]["total_cost"] == 100
    assert result["portfolio"]["total_pnl"] == 10
    assert result["closed_positions_count"] == 0
    _assert_unavailable(result["var"])


def test_real_daily_returns_still_use_existing_var_calculation():
    returns = [.01, -.02, .005, -.015, .003, .009, -.004, .02, .007, -.01]
    mean = sum(returns) / len(returns)
    std = math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))
    result = risk.calculate_var(returns, confidence=.95, position_value=10000)
    assert result["observations"] == 10 and result["method"] == "parametric"
    assert result["historical_var"] == 2
    assert result["parametric_var"] == round(abs(mean - 1.645 * std) * 100, 2)
    assert result["var_amount"] == round(abs(mean - 1.645 * std) * 10000, 2)
    assert result["std_dev"] == round(std * 100, 4)
    assert result["sharpe_ratio"] == round(mean / std * math.sqrt(252), 4)
    assert VaRResult(**result).data_available is True
