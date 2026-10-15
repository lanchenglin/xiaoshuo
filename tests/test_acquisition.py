"""Offline fixtures: no copyrighted novels, public websites or paid model calls."""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.acquisition import (BookAcquirer, Catalog, SearXNG, book_list, catalogue,
                             chapter_number, digest, html_soup, numeric_gaps)
from app.acquisition_models import AcquisitionOptions
from app.engine import NovelEngine
from app.llm import PipelineError, UserStop
from app.main import create_app
from app.models import Analysis, Mechanism, Synthesis
from app.sources import FetchError
from app.store import dumps

BASE = 'https://books.example'


async def nothing(*args):
    return None


def text(n):
    return (f'这是本测试自己编写的第{n}段小说素材。人物走进院子，检查钥匙，然后决定把信交给门外的朋友。' * 6)


def toc(numbers, more='', container=True):
    links = ''.join(f'<a href="/chapter/{i}">第{i}章 自编测试{i}</a>' for i in numbers)
    return '<html><head><meta property="og:novel:book_name" content="测试参考书"><meta property="og:novel:author" content="测试作者"><meta property="og:novel:status" content="连载"></head><body>' + (f'<div id="list">{links}</div>' if container else links) + more + '</body></html>'


class Pages:
    def __init__(self, mapping):
        self.mapping = mapping
        self.requests = []
        self.denied = set()

    async def allowed(self, url):
        return url not in self.denied

    async def get(self, url, max_bytes=3000000):
        self.requests.append(url)
        value = self.mapping.get(url)
        if value is None:
            raise FetchError('来源 HTTP 404')
        if isinstance(value, Exception):
            raise value
        return url, value.encode('utf-8'), 'text/html; charset=utf-8'


def make_acquirer(store, project, pages=None, **kwargs):
    pid, jid, cfg = project
    cfg = {**cfg, 'demo': False, 'urls': [BASE + '/book/test']}
    options = AcquisitionOptions(mode='books', book_count=1, backend='native', retries=0,
                                 acquire_only=True, **kwargs)
    cfg['acquisition'] = options.model_dump()
    store.execute('UPDATE projects SET config=? WHERE id=?', (dumps(cfg), pid))
    if pages is None:
        pages = Pages({BASE + '/book/test': toc([1, 2, 3]),
                       **{BASE + f'/chapter/{i}': f'<div id="content">{text(i)}</div>' for i in [1, 2, 3]}})
    return BookAcquirer(store, pid, cfg, nothing, pages, sleeper=nothing), pages


@pytest.mark.parametrize('label,expected', [('第123章 现场',123),('第一百零二章',102),('第两千零一章',2001),('Chapter 28',28),('第二十回',20),('不是章节',None)])
def test_chapter_numbers(label, expected):
    assert chapter_number(label) == expected


def test_full_toc_not_sixty_link_sample():
    raw = toc(range(1, 202)).encode()
    chapters, pages, recognized = catalogue(html_soup(raw), BASE + '/book/test', 1000)
    assert len(chapters) == 201 and chapters[-1]['label'].startswith('第201章')
    assert recognized and pages == []


def test_toc_cap_is_error_not_silent_truncation():
    with pytest.raises(FetchError, match='章节上限'):
        catalogue(html_soup(toc(range(1, 20)).encode()), BASE, 5)


def test_whole_book_order_manifest_and_cache(store, project):
    acq, pages = make_acquirer(store, project)
    asyncio.run(acq.run())
    book = book_list(store, project[0])[0]
    assert book['status'] == 'complete_snapshot'
    assert book['manifest']['downloaded_chapters'] == 3
    assert book['manifest']['work_completion'] == 'ongoing'
    content = (acq.root / book['id'] / 'book.txt').read_text(encoding='utf-8-sig')
    assert content.index('第1章') < content.index('第2章') < content.index('第3章')
    original_requests = list(pages.requests)
    asyncio.run(acq.run())
    assert pages.requests == original_requests
    source = store.one("SELECT * FROM sources WHERE project_id=? AND status='ready'", (project[0],))
    assert source and json.loads(source['meta'])['catalogue_snapshot_complete']
    assert store.usage(project[0])['calls'] == 0


