import json
import time
import pytest
from app.models import ProjectInput,Provider
from app.store import Store,dumps,uid

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'test.sqlite3')

@pytest.fixture
def project(store):
    cfg=ProjectInput(title='测试小说',chapter_count=2,chapter_chars=800,demo=True,humanizer=False).model_dump()
    pid,jid=uid(),uid()
    now=time.time()
    store.execute('INSERT INTO projects(id,title,config,created,updated) VALUES (?,?,?,?,?)',(pid,'测试小说',dumps(cfg),now,now))
    store.execute('INSERT INTO jobs(id,project_id,status,snapshot,created,updated) VALUES (?,?,?,?,?,?)',(jid,pid,'running','{}',now,now))
    return pid,jid,cfg

@pytest.fixture
def snapshot():
    return {'providers':[Provider(id='one',label='测试模型',base_url='https://model.example/v1',model='fixture-model',api_key='test-secret-not-real',context_tokens=200000).model_dump()],'roles':{}}
