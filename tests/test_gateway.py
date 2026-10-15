import asyncio
import json
import time
import httpx
import pytest
from app.llm import Gateway,BudgetStop,ModelFailure,parse_json,provider_endpoint
from app.models import Fidelity
from app.store import dumps,uid

async def guard():
    pass


def setup_gateway(store,project,snapshot,handler):
    pid,jid,cfg=project
    cfg['demo']=False
    store.execute('UPDATE projects SET config=? WHERE id=?',(dumps(cfg),pid))
    return Gateway(store,pid,cfg,snapshot,guard,httpx.MockTransport(handler))


def response(content='{"preserved":true,"issues":[]}',finish='stop',usage=True):
    body={'choices':[{'message':{'content':content},'finish_reason':finish}]}
    if usage: body['usage']={'prompt_tokens':10,'completion_tokens':20}
    return httpx.Response(200,json=body)


def test_usage_and_checkpoint(store,project,snapshot):
    count=[]
    gateway=setup_gateway(store,project,snapshot,lambda req:(count.append(1) or response()))
    first=asyncio.run(gateway.ask('a','t',{},Fidelity))
    second=asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert first==second and len(count)==1
    assert store.usage(project[0])['charged']==30


def test_budget_checked_before_network(store,project,snapshot):
    def forbidden(req):
        raise AssertionError('Network should not run')
    gateway=setup_gateway(store,project,snapshot,forbidden)
    cfg=gateway.config.copy();cfg['token_budget']=1000
    store.execute('UPDATE projects SET config=? WHERE id=?',(dumps(cfg),project[0]))
    with pytest.raises(BudgetStop):asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert store.usage(project[0])['calls']==0


def test_invalid_json_repaired_once(store,project,snapshot):
    attempts=[]
    def handler(req):
        attempts.append(1)
        return response('not json' if len(attempts)==1 else '{"preserved":true,"issues":[]}')
    gateway=setup_gateway(store,project,snapshot,handler)
    assert asyncio.run(gateway.ask('a','t',{},Fidelity)).preserved
    assert len(attempts)==2 and store.usage(project[0])['charged']==60
    assert store.one("SELECT COUNT(*) n FROM calls WHERE state='invalid'")['n']==1


def test_invalid_json_not_infinite(store,project,snapshot):
    gateway=setup_gateway(store,project,snapshot,lambda req:response('bad'))
    with pytest.raises(ModelFailure):asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert store.usage(project[0])['calls']==2
    assert store.artifact(project[0],'a') is None


def test_truncation_is_failure(store,project,snapshot):
    gateway=setup_gateway(store,project,snapshot,lambda req:response(finish='length'))
    with pytest.raises(ModelFailure,match='截断'):asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert store.artifact(project[0],'a') is None
    assert store.usage(project[0])['charged']==30


def test_401_redacts_response_body(store,project,snapshot):
    gateway=setup_gateway(store,project,snapshot,lambda req:httpx.Response(401,text='secret echo test-secret-not-real'))
    with pytest.raises(ModelFailure,match='HTTP 401'):asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert all('test-secret' not in row['error'] for row in store.all('SELECT error FROM calls'))
    assert store.usage(project[0])['uncertain_tokens']>0


def test_missing_usage_is_explicit_conservative(store,project,snapshot):
    gateway=setup_gateway(store,project,snapshot,lambda req:response(usage=False))
    asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert store.usage(project[0])['uncertain_tokens']>0
    assert store.usage(project[0])['input_tokens']==0


def test_restart_inflight_reserved_charged(store,project):
    pid=project[0]
    store.execute("INSERT INTO calls(id,project_id,step,hash,provider,model,state,reserved,created) VALUES (?,?,?,?,?,?,?,?,?)",(uid(),pid,'a','hash','p','m','inflight',100,time.time()))
    store.recover()
    assert store.usage(pid)['reserved']==0
    assert store.usage(pid)['charged']==100
    store.recover()
    assert store.usage(pid)['charged']==100


def test_endpoint_and_strict_json():
    assert provider_endpoint('https://example.com/v1/')=='https://example.com/v1/chat/completions'
    assert provider_endpoint('https://example.com/v1/chat/completions')=='https://example.com/v1/chat/completions'
    for url in ['file:///etc/passwd','https://user:pass@example.com/v1','https://example.com/v1?api_key=secret']:
        with pytest.raises(ValueError):provider_endpoint(url)
    with pytest.raises(ValueError):parse_json('[]')
    assert parse_json('```json\n{"x":1}\n```')=={'x':1}


def test_success_cache_survives_missing_artifact(store,project,snapshot):
    count=[]
    gateway=setup_gateway(store,project,snapshot,lambda req:(count.append(1) or response()))
    asyncio.run(gateway.ask('a','t',{},Fidelity))
    store.execute('DELETE FROM artifacts WHERE project_id=?',(project[0],))
    asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert len(count)==1


def test_context_limit_rejected_without_silent_truncation(store,project,snapshot):
    snapshot['providers'][0]['context_tokens']=4096
    gateway=setup_gateway(store,project,snapshot,lambda req:response())
    with pytest.raises(ModelFailure,match='上下文'):
        asyncio.run(gateway.ask('a','t',{'huge':'甲'*20000},Fidelity))
    assert store.usage(project[0])['calls']==0


def test_timeout_retries_are_bounded_and_accounted(store,project,snapshot):
    def handler(req):
        raise httpx.ReadTimeout('not a real secret',request=req)
    gateway=setup_gateway(store,project,snapshot,handler)
    with pytest.raises(ModelFailure,match='超时'):
        asyncio.run(gateway.ask('a','t',{},Fidelity))
    usage=store.usage(project[0])
    assert usage['calls']==2 and usage['uncertain_tokens']>0


def test_large_model_response_rejected(store,project,snapshot):
    gateway=setup_gateway(store,project,snapshot,lambda req:httpx.Response(200,content=b'x'*1500001))
    with pytest.raises(ModelFailure,match='1.5MB'):
        asyncio.run(gateway.ask('a','t',{},Fidelity))
    assert store.artifact(project[0],'a') is None
