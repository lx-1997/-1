"""Synthetic provider fixtures follow the frozen observation clock, never wall date."""
from datetime import datetime,timezone
import pytest
from deepfocus_api import ai_fund_market as market
from deepfocus_api.tests.market_fixture import source_fixture


def install(monkeypatch,stamps):
    calls=[]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            value=stamps[min(len(calls),len(stamps)-1)];calls.append(value)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(market,'datetime',Clock);return calls


def test_completed_fixture_dates_match_simulated_observation_not_actual_wall_date(monkeypatch):
    stamp=datetime(2026,10,30,2,tzinfo=timezone.utc);calls=install(monkeypatch,[stamp])
    result=source_fixture('002594',{'closes':[90+i*.2 for i in range(40)]})
    assert len(calls)==1 and result['market_meta']['fetched_at']==stamp.isoformat()
    assert result['ohlc'][-1]['d']=='2026-10-29'
    assert market.completed_bar(result,'002594',now=stamp)['d']=='2026-10-29'


def test_future_explicit_rows_are_still_rejected_not_trimmed_or_redated(monkeypatch):
    stamp=datetime(2026,10,30,2,tzinfo=timezone.utc);install(monkeypatch,[stamp])
    with pytest.raises(ValueError,match='Future or duplicate'):
        source_fixture('002594',{'ohlc':[{'d':'2026-10-31','c':100}]})


def test_single_clock_sample_keeps_generation_and_parser_consistent_across_midnight(monkeypatch):
    first=datetime(2026,10,30,15,59,59,tzinfo=timezone.utc);later=datetime(2026,10,30,16,0,1,tzinfo=timezone.utc)
    calls=install(monkeypatch,[first,later]);result=source_fixture('002594',{'closes':[90,91]})
    assert len(calls)==1 and result['market_meta']['fetched_at']==first.isoformat()
    assert result['ohlc'][-1]['d']=='2026-10-29'
