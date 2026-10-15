import asyncio
import io
import json
import zipfile
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.engine import NovelEngine

PASSWORD='a-long-test-password'

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,start_worker=False,password=PASSWORD)
    with TestClient(app) as c:
        c.headers['X-Requested-With']='MoLiu'
        assert c.post('/api/auth/login',json={'password':PASSWORD}).status_code==200
        yield c


def create_project(client,**kwargs):
    data={'demo':True,'humanizer':False,'chapter_count':2,'chapter_chars':800,**kwargs}
    response=client.post('/api/projects',json=data)
    assert response.status_code==201,response.text
    return response.json()['id']


def test_auth_csrf_and_logout(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False,password=PASSWORD)) as c:
        assert c.get('/api/health').status_code==200
        assert c.get('/api/projects').status_code==401
        assert c.post('/api/auth/login',json={'password':PASSWORD}).status_code==403
        c.headers['X-Requested-With']='MoLiu'
        assert c.post('/api/auth/login',json={'password':'wrong'}).status_code==401
        assert c.post('/api/auth/login',json={'password':PASSWORD}).status_code==200
        assert c.post('/api/projects',json={},headers={'origin':'https://evil.example'}).status_code==403
        assert 'httponly' in c.cookies.get('moliu_session','').lower() or c.cookies.get('moliu_session')
        assert c.post('/api/auth/logout').status_code==200
        assert c.get('/api/projects').status_code==401


def test_secret_encrypted_and_not_returned(client):
    body={'providers':[{'id':'p','label':'模型','base_url':'https://example.com/v1','model':'m','api_key':'should-never-appear'}],'roles':{'writer':'p'}}
    assert client.put('/api/settings',json=body).status_code==200
    assert 'should-never-appear' not in client.get('/api/settings').text
    encrypted=client.app.state.store.setting('providers_encrypted')
    assert 'should-never-appear' not in encrypted
    body['providers'][0]['api_key']=''
    assert client.put('/api/settings',json=body).status_code==200
    decrypted=client.app.state.security.decrypt(client.app.state.store.setting('providers_encrypted'))
    assert decrypted['providers'][0]['api_key']=='should-never-appear'


def test_duplicate_start_and_pause(client):
    pid=create_project(client)
    assert client.post(f'/api/projects/{pid}/start').status_code==200
    assert client.post(f'/api/projects/{pid}/start').status_code==409
    assert client.post(f'/api/projects/{pid}/stop/pause').status_code==200
    assert client.get(f'/api/projects/{pid}').json()['project']['status']=='paused'
    assert client.post(f'/api/projects/{pid}/start').status_code==200
    assert 'snapshot' not in client.get(f'/api/projects/{pid}').text


def test_upload_and_sealed_sources(client):
    pid=create_project(client)
    text='可分析参考内容，记录人物选择与行动。'*30
    assert client.post(f'/api/projects/{pid}/sources/text',json={'title':'素材','text':text}).status_code==201
    assert client.post(f'/api/projects/{pid}/sources/text',json={'title':'重复','text':text}).status_code==409
    assert client.post(f'/api/projects/{pid}/start').status_code==200
    assert client.post(f'/api/projects/{pid}/sources/text',json={'title':'追加','text':text+'不同内容'}).status_code==409


def test_utf8_and_gb18030_upload(client):
    pid=create_project(client)
    for encoding in ['utf-8-sig','gb18030']:
        content=('参考内容：'+encoding+'。')*80
        r=client.post(f'/api/projects/{pid}/sources/file',files={'file':('novel.txt',content.encode(encoding),'text/plain')})
        assert r.status_code==201,r.text
    assert client.post(f'/api/projects/{pid}/sources/file',files={'file':('bad.pdf',b'%PDF fake','application/pdf')}).status_code==400


def test_export_chapter_order_and_no_secrets(client):
    pid=create_project(client)
    jid=client.post(f'/api/projects/{pid}/start').json()['job_id']
    store=client.app.state.store
    asyncio.run(NovelEngine(store,pid,jid,{}).run())
    response=client.get(f'/api/projects/{pid}/export/txt')
    text=response.content.decode('utf-8-sig')
    assert text.index('第1章')<text.index('第2章')
    response=client.get(f'/api/projects/{pid}/export/zip')
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert 'novel.txt' in z.namelist() and 'chapters/0002.txt' in z.namelist()
        assert all(not p.startswith('/') and '..' not in p.split('/') for p in z.namelist())
        assert all('api_key' not in z.read(p).decode('utf-8-sig') for p in z.namelist())


def test_bad_model_role(client):
    assert client.put('/api/settings',json={'providers':[],'roles':{'writer':'missing'}}).status_code==400


def test_unknown_project(client):
    assert client.get('/api/projects/missing').status_code==404


def test_worker_lock_prevents_two_workers(tmp_path):
    from app.main import WorkerLock
    a=WorkerLock(tmp_path/'worker.lock')
    try:
        with pytest.raises(RuntimeError):WorkerLock(tmp_path/'worker.lock')
    finally:a.close()
    b=WorkerLock(tmp_path/'worker.lock');b.close()


def test_non_demo_start_requires_models(client):
    pid=create_project(client,demo=False)
    assert client.post(f'/api/projects/{pid}/start').status_code==400


def test_budgets_do_not_zero_prior_usage(client):
    pid=create_project(client)
    assert client.put(f'/api/projects/{pid}/budget',json={'token_budget':123456,'max_revisions':4}).status_code==200
    cfg=client.get(f'/api/projects/{pid}').json()['project']['config']
    assert cfg['token_budget']==123456 and cfg['max_revisions']==4


def test_project_archive_path_components_are_constrained():
    from app.models import Concept
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Concept(id='../bad',title='测试',premise='原始故事前提。'*10,difference='独立的故事和角色设计。',conflict='人物追求目标遇到明确障碍。',ending='完成主要目标。')
