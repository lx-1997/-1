import json
import pytest
from deepfocus_api.streamed_prose import streamed_prose, REPORT_PROSE_DELTA, complete_report_json

def test_incomplete_paragraphs_are_readable_before_json_is_complete():
    prefix='{"one_liner":"材料显示需求改善","sections":[{"title":"需求","paragraphs":["收入增长20%，仍需核对'
    body=streamed_prose(prefix)
    assert body['intro']=='材料显示需求改善'
    assert body['sections'][0]['paragraphs']==['收入增长20%，仍需核对']
    assert streamed_prose(prefix+'后续订单。"]}]}')['sections'][0]['paragraphs'][0].endswith('后续订单。')

def test_escapes_unicode_and_hidden_or_nested_fields_do_not_escape():
    full=json.dumps({'reasoning':'秘密推理','nested':{'summary':'不应展示'},'summary':'第一行\n第二行"引号"😀'},ensure_ascii=True)
    for length in range(1,len(full)+1):
        result=streamed_prose(full[:length]);json.dumps(result,ensure_ascii=False).encode('utf-8')
        assert '秘密推理' not in str(result) and '不应展示' not in str(result)
    assert streamed_prose(full)['summary']=='第一行\n第二行"引号"😀'
    assert streamed_prose('```json\n'+full)=={}

def test_compact_vision_logic_lines_are_rendered_as_prose():
    body=streamed_prose('{"summary":"扫描版摘要","logic_lines":[{"title":"订单","evidence":"订单上升","chain":"带来收入')
    assert body['sections'][0]['paragraphs']==['订单上升','带来收入']

@pytest.mark.asyncio
async def test_report_sink_is_optional_and_does_not_change_background_calls():
    class Model:
        async def complete_json(self,prompt,**kwargs):return {'summary':'cached contract'}
        async def complete_json_streaming(self,prompt,on_delta,**kwargs):
            on_delta('{"summary":"开始');return {'summary':'开始结束'}
    model=Model()
    assert await complete_report_json(model,'x')=={'summary':'cached contract'}
    events=[];token=REPORT_PROSE_DELTA.set(events.append)
    try:assert await complete_report_json(model,'x')=={'summary':'开始结束'}
    finally:REPORT_PROSE_DELTA.reset(token)
    assert events==[None,'{"summary":"开始']
