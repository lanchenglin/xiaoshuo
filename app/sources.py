"""Bounded public-page ingestion. No login bypass, cookies, JavaScript or hidden crawlers."""
from __future__ import annotations
import asyncio
import hashlib
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup, UnicodeDammit
from .models import CrawlChoice
from .store import dumps, uid


class FetchError(ValueError):
    pass


def normalize_url(url: str) -> str:
    p=urlsplit(url.strip())
    if p.scheme not in ("http","https") or not p.hostname or p.username or p.password:
        raise FetchError("来源仅支持不含账号密码的 HTTP(S) 网址")
    try:
        if p.port not in (None,80,443):
            raise FetchError("公开来源仅允许 80/443 端口")
    except ValueError as exc:
        raise FetchError("来源端口无效") from exc
    if len(url)>2000:
        raise FetchError("网址过长")
    return urlunsplit((p.scheme,p.netloc,p.path or "/",p.query,""))


def ensure_public(address: str) -> None:
    ip=ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise FetchError("禁止从来源采集器访问内网、回环、链路本地、保留或非公网地址")
    if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:
        ensure_public(str(ip.ipv4_mapped))


class SafeFetcher:
    def __init__(self, transport=None, resolver=None):
        self.transport=transport
        self.resolver=resolver or socket.getaddrinfo
        self.robots: dict[str, RobotFileParser] = {}

    async def get(self,url: str,max_bytes: int=3000000) -> tuple[str,bytes,str]:
        url=normalize_url(url)
        for _ in range(5):
            p=urlsplit(url)
            records=await asyncio.to_thread(self.resolver,p.hostname,p.port or (443 if p.scheme=="https" else 80),0,socket.SOCK_STREAM)
            addresses=list(dict.fromkeys(row[4][0] for row in records))
            if not addresses:
                raise FetchError("来源域名没有可用地址")
            for address in addresses:
                ensure_public(address)
            address=addresses[0]
            host=f"[{address}]" if ":" in address else address
            port=p.port or (443 if p.scheme=="https" else 80)
            pinned=urlunsplit((p.scheme,f"{host}:{port}",p.path,p.query,""))
            # Pin DNS resolution while retaining the original TLS server name/Host.
            # Redirects are manually revalidated; trust_env=False excludes proxy surprises.
            async with httpx.AsyncClient(timeout=25,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                async with client.stream("GET",pinned,headers={"Host":p.netloc,"User-Agent":"MoLiuNovelBot/0.1"},
                                         extensions={"sni_hostname":p.hostname}) as response:
                    if response.status_code in (301,302,303,307,308):
                        location=response.headers.get("location")
                        if not location:
                            raise FetchError("重定向缺少地址")
                        url=normalize_url(urljoin(url,location))
                        continue
                    if response.status_code!=200:
                        raise FetchError(f"来源 HTTP {response.status_code}")
                    mime=response.headers.get("content-type","").lower()
                    if not any(x in mime for x in ("text/","application/xhtml","application/xml")):
                        raise FetchError("来源不是可读取的文本或网页")
                    content=bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content)>max_bytes:
                            raise FetchError("来源页面超过下载上限")
                    return url,bytes(content),mime
        raise FetchError("来源重定向过多")

    async def allowed(self,url: str) -> bool:
        p=urlsplit(normalize_url(url))
        origin=urlunsplit((p.scheme,p.netloc,"", "", ""))
        if origin not in self.robots:
            parser=RobotFileParser()
            try:
                _,raw,_=await self.get(origin+"/robots.txt",max_bytes=200000)
                parser.parse(raw.decode("utf-8",errors="replace").splitlines())
            except FetchError as exc:
                if "HTTP 404" in str(exc):
                    parser.parse([])
                else:
                    # Fail closed for restrictions or unavailable robot instructions.
                    parser.parse(["User-agent: *","Disallow: /"])
            self.robots[origin]=parser
        return self.robots[origin].can_fetch("MoLiuNovelBot",url)


def extract_page(raw: bytes,url: str) -> dict:
    decoded=UnicodeDammit(raw,["utf-8","gb18030"]).unicode_markup or ""
    soup=BeautifulSoup(decoded,"html.parser")
    title_node=soup.find("h1") or soup.find("title")
    title=title_node.get_text(" ",strip=True)[:200] if title_node else urlsplit(url).path[-100:]
    links=[]
    seen=set()
    for a in soup.select("a[href]"):
        try:
            target=normalize_url(urljoin(url,a.get("href","")))
        except (ValueError,TypeError):
            continue
        label=a.get_text(" ",strip=True)[:100]
        if label and target not in seen and urlsplit(target).hostname==urlsplit(url).hostname:
            seen.add(target)
            links.append({"url":target,"label":label})
    for item in soup.select("script,style,noscript,nav,footer,aside,form,header"):
        item.decompose()
    nodes=soup.select("#chaptercontent,#chapter-content,.chapter-content,.read-content,#content,article")
    chosen=max(nodes,key=lambda n:len(n.get_text())) if nodes else soup.body or soup
    for br in chosen.select("br"):
        br.replace_with("\n")
    text=chosen.get_text("\n",strip=True)
    text=re.sub(r"\n{3,}","\n\n",text).strip()
    paragraphs=[p.strip() for p in text.splitlines() if p.strip()]
    narrative=sum(len(p) for p in paragraphs if len(p)>=40 and re.search(r"[。！？.!?]",p))
    readable=len(text)>=200 and narrative>=len(text)*0.35
    # Content pages favour next chapters; list pages keep observed order as evidence.
    links=sorted(links,key=lambda v:0 if re.search("下一章|下一页|next",v["label"],re.I) else 1)
    return {"title":title or "未命名页面","text":text,"readable":readable,"links":links[:60]}