def test_missing_chapter_blocks_writing_and_resume_only_missing(store, project):
    acq, pages = make_acquirer(store, project)
    del pages.mapping[BASE + '/chapter/2']
    with pytest.raises(PipelineError, match='1 本|0/1'):
        asyncio.run(acq.run())
    book = book_list(store, project[0])[0]
    assert book['status'] == 'partial' and book['manifest']['missing_chapters'] == 1
    assert not store.one("SELECT id FROM sources WHERE project_id=? AND status='ready'", (project[0],))
    before = len(pages.requests)
    pages.mapping[BASE + '/chapter/2'] = '<div id="content">' + text(2) + '</div>'
    asyncio.run(acq.run())
    assert pages.requests[before:] == [BASE + '/chapter/2']
    assert book_list(store, project[0])[0]['status'] == 'complete_snapshot'


def test_partial_is_explicit_and_missing_not_fabricated(store, project):
    acq, pages = make_acquirer(store, project, allow_partial=True)
    del pages.mapping[BASE + '/chapter/2']
    asyncio.run(acq.run())
    book = book_list(store, project[0])[0]
    source = store.one("SELECT * FROM sources WHERE project_id=? AND status='ready'", (project[0],))
    assert not json.loads(source['meta'])['catalogue_snapshot_complete']
    assert text(2) not in source['text']
    assert '此章缺失' in (acq.root / book['id'] / 'book.txt').read_text(encoding='utf-8-sig')


def test_corrupt_cached_content_is_not_imported_as_partial(store, project):
    acq, pages = make_acquirer(store, project, allow_partial=True)
    asyncio.run(acq.run())
    bid = book_list(store, project[0])[0]['id']
    store.execute("UPDATE reference_chapters SET text='corrupted' WHERE book_id=? AND ordinal=2", (bid,))
    report = acq.finalize(bid)
    assert report['missing_chapters'] == 1
    store.execute("UPDATE sources SET status='excluded' WHERE project_id=?", (project[0],))
    assert acq.import_book(bid)
    source = store.one("SELECT text FROM sources WHERE project_id=? AND status='ready'", (project[0],))
    assert 'corrupted' not in source['text']


def test_exact_duplicate_chapter_fails(store, project):
    acq, pages = make_acquirer(store, project)
    pages.mapping[BASE + '/chapter/2'] = pages.mapping[BASE + '/chapter/1']
    with pytest.raises(PipelineError):
        asyncio.run(acq.run())
    book = book_list(store, project[0])[0]
    assert '重复' in book['manifest']['missing'][0]['error']


def test_directory_gap_blocks_complete_claim(store, project):
    acq, pages = make_acquirer(store, project)
    pages.mapping[BASE + '/book/test'] = toc([1, 3])
    with pytest.raises(PipelineError):
        asyncio.run(acq.run())
    book = book_list(store, project[0])[0]
    assert book['status'] == 'partial'
    assert book['manifest']['missing_chapters'] == 0
    assert '跳跃' in book['manifest']['catalogue_issues'][0]


def test_toc_pagination_loads_all_entries(store, project):
    acq, pages = make_acquirer(store, project)
    pages.mapping[BASE + '/book/test'] = toc([1,2], '<a href="/book/test?page=2">下一页</a>')
    pages.mapping[BASE + '/book/test?page=2'] = toc([3,4])
    pages.mapping[BASE + '/chapter/4'] = '<div id="content">' + text(4) + '</div>'
    asyncio.run(acq.run())
    b = book_list(store, project[0])[0]
    assert b['manifest']['expected_chapters'] == 4
    assert len(b['manifest']['catalogue_pages']) == 2


def test_toc_pagination_cap_marks_incomplete(store, project):
    acq, pages = make_acquirer(store, project, max_toc_pages=1)
    pages.mapping[BASE + '/book/test'] = toc([1,2], '<a href="/book/test?page=2">下一页</a>')
    with pytest.raises(PipelineError):
        asyncio.run(acq.run())
    assert '未遍历完' in book_list(store, project[0])[0]['manifest']['catalogue_issues'][0]


def test_chapter_pagination_not_next_chapter(store, project):
    acq, pages = make_acquirer(store, project)
    pages.mapping[BASE + '/chapter/1'] = '<div id="content">' + text(1) + '</div><a href="/chapter/1?page=2">下一页</a><a href="/chapter/2">下一章</a>'
    pages.mapping[BASE + '/chapter/1?page=2'] = '<div id="content">' + text('1续页') + '</div>'
    content = asyncio.run(acq.chapter_text(BASE + '/chapter/1'))
    assert text(1) in content and text('1续页') in content
    assert BASE + '/chapter/2' not in pages.requests


@pytest.mark.parametrize('page', ['<div id="content">订阅本章后即可阅读。' * 20 + '</div>', '<h1>没有正文</h1>', '<div id="content">太短。</div>'])
def test_invalid_chapter_not_success(store, project, page):
    acq, pages = make_acquirer(store, project)
    pages.mapping[BASE + '/chapter/1'] = page
    with pytest.raises(FetchError):
        asyncio.run(acq.chapter_text(BASE + '/chapter/1'))


