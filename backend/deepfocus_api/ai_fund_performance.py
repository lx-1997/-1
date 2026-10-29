"""Read-only simulated-account performance from unrounded stored amounts."""
from datetime import datetime, timedelta, timezone
import math

BJ = timezone(timedelta(hours=8))


def _day(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(BJ).date().isoformat()


def account_performance(conn, fund_id, nav, *, now=None, trading_day=True):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(BJ).date().isoformat()
    initial = float(nav['started_nav'])
    started = _day(nav['started_at'])
    days = {}
    for row in conn.execute('SELECT ts,nav FROM aif_nav WHERE fund_id=? ORDER BY ts', (fund_id,)):
        day = _day(row['ts'])
        value = float(row['nav'])
        if started <= day <= today and math.isfinite(value):
            days[day] = {'nav': value, 'ts': row['ts']}
    # Today's live valuation is never projected backwards onto an earlier date.
    if trading_day and math.isfinite(float(nav['nav'])):
        days[today] = {'nav': float(nav['nav']), 'ts': now.isoformat()}
    first_day = min(days) if days else None
    previous, previous_day = (initial, started) if first_day == started else (None, None)
    daily = []
    for day, point in sorted(days.items()):
        value = point['nav']
        daily.append({'date': day, 'nav': round(value, 2),
                      'pnl': round(value - previous, 2) if previous is not None else None,
                      'return_pct': round((value / previous - 1) * 100, 4) if previous is not None and previous > 0 else None,
                      'total_pnl': round(value - initial, 2),
                      'total_return_pct': round((value / initial - 1) * 100, 4) if initial > 0 else None,
                      'previous_date': previous_day, 'as_of': point['ts']})
        previous, previous_day = value, day
    today_point = next((p for p in daily if p['date'] == today), None)
    wins, losses = [], []
    for row in conn.execute("SELECT amount,pnl_pct FROM aif_trade WHERE fund_id=? AND side='sell' AND pnl_pct IS NOT NULL", (fund_id,)):
        pct, amount = float(row['pnl_pct']) / 100, float(row['amount'])
        # amount = original cost * (1 + pnl_pct), including simulated execution impact.
        if not math.isfinite(pct) or not math.isfinite(amount) or pct <= -1:
            continue
        pnl = amount * pct / (1 + pct)
        if pnl > 0: wins.append(pnl)
        elif pnl < 0: losses.append(-pnl)
    return {'initial_capital': round(initial, 2), 'total_pnl': round(float(nav['nav']) - initial, 2),
            'today_pnl': today_point['pnl'] if today_point else None,
            'today_return_pct': today_point['return_pct'] if today_point else None,
            'cash': round(float(nav['cash']), 2), 'market_value': round(float(nav['market_value']), 2),
            'payoff_ratio': round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 2) if wins and losses else None,
            'winning_trades': len(wins), 'losing_trades': len(losses),
            'daily': daily[-366:], 'as_of': now.isoformat(),
            'basis': '人民币模拟账户；无出入金。每日盈亏按相邻日期末次净值快照之差计算，当日采用最新估值；缺失日期不补值。盈亏比为已平仓平均盈利金额/平均亏损金额，需同时存在盈利与亏损样本。'}
