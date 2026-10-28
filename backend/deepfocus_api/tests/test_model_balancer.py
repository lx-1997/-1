from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from deepfocus_api import model_balancer as balancer
from deepfocus_api import llm as module

@pytest.fixture(autouse=True)
def reset():
    balancer._cursors.clear()
    yield
    balancer._cursors.clear()

def pool():
    return [{'provider':'openai-compatible','model':name,'base_url':'https://unit.invalid','api_key':'fixture-'+name} for name in ['glm-5.3-flash','qwen3.6-flash']]

def test_rotation_default_first_and_disabled():
    p=pool()
    assert [balancer.balance_candidates(p,True)[0]['model'] for _ in range(6)]==[p[0]['model'],p[1]['model']]*3
    assert balancer.balance_candidates(p,False)==p
    assert balancer.balance_candidates([p[1]],True)==[p[1]]

def test_concurrent_rotation_and_bounded_state():
    p=pool()
    with ThreadPoolExecutor(max_workers=8) as executor:
        values=list(executor.map(lambda _:balancer.balance_candidates(p,True)[0]['model'],range(100)))
    assert values.count('glm-5.3-flash')==values.count('qwen3.6-flash')==50
    for i in range(50):balancer.balance_candidates([{**c,'api_key':str(i)} for c in p],True)
    assert len(balancer._cursors)==32

@pytest.mark.asyncio
async def test_completion_rotates_model_and_matching_credential_together(monkeypatch):
    p=pool();calls=[]
    monkeypatch.setattr(module,'load_model_config',lambda:{**p[0],'model_pool':p,'load_balance':True})
    monkeypatch.setattr(module,'slot_available',lambda c:True)
    llm=module.CloudResearchLLM()
    def client(config):
        async def create(**payload):
            calls.append((config['model'],payload['model'],config['api_key']))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='OK'))])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm,'_client_for_config',client)
    for _ in range(4):await llm._completion({'model':p[0]['model'],'messages':[]},timeout_seconds=2)
    assert [x[0] for x in calls]==['glm-5.3-flash','qwen3.6-flash']*2
    assert all(config_model==payload_model and key=='fixture-'+payload_model for config_model,payload_model,key in calls)