def test_quota_does_not_truncate_chapters(store, project):
    acq, pages = make_acquirer(store, project, max_book_chars=1000)
    pages.mapping[BASE + '/chapter/1'] = '<div id="content">' + text(1) * 10 + '</div>'
    with pytest.raises(PipelineError):
        asyncio.run(acq.run())
    b = book_list(store, project[0])[0]
    assert b['status'] == 'failed' and b['downloaded'] == 0
    assert '字符上限' in b['manifest']['missing'][0]['error']


def test_robots_denial_skips_chapter(store, project):
    acq, pages = make_acquirer(store, project)
    pages.denied.add(BASE + '/chapter/2')
    with pytest.raises(PipelineError):
        asyncio.run(acq.run())
    assert BASE + '/chapter/2' not in pages.requests


def test_stop_keeps_successful_chapters(store, project):
    acq, pages = make_acquirer(store, project)
    async def stop():
        if store.one("SELECT ordinal FROM reference_chapters WHERE status='ready' LIMIT 1"):
            raise UserStop('pause')
    acq.guard = stop
    with pytest.raises(UserStop):
        asyncio.run(acq.run())
    assert store.one("SELECT count(*) n FROM reference_chapters WHERE status='ready'")['n'] == 1


def test_catalogue_cannot_be_reordered_on_resume(store, project):
    acq, pages = make_acquirer(store, project)
    cat = Catalog(BASE+'/book/test', '测试', chapters=[{'title':'第1章','url':BASE+'/chapter/1'},{'title':'第2章','url':BASE+'/chapter/2'}])
    acq.save_catalog(cat, {})
    cat.chapters.reverse()
    with pytest.raises(FetchError, match='版本变化'):
        acq.save_catalog(cat, {})


def test_search_json_sanitization_and_provenance():
    def response(req):
        assert req.url.path == '/search'
        assert req.url.params['format'] == 'json'
        return httpx.Response(200, json={'results':[
            {'url':'http://127.0.0.1/private','title':'bad'},
            {'url':'http://localhost/private','title':'bad'},
            {'url':'file:///etc/passwd','title':'bad'},
            {'url':BASE+'/book/a','title':'作品A','content':'公开摘要'},
            {'url':BASE+'/book/a','title':'duplicate'}]})
    result = asyncio.run(SearXNG('http://localhost:8080', httpx.MockTransport(response)).search('测试榜单'))
    assert len(result) == 1 and result[0]['url'] == BASE+'/book/a'
    assert result[0]['query'] == '测试榜单'
    assert result[0]['heat_verified'] is False


def test_search_403_explains_json_setting():
    with pytest.raises(FetchError, match='JSON'):
        asyncio.run(SearXNG('http://localhost:8080', httpx.MockTransport(lambda r:httpx.Response(403))).search('query'))


def test_unconfigured_search_is_not_fake_results():
    with pytest.raises(FetchError, match='未配置'):
        asyncio.run(SearXNG('').search('query'))


def test_search_result_can_download_without_llm(store, project):
    acq, pages = make_acquirer(store, project)
    acq.cfg['urls']=[]
    acq.options.queries=['作品 目录']
    class Search:
        async def search(self, query):
            return [{'url':BASE+'/book/test','label':'测试参考书','evidence':'搜索结果','query':query,'heat_verified':False}]
    acq.searcher=Search()
    asyncio.run(acq.run())
    assert store.artifact(project[0],'acquisition/done')['accepted_count']==1
    assert store.usage(project[0])['calls']==0


def test_full_analysis_reads_all_supplied_ranges(store, project):
    pid,jid,cfg=project
    cfg['acquisition']=AcquisitionOptions(mode='books',analysis='full').model_dump()
    cfg['sample_chars_per_source']=1000
    store.execute('UPDATE projects SET config=? WHERE id=?',(dumps(cfg),pid))
    engine=NovelEngine(store,pid,jid,{})
    spans=[]
    class Gateway:
        async def ask(self,key,prompt,payload,model,**kwargs):
            if model is Analysis:
                spans.append(payload['range'])
                return Analysis(summary='分析自编片段',mechanisms=[Mechanism(name='具体行动',explanation='通过动作推进',evidence=payload['text'][:10],transferable='以选择推进冲突')])
            return Synthesis(audience='测试',methods=['人物行动'],avoid_copying=['不复制具体事件'])
    engine.gateway=Gateway()
    original=text(1)*50
    asyncio.run(engine.research([{'id':'fixture','title':'自编参考','text':original,'digest':digest(original),'meta':'{}'}]))
    assert sum(b-a for a,b in spans)==len(original)
    assert store.artifact(pid,'coverage/fixture')['sampled'] is False


