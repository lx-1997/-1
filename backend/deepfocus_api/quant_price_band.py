"""Exact price-band arithmetic for an explicitly DECLARED standard scenario.

No code-prefix inference, historical regime lookup, exchange-reference
certification, price-cage/auction/queue assessment or execution permission.
"""
from fractions import Fraction as F
import re

VERSION='declared-tick-price-band-v1'
RULES={'reference':'explicit_positive_tick_aligned_decimal_string',
       'percentage':'explicit_decimal_percent_strictly_between_0_and_100',
       'rounding':'positive_half_up_to_declared_tick',
       'minimum_distance':'at_least_one_tick_from_reference_before_minimum_price_floor',
       'minimum_price':'one_tick',
       'no_limit_or_unknown_regime':'not_supported_never_inferred_as_standard',
       'interpretation':'arithmetic_only_not_exchange_band_or_execution_certification',
       'promotion_allowed':False}


def scalar(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,8})?',value):
        raise ValueError('Bounded plain decimal string required')
    result=F(value)
    if result<=0:raise ValueError('Positive declared input required')
    return result


def half_up(value):
    q,r=divmod(value.numerator,value.denominator)
    return q+int(2*r>=value.denominator)


def decimal_string(value):
    # All accepted decimal scalars and integer tick multiples terminate.
    den=value.denominator;twos=fives=0
    while den%2==0:den//=2;twos+=1
    while den%5==0:den//=5;fives+=1
    if den!=1:raise ValueError('Nonterminating decimal output')
    places=max(twos,fives);scaled=value.numerator*(10**places//value.denominator)
    if not places:return str(scaled)
    raw=str(scaled).zfill(places+1)
    return (raw[:-places]+'.'+raw[-places:]).rstrip('0').rstrip('.')


def calculate(reference,percentage,*,tick,regime):
    if regime!='declared_standard':raise ValueError('Unknown/no-limit regime requires its own explicit rule')
    ref,pct,step=scalar(reference),scalar(percentage),scalar(tick)
    if pct>=100 or step>ref:raise ValueError('Invalid declared band percentage/tick')
    units=ref/step
    if units.denominator!=1:raise ValueError('Reference is not tick-aligned; do not silently round an ex-reference')
    n=units.numerator
    upper=half_up(F(n)*(1+pct/100));lower=half_up(F(n)*(1-pct/100))
    if abs(upper-n)<1:upper=n+1
    if abs(lower-n)<1:lower=n-1
    lower=max(1,lower);upper=max(1,upper)
    return {'version':VERSION,'reference':reference,'percentage':percentage,'tick':tick,'regime':regime,
            'lower':decimal_string(lower*step),'upper':decimal_string(upper*step),
            'exchange_reference_verified':False,'security_regime_verified':False,
            'order_or_fill_permission':False,'promotion_allowed':False}


def check_price(price,band):
    # Recalculate rather than trust caller-supplied bounds or permissions.
    expected=calculate(band['reference'],band['percentage'],tick=band['tick'],regime=band['regime'])
    if band!=expected:raise ValueError('Declared arithmetic band changed')
    value=scalar(price);step=scalar(band['tick'])
    on_tick=(value/step).denominator==1
    inside=F(band['lower'])<=value<=F(band['upper'])
    return {'on_tick':on_tick,'within_declared_arithmetic_band':on_tick and inside,'order_or_fill_permission':False}
