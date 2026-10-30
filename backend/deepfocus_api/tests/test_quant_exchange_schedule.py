import copy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from deepfocus_api import quant_exchange_schedule as schedule

ROOT = Path(__file__).resolve().parents[3]
REVIEW = ROOT/'docs/quant-reviewed-exchange-schedule-2023-2026-20261030.json'
CLOCK = '2026-10-30T16:00:00+00:00'


@pytest.fixture
def reviewed():
    manifest = json.loads(REVIEW.read_text())
    # Explicit synthetic HTML envelopes exercise integrity guards without
    # redistributing retained live HTML or pretending these are live notices.
    sources = {}
    for row in manifest['records']:
        raw = ('<html><title>unit test only</title>一、休市安排'
               +row['exchange']+str(row['year'])+' synthetic fixture 二、清算</html>').encode()
        sha = hashlib.sha256(raw).hexdigest()
        row['source_sha256'] = sha
        row['body_sha256'] = hashlib.sha256(schedule.notice_body(raw).encode()).hexdigest()
        sources[sha] = raw
    return manifest, sources


def test_reviewed_years_are_bounded_and_weekend_makeup_days_stay_closed(reviewed):
    manifest, sources = reviewed
    schedule.validate_manifest(manifest, sources, as_of=CLOCK)
    expected = schedule.planned_sessions(manifest, '2024-02-01', '2024-02-20')
    assert '2024-02-08' in expected and '2024-02-09' not in expected
    assert '2024-02-18' not in expected and '2024-02-19' in expected
    expected = schedule.planned_sessions(manifest, '2025-02-01', '2025-02-10')
    assert '2025-02-05' in expected and '2025-02-08' not in expected
    with pytest.raises(ValueError, match='outside'):
        schedule.planned_sessions(manifest, '2026-12-31', '2027-01-01')


@pytest.mark.parametrize('damage', ['source', 'body', 'facts', 'scope', 'missing', 'duplicate',
                                    'future', 'naive', 'overlap', 'reopen', 'makeup', 'disagree'])
def test_corrupt_evidence_identity_facts_clocks_and_scope_are_rejected(reviewed, damage):
    manifest, sources = copy.deepcopy(reviewed)
    row = manifest['records'][0]
    if damage == 'source':
        sources[row['source_sha256']] += b'changed'
    elif damage == 'body':
        row['body_sha256'] = '0'*64
    elif damage == 'facts':
        row['closed_ranges'][0][1] = '2023-01-01'
    elif damage == 'scope':
        manifest['unexpected_closure_search_complete'] = True
    elif damage == 'missing':
        manifest['records'].pop()
    elif damage == 'duplicate':
        manifest['records'][-1] = copy.deepcopy(row)
    elif damage == 'future':
        row['reviewed_at'] = '2099-01-01T00:00:00+00:00'
    elif damage == 'naive':
        row['available_at'] = '2022-12-27'
    elif damage == 'overlap':
        row['closed_ranges'][1] = row['closed_ranges'][0]
        row['resumes_on'][1] = row['resumes_on'][0]
        row['reviewed_facts_digest'] = schedule.facts_digest(row)
    elif damage == 'reopen':
        row['resumes_on'][0] = '2023-01-04'
        row['reviewed_facts_digest'] = schedule.facts_digest(row)
    elif damage == 'makeup':
        row['weekend_makeup_days_closed'][0] = '2023-01-30'
        row['reviewed_facts_digest'] = schedule.facts_digest(row)
    else:
        row['closed_ranges'][2] = ['2023-04-04', '2023-04-04']
        row['resumes_on'][2] = '2023-04-05'
        row['reviewed_facts_digest'] = schedule.facts_digest(row)
    with pytest.raises(ValueError):
        schedule.validate_manifest(manifest, sources, as_of=CLOCK)


