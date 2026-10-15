"""Search -> catalogue snapshot -> whole-book download -> verified reference import.

No model is needed for search/download. Optional model calls select only observed
links; the normal novel research stage spends tokens after acquisition succeeds.
Downloaded content is untrusted data, never executable instructions.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, UnicodeDammit

from .acquisition_models import AcquisitionOptions
from .llm import PipelineError
from .sources import FetchError, SafeFetcher, ensure_public, normalize_url
from .store import dumps, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS reference_books (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 url TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
 backend TEXT NOT NULL, status TEXT NOT NULL, manifest TEXT NOT NULL,
 created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(project_id,url)
);
CREATE TABLE IF NOT EXISTS reference_chapters (
 book_id TEXT NOT NULL REFERENCES reference_books(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', text TEXT NOT NULL DEFAULT '',
 digest TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
 attempts INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
 PRIMARY KEY(book_id,ordinal), UNIQUE(book_id,url)
);
CREATE INDEX IF NOT EXISTS reference_project ON reference_books(project_id);
"""
CHAPTER = re.compile(r"(?:^|\s)(?:第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*[章回节]|(?:chapter|ch\.)\s*(\d+))", re.I)
TOC_SELECTORS = "#list,.listmain,.chapter-list,.chapterlist,.catalog,.catalog-list,#catalog,#chapterlist,.book-chapter-list"
CONTENT_SELECTORS = "#chaptercontent,#chapter-content,.chapter-content,.read-content,#content,#BookText,article"
BLOCK = re.compile(r"(?:请先登录.{0,20}(?:阅读|查看)|订阅本章|购买本章|付费后.{0,10}阅读|验证您是人类|人机验证|checking your browser|just a moment|access denied)", re.I)
NEXT_PAGE = re.compile(r"^(?:下一页|下页|next\s*page|next|[>»›])$", re.I)
CATALOG_LINK = re.compile(r"(?:全部章节|章节目录|全部目录|查看目录|目录|table of contents|all chapters)", re.I)
UNWANTED_LINK = re.compile(r"(?:登录|注册|充值|订阅|下载APP|客服|隐私|书架|login|signup|privacy)", re.I)


