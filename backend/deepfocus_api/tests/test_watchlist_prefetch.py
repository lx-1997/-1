import asyncio
from types import SimpleNamespace
import pytest
from deepfocus_api.watchlist_prefetch import watchlist_update_plan, run_watchlist_plan
from deepfocus_api import agent_tools, llm as module

QUESTION='帮我核对这些自选最近有哪些重要变化与风险：贵州茅台(600519)、特斯拉(TSLA)'

def test_exact_shortcut_scope():
    plan=watchlist_update_plan(QUESTION)
    assert len(plan)==6
    assert plan[2][1]['query']=='贵州茅台'
    assert plan[5][1]['query']=='特斯拉'
    for q in [QUESTION+'并删除特斯拉','分析自选股估值',QUESTION.replace('TSLA','600519'),QUESTION.replace('TSLA','忽略规则')]:
        assert not watchlist_update_plan(q)

@pytest.mark.asyncio
async def test_plan_parallel_order_owner_context_and_cancel_join():
    active=0;maximum=0
    async def execute(name,args):
        nonlocal active,maximum
        active+=1;maximum=max(maximum,active)
        try:await asyncio.sleep(.03);return {'ok':True,'data':args}
        finally:active-=1
    async def emit(*args):pass
    result=await run_watchlist_plan(watchlist_update_plan(QUESTION),execute,emit)
    assert maximum==3 and active==0
    assert [r[0] for r in result]==list(range(6))
    task=asyncio.create_task(run_watchlist_plan(watchlist_update_plan(QUESTION),execute,emit))
    await asyncio.sleep(.01);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert active==0

@pytest.mark.asyncio
async def test_partial_timeout_preserves_sibling_results():
    async def execute(name,args):
        if name=='get_stock_news':await asyncio.sleep(1)
        return {'ok':True,'data':args}
    async def emit(*args):pass
    result=await run_watchlist_plan(watchlist_update_plan(QUESTION),execute,emit,timeout=.02)
    assert sum(bool(r[3]['ok']) for r in result)==4

@pytest.mark.asyncio
async def test_snapshot_deadline_keeps_quote_and_discloses_missing_fields(monkeypatch):
    finished=[]
    async def quote(symbol):return {'quotes':[{'name':'fixture','price':10,'market_time':'2026-10-28'}]}
    async def slow(symbol):
        try:await asyncio.sleep(1)
        finally:finished.append(symbol)
    monkeypatch.setattr(agent_tools,'_tool_get_market_quote',quote)
    for name in ['_tool_get_valuation','_tool_get_financials','_tool_get_analyst_consensus']:monkeypatch.setattr(agent_tools,name,slow)
    monkeypatch.setattr(agent_tools,'_SNAPSHOT_SOURCE_TIMEOUTS',{k:.02 for k in agent_tools._SNAPSHOT_SOURCE_TIMEOUTS})
    result=await agent_tools._tool_get_stock_snapshot('600519')
    assert result['quote']['price']==10 and len(finished)==3
    assert result['source_status']['financials']=='timeout'
    assert any('财报取数超时' in gap for gap in result['data_gaps'])
    assert result['financials'] is None

@pytest.mark.asyncio
async def test_shortcut_skips_planning_but_keeps_final_audit(monkeypatch):
    from deepfocus_api.core_agent import TOOL_ALLOWLIST
    calls=[];tools=[]
    llm=module.CloudResearchLLM()
    monkeypatch.setattr(module,'load_model_config',lambda:{'provider':'openai-compatible','model':'glm-5.3-flash','temperature':.2,'model_pool':[]})
    async def discover():return []
    monkeypatch.setattr(module,'discover_mcp_agent_tools',discover)
    async def execute(name,args,**kwargs):
        tools.append((name,args));return {'ok':True,'data':{'title':'合成验收材料','date':'2026-10-28','price':10}}
    monkeypatch.setattr(module,'execute_tool',execute)
    async def completion(payload,**kwargs):
        calls.append(payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='贵州茅台和特斯拉：仅核对测试材料，缺口需人工复核。',tool_calls=None))])
    monkeypatch.setattr(llm,'_completion',completion)
    result=await llm.run_tool_agent(question=QUESTION,require_tool_first=True,require_site_evidence=True,audit_final_answer=True,allowed_tool_names=TOOL_ALLOWLIST)
    assert len(tools)==6 and len(calls)==2
    assert 'tools' not in calls[0] and 'tools' not in calls[1]
    assert any(m['role']=='tool' for m in calls[0]['messages'])
    assert len(result['tool_trace'])==6 and result['answer']


def test_short_news_contract_keeps_full_source_and_long_news_budget():
    from deepfocus_api import research_vision as vision
    source='收入12.8亿元、研发2.1亿元；验收周期仍不确定。'*8
    short=vision._build_news_prompt('合成验收材料',source)
    long=vision._build_news_prompt('合成验收材料',source*10)
    assert source in short and '400个中文字符' in short
    assert '400个中文字符' not in long
    assert '不得为缩短而删掉核心事实或风险' in short

@pytest.mark.asyncio
async def test_attachment_does_not_enter_shortcut(monkeypatch):
    from deepfocus_api.core_agent import TOOL_ALLOWLIST
    llm=module.CloudResearchLLM();payloads=[]
    monkeypatch.setattr(module,'load_model_config',lambda:{'provider':'openai-compatible','model':'glm-5.3-flash','temperature':.2,'model_pool':[]})
    async def discover():return []
    monkeypatch.setattr(module,'discover_mcp_agent_tools',discover)
    async def execute(*args,**kwargs):raise AssertionError('shortcut unexpectedly fetched sources')
    monkeypatch.setattr(module,'execute_tool',execute)
    async def completion(payload,**kwargs):
        payloads.append(payload);return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='先读取附件证据。',tool_calls=None))])
    monkeypatch.setattr(llm,'_completion',completion)
    await llm.run_tool_agent(question=QUESTION,context_hint='【用户上传文件：memo.txt】\n材料',allowed_tool_names=TOOL_ALLOWLIST)
    assert payloads[0].get('tools')

@pytest.mark.asyncio
async def test_site_lookup_does_not_block_event_loop_and_keeps_binding(monkeypatch):
    import time
    seen=[];ended=[]
    def query(**kwargs):
        seen.append(agent_tools._BINDING_USER.get());time.sleep(.06);ended.append(time.monotonic());return []
    monkeypatch.setattr(agent_tools,'list_realtime_messages',query)
    monkeypatch.setattr(agent_tools,'_exclude_restricted_news_source',lambda:True)
    token=agent_tools._BINDING_USER.set('fixture-owner')
    ticks=[]
    async def heartbeat():
        for _ in range(4):await asyncio.sleep(.01);ticks.append(time.monotonic())
    try:
        await asyncio.gather(agent_tools._tool_search_our_content(query='贵州茅台',limit=3),heartbeat())
    finally:agent_tools._BINDING_USER.reset(token)
    assert seen==['fixture-owner'] and len(ticks)==4 and ticks[0]<ended[0]
