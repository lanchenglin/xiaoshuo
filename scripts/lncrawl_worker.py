#!/usr/bin/env python3
"""Optional Lightnovel Crawler bridge, executed by a separate Python environment.

Contract checked against upstream commit 59b0382d51927953aa8120c5de62dab23ce3f731.
No upstream source code is copied. Install its pinned dependency separately.
STDOUT is JSONL only; the parent discards diagnostic STDERR. No shell is invoked.
This process is an isolation of dependencies, NOT a network security sandbox.
"""
from __future__ import annotations
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit

PROTOCOL_OUT = sys.stdout


def emit(event, **payload):
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), file=PROTOCOL_OUT, flush=True)


def validate_public(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
        raise ValueError("invalid_public_url")
    records = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), 0, socket.SOCK_STREAM)
    if not records:
        raise ValueError("unresolved_source")
    for record in records:
        ip = ipaddress.ip_address(record[4][0])
        if not ip.is_global or ip.is_reserved or ip.is_multicast:
            raise ValueError("non_public_source")
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped and not ip.ipv4_mapped.is_global:
            raise ValueError("non_public_source")


def clean_html(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.select("script,style,nav,form,footer"):
        tag.decompose()
    for tag in soup.select("br"):
        tag.replace_with("\n")
    return soup.get_text("\n", strip=True)


def run(request, context=None):
    """context injection exists only for offline protocol tests, never user input."""
    url = request["url"]
    validate_public(url)
    max_chapters = min(10000, max(1, int(request.get("max_chapters", 3000))))
    max_chars = min(30000000, max(1000, int(request.get("max_chars", 8000000))))
    delay = min(30, max(0.2, float(request.get("delay", 1))))
    retries = min(5, max(0, int(request.get("retries", 2))))
    skip = set(request.get("skip", []))
    if context is None:
        from lncrawl.context import ctx as context
    # Never reuse another application's database/config, and never pull arbitrary
    # crawler updates at runtime: site code comes from the installed fixed version.
    runtime = Path(os.environ.get("LNCRAWL_DATA_PATH", "runtime")).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    context.setup(config_file=runtime / "config.json", sync_remote_index=False)
    try:
        crawler = context.sources.init_crawler(url)
        context.scraper.set_rate_limit(url, 1 / delay)
        user = context.users.get_admin()
        novel = context.crawler.fetch_novel(user.id, url, custom=crawler)
        ids = context.chapters.list_ids(novel_id=novel.id)
        chapters = sorted(context.chapters.get_many(ids), key=lambda c: c.serial)
        if not chapters or len(chapters) > max_chapters:
            raise ValueError("empty_catalogue_or_chapter_limit")
        if novel.chapter_count != len(chapters):
            raise ValueError("catalogue_count_mismatch")
        for ch in chapters:
            validate_public(ch.url)
        emit("catalogue", title=novel.title, author=str(novel.authors or ""), work_completion="unknown",
             chapters=[{"title": c.title, "url": c.url} for c in chapters])
        chars = 0
        for index, ch in enumerate(chapters, 1):
            if ch.url in skip:
                continue
            success = False
            for attempt in range(retries + 1):
                try:
                    downloaded = context.crawler.fetch_chapter(user.id, ch.id, custom=crawler, refresh=attempt > 0)
                    if not downloaded or not downloaded.is_available:
                        raise ValueError("chapter_unavailable")
                    text = clean_html(context.files.load_text(downloaded.content_file))
                    if not text:
                        raise ValueError("chapter_empty")
                    if len(text.encode("utf-8")) > 3000000:
                        raise ValueError("chapter_too_large")
                    if chars + len(text) > max_chars:
                        emit("error", error="book_char_limit")
                        return
                    chars += len(text)
                    emit("chapter", ordinal=index, url=ch.url, text=text)
                    success = True
                    break
                except Exception as exc:
                    # Exception strings from an upstream library may contain
                    # cookies, raw HTML or URLs. The protocol emits only class names.
                    error = type(exc).__name__
                    if attempt < retries:
                        time.sleep(delay * (attempt + 1))
            if not success:
                emit("chapter_error", ordinal=index, error=error)
            time.sleep(delay)
        emit("finished")
    finally:
        context.destroy()


def main():
    if len(sys.argv) != 2:
        emit("error", error="one_request_file_required")
        return 2
    path = Path(sys.argv[1])
    try:
        if path.stat().st_size > 3000000:
            raise ValueError("request_too_large")
        request = json.loads(path.read_text(encoding="utf-8"))
        with contextlib.redirect_stdout(sys.stderr):
            run(request)
        return 0
    except Exception as exc:
        emit("error", error=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