def market(days, observed='2026-10-30T08:30:00+00:00'):
    payload = {'source': 'unit_test_fixture', 'adjustment': 'none', 'volume_unit': 'shares',
               'is_synthetic': False, 'fetched_at': observed,
               'bars': [{'date': d, 'open': 10., 'high': 11., 'low': 9., 'close': 10.,
                         'volume': 1000000} for d in days]}
    return {'600519': copy.deepcopy(payload), '000300': copy.deepcopy(payload)}


@pytest.mark.parametrize('position', [0, 10, -1])
def test_common_missing_first_middle_and_tail_detected_without_observed_bounds(reviewed, position):
    manifest, _ = reviewed
    days = schedule.planned_sessions(manifest, '2026-09-01', '2026-10-30')
    removed = days.pop(position)
    data = market(days)
    original = copy.deepcopy(data)
    report = schedule.check_dataset(manifest, data, '2026-09-01', '2026-10-30', as_of=CLOCK)
    assert report['status'] == 'rejected' and data == original
    assert all(r['missing_planned_sessions'] == [removed] for r in report['symbols'].values())
    assert all(not r['actual_exchange_sessions_certified'] for r in report['symbols'].values())


def test_intraday_capture_does_not_require_uncompleted_day_but_close_boundary_does(reviewed):
    manifest, _ = reviewed
    days = schedule.planned_sessions(manifest, '2026-09-01', '2026-10-29')
    data = market(days, '2026-10-30T08:29:59+00:00')
    report = schedule.check_dataset(manifest, data, '2026-09-01', '2026-10-30', as_of=CLOCK)
    assert report['status'] == 'matches_plan' and report['completed_through'] == '2026-10-29'
    for payload in data.values():
        payload['fetched_at'] = '2026-10-30T08:30:00+00:00'
    report = schedule.check_dataset(manifest, data, '2026-09-01', '2026-10-30', as_of=CLOCK)
    assert report['status'] == 'rejected' and report['completed_through'] == '2026-10-30'


def test_holiday_extra_session_and_unordered_dates_not_accepted(reviewed):
    manifest, _ = reviewed
    days = schedule.planned_sessions(manifest, '2026-09-01', '2026-10-30')
    days = sorted(days+['2026-10-01'])
    result = schedule.compare_dates(manifest, days, '2026-09-01', '2026-10-30')
    assert result['status'] == 'rejected' and result['unexpected_sessions'] == ['2026-10-01']
    with pytest.raises(ValueError):
        schedule.compare_dates(manifest, list(reversed(days)), '2026-09-01', '2026-10-30')


@pytest.mark.parametrize('clock', ['2026-10-30', '2099-01-01T00:00:00+00:00'])
def test_unknown_or_future_capture_clock_rejected(reviewed, clock):
    manifest, _ = reviewed
    data = market(schedule.planned_sessions(manifest, '2026-09-01', '2026-10-29'), clock)
    with pytest.raises(ValueError):
        schedule.check_dataset(manifest, data, '2026-09-01', '2026-10-30', as_of=CLOCK)


@pytest.mark.parametrize('damage', ['outside', 'overwrite', 'empty'])
def test_cli_rejects_paths_overwrite_and_empty_scope(tmp_path, reviewed, damage):
    manifest, sources = reviewed
    manifest['source_files'] = {}
    for i, (sha, raw) in enumerate(sources.items()):
        name = f'{i}.html'
        (tmp_path/name).write_bytes(raw)
        manifest['source_files'][sha] = '../outside.html' if damage == 'outside' else name
    cal, experiments, output = [tmp_path/n for n in ('calendar.json', 'experiments.json', 'out.json')]
    cal.write_text(json.dumps(manifest)); experiments.write_text('[]')
    if damage == 'overwrite':
        output.write_text('preserved prior report')
    result = subprocess.run([sys.executable, str(ROOT/'scripts/quant-exchange-calendar-review.py'),
        '--calendar', str(cal), '--evidence-dir', str(tmp_path), '--experiments', str(experiments),
        '--output', str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    if damage == 'overwrite':
        assert output.read_text() == 'preserved prior report'
    else:
        assert not output.exists()
