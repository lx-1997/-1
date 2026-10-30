"""Provider contracts use synthetic source bytes; no network or production writes."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace as N
import pytest
from deepfocus_api import ai_fund_market as m,ai_fund as af,ai_fund_audit as audit
from deepfocus_api.tests.test_ai_fund_execution_guards import simulation,config
from deepfocus_api.tests.market_fixture import source_fixture

NOW=datetime(2026,10,30,3,tzinfo=timezone.utc)
SINA='https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol=sh600519&scale=240'
EM='https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519&klt=101&fqt=0'

def source(provider='sina',**changes):
    if provider=='sina':
        row=dict(day='2026-10-29',open=100,high=102,low=98,close=101,volume=100000);row.update(changes)
        return json.dumps([row]).encode()
    p=['2026-10-29','100','101','102','98','1000','10050000']
    for k,v in changes.items():p[int(k)]=str(v)
    return json.dumps({'data':{'code':'600519','klines':[','.join(p)]}}).encode()

def snapshot(provider='sina',**changes):return m.parse(source(provider,**changes),'600519',provider,SINA if provider=='sina' else EM,fetched_at=NOW)


def test_raw_basis_units_precision_and_turnover_are_explicit():
    a=snapshot();b=snapshot('eastmoney')
    assert a['bars'][0]['v']==b['bars'][0]['v']==100000
    assert a['bars'][0]['v_lower_bound']==100000 and b['bars'][0]['v_lower_bound']==99900
    assert a['meta']['volume_precision_shares']==1 and b['meta']['volume_precision_shares']==100
    assert b['bars'][0]['source_v']=='1000' and b['meta']['price_basis']=='unadjusted'
    assert m.turnover(a['bars'][0])==10100000
    assert m.turnover(b['bars'][0])==10050000
    assert not b['meta']['commercial_license_verified'] and not b['meta']['corporate_actions_complete']


@pytest.mark.parametrize('bad_url',[EM.replace('fqt=0','fqt=1'),EM.replace('600519','000001'),EM.replace('push2his.eastmoney.com','example.invalid')])
def test_adjusted_or_wrong_stock_source_request_is_rejected(bad_url):
    with pytest.raises(ValueError):m.parse(source('eastmoney'),'600519','eastmoney',bad_url,fetched_at=NOW)


def test_adjustment_argument_and_response_identity_cannot_override_contract():
    with pytest.raises(ValueError,match='Adjusted'):m.parse(source(),'600519','sina',SINA,fetched_at=NOW,adjustment='qfq')
    p=json.loads(source('eastmoney'));p['data']['code']='000001'
    with pytest.raises(ValueError,match='identity'):m.parse(json.dumps(p).encode(),'600519','eastmoney',EM,fetched_at=NOW)


@pytest.mark.parametrize('bad',[{'close':0},{'close':103},{'volume':-1},{'volume':'NaN'},{'volume':1.5},{'day':'2026-10-31'}])
def test_malformed_nonfinite_and_future_rows_fail_closed(bad):
    with pytest.raises(ValueError):snapshot(**bad)


def test_duplicate_rows_and_conflicting_amount_are_rejected():
    row=json.loads(source())[0]
    with pytest.raises(ValueError,match='duplicate'):m.parse(json.dumps([row,row]).encode(),'600519','sina',SINA,fetched_at=NOW)
    with pytest.raises(ValueError,match='inconsistent'):snapshot('eastmoney',**{'6':100500})
    with pytest.raises(ValueError,match='Contradictory'):snapshot('eastmoney',**{'6':0})


def test_raw_bytes_and_normalized_bars_are_both_checked():
    md=m.fields(snapshot());assert m.completed_bar(md,'600519',now=NOW)
    md['ohlc'][0]['v']*=100
    assert m.completed_bar(md,'600519',now=NOW) is None
    md=m.fields(snapshot());md['market_raw']+=' '
    assert m.completed_bar(md,'600519',now=NOW) is None
    assert m.completed_bar(m.fields(snapshot()),'000001',now=NOW) is None


def test_cache_clock_cross_day_future_and_expiry_are_not_reused():
    s=snapshot();assert m.fresh(s,NOW)
    assert not m.fresh(s,NOW+timedelta(hours=7))
    assert not m.fresh(s,NOW-timedelta(minutes=1))
    s['meta']['fetched_at']='2026-10-29T15:59:00+00:00'
    assert not m.fresh(s,datetime(2026,10,29,16,1,tzinfo=timezone.utc))


def test_today_bar_is_preserved_as_observed_but_not_used_for_liquidity():
    rows=json.loads(source());rows.append({**rows[0],'day':'2026-10-30','volume':90000000})
    s=m.parse(json.dumps(rows).encode(),'600519','sina',SINA,fetched_at=NOW)
    assert len(s['bars'])==2 and m.completed_bar(m.fields(s),'600519',now=NOW)['d']=='2026-10-29'
    s=m.parse(json.dumps(rows[-1:]).encode(),'600519','sina',SINA,fetched_at=NOW)
    assert m.completed_bar(m.fields(s),'600519',now=NOW) is None


def test_mutating_one_consumers_fields_does_not_change_shared_snapshot():
    s=snapshot();md=m.fields(s);md['ohlc'][0]['c']=1;md['market_meta']['symbol']='000001'
    assert s['bars'][0]['c']==101 and s['meta']['symbol']=='600519'


def test_unknown_volume_is_not_silently_used_as_shares():
    assert m.turnover({'c':100,'v':1000}) is None
    assert af._impact_cost_bps({'c':100,'v':1000},10000)==af._IMPACT_FLOOR_BPS
    normalized=snapshot('eastmoney')['bars'][0]
    assert af._impact_cost_bps(normalized,100500)>af._IMPACT_FLOOR_BPS


def test_unknown_contract_blocks_new_risk_even_with_strong_signal(simulation,monkeypatch):
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:{'002594':{'closes':[100]*30}})
    out=af.run_tick(cfg=config());assert out['traded']==[]
    with af._connect() as c:
        r=audit.load(c,'main',out['audit']['id'])
        assert r['sections']['candidates']['002594']['actions'][-1]['reason']=='market_contract_unverified_or_stale'


def test_missing_history_still_allows_valid_risk_reduction(simulation,monkeypatch):
    # Existing holdings may reduce risk with an explicitly provisional cost floor.
    from deepfocus_api.tests.test_ai_fund_execution_guards import hold
    hold();simulation.update(latest=88,changeRatio=-3)
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:{})
    out=af.run_tick(cfg=config());assert [t['side'] for t in out['traded']]==['sell']


class ResponseClient:
    def __init__(self,body,seen):self.body=body;self.seen=seen
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def get(self,url,**kw):self.seen.append(url);return N(status_code=200,content=self.body)


@pytest.mark.asyncio
async def test_backup_endpoint_requests_unadjusted_prices_and_reported_amount(monkeypatch):
    seen=[];monkeypatch.setattr(af,'_em_client',lambda:ResponseClient(source('eastmoney'),seen))
    result=await af._em_kline('1.600519',10)
    assert result['meta']['price_basis']=='unadjusted'
    assert 'fqt=0' in seen[0] and 'f57' in seen[0] and 'fqt=1' not in seen[0]


@pytest.mark.asyncio
async def test_legacy_unversioned_cache_is_fenced_and_fallback_keeps_provenance(monkeypatch):
    import time
    monkeypatch.setattr(af,'_KLINE_CACHE',{'1.600519':(time.time(),[{'d':'2026-10-29','c':1}])})
    calls=[]
    async def primary(*a):calls.append('sina');return None
    async def backup(*a):calls.append('eastmoney');return snapshot('eastmoney')
    monkeypatch.setattr(af,'_sina_kline',primary);monkeypatch.setattr(af,'_em_kline',backup)
    result=await af._fetch_kline_ohlc('1.600519')
    assert calls==['sina','eastmoney'] and result['meta']['provider']=='eastmoney'


@pytest.mark.asyncio
async def test_cached_snapshot_reaches_strategy_with_raw_metadata_and_independent_rows(monkeypatch):
    import time
    now=datetime.now(timezone.utc)
    s=m.parse(source(),'600519','sina',SINA,fetched_at=now)
    monkeypatch.setattr(af,'_KLINE_CACHE',{'1.600519':(time.time(),s)})
    monkeypatch.setattr(af,'_FLOW_CACHE',{});monkeypatch.setattr(af,'_FUND_CACHE',{});monkeypatch.setattr(af,'FUND_FETCH_CAP',0)
    result=await af._gather_md(['600519'])
    assert result['600519']['market_meta']['response_sha256']==s['meta']['response_sha256']
    assert result['600519']['market_raw']==s['raw_response']
    result['600519']['ohlc'][0]['v']=0
    assert (await af._gather_md(['600519']))['600519']['ohlc'][0]['v']==100000


def test_risk_reducing_provisional_floor_is_explicit_in_audit(simulation,monkeypatch):
    from deepfocus_api.tests.test_ai_fund_execution_guards import hold
    hold();simulation.update(latest=88,changeRatio=-3)
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:{})
    out=af.run_tick(cfg=config())
    with af._connect() as c:r=audit.load(c,'main',out['audit']['id'])
    assert r['sections']['candidates']['002594']['cost_observation']['basis']=='provisional_floor_no_verified_liquidity'


@pytest.mark.parametrize('symbol',['000300','000905','000852'])
def test_price_benchmarks_cannot_become_tradable_stock_history(symbol):
    with pytest.raises(ValueError,match='identity'):
        m.parse(source(),symbol,'sina',SINA.replace('sh600519','sz'+symbol),fetched_at=NOW)


@pytest.mark.parametrize('provider,url',[('sina',SINA.replace('scale=240','scale=5')),('eastmoney',EM.replace('klt=101','klt=5'))])
def test_intraday_source_cannot_claim_daily_contract(provider,url):
    with pytest.raises(ValueError):m.parse(source(provider),'600519',provider,url,fetched_at=NOW)


def test_full_warm_universe_fits_atomic_journal_budget(simulation,monkeypatch):
    from dataclasses import replace
    from deepfocus_api.tests.market_fixture import source_map
    cfg=replace(af.MAIN_CFG,muse=False,debate=False)
    frame=source_map({s:{'closes':[80+i*.1 for i in range(250)]} for s in cfg.pool})
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:frame)
    out=af.run_tick(cfg=cfg);assert out['traded']
    with af._connect() as c:r=audit.load(c,'main',out['audit']['id'])
    assert len(r['sections']['market_data'])==28
    assert len(audit.encode(r['sections']['market_data']))<=audit.MAX_RAW_BLOB


def test_fresh_receipt_cannot_make_old_or_zero_volume_history_executable():
    old=snapshot(day='2026-09-01')
    assert m.fresh(old,NOW) and m.completed_bar(m.fields(old),'600519',now=NOW) is None
    zero=snapshot(volume=0)
    assert m.completed_bar(m.fields(zero),'600519',now=NOW) is None
