"""US symbols must reach real data adapters and survive the evidence projection."""
import asyncio
from datetime import date, timedelta
import json
import pytest
import httpx
from deepfocus_api import agent_tools, us_financials, stock_news, valuation_source, google_finance


def run(coro):
    return asyncio.run(coro)


def row(**changes):
    return {'SECURITY_CODE': 'GOOGL', 'REPORT_DATE': '2025-09-30', 'START_DATE': '2025-07-01',
            'NOTICE_DATE': '2025-10-29', 'CURRENCY_ABBR': 'USD', 'DATE_TYPE': '单季报',
            'OPERATE_INCOME': 120, 'PARENT_HOLDER_NETPROFIT': -5, 'BASIC_EPS': 0,
            **changes}


@pytest.mark.parametrize('changes', [
    {'SECURITY_CODE': 'GOOG'}, {'CURRENCY_ABBR': 'HKD'},
    {'NOTICE_DATE': (date.today()+timedelta(days=10)).isoformat()},
    {'REPORT_DATE': 'bad'}, {'START_DATE': '2025-10-01'},
    {'OPERATE_INCOME': None, 'PARENT_HOLDER_NETPROFIT': None, 'BASIC_EPS': None}])
def test_us_financials_reject_wrong_or_unusable_source(changes):
    assert us_financials.normalize_earnings(row(**changes), 'GOOGL') is None


def test_us_period_zero_and_loss_are_not_rewritten():
    value = us_financials.normalize_earnings(row(), 'GOOGL')
    assert value['net_income'] == -5 and value['eps'] == 0
    assert value['roe'] is None and value['period_basis'] == '单季报'
    assert value['period_start'] == '2025-07-01' and value['published_date'] == '2025-10-29'


def test_us_routing_keeps_cn_separate(monkeypatch):
    calls = []
    async def us(symbol, market=None):
        calls.append(('US', symbol));return {'revenue': 120}
    async def cn(symbol, market=None):
        calls.append(('CN', symbol));return {'revenue': 5}
    monkeypatch.setattr(us_financials, 'fetch_us_earnings', us)
    monkeypatch.setattr(agent_tools, 'fetch_eastmoney_earnings', cn)
    assert run(agent_tools._tool_get_financials('GOOGL')) == {'revenue': 120}
    assert run(agent_tools._tool_get_financials('600519', 'CN')) == {'revenue': 5}
    assert calls == [('US', 'GOOGL'), ('CN', '600519')]


def test_snapshot_preserves_us_consensus_and_period_provenance(monkeypatch):
    async def empty(*args, **kw):return None
    async def finance(*args, **kw):return us_financials.normalize_earnings(row(), 'GOOGL')
    async def consensus(*args, **kw):return {'rating': 'Buy', 'analyst_count': 18, 'currency': 'USD', 'target_price': 123}
    monkeypatch.setattr(agent_tools, '_tool_get_market_quote', empty)
    monkeypatch.setattr(agent_tools, '_tool_get_valuation', empty)
    monkeypatch.setattr(agent_tools, '_tool_get_financials', finance)
    monkeypatch.setattr(agent_tools, '_tool_get_analyst_consensus', consensus)
    data = run(agent_tools._tool_get_stock_snapshot('GOOGL'))
    assert data['consensus']['rating'] == 'Buy' and data['consensus']['analyst_count'] == 18
    assert data['consensus']['target_price'] == 123
    assert data['financials']['period_basis'] == '单季报'
    assert data['financials']['source_url'] == us_financials._URL
    assert data['source_status']['valuation'] == 'no_data'


def test_us_news_keeps_ticker_and_rejects_url(monkeypatch):
    requests = []
    class Client:
        def __init__(self, **kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self, *args):pass
        async def get(self, url, **kw):
            requests.append(json.loads(kw['params']['param'])['keyword'])
            return httpx.Response(200, json={'result': {'cmsArticleWebOld': [
                {'title': 'GOOGL earnings', 'date': '2025-10-29', 'url': 'https://example.com/item'}]}})
    monkeypatch.setattr(stock_news.httpx, 'AsyncClient', Client)
    stock_news._CACHE.clear()
    assert len(run(stock_news.fetch_stock_news('GOOGL', 'US'))) == 1
    assert run(stock_news.fetch_stock_news('https://example.com', 'US')) == []
    assert requests == ['GOOGL']


def test_valuation_live_fallback_retains_unspecified_pe_basis(monkeypatch):
    class Client:
        def __init__(self, **kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self, *args):pass
        async def get(self, url, **kw):return httpx.Response(503)
    async def google(*args, **kw):
        return {'currency': 'USD', 'market_cap': 1200, 'pe_ratio': 20, 'exchange': 'NASDAQ'}
    monkeypatch.setattr(valuation_source.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(google_finance, 'fetch_google_finance_quote', google)
    valuation_source._CACHE.clear()
    data = run(valuation_source.fetch_valuation('GOOGL', 'US'))
    assert data['market_cap'] == 1200 and data['pe_ratio'] == 20
    assert data['pe_basis'] == 'provider_unspecified' and not data['is_realtime']


def test_nasdaq_fallback_checks_identity_and_keeps_missing_pe(monkeypatch):
    class Client:
        async def get(self, url, **kw):
            return httpx.Response(200, json={'data': {'symbol': 'GOOGL', 'summaryData': {
                'MarketCap': {'label': 'Market Cap', 'value': '1,200,000'}}}})
    data = run(valuation_source._fetch_nasdaq_us(Client(), 'GOOGL'))
    assert data['market_cap'] == 1200000 and data['currency'] == 'USD'
    assert 'pe_ratio' not in data and not data['is_realtime']
    assert run(valuation_source._fetch_nasdaq_us(Client(), 'GOOG')) is None


def test_unhealthy_primary_keeps_nasdaq_valuation(monkeypatch):
    class Client:
        def __init__(self, **kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self, *args):pass
    async def primary(*args):raise httpx.ReadTimeout('primary unavailable')
    async def alternate(*args):return {'market_cap': 123, 'currency': 'USD', 'provider': 'nasdaq'}
    monkeypatch.setattr(valuation_source.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(valuation_source, '_fetch_stockanalysis_us', primary)
    monkeypatch.setattr(valuation_source, '_fetch_nasdaq_us', alternate)
    valuation_source._CACHE.clear()
    data = run(valuation_source.fetch_valuation('GOOGL', 'US'))
    assert data['market_cap'] == 123 and data['provider'] == 'nasdaq'
