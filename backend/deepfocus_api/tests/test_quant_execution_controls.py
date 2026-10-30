import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from deepfocus_api import quant_event_replay as engine, quant_event_policy as policy
from deepfocus_api import quant_execution_controls as control
from deepfocus_api.quant_dividend_book import INVESTOR
from deepfocus_api.tests.test_quant_event_replay import fixture, PARAM


def setup():
    data, _, days = fixture(count=120, dividend=1)
    for row in data['000333']['bars'][31:]:
        for key in ('open', 'close', 'high', 'low'): row[key] += 1
    market = [{'date': d, 'close': 100.+i} for i, d in enumerate(days)]
    return data, days, market


def replay(data, days, market, name='cadence20_market60', **kwargs):
    return engine.replay(data, ['000333'], PARAM, [], {}, investor=INVESTOR,
                         start=days[60], policy=name,
                         market_curve=market if 'market' in name else None, **kwargs)


def test_cadence_anchor_crosses_fold_boundary_and_json_restore_without_reset():
    data, days, market = setup()
    schedule = [dict(train_end=days[54], test_start=days[60], test_end=days[72], parameters=PARAM),
                dict(train_end=days[67], test_start=days[73], test_end=days[-1], parameters=PARAM)]
    whole = replay(data, days, market, schedule=schedule)
    prefix = replay(data, days, market, end=days[71], schedule=schedule)
    suffix = replay(data, days, market, state=json.loads(json.dumps(prefix['state'])), schedule=schedule)
    assert prefix['fills'] and prefix['state']['book']['lots']
    for key in ('fills', 'curve', 'blocked', 'policy_audit'):
        assert prefix[key]+suffix[key] == whole[key]
    assert suffix['state'] == whole['state']
    assert [a['date'] for a in whole['policy_audit'] if a['rebalance_due']] == days[60::20]
    assert all(f['date'] in days[60::20] for f in whole['fills'])


def test_market_reads_only_prior_sixty_closes_and_current_close_not_orders():
    data, days, market = setup()
    before = replay(data, days, market, end=days[60])
    changed = copy.deepcopy(market)
    for row in changed[60:]: row['close'] *= .1
    after = replay(data, days, changed, end=days[60])
    assert before['fills'] and before['fills'] == after['fills']
    assert before['policy_audit'] == after['policy_audit']
    audit = before['policy_audit'][0]
    assert audit['market_signal_date'] == days[59] and audit['market_mean'] == pytest.approx(129.5)
    market[59]['close'] = 100.
    off = replay(data, days, market, end=days[60])
    assert not off['fills'] and not off['policy_audit'][0]['market_on']


def test_market_off_forces_daily_exit_but_reentry_waits_for_due_day():
    data, days, market = setup()
    market[61]['close'] = 50.
    result = replay(data, days, market, end=days[81])
    exit = [f for f in result['fills'] if f['date']==days[62]]
    assert exit and all(f['side']=='sell' and f['reason']=='market_filter_exit' for f in exit)
    assert not result['policy_audit'][2]['rebalance_due']
    assert not any(f['side']=='buy' and days[60]<f['date']<days[80] for f in result['fills'])
    assert any(f['date']==days[80] and f['side']=='buy' for f in result['fills'])


def test_stop_loss_exits_nonrebalance_day_without_waiting_twenty_sessions():
    data, days, market = setup()
    for row in data['000333']['bars'][61:]:
        row.update(open=95., close=95., high=96., low=94.)
    result = replay(data, days, market, name='cadence20', end=days[65])
    sells = [f for f in result['fills'] if f['side']=='sell']
    # Stops use the prior completed close, not today's not-yet-known close.
    assert not any(f['date']==days[61] for f in result['fills'])
    assert any(f['date']==days[62] and f['reason']=='stop_loss' for f in sells)
    assert not result['state']['paused']


def test_permanent_pause_exits_daily_and_rebound_never_resets_it():
    data, days, market = setup(); symbols=['000333', '000001', '600036', '600519', '600900']
    for symbol in symbols: data[symbol] = copy.deepcopy(data['000333'])
    for symbol in symbols:
        for row in data[symbol]['bars'][61:63]: row.update(open=80., close=80., high=81., low=79.)
    result = engine.replay(data, symbols, PARAM, [], {}, investor=INVESTOR, start=days[60], policy='cadence20')
    assert result['state']['paused']
    assert any(f['date']==days[62] and f['reason']=='risk_pause' for f in result['fills'])
    assert not any(f['side']=='buy' and f['date']>days[60] for f in result['fills'])


def test_market_off_blocked_exit_keeps_real_position_and_cannot_buy():
    data, days, market = setup(); market[61]['close']=50.
    previous = data['000333']['bars'][61]['close']
    data['000333']['bars'][62].update(open=previous*.9, close=previous*.9, high=previous*.91, low=previous*.89)
    result = replay(data, days, market, end=days[62])
    assert any(b['date']==days[62] and b['reason']=='price_limit' for b in result['blocked'])
    assert result['state']['book']['lots']
    assert not any(f['date']==days[62] for f in result['fills'])


@pytest.mark.parametrize('damage', ['revision', 'policy', 'v2', 'missing_day', 'warmup', 'unknown', 'unneeded_market'])
def test_bad_frozen_policy_or_market_input_fails_without_mutating_state(damage):
    data, days, market = setup(); result = replay(data, days, market, end=days[65]); original=copy.deepcopy(result['state'])
    args = {'state': result['state']}
    name = 'cadence20_market60'
    if damage=='revision': market[40]['close']+=1
    elif damage=='policy': name='daily_market60'
    elif damage=='v2': args['state']=copy.deepcopy(original);args['state']['kernel']='v2-old-kernel'
    elif damage=='missing_day': market.pop(55)
    elif damage=='warmup':
        with pytest.raises(ValueError, match='warmup'):
            engine.replay(data,['000333'],PARAM,[],{},investor=INVESTOR,start=days[50],end=days[59],
                          policy=name,market_curve=market)
        return
    elif damage=='unknown': name='cadence5_market20'
    elif damage=='unneeded_market':
        with pytest.raises(ValueError):
            engine.replay(data,['000333'],PARAM,[],{},investor=INVESTOR,policy='daily',market_curve=market)
        return
    with pytest.raises(ValueError): replay(data, days, market, name=name, **args)
    assert result['state']==original