def test_options_reject_shell_parameters_and_unbounded_counts():
    with pytest.raises(ValidationError):AcquisitionOptions(command='rm -rf /')
    with pytest.raises(ValidationError):AcquisitionOptions(max_chapters=1000000)
    with pytest.raises(ValidationError):AcquisitionOptions(queries=['x'*301])


def test_api_download_only_without_model_and_new_assets(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False,password='test-password-long')) as c:
        c.headers['X-Requested-With']='MoLiu'
        c.post('/api/auth/login',json={'password':'test-password-long'})
        r=c.post('/api/projects',json={'urls':[BASE+'/book/test'],'acquisition':{'mode':'books','book_count':1,'acquire_only':True}})
        assert r.status_code==201
        pid=r.json()['id']
        assert c.post(f'/api/projects/{pid}/start').status_code==200
        assert c.put(f'/api/projects/{pid}/acquisition',json={'mode':'books'}).status_code==409
        assert c.get(f'/api/projects/{pid}/books/unknown/export/txt').status_code==404
        assert c.post(f'/api/projects/{pid}/continue-writing').status_code==409
        assert 'acquisition.js' in c.get('/').text
        assert c.get('/assets/acquisition.js').status_code==200


def test_bridge_contract_offline(tmp_path, monkeypatch):
    path=Path(__file__).resolve().parents[1]/'scripts/lncrawl_worker.py'
    spec=importlib.util.spec_from_file_location('tested_lncrawl_worker',path)
    bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
    monkeypatch.setenv('LNCRAWL_DATA_PATH',str(tmp_path/'runtime'))
    monkeypatch.setattr(bridge,'validate_public',lambda u:None)
    monkeypatch.setattr(bridge.time,'sleep',lambda n:None)
    output=io.StringIO();bridge.PROTOCOL_OUT=output
    chapters=[SimpleNamespace(id=str(n),serial=n,title=f'第{n}章',url=BASE+f'/chapter/{n}',is_available=True,content_file=str(n)) for n in [2,1]]
    calls=[]
    class Context:
        def setup(self,**kwargs):assert kwargs['sync_remote_index'] is False
        def destroy(self):calls.append('destroy')
    ctx=Context()
    ctx.sources=SimpleNamespace(init_crawler=lambda u:object())
    ctx.scraper=SimpleNamespace(set_rate_limit=lambda *a:None)
    ctx.users=SimpleNamespace(get_admin=lambda:SimpleNamespace(id='fixture-user'))
    ctx.chapters=SimpleNamespace(list_ids=lambda **kw:['2','1'],get_many=lambda ids:chapters)
    ctx.files=SimpleNamespace(load_text=lambda f:'<p>'+text(f)+'</p>')
    ctx.crawler=SimpleNamespace(fetch_novel=lambda *a,**kw:SimpleNamespace(id='fixture-novel',title='测试',authors='测试作者',chapter_count=2),fetch_chapter=lambda user, cid, **kw:next(c for c in chapters if c.id==cid))
    bridge.run({'url':BASE+'/book/test','skip':[BASE+'/chapter/1']},ctx)
    events=[json.loads(line) for line in output.getvalue().splitlines()]
    assert events[0]['event']=='catalogue' and events[0]['chapters'][0]['title']=='第1章'
    downloaded=[x for x in events if x['event']=='chapter']
    assert len(downloaded)==1 and downloaded[0]['ordinal']==2
    assert events[-1]['event']=='finished' and calls==['destroy']


def test_ranking_list_not_mistaken_for_book_catalogue():
    raw='<html><title>热门小说排行榜</title><div id="list"><a href="/book/one">作品一</a><a href="/book/two">作品二</a></div></html>'
    entries,pages,recognized=catalogue(html_soup(raw.encode()),BASE+'/rank',100)
    assert entries==[] and not recognized


def test_search_failure_tries_next_query(store,project):
    acq,pages=make_acquirer(store,project)
    acq.cfg['urls']=[]
    acq.options.queries=['first','second']
    calls=[]
    class Search:
        async def search(self,query):
            calls.append(query)
            if query=='first':raise FetchError('search temporarily failed')
            return [{'url':BASE+'/book/test','label':'测试','evidence':'result'}]
    acq.searcher=Search()
    asyncio.run(acq.run())
    assert calls==['first','second']
    assert store.artifact(project[0],'acquisition/done')
