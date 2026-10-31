import copy
import hashlib

import pytest
from deepfocus_api import quant_research_sample as sampler


def codes(): return ['sz.' + str(i).zfill(6) for i in range(100)]


def test_selection_is_independent_of_input_order_and_matches_independent_hash_sort():
    c = codes(); before = c[:]
    a = sampler.sample(c, '2023-10-26', '2026-10-29')
    assert a == sampler.sample(c[::-1], '2023-10-26', '2026-10-29') and c == before
    expected = sorted(c, key=lambda value: (hashlib.sha256(('daocaijing-internal-research-universe-v1:2023-10-26:' + value).encode()).digest(), value))[:64]
    assert [q['code'] for q in a] == expected and len(a) == 64
    assert all(q['fields'] == sampler.FIELDS and q['adjustflag'] == '3' for q in a)


def test_no_extra_status_or_return_input_and_no_replacement_rule():
    assert sampler.RULES['current_status_filter'] is False and sampler.RULES['tradestatus_filter'] is False
    assert sampler.RULES['replace_failures'] is False and sampler.RULES['promotion_allowed'] is False
    assert sampler.RULES['new_unseen_validation'] is False


@pytest.mark.parametrize('bad', [[], codes()[:63], codes() + codes()[:1], ['sh.600001'] * 64, ['bj.800001'] + codes(), ['sz.200001'] + codes(), ['sz.000001 '] + codes(), [True] + codes()])
def test_invalid_or_undersized_population_refused_without_fallback(bad):
    with pytest.raises(ValueError): sampler.sample(bad, '2023-10-26', '2026-10-29')


@pytest.mark.parametrize('start,end', [('2026-10-29', '2023-10-26'), ('2023-10-26', '2023-10-26'), ('bad', '2026-10-29')])
def test_invalid_interval_refused(start, end):
    with pytest.raises(ValueError): sampler.sample(codes(), start, end)


@pytest.mark.parametrize('mutation', ['replacement', 'size', 'status_filter', 'query', 'adjustment', 'promotion'])
def test_plan_mutation_refused(mutation):
    p = {'schema': 'historical-research-sample-v1', 'rules': copy.deepcopy(sampler.RULES),
         'eligible_codes': codes(), 'start': '2023-10-26', 'end': '2026-10-29',
         'requests': sampler.sample(codes(), '2023-10-26', '2026-10-29')}
    assert len(sampler.validate_plan(p)) == 64
    if mutation == 'replacement': p['rules']['replace_failures'] = True
    if mutation == 'size': p['rules']['size'] = 63
    if mutation == 'status_filter': p['rules']['current_status_filter'] = True
    if mutation == 'query': p['requests'][0]['code'] = 'sz.999999'
    if mutation == 'adjustment': p['requests'][0]['adjustflag'] = '2'
    if mutation == 'promotion': p['rules']['promotion_allowed'] = True
    with pytest.raises(ValueError): sampler.validate_plan(p)
