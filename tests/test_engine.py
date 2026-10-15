import asyncio
import json
import httpx
import pytest
from app import demo,models
from app.engine import NovelEngine,QualityStop
from app.llm import UserStop
from app.store import dumps,uid


def test_demo_entire_book_and_resume(store,project):
    pid,jid,cfg=project
    engine=NovelEngine(store,pid,jid,{})
    asyncio.run(engine.run())
    chapters=store.all('SELECT * FROM chapters WHERE project_id=? ORDER BY number',(pid,))
    assert [c['number'] for c in chapters]==[1,2]
    assert len(store.all('SELECT * FROM revisions WHERE project_id=?',(pid,)))==4
    assert store.artifact(pid,'audit/final')
    assert store.artifact(pid,'plan/selected')['id']=='1'
    calls=store.usage(pid)['calls']
    assert store.usage(pid)['charged']==0
    asyncio.run(NovelEngine(store,pid,jid,{}).run())
    assert store.usage(pid)['calls']==calls
    assert store.all('SELECT text FROM chapters WHERE project_id=? ORDER BY number',(pid,))==[{'text':c['text']} for c in chapters]


def test_full_http_mock_pipeline(store,project,snapshot):
    pid,jid,cfg=project
    cfg['demo']=False
    store.execute('UPDATE projects SET config=? WHERE id=?',(dumps(cfg),pid))
    source='这份测试资料描述人物通过修理工单寻找线索，重点是信息如何通过行动获得。'*30
    store.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)',(uid(),pid,'upload:test','测试资料',source,'fixture-source','ready','{}'))
    calls=[]
    def handler(request):
        assert request.headers['Authorization']=='Bearer test-secret-not-real'
        payload=json.loads(request.content)
        spec=json.loads(payload['messages'][1]['content'])
        schema=getattr(models,spec['schema']['title'])
        response=demo.generate(schema,spec['data'])
        if schema is models.Analysis:
            response['mechanisms'][0]['evidence']=spec['data']['text'][:35]
        calls.append(schema.__name__)
        return httpx.Response(200,json={'choices':[{'message':{'content':dumps(response)},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':200}})
    asyncio.run(NovelEngine(store,pid,jid,snapshot,transport=httpx.MockTransport(handler)).run())
    assert len(store.all('SELECT * FROM chapters WHERE project_id=?',(pid,)))==2
    assert 'Fidelity' in calls and 'Review' in calls and 'Analysis' in calls
    assert store.usage(pid)['charged']==len(calls)*300
    assert store.usage(pid)['uncertain_tokens']==0


def test_pause_before_first_step(store,project):
    pid,jid,cfg=project
    store.execute('UPDATE jobs SET stop=? WHERE id=?',('pause',jid))
    with pytest.raises(UserStop):
        asyncio.run(NovelEngine(store,pid,jid,{}).run())
    assert store.usage(pid)['calls']==0


def test_polish_fidelity_failure_keeps_draft(store,project):
    pid,jid,cfg=project
    engine=NovelEngine(store,pid,jid,{})
    original=engine.gateway.ask
    async def ask(*args,**kwargs):
        if args[3] is models.Fidelity:
            return models.Fidelity(preserved=False,issues=['测试：改动了关键事件'])
        return await original(*args,**kwargs)
    engine.gateway.ask=ask
    asyncio.run(engine.run())
    chapters=store.all('SELECT report FROM chapters WHERE project_id=?',(pid,))
    assert all(json.loads(c['report'])['polish']=='rejected-kept-draft' for c in chapters)
    assert len(store.all("SELECT * FROM revisions WHERE project_id=? AND label='polished-rejected'",(pid,)))==2


def test_quality_gate_stops_instead_of_fake_success(store,project):
    pid,jid,cfg=project
    engine=NovelEngine(store,pid,jid,{})
    original=engine.gateway.ask
    async def ask(*args,**kwargs):
        if args[3] is models.Review:
            return models.Review(score=10,issues=[models.Issue(severity='critical',category='continuity',detail='测试冲突')],repair_brief='修复冲突')
        return await original(*args,**kwargs)
    engine.gateway.ask=ask
    with pytest.raises(QualityStop):
        asyncio.run(engine.run())
    assert not store.all('SELECT * FROM chapters WHERE project_id=?',(pid,))
    assert len(store.all('SELECT * FROM revisions WHERE project_id=?',(pid,)))==3


def test_missing_sources_not_hallucinated(store,project,snapshot):
    pid,jid,cfg=project
    cfg['demo']=False
    store.execute('UPDATE projects SET config=? WHERE id=?',(dumps(cfg),pid))
    from app.llm import PipelineError
    with pytest.raises(PipelineError,match='没有取得'):
        asyncio.run(NovelEngine(store,pid,jid,snapshot).run())
