"""Deterministic annual-EPS assumption × PE assumption, never a forecast."""
from decimal import Decimal, InvalidOperation, localcontext


def calculate(annual_eps, pe, fiscal_year, currency, *, share_basis_confirmed=False,
              reference_price=None, reference_currency=None):
    if share_basis_confirmed is not True:
        raise ValueError("请确认年度EPS与交易标的的每股单位和币种一致")
    if type(fiscal_year) is not int or not 1900 <= fiscal_year <= 2200:
        raise ValueError("请填写明确的盈利年度")
    if currency not in {"CNY", "HKD", "USD"}:
        raise ValueError("请明确假设币种")
    try:
        eps, multiple = Decimal(str(annual_eps)), Decimal(str(pe))
    except (InvalidOperation, ValueError):
        raise ValueError("EPS和PE必须是有限正数") from None
    if not eps.is_finite() or not multiple.is_finite() or not 0 < eps <= Decimal('1e9') or not 0 < multiple <= Decimal('1e6'):
        raise ValueError("PE法仅支持有限正EPS和正PE；不能用亏损或零EPS估价")
    with localcontext() as context:
        context.prec = 40
        price = eps * multiple
        relative = None
        try:
            reference = Decimal(str(reference_price))
            if reference.is_finite() and reference > 0 and reference_currency == currency:
                relative = str((price / reference - 1) * 100)
        except (InvalidOperation, ValueError):
            pass
        return {"annual_eps": format(eps.normalize(), 'f'), "pe": format(multiple.normalize(), 'f'), "fiscal_year": fiscal_year,
                "currency": currency, "conditional_price": format(price.normalize(), 'f'),
                "reference_price": str(reference_price) if relative is not None else None,
                "relative_to_snapshot_pct": relative, "kind": "user_assumption",
                "notice": "用户年度EPS假设 × PE假设；不自动年化累计/单季EPS。相对快照的变化不是收益预测或实现概率。"}
