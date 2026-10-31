"""Fixed read workload cannot certify short, missing or failed observations."""
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('financial_soak',ROOT/'scripts/quant-financial-service-soak.py');soak=importlib.util.module_from_spec(spec);spec.loader.exec_module(soak)


def metrics(full=False):
    rounds=120 if full else 3;n=rounds*4
    return dict(rounds=rounds,client_completed=n-n//5,client_cancelled=n//5,server_completed=n,exact_server_results=n,quota_checks=rounds*3,remaining_reads=0,paper_accounts=0,research_main_unchanged=True,peak_qr_disk_bytes=16*1024**2,memory=dict(api_lifetime_peak_bytes=200*1024**2,largest_waited_child_peak_bytes=0,all_children_waited=True,SIGCHLD_not_ignored=True),external_connection_attempts=0,forbidden_spawn_attempts=0,observer_cycles=3000 if full else 9,query_ms=[10.0]*n,server_query_ms=[10.0]*n,observer_ms=[10.0]*(9000 if full else 27),loop_ms=[1.0]*(200000 if full else 1000),errors=[],elapsed_seconds=3600 if full else 12)


def test_smoke_is_not_a_full_certificate_even_with_long_declared_elapsed():
    m=metrics();assert soak.evaluate(m,'smoke')['engineering_checks_passed']
    assert not soak.evaluate(m,'full')['full_soak_passed'];m['elapsed_seconds']=3600
    assert not soak.evaluate(m,'smoke')['full_soak_passed']


def test_fixed_full_duration_and_all_coverage_requirements_accept_only_registered_full():
    assert soak.evaluate(metrics(True),'full')['full_soak_passed']
    assert soak.PROTOCOL['duration_seconds']==3600 and soak.PROTOCOL['global_read_limit']==4 and soak.PROTOCOL['owner_read_limit']==2


@pytest.mark.parametrize('damage',['short','observer_gap','heartbeat_gap','not_exact','missed_cancel','slot_leak','paper','changed_db','slow_query','slow_server','slow_observer','nan','negative','large_rss','child','ignored_SIGCHLD','external','spawn','error'])
def test_failures_cannot_be_papered_over(damage):
    m=metrics(True)
    if damage=='short':m['elapsed_seconds']=3599.99
    if damage=='observer_gap':m['observer_cycles']=2999
    if damage=='heartbeat_gap':m['loop_ms']=[1.0]*1000
    if damage=='not_exact':m['exact_server_results']-=1
    if damage=='missed_cancel':m['client_cancelled']-=1
    if damage=='slot_leak':m['remaining_reads']=1
    if damage=='paper':m['paper_accounts']=1
    if damage=='changed_db':m['research_main_unchanged']=False
    if damage.startswith('slow_'):m[{'slow_query':'query_ms','slow_server':'server_query_ms','slow_observer':'observer_ms'}[damage]]=[501.0]*10
    if damage=='nan':m['query_ms']=[float('nan')]
    if damage=='negative':m['query_ms']=[-1.0]
    if damage=='large_rss':m['memory']['api_lifetime_peak_bytes']=1024**3+1
    if damage=='child':m['memory']['largest_waited_child_peak_bytes']=1
    if damage=='ignored_SIGCHLD':m['memory']['SIGCHLD_not_ignored']=False
    if damage=='external':m['external_connection_attempts']=1
    if damage=='spawn':m['forbidden_spawn_attempts']=1
    if damage=='error':m['errors']=[{'type':'ObservedFailure'}]
    assert not soak.evaluate(m,'full')['full_soak_passed']


def test_runtime_identity_records_actual_jose_auth_dependencies():
    e=soak.environment()
    assert e['packages']['python-jose'] and e['packages']['passlib']
    assert 'PyJWT' not in e['packages'] and e['python'] and e['executable']
