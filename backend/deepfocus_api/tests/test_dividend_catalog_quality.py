"""An information tool must not describe an incomplete catalog as ground truth."""
import asyncio
from deepfocus_api import dividend_history as history


def fake_client(monkeypatch,rows):
    class Response:
        status_code=200
        def json(self):return {'result':{'data':rows}}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args,**kwargs):return Response()
    monkeypatch.setattr(history.httpx,'AsyncClient',Client)
    monkeypatch.setattr(history,'_CACHE',{})


def test_catalog_explicitly_denies_complete_history_and_execution_quality(monkeypatch):
    fake_client(monkeypatch,[{'SECURITY_CODE':'300750','PRETAX_BONUS_RMB':20.11,'IMPL_PLAN_PROFILE':'供应商目录条目'}])
    rows=asyncio.run(history.fetch_dividend_history('300750'))
    quality=rows[0]['data_quality']
    assert not quality['complete_event_history_verified'] and not quality['implementation_filing_verified']
    assert not quality['execution_usable'] and '特别分红' in quality['warning']
    rows[0]['cash_per_10_pretax']=999;rows[0]['data_quality']['execution_usable']=True
    second=asyncio.run(history.fetch_dividend_history('300750'))
    assert second[0]['cash_per_10_pretax']==20.11 and not second[0]['data_quality']['execution_usable']


def test_wrong_security_is_not_relabelled_as_requested_stock(monkeypatch):
    fake_client(monkeypatch,[{'SECURITY_CODE':'600519','PRETAX_BONUS_RMB':20.11}])
    assert asyncio.run(history.fetch_dividend_history('300750')) is None