def test_financial_compatibility_ignores_only_declared_metadata_and_checks_cash():
    data, days, market=setup();current=replay(data, days, market, name='daily', end=days[65]);old=copy.deepcopy(current)
    old['state']['kernel']='old';old['state']['plan']='old'
    old['state'].pop('rebalance_anchor');old['state'].pop('market_prefix')
    assert control.financial_compatibility(current,old)
    old['state']['book']['cash']='0.00'
    assert not control.financial_compatibility(current,old)


@pytest.mark.parametrize('damage', ['runner', 'path', 'overwrite'])
def test_cli_refuses_changes_and_overwrite(tmp_path, damage):
    script=Path(__file__).resolve().parents[3]/'scripts/quant-execution-controls.py'
    paths={k:tmp_path/(k+'.json') for k in ('experiments','events','reference','protocol','output')}
    paths['experiments'].write_text('[]');paths['reference'].write_text('{}')
    paths['events'].write_text(json.dumps({'source_files':{'0'*64:'../outside.pdf'}}))
    (tmp_path/'manifest.json').write_text('{}')
    paths['protocol'].write_text(json.dumps({'runner_sha256':'0'*64 if damage=='runner' else hashlib.sha256(script.read_bytes()).hexdigest(),
        'reference_sha256':hashlib.sha256(paths['reference'].read_bytes()).hexdigest(),
        'benchmark_manifest_sha256':hashlib.sha256((tmp_path/'manifest.json').read_bytes()).hexdigest()}))
    if damage=='overwrite': paths['output'].write_text('retain failed report')
    args=[sys.executable,str(script),'--evidence-dir',str(tmp_path),'--reference-source-dir',str(tmp_path),'--benchmark-dir',str(tmp_path)]
    for k,v in paths.items():args.extend(['--'+k,str(v)])
    result=subprocess.run(args,capture_output=True,text=True)
    assert result.returncode!=0
    if damage=='overwrite': assert paths['output'].read_text()=='retain failed report'
    else: assert not paths['output'].exists()


def registered_fixture():
    # Structurally historical fixture only, never execute an archived source.
    from deepfocus_api import quant_event_training as training
    from deepfocus_api.quant_research_engine import digest
    data, days, market=setup()
    fold=dict(train_end=days[54],test_start=days[60],test_end=days[-1],parameters=PARAM)
    result={'dataset_digest':digest(data),'folds':[fold]}
    exp={'id':'synthetic-protocol-only','request':{'symbols':['000333'],'capital':1000000},
         'dataset':data,'result':result,'result_digest':digest(result)}
    manifest={'events':[]};inputs=training.bindings([exp],manifest)
    root=Path(training.__file__).parent
    archive={n:(root/n).read_bytes() for n in inputs['source_pins']}
    oldnames=('quant_event_replay.py','quant_dividend_book.py','quant_corporate_actions.py',
              'quant_research_engine.py','ai_fund_ledger.py','ai_fund_policy.py')
    inputs['kernel']=digest({n:inputs['source_pins'][n] for n in oldnames})
    parent={'schema':'event-training-basis-controls-v1','rules':copy.deepcopy(training.RULES),
            'arms':list(training.ARMS),'purpose':'seen_development_method_control',
            'promotion_allowed':False,'bindings':inputs}
    reference={'protocol':parent,'protocol_digest':digest(parent),'results':[{
        'experiment':exp['id'],'schedules':{'refit_expanded':[fold]},
        'cases':{'refit_expanded':{'version':'event-replay-development-v2','state':{'kernel':inputs['kernel']}}}}]}
    protocol={'schema':'execution-exposure-cadence-controls-v1','rules':copy.deepcopy(control.RULES),
              'arms':list(control.ARMS),'promotion_allowed':False,
              'registered_at':datetime.now(timezone.utc).isoformat(),
              'restore_dates':{exp['id']:days[71]},
              'bindings':control.bindings([exp],manifest,reference,archive,market)}
    return protocol,[exp],manifest,reference,archive,market


@pytest.mark.parametrize('damage',['source','archived_source','events','reference','rules','market','arms','promotion','restore','future'])
def test_protocol_does_not_accept_changed_frozen_input_or_v2_source(damage):
    args=list(registered_fixture());control.validate_protocol(*args)
    protocol,experiments,manifest,reference,archive,market=args
    if damage=='source':protocol['bindings']['source_pins']['quant_event_policy.py']='0'*64
    elif damage=='archived_source':archive['quant_event_replay.py']+=b'changed'
    elif damage=='events':manifest['events'].append({'unregistered':True})
    elif damage=='reference':reference['results'][0]['schedules']['refit_expanded'][0]['test_end']='2024-01-01'
    elif damage=='rules':protocol['rules']['pause_reset']='sometimes'
    elif damage=='market':market[30]['close']+=1
    elif damage=='arms':protocol['arms'].pop()
    elif damage=='promotion':protocol['promotion_allowed']=True
    elif damage=='restore':protocol['restore_dates'][experiments[0]['id']]='2099-01-01'
    else:protocol['registered_at']='2099-01-01T00:00:00+00:00'
    with pytest.raises(ValueError):control.validate_protocol(*args)
