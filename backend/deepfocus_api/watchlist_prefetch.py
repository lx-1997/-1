"""Evidence-first fast lane for the terminal's exact watchlist update shortcut.
No general intent classifier, account reads, writes, caches, or model calls.
"""
from __future__ import annotations
import asyncio
import re

_PREFIX = re.compile(r'^帮我核对这些自选最近有哪些重要变化与风险[：:](.+)$')
_PAIR = re.compile(r'([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 .\-]{1,24})[（(](\d{5,6}|[A-Z]{1,5})[)）]')
TOOLS = frozenset({'get_market_quote','get_stock_news','search_our_content'})

def watchlist_update_plan(question: str) -> list[tuple[str,dict]]:
    match=_PREFIX.fullmatch(question.strip())
    if not match:return []
    parts=re.split(r'[、，,]',match.group(1))
    if not 1<=len(parts)<=6:return []
    seen=set();plan=[]
    for part in parts:
        item=_PAIR.fullmatch(part.strip())
        if not item:return []
        name,symbol=item.groups()
        if symbol in seen:return []
        seen.add(symbol)
        # Query each company separately so a shared result limit cannot
        # let one frequently mentioned stock crowd out its siblings.
        query=re.sub(r'[- ]W$','',name,flags=re.I)
        plan.extend([('get_market_quote',{'symbol':symbol}),('get_stock_news',{'symbol':symbol,'limit':3}),
                     ('search_our_content',{'query':query,'limit':3})])
    return plan

async def run_watchlist_plan(plan, execute, emit, *, concurrency=3, timeout=8.):
    semaphore=asyncio.Semaphore(concurrency)
    async def one(index,name,args):
        async with semaphore:
            await emit('tool_start',{'tool':name,'args':args})
            try:result=await asyncio.wait_for(execute(name,args),timeout=timeout)
            except asyncio.TimeoutError:result={'ok':False,'error':'本来源等待超时，未取得可核验资料'}
            except Exception:result={'ok':False,'error':'本来源暂不可用'}
            await emit('tool_result',{'tool':name,'ok':bool(result.get('ok'))})
            return index,name,args,result
    tasks=[asyncio.create_task(one(i,n,a)) for i,(n,a) in enumerate(plan)]
    try:return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
