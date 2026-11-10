from datetime import date, timedelta
import pytest
from deepfocus_api.macro_insight_study import signals, evaluate, KEYS
from deepfocus_api.market_regime import simulate


def sample(n=600):
    days = []; day = date(2023, 1, 2)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return {k: [{'date': d, 'open': 100+i, 'close': 100+i, 'volume': 1000+i} for i, d in enumerate(days)] for k in KEYS}


def test_prefix_has_no_future_information():
    full = sample()
    prefix = signals({k: v[:300] for k, v in full.items()})
    result = signals(full)
    for rule in prefix:
        assert result[rule][:len(prefix[rule])] == prefix[rule]


def test_missing_session_is_rejected():
    data = sample(); del data['csi500'][100]
    with pytest.raises(ValueError, match='缺口'):
        signals(data)


def test_duplicate_dates_rejected():
    data = sample(); data['sse'][10] = data['sse'][9]
    with pytest.raises(ValueError, match='日期'):
        signals(data)


def test_today_signal_cannot_change_today_fill():
    rows = signals(sample())['trend'][:5]
    original = simulate(rows, 1, 4)
    altered = [dict(r) for r in rows]; altered[3]['temperature'] = 0
    assert simulate(altered, 1, 4) == original
    assert simulate(altered, 1, 5) != simulate(rows, 1, 5)


def test_historical_pass_cannot_enable_promotion():
    report = evaluate(sample())
    assert report['promotion_allowed'] is False
    assert len(report['candidates']) == 4
    assert all(c['promotion_allowed'] is False for c in report['candidates'])
    assert all(c['holdout']['cost_pct_initial'] > 0 for c in report['candidates'])


def test_public_report_does_not_open_analysis_or_subpaths():
    from deepfocus_api.auth import is_public_path
    assert is_public_path('/api/market-dashboard/insight-study')
    assert is_public_path('/api/market-dashboard/ashare')
    assert not is_public_path('/api/market-dashboard/analyze')
    assert not is_public_path('/api/market-dashboard/ashare/analyze')
    assert not is_public_path('/api/market-dashboard/insight-study/private')


def test_deployed_summary_matches_retained_json():
    import json
    from pathlib import Path
    from deepfocus_api.macro_insight_report import REPORT
    path = Path(__file__).parents[1] / 'data/macro_insight_study_v1.json'
    assert REPORT == json.loads(path.read_text())
