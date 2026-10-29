"""Deterministic safeguards for the paper portfolio; no broker or model calls."""
from datetime import datetime, timedelta, timezone
import math
import re

VERSION = 'paper-v2.0'
LEARNING_ACTIVE = False
ROTATION_ACTIVE = False
BJ = timezone(timedelta(hours=8))
MAX_QUOTE_AGE = 600
MAX_POSITION = .20
MAX_GROSS = .80
MAX_GROUP = .35
RISK_PER_TRADE = .005
MAX_DRAWDOWN = .08
MIN_LEARNING_CLOSES = 30


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def quote_time(q):
    raw = q.get('time') or q.get('exchange_time')
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        return dt.replace(tzinfo=BJ) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def executable(q, now=None):
    now = now or datetime.now(timezone.utc)
    if not q or (number(q.get('latest')) or 0) <= 0:
        return False
    stamp = quote_time(q)
    if stamp is None or stamp.astimezone(BJ).date() != now.astimezone(BJ).date():
        return False
    age = (now - stamp).total_seconds()
    return (-5 <= age <= MAX_QUOTE_AGE and number(q.get('changeRatio')) is not None
            and not q.get('suspended') and q.get('trading_status') not in {'halted', 'suspended'})


def direct_events(name, items, max_age):
    """Only explicit target mentions qualify; body-only recall stays observational."""
    result, seen = [], set()
    for item in items:
        age = number(item.get('age_h'))
        title = str(item.get('title') or '')
        if age is None or not 0 <= age <= max_age or not name or name not in title:
            continue
        if re.search(re.escape(name) + r'的?(供应商|客户|竞争对手|参股公司|子公司)', title):
            continue
        if '：' in title and name not in title.split('：', 1)[0]:
            continue
        key = re.sub(r'[\W_]+', '', title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({**item, 'relation': 'explicit_title_mention', 'target_name': name})
    return result


def completed_closes(md, now=None):
    """A cached partial daily bar and the current quote must not become two days."""
    today = (now or datetime.now(timezone.utc)).astimezone(BJ).date().isoformat()
    bars = (md or {}).get('ohlc') or []
    if bars:
        return [float(bar['c']) for bar in bars
                if str(bar.get('d') or '')[:10] < today and str(bar.get('d') or '')[:10]
                and (number(bar.get('c')) or 0) > 0]
    return list((md or {}).get('closes') or [])


def exit_reason(cfg, price, avg, high_water, analysis, *, bear=False, age_days=0):
    pnl = (price - avg) / avg if avg else 0
    tech = analysis.get('tech') or {}
    ma20 = tech.get('ma20')
    trail = max(cfg.trail_min, 2.2 * (tech.get('vol') or .03))
    if bear:
        trail = max(.05, trail * .65)
    if pnl <= cfg.hard_stop:
        return f'硬止损 {pnl*100:+.1f}%'
    if high_water > avg and price <= high_water * (1-trail) and pnl > 0:
        return f'移动止盈：自高点回落，落袋 {pnl*100:+.1f}%'
    if analysis.get('msg_dir', 0) < -.35 and analysis.get('fresh'):
        return f'利空催化兑现 {pnl*100:+.1f}%'
    if cfg.model == 'reversion':
        if ma20 and price >= ma20 and pnl > 0:
            return f'均值回归目标到达 {pnl*100:+.1f}%'
        if age_days >= 2 and not analysis.get('model_ready') and pnl < 0:
            return f'逆向企稳失效 {pnl*100:+.1f}%'
        if age_days >= 10 and pnl <= 0:
            return f'逆向时间止损 {pnl*100:+.1f}%'
        return None
    if cfg.model == 'value':
        detail = analysis.get('scores') or {}
        if detail.get('质量') is not None and detail['质量'] < -.4:
            return f'价值论点失效：盈利质量转弱 {pnl*100:+.1f}%'
        if detail.get('估值') is not None and detail['估值'] < -.4 and pnl > 0:
            return f'估值修复目标到达 {pnl*100:+.1f}%'
        return None
    if cfg.style == 'event' and age_days >= 5 and not analysis.get('catalyst_ok') and pnl <= 0:
        return f'事件到期未兑现 {pnl*100:+.1f}%'
    if bear and ma20 and price < ma20 and pnl < .02:
        return f'大盘转空+个股破位，依指数止损出局 {pnl*100:+.1f}%'
    if ma20 and price < ma20 * .985:
        return f'跌破20日线（{ma20}）止盈止损 {pnl*100:+.1f}%'
    if analysis.get('score', 0) <= cfg.sell_threshold:
        return f'信号转弱 {pnl*100:+.1f}%'
    return None


# Explicit conservative risk buckets for the current pool, not inferred by an LLM.
GROUPS = {}
for group, codes in {
    '半导体': '688981 688256 603501 002371 688012',
    '新能源车': '002594 300750 601127',
    '科技硬件': '002475 002415 300308 002241 300433',
    '软件': '300059 002230 688111',
    '工业': '300124', '资源': '600111 601899',
    '能源公用': '601012 601088 600900',
    '医药': '300760 600276', '金融': '601318 600036', '消费': '600519 000858',
}.items():
    GROUPS.update({code: group for code in codes.split()})


def risk_budget(code, nav, cash, holdings, prices, stop, peak):
    if nav <= 0 or peak <= 0 or (nav / peak - 1) <= -MAX_DRAWDOWN:
        return 0.0
    gross = sum(float(p['qty']) * prices[p['symbol']] for p in holdings)
    group = GROUPS.get(code, '未分类')
    grouped = sum(float(p['qty']) * prices[p['symbol']] for p in holdings
                  if GROUPS.get(p['symbol'], '未分类') == group)
    return max(0., min(cash, nav*MAX_POSITION, nav*RISK_PER_TRADE/max(abs(stop), .01),
                       nav*MAX_GROSS-gross, nav*MAX_GROUP-grouped))


def order_quantity(code, budget, price):
    if price <= 0:
        return 0
    # Minimum STAR order is 200, with 1-share increments above that.
    if code.startswith(('688', '689')):
        qty = math.floor(budget / price)
        return qty if qty >= 200 else 0
    return max(0, math.floor(budget / price / 100) * 100)


def valuation_price(conn, fund_id, position, quote):
    """Never substitute average cost for a missing quote."""
    price = number((quote or {}).get('latest'))
    stamp = quote_time(quote or {})
    row = conn.execute('SELECT price,ts FROM aif_marks WHERE symbol=?', (position['symbol'],)).fetchone()
    previous_stamp = quote_time({'time': row['ts']}) if row else None
    if (price and price > 0 and stamp and stamp <= datetime.now(timezone.utc)+timedelta(seconds=5)
            and (previous_stamp is None or stamp >= previous_stamp)):
        return price, not executable(quote), 'quote'
    if row and number(row['price']) and float(row['price']) > 0:
        return float(row['price']), True, 'last_trusted_mark'
    row = conn.execute("SELECT price FROM aif_trade WHERE fund_id=? AND symbol=? AND side='buy' ORDER BY ts DESC LIMIT 1",
                       (fund_id, position['symbol'])).fetchone()
    if row and number(row['price']) and float(row['price']) > 0:
        return float(row['price']), True, 'last_execution'
    return None, True, 'unpriced'


def audit_metadata():
    return {'version': VERSION, 'mode': 'paper_only', 'risk_per_trade': RISK_PER_TRADE,
            'single_position_cap': MAX_POSITION, 'gross_cap': MAX_GROSS,
            'group_cap': MAX_GROUP, 'drawdown_pause': MAX_DRAWDOWN,
            'learning': 'frozen_pending_out_of_sample_validation',
            'score_rotation': 'disabled_pending_execution_validation',
            'validation': 'not_yet_proven_profitable',
            'live_trading_enabled': False,
            'unvalidated': ['corporate_actions', 'liquidity_fill_model', 'out_of_sample_alpha'],
            'fee_assumptions': {'commission': .00025, 'minimum': 5, 'sell_stamp': .0005, 'transfer': .00001},
            'note': '历史成交按原版本保留；新增风险限额为待验证的保守初值，不是已优化的收益参数。'}