def setup(store):
    with store.connect() as conn:
        conn.executescript(SCHEMA)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def html_soup(raw: bytes) -> BeautifulSoup:
    decoded = UnicodeDammit(raw, ["utf-8", "gb18030"]).unicode_markup
    if not decoded or decoded.count("\ufffd") > max(3, len(decoded) // 100):
        raise FetchError("页面编码无效或乱码比例过高")
    return BeautifulSoup(decoded, "html.parser")


def plain(node) -> str:
    for bad in list(node.select("script,style,noscript,nav,footer,aside,form")):
        bad.decompose()
    for br in node.select("br"):
        br.replace_with("\n")
    return re.sub(r"\n{3,}", "\n\n", node.get_text("\n", strip=True)).strip()


def observed_links(soup, url: str) -> list[dict]:
    seen, result = set(), []
    for a in soup.select("a[href]"):
        try:
            target = normalize_url(urljoin(url, a.get("href", "")))
        except (ValueError, TypeError):
            continue
        label = a.get_text(" ", strip=True)[:180]
        if not label or target in seen or urlsplit(target).hostname != urlsplit(url).hostname:
            continue
        seen.add(target)
        # Keep local card text as evidence, not an invented popularity score.
        context = a.parent.get_text(" ", strip=True)[:500] if a.parent else label
        result.append({"url": target, "label": label, "evidence": context})
        if len(result) >= 20000:
            break
    return result


def metadata(soup, fallback: str) -> tuple[str, str, str]:
    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return str(tag["content"]).strip()[:200]
        return ""
    h = soup.find("h1") or soup.find("title")
    title = meta("og:novel:book_name", "og:title") or (h.get_text(" ", strip=True)[:200] if h else fallback)
    author = meta("og:novel:author", "author")
    status = meta("og:novel:status", "og:novel:book_status")
    completion = "completed" if re.search(r"完结|已完本|completed", status, re.I) else ("ongoing" if re.search(r"连载|ongoing", status, re.I) else "unknown")
    return title, author, completion


def chapter_number(label: str) -> int | None:
    found = CHAPTER.search(label)
    if not found:
        return None
    value = found.group(1) or found.group(2)
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total, section, n = 0, 0, 0
    for ch in value:
        if ch in digits:
            n = digits[ch]
        elif ch == "万":
            total += (section + n) * 10000
            section, n = 0, 0
        elif ch in units:
            section += (n or 1) * units[ch]
            n = 0
        else:
            return None
    return total + section + n


def catalogue(soup, url: str, limit: int) -> tuple[list[dict], list[str], bool]:
    """Extract ALL observed entries, never the old 40/60-link page sample."""
    heading = soup.find("h1") or soup.find("title")
    heading_text = heading.get_text(" ", strip=True) if heading else ""
    book_meta = soup.find("meta", attrs={"property": "og:novel:book_name"})
    if not book_meta and re.search("排行榜|人气榜|热度榜|畅销榜|推荐榜|rankings", heading_text, re.I):
        return [], [], False
    containers = soup.select(TOC_SELECTORS)
    root = max(containers, key=lambda n: len(n.select("a[href]"))) if containers else soup
    all_links = observed_links(root, url)
    links = [x for x in all_links if CHAPTER.search(x["label"])]
    # A named directory may use titles instead of chapter numbers.
    named_toc = bool(containers and (book_meta or re.search("目录|chapters|contents", heading_text, re.I) or re.search("chapter|catalog", str(root.get("id", ""))+" "+" ".join(root.get("class", [])), re.I)))
    if len(links) < 2 and named_toc:
        links = [x for x in all_links if not UNWANTED_LINK.search(x["label"]) and not NEXT_PAGE.fullmatch(x["label"]) and not CATALOG_LINK.fullmatch(x["label"])]
    if len(links) < 2:
        return [], [], False
    # Latest-chapter widgets preceding a full TOC are deduplicated by URL above.
    if len(links) > limit:
        raise FetchError(f"目录超过章节上限 {limit}；未将截取后的目录冒充整本")
    pages = []
    for a in soup.select("a[href]"):
        label = a.get_text(" ", strip=True)
        rel = a.get("rel", [])
        if NEXT_PAGE.fullmatch(label) or "next" in rel:
            try:
                target = normalize_url(urljoin(url, a["href"]))
                if urlsplit(target).hostname == urlsplit(url).hostname and target != url:
                    pages.append(target)
            except (ValueError, TypeError):
                pass
    return links, list(dict.fromkeys(pages)), bool(containers)


def numeric_gaps(rows: list[dict]) -> list[str]:
    nums = [chapter_number(r["title"]) for r in rows]
    nums = [n for n in nums if n is not None]
    if len(nums) < 2:
        return []
    # Resetting chapter numbers across volumes makes global arithmetic ambiguous.
    if any(b < a for a, b in zip(nums, nums[1:])):
        return ["章节编号存在倒序或分卷重置，需要核对目录顺序"]
    issues = []
    if nums[0] > 1:
        issues.append(f"目录首个数字章节为 {nums[0]}，可能缺少开篇")
    for a, b in zip(nums, nums[1:]):
        if b > a + 1:
            issues.append(f"目录编号跳跃：{a} → {b}")
        elif b == a:
            issues.append(f"目录章节编号重复：{a}")
        if len(issues) >= 20:
            break
    return issues


class SearXNG:
    """Only an administrator-configured endpoint can access a local search service."""
    def __init__(self, endpoint: str | None = None, transport=None):
        self.endpoint = (endpoint if endpoint is not None else os.getenv("SEARXNG_URL", "")).rstrip("/")
        self.transport = transport

    async def search(self, query: str, limit: int = 15) -> list[dict]:
        p = urlsplit(self.endpoint)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise FetchError("搜索服务未配置：请设置 SEARXNG_URL，并在 SearXNG 开启 JSON 输出")
        endpoint = self.endpoint if p.path.rstrip("/").endswith("/search") else self.endpoint + "/search"
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False, transport=self.transport) as client:
            async with client.stream("GET", endpoint, params={"q": query, "format": "json", "language": "zh-CN", "pageno": 1}) as response:
                if response.status_code != 200:
                    raise FetchError(f"搜索服务 HTTP {response.status_code}；403 时检查 JSON 格式是否启用")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2000000:
                        raise FetchError("搜索结果超过2MB上限")
        try:
            data = json.loads(raw)
            items = data["results"]
            if not isinstance(items, list):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise FetchError("搜索服务没有返回有效 results JSON") from None
        seen, results = set(), []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                url = normalize_url(str(item.get("url", "")))
                host = urlsplit(url).hostname
                if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
                    continue
                try:
                    import ipaddress
                    ensure_public(str(ipaddress.ip_address(host)))
                except ValueError as exc:
                    if isinstance(exc, FetchError):
                        continue
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append({"url": url, "label": str(item.get("title", ""))[:200],
                            "evidence": str(item.get("content", ""))[:600],
                            "query": query, "search_position": len(results) + 1,
                            "heat_verified": False, "captured_at": time.time()})
            if len(results) >= limit:
                break
        return results


