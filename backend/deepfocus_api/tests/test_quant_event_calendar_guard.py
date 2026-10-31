import copy
import hashlib
from pathlib import Path

import pytest
from deepfocus_api import quant_event_replay as engine, quant_dividend_book as book
from deepfocus_api.quant_calendar import CalendarAlignmentError
from deepfocus_api.quant_research_engine import digest
from deepfocus_api.tests.test_quant_event_replay import fixture, PARAM


@pytest.mark.parametrize('symbol,index', [('000333', 0), ('000333', 50), ('000333', -1),
                                         ('000300', 0), ('000300', 50), ('000300', -1)])
def test_missing_first_middle_or_final_day_refuses_intersection(symbol, index):
    data, _, _ = fixture(dividend=.01); del data[symbol]['bars'][index]; before = copy.deepcopy(data)
    with pytest.raises(CalendarAlignmentError) as failure:
        engine.replay(data, ['000333'], PARAM, [], {}, investor=book.INVESTOR)
    assert failure.value.report['reason'] == 'series_dates_differ'
    assert failure.value.report['intersection_trimming'] is False
    assert data == before


def test_missing_tail_rejected_before_account_creation_or_progress(monkeypatch):
    data, _, _ = fixture(dividend=.01); data['000333']['bars'] = data['000333']['bars'][:61]
    def forbidden(*args, **kwargs): raise AssertionError('Must not begin account or timeline')
    monkeypatch.setattr(book, 'initial_book', forbidden)
    with pytest.raises(CalendarAlignmentError):
        engine.replay(data, ['000333'], PARAM, [], {}, investor=book.INVESTOR, progress=forbidden)


def test_gap_on_resume_preserves_existing_held_positions_and_risk_state():
    data, _, days = fixture(dividend=.01)
    prefix = engine.replay(data, ['000333'], PARAM, [], {}, investor=book.INVESTOR, end=days[40])
    assert prefix['state']['book']['lots']
    state = prefix['state']; before = copy.deepcopy(state)
    data['000333']['bars'] = data['000333']['bars'][:61]
    with pytest.raises(CalendarAlignmentError):
        engine.replay(data, ['000333'], PARAM, [], {}, investor=book.INVESTOR, state=state)
    assert state == before


def test_aligned_active_path_completes_and_restores_with_new_kernel():
    data, event, days = fixture(); sources = {event.source_sha256: b'unit-test-fixture-not-an-actual-disclosure'}
    def replay(**kw): return engine.replay(data, ['000333'], PARAM, [event], sources, investor=book.INVESTOR, **kw)
    full = replay(); prefix = replay(end=days[32]); suffix = replay(state=prefix['state'])
    assert prefix['state']['book']['lots'] and full['fills']
    assert prefix['curve'] + suffix['curve'] == full['curve']
    assert prefix['fills'] + suffix['fills'] == full['fills'] and suffix['state'] == full['state']
    assert full['curve'][-1]['date'] == days[-1] and full['version'] == 'event-replay-development-v4'
    assert not full['gate']['passed']


def test_kernel_fingerprint_includes_alignment_dependency():
    root = Path(engine.__file__).parent
    names = ['quant_event_replay.py', 'quant_event_policy.py', 'quant_dividend_book.py', 'quant_corporate_actions.py',
             'quant_research_engine.py', 'ai_fund_ledger.py', 'ai_fund_policy.py', 'quant_calendar.py']
    assert engine.KERNEL == digest({name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names})


def test_control_and_training_archives_pin_the_alignment_source():
    from deepfocus_api import quant_event_controls, quant_event_training
    expected = hashlib.sha256(Path(engine.__file__).with_name('quant_calendar.py').read_bytes()).hexdigest()
    assert quant_event_controls.source_pins()['quant_calendar.py'] == expected
    assert quant_event_training.source_pins()['quant_calendar.py'] == expected


@pytest.mark.parametrize('data', [None, [], {}, {'000333': {}}])
def test_malformed_input_refuses_before_iterating_scope(data):
    with pytest.raises(CalendarAlignmentError):
        engine.replay(data, ['000333'], PARAM, [], {}, investor=book.INVESTOR)
