"""Independent regression checks; all training data here is explicitly fictional."""
import base64
from copy import deepcopy
import csv
from datetime import date, timedelta
import io
import json

import pytest

from deepfocus_api import research_factors as factors
from deepfocus_api.research_templates import research_template


@pytest.fixture(scope="module")
def fictional_prices():
    # The downloadable example exercises software only, not market alpha.
    return list(csv.DictReader(io.StringIO(research_template("factors").lstrip("\ufeff"))))


def _file(text, file_name="factors.csv"):
    return {"file_name": file_name, "content_base64": base64.b64encode(text.encode("utf-8")).decode()}


@pytest.mark.parametrize("count", [180, 181, 239, 240, 365, 1000])
def test_five_observation_labels_never_cross_split_boundaries(count):
    days = [date(2020, 1, 1) + timedelta(days=offset) for offset in range(count)]
    days = [day.isoformat() for day in days]
    splits = factors.chronological_segments(days, horizon=5)
    index = {day: i for i, day in enumerate(days)}
    assert index[splits["train"][1]] + 5 < index[splits["valid"][0]]
    assert index[splits["valid"][1]] + 5 < index[splits["test"][0]]
    assert index[splits["test"][1]] + 5 == len(days) - 1
    assert index[splits["train"][1]] < index[splits["valid"][0]] < index[splits["valid"][1]]


def test_real_qlib_lightgbm_metrics_labels_order_and_future_isolation(fictional_prices, monkeypatch, tmp_path):
    """Spy on real fit/predict; never replace Qlib expressions or the trained model."""
    import numpy as np
    import pandas as pd
    from pandas.testing import assert_frame_equal
    from qlib.contrib.model.gbdt import LGBModel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    original_fit, original_predict = LGBModel.fit, LGBModel.predict
    captured = []

    def capture_fit(self, dataset, *args, **kwargs):
        snapshot = {segment: dataset.prepare(segment, col_set=["feature", "label"]).copy(deep=True) for segment in ("train", "valid", "test")}
        captured.append(snapshot)
        output = original_fit(self, dataset, *args, **kwargs)
        assert self.model.num_trees() > 0
        assert self.model.num_trees() <= 50
        return output

    def capture_predict(self, dataset, *args, **kwargs):
        output = original_predict(self, dataset, *args, **kwargs)
        captured[-1]["prediction"] = output.copy()
        return output

    monkeypatch.setattr(LGBModel, "fit", capture_fit)
    monkeypatch.setattr(LGBModel, "predict", capture_predict)
    base = factors.analyze_factors({"prices": deepcopy(fictional_prices), "is_demo": True})
    _, symbols, days, _ = factors._validate({"prices": fictional_prices})
    boundaries = factors.chronological_segments(days)
    positions = {day: i for i, day in enumerate(days)}
    closes = {(row["symbol"], row["date"]): float(np.float32(row["close"])) for row in fictional_prices}
    first = captured[0]
    for segment in ("train", "valid", "test"):
        frame = first[segment]
        assert len(frame) == len(symbols) * (positions[boundaries[segment][1]] - positions[boundaries[segment][0]] + 1)
        assert np.isfinite(frame["feature"].to_numpy()).all()
        for (day, symbol), row in frame.iterrows():
            actual_day = day.date().isoformat()
            forward_day = days[positions[actual_day] + 5]
            expected = closes[symbol, forward_day] / closes[symbol, actual_day] - 1
            assert row["label", "forward_5d"] == pytest.approx(expected, abs=2e-7)
    predictions = first["prediction"]
    labels = first["test"]["label"]["forward_5d"].reindex(predictions.index)
    independent_mse = float(((predictions - labels) ** 2).mean())
    summary = {item["label"]: item["value"] for item in base["summary"]}
    assert summary["测试均方误差"] == pytest.approx(independent_mse, abs=5.1e-9)
    paired = pd.DataFrame({"prediction": predictions, "label": labels})
    independent_ic = paired.groupby(level="datetime").apply(lambda rows: rows["prediction"].corr(rows["label"])).mean()
    independent_rank_ic = paired.groupby(level="datetime").apply(lambda rows: rows["prediction"].corr(rows["label"], method="spearman")).mean()
    assert summary["测试期 IC"] == pytest.approx(independent_ic, abs=5.1e-9)
    assert summary["测试期 Rank IC"] == pytest.approx(independent_rank_ic, abs=5.1e-9)
    assert base["engine"].startswith("Qlib ") and "LightGBM" in base["engine"]
    assert any("虚构" in warning for warning in base["warnings"])
    json.dumps(base, allow_nan=False)

    reversed_report = factors.analyze_factors({"prices": list(reversed(deepcopy(fictional_prices))), "is_demo": True})
    for segment in ("train", "valid", "test"):
        assert_frame_equal(first[segment], captured[1][segment], check_exact=True)
    assert base["summary"] == reversed_report["summary"]
    assert base["charts"] == reversed_report["charts"]

    # Only rows after the final training label endpoint change. Validation/test
    # may change, but neither training labels, features nor train-only imputation may.
    future = deepcopy(fictional_prices)
    future_start = boundaries["valid"][0]
    for row in future:
        if row["date"] >= future_start:
            multiplier = 1.3 + .1 * symbols.index(row["symbol"])
            for field in ("open", "high", "low", "close"):
                row[field] = float(row[field]) * multiplier
            row["volume"] = float(row["volume"]) * 2
    factors.analyze_factors({"prices": future, "is_demo": True})
    assert_frame_equal(first["train"], captured[2]["train"], check_exact=True)
    assert not first["test"]["feature"].equals(captured[2]["test"]["feature"])


