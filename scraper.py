from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

import requests
import lxml.html

from config import USER_AGENT, REQUEST_TIMEOUT

REMOVE_TAGS = [
    "script", "style", "noscript", "iframe", "svg", "nav", "footer",
    "header", "aside", "form", "button", "select", "option", "input",
    "textarea", "template", "dialog", "canvas", "audio", "video",
    "figure", "figcaption",
]

_client: Optional[requests.Session] = None


def _get_client() -> requests.Session:
    global _client
    if _client is None:
        _client = requests.Session()
        _client.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
    return _client


def fetch_url(url: str, max_chars: int = 20000) -> Dict[str, Any]:
    client = _get_client()
    resp = client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = lxml.html.fromstring(resp.content, base_url=url)
    for tag in REMOVE_TAGS:
        for el in root.xpath(f"//{tag}"):
            el.getparent().remove(el)
    title = root.cssselect("title")
    title_text = title[0].text_content().strip() if title else ""
    text = root.text_content() or ""
    import re
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
    return {"url": url, "status": resp.status_code, "title": title_text, "text": text[:max_chars], "length": len(text)}


def extract_structured(url: str) -> Dict[str, Any]:
    client = _get_client()
    resp = client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = lxml.html.fromstring(resp.content, base_url=url)
    json_ld = []
    for script in root.cssselect('script[type="application/ld+json"]'):
        try:
            import json
            json_ld.append(json.loads(script.text or ""))
        except Exception:
            continue
    og = {}
    for m in root.cssselect("meta[property^='og:']"):
        key = m.get("property", "").replace("og:", "")
        og[key] = m.get("content", "")
    twitter = {}
    for m in root.cssselect("meta[name^='twitter:']"):
        key = m.get("name", "").replace("twitter:", "")
        twitter[key] = m.get("content", "")
    return {"url": url, "json_ld": json_ld, "open_graph": og, "twitter_card": twitter}


def query_page(url: str, selector: str, limit: int = 25) -> Dict[str, Any]:
    client = _get_client()
    resp = client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = lxml.html.fromstring(resp.content, base_url=url)
    els = root.cssselect(selector)
    results = []
    for el in els[:limit]:
        results.append({"tag": el.tag, "text": el.text_content()[:200], "attributes": dict(el.attrib)})
    return {"url": url, "selector": selector, "count": len(results), "results": results}


def extract_links(url: str) -> Dict[str, Any]:
    client = _get_client()
    resp = client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = lxml.html.fromstring(resp.content, base_url=url)
    links = []
    seen = set()
    for a in root.cssselect("a[href]"):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = urllib.parse.urljoin(url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append({"href": absolute, "text": a.text_content().strip()[:200]})
    return {"url": url, "count": len(links), "links": links}


def extract_images(url: str) -> Dict[str, Any]:
    client = _get_client()
    resp = client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = lxml.html.fromstring(resp.content, base_url=url)
    images = []
    for img in root.cssselect("img[src]"):
        src = img.get("src", "").strip()
        if not src or src.startswith("data:"):
            continue
        absolute = urllib.parse.urljoin(url, src)
        images.append({"src": absolute, "alt": (img.get("alt") or "").strip()[:200]})
    return {"url": url, "count": len(images), "images": images}


def discover_feeds(url: str) -> Dict[str, Any]:
    client = _get_client()
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    feeds = []
    try:
        resp = client.get(url, timeout=REQUEST_TIMEOUT)
        if resp.content.startswith(b"<"):
            root = lxml.html.fromstring(resp.content, url)
            for link in root.cssselect('link[rel="alternate"]'):
                href = link.get("href", "")
                ctype = link.get("type", "").lower()
                if "rss" in ctype or "atom" in ctype:
                    feeds.append({"url": urllib.parse.urljoin(url, href), "type": ctype})
    except Exception:
        pass
    return {"url": url, "feeds": feeds}


def discover_sitemap(url: str) -> Dict[str, Any]:
    client = _get_client()
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    sitemaps = []
    try:
        resp = client.get(base + "/robots.txt", timeout=10)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line = line.strip()
                if line.lower().startswith("sitemap:"):
                    sitemaps.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return {"url": url, "sitemaps": sitemaps}


def web_search(query: str, limit: int = 10, engine: str = "duckduckgo") -> Dict[str, Any]:
    try:
        resp = requests.post(
            "http://127.0.0.1:3210/search",
            json={"query": query, "limit": min(limit, 50), "engines": [engine]},
            timeout=25,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok":
                results = data.get("data", {}).get("results", [])
                return {
                    "query": query,
                    "count": len(results),
                    "results": [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")} for r in results[:limit]],
                    "mode": "daemon",
                }
    except Exception:
        pass
    return {"query": query, "count": 0, "results": [], "mode": "failed"}


def batch_scrape(urls: List[str], max_chars: int = 2000) -> Dict[str, Any]:
    results = []
    for url in urls[:20]:
        try:
            r = fetch_url(url, max_chars=max_chars)
            results.append(r)
        except Exception as e:
            results.append({"url": url, "error": str(e)})
    return {"count": len(results), "results": results}
