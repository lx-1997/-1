"""财务报表多源取数 + 回退链 + 现金流八类型/含金量/好生意计算(忠于《价值投资之长线牛股》第4/9/10章)。"""
import asyncio

from deepfocus_api import eastmoney_data
from deepfocus_api import financial_statements as fs


def _run(coro):
    return asyncio.run(coro)


def _wire_eastmoney(monkeypatch, perf, cf, bal=None):
    async def _e(symbol, market=None):
        return perf

    async def _c(symbol, market=None):
        return cf

    async def _b(symbol, market=None):
        return bal
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_earnings", _e)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_cashflow", _c)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_balance", _b)
    fs._CACHE.clear()


# 茅台式样本：成熟现金奶牛
_PERF = {"eps": 21.56, "deduct_eps": 21.5, "ocf_per_share": 21.49, "gross_margin": 89.76,
         "roe": 22.0, "revenue": 5.47e10, "net_income": 2.72e10,
         "revenue_yoy": 11.0, "profit_yoy": 12.0, "report_date": "2026-03-31", "name": "贵州茅台"}
_CF = {"ocf": 2.69e10, "icf": -5.0e9, "fcf_financing": -1.0e10, "capex": 6.0e8, "report_date": "2026-03-31"}


# 茅台式资产负债表：应收≈0、预收>应收
_BAL = {"accounts_receivable": 3.2e7, "advance_receipts": 3.0e9, "inventory": 6e10,
        "equity": 2.7e11, "report_date": "2026-03-31"}


def test_eastmoney_normalization_and_quality(monkeypatch):
    _wire_eastmoney(monkeypatch, _PERF, _CF, _BAL)
    s = _run(fs.fetch_statements("600519", "CN"))
    assert s["provider"] == "eastmoney"
    assert s["ocf"] == 2.69e10 and s["fcf"] == 2.69e10 - 6.0e8       # 自由现金流 = 经营 − capex
    assert s["cashflow_type"]["type"] == 4                           # (+,-,-) 现金奶牛
    assert s["earnings_quality"]["score"] is not None
    assert s["earnings_quality"]["detail"]["ar_ratio"] < 0.01        # 应收占营收≈0(茅台)
    assert s["good_business"]["score"] is not None
    assert any("预收>应收" in n for n in s["good_business"]["notes"])  # 第1.6 回款能力强


def test_high_receivable_flagged(monkeypatch):
    # 应收占营收过高 → 盈利质量扣分 + flag
    _wire_eastmoney(monkeypatch, _PERF, _CF,        # 应收 2.5e10 / 营收 5.47e10 ≈ 0.46 > 0.4 阈值
                    {"accounts_receivable": 2.5e10, "advance_receipts": 1e8, "inventory": 2e10})
    s = _run(fs.fetch_statements("000651", "CN"))
    assert any("应收账款占营收过高" in f for f in s["earnings_quality"]["flags"])


def test_cashflow_eight_type_signs(monkeypatch):
    # (+,-,+) 高速成长期：主业盈利但扩张靠融资
    _wire_eastmoney(monkeypatch, {**_PERF, "roe": 6.0},
                    {"ocf": 3.0e9, "icf": -2.0e10, "fcf_financing": 2.3e10, "capex": 2.2e10})
    s = _run(fs.fetch_statements("002594", "CN"))
    assert s["cashflow_type"]["type"] == 3 and s["cashflow_type"]["risk"] == "growth"
    assert s["fcf"] < 0                                              # 扩张期自由现金流为负


def test_fallback_to_none_when_no_source(monkeypatch):
    _wire_eastmoney(monkeypatch, None, None)
    assert _run(fs.fetch_statements("000001", "CN")) is None


def test_ifind_off_by_default_uses_eastmoney(monkeypatch):
    # 默认 iFinD 关闭 → 直接落到东财；provider 应为 eastmoney
    monkeypatch.delenv("DEEPFOCUS_IFIND_STATEMENTS", raising=False)
    _wire_eastmoney(monkeypatch, _PERF, _CF)
    s = _run(fs.fetch_statements("600519", "CN"))
    assert s["provider"] == "eastmoney"


def test_assess_quality_pure():
    q = fs.assess_quality({"ocf": 100, "icf": -50, "fcf_financing": -30, "fcf": 70,
                           "eps": 2.0, "ocf_per_share": 2.4, "deduct_eps": 1.9,
                           "roe": 20.0, "gross_margin": 45.0})
    assert q["cashflow_type"]["type"] == 4
    assert q["earnings_quality"]["detail"]["cfo_ratio"] == 1.2      # 2.4/2.0 含金量
    assert q["good_business"]["score"] is not None
