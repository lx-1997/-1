#!/usr/bin/env python3
"""Join reviewed issuer facts to retained raw prices for fixed-holding accounting.

Positions/fills are explicitly counterfactual; this is not a strategy backtest.
No supplier calls, production writes, automatic tax qualification or migration.
"""
import argparse,hashlib,importlib.util,json,subprocess,sys
from decimal import Decimal,Context,localcontext,ROUND_HALF_UP
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from deepfocus_api import quant_capitalization_prices as prices,quant_capitalization_book as book
from deepfocus_api.ai_fund_ledger import fee
spec=importlib.util.spec_from_file_location('share_input',ROOT/'scripts/quant-share-inventory-review.py');inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(inputs)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','action-plan','facts','workspace'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if any(not path.is_absolute() for path in (a.capture,a.action_plan,a.facts,a.workspace)) or a.workspace.exists() or not a.workspace.parent.is_dir():p.error('Absolute inputs and NEW isolated workspace required')
    w=a.workspace;w.mkdir(mode=0o700);stage='input'
    report={'schema':'retained-price-capitalization-reconciliation-v1','status':'rejected','position_role':'counterfactual_model',
            'price_role':'retained_provider_unadjusted_observations','strategy_replays':0,'network_requests':0,'production_writes':0,
            'broker_clearing_verified':False,'exchange_reference_rule_verified':False,'data_rights_verified':False,
            'promotion_allowed':False,'commercial_ready':False,'source_pins':book.LOADED_PINS,
            'reader_source_pins':{name:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for name,path in [
                ('price_normalizer','backend/deepfocus_api/quant_capitalization_prices.py'),
                ('protocol_decoder','backend/deepfocus_api/quant_security_status.py'),
                ('input_reader','scripts/quant-share-inventory-review.py')]},
            'reviewer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        raw={name:inputs.bounded_read(path,2*1024**2) for name,path in [('manifest',a.capture/'manifest.json'),('action-plan',a.action_plan),('facts',a.facts)]}
        for name,body in raw.items():(w/(name+'.json')).write_bytes(body)
        m=inputs.load(raw['manifest']);plan=inputs.load(raw['action-plan']);facts=inputs.load(raw['facts'])['fact']
        report['input_sha256']={name:hashlib.sha256(body).hexdigest() for name,body in raw.items()}
        sources={name:inputs.bounded_read(a.capture/name,4*1024**2) for name in ['sdk-response-0.bin','sdk-response-1.bin','sdk-response-2.bin']}
        stage='prices';normalized=prices.normalize(m,sources);inputs.dump(w/'normalized-prices.json',normalized)
        (w/'retained-protocol').mkdir()
        for name,body in sources.items():(w/'retained-protocol'/name).write_bytes(body)
        if m['collector_sha256']!=hashlib.sha256((ROOT/'scripts/capture-quant-capitalization-prices.py').read_bytes()).hexdigest():raise ValueError('Price collector version changed')
        stage='facts';event=book.shares.Capitalization(**plan['action']);scope=plan['scope']
        if (plan['position_role']!='counterfactual_model' or plan['mode']!='historical_reconstruction' or event.symbol!='300750'
                or event.kind!='capital_reserve' or event.record_date!='2023-04-25' or event.ex_date!='2023-04-26' or event.new_shares_per_old_share!='0.8'
                or len(plan['closing_lots'])!=1 or plan['closing_lots'][0]['qty']!=100 or plan['closing_lots'][0]['symbol']!=event.symbol or plan['closing_lots'][0]['restriction']!='unrestricted'):
            raise ValueError('Only the explicit reviewed 100-share model sample is supported')
        for key in ('symbol','record_date','ex_date','source_sha256','source_url','share_credit_date'):
            if facts[key]!=getattr(event,key):raise ValueError('Issuer action and reviewed fact differ')
        if (facts['new_shares_per_old_share']!=event.new_shares_per_old_share or facts['event_kind']!='capital_reserve_capitalization'
                or facts['available_at_conservative']!=event.available_at or facts['observed_at']!=event.observed_at):raise ValueError('Issuer ratio/type/knowledge clocks differ')
        cash_ps=prices._decimal(facts['gross_cash_per_old_share']);pay=facts['cash_pay_date']
        if not cash_ps.is_finite() or not 0<cash_ps<100 or not event.ex_date<=pay<=prices.DAYS[-1]:raise ValueError('Unsupported reviewed cash fact')
        cash_event={'event_id':'retained-sample-cash','revision':1,'symbol':event.symbol,'record_date':event.record_date,'ex_date':event.ex_date,'pay_date':pay,
            'cash_per_share_gross':str(cash_ps),'price_adjustment_per_share':str(cash_ps),'available_at':event.available_at,'observed_at':event.observed_at,'source_url':event.source_url,'source_sha256':event.source_sha256}
        rows=normalized['rows'];qty=100;capital=50000;first_price=Decimal(rows[0]['open']);charge=Decimal(str(fee(first_price*qty,'buy')))
        generated={'position_role':'counterfactual_model','price_role':'operator_supplied_retained_raw_marks','scope':scope,'capital':capital,
            'start_after':'2023-04-23','mode':'historical_reconstruction','investor':book.cash.INVESTOR,'sessions':prices.DAYS,
            'actions':[plan['action']],'cash_events':[cash_event],'sources':plan['sources'],'days':[]}
        for i,row in enumerate(rows):
            fills=[{'id':'retained-model-buy','symbol':event.symbol,'date':row['date'],'side':'buy','qty':qty,'price':row['open'],'fee':str(charge)}] if i==0 else []
            generated['days'].append({'date':row['date'],'open_prices':{event.symbol:row['open']},'close_prices':{event.symbol:row['close']},'fills':fills})
        inputs.dump(w/'generated-plan.json',generated);stage='ledger'
        child=subprocess.run([sys.executable,str(ROOT/'scripts/quant-capitalization-review.py'),'--plan',str(w/'generated-plan.json'),'--workspace',str(w/'ledger-review')],capture_output=True,text=True,timeout=30)
        (w/'ledger-command.log').write_text(child.stdout+child.stderr)
        outcome=inputs.load((w/'ledger-review/result.json').read_bytes())
        if child.returncode!=0 or outcome['status']!='composite_quantity_valuation_completed':raise ValueError('Composite ledger did not complete')
        stage='independent_identity';values=inputs.load((w/'ledger-review/daily-valuations.json').read_bytes());checks=[]
        with localcontext(Context(prec=50)):
            def cents(n):return n.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            gross_cash=Decimal(qty)*cash_ps;initial_cash=Decimal(capital)-qty*first_price-charge
            for row,val in zip(rows,values):
                for phase in ('open','close'):
                    d=row['date'];n=Decimal(qty)*(1+Decimal(event.new_shares_per_old_share)) if d>=event.ex_date else Decimal(qty)
                    paid=d>pay or d==pay and phase=='close';recognized=d>=event.ex_date
                    expected_cash=initial_cash+(gross_cash if paid else 0)
                    if d==rows[0]['date'] and phase=='open':n=Decimal(0);expected_cash=Decimal(capital)
                    receivable=gross_cash if recognized and not paid else Decimal(0)
                    expected=cents(expected_cash+n*Decimal(row[phase])+receivable)
                    actual=val[phase]
                    if Decimal(actual['composite_gross_nav_exact'])!=expected:raise ValueError('Independent gross asset identity differs')
                    reserve=gross_cash*Decimal('.2') if recognized else Decimal(0)  # all model lots remain under one month
                    if Decimal(actual['cash_dividend_tax_reserved_nav_lower'])!=cents(expected-reserve):raise ValueError('Cash dividend tax reserve identity differs')
                    if Decimal(actual['available_cash'])!=cents(expected_cash-(reserve if paid else 0)):raise ValueError('Unpaid rights financed cash')
                    checks.append({'date':d,'phase':phase,'gross_identity_difference':'0.00','cash_identity_difference':'0.00','cash_dividend_tax_reserve_difference':'0.00'})
            reference=Decimal(rows[1]['close']);theoretical=(reference-cash_ps)/(1+Decimal(event.new_shares_per_old_share));provider=Decimal(rows[2]['provider_reference'])
            report['reference_check']={'prior_raw_close':str(reference),'provider_ex_day_reference':str(provider),'declared_ratio_cash_reference_exact':str(theoretical),
                'cent_rounded_difference':str(cents(theoretical)-provider),'role':'numeric_sample_consistency_not_exchange_rule_certification'}
        if len(values)!=5 or len(checks)!=10:raise ValueError('Incomplete ledger valuation coverage')
        for name,path in [('price_normalizer','backend/deepfocus_api/quant_capitalization_prices.py'),('protocol_decoder','backend/deepfocus_api/quant_security_status.py'),('input_reader','scripts/quant-share-inventory-review.py')]:
            if report['reader_source_pins'][name]!=hashlib.sha256((ROOT/path).read_bytes()).hexdigest():raise ValueError('Price review source changed during run')
        if report['reviewer_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Reviewer changed during run')
        report.update(status='raw_price_accounting_reconciled',identity_checks=checks,retained_price_response_sha256=m['captures'][1]['sha256'],
                      price_observed_at=m['captures'][1]['observed_at'],model_registered_shares=qty,model_new_shares=80,
                      cash_reference_field_role='declared_cash_scenario_unused_by_valuation_not_exchange_reference',real_strategy_or_account_performance=False)
    except (ValueError,TypeError,KeyError,OSError,subprocess.TimeoutExpired) as exc:report.update(failed_stage=stage,error_type=type(exc).__name__,error=str(exc))
    inputs.dump(w/'result.json',report);print(json.dumps({'status':report['status'],'failed_stage':report.get('failed_stage')}))
    return 0 if report['status']=='raw_price_accounting_reconciled' else 2


if __name__=='__main__':raise SystemExit(main())
