"""Synthetic, request-bound provider fixtures; never call a real data service."""
from datetime import datetime,timedelta,timezone
import json
from deepfocus_api import ai_fund_market as market


def source_fixture(symbol,payload=None):
    payload=payload or {};rows=payload.get('ohlc');stamp=market.datetime.now(timezone.utc)
    if not rows:
        closes=payload.get('closes') or [100.]
        today=stamp.astimezone(market.BJ).date();days=[];d=today
        while len(days)<len(closes):
            d-=timedelta(days=1)
            if d.weekday()<5:days.append(d.isoformat())
        rows=[{'d':d,'o':c,'h':c*1.01,'l':c*.99,'c':c,'v':1000000} for d,c in zip(reversed(days),closes)]
    raw=[{'day':r['d'],'open':r.get('o',r['c']),'high':r.get('h',r['c']*1.01),
          'low':r.get('l',r['c']*.99),'close':r['c'],'volume':r.get('v',1000000)} for r in rows]
    prefix='sh' if symbol.startswith('6') else 'sz'
    snapshot=market.parse(json.dumps(raw).encode(),symbol,'sina',f'https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={prefix}{symbol}&scale=240',fetched_at=stamp)
    return {**payload,**market.fields(snapshot)}


def source_map(payload):return {s:source_fixture(s,p) for s,p in payload.items()}
