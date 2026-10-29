"""Persistent daily NAV and risk observations; historical trades stay immutable."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from .ai_fund_policy import BJ, VERSION


def fee(amount, side):
    """Explicit paper assumptions, not a statement of any broker's actual tariff."""
    amount = Decimal(str(amount))
    commission = max(Decimal('5'), amount * Decimal('.00025'))
    transfer = amount * Decimal('.00001')
    stamp = amount * Decimal('.0005') if side == 'sell' else Decimal(0)
    return float((commission + transfer + stamp).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def day(ts):
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(BJ).date().isoformat()


def record(conn, fund_id, ts, nav, initial, *, trading_day=True):
    if trading_day:
        conn.execute('INSERT INTO aif_nav_daily(fund_id,date,ts,nav) VALUES(?,?,?,?) '
                     'ON CONFLICT(fund_id,date) DO UPDATE SET ts=excluded.ts,nav=excluded.nav WHERE excluded.ts>=aif_nav_daily.ts',
                     (fund_id, day(ts), ts, nav))
    previous = conn.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?', (fund_id,)).fetchone()
    peak = max(initial, nav, float(previous['peak']) if previous else initial)
    worst = min(float(previous['worst']) if previous else 0, (nav/peak-1)*100 if peak else 0)
    conn.execute('INSERT OR REPLACE INTO aif_risk_state(fund_id,peak,worst) VALUES(?,?,?)', (fund_id, peak, worst))


def initialize(conn, trading_day_fn=None):
    conn.execute('CREATE TABLE IF NOT EXISTS aif_nav_daily(fund_id TEXT,date TEXT,ts TEXT,nav REAL,PRIMARY KEY(fund_id,date))')
    conn.execute('CREATE TABLE IF NOT EXISTS aif_risk_state(fund_id TEXT PRIMARY KEY,peak REAL,worst REAL)')
    conn.execute('CREATE TABLE IF NOT EXISTS aif_policy_epoch(fund_id TEXT,version TEXT,started_at TEXT,PRIMARY KEY(fund_id,version))')
    conn.execute('CREATE TABLE IF NOT EXISTS aif_trade_outbox(trade_id TEXT PRIMARY KEY,fund_id TEXT,symbol TEXT,payload TEXT,delivered INTEGER DEFAULT 0)')
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(aif_trade)')}
    for name, kind in [('fee', 'REAL'), ('realized_pnl', 'REAL')]:
        if name not in columns:
            conn.execute(f'ALTER TABLE aif_trade ADD COLUMN {name} {kind}')
    for st in conn.execute('SELECT fund_id,started_nav FROM aif_state').fetchall():
        fund_id = st['fund_id']
        if conn.execute('SELECT 1 FROM aif_policy_epoch WHERE fund_id=? AND version=?', (fund_id, VERSION)).fetchone():
            continue
        # Seed only once, using remaining original observations, without invented history.
        for row in conn.execute('SELECT ts,nav FROM aif_nav WHERE fund_id=? ORDER BY ts', (fund_id,)).fetchall():
            dt = datetime.fromisoformat(row['ts'].replace('Z', '+00:00')).astimezone(BJ)
            trading = trading_day_fn(dt) if trading_day_fn else dt.weekday() < 5
            record(conn, fund_id, row['ts'], float(row['nav']), float(st['started_nav']), trading_day=trading)
        conn.execute('INSERT INTO aif_policy_epoch VALUES(?,?,?)', (fund_id, VERSION, datetime.now(timezone.utc).isoformat()))


def drawdown(conn, fund_id, nav, initial):
    previous = conn.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?', (fund_id,)).fetchone()
    peak = max(initial, nav, float(previous['peak']) if previous else initial)
    return min(float(previous['worst']) if previous else 0, (nav/peak-1)*100 if peak else 0)


def enqueue(conn, trade_id, fund_id, symbol, payload):
    conn.execute('INSERT OR IGNORE INTO aif_trade_outbox VALUES(?,?,?,?,0)',
                 (trade_id, fund_id, symbol, json.dumps({**payload, 'trade_id': trade_id}, ensure_ascii=False)))


def flush_events(conn, fund_id, record_fn):
    # At-least-once ancillary delivery. Canonical fills remain unique by trade_id.
    conn.execute('BEGIN IMMEDIATE')
    for row in conn.execute('SELECT * FROM aif_trade_outbox WHERE fund_id=? AND delivered=0 LIMIT 20', (fund_id,)).fetchall():
        try:
            acknowledged = record_fn('simulation_trade', row['symbol'], json.loads(row['payload']), market='A')
            if acknowledged is not True:
                continue
        except Exception:
            continue
        conn.execute('UPDATE aif_trade_outbox SET delivered=1 WHERE trade_id=?', (row['trade_id'],))
    conn.commit()
