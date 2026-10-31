"""Across-session evidence/calendar integrity; all fixtures are synthetic."""
from dataclasses import replace
import copy
import json
import pytest
from deepfocus_api import quant_capitalization_book as v
from deepfocus_api.tests.test_quant_capitalization_book import initial,step,fill,CASH,ACTION,DAYS,SOURCES,SCOPE


def cash_registered():
    b=step(initial(),DAYS[0],182.52,actions=[],fills=[fill(DAYS[0])])
    return step(b,DAYS[1],182.52,actions=[])


def test_registered_cash_action_cannot_disappear_across_sessions():
    b=cash_registered();before=copy.deepcopy(b)
    with pytest.raises(ValueError,match='history'):
        v.open_session(b,DAYS[2],{'300750':100},[],[],SOURCES,DAYS,scope=SCOPE)
    assert b==before


def test_calendar_cannot_delete_ex_date_to_skip_an_accounting_session():
    b=cash_registered();changed=[d for d in DAYS if d!=DAYS[2]]
    with pytest.raises(ValueError,match='calendar'):
        v.open_session(b,DAYS[3],{'300750':100},[CASH],[],SOURCES,changed,scope=SCOPE)


def test_cash_fact_already_seen_before_registration_cannot_be_rewritten():
    b=step(initial(),DAYS[0],182.52,actions=[],fills=[fill(DAYS[0])])
    changed=replace(CASH,cash_per_share_gross='3')
    with pytest.raises(ValueError,match='history'):
        step(b,DAYS[1],182.52,events=[changed],actions=[])


@pytest.mark.parametrize('changes',[[],[replace(ACTION,new_shares_per_old_share='0.9')]])
def test_observed_capitalization_cannot_be_removed_or_revised_before_registration(changes):
    b=step(initial(),DAYS[0],182.52,fills=[fill(DAYS[0])])
    with pytest.raises(ValueError,match='history'):
        step(b,DAYS[1],182.52,actions=changes)


def test_previously_hidden_future_evidence_can_arrive_without_rewriting_past_state():
    late=replace(CASH,event_id='later',record_date=DAYS[3],ex_date=DAYS[4],pay_date=DAYS[4],
                 available_at=DAYS[2]+'T08:00:00+08:00',observed_at=DAYS[2]+'T08:00:00+08:00')
    b=step(initial(),DAYS[0],182.52,actions=[],fills=[fill(DAYS[0])]);snapshot=copy.deepcopy(b)
    with_future=step(b,DAYS[1],182.52,events=[CASH,late],actions=[])
    without_future=step(b,DAYS[1],182.52,actions=[])
    assert with_future==without_future and b==snapshot
    restored=v.restore(json.dumps(with_future),scope=SCOPE)
    for d in DAYS[2:]:restored=step(restored,d,100,events=[CASH,late],actions=[])
    assert late.event_id in restored['cash']['claims']


def test_calendar_can_append_future_sessions_but_cannot_rewrite_its_frozen_prefix():
    b=cash_registered();extended=DAYS+['2023-05-04'];before=copy.deepcopy(b)
    opened=v.open_session(b,DAYS[2],{'300750':100},[CASH],[],SOURCES,extended,scope=SCOPE)
    assert opened['cash']['cursor']==DAYS[2] and b==before
    changed=DAYS[:-1]+['2023-05-04']
    with pytest.raises(ValueError,match='calendar'):
        v.open_session(b,DAYS[2],{'300750':100},[CASH],[],SOURCES,changed,scope=SCOPE)


def test_new_backdated_known_cash_fact_requires_rebuild_instead_of_appearing_mid_replay():
    b=step(initial(),DAYS[0],182.52,events=[],actions=[])
    with pytest.raises(ValueError,match='history'):
        step(b,DAYS[1],182.52,events=[CASH],actions=[])


def test_new_revision_can_be_observed_before_registration_with_old_chain_retained():
    b=step(initial(),DAYS[0],182.52,actions=[],fills=[fill(DAYS[0])])
    updated=replace(CASH,revision=2,cash_per_share_gross='3',available_at=DAYS[1]+'T08:00:00+08:00',observed_at=DAYS[1]+'T08:00:00+08:00')
    c=step(b,DAYS[1],182.52,events=[CASH,updated],actions=[])
    assert c['cash']['registrations'][CASH.event_id]['fingerprint']==updated.fingerprint
    assert c['history']['cash'][CASH.event_id]==updated.fingerprint


def test_history_survives_json_and_refuses_removal_on_first_resumed_call():
    b=cash_registered();restored=v.restore(json.dumps(b),scope=SCOPE)
    with pytest.raises(ValueError,match='history'):
        v.open_session(restored,DAYS[2],{'300750':100},[],[],SOURCES,DAYS,scope=SCOPE)
    assert step(restored,DAYS[2],100,actions=[])==step(b,DAYS[2],100,actions=[])


def test_close_observed_cash_fact_is_frozen_for_the_next_open():
    late=replace(CASH,available_at=DAYS[0]+'T20:00:00+08:00',observed_at=DAYS[0]+'T20:00:00+08:00')
    b=step(initial(),DAYS[0],182.52,events=[late],actions=[])
    assert CASH.event_id in b['history']['cash']
    with pytest.raises(ValueError,match='history'):
        step(b,DAYS[1],182.52,events=[],actions=[])


def test_appended_calendar_and_json_resume_match_one_shot_final_state():
    def run(b,d,price,sessions):
        prices={'300750':price}
        o=v.open_session(b,d,prices,[CASH],[ACTION],SOURCES,sessions,scope=SCOPE)
        f=v.execute_fills(o,[fill(d)] if d==DAYS[0] else [],scope=SCOPE)
        return v.close_session(f,prices,[CASH],[ACTION],SOURCES,sessions,scope=SCOPE)
    incremental=initial();whole=initial()
    for i,(d,price) in enumerate(zip(DAYS,[182.52,182.52,100,100,100])):
        incremental=run(v.restore(json.dumps(incremental),scope=SCOPE),d,price,DAYS[:3] if i<2 else DAYS)
        whole=run(whole,d,price,DAYS)
    assert incremental==whole
