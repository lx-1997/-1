"""Bounded public daily-series acquisition with provenance and shared refresh.
Two independent vendors; choose latest completed series, never splice volume units.
"""
from __future__ import annotations
import asyncio
import json
import time
from datetime import datetime
import httpx
from .market_regime import BJ, UNIVERSE, VERSION, normalize, build_gauge

INDEXES = {'csi300': ('1.000300','sh000300'), 'csi500': ('1.000905','sh000905'),
           'chinext': ('0.399006','sz399006'), 'sse': ('1.000001','sh000001')}
HEADERS = {'User-Agent':'Mozilla/5.0 Chrome/132.0.0.0', 'Referer':'https://finance.sina.com.cn/'}
_cache: tuple[float, dict | None] = (0., None)
_inflight: asyncio.Task | None = None

async def vendor_series(client: httpx.AsyncClient, vendor: str, key: str, now: datetime) -> list[dict]:
    east, sina = INDEXES[key]
    try:
        if vendor == 'eastmoney':
            url = f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={east}&fields1=f1&fields2=f51,f52,f53,f56&klt=101&fqt=0&end=20500101&lmt=1023'
            response = await client.get(url); response.raise_for_status()
            data = response.json(); rows = []
            for line in ((data.get('data') or {}).get('klines') or []):
                p = line.split(',')
                if len(p) >= 4: rows.append({'date':p[0], 'open':p[1], 'close':p[2], 'volume':p[3]})
        else:
            url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina}&scale=240&ma=no&datalen=1023'
            response = await client.get(url); response.raise_for_status(); data = response.json()
            if isinstance(data,str): data=json.loads(data)
            rows=[{'date':str(r.get('day',''))[:10], 'open':r.get('open'), 'close':r.get('close'), 'volume':r.get('volume')} for r in (data or []) if isinstance(r,dict)]
        return normalize(rows, now)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return []

async def acquire(now: datetime) -> tuple[dict, list]:
    async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(12.,connect=5.), headers=HEADERS) as client:
        keys = [(key,vendor) for key in INDEXES for vendor in ('eastmoney','sina')]
        results = await asyncio.gather(*(vendor_series(client,vendor,key,now) for key,vendor in keys))
    fetched = dict(zip(keys,results)); series, sources = {}, []
    for key in INDEXES:
        candidates = [(vendor,fetched[(key,vendor)]) for vendor in ('eastmoney','sina') if len(fetched[(key,vendor)])>=120]
        candidates.sort(key=lambda c:(c[1][-1]['date'],len(c[1])),reverse=True)
        vendor, bars = candidates[0] if candidates else ('unavailable',[])
        conflict, deviation = False, None
        if len(candidates)==2:
            other = {r['date']:r for r in candidates[1][1]}
            if bars[-1]['date'] in other:
                deviation = abs(bars[-1]['close']/other[bars[-1]['date']]['close']-1)*100
                conflict = deviation > .5
        series[key] = bars
        sources.append({'key':key, 'name':UNIVERSE[key], 'provider':vendor,
                        'as_of':bars[-1]['date'] if bars else None, 'rows':len(bars),
                        'cross_checked':deviation is not None, 'close_deviation_pct':round(deviation,4) if deviation is not None else None,
                        'conflict':conflict,
                        'vendors':[{'provider':v,'as_of':fetched[(key,v)][-1]['date'] if fetched[(key,v)] else None,'rows':len(fetched[(key,v)])} for v in ('eastmoney','sina')]})
    return series,sources

async def _refresh() -> dict:
    global _cache
    now = datetime.now(BJ)
    try:
        series,sources = await asyncio.wait_for(acquire(now), timeout=16.)
        result = await asyncio.to_thread(build_gauge,series,sources,now)
    except (TimeoutError, httpx.HTTPError, ValueError):
        result = {'available':False,'reason':'多源日线暂不可用，暂停综合评分','model_version':VERSION}
    _cache = (time.monotonic(), result)
    return result

async def market_gauge_v4() -> dict:
    global _inflight
    ts,hit = _cache
    if hit is not None and time.monotonic()-ts < (1800 if hit.get('available') else 60):
        return hit
    if _inflight is None or _inflight.done():
        _inflight = asyncio.create_task(_refresh())
    # Cancellation propagates to the caller; shared bounded data collection may
    # finish for other callers. It cannot create writes or model jobs.
    return await asyncio.shield(_inflight)
