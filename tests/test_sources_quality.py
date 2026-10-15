import asyncio
import socket
import httpx
import pytest
from app.sources import SafeFetcher,FetchError,ensure_public,normalize_url,extract_page,sample_chunks
from app.quality import diagnose,char_count

@pytest.mark.parametrize('ip',['127.0.0.1','10.1.2.3','192.168.1.1','169.254.169.254','::1','::ffff:127.0.0.1','224.0.0.1','0.0.0.0'])
def test_private_sources_blocked(ip):
    with pytest.raises(FetchError):ensure_public(ip)


def resolver(host,port,*args):
    return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1' if host=='localhost' else '8.8.8.8',port))]


def test_pinned_dns_and_tls_name():
    def handler(req):
        assert req.url.host=='8.8.8.8'
        assert req.headers['host']=='fiction.example'
        assert req.extensions['sni_hostname']=='fiction.example'
        return httpx.Response(200,headers={'Content-Type':'text/plain'},content=b'hello')
    fetcher=SafeFetcher(httpx.MockTransport(handler),resolver)
    actual,raw,_=asyncio.run(fetcher.get('https://fiction.example/test'))
    assert actual=='https://fiction.example/test' and raw==b'hello'


def test_redirect_private_rejected():
    fetcher=SafeFetcher(httpx.MockTransport(lambda req:httpx.Response(302,headers={'location':'http://localhost/admin'})),resolver)
    with pytest.raises(FetchError):asyncio.run(fetcher.get('https://fiction.example/test'))


def test_source_size_limit():
    fetcher=SafeFetcher(httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/plain'},content=b'a'*20)),resolver)
    with pytest.raises(FetchError):asyncio.run(fetcher.get('https://fiction.example/test',max_bytes=10))


def test_robots_disallow():
    fetcher=SafeFetcher(httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/plain'},text='User-agent: *\nDisallow: /')),resolver)
    assert not asyncio.run(fetcher.allowed('https://fiction.example/book'))


def test_html_extraction_does_not_execute_script():
    text='这是人物真实面对的一次选择，他推开门，把需要核实的线索交给同行者。'*30
    raw=f'<html><title>测试</title><nav>不要保留导航</nav><a href="/2">下一章</a><article><p>{text}</p></article><script>恶意指令</script></html>'.encode()
    page=extract_page(raw,'https://fiction.example/1')
    assert page['readable'] and '恶意指令' not in page['text'] and page['text']==text
    assert page['links'][0]['url']=='https://fiction.example/2'


def test_sample_coverage_honest():
    chunks=sample_chunks('甲'*10000,3000,1000)
    assert sum(end-start for start,end,_ in chunks)==3000
    assert chunks[0][0]==0 and chunks[-1][1]==10000


def test_length_overlap_and_style_are_distinct():
    text='命运的齿轮'+''.join(chr(0x4e00+i) for i in range(400))
    result=diagnose(text,400,[text])
    assert result['hard'] and result['warnings']
    assert char_count('甲 乙\n丙。')==4
    assert not diagnose('甲'*300,400,[])['hard']


def test_unsafe_urls():
    for url in ['file:///etc/passwd','http://user:pass@example.com/','http://example.com:8080/']:
        with pytest.raises(FetchError):normalize_url(url)


def test_mixed_dns_results_fail_closed():
    def mixed(host,port,*args):
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in ['8.8.8.8','127.0.0.1']]
    fetcher=SafeFetcher(httpx.MockTransport(lambda req:pytest.fail('must not connect')),mixed)
    with pytest.raises(FetchError):asyncio.run(fetcher.get('https://fiction.example/test'))


def test_robots_404_allows_public_page():
    fetcher=SafeFetcher(httpx.MockTransport(lambda req:httpx.Response(404)),resolver)
    assert asyncio.run(fetcher.allowed('https://fiction.example/book'))


def test_legitimate_style_word_is_not_hard_failure():
    text='殊不知，'+''.join(chr(0x4e00+i) for i in range(400))
    report=diagnose(text,400,[])
    assert report['warnings'] and not report['hard']
