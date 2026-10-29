import sqlite3
from datetime import datetime, timezone
import pytest
from deepfocus_api.ai_fund_performance import account_performance


def make():
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE aif_nav(fund_id TEXT,ts TEXT,nav REAL)')
    conn.execute('CREATE TABLE aif_trade(fund_id TEXT,side TEXT,amount REAL,pnl_pct REAL)')
    nav = dict(started_nav=1000000, started_at='2026-10-27T01:00:00+00:00', nav=1005121.37, cash=300000, market_value=705121.37)
    return conn, nav


def test_unrounded_daily_amounts_and_day_end_dedup():
    conn, nav = make()
    conn.executemany('INSERT INTO aif_nav VALUES(?,?,?)', [('a','2026-10-27T01:00:00+00:00',999000.12), ('a','2026-10-27T07:00:00+00:00',995000.33), ('a','2026-10-28T07:00:00+00:00',994500.21), ('b','2026-10-28T07:00:00+00:00',1)])
    p = account_performance(conn, 'a', nav, now=datetime(2026,10,29,8,tzinfo=timezone.utc))
    assert p['today_pnl'] == 10621.16 and p['total_pnl'] == 5121.37
    assert [x['pnl'] for x in p['daily']] == [-4999.67,-500.12,10621.16]
    assert p['today_return_pct'] == pytest.approx(1.0680)
    assert sum(x['pnl'] for x in p['daily']) == pytest.approx(p['total_pnl'])


def test_beijing_day_and_no_weekend_projection():
    conn, nav = make()
    conn.execute('INSERT INTO aif_nav VALUES(?,?,?)',('a','2026-10-28T18:00:00+00:00',1000001.23))
    p = account_performance(conn,'a',nav,now=datetime(2026,10,31,4,tzinfo=timezone.utc),trading_day=False)
    assert p['daily'][0]['date'] == '2026-10-29'
    assert p['today_pnl'] is None and len(p['daily']) == 1
    assert p['daily'][0]['pnl'] is None


def test_payoff_uses_average_amounts_instead_of_sum_or_percentage():
    conn, nav = make()
    # Profits 100 and 300, loss 50; average payoff 200/50=4.
    conn.executemany('INSERT INTO aif_trade VALUES(?,?,?,?)', [('a','sell',1100,10),('a','sell',3300,10),('a','sell',450,-10),('b','sell',1,-90),('a','buy',99999,99)])
    p = account_performance(conn,'a',nav)
    assert p['payoff_ratio'] == 4 and p['winning_trades'] == 2 and p['losing_trades'] == 1


@pytest.mark.parametrize('trades',[[],[('a','sell',1100,10)],[('a','sell',900,-10)],[('a','sell',1000,0)]])
def test_no_fake_ratio_when_one_side_missing(trades):
    conn, nav = make()
    conn.executemany('INSERT INTO aif_trade VALUES(?,?,?,?)',trades)
    assert account_performance(conn,'a',nav)['payoff_ratio'] is None


def test_read_only_missing_dates_remain_missing():
    conn, nav = make()
    conn.execute('INSERT INTO aif_nav VALUES(?,?,?)',('a','2026-10-27T07:00:00+00:00',999000))
    conn.execute('PRAGMA query_only=ON')
    p = account_performance(conn,'a',nav,now=datetime(2026,10,29,8,tzinfo=timezone.utc))
    assert [r['date'] for r in p['daily']] == ['2026-10-27','2026-10-29']
    assert p['daily'][-1]['previous_date'] == '2026-10-27'
