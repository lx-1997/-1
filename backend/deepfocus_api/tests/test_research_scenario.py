from decimal import Decimal
import pytest
from deepfocus_api.research_scenario import calculate


def test_explicit_annual_assumptions_use_decimal_math():
    result = calculate('0.1', '3', 2027, 'USD', share_basis_confirmed=True, reference_price='0.2', reference_currency='USD')
    assert result['conditional_price'] == '0.3'
    assert Decimal(result['relative_to_snapshot_pct']) == 50
    assert result['kind'] == 'user_assumption'


@pytest.mark.parametrize('eps', [None, True, 'NaN', 'Infinity', '0', '-1', '1e10'])
def test_pe_method_rejects_unusable_annual_earnings(eps):
    with pytest.raises(ValueError):
        calculate(eps, '20', 2027, 'USD', share_basis_confirmed=True)


def test_currency_and_share_units_are_not_inferred():
    with pytest.raises(ValueError):
        calculate('6', '20', 2027, 'USD')
    result = calculate('6', '20', 2027, 'HKD', share_basis_confirmed=True, reference_price=100, reference_currency='USD')
    assert result['conditional_price'] == '120'
    assert result['relative_to_snapshot_pct'] is None


@pytest.mark.parametrize('year,currency,pe', [(True, 'USD', '20'), (2027, 'GBP', '20'), (2027, 'USD', '0'), (2027, 'USD', '-2'), (2027, 'USD', '1e7')])
def test_invalid_model_inputs_are_rejected(year, currency, pe):
    with pytest.raises(ValueError):
        calculate('6', pe, year, currency, share_basis_confirmed=True)
