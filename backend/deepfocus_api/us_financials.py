"""US earnings from the platform's existing Eastmoney source, with explicit periods."""
from __future__ import annotations
import re
import time
from datetime import datetime, timezone
import httpx
from .shared_utils import safe_float

_CACHE = {}
_URL = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'
_FIELDS = {'revenue': 'OPERATE_INCOME', 'revenue_yoy': 'OPERATE_INCOME_YOY',
           'net_income': 'PARENT_HOLDER_NETPROFIT', 'profit_yoy': 'PARENT_HOLDER_NETPROFIT_YOY',
           'eps': 'BASIC_EPS', 'roe': 'ROE_AVG', 'gross_margin': 'GROSS_PROFIT_RATIO'}


def normalize_earnings(row, symbol):
    """Reject a wrong issuer, unknown currency or future disclosure; do not invent missing metrics."""
    if not isinstance(row, dict) or str(row.get('SECURITY_CODE') or '').upper() != symbol:
        return None
    try:
        end = datetime.fromisoformat(str(row['REPORT_DATE'])[:10]).date()
        start = datetime.fromisoformat(str(row['START_DATE'])[:10]).date()
        notice = datetime.fromisoformat(str(row['NOTICE_DATE'])[:10]).date()
    except (KeyError, ValueError, TypeError):
        return None
    today = datetime.now(timezone.utc).date()
    if not start <= end <= notice <= today or row.get('CURRENCY_ABBR') != 'USD':
        return None
    metrics = {k: safe_float(row.get(v)) for k, v in _FIELDS.items()}
    if not any(metrics[k] is not None for k in ('revenue', 'net_income', 'eps')):
        return None
    return {**metrics, 'symbol': symbol, 'name': row.get('SECURITY_NAME_ABBR'), 'currency': 'USD',
            'report_date': end.isoformat(), 'period_start': start.isoformat(),
            'published_date': notice.isoformat(), 'period_basis': row.get('DATE_TYPE'),
            'report_type': row.get('REPORT_TYPE'),
            'period_key': str(row.get('REPORT_TYPE') or '').replace('/', '-') or None, 'net_income_basis': 'parent_attributable',
            'provider': 'eastmoney', 'source_url': _URL,
            'fetched_at': datetime.now(timezone.utc).isoformat(), 'is_realtime': False}


async def fetch_us_earnings(symbol, market=None):
    symbol = re.sub(r'\.US$', '', str(symbol or '').strip().upper())
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol) or (market or 'US').upper() != 'US':
        return None
    hit = _CACHE.get(symbol)
    if hit and time.monotonic() - hit[0] < 900:
        return dict(hit[1])
    async with httpx.AsyncClient(trust_env=False, timeout=6.0) as client:
        response = await client.get(_URL, params={
            'reportName': 'RPT_USF10_FN_GMAININDICATOR', 'columns': 'ALL', 'pageSize': 8,
            'sortColumns': 'REPORT_DATE,NOTICE_DATE', 'sortTypes': '-1,-1',
            'filter': f'(SECURITY_CODE="{symbol}")'}, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        payload = response.json()
    if not payload.get('success'):
        return None
    rows = (payload.get('result') or {}).get('data') or []
    normalized = [value for row in rows if (value := normalize_earnings(row, symbol))]
    if not normalized:
        return None
    result = max(normalized, key=lambda x: (x['report_date'], x['published_date']))
    _CACHE[symbol] = (time.monotonic(), result)
    return dict(result)