@dataclass
class Catalog:
    url: str
    title: str
    author: str = ""
    chapters: list[dict] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    work_completion: str = "unknown"
    backend: str = "native"

    def manifest(self):
        return {"schema": 1, "catalogue_url": self.url, "catalogue_pages": self.pages,
                "expected_chapters": len(self.chapters), "catalogue_issues": self.issues,
                "work_completion": self.work_completion, "captured_at": time.time(),
                "scope": "已发现目录快照；不证明网站未隐藏其他章节，也不证明作者已完结"}


class BookAcquirer:
    def __init__(self, store, project: str, cfg: dict, guard, fetcher=None, searcher=None, sleeper=asyncio.sleep):
        setup(store)
        self.store, self.project, self.cfg, self.guard = store, project, cfg, guard
        self.options = AcquisitionOptions.model_validate(cfg.get("acquisition", {}))
        self.fetcher = fetcher or SafeFetcher()
        self.searcher = searcher or SearXNG()
        self.sleep = sleeper
        self.root = store.path.parent / "reference-books" / project
        self.root.mkdir(parents=True, exist_ok=True)

    async def page(self, url: str):
        await self.guard()
        if not await self.fetcher.allowed(url):
            raise FetchError("robots未允许读取，或规则暂不可用")
        last = "下载失败"
        for attempt in range(self.options.retries + 1):
            await self.guard()
            try:
                actual, raw, mime = await self.fetcher.get(url)
                if actual != url and not await self.fetcher.allowed(actual):
                    raise FetchError("重定向目标未允许读取")
                return actual, raw, mime
            except (FetchError, httpx.HTTPError, OSError) as exc:
                last = str(exc) if isinstance(exc, FetchError) else "网络连接失败或超时"
                # Do not hammer authentication, permission or missing-page failures.
                if re.search(r"HTTP (?:401|403|404|410)|robots|禁止|内网|超过", last):
                    break
                if attempt < self.options.retries:
                    await self.sleep(min(20, self.options.delay_seconds * (2 ** attempt)))
            finally:
                await self.sleep(self.options.delay_seconds)
        raise FetchError(last)

    async def load_catalog(self, url: str) -> Catalog | None:
        pending, visited, chapters, seen, issues = [url], [], [], set(), []
        result = None
        while pending:
            if len(visited) >= self.options.max_toc_pages:
                issues.append("目录分页超过配置上限，目录未遍历完")
                break
            current = pending.pop(0)
            if current in visited:
                continue
            actual, raw, mime = await self.page(current)
            if "html" not in mime:
                if result is None:
                    return None
                issues.append("目录分页不是HTML")
                break
            soup = html_soup(raw)
            entries, next_pages, recognized = catalogue(soup, actual, self.options.max_chapters)
            if not entries:
                if result is None:
                    return None
                issues.append("目录分页未取得章节链接")
                break
            if result is None:
                title, author, completion = metadata(soup, url)
                result = Catalog(url, title, author, work_completion=completion)
                if not recognized:
                    issues.append("未识别专门目录容器，目录范围未经确认")
            visited.append(current)
            for entry in entries:
                if entry["url"] not in seen:
                    seen.add(entry["url"])
                    chapters.append({"title": entry["label"], "url": entry["url"]})
            if len(chapters) > self.options.max_chapters:
                raise FetchError("完整目录超过章节上限；请提高上限后继续")
            for target in next_pages:
                if target in visited:
                    issues.append("目录下一页形成循环，不能认证目录完整")
                elif target not in pending:
                    pending.append(target)
        if result is not None:
            result.chapters, result.pages = chapters, visited
            result.issues = list(dict.fromkeys(issues + numeric_gaps(chapters)))
        return result

    async def discover(self, gateway=None) -> list[dict]:
        existing = self.store.artifact(self.project, "acquisition/candidates")
        if existing is not None:
            return existing
        queue = [{"url": u, "label": "指定网址", "evidence": "用户指定", "heat_verified": False} for u in self.cfg.get("urls", [])]
        search_errors=[]
        for query in self.options.queries:
            await self.guard()
            key = "acquisition/search/" + digest(query)[:20]
            items = self.store.artifact(self.project, key)
            if items is None:
                self.store.stage(self.project, "搜索参考作品：" + query[:40])
                try:
                    items = await self.searcher.search(query)
                except (FetchError, httpx.HTTPError, OSError) as exc:
                    message = str(exc) if isinstance(exc, FetchError) else "搜索网络失败"
                    self.store.event(self.project, message, "warning")
                    search_errors.append(message)
                    continue
                self.store.put_artifact(self.project, key, items)
            queue.extend(items)
        if not queue:
            raise PipelineError("；".join(search_errors) if search_errors else "没有搜索结果；请调整搜索词或提供作品/榜单网址")
        visited, candidates = set(), []
        while queue and len(visited) < self.options.max_discovery_pages and len(candidates) < self.options.max_candidates:
            await self.guard()
            item = queue.pop(0)
            url = normalize_url(item["url"])
            if url in visited:
                continue
            visited.add(url)
            self.store.stage(self.project, f"识别作品与目录 {len(visited)}/{self.options.max_discovery_pages}")
            try:
                # An explicitly allowed upstream source can resolve its own directory.
                if self.options.backend in {"auto", "lncrawl"} and external_enabled(url):
                    candidates.append({**item, "backend": "lncrawl"})
                    continue
                if self.options.backend == "lncrawl":
                    raise FetchError("此域名未启用外部下载器，或 LNCRAWL_PYTHON 未配置")
                cat = await self.load_catalog(url)
                if cat:
                    self.save_catalog(cat, item)
                    candidates.append({**item, "backend": "native", "title": cat.title})
                    continue
                actual, raw, mime = await self.page(url)
                if "html" not in mime:
                    raise FetchError("不是可识别的目录页；现有TXT可在参考资料中导入")
                soup = html_soup(raw)
                links = [l for l in observed_links(soup, actual) if not UNWANTED_LINK.search(l["label"]) and l["url"] not in visited]
                # Work detail pages go to their observed TOC first. Search results and
                # ranking pages may require a model to select book cards (never URLs).
                direct = [l for l in links if CATALOG_LINK.search(l["label"])]
                picked = direct[:5]
                if not picked:
                    links = links[:80]
                    if gateway is not None and links:
                        from .models import CrawlChoice
                        def validate(result):
                            if len(set(result.ids)) != len(result.ids) or any(i < 0 or i >= len(links) for i in result.ids):
                                raise ValueError("只能选择实际提供的唯一链接ID")
                        choice = await gateway.ask("acquisition/choose/" + digest(url)[:20],
                            "从真实页面链接选择最多6部符合题材的作品详情或完整目录入口，排除广告、分类切换和登录。只有真实榜单上下文才能作为热度依据，搜索位置不等于热度。不生成新网址。", 
                            {"title": metadata(soup, url)[0], "genre": self.cfg.get("genre"), "links": [dict(id=i, **l) for i, l in enumerate(links)]},
                            CrawlChoice, role="research", output_limit=1800, validate=validate)
                        picked = [links[i] for i in choice.ids]
                    else:
                        # Download-only mode remains usable without a model account.
                        def score(link):
                            return int(bool(re.search(r"/(?:book|novel|info|shuku|read)/|bookid=", link["url"], re.I))) * 5 + int(bool(re.search("作者|完结|连载|万字|阅读", link["evidence"])))
                        picked = sorted(links, key=score, reverse=True)[:6]
                for link in picked:
                    queue.append({**link, "discovered_from": actual, "captured_at": time.time(), "heat_verified": False})
            except (FetchError, httpx.HTTPError, OSError) as exc:
                reason = str(exc) if isinstance(exc, FetchError) else "网络或页面解析失败"
                self.store.event(self.project, "候选来源跳过", "warning", url=url, reason=reason)
        self.store.put_artifact(self.project, "acquisition/discovery", {"visited": sorted(visited), "remaining": len(queue), "note": "搜索位置/模型选择不等于已验证热门排名"})
        if not candidates:
            raise PipelineError("没有识别到可下载的完整目录。请提供作品目录网址，或配置该站点的下载器；没有伪造下载结果。")
        self.store.put_artifact(self.project, "acquisition/candidates", candidates)
        return candidates

    def save_catalog(self, cat: Catalog, evidence: dict) -> str:
        row = self.store.one("SELECT id FROM reference_books WHERE project_id=? AND url=?", (self.project, cat.url))
        if row:
            saved=self.store.all("SELECT ordinal,title,url FROM reference_chapters WHERE book_id=? ORDER BY ordinal",(row["id"],))
            old_urls=[ch["url"] for ch in saved]
            new_urls=[ch["url"] for ch in cat.chapters]
            if old_urls != new_urls[:len(old_urls)]:
                raise FetchError("目录顺序或版本变化，请新建采集任务，避免混合不同版本")
            with self.store.connect() as conn:
                for i,ch in enumerate(cat.chapters[len(saved):],len(saved)+1):
                    conn.execute("INSERT INTO reference_chapters(book_id,ordinal,title,url,updated) VALUES (?,?,?,?,?)",(row["id"],i,ch["title"],ch["url"],time.time()))
                conn.execute("UPDATE reference_books SET manifest=?,updated=? WHERE id=?",(dumps({**cat.manifest(),"discovery":evidence}),time.time(),row["id"]))
            return row["id"]
        bid, now = uid(), time.time()
        manifest = {**cat.manifest(), "discovery": evidence}
        with self.store.connect() as conn:
            conn.execute("INSERT INTO reference_books VALUES (?,?,?,?,?,?,?,?,?,?)", (bid, self.project, cat.url, cat.title, cat.author, cat.backend, "pending", dumps(manifest), now, now))
            for i, ch in enumerate(cat.chapters, 1):
                conn.execute("INSERT INTO reference_chapters(book_id,ordinal,title,url,updated) VALUES (?,?,?,?,?)", (bid, i, ch["title"], ch["url"], now))
        return bid

    async def chapter_text(self, url: str) -> str:
        pages, seen, parts = [url], set(), []
        while pages:
            current = pages.pop(0)
            if current in seen:
                raise FetchError("章内分页循环")
            if len(seen) >= self.options.max_chapter_pages:
                raise FetchError("章内分页超过上限，不能把半章算成功")
            seen.add(current)
            actual, raw, mime = await self.page(current)
            if "html" not in mime:
                text = UnicodeDammit(raw, ["utf-8", "gb18030"]).unicode_markup or ""
                next_pages = []
            else:
                soup = html_soup(raw)
                title = soup.title.get_text(" ", strip=True) if soup.title else ""
                candidates = soup.select(CONTENT_SELECTORS)
                if not candidates:
                    raise FetchError("未找到正文容器；不是把整页导航当小说")
                chosen = max(candidates, key=lambda n: len(n.get_text()))
                next_pages = []
                for link in observed_links(soup, actual):
                    # '下一章' deliberately does NOT join into the current chapter.
                    if link["label"] in {"下一页", "下页", "Next Page", "next page"}:
                        next_pages.append(link["url"])
                text = plain(chosen)
                if BLOCK.search(title) or (len(text) < 1500 and BLOCK.search(text)):
                    raise FetchError("收到登录、付费或验证提示页，不是正文")
            if not text or "\x00" in text or text.count("\ufffd") > max(3, len(text) // 100):
                raise FetchError("正文为空、二进制或乱码")
            if text in parts:
                raise FetchError("章内分页正文重复")
            parts.append(text)
            pages.extend(x for x in next_pages[:1] if x not in pages)
        text = "\n\n".join(parts)
        if len(re.sub(r"\s", "", text)) < self.options.min_chapter_chars:
            raise FetchError("正文过短，可能是空章、作者通知或提取失败")
        return text

    def store_chapter(self, bid: str, ordinal: int, text: str):
        text = text.strip()
        if len(re.sub(r"\s", "", text)) < self.options.min_chapter_chars or "\x00" in text:
            raise FetchError("外部下载结果为空或过短")
        if len(text) < 1500 and BLOCK.search(text):
            raise FetchError("外部下载器返回了访问提示，不是正文")
        used = self.store.one("SELECT COALESCE(SUM(length(text)),0) n FROM reference_chapters WHERE book_id=? AND ordinal<>?", (bid, ordinal))["n"]
        total = self.store.one("SELECT COALESCE(SUM(length(c.text)),0) n FROM reference_chapters c JOIN reference_books b ON b.id=c.book_id WHERE b.project_id=? AND NOT(c.book_id=? AND c.ordinal=?)", (self.project, bid, ordinal))["n"]
        if used + len(text) > self.options.max_book_chars or total + len(text) > self.options.max_total_chars:
            raise FetchError("达到下载字符上限，保留已下内容，不截断章节冒充完成")
        sha = digest(text)
        dup = self.store.one("SELECT ordinal FROM reference_chapters WHERE book_id=? AND digest=? AND ordinal<>? AND status='ready'", (bid, sha, ordinal))
        if dup:
            raise FetchError(f"正文与第 {dup['ordinal']} 个目录条目完全重复")
        self.store.execute("UPDATE reference_chapters SET status='ready',text=?,digest=?,error='',attempts=attempts+1,updated=? WHERE book_id=? AND ordinal=?", (text, sha, time.time(), bid, ordinal))

    async def download_native(self, item: dict) -> str:
        row = self.store.one("SELECT * FROM reference_books WHERE project_id=? AND url=?", (self.project, item["url"]))
        if not row:
            cat = await self.load_catalog(item["url"])
            if not cat:
                raise FetchError("作品目录已无法读取")
            bid = self.save_catalog(cat, item)
        else:
            bid = row["id"]
        self.store.execute("UPDATE reference_books SET status='downloading',updated=? WHERE id=?",(time.time(),bid))
        for ch in self.store.all("SELECT * FROM reference_chapters WHERE book_id=? ORDER BY ordinal", (bid,)):
            await self.guard()
            if ch["status"] == "ready" and digest(ch["text"]) == ch["digest"]:
                continue
            self.store.stage(self.project, f"整本下载 · 第 {ch['ordinal']} 个目录条目 · {ch['title'][:28]}")
            try:
                self.store_chapter(bid, ch["ordinal"], await self.chapter_text(ch["url"]))
            except (FetchError, httpx.HTTPError, OSError) as exc:
                reason = str(exc) if isinstance(exc, FetchError) else "章节网络失败"
                self.store.execute("UPDATE reference_chapters SET status='failed',error=?,attempts=attempts+1,updated=? WHERE book_id=? AND ordinal=?", (reason, time.time(), bid, ch["ordinal"]))
                if "字符上限" in reason:
                    break
        self.finalize(bid)
        return bid

    def finalize(self, bid: str) -> dict:
        book = self.store.one("SELECT * FROM reference_books WHERE id=? AND project_id=?", (bid, self.project))
        rows = self.store.all("SELECT * FROM reference_chapters WHERE book_id=? ORDER BY ordinal", (bid,))
        manifest = json.loads(book["manifest"])
        good = [c for c in rows if c["status"] == "ready" and digest(c["text"]) == c["digest"]]
        ready_ids = {c["ordinal"] for c in good}
        bad = [c for c in rows if c["ordinal"] not in ready_ids]
        for ch in bad:
            if ch["status"]=="ready":
                ch["error"]="缓存正文校验失败，需重新下载"
                ch["status"]="failed"
                self.store.execute("UPDATE reference_chapters SET status='failed',error=?,updated=? WHERE book_id=? AND ordinal=?",(ch["error"],time.time(),bid,ch["ordinal"]))
        issues = manifest.get("catalogue_issues", [])
        complete = bool(rows) and not bad and len(rows) == manifest["expected_chapters"] and not issues
        status = "complete_snapshot" if complete else ("partial" if good else "failed")
        manifest.update({"downloaded_chapters": len(good), "missing_chapters": len(bad),
                         "missing": [{"ordinal": c["ordinal"], "title": c["title"], "url": c["url"], "error": c["error"] or "尚未下载/校验失败"} for c in bad],
                         "downloaded_chars": sum(len(c["text"]) for c in good),
                         "catalogue_snapshot_complete": complete,
                         "status": status, "checked_at": time.time()})
        folder = self.root / bid
        folder.mkdir(parents=True, exist_ok=True)
        body = book["title"] + "\n" + ("作者：" + book["author"] if book["author"] else "作者未核实") + "\n\n"
        for c in rows:
            body += c["title"] + "\n\n" + (c["text"] if c["ordinal"] in ready_ids else f"【此章缺失：{c['error'] or '未完成'}】") + "\n\n"
        atomic_text(folder / "book.txt", body, "utf-8-sig")
        atomic_text(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        self.store.execute("UPDATE reference_books SET status=?,manifest=?,updated=? WHERE id=?", (status, dumps(manifest), time.time(), bid))
        return manifest

    def import_book(self, bid: str) -> bool:
        book = self.store.one("SELECT * FROM reference_books WHERE id=?", (bid,))
        if book["status"] != "complete_snapshot" and not (self.options.allow_partial and book["status"] == "partial"):
            return False
        source_url = "book:" + book["url"]
        existing=self.store.one("SELECT id FROM sources WHERE project_id=? AND url=?", (self.project, source_url))
        rows = self.store.all("SELECT title,text FROM reference_chapters WHERE book_id=? AND status='ready' ORDER BY ordinal", (bid,))
        text = "\n\n".join(c["title"] + "\n\n" + c["text"] for c in rows)
        sha = digest(text)
        if self.store.one("SELECT id FROM sources WHERE project_id=? AND digest=? AND status='ready'", (self.project, sha)):
            self.store.event(self.project, "重复版本未重复计为一本参考书", book_id=bid)
            return False
        manifest = json.loads(book["manifest"])
        meta = {"book_id": bid, "captured_chars": len(text), "truncated": False,
                "coverage": "已发现目录快照全部取得；作品完结状态：" + manifest.get("work_completion", "unknown") if manifest["catalogue_snapshot_complete"] else "部分章节资料，已显式允许；不是全书",
                "downloaded_chapters": manifest["downloaded_chapters"], "expected_chapters": manifest["expected_chapters"],
                "catalogue_snapshot_complete": manifest["catalogue_snapshot_complete"], "analysis_mode": self.options.analysis}
        if existing:
            self.store.execute("UPDATE sources SET title=?,text=?,digest=?,status='ready',meta=? WHERE id=?",(book["title"],text,sha,dumps(meta),existing["id"]))
        else:
            self.store.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", (uid(), self.project, source_url, book["title"], text, sha, "ready", dumps(meta)))
        return True

    async def run(self, gateway=None):
        if self.store.artifact(self.project, "acquisition/done"):
            return
        candidates = await self.discover(gateway)
        # Previously accepted partial files cannot silently survive a stricter retry.
        self.store.execute("UPDATE sources SET status='excluded' WHERE project_id=? AND url LIKE 'book:%'",(self.project,))
        accepted = []
        for item in candidates:
            await self.guard()
            if len(accepted) >= self.options.book_count:
                break
            try:
                bid = await self.download_external(item) if item["backend"] == "lncrawl" else await self.download_native(item)
                if self.import_book(bid):
                    accepted.append(bid)
                else:
                    self.store.event(self.project, "参考书未达到完整性要求，保留结果并尝试下一候选", "warning", book_id=bid)
            except (FetchError, httpx.HTTPError, OSError) as exc:
                reason = str(exc) if isinstance(exc, FetchError) else "下载器运行失败"
                self.store.event(self.project, "整本下载失败，尝试下一候选", "warning", url=item["url"], reason=reason)
        report = {"accepted_book_ids": accepted, "required_books": self.options.book_count,
                  "accepted_count": len(accepted), "allow_partial": self.options.allow_partial,
                  "note": "整本下载与模型全文阅读是两个状态；此处不代表模型已读全书"}
        self.store.put_artifact(self.project, "acquisition/report", report)
        if len(accepted) < self.options.book_count:
            raise PipelineError(f"参考书仅通过 {len(accepted)}/{self.options.book_count} 本，未开始写作。缺章与失败已保存；可补充采集配置后重试。")
        self.store.put_artifact(self.project, "acquisition/done", report)
        self.store.event(self.project, "参考书下载、目录核对与入库完成", books=len(accepted))

    async def download_external(self, item: dict) -> str:
        """Independent optional process. No shell, no model-provided executable."""
        url = normalize_url(item["url"])
        if not external_enabled(url):
            raise FetchError("外部下载器未配置或域名不在管理员允许列表")
        await public_host(url)
        work = self.root / ("external-" + digest(url)[:24])
        work.mkdir(parents=True, exist_ok=True)
        previous = self.store.one("SELECT * FROM reference_books WHERE project_id=? AND url=?", (self.project, url))
        skip = []
        if previous:
            skip = [r["url"] for r in self.store.all("SELECT url,text,digest FROM reference_chapters WHERE book_id=? AND status='ready'", (previous["id"],)) if digest(r["text"])==r["digest"]]
        request_file = work / "request.json"
        atomic_text(request_file, dumps({"url": url, "max_chapters": self.options.max_chapters,
                     "max_chars": self.options.max_book_chars, "delay": self.options.delay_seconds,
                     "retries": self.options.retries, "skip": skip}))
        python = os.environ["LNCRAWL_PYTHON"]
        script = Path(__file__).resolve().parent.parent / "scripts" / "lncrawl_worker.py"
        # Keep model keys, cloud credentials and unrelated DB/config variables out.
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "SSL_CERT_FILE") if k in os.environ}
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "LNCRAWL_DATA_PATH": str(work / "runtime"), "HOME": str(work), "USERPROFILE": str(work)})
        proc = await asyncio.create_subprocess_exec(python, str(script), str(request_file.resolve()), cwd=work, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=5000000,
                    **({"start_new_session": True} if os.name != "nt" else {}))
        bid = previous["id"] if previous else None
        done, deadline, reader = False, time.monotonic() + 7200, None
        try:
            while True:
                await self.guard()
                if time.monotonic() > deadline:
                    raise FetchError("外部下载器达到2小时单次上限，可从检查点继续")
                if reader is None:
                    reader = asyncio.create_task(proc.stdout.readline())
                try:
                    line = await asyncio.wait_for(asyncio.shield(reader), 0.5)
                    reader = None
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                if len(line) > 4500000:
                    raise FetchError("外部下载器单条消息过大")
                try:
                    msg = json.loads(line)
                except ValueError:
                    raise FetchError("外部下载器协议不兼容；不是把控制台日志当小说") from None
                event = msg.get("event")
                if event == "catalogue":
                    chapters = msg.get("chapters", [])
                    if not 1 <= len(chapters) <= self.options.max_chapters:
                        raise FetchError("外部目录为空或超过章节上限")
                    urls = [normalize_url(c["url"]) for c in chapters]
                    if len(set(urls)) != len(urls):
                        raise FetchError("外部目录有重复章节网址")
                    if previous:
                        saved = [c["url"] for c in self.store.all("SELECT url FROM reference_chapters WHERE book_id=? ORDER BY ordinal", (bid,))]
                        if saved != urls:
                            raise FetchError("上游目录与已保存快照不同；请新建采集以免混合版本")
                    else:
                        cat = Catalog(url, str(msg.get("title", url))[:200], str(msg.get("author", ""))[:200],
                                      [{"url": u, "title": str(c.get("title", "未命名章节"))[:200]} for u, c in zip(urls, chapters)],
                                      [url], [], str(msg.get("work_completion", "unknown")), "lncrawl")
                        bid = self.save_catalog(cat, item)
                elif event == "chapter" and bid:
                    ordinal = int(msg["ordinal"])
                    row = self.store.one("SELECT url FROM reference_chapters WHERE book_id=? AND ordinal=?", (bid, ordinal))
                    if not row or row["url"] != normalize_url(msg["url"]):
                        raise FetchError("外部章节与目录不一致")
                    try:
                        self.store_chapter(bid, ordinal, str(msg.get("text", "")))
                    except FetchError as exc:
                        self.store.execute("UPDATE reference_chapters SET status='failed',error=?,updated=? WHERE book_id=? AND ordinal=?", (str(exc), time.time(), bid, ordinal))
                        if "字符上限" in str(exc):
                            raise
                    self.store.stage(self.project, f"开源下载器 · 已处理目录第 {ordinal} 章")
                elif event == "chapter_error" and bid:
                    self.store.execute("UPDATE reference_chapters SET status='failed',error=?,attempts=attempts+1,updated=? WHERE book_id=? AND ordinal=?", ("上游章节下载失败：" + str(msg.get("error", "unknown"))[:160], time.time(), bid, int(msg["ordinal"])))
                elif event == "error":
                    raise FetchError("开源下载器错误：" + str(msg.get("error", "unknown"))[:160])
                elif event == "finished":
                    done = True
            code = await asyncio.wait_for(proc.wait(), 10)
            if not done or code != 0 or not bid:
                raise FetchError("开源下载器未完整返回；已收到的章节保留，下次可继续")
            self.finalize(bid)
            return bid
        finally:
            if reader:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if proc.returncode is None:
                try:
                    if os.name != "nt":
                        import signal
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    await asyncio.wait_for(proc.wait(), 5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
            if bid:
                self.finalize(bid)


def atomic_text(path: Path, text: str, encoding: str = "utf-8"):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)