async def crawl(store,project: str,cfg: dict,gateway,guard,fetcher=None):
    if store.artifact(project,"sources/crawl_done"):
        return
    if cfg["demo"] or not cfg["urls"]:
        store.put_artifact(project,"sources/crawl_done",{"mode":"demo" if cfg["demo"] else "text-only"})
        return
    fetcher=fetcher or SafeFetcher()
    queue=store.artifact(project,"sources/queue")
    if queue is None:
        queue=[normalize_url(u) for u in cfg["urls"]]
    processed=store.artifact(project,"sources/processed") or []
    while queue and len(processed)<cfg["max_pages"]:
        await guard()
        url=queue[0]
        if url in processed:
            queue.pop(0)
            continue
        store.stage(project,f"采集资料 {len(processed)+1}/{cfg['max_pages']}")
        row=store.one("SELECT * FROM sources WHERE project_id=? AND url=?",(project,url))
        page=None
        if row:
            import json
            meta=json.loads(row["meta"])
            page={"title":row["title"],"text":row["text"],"readable":row["status"]=="ready","links":meta.get("links",[])}
        else:
            try:
                if not await fetcher.allowed(url):
                    raise FetchError("robots 规则未允许访问，或规则暂时无法读取")
                actual,raw,mime=await fetcher.get(url)
                page=extract_page(raw,actual) if "html" in mime else {"title":urlsplit(url).path[-100:],"text":raw.decode("utf-8-sig",errors="replace"),"readable":True,"links":[]}
                existing=store.one("SELECT COALESCE(SUM(length(text)),0) n FROM sources WHERE project_id=?",(project,))["n"]
                remaining=max(0,cfg["max_source_chars"]-existing)
                text=page["text"][:remaining] if page["readable"] else ""
                digest=hashlib.sha256(text.encode()).hexdigest()
                duplicate=bool(text and store.one("SELECT id FROM sources WHERE project_id=? AND digest=? AND status='ready'",(project,digest)))
                status="duplicate" if duplicate else ("ready" if len(text)>=200 else "index")
                meta={"actual_url":actual,"captured_chars":len(text),"page_chars":len(page["text"]),
                      "truncated":len(text)<len(page["text"]) and page["readable"],"links":page["links"],
                      "coverage":"仅已取得页面；不代表已取得全书"}
                store.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",(uid(),project,url,page["title"],text if status=="ready" else "",digest,status,dumps(meta)))
                store.event(project,"已处理来源页面",url=url,status=status,captured_chars=len(text))
                if remaining==0:
                    store.event(project,"达到资料总量上限，停止继续抓取","warning")
                    queue=[]
            except (FetchError,httpx.HTTPError,OSError) as exc:
                message=str(exc) if isinstance(exc,FetchError) else "网络连接失败"
                store.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?,?)",(uid(),project,url,url,"","","failed",dumps({"error":message})))
                store.event(project,"来源读取失败，保留记录并继续其他来源","warning",url=url,reason=message)
        if page and page["links"] and len(processed)+len(queue)<cfg["max_pages"]:
            links=page["links"][:40]
            def validate(result):
                if any(i<0 or i>=len(links) for i in result.ids) or len(set(result.ids))!=len(result.ids):
                    raise ValueError("只能选择提供的唯一链接 ID")
            choice=await gateway.ask("sources/choose/"+hashlib.sha256(url.encode()).hexdigest()[:20],
                "从真实页面给出的链接选择最多 6 个最相关后续资料：榜单优先不同作品入口，目录优先章节，正文优先下一章。不要编造排名、浏览量或链接。排除登录、广告、充值等。可返回空列表。",
                {"page_title":page["title"],"url":url,"readable":page["readable"],"links":[dict(id=i,**v) for i,v in enumerate(links)],"genre":cfg["genre"]},
                CrawlChoice,role="research",output_limit=1200,validate=validate)
            for i in choice.ids:
                target=links[i]["url"]
                if target not in queue and target not in processed:
                    queue.append(target)
        processed.append(url)
        queue=[u for u in queue if u!=url]
        store.put_artifact(project,"sources/processed",processed)
        store.put_artifact(project,"sources/queue",queue)
        await asyncio.sleep(1)
    store.put_artifact(project,"sources/crawl_done",{"processed":len(processed),"remaining_queue":len(queue),"coverage":"限额内公开网页采样；不是全书完整性认证"})


def sample_chunks(text: str,limit: int,chunk_size: int=5000) -> list[tuple[int,int,str]]:
    """Explicit head/middle/tail sampling; offsets remain visible in research artifacts."""
    if len(text)<=limit:
        spans=[(0,len(text))]
    else:
        n=limit//3
        middle=(len(text)-n)//2
        spans=[(0,n),(middle,middle+n),(len(text)-n,len(text))]
    result=[]
    for start,end in spans:
        for offset in range(start,end,chunk_size):
            stop=min(offset+chunk_size,end)
            result.append((offset,stop,text[offset:stop]))
    return result
