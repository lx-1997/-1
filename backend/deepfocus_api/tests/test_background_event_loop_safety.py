"""Regression for production stacks that blocked /ready during background ingest."""
import asyncio
import ssl
import threading
import time
from types import SimpleNamespace
import pytest


@pytest.mark.asyncio
async def test_topic_normalization_and_share_writes_allow_heartbeat(monkeypatch):
    from deepfocus_api import zsxq_stream as zs, research_wire
    main_thread=threading.get_ident();calls=[];saved=[]
    def normalize(topic):
        calls.append(threading.get_ident());time.sleep(.12)
        return {'id':topic['id'],'title':'fixture','text':'not sent anywhere'}
    def persist(items):
        calls.append(threading.get_ident());time.sleep(.10);saved.extend(items)
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'items':[{'id':str(i)} for i in range(3)]})
    monkeypatch.setattr(zs,'_CACHE',{})
    monkeypatch.setattr(zs,'stream_groups',lambda:[{'id':'fixture','name':'fixture'}])
    monkeypatch.setattr(research_wire,'auth_payload',lambda:{})
    monkeypatch.setattr(zs.httpx,'AsyncClient',Client)
    monkeypatch.setattr(zs,'_norm_topic',normalize);monkeypatch.setattr(zs,'_persist_share_topics',persist)
    task=asyncio.create_task(zs.fetch_stream(group='fixture',use_cache=False))
    for _ in range(5):await asyncio.sleep(.01)
    assert not task.done(),'slow SQLite work must not freeze the event loop'
    result=await task
    assert len(result['items'])==3 and result['items']==saved
    assert calls and all(t!=main_thread for t in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('method',['_poll_loop','_handle_batch'])
async def test_weixin_binding_reads_allow_heartbeat(monkeypatch,method):
    from deepfocus_api import weixin_channel as ch
    main_thread=threading.get_ident();threads=[]
    def read(_):threads.append(threading.get_ident());time.sleep(.2);return None
    monkeypatch.setattr(ch.bind,'get_by_bot',read)
    async def forbidden(*_):raise AssertionError('no real bot traffic in this test')
    monkeypatch.setattr(ch.ilink,'get_updates',forbidden);monkeypatch.setattr(ch.ilink,'send_text',forbidden)
    manager=ch.WeixinChannelManager(forbidden);manager._running=True
    task=asyncio.create_task(manager._poll_loop('fixture') if method=='_poll_loop' else manager._handle_batch('fixture',[]))
    for _ in range(5):await asyncio.sleep(.01)
    assert not task.done()
    await task
    assert threads and all(t!=main_thread for t in threads)


def test_weixin_clients_reuse_verified_context_without_reloading_ca(monkeypatch):
    from deepfocus_api import weixin_ilink as ilink
    calls=[]
    monkeypatch.setattr(ilink.httpx,'AsyncClient',lambda **kwargs:calls.append(kwargs) or SimpleNamespace())
    monkeypatch.setattr(ssl,'create_default_context',lambda **_:(_ for _ in ()).throw(AssertionError('CA must not reload in hot polling')))
    ilink._client(1);ilink._client(2);ilink.push_client(3)
    assert len(calls)==3 and all(c['verify'] is ilink._TLS_CONTEXT for c in calls)
    assert all(c['trust_env'] is False for c in calls)
    assert ilink._TLS_CONTEXT.verify_mode==ssl.CERT_REQUIRED and ilink._TLS_CONTEXT.check_hostname


@pytest.mark.asyncio
async def test_valid_bound_member_reply_and_owner_context_are_preserved(tmp_path,monkeypatch):
    from deepfocus_api import weixin_channel as ch,auth,data_store,metrics_store,ifind_api,realtime_messages
    from deepfocus_api.ownership import bind_owner,current_owner_id
    monkeypatch.setattr(data_store,'DB_PATH',tmp_path/'data.sqlite3');data_store.init_data_store()
    monkeypatch.setattr(metrics_store,'DB_PATH',tmp_path/'metrics.sqlite3');metrics_store.init_db()
    binding={'active':True,'deepfocus_user_id':'fixture-owner','username':'fixture-member','ilink_bot_id':'fixture-bot','token':'fixture-token','base_url':'https://fixture.invalid'}
    reads=[];sent=[];questions=[]
    def read(_):reads.append(current_owner_id());return dict(binding)
    monkeypatch.setattr(ch.bind,'get_by_bot',read)
    monkeypatch.setattr(ch.bind,'update_context_token',lambda *_:None)
    monkeypatch.setattr(auth,'membership_of_username',lambda _: {'tier':'premium'})
    role='viewer' if 'viewer' in auth.VALID_ROLES else sorted(auth.VALID_ROLES)[0]
    account=SimpleNamespace(id='fixture-owner',role=role,is_active=True,username='fixture-member',membership={'tier':'premium'})
    monkeypatch.setattr(auth,'get_user_out_by_id',lambda _:account,raising=False)
    monkeypatch.setattr(ifind_api,'allowed_usernames',lambda:set())
    monkeypatch.setattr(ifind_api,'user_allowed',lambda _:False)
    monkeypatch.setattr(realtime_messages,'is_futoucaixin_restricted_user',lambda _:True)
    async def send(*args,**kwargs):sent.append(args[4]);return {'ret':0}
    monkeypatch.setattr(ch.ilink,'send_text',send)
    monkeypatch.setattr(ch.ilink,'extract_inbound_text',lambda m:m.get('text',''))
    async def agent(question,hint):
        questions.append((question,current_owner_id()));return 'fixture authorized analysis'
    agent.qa_cache_policy_fn=lambda:{'provider':'fixture','model':'fixture','protocol':'no-network-v1'}
    manager=ch.WeixinChannelManager(agent)
    message={'message_type':1,'from_user_id':'fixture-peer','context_token':'fixture-context','text':'fixture internal research question'}
    with bind_owner('fixture-owner'):await manager._handle_batch('fixture-bot',[message])
    assert questions==[(message['text'],'fixture-owner')]
    assert sent and any('fixture authorized analysis' in text for text in sent)
    assert reads and all(owner=='fixture-owner' for owner in reads)