async def public_host(url: str):
    p = urlsplit(normalize_url(url))
    records = await asyncio.to_thread(socket.getaddrinfo, p.hostname, p.port or (443 if p.scheme == "https" else 80), 0, socket.SOCK_STREAM)
    if not records:
        raise FetchError("来源域名无地址")
    for record in records:
        ensure_public(record[4][0])


def external_enabled(url: str) -> bool:
    python = os.getenv("LNCRAWL_PYTHON", "")
    hosts = {v.strip().lower() for v in os.getenv("LNCRAWL_ALLOWED_HOSTS", "").split(",") if v.strip()}
    return bool(python and Path(python).is_absolute() and Path(python).is_file() and urlsplit(url).hostname in hosts)


def book_list(store, project: str) -> list[dict]:
    setup(store)
    rows = store.all("SELECT b.id,b.url,b.title,b.author,b.backend,b.status,b.manifest,b.updated,(SELECT COUNT(*) FROM reference_chapters c WHERE c.book_id=b.id AND c.status='ready') downloaded FROM reference_books b WHERE b.project_id=? ORDER BY b.created", (project,))
    for row in rows:
        row["manifest"] = json.loads(row["manifest"])
        missing=row["manifest"].get("missing",[])
        row["manifest"]["missing"]=missing[:20]
        row["manifest"]["missing_list_truncated"]=len(missing)>20
    return rows
