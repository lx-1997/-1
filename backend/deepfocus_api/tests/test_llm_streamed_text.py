import asyncio
from types import SimpleNamespace as N
import pytest
from deepfocus_api.llm import CloudResearchLLM

class Stream:
    def __init__(self,gate=None,fail=False):self.closed=False;self.gate=gate;self.fail=fail
    async def __aiter__(self):
        for text in ['<think>内部推理</think>','第一段正文已经逐步输出。','第二段正文。']:
            yield N(choices=[N(delta=N(content=text),finish_reason=None)])
            if self.gate is not None and text == '第一段正文已经逐步输出。':await self.gate.wait()
        if self.fail:raise RuntimeError('provider disconnected')
    async def close(self):self.closed=True

@pytest.mark.asyncio
async def test_first_prose_arrives_before_completion_and_stream_is_closed(monkeypatch):
    adapter=CloudResearchLLM();gate=asyncio.Event();stream=Stream(gate);seen=[]
    async def complete(payload,**kw):assert payload['stream'];return stream
    monkeypatch.setattr(adapter,'_completion',complete)
    task=asyncio.create_task(adapter._stream_user_text({},timeout_seconds=5,on_delta=seen.append))
    await asyncio.sleep(0)
    assert seen and '第一段正文已经逐步输出。'.startswith(''.join(seen)) and not task.done()
    gate.set()
    text=await task
    assert text=='第一段正文已经逐步输出。第二段正文。' and text==''.join(seen) and stream.closed
    assert '内部推理' not in text

@pytest.mark.asyncio
async def test_cancel_and_disconnect_preserve_emitted_text_without_retry(monkeypatch):
    adapter=CloudResearchLLM();stream=Stream(fail=True);seen=[];calls=[]
    async def complete(*a,**kw):calls.append(1);return stream
    monkeypatch.setattr(adapter,'_completion',complete)
    with pytest.raises(RuntimeError):await adapter._stream_user_text({},timeout_seconds=5,on_delta=seen.append)
    assert seen and stream.closed and len(calls)==1
    gate=asyncio.Event();stream=Stream(gate)
    task=asyncio.create_task(adapter._stream_user_text({},timeout_seconds=5,on_delta=seen.append))
    await asyncio.sleep(0);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert stream.closed

@pytest.mark.asyncio
async def test_partial_answer_tokens_never_enter_the_persistent_event_ledger(monkeypatch):
    from deepfocus_api.core_agent import CoreAgent
    core=CoreAgent(CloudResearchLLM());seen=[]
    async def forbidden(*args,**kwargs):raise AssertionError('partial answer was persisted')
    async def emit(kind,payload):seen.append((kind,payload))
    monkeypatch.setattr(core,'_event',forbidden)
    await core._relay_tool_event('run','answer_delta',{'delta':'正文片段'},emit)
    assert seen==[('answer_delta',{'delta':'正文片段'})]