def test_missing_individual_date_intersection_never_fills_prices(fictional_prices):
    prices = deepcopy(fictional_prices)
    removed = prices.pop(5 * 50 + 1)
    selected, symbols, days, excluded = factors._validate({"prices": prices})
    assert removed["date"] not in days
    assert not any(row["date"] == removed["date"] for row in selected)
    assert len(days) == 239 and len(selected) == 239 * len(symbols)
    assert excluded == 4  # Four supplied peers are dropped, no missing row invented.


@pytest.mark.parametrize("mutation,match", [
    (lambda rows: rows.append(deepcopy(rows[0])), "重复"),
    (lambda rows: rows[0].update(symbol="../../OTHER"), "证券代码"),
    (lambda rows: rows[0].update(symbol=""), "证券代码"),
    (lambda rows: rows[0].update(high="1"), "高低价"),
    (lambda rows: rows[0].update(low="100000"), "高低价"),
    (lambda rows: rows[0].update(close="nan"), "有限"),
    (lambda rows: rows[0].update(volume="inf"), "有限"),
    (lambda rows: rows[0].update(open="0"), "正值"),
    (lambda rows: rows[0].update(volume="-1"), "有限"),
    (lambda rows: rows[0].pop("volume"), "volume"),
    (lambda rows: rows[0].update(date="2024-01-06"), "日期"),
    (lambda rows: rows[0].update(date="2100-01-04"), "日期"),
    (lambda rows: rows[0].update(date="2024-02-30"), "日期"),
])
def test_input_rejects_invalid_security_price_date_or_numeric(fictional_prices, mutation, match):
    rows = deepcopy(fictional_prices)
    mutation(rows)
    with pytest.raises(ValueError, match=match):
        factors._validate({"prices": rows})


def test_zero_volume_is_a_valid_observation(fictional_prices):
    rows = deepcopy(fictional_prices)
    rows[0]["volume"] = 0
    selected, _, _, _ = factors._validate({"prices": rows})
    assert selected[0]["volume"] == 0


@pytest.mark.parametrize("prices,match", [([], "请输入"), ([None], "对象"), ([{}], "证券"), ([{}] * 5001, "5000")])
def test_empty_malformed_or_too_many_rows(prices, match):
    with pytest.raises(ValueError, match=match):
        factors._validate({"prices": prices})


def test_cross_section_and_history_minimums(fictional_prices):
    with pytest.raises(ValueError, match="5–20"):
        factors._validate({"prices": [row for row in fictional_prices if row["symbol"] != "DEMO5"]})
    first_days = sorted({row["date"] for row in fictional_prices})[:179]
    with pytest.raises(ValueError, match="180"):
        factors._validate({"prices": [row for row in fictional_prices if row["date"] in first_days]})
    too_many = [dict(row, symbol="DEMO" + str(number)) for number in range(21) for row in fictional_prices if row["symbol"] == "DEMO1"]
    # Keep within 5000 rows so this checks the 20-security bound itself.
    too_many = [row for row in too_many if row["date"] in first_days]
    with pytest.raises(ValueError, match="5–20"):
        factors._validate({"prices": too_many})


@pytest.mark.parametrize("data,match", [
    ({"content_base64": "not%%%base64"}, "编码"),
    ({"content_base64": base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode()}, "2 MiB"),
    (_file(""), "CSV"),
])
def test_file_corruption_and_size_limits(data, match):
    with pytest.raises(ValueError, match=match):
        factors._records(data)


def test_bom_tsv_and_gb18030_csv_round_trip(fictional_prices):
    fields = ["date", "symbol", "open", "high", "low", "close", "volume", "name"]
    for delimiter, encoding in [("\t", "utf-8-sig"), (",", "gb18030")]:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows([dict(row, name="虚构演示") for row in fictional_prices])
        selected, symbols, days, excluded = factors._validate({"file_name": "演示.csv", "content_base64": base64.b64encode(buffer.getvalue().encode(encoding)).decode()})
        assert len(selected) == 1200 and len(symbols) == 5 and len(days) == 240 and excluded == 0


def test_weekly_data_cannot_claim_a_five_trading_day_label(fictional_prices):
    rows = deepcopy(fictional_prices)
    old_days = sorted({row["date"] for row in rows})
    remap = {day: (date(2018, 1, 1) + timedelta(weeks=i)).isoformat() for i, day in enumerate(old_days)}
    for row in rows:
        row["date"] = remap[row["date"]]
    with pytest.raises(ValueError, match="日|频|间隔"):
        factors._validate({"prices": rows})


def test_duplicate_header_cannot_silently_replace_close():
    text = "date,symbol,open,high,low,close,close,volume\n2024-01-02,DEMO1,10,20,9,10,20,100\n"
    with pytest.raises(ValueError, match="表头|重复|CSV"):
        factors._records(_file(text))
